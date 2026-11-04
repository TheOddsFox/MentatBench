"""Builds the transfer pilot's candidate pool from the local snapshot (PREREG.md, "Pool" and "Slices"). Deterministic: one seed,
SEED, drives every draw in one fixed order, so the same snapshot gives the same bytes.

usage: python -B build_pool.py

Reads LOCAL snapshot/events.jsonl, after checking its sha256 against snapshot_manifest.json. Writes:
  - pool.jsonl, slices.json and pool_summary.json in this folder (published): opaque ids, outcome ids, the sha256 of each text,
    the structural type, the cluster, the judge slices as id lists and the sha256 of each judge slice file, and the counts;
  - LOCAL pool_texts.jsonl (the texts of the pool's outcomes, with each event's title) and judge/slices/slice<j>.jsonl (the judge
    input items with their prompts). These hold text and are never published.

A proposition is one outcome of one market: its text is the market's question and the outcome's label. A relation item is an
ordered pair of propositions (a, b). A group item is a set of 2 to 8 propositions from distinct markets, in a shuffled order. Judges
see only the opaque id, the item type, the prompt and the answer format. A spanning item's cluster is the smallest cluster id among
its members.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[3]  # the repository root
LOCAL = ROOT.parent / "mb-runs" / "transfer-pilot"  # outside the repository: text never goes in it
SNAPSHOT = LOCAL / "snapshot" / "events.jsonl"
MANIFEST = HERE / "snapshot_manifest.json"
POOL = HERE / "pool.jsonl"
SLICES = HERE / "slices.json"
POOL_SUMMARY = HERE / "pool_summary.json"
POOL_TEXTS = LOCAL / "pool_texts.jsonl"
JUDGE_SLICES = LOCAL / "judge" / "slices"
sys.dont_write_bytecode = True
sys.path[:0] = [str(ROOT / "python")]

from mentat_bench.children import systemone_child as CHILD  # noqa: E402  the child's own rendering and caps

SEED = "transfer-pilot:v1"  # random.Random(SEED): the one generator of the pool, the ids and the slices
MAX_QUESTION = 230  # characters; the child's instruction cap is 300 (python/mentat_bench/children/systemone_child.py:101)
SERIES_CAP = 3  # events per seriesSlug, by volume
CATEGORY_SHARE = 0.25  # no category (the slug of an event's first tag) holds more than this share of the kept events
UNTAGGED = "(untagged)"
MAX_ITEMS_PER_SLICE = 50
MAX_MEMBERS = 8
MIX_TRIES = 20  # redraws of a mixed group's order before the alternating fallback
RELATION_QUOTA = {"same_event_alt": 110, "threshold_ladder": 50, "date_ladder": 40, "trivial": 40, "same_topic": 90,
                  "random_cross": 70}
GROUP_QUOTA = {"event_fields": 60, "ladder": 40, "mixed": 30, "decoy": 30}

# Language heuristic (PREREG.md, "Pool"): a question is English unless it has more non-English function words than English ones,
# or more than 20% of its letters are outside ASCII (a few accented letters in a name do not make a question non-English).
ENGLISH_WORDS = frozenset({"the", "will", "be", "by", "of", "in", "is", "on", "to", "and", "or", "for", "at", "win", "who", "what",
                           "when", "does", "more", "than", "less", "over", "under", "between", "before", "after", "above", "below",
                           "from", "with", "not", "if", "this", "that", "are", "was", "has", "have"})
FOREIGN_WORDS = frozenset({"el", "los", "las", "del", "que", "por", "para", "una", "con", "es", "de", "und", "der", "das", "ist",
                           "nicht", "mit", "auf", "le", "les", "des", "est", "une", "pour", "dans", "avec", "et", "não", "uma",
                           "um", "ser", "sur", "qui"})
THRESHOLD = re.compile(r"\b(?:o/u|over/under|over|under|above|below|at least|reach|hit|more than|less than|exceed)\s*\$?(\d[\d,]*(?:\.\d+)?)",
                       re.IGNORECASE)
DATE = re.compile(r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\b(?:\s+\d{1,2}\b)?"
                  r"|\b20\d{2}\b")
INDEX_WORDS = frozenset({"player", "map", "game", "round", "set", "quarter", "q", "week", "race", "stage", "match", "leg", "half",
                         "period", "inning", "day", "episode", "season", "part"})  # a number after one of these names a position
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
SCORE = re.compile(r"\d\s*[-–:]\s*\d")  # a score such as 2-1, which is not a threshold
PLACEHOLDER = re.compile(r"\bplaceholder\b", re.IGNORECASE)  # a stand-in market, dropped before anything else is drawn
RELATION_ANSWER = "{\"relation\": \"EQUIV | IMPLIES | IMPLIED_BY | EXCLUDES | NONE | UNCLEAR\"}"


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def norm(text: str) -> str:
    """The comparison form of a question: lower case, words and digits only, single spaces."""
    return " ".join(re.findall(r"\w+", text.lower()))


def is_english(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    if not letters or sum(ord(c) > 127 for c in letters) > 0.2 * len(letters):
        return False
    words = re.findall(r"[a-zãõçéíóúáà]+", text.lower())
    return sum(w in FOREIGN_WORDS for w in words) <= sum(w in ENGLISH_WORDS for w in words)


def ineligible(market: dict[str, Any]) -> str | None:
    """The reason a market cannot give propositions, or None."""
    question, labels = market.get("question"), market.get("outcomes")
    if not isinstance(question, str) or not question.strip():
        return "no question"
    if len(question) > MAX_QUESTION:
        return "question over 230 characters"
    if not isinstance(labels, list) or len(labels) < 2 or not all(isinstance(x, str) and x.strip() for x in labels):
        return "outcomes not a list of two or more labels"
    if PLACEHOLDER.search(question) or PLACEHOLDER.search(market.get("groupItemTitle") or ""):
        return "placeholder market"  # Polymarket's stand-in markets (the word "placeholder" followed by a number), not real questions
    if not is_english(question):
        return "not English"
    return None


def _inside_date(question: str, start: int, end: int) -> bool:
    return any(d.start() < end and start < d.end() for d in DATE.finditer(question))


def _is_index_number(question: str, start: int, end: int) -> bool:
    """A number that names a position, not a level: it follows an index word (the two words before it), it is Q1-style (a q
    directly before it), it is a period marker after it (1Q, 2H, 3P, 1OT: uppercase, so 5h of hours is not one), or it is an ordinal
    (1st, 2nd, 3rd, 4th)."""
    before = re.findall(r"[a-z]+", question[:start].lower())[-2:]
    if any(w in INDEX_WORDS for w in before):
        return True
    if start >= 1 and question[start - 1] in "qQ" and (start < 2 or not question[start - 2].isalnum()):
        return True
    if re.match(r"(?:Q|H|P|OT)\b", question[end:end + 3]):
        return True
    return question[end:end + 2].lower() in ("st", "nd", "rd", "th")


def threshold_of(market: dict[str, Any]) -> tuple[float | None, str | None]:
    """The threshold a market names and its template: the question with that number masked. The number is the single number
    of the group item title when the question holds it (outside a score, a date and an index); otherwise the number after a
    threshold word in the question. A number inside a date, or one that names a position, is never a threshold."""
    question = market["question"]
    title = market.get("groupItemTitle") or ""
    numbers = NUMBER.findall(title)
    if len(numbers) == 1 and not SCORE.search(title):
        found = re.search(r"(?<![\d.,])" + re.escape(numbers[0]) + r"(?![\d.,])", question)
        if found and not _inside_date(question, found.start(), found.end()) \
                and not _is_index_number(question, found.start(), found.end()):
            return float(numbers[0].replace(",", "")), norm(question[:found.start()] + " thresholdmask " + question[found.end():])
    hit = THRESHOLD.search(question)
    if not hit or _inside_date(question, hit.start(1), hit.end(1)) or _is_index_number(question, hit.start(1), hit.end(1)):
        return None, None
    return float(hit.group(1).replace(",", "")), norm(question[:hit.start(1)] + " thresholdmask " + question[hit.end(1):])


def _child_view(prop: dict[str, Any]) -> dict[str, Any]:
    """The fields the child reads from a proposition (its id, its question and its label)."""
    return {"id": prop["id"], "question": prop["question"], "label": prop["label"]}


def _runs(members: list[dict[str, Any]]) -> int:
    """The number of runs of one event in a member order (two runs is two contiguous event blocks)."""
    return 1 + sum(members[i]["event"] != members[i - 1]["event"] for i in range(1, len(members)))


def _alternate(members: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A fallback order: the two events' members alternate, the larger event first, so a group of three or more has three or more runs."""
    first = [m for m in members if m["event"] == members[0]["event"]]
    other = [m for m in members if m["event"] != members[0]["event"]]
    big, small = (first, other) if len(first) >= len(other) else (other, first)
    out: list[dict[str, Any]] = []
    for i in range(len(big)):
        out.append(big[i])
        if i < len(small):
            out.append(small[i])
    return out


def date_of(market: dict[str, Any]) -> tuple[str | None, str | None]:
    """The first date token of a question (lower case) and the question's template with its dates masked."""
    question = market["question"]
    hit = DATE.search(question)
    if not hit:
        return None, None
    template = norm(DATE.sub(" datemask ", question))
    return hit.group(0).lower(), template if len(template.split()) >= 3 else None


def cap_categories(events: list[dict[str, Any]], stats: Counter) -> list[dict[str, Any]]:
    """Drops the lowest-volume event of the largest category, until no category holds more than CATEGORY_SHARE of the events.
    The events are in volume order, so the last event of a category is its lowest-volume one."""
    if len({e["category"] for e in events}) < 4:  # with fewer than four categories no set can hold a 25% share: refuse, do not empty
        raise ValueError("the category cap needs at least four categories among the events")
    kept = list(events)
    while kept:
        counts = Counter(e["category"] for e in kept)
        cat, n = max(counts.items(), key=lambda kv: (kv[1], kv[0]))
        if n <= CATEGORY_SHARE * len(kept):
            break
        index = max(i for i, e in enumerate(kept) if e["category"] == cat)
        del kept[index]
        stats["event dropped: category cap"] += 1
    return kept


def select_events(events: list[dict[str, Any]], stats: Counter) -> list[dict[str, Any]]:
    """The kept events, as records (id, volume, series, category, title, markets). The series cap, the category cap and the
    duplicate-question rule are applied together until nothing changes, so a question is represented by its highest-volume copy
    among the events that survive the caps, and every cap holds on the final set. Each event is counted once, under the reason it
    was dropped."""
    ordered = sorted(events, key=lambda e: (-float(e["volume"]), str(e["id"])))
    records: dict[str, dict[str, Any]] = {}
    for e in ordered:
        markets = [m for m in e["markets"] if not _note(m, stats)]
        if not markets:
            stats["event dropped: no eligible market"] += 1
            continue
        tags = e.get("tags") or []
        records[str(e["id"])] = {"id": str(e["id"]), "volume": float(e["volume"]), "series": e.get("seriesSlug") or None,
                                 "category": (tags[0].get("slug") or UNTAGGED) if tags else UNTAGGED,
                                 "title": e.get("title"), "eligible": markets, "markets": []}
    current = list(records)  # volume order
    while True:
        series_count: Counter = Counter()
        after_series = []
        for eid in current:
            series = records[eid]["series"]
            if series is not None:
                if series_count[series] >= SERIES_CAP:
                    stats["event dropped: series cap"] += 1
                    continue
                series_count[series] += 1
            after_series.append(records[eid])
        capped = cap_categories(after_series, stats)
        seen: set[str] = set()
        survivors: list[str] = []
        for rec in capped:
            markets = []
            for m in rec["eligible"]:
                key = norm(m["question"])
                if key in seen:
                    continue
                seen.add(key)
                markets.append(m)
            rec["markets"] = markets
            if markets:
                survivors.append(rec["id"])
            else:
                stats["event dropped: all markets are duplicate questions"] += 1
        if survivors == current:
            break
        current = survivors
    stats["market dropped: duplicate question (in kept events)"] = sum(
        len(records[eid]["eligible"]) - len(records[eid]["markets"]) for eid in current)
    kept = []
    for eid in current:
        rec = records[eid]
        kept.append({"id": rec["id"], "volume": rec["volume"], "series": rec["series"], "category": rec["category"],
                     "title": rec["title"], "markets": [_market_record(m, rec["id"]) for m in rec["markets"]]})
    return kept


def _note(market: dict[str, Any], stats: Counter) -> bool:
    """True when the market is not eligible; counts the reason."""
    reason = ineligible(market)
    if reason:
        stats[f"market dropped: {reason}"] += 1
    return reason is not None


def _market_record(m: dict[str, Any], event_id: str) -> dict[str, Any]:
    threshold, threshold_key = threshold_of(m)
    date_key, date_template = date_of(m)
    return {"id": str(m["id"]), "event": event_id, "question": m["question"], "labels": list(m["outcomes"]),
            "threshold": threshold, "threshold_key": threshold_key, "date": date_key, "date_template": date_template}


def build_pool(events: list[dict[str, Any]], seed: str = SEED) -> dict[str, Any]:
    """The pool, its slices and its stats, from the snapshot's event records (the layout of events.jsonl)."""
    rng = random.Random(seed)
    stats: Counter = Counter()
    kept = select_events(events, stats)
    stats["events kept"] = len(kept)

    # Clusters: a union-find over the kept events, joining events that share a seriesSlug. A cluster is numbered by its smallest
    # event id.
    parent = {e["id"]: e["id"] for e in kept}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    first_of_series: dict[str, str] = {}
    for e in sorted(kept, key=lambda e: e["id"]):
        if e["series"] is not None:
            if e["series"] in first_of_series:
                parent[find(e["id"])] = find(first_of_series[e["series"]])
            else:
                first_of_series[e["series"]] = e["id"]
    smallest: dict[str, str] = {}
    for e in sorted(kept, key=lambda e: e["id"]):
        smallest.setdefault(find(e["id"]), e["id"])
    numbering = {root: f"C{i + 1:04d}" for i, root in enumerate(sorted(smallest, key=lambda r: smallest[r]))}
    for e in kept:
        e["cluster"] = numbering[find(e["id"])]
        for m in e["markets"]:
            m["cluster"], m["series"], m["category"] = e["cluster"], e["series"], e["category"]
            m["props"] = [{"id": f"pm:{m['id']}:{i}", "market": m["id"], "event": e["id"], "label": label,
                           "question": m["question"], "event_title": e["title"], "cluster": e["cluster"]}
                          for i, label in enumerate(m["labels"])]

    ctx = _Context(kept)
    relations = _relations(rng, ctx, stats)
    groups = _groups(rng, ctx, stats)
    items = [{"op": "decide_relation", "type": kind, "outcomes": [a, b]} for kind, a, b in relations]
    items += [{"op": "decide_group", "type": kind, "outcomes": members} for kind, members in groups]
    rng.shuffle(items)
    for index, item in enumerate(items, start=1):
        item["id"] = f"T{index:04d}"
    return _assemble(items, kept, stats)


class _Context:
    """The draw sources of the generators, in the kept events' volume order, and the events that are mates of each event: a
    shared seriesSlug (series mates) or a shared category."""

    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.events = events
        self.multi = [e for e in events if len(e["markets"]) >= 2]
        self.binary = [m for e in events for m in e["markets"] if len(m["labels"]) == 2]
        self.thr_events = [e for e in events if sum(m["threshold"] is not None for m in e["markets"]) >= 2]
        self.date_events = [e for e in events if sum(m["date"] is not None for m in e["markets"]) >= 2]
        # Ladder classes for groups: the markets of one event that share a template (a threshold template or a date template).
        classes: dict[tuple, list[dict[str, Any]]] = {}
        for e in events:
            for m in e["markets"]:
                if m["threshold_key"] is not None:
                    classes.setdefault((e["id"], "threshold", m["threshold_key"]), []).append(m)
                if m["date_template"] is not None:
                    classes.setdefault((e["id"], "date", m["date_template"]), []).append(m)
        self.ladder_classes = [members for key, members in sorted(classes.items()) if len(members) >= 2]
        self.series_mates: dict[str, list[dict[str, Any]]] = {e["id"]: [] for e in events}
        self.mates: dict[str, list[dict[str, Any]]] = {e["id"]: [] for e in events}
        for e in events:
            for f in events:
                if f is e:
                    continue
                if e["series"] is not None and f["series"] == e["series"]:
                    self.series_mates[e["id"]].append(f)
                    self.mates[e["id"]].append(f)
                elif e["category"] == f["category"]:
                    self.mates[e["id"]].append(f)
        self.series_events = [e for e in events if self.series_mates[e["id"]]]


def _relations(rng: random.Random, ctx: _Context, stats: Counter) -> list[tuple[str, dict, dict]]:
    out: list[tuple[str, dict, dict]] = []
    seen: set[frozenset] = set()

    def take(kind: str, a: dict, b: dict) -> bool:
        if a["id"] == b["id"] or (kind != "trivial" and a["market"] == b["market"]):
            return False
        key = frozenset((a["id"], b["id"]))
        if key in seen:
            return False
        if rng.random() < 0.5:  # the order of an ordered pair is a coin flip, so Yes and No appear on both sides
            a, b = b, a
        if len(CHILD._relation_question(_child_view(a), _child_view(b))["instructions"]) > CHILD.MAX_INSTRUCTION_CHARS:
            stats[f"relation rejected: child instructions over {CHILD.MAX_INSTRUCTION_CHARS} characters"] += 1
            return False  # the child would refuse this pair: redraw
        seen.add(key)
        out.append((kind, a, b))
        return True

    def ladder_pair(event: dict[str, Any], kind: str) -> tuple[dict, dict] | None:
        if kind == "threshold_ladder":
            cands = [m for m in event["markets"] if m["threshold"] is not None]
        else:
            cands = [m for m in event["markets"] if m["date_template"] is not None]
        if len(cands) < 2:
            return None
        m1, m2 = rng.sample(cands, 2)
        if kind == "threshold_ladder":  # the same template with the number masked, and two different numbers
            ok = m1["threshold_key"] == m2["threshold_key"] and m1["threshold"] != m2["threshold"]
        else:  # the same template with the dates masked, and two different dates
            ok = m1["date_template"] == m2["date_template"] and m1["date"] != m2["date"]
        return (rng.choice(m1["props"]), rng.choice(m2["props"])) if ok else None

    def draw(kind: str) -> tuple[dict, dict] | None:
        if kind == "same_event_alt":
            m1, m2 = rng.sample(rng.choice(ctx.multi)["markets"], 2)
            return rng.choice(m1["props"]), rng.choice(m2["props"])
        if kind == "threshold_ladder":
            return ladder_pair(rng.choice(ctx.thr_events), kind) if ctx.thr_events else None
        if kind == "date_ladder":
            return ladder_pair(rng.choice(ctx.date_events), kind) if ctx.date_events else None
        if kind == "trivial":
            m = rng.choice(ctx.binary)
            return m["props"][0], m["props"][1]
        if kind == "same_topic":
            e1 = rng.choice(ctx.events)
            if not ctx.mates[e1["id"]]:
                return None
            e2 = rng.choice(ctx.mates[e1["id"]])
            return rng.choice(rng.choice(e1["markets"])["props"]), rng.choice(rng.choice(e2["markets"])["props"])
        e1, e2 = rng.sample(ctx.events, 2)  # random_cross
        return rng.choice(rng.choice(e1["markets"])["props"]), rng.choice(rng.choice(e2["markets"])["props"])

    for kind, quota in RELATION_QUOTA.items():
        made, attempts = 0, 0
        while made < quota and attempts < 300 * quota:
            attempts += 1
            pair = draw(kind)
            if pair and take(kind, *pair):
                made += 1
        stats[f"relation candidates: {kind}"] = made
        stats[f"relation shortfall: {kind}"] = quota - made
    return out


def _groups(rng: random.Random, ctx: _Context, stats: Counter) -> list[tuple[str, list[dict]]]:
    out: list[tuple[str, list[dict]]] = []
    seen: set[frozenset] = set()

    def take(kind: str, members: list[dict] | None) -> bool:
        if members is None or not 2 <= len(members) <= min(MAX_MEMBERS, CHILD.MAX_K, CHILD.MAX_CHAIN):
            return False
        if len({m["market"] for m in members}) != len(members):
            return False
        key = frozenset(m["id"] for m in members)
        if key in seen:
            return False
        seen.add(key)
        out.append((kind, members))
        return True

    def draw(kind: str) -> list[dict] | None:
        if kind == "event_fields":  # one outcome from each of several markets of one event
            e = rng.choice(ctx.multi)
            n = rng.randint(2, min(MAX_MEMBERS, len(e["markets"])))
            return [rng.choice(m["props"]) for m in rng.sample(e["markets"], n)]
        if kind == "ladder":  # the markets of one event that share a template (thresholds or dates)
            cls = rng.choice(ctx.ladder_classes)
            n = rng.randint(2, min(MAX_MEMBERS, len(cls)))
            return [rng.choice(m["props"]) for m in rng.sample(cls, n)]
        if kind == "mixed":  # two events of one series, so one cluster; the members are shuffled across the two events
            e1 = rng.choice(ctx.series_events)
            e2 = rng.choice(ctx.series_mates[e1["id"]])
            n = rng.randint(2, MAX_MEMBERS)
            k1 = rng.randint(1, n - 1)
            if k1 > len(e1["markets"]) or n - k1 > len(e2["markets"]):
                return None
            members = [rng.choice(m["props"]) for m in rng.sample(e1["markets"], k1) + rng.sample(e2["markets"], n - k1)]
            for _ in range(MIX_TRIES):  # redraw the order until the events are not two contiguous blocks (three or more members)
                if n < 3 or _runs(members) != 2:
                    break
                rng.shuffle(members)
            else:
                members = _alternate(members)
            return members
        n = rng.randint(2, MAX_MEMBERS)  # decoy: one outcome from each of several events, in at least two clusters
        members = [rng.choice(rng.choice(e["markets"])["props"]) for e in rng.sample(ctx.events, n)]
        return members if len({m["cluster"] for m in members}) >= 2 else None

    sources = {"event_fields": ctx.multi, "ladder": ctx.ladder_classes, "mixed": ctx.series_events, "decoy": ctx.events}
    for kind, quota in GROUP_QUOTA.items():
        made, attempts = 0, 0
        while sources[kind] and made < quota and attempts < 300 * quota:
            attempts += 1
            if take(kind, draw(kind)):
                made += 1
        stats[f"group candidates: {kind}"] = made
        stats[f"group shortfall: {kind}"] = quota - made
    return out


def slice_sizes(n: int, per_slice: int = MAX_ITEMS_PER_SLICE) -> list[int]:
    """n items in ceil(n / per_slice) slices of as equal size as possible, the larger slices first."""
    if n == 0:
        return []
    count = math.ceil(n / per_slice)
    base, extra = divmod(n, count)
    return [base + 1 if j < extra else base for j in range(count)]


def render_relation(a: dict[str, Any], b: dict[str, Any]) -> str:
    return (f"Proposition A.\nQuestion: {a['question']}\nOutcome: {a['label']}\n\n"
            f"Proposition B.\nQuestion: {b['question']}\nOutcome: {b['label']}\n\n"
            "Answer with the relation between A and B.")


def judge_item(item_id: str, op: str, props: list[dict[str, Any]]) -> dict[str, Any]:
    """The judge input item of a pool item: its opaque id, its type, its prompt and its answer format. props are the items'
    propositions (question and label), in relation order (a, b) or group order. No event title is shown."""
    if op == "decide_relation":
        return {"id": item_id, "type": "relation", "prompt": render_relation(props[0], props[1]), "answer_format": RELATION_ANSWER}
    return {"id": item_id, "type": "group", "prompt": render_group(props), "answer_format": group_answer_format(len(props))}


def text_problems(rows: list[dict[str, Any]], texts: dict[str, dict[str, Any]]) -> list[str]:
    """Each outcome's question, label and event title, checked against the sha256 that pool.jsonl publishes (and the event id).
    texts maps an outcome id to its local record. Returns the mismatches and the outcomes that have no record."""
    problems = []
    for row in rows:
        for oid, hashes in row["text_sha256"].items():
            t = texts.get(oid)
            if t is None:
                problems.append(f"{oid}: no local text")
                continue
            if sha256_text(t["question"]) != hashes["question"] or sha256_text(t["label"]) != hashes["label"]:
                problems.append(f"{oid}: question or label differs from pool.jsonl")
            if sha256_text(t.get("event_title") or "") != hashes["event_title"] or str(t.get("event")) != str(hashes["event"]):
                problems.append(f"{oid}: event title or event id differs from pool.jsonl")
    return problems


def judge_slice_bytes(ids: list[str], judge_by_id: dict[str, dict[str, Any]]) -> bytes:
    return "".join(json.dumps(judge_by_id[i], ensure_ascii=False) + "\n" for i in ids).encode("utf-8")


def group_answer_format(n: int) -> str:
    return ("{\"kind\": \"none | exactly_one | at_most_one | at_least_k | at_most_k | ordered_chain | UNCLEAR\", "
            f"\"lower\": integer 0 to {n}, \"upper\": integer 0 to {n}, "
            "\"chain\": [outcome numbers, strongest first; [] unless the kind is ordered_chain]}")


def render_group(members: list[dict[str, Any]]) -> str:
    lines = [f"Set of {len(members)} outcomes, numbered 1 to {len(members)}."]
    for i, m in enumerate(members, start=1):
        lines.append(f"{i}. Question: {m['question']}\n   Outcome: {m['label']}")
    lines.append("Answer with the constraint on how many of the outcomes are true, and the chain if there is one.")
    return "\n".join(lines)


def _assemble(items: list[dict[str, Any]], events: list[dict[str, Any]], stats: Counter) -> dict[str, Any]:
    """The published rows, the local texts and judge items, the slices, and the summary."""
    rows, texts, judge = [], {}, []
    for item in items:
        props = item["outcomes"]
        for p in props:
            texts[p["id"]] = {"outcome": p["id"], "market": p["market"], "event": p["event"], "cluster": p["cluster"],
                              "event_title": p["event_title"], "question": p["question"], "label": p["label"]}
        row = {"id": item["id"], "op": item["op"], "structural_type": item["type"],
               "cluster": min(p["cluster"] for p in props), "span": len({p["cluster"] for p in props}) > 1,
               "text_sha256": {p["id"]: {"question": sha256_text(p["question"]), "label": sha256_text(p["label"]),
                                         "event": p["event"], "event_title": sha256_text(p["event_title"] or "")}
                               for p in props}}
        if item["op"] == "decide_relation":
            row.update(a=props[0]["id"], b=props[1]["id"])
        else:
            row["members"] = [p["id"] for p in props]
        judge.append(judge_item(item["id"], item["op"], props))
        rows.append(row)
    rows.sort(key=lambda r: r["id"])
    pool_bytes = "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows).encode("utf-8")
    by_id = {j["id"]: j for j in judge}
    slices, start = [], 0
    for number, size in enumerate(slice_sizes(len(judge)), start=1):
        ids = [j["id"] for j in judge[start:start + size]]
        start += size
        slices.append({"slice": number, "ids": ids, "items": size,
                       "relations": sum(by_id[i]["type"] == "relation" for i in ids),
                       "groups": sum(by_id[i]["type"] == "group" for i in ids),
                       "sha256": hashlib.sha256(judge_slice_bytes(ids, by_id)).hexdigest()})
    doc = {"schema": "mentat-bench-transfer-slices/1", "seed": SEED, "max_items": MAX_ITEMS_PER_SLICE,
           "pool_sha256": hashlib.sha256(pool_bytes).hexdigest(), "items": len(judge), "slices": slices}
    used = list({p["id"]: p for it in items for p in it["outcomes"]}.values())  # distinct outcomes, as outcomes_used counts them
    spanning: Counter = Counter(r["structural_type"] for r in rows if r["span"])
    summary = {"items": {"relation": sum(r["op"] == "decide_relation" for r in rows),
                         "group": sum(r["op"] == "decide_group" for r in rows)},
               "structural_type": dict(sorted(Counter(r["structural_type"] for r in rows).items())),
               "clusters_in_pool": len({r["cluster"] for r in rows}),
               "clusters_in_kept_events": len({e["cluster"] for e in events}),
               "spanning_items": sum(r["span"] for r in rows),
               "spanning_by_type": dict(sorted(spanning.items())),
               "kept_events": len(events), "kept_markets": sum(len(e["markets"]) for e in events),
               "kept_categories": len({e["category"] for e in events}),
               "outcomes_used": len(texts), "child_instruction_limit": CHILD.MAX_INSTRUCTION_CHARS,
               "label_No_share_of_outcomes_used": round(sum(p["label"] == "No" for p in used) / len(used), 4) if used else None,
               "stats": dict(sorted(stats.items()))}
    return {"rows": rows, "pool_bytes": pool_bytes, "texts": [texts[k] for k in sorted(texts)], "judge": judge,
            "slices": doc, "summary": summary, "event_clusters": {e["id"]: e["cluster"] for e in events}}


def write_outputs(result: dict[str, Any], pool: Path = POOL, slices: Path = SLICES, summary: Path = POOL_SUMMARY,
                  texts: Path = POOL_TEXTS, judge_dir: Path = JUDGE_SLICES) -> None:
    pool.write_bytes(result["pool_bytes"])
    slices.write_text(json.dumps(result["slices"], indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    summary.write_text(json.dumps(result["summary"], indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    texts.parent.mkdir(parents=True, exist_ok=True)
    with open(texts, "w", encoding="utf-8", newline="\n") as fh:
        for row in result["texts"]:
            fh.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
    judge_dir.mkdir(parents=True, exist_ok=True)
    by_id = {j["id"]: j for j in result["judge"]}
    for s in result["slices"]["slices"]:
        (judge_dir / f"slice{s['slice']}.jsonl").write_bytes(judge_slice_bytes(s["ids"], by_id))


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    data = SNAPSHOT.read_bytes()
    if hashlib.sha256(data).hexdigest() != manifest["events_sha256"]:
        print("build_pool.py: events.jsonl does not match snapshot_manifest.json", file=sys.stderr)
        return 1
    events = [json.loads(line) for line in data.decode("utf-8").split("\n") if line]
    result = build_pool(events)
    write_outputs(result)
    print(json.dumps(result["summary"], indent=1, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
