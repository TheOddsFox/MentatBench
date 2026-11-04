"""Fetches the frozen snapshot of Polymarket events for the transfer pilot (PREREG.md, "Snapshot").

usage: python -B fetch.py     (once: refuses when the snapshot or its manifest already exists)

Pulls /events from the public Gamma API, ordered by volume (descending) with start_date_min 2026-05-03, one page of 100 at a
time. The API refuses offset 2001 and above ("use /events/keyset for deeper pagination"), so pagination stops at offset 2000
and the stop is recorded. Events are kept when createdAt is on or after 2026-05-03, with the fields the pilot uses.

Writes:
  - LOCAL mb-runs/transfer-pilot/snapshot/events.jsonl: the selected fields, one event per line, sorted by id, LF. It holds
    question and event text, so it is never published.
  - snapshot_manifest.json in this folder (published): the query, the page record, the counts, the sha256 of events.jsonl, and
    per event and per market its ids, structure and the sha256 of each text. No text.

Stdlib only. The User-Agent is generic. At most two requests a second. Retries with backoff (1, 2, 4, 8 seconds) on 429, 5xx and
network errors.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[3]  # the repository root
LOCAL = ROOT.parent / "mb-runs" / "transfer-pilot"  # outside the repository: text never goes in it
EVENTS = LOCAL / "snapshot" / "events.jsonl"
MANIFEST = HERE / "snapshot_manifest.json"
sys.dont_write_bytecode = True

GAMMA = "https://gamma-api.polymarket.com"
USER_AGENT = "mentatbench-pilot/0.1"
PAGE = 100
MAX_OFFSET = 2000  # the API refuses offset 2001 and above (probed 2026-11-03: 2000 returns a page, 2001 is a 422)
START_DATE_MIN = "2026-05-03T00:00:00Z"
MIN_CREATED = datetime(2026, 5, 3, tzinfo=timezone.utc)
MIN_INTERVAL = 0.5  # seconds between requests: at most two a second
RETRIES = 4
SCHEMA = "mentat-bench-transfer-snapshot/1"
FETCH_COUNTS = ("events_returned", "duplicate_events", "events_without_created_time", "events_created_before_cutoff")


class FetchError(RuntimeError):
    """A request that failed for good (after its retries), or a response the pilot cannot read."""


def query_url(offset: int | str) -> str:
    """The events query for one offset. The parameter order is fixed, so the template in the manifest is the exact query."""
    params = [("limit", PAGE), ("offset", offset), ("order", "volume"), ("ascending", "false"),
              ("start_date_min", START_DATE_MIN)]
    return f"{GAMMA}/events?" + urllib.parse.urlencode(params, safe=":")


class Client:
    """GET with a User-Agent, at most one request per MIN_INTERVAL, and retries with backoff. It returns (status, body) for any
    HTTP answer; the caller decides what a status means. Network errors are retried, then raised as FetchError."""

    def __init__(self, sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> None:
        self.sleep, self.clock = sleep, clock
        self.last: float | None = None

    def get(self, url: str) -> tuple[int, bytes]:
        for attempt in range(RETRIES + 1):
            if self.last is not None:
                wait = self.last + MIN_INTERVAL - self.clock()
                if wait > 0:
                    self.sleep(wait)
            self.last = self.clock()
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    return response.status, response.read()
            except urllib.error.HTTPError as exc:
                if (exc.code == 429 or exc.code >= 500) and attempt < RETRIES:
                    self.sleep(2 ** attempt)
                    continue
                return exc.code, exc.read()
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt < RETRIES:
                    self.sleep(2 ** attempt)
                    continue
                raise FetchError(f"{url}: {exc}") from exc
        raise AssertionError("unreachable")


def fetch_events(get: Callable[[str], tuple[int, bytes]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """The raw event rows of every page, the page record, and the stop record. Pagination stops at a short page, or after the
    offset cap. The one refusal that is a stop (the offset cap) is recorded; any other status is a FetchError."""
    raw: list[dict[str, Any]] = []
    pages: list[dict[str, Any]] = []
    for offset in range(0, MAX_OFFSET + 1, PAGE):
        status, body = get(query_url(offset))
        if status == 422 and b"offset too large" in body:
            return raw, pages, {"reason": "offset cap", "offset": offset, "status": status,
                                "message": json.loads(body).get("error")}
        if status != 200:
            raise FetchError(f"offset {offset}: HTTP {status}: {body[:200]!r}")
        rows = json.loads(body)
        if not isinstance(rows, list):
            raise FetchError(f"offset {offset}: the response is not a list")
        pages.append({"offset": offset, "returned": len(rows)})
        raw.extend(rows)
        if len(rows) < PAGE:
            return raw, pages, {"reason": "short page (end of the listing)", "offset": offset, "status": status}
    return raw, pages, {"reason": "offset cap reached", "offset": MAX_OFFSET, "status": None,
                        "message": f"offsets above {MAX_OFFSET} are refused by the API"}


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def parse_outcomes(value: Any) -> Any:
    """The outcomes as a list of labels (the API sends a JSON-encoded string). A value that does not parse is kept as it is, and
    build_pool drops the market."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def market_record(m: dict[str, Any]) -> dict[str, Any]:
    return {"id": str(m.get("id")), "question": m.get("question"), "outcomes": parse_outcomes(m.get("outcomes")),
            "groupItemTitle": m.get("groupItemTitle"), "createdAt": m.get("createdAt"), "closed": bool(m.get("closed"))}


def event_record(e: dict[str, Any]) -> dict[str, Any]:
    return {"id": str(e["id"]), "slug": e.get("slug"), "title": e.get("title"), "createdAt": e.get("createdAt"),
            "seriesSlug": e.get("seriesSlug") or None, "negRisk": bool(e.get("negRisk")),
            "volume": float(e.get("volume") or 0.0),
            "tags": [{"label": t.get("label"), "slug": t.get("slug")} for t in e.get("tags") or []],
            "markets": [market_record(m) for m in e.get("markets") or []]}


def select_events(raw: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """The events created on or after MIN_CREATED, one record per id (the first copy), sorted by id, with the counts."""
    seen: set[str] = set()
    kept: list[dict[str, Any]] = []
    counts = {"events_returned": len(raw), "duplicate_events": 0, "events_without_created_time": 0, "events_created_before_cutoff": 0}
    for row in raw:
        if row.get("id") is None:
            counts["events_without_created_time"] += 1
            continue
        eid = str(row["id"])
        if eid in seen:
            counts["duplicate_events"] += 1
            continue
        seen.add(eid)
        created = parse_time(row.get("createdAt"))
        if created is None:
            counts["events_without_created_time"] += 1
        elif created < MIN_CREATED:
            counts["events_created_before_cutoff"] += 1
        else:
            kept.append(event_record(row))
    kept.sort(key=lambda r: r["id"])
    return kept, counts


def snapshot_bytes(events: list[dict[str, Any]]) -> bytes:
    lines = [json.dumps(e, sort_keys=True, ensure_ascii=False) + "\n" for e in events]
    return "".join(lines).encode("utf-8")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def slug_sha256(slug: str | None) -> str | None:
    """A slug is published only as its sha256: slugs are often derived from the question's title."""
    return None if slug is None else sha256_text(slug)


def category_of(event: dict[str, Any]) -> str | None:
    """The category of an event: the slug of its first tag (None when it has no tag)."""
    tags = event["tags"]
    return tags[0]["slug"] if tags else None


def manifest(events: list[dict[str, Any]], counts: dict[str, int], pages: list[dict[str, Any]], stop: dict[str, Any],
             events_sha256: str, fetched_at: str) -> dict[str, Any]:
    """The published manifest: the query, the pages, the counts and the sha256 of events.jsonl, and one structural row per event.
    It holds no text and no market-level hashes: the snapshot has far more markets than the pool uses, so the per-market hashes of
    the pool's markets are published in pool.jsonl instead (its outcome text_sha256)."""
    rows = [{"event_id": e["id"], "series_sha256": slug_sha256(e["seriesSlug"]), "category_sha256": slug_sha256(category_of(e)),
             "neg_risk": e["negRisk"],
             "created_at": e["createdAt"], "volume": e["volume"], "market_count": len(e["markets"])} for e in events]
    categories = {r["category_sha256"] for r in rows if r["category_sha256"] is not None}
    series = {r["series_sha256"] for r in rows if r["series_sha256"] is not None}
    return {
        "schema": SCHEMA,
        "source": "Polymarket Gamma API (public, no key), gamma-api.polymarket.com",
        "fetched_at": fetched_at,
        "query": {"url_template": query_url(0).replace("offset=0&", "offset={offset}&"), "page": PAGE,
                  "offsets": f"0 to {MAX_OFFSET} in steps of {PAGE}",
                  "order": "volume, descending", "start_date_min": START_DATE_MIN, "user_agent": USER_AGENT},
        "pages": pages,
        "stop": stop,
        "min_created": "2026-05-03",
        "counts": {**counts, "events_kept": len(events), "markets": sum(r["market_count"] for r in rows),
                   "categories": len(categories), "series": len(series)},
        "events_file": "mb-runs/transfer-pilot/snapshot/events.jsonl",
        "events_sha256": events_sha256,
        "markets_note": "The per-market question and outcome hashes of the pool's markets are in pool.jsonl (text_sha256). The "
                        "snapshot's other markets are not listed here; events.jsonl (local) is the whole snapshot.",
        "events": rows,
    }


def write_manifest(doc: dict[str, Any]) -> None:
    text = json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    MANIFEST.write_text(text, encoding="utf-8", newline="\n")


def rebuild_manifest() -> int:
    """Rewrites snapshot_manifest.json from the frozen local events.jsonl, with no network. The events file must have the sha256
    the manifest records, and the fetch record (time, pages, stop and the fetch counts) is kept from the manifest."""
    old = json.loads(MANIFEST.read_text(encoding="utf-8"))
    data = EVENTS.read_bytes()
    if hashlib.sha256(data).hexdigest() != old["events_sha256"]:
        print("fetch.py: events.jsonl does not have the sha256 the manifest records; not rebuilt", file=sys.stderr)
        return 1
    events = [json.loads(line) for line in data.decode("utf-8").split("\n") if line]
    fetch_counts = {k: old["counts"][k] for k in FETCH_COUNTS}
    doc = manifest(events, fetch_counts, old["pages"], old["stop"], old["events_sha256"], old["fetched_at"])
    if doc["counts"]["events_kept"] != old["counts"]["events_kept"]:
        print("fetch.py: the event count differs from the manifest; not rebuilt", file=sys.stderr)
        return 1
    write_manifest(doc)
    print(f"rebuilt {MANIFEST.name} from {EVENTS} ({len(events)} events, {doc['counts']['markets']} markets)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rebuild-manifest", action="store_true", help="rewrite the manifest from the frozen local events.jsonl (no network)")
    if ap.parse_args(argv).rebuild_manifest:
        return rebuild_manifest()
    if EVENTS.exists() or MANIFEST.exists():
        print("fetch.py: the snapshot exists; the pull is frozen (delete it by hand to pull again)", file=sys.stderr)
        return 1
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    raw, pages, stop = fetch_events(Client().get)
    events, counts = select_events(raw)
    data = snapshot_bytes(events)
    EVENTS.parent.mkdir(parents=True, exist_ok=True)
    EVENTS.write_bytes(data)
    doc = manifest(events, counts, pages, stop, hashlib.sha256(data).hexdigest(), fetched_at)
    MANIFEST.write_text(json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"events kept {len(events)}, markets {doc['counts']['markets']}, categories {doc['counts']['categories']}, "
          f"series {doc['counts']['series']}; stop: {stop['reason']} at offset {stop['offset']}")
    print(f"wrote {EVENTS} and {MANIFEST.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
