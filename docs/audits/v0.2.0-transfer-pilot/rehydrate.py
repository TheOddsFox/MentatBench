"""Rehydrates the transfer pilot's texts from the published ids (PREREG.md, "Terms, and what is published").

usage: python -B rehydrate.py [--out DIR]     (DIR defaults to LOCAL rehydrated; it must be outside the repository)

Refetches each market of pool.jsonl by id from the public Gamma API (GET /markets/<id>), and each event (GET /events/<id>). For every
outcome it checks the sha256 of the market's question, of the outcome's label and of the event's title against pool.jsonl, and reports
every mismatch and every missing market or event. It writes the verified texts with their event titles (DIR/pool_texts.jsonl), and, when
every outcome is verified, the judge input rebuilt from
pool.jsonl, slices.json and those texts (DIR/judge/slices/slice<j>.jsonl). Each rebuilt slice is checked against the sha256
published in slices.json, and against the local judge/slices file when that exists. The exit status is 0 only when every hash
matches, every market is found, and every rebuilt slice matches.

The network code is `main` and `Client` (fetch.py); `verify`, `rebuild` and `slice_problems` are offline.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.parse
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path[:0] = [str(HERE)]

import build_pool as BP  # noqa: E402
import fetch as FE  # noqa: E402

POOL = HERE / "pool.jsonl"
SLICES = HERE / "slices.json"
LOCAL_OUT = FE.LOCAL / "rehydrated"


def market_url(market_id: str) -> str:
    return f"{FE.GAMMA}/markets/{urllib.parse.quote(str(market_id), safe='')}"


def parse_outcome_id(outcome_id: str) -> tuple[str, int]:
    """pm:<market_id>:<outcome_index> -> (market_id, index)."""
    prefix, market_id, index = outcome_id.split(":")
    if prefix != "pm":
        raise ValueError(f"not an outcome id: {outcome_id!r}")
    return market_id, int(index)


def verify(rows: list[dict[str, Any]], markets: dict[str, dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """The texts of every outcome of the pool that verify, and the problems: a market that is missing, an outcome the market does
    not have, and a question or label whose sha256 differs from pool.jsonl's."""
    texts: dict[str, dict[str, Any]] = {}
    problems: list[str] = []
    for row in rows:
        for oid, hashes in row["text_sha256"].items():
            if oid in texts:
                continue
            market_id, index = parse_outcome_id(oid)
            doc = markets.get(market_id)
            if doc is None:
                problems.append(f"{oid}: market {market_id} is missing")
                continue
            question, labels = doc.get("question"), FE.parse_outcomes(doc.get("outcomes"))
            if not isinstance(question, str) or not isinstance(labels, list) or not 0 <= index < len(labels) \
                    or not isinstance(labels[index], str):
                problems.append(f"{oid}: market {market_id} has no outcome {index} with a text")
                continue
            bad = [name for name, text in (("question", question), ("label", labels[index]))
                   if hashlib.sha256(text.encode("utf-8")).hexdigest() != hashes[name]]
            if bad:
                problems.append(f"{oid}: sha256 differs for {', '.join(bad)}")
                continue
            texts[oid] = {"outcome": oid, "market": market_id, "question": question, "label": labels[index]}
    return texts, problems


def rebuild(rows: list[dict[str, Any]], slices: dict[str, Any], texts: dict[str, dict[str, Any]]) -> dict[int, bytes]:
    """The judge input of each slice, from the pool rows, the slices and the verified texts (build_pool's rendering)."""
    judge_by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row["op"] == "decide_relation":
            props = [texts[row["a"]], texts[row["b"]]]
        else:
            props = [texts[m] for m in row["members"]]
        judge_by_id[row["id"]] = BP.judge_item(row["id"], row["op"], props)
    return {s["slice"]: BP.judge_slice_bytes(s["ids"], judge_by_id) for s in slices["slices"]}


def slice_problems(rebuilt: dict[int, bytes], slices: dict[str, Any], local_dir: Path) -> list[str]:
    """Each rebuilt slice must have the sha256 published in slices.json, and the local copy's bytes when a local copy exists."""
    problems = []
    for s in slices["slices"]:
        data = rebuilt[s["slice"]]
        if hashlib.sha256(data).hexdigest() != s["sha256"]:
            problems.append(f"slice {s['slice']}: rebuilt sha256 differs from slices.json")
        local = local_dir / f"slice{s['slice']}.jsonl"
        if local.is_file() and local.read_bytes() != data:
            problems.append(f"slice {s['slice']}: the local judge file differs from the rebuilt one")
    return problems


def event_url(event_id: str) -> str:
    return f"{FE.GAMMA}/events/{urllib.parse.quote(str(event_id), safe='')}"


def verify_titles(rows: list[dict[str, Any]], events: dict[str, dict[str, Any]]) -> tuple[dict[str, str], list[str]]:
    """Each outcome's event title, checked against the sha256 in pool.jsonl, and the event id it names. Returns the titles that verify
    (by outcome id) and the problems."""
    titles: dict[str, str] = {}
    problems: list[str] = []
    for row in rows:
        for oid, hashes in row["text_sha256"].items():
            if oid in titles:
                continue
            doc = events.get(str(hashes["event"]))
            title = doc.get("title") if isinstance(doc, dict) else None
            if not isinstance(title, str):
                problems.append(f"{oid}: event {hashes['event']} is missing or has no title")
            elif hashlib.sha256(title.encode("utf-8")).hexdigest() != hashes["event_title"]:
                problems.append(f"{oid}: sha256 differs for the event title")
            else:
                titles[oid] = title
    return titles, problems


def fetch_events(rows: list[dict[str, Any]], client: FE.Client) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Each pool event, fetched by id (GET /events/<id>). A failure is reported as a problem."""
    events: dict[str, dict[str, Any]] = {}
    problems: list[str] = []
    ids = sorted({str(h["event"]) for row in rows for h in row["text_sha256"].values()})
    for event_id in ids:
        status, body = client.get(event_url(event_id))
        if status == 200:
            events[event_id] = json.loads(body)
        else:
            problems.append(f"event {event_id}: HTTP {status}")
    return events, problems


def fetch_markets(rows: list[dict[str, Any]], client: FE.Client) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Each pool market, fetched by id. A 404 is missing; any other failure is reported and counted as missing."""
    markets: dict[str, dict[str, Any]] = {}
    problems: list[str] = []
    ids = sorted({parse_outcome_id(oid)[0] for row in rows for oid in row["text_sha256"]})
    for market_id in ids:
        status, body = client.get(market_url(market_id))
        if status == 200:
            markets[market_id] = json.loads(body)
        else:
            problems.append(f"market {market_id}: HTTP {status}")
    return markets, problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=LOCAL_OUT)
    a = ap.parse_args(argv)
    out = a.out.resolve()
    if out == FE.ROOT or FE.ROOT in out.parents:
        print(f"rehydrate.py: refused: {out} is inside the repository; the texts stay outside it", file=sys.stderr)
        return 1
    pool_bytes = POOL.read_bytes()
    slices = json.loads(SLICES.read_text(encoding="utf-8"))
    if hashlib.sha256(pool_bytes).hexdigest() != slices["pool_sha256"]:
        print("rehydrate.py: pool.jsonl does not match slices.json (pool_sha256)", file=sys.stderr)
        return 1
    rows = [json.loads(line) for line in pool_bytes.decode("utf-8").split("\n") if line]
    client = FE.Client()
    markets, problems = fetch_markets(rows, client)
    texts, text_problems = verify(rows, markets)
    problems += text_problems
    events, event_fetch_problems = fetch_events(rows, client)
    titles, title_problems = verify_titles(rows, events)
    problems += event_fetch_problems + title_problems
    for oid in texts:
        texts[oid]["event_title"] = titles.get(oid)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "pool_texts.jsonl", "w", encoding="utf-8", newline="\n") as fh:
        for oid in sorted(texts):
            fh.write(json.dumps(texts[oid], sort_keys=True, ensure_ascii=False) + "\n")
    report: dict[str, Any] = {"markets": len({parse_outcome_id(o)[0] for r in rows for o in r["text_sha256"]}),
                              "markets_fetched": len(markets), "outcomes": len({o for r in rows for o in r["text_sha256"]}),
                              "outcomes_verified": len(texts), "problems": problems, "judge_slices": None}
    if not problems:
        rebuilt = rebuild(rows, slices, texts)
        judge_dir = out / "judge" / "slices"
        judge_dir.mkdir(parents=True, exist_ok=True)
        for number, data in rebuilt.items():
            (judge_dir / f"slice{number}.jsonl").write_bytes(data)
        slice_errors = slice_problems(rebuilt, slices, BP.JUDGE_SLICES)
        problems += slice_errors
        report["judge_slices"] = {"rebuilt": len(rebuilt), "problems": slice_errors}
    print(json.dumps(report, indent=1, sort_keys=True, ensure_ascii=False))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
