"""Fixed harness: lexical blocking -> typed adapter decisions -> union-find classes -> IR export.

1. One decide_binding per eligible outcome (budget stop here means no checkpoint: BuildIncomplete). Records stream
   through once and are spilled to disk; only compact per-record state stays in memory. Each usage call is written to
   usage_calls.jsonl and flushed at its commit, before the next call (fsynced every USAGE_FLUSH_EVERY calls and at each
   checkpoint), so the gateway never holds the call list. An adapter that keeps failing (MAX_CONSECUTIVE_ERRORS in a row, or over
   half of at least 100 decisions) raises AdapterFatal: no checkpoint exists yet in phase 1, so nothing is written.
2. Deterministic token-set blocking over market questions proposes candidate market pairs. Blocking units are markets,
   not outcome rows, so sibling outcomes never inflate token frequencies or fill the top-k. Tokens in more than a
   relative cutoff of markets (DF_FRAC, never below DF_FLOOR, never above max_df) carry no blocking signal. Each
   market's top_k partner markets are the best-scoring markets over all markets (either direction); a candidate market
   pair is any pair that either market lists. Every candidate pair expands to the record pairs of its outcomes, and the
   outcomes of one market are candidates for each other. Pairs go to a file; the build deadline is checked while
   blocking (a stop keeps the pairs found so far). Adapters that replay a full theory (extra_theory(), i.e. the oracle)
   skip phases 2 to 3 and only export.
2b. Groups (adapters that declare decide_group and supports_groups; not full replay; use_groups). Candidate sets come
   from group_candidates: s1 markets, s2 events, s3 template and entity families, s4 opt-in agglomeration. Each has at
   most group_max_members outcomes, capped by the adapter's advertised max_members. One decide_group call per
   candidate, committed in batches. A decision is admitted if check_group accepts it, it is not a duplicate, no two
   bound members of an AMO-shaped kind share a literal, no two bound members of an ordered chain share a class (a
   degenerate chain: a same-class duplicate, or complementary members p and not-p; checked at admission only, so a
   chain whose members share a class by a later EQUIV merge is not dropped at finalize), and its scope axes stay within
   max_interpretations. The group phase has its own budget: at most group_budget_frac of the
   spend ceiling and of the deadline left at its start, and at most max_group_calls candidates. The share counts the spend of the committed group decisions: the paid calls of a discarded batch are excluded on a resume (they still count toward the spend ceiling). Reaching that share ends the group phase only (group_budget_stopped); the
   global budget stops are unchanged. A failing adapter in this phase is fatal to the build (groups_fatal and
   adapter_fatal): the phase, the relation phase and the seal stop, like a relation-phase AdapterFatal.
3. One decide_relation per candidate pair, committed in batches of `checkpoint_every` pairs. Every candidate pair is
   asked, whatever the groups say: a group can be dropped or downgraded later, and its pairs can carry other relations
   (pairs_skipped_by_groups stays 0, kept for compatibility). A budget stop discards the unfinished batch; completed
   batches are the latest checkpoint. An AdapterFatal here discards the unfinished batch and seals the latest
   checkpoint (summary adapter_fatal).
4. EQUIV decisions merge alias classes with parity (a union-find over class names). An EQUIV that contradicts the parity
   already recorded (for example same class, opposite negation) is rejected and emits no constraint. The same holds for
   IMPLIES, IMPLIED_BY and EXCLUDES between outcomes of one class when they force the class meaning to a value another
   relation already forced otherwise.
5. Export. The base is made consistent at finalize by admission (harness/admit.py: the units in a tiered order, a unit is
kept only if it keeps the bindings satisfiable together with the kept units; see _admission_order and docs/ir.md, Base
consistency), or, for scoped or very large theories and when admission is off, by the post-hoc repair described below. A
candidates build's scoped theory (plain 2-CNF scoped groups) is admitted without its scoped units, which are then settled
per interpretation (_settle_scoped).
The theory is a list of units: the accepted relations, then the accepted groups (an ordered chain gives one
   unit per link). A unit's 2-CNF part is checked in process, per interpretation: bindings plus every unit that holds
   in that interpretation. A unit that makes an interpretation inconsistent is dropped, or, when it is scoped to
   several interpretations, loses only that interpretation. Relation drops count against max_relation_drops, group
   drops against MAX_GROUP_DROPS. An AMO-shaped group whose members became one literal through EQUIV merges is dropped.
   Non-2-CNF parts (the ALO of an exactly_one over 3+ members, at_least_k and at_most_k beyond pairwise) are deferred
   to mb-engine. When the engine rejects the base, the engine is first asked about the base without deferred units
   (a rejection there is final); otherwise galloping, then bisection over the deferred units (from the bound the last
   round left: _settle_with_engine) finds the first one that causes it: an
   exactly_one is downgraded to at_most_one (groups_downgraded), anything else is dropped. Before the engine is asked, a
   deferred part that is impossible against the non-deferred base (N0) is dropped (or downgraded, for an exactly_one) without
   the engine (_drop_infeasible_deferred: unscoped theories, within the admission bound; it reaches the localization's base,
   see its docstring; a check that cannot run records deferred_check_skipped). When the localization stops short (the round
   cap or the deadline), the rest are dropped and the base is sealed if the engine accepts it, as a partial build
   (_seal_unresolved); a seal over the group cap, or a seal probe the deadline skips, is refused as described there.
   Then the submission is written and mb-engine confirms its base (summary base_probe). Nothing calls a model after this.

Pairwise cost. Group decisions use the AMO encodings pairwise: an AMO over m members is m(m-1)/2 clauses. The size cap
(at most 256, default 64) keeps this bounded; sequential counters are not used.

Cost controls. An adapter that advertises batching (protocol 2, see docs/local_protocol.md) gets its binding, group and
relation calls in batches of min(advertised max_batch, batch_size); a failed item is retried alone, and every decision
goes through the same health counters. top_k is the blocking partner count per market. Complement dedup: when both
markets of a candidate unit pair are binary and every outcome is bound (one class, opposite negation), the pair of
their first outcomes is asked and the other three record pairs are derived from it when its answer is a named relation (_complement_rows); otherwise they are asked (dedup_fallback_asked).
complement_fallback (all by default) picks what a representative with no named answer asks: all three pairs, only the
both-flipped one (flipped), or none; the pairs it leaves unasked are counted in dedup_fallback_skipped. A derived
relation follows from the representative and the bindings, so it claims nothing the representative does not, and
unresolved outcomes are never deduplicated. pair_order (index by default) asks the market pairs by blocking score,
highest first (score), so that a budget or deadline stop cuts the weakest pairs; siblings of one market stay first.
max_pairs caps the candidate pairs of the relation phase (pairs_capped);
max_calls is a hard cap on the decision items of the build, bindings included: every charged attempt counts
(Gateway.items_left), and a retry runs only if it fits, so the cap is never exceeded. A binding retry also leaves the
items of the bindings not yet asked (Gateway.reserve), so every binding gets its first attempt. A fresh build with a cap
below its outcome count (one binding call per outcome) is refused before any call. The group and relation phases stop at
the cap (calls_capped), and a relation batch cut by the cap keeps its complete units. A resume charges every paid call
in usage_calls.jsonl, including those made after the last checkpoint (a crash's discarded batch), against the cap and the
spend ceiling; their decisions are asked again.

Memory, stated plainly. Still O(outcomes) in memory: the blocking token index and partner lists, per-record class ids
and negation, the id-to-index map of the group phase, and the market and event member lists of the group candidates.
The s3 family index keeps one 16-byte digest per family and retires a family once it outgrows max_members. Records,
decisions and the relation and group logs are streamed from disk. A 1M-outcome build still needs the external-sort
work of Step 4d.

Checkpoints. Work state lives in `<out_dir>.build/` as append-only logs plus `checkpoint.json`, which is replaced
atomically (write, fsync, rename) after each committed batch and names exactly how much of each log is valid.
`finalize_checkpoint` turns the latest checkpoint into a submission directory; `build` calls it on normal exit and it
can be called on a work directory left behind by a killed build. Checkpoints written before the group phase have no
group keys and finalize with no groups. The build policy (the knobs the build used, and the adapter name) is written
as build_policy.json beside the submission and is sealed with it.
A budget stop (summary budget_stopped, with stopped_in naming the phase) keeps its work dir only with keep_work. A resume
with a larger spend_ceiling_usd or build_deadline_s clears it when the new limits leave headroom (budget_extended records
the limits); a smaller spend_ceiling_usd or build_deadline_s is refused, and every other profile key must match the
checkpoint's profile (_check_resume_profile).
"""

from __future__ import annotations

import bisect
import faulthandler
import functools
import hashlib
import json
import math
import os
import re
import shutil
import sys
import threading
import time
import warnings
from array import array
from collections import Counter, deque
from itertools import chain, islice, product
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from .. import engine, ir
from ..adapters.base import (
    MAX_GROUP_AXES,
    USAGE_KINDS,
    USAGE_OPS,
    AdapterError,
    AdapterFatal,
    DecisionError,
    ScopeError,
    check_binding,
    check_group,
    check_relation_decision,
    check_usage,
    finite_number,
    reading_picks,
)
from ..gateway import BudgetExceeded, Gateway, peak_rss_mb
from ..seal import replace_with_retry

CHECKPOINT_EVERY = 5000
MAX_DF = 4000  # blocking tokens in more markets than this carry no signal; work per market <= tokens * MAX_DF
DF_FRAC = 0.3  # ... and so do tokens in more than this fraction of markets
DF_FLOOR = 64  # the fractional cutoff never drops below this many markets (small corpora keep shared tokens)
TOP_K = 128  # best-scoring partner markets per market
USAGE_FLUSH_EVERY = 1000  # usage calls per fsync
MAX_CONSECUTIVE_ERRORS = 20  # adapter failures in a row before the build is aborted
ERROR_FRACTION_LIMIT = 0.5  # ... or this share of failed decisions, once ERROR_FRACTION_MIN_DECISIONS have been made
ERROR_FRACTION_MIN_DECISIONS = 100
MAX_RELATION_DROPS = 20000  # relation constraints a build may drop to restore a consistent base
MAX_RELATION_DROP_FRAC = 0.25  # ... and at most this share of the relations it asked (the contradiction rate)
PRUNE_PROGRESS_EVERY = 30.0  # seconds between progress lines on stderr while the base is repaired
PRUNE_METHODS = ("scc", "bisect")  # scc: conflict-cycle extraction (default); bisect: the old prefix bisection
MAX_GROUP_DROPS = 200  # rounds of engine localization of deferred group constraints
# Group drops are counted per group row. Measured with the admission order: toy_native-1.5B drops 351 of 4,659 rows,
# toy10k-0.5B 6,881 of 9,322 (74%), so the defaults must admit that share (see docs/ir.md, Base consistency).
MAX_GROUP_DROPS_DEFAULT = 20000  # group rows a build may drop to restore a consistent base (the build and CLI default)
MAX_GROUP_DROP_FRAC = 0.9  # ... and at most this share of the committed group rows
ADMIT_MAX_LITERALS = 2**17  # admission (harness/admit.py) keeps a bit matrix of literals^2 bits: 2 GB at this cap, ~3x at peak
# Provenance REFs only gold knows (axiom ids, event ids). Only gold-backed adapters (oracle, gold_view) may supply them;
# any other adapter's are stripped from its binding evidence and counted (gold_refs_rejected). See docs/local_protocol.md.
GOLD_ONLY_REF = re.compile(r"ax:.+|src:event:[^:]+:event_title")  # exact shapes: a market id "event" keeps src:event:event_title
GOLD_BACKED_ADAPTERS = ("oracle", "gold_view")  # the names of the gold-backed adapters: only checkpoints before the policy recorded gold_backed use them


def _gold_backed(policy: dict[str, Any] | None) -> bool:
    """Whether a build may carry gold-only REFs, from its policy. A policy without the gold_backed key (a build before it
    was recorded) falls back to the adapter name; no policy at all (a checkpoint of a build before policies) is not gold."""
    if not policy:
        return False
    return bool(policy.get("gold_backed", policy.get("adapter") in GOLD_BACKED_ADAPTERS))
_TOKEN = re.compile(r"[a-z0-9]+")
_STOP_CHECK_EVERY = 1000


class BuildIncomplete(RuntimeError):
    """The budget stopped the bindings phase before any checkpoint existed. Nothing is written."""


class BaseInconsistentError(RuntimeError):
    """The base theory is inconsistent and cannot be repaired by dropping at most max_relation_drops relation
    constraints, or mb-engine disagrees with the in-process check. The submission is not valid; cli exits 1."""


class BaseCheckIncomplete(RuntimeError):
    """The build deadline passed before the base theory was verified consistent. Nothing is written; the
    checkpoint is kept."""


class CorruptCheckpoint(RuntimeError):
    """A checkpoint.json that is not a JSON object (corrupt, truncated or of another shape). The work dir keeps it."""


class BindingCountMismatch(ValueError):
    """--max-calls was checked against an outcome count that the bindings did not read. The build's usage log and work
    dir are kept (the paid calls are on disk); no checkpoint exists, so nothing is resumed."""


# The keys every checkpoint written by build has, and the kinds they have: a resume or a finalize reads them by subscript
# or as counts, so a checkpoint without one, or with a value of another kind, is CorruptCheckpoint.
CHECKPOINT_KEYS = {
    "decided_bytes": "int", "relations_bytes": "int", "usage_bytes": "int", "pairs_total": "int",
    "pairs_considered": "int", "relations_committed": "int", "equiv_rejected": "int", "relation_rejected": "int",
    "pairs_derived": "int", "pairs_derived_none": "int", "dedup_representatives": "int", "dedup_named": "int",
    "dedup_undecided": "int", "dedup_none": "int", "dedup_fallback_asked": "int", "constraints": "int",
    "budget_stopped": "bool", "adapter_fatal": "bool", "wall_seconds": "number", "peak_rss_mb": "number",
}
# The group-phase keys: a checkpoint of phase "groups" must have them (the phase reads them by subscript). A checkpoint
# from before the group phase lacks them: a phase before "groups" starts that phase with these defaults (see build), and a
# finalize reads them as 0. Where present, they must have their kind.
GROUP_PHASE_KEYS = {
    "groups_bytes": "int", "group_candidates": "int", "groups_committed": "int", "group_errors": "int",
    "groups_rejected": "int", "group_scope_rejected": "int", "groups_fatal": "bool", "group_budget_stopped": "bool",
    "axes": "dict",
}
# Keys an older checkpoint may lack (the resume defaults them); typed when present.
CHECKPOINT_OPTIONAL_KEYS = {
    "pairs_ahead": "int", "dedup_fallback_skipped": "int", "group_cursor": "int", "group_committed_spend": "number",
    "discarded_spend": "number", "group_spend0": "number", "group_t0": "number", "decisions_bytes": "int",
    "decisions_complete": "bool",
}


def _kind_ok(value: Any, kind: str) -> bool:
    if kind == "bool":
        return isinstance(value, bool)
    if kind == "int":
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0
    if kind == "dict":
        return isinstance(value, dict)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    raise ValueError(f"unknown kind {kind!r}")


def _checkpoint_problem(ckpt: dict[str, Any]) -> str | None:
    """What is wrong with a checkpoint object (no phase or an unknown one, a missing or wrongly typed key, a policy that is
    not an object), or None."""
    if "phase" not in ckpt:
        return "no phase"
    if ckpt["phase"] not in PHASE_ORDER:
        return f"phase {ckpt['phase']!r} is not a build phase"
    required = {**CHECKPOINT_KEYS, **(GROUP_PHASE_KEYS if ckpt["phase"] == "groups" else {})}
    for key, kind in required.items():
        if key not in ckpt:
            return f"no {key}"
        if not _kind_ok(ckpt[key], kind):
            return f"{key} is not a {kind}"
    for key, kind in {**GROUP_PHASE_KEYS, **CHECKPOINT_OPTIONAL_KEYS}.items():
        if key in ckpt and not _kind_ok(ckpt[key], kind):
            return f"{key} is not a {kind}"
    if ckpt.get("policy") is not None and not isinstance(ckpt["policy"], dict):
        return "policy is not an object"
    return None


def paid_calls(work: Path) -> int:
    """The paid calls in a work dir's usage log: each row is one charged call (0 when there is no log)."""
    path = work / "usage_calls.jsonl"
    if not path.exists():
        return 0
    with open(path, "rb") as fh:
        return sum(1 for _ in fh)


class RepairBudgetStopped(BaseCheckIncomplete):
    """The post-hoc repair ran past --repair-budget-s before the base theory was verified consistent. Nothing is written;
    the checkpoint is kept. A larger repair_budget_s finishes it. `deadline_passed` says the build deadline has also passed,
    so a larger build_deadline_s is needed too. A repair runs only when admission was refused or does not apply, or in the engine fallback."""

    def __init__(self, message: str, deadline_passed: bool = False):
        super().__init__(message)
        self.deadline_passed = deadline_passed


def token_set(text: str) -> set[str]:
    return {t for t in _TOKEN.findall(text.lower()) if len(t) > 1}


def market_key(record_id: Any, position: int) -> str:
    """Blocking unit of a record: its market (outcome IDs are venue:market:index). A record without such an ID
    is its own unit."""
    if isinstance(record_id, str) and ":" in record_id:
        return record_id.rsplit(":", 1)[0]
    return f"#{position}"


def _in_list(tops: array, offs: array, owner: int, value: int) -> bool:
    """Is `value` among the (sorted) partner list of unit `owner`?"""
    seg = tops[offs[owner] : offs[owner + 1]]
    pos = bisect.bisect_left(seg, value)
    return pos < len(seg) and seg[pos] == value


def iter_candidate_pairs(
    questions: Callable[[], Iterable[str]],
    n: int,
    *,
    max_df_frac: float = DF_FRAC,
    max_df: int = MAX_DF,
    top_k: int = TOP_K,
    stop: Callable[[], bool] = lambda: False,
) -> Iterator[tuple[int, int]]:
    """Lexical blocking over n units (markets). A unit's partners are its top_k best-scoring other units by
    idf-weighted shared tokens, over all units. Tokens present in more than min(max_df, max(DF_FLOOR,
    max_df_frac * n)) units are skipped. Each unordered pair that either unit lists is yielded once, as
    (lower, higher). `questions()` must return the same texts in the same order each call (three passes).
    Memory is O(units * top_k) ints for the partner lists; time per unit is O(tokens * cutoff). `stop()` is
    polled every few units; when it fires, the pairs among the units scored so far are yielded."""
    for _, i, j in _scored_candidate_pairs(questions, n, max_df_frac=max_df_frac, max_df=max_df, top_k=top_k, stop=stop,
                                           scored=False):
        yield i, j


def _scored_candidate_pairs(
    questions: Callable[[], Iterable[str]],
    n: int,
    *,
    max_df_frac: float = DF_FRAC,
    max_df: int = MAX_DF,
    top_k: int = TOP_K,
    stop: Callable[[], bool] = lambda: False,
    scored: bool,
) -> Iterator[tuple[float, int, int]]:
    """iter_candidate_pairs with each pair's blocking score: (score, lower, higher). The score is the idf-weighted shared
    token sum that the listing unit gave the pair (symmetric, so the same from either list). Without `scored` the score
    is 0.0 and no score array is kept (the default build's memory)."""
    df: Counter[str] = Counter()
    for q in questions():
        df.update(token_set(q))
    cap = min(max_df, max(DF_FLOOR, max_df_frac * n))
    keep = {t for t, c in df.items() if c <= cap}
    weight = {t: math.log(n / df[t]) for t in keep}
    postings: dict[str, array] = {t: array("I") for t in keep}
    for i, q in enumerate(questions()):
        for t in token_set(q) & keep:
            postings[t].append(i)
    tops, offs = array("I"), array("Q", [0])  # partners of unit i: tops[offs[i]:offs[i + 1]], sorted
    scores = array("d") if scored else None  # the score of each entry of tops (scored only)
    done = 0
    for i, q in enumerate(questions()):
        if i % _STOP_CHECK_EVERY == 0 and stop():
            break
        score: dict[int, float] = {}
        for t in sorted(token_set(q) & keep):
            for j in postings[t]:
                if j != i:
                    score[j] = score.get(j, 0.0) + weight[t]
        ranked = sorted(score.items(), key=lambda item: (-item[1], item[0]))[:top_k]
        picked = sorted(ranked)  # by partner index
        tops.extend(j for j, _ in picked)
        if scores is not None:
            scores.extend(s for _, s in picked)
        offs.append(len(tops))
        done = i + 1
    for i in range(done):
        for k in range(offs[i], offs[i + 1]):
            j = tops[k]
            if j >= done:
                continue
            if j < i and _in_list(tops, offs, j, i):
                continue  # the pair is emitted from the lower unit's list, which also lists this one
            lo, hi = (j, i) if j < i else (i, j)
            yield (scores[k] if scores is not None else 0.0), lo, hi


def _score_order(scored: Iterable[tuple[float, int, int]]) -> Iterator[tuple[int, int]]:
    """The unit pairs of `scored` by score, highest first. Ties keep the order they were yielded in (the index order)."""
    scores, pairs = array("d"), array("I")
    for score, i, j in scored:
        scores.append(score)
        pairs.extend((i, j))
    for k in sorted(range(len(scores)), key=scores.__getitem__, reverse=True):  # reverse keeps the sort stable
        yield pairs[2 * k], pairs[2 * k + 1]


class _Units:
    """Blocking units (markets) numbered by first appearance. unit_of[i] is the unit of record i; is_first[i]
    marks the first record of its unit (the one whose question represents the unit)."""

    def __init__(self) -> None:
        self.index: dict[str, int] = {}
        self.count = 0
        self.unit_of = array("I")
        self.is_first = bytearray()

    def add(self, key: str) -> None:
        unit = self.index.get(key)
        self.is_first.append(unit is None)
        if unit is None:
            unit = self.index[key] = self.count
            self.count += 1
        self.unit_of.append(unit)


def _csr(unit_of: array, n_units: int) -> tuple[array, array]:
    """Records grouped by unit: the records of unit u are rows[start[u]:start[u + 1]]."""
    start = array("Q", [0]) * (n_units + 1)
    for u in unit_of:
        start[u + 1] += 1
    for u in range(n_units):
        start[u + 1] += start[u]
    fill = array("Q", start)
    rows = array("I", [0]) * len(unit_of)
    for i, u in enumerate(unit_of):
        rows[fill[u]] = i
        fill[u] += 1
    return start, rows


def _record_pairs(unit_pairs: Iterable[tuple[int, int]], start: array, rows: array) -> Iterator[tuple[int, int]]:
    """Every record of unit u paired with every record of unit v, for each candidate unit pair (u, v)."""
    for u, v in unit_pairs:
        for a in rows[start[u] : start[u + 1]]:
            for b in rows[start[v] : start[v + 1]]:
                yield (a, b) if a < b else (b, a)


def _sibling_pairs(start: array, rows: array, n_units: int) -> Iterator[tuple[int, int]]:
    """Outcomes of one market are candidates for each other: they are complements or exclusive alternatives."""
    for u in range(n_units):
        members = rows[start[u] : start[u + 1]]
        for x in range(len(members)):
            for y in range(x + 1, len(members)):
                yield members[x], members[y]


def candidate_pairs(records: list[dict[str, Any]], **kwargs: Any) -> list[tuple[int, int]]:
    """In-memory convenience wrapper over `iter_candidate_pairs`: records grouped by market, expanded to
    sorted record-index pairs (sibling outcomes included)."""
    recs = list(records)
    units = _Units()
    for i, r in enumerate(recs):
        units.add(market_key(r.get("id"), i))
    first_questions = [r["question"] for r, is_first in zip(recs, units.is_first) if is_first]
    start, rows = _csr(units.unit_of, units.count)
    unit_pairs = iter_candidate_pairs(lambda: iter(first_questions), units.count, **kwargs)
    return sorted(chain(_sibling_pairs(start, rows, units.count), _record_pairs(unit_pairs, start, rows)))


GROUP_MAX_MEMBERS = 64  # largest candidate group, and largest group a decision may name
DEFAULT_GROUP_SOURCES = ("s1", "s2", "s3")
GROUP_SOURCES = (*DEFAULT_GROUP_SOURCES, "s4")  # s4 (bounded agglomeration) is opt-in
GROUP_MAX_MEMBERS_LIMIT = 256  # the most a decision may name, and the largest --group-max-members
GROUP_BUDGET_FRAC = 0.25  # the group phase's share of the spend ceiling and of the deadline left at its start
# Model calls stop at (1 - FINALIZE_RESERVE_FRAC) x build_deadline_s; a call that crosses that point is discarded and charged.
# This leaves finalize the rest, but does not bound it (admission without a refusal may run past the deadline). The stop
# bounds the calls only for adapters that honour timeout_s (the local adapter does).
FINALIZE_RESERVE_FRAC = 0.05
MAX_GROUP_CALLS = 20000  # candidate calls the group phase may make
BATCH_SIZE = 64  # decisions per batch call, capped by the adapter's advertised max_batch (--batch-size)


def group_candidates(
    records: Iterable[dict[str, Any]],
    sources: Iterable[str],
    max_members: int = GROUP_MAX_MEMBERS,
    pairs: Iterable[tuple[int, int]] = (),
) -> Iterator[tuple[str, list[int]]]:
    """Candidate group member sets over records, read once in order, as (source, sorted record indices). Each set is
    yielded once, by its first source. Sets outside 2..max_members members are not candidates.
    s1: the outcomes of one market (complementary pairs of binary markets included).
    s2: the outcomes of one event.
    s3: template families inside one event: records whose questions agree on every whitespace word but a one- or
        two-word slot, and records of one event that share a word bigram (entity families). A family that outgrows
        max_members is retired at once, so its members are not collected any further.
    s4 (opt-in): bounded agglomeration over `pairs` (candidate record pairs): a same-event pair merges its groups
        while the merged group stays within max_members.
    Memory: one integer per record and source in use, plus one 16-byte digest per s3 family."""
    wanted = set(sources)
    seen: set[bytes] = set()

    def fresh(members: list[int]) -> bool:
        if not 2 <= len(members) <= max_members:
            return False
        digest = hashlib.blake2b(array("I", members).tobytes(), digest_size=16).digest()
        if digest in seen:
            return False
        seen.add(digest)
        return True

    markets: dict[str, list[int]] = {}
    events: dict[str, list[int]] = {}
    families: dict[bytes, list[int] | None] = {}  # None: retired (more members than max_members)
    event_no: dict[str, int] = {}
    event_of = array("I")  # event number of each record, 0 when it has none (s4 reads it)
    for i, rec in enumerate(records):
        if "s1" in wanted:
            markets.setdefault(market_key(rec.get("id"), i), []).append(i)
        title = rec.get("event_title")
        event_of.append(event_no.setdefault(title, len(event_no) + 1) if title else 0)
        if not title:
            continue
        if "s2" in wanted:
            events.setdefault(title, []).append(i)
        if "s3" in wanted:
            words = rec["question"].split()
            keys = [f"{title}\x1e{len(words)}\x1e{w}\x1e{k}\x1e" + " ".join(words[:k] + ["\x00"] * w + words[k + w:])
                    for w in (1, 2) for k in range(len(words) - w + 1)]
            keys += [f"{title}\x1f{words[k]} {words[k + 1]}" for k in range(len(words) - 1)]
            for key in keys:
                digest = hashlib.blake2b(key.encode("utf-8"), digest_size=16).digest()
                if digest not in families:
                    families[digest] = [i]
                    continue
                members = families[digest]
                if members is None or members[-1] == i:
                    continue
                if len(members) >= max_members:
                    families[digest] = None
                else:
                    members.append(i)
    if "s1" in wanted:
        for members in markets.values():
            if fresh(members):
                yield "s1", members
    if "s2" in wanted:
        for members in events.values():
            if fresh(members):
                yield "s2", members
    if "s3" in wanted:
        for members in families.values():
            if members is not None and fresh(members):
                yield "s3", members
    if "s4" in wanted:
        parent = list(range(len(event_of)))
        size = [1] * len(event_of)

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for i, j in pairs:
            if not event_of[i] or event_of[i] != event_of[j]:
                continue
            ri, rj = find(i), find(j)
            if ri != rj and size[ri] + size[rj] <= max_members:
                parent[rj] = ri
                size[ri] += size[rj]
        groups: dict[int, list[int]] = {}
        for i in range(len(event_of)):
            groups.setdefault(find(i), []).append(i)
        for members in groups.values():
            if fresh(members):
                yield "s4", members


class ParityUnionFind:
    """Class names merged by EQUIV. parity(x) records meaning(x) = meaning(parent) XOR parity."""

    def __init__(self) -> None:
        self.parent: dict[str, str] = {}
        self.parity: dict[str, bool] = {}

    def find(self, x: str) -> tuple[str, bool]:
        self.parent.setdefault(x, x)
        self.parity.setdefault(x, False)
        start, path, root = x, [], x
        while self.parent[root] != root:
            path.append(root)
            root = self.parent[root]
        acc = False
        for node in reversed(path):
            acc ^= self.parity[node]
            self.parity[node] = acc
            self.parent[node] = root
        return root, (self.parity[start] if start != root else False)

    def union(self, a: str, b: str, differ: bool) -> bool:
        """Record meaning(a) == meaning(b) XOR differ. Returns False (and changes nothing) on contradiction."""
        ra, pa = self.find(a)
        rb, pb = self.find(b)
        if ra == rb:
            return (pa ^ pb) == differ
        self.parent[ra] = rb
        self.parity[ra] = pa ^ pb ^ differ
        return True


_ONE_SIDED = ("IMPLIES", "IMPLIED_BY", "EXCLUDES")


class _ClassParity:
    """Class names merged by EQUIV, and the meaning each merged class is forced to by one-sided relations.
    A relation that contradicts what is already recorded is rejected up front (as an EQUIV with the wrong
    parity always was) and emits nothing. Only relations between outcomes of one class can be checked this
    way; contradictions across classes are left to the base-consistency check in finalize_checkpoint."""

    def __init__(self) -> None:
        self.uf = ParityUnionFind()
        self.forced: dict[str, bool] = {}  # class root -> value its meaning must take

    def equiv(self, a: str, b: str, differ: bool) -> bool:
        """meaning(a) == meaning(b) XOR differ, for class names a and b. False (and no change) on contradiction."""
        ra, pa = self.uf.find(a)
        rb, pb = self.uf.find(b)
        if ra == rb:
            return (pa ^ pb) == differ
        link = pa ^ pb ^ differ  # meaning(ra) == meaning(rb) XOR link
        if ra in self.forced and rb in self.forced and self.forced[rb] != self.forced[ra] ^ link:
            return False
        self.uf.union(a, b, differ)
        if ra in self.forced:
            self.forced[rb] = self.forced.pop(ra) ^ link
        return True

    def one_sided(self, a: str, neg_a: bool, b: str, neg_b: bool, rel: str) -> bool:
        """IMPLIES(x, y), IMPLIED_BY(x, y) or EXCLUDES(x, y) for outcomes x (class a) and y (class b). Between two
        outcomes of one class it forces the class meaning: IMPLIES and IMPLIED_BY when the outcomes have opposite
        polarity, EXCLUDES when they have the same one. False if that conflicts with an earlier forced value."""
        ra, pa = self.uf.find(a)
        rb, pb = self.uf.find(b)
        if ra != rb:
            return True
        p_a, p_b = pa != neg_a, pb != neg_b  # outcome meaning = meaning(root) XOR p
        if rel == "IMPLIES" and p_a != p_b:
            value = p_a
        elif rel == "IMPLIED_BY" and p_a != p_b:
            value = not p_a
        elif rel == "EXCLUDES" and p_a == p_b:
            value = p_a
        else:
            return True
        return self.forced.setdefault(ra, value) == value


class _AdapterHealth:
    """Counts failed decisions. A decision that failed alone (a malformed decision, or an AdapterError of a single
    call) is one failed decision. A whole call that failed (one AdapterError shared by the items of a batch, see
    _book_errors) is one failure in a row, and its items are failed decisions. Raises AdapterFatal after
    `max_consecutive` failures in a row, or once at least ERROR_FRACTION_MIN_DECISIONS decisions have more than
    ERROR_FRACTION_LIMIT of them failing. A successful decision resets the run."""

    def __init__(self, max_consecutive: int):
        self.max_consecutive = max_consecutive
        self.decisions = self.failures = self.run = 0

    def record(self, ok: bool) -> None:
        self.decisions += 1
        if ok:
            self.run = 0
            return
        self.failures += 1
        self._failed()

    def record_failed_call(self, items: int) -> None:
        """One failed call that carried `items` decisions: one failure in a row, `items` failed decisions."""
        self.decisions += items
        self.failures += items
        self._failed()

    def _failed(self) -> None:
        self.run += 1
        if self.run >= self.max_consecutive:
            raise AdapterFatal(f"{self.run} adapter failures in a row; the adapter is not answering usefully")
        if self.decisions >= ERROR_FRACTION_MIN_DECISIONS and self.failures > ERROR_FRACTION_LIMIT * self.decisions:
            raise AdapterFatal(f"{self.failures} of {self.decisions} adapter decisions failed")


def _one(gw: Gateway, op: str, *args: Any) -> Any:
    """The answer of one single call gw.<op>(*args): the decision, or the AdapterError left after the retries."""
    try:
        return getattr(gw, op)(*args)
    except AdapterError as exc:
        return exc


def _decide_many(gw: Gateway, op: str, calls: list[tuple[Any, ...]], batched: bool) -> list[Any]:
    """One answer per call of `op` (argument tuples): one batch call when `batched` (the caller keeps it within the
    adapter's batch size, see _batch_size), else one single call each. An item the gateway gave up on is its
    AdapterError; a BudgetExceeded or AdapterFatal propagates."""
    if batched:
        return gw.decide_batch(op, calls)
    return [_one(gw, op, *args) for args in calls]


def _batch_size(adapter: Any, op: str, flag: int) -> int:
    """Calls per batch of `op`: the adapter's advertised max_batch capped by the batch-size flag. 1 (single calls) unless
    the adapter supports batching and advertises this op."""
    if not getattr(adapter, "supports_batch", False):
        return 1
    advertised = (getattr(adapter, "advertised_max_batch", None) or {}).get(op)
    return 1 if advertised is None else max(1, min(int(advertised), flag))


def _book_errors(raws: list[Any], health: _AdapterHealth) -> None:
    """Health for the AdapterErrors among one batch of answers. An AdapterError shared by several items is one failed
    call (the gateway gave up on the whole batch): one failure in a row, its items failed decisions. Any other
    AdapterError is one failed decision. Successful answers are booked by the settle functions."""
    shared = Counter(id(raw) for raw in raws if isinstance(raw, AdapterError))
    booked: set[int] = set()
    for raw in raws:
        if not isinstance(raw, AdapterError):
            continue
        if shared[id(raw)] == 1:
            health.record(False)
        elif id(raw) not in booked:
            booked.add(id(raw))
            health.record_failed_call(shared[id(raw)])


def _settle_binding(raw: Any, record: dict[str, Any], health: _AdapterHealth) -> dict[str, Any]:
    if isinstance(raw, AdapterError):  # booked by _book_errors
        decision = {"status": "unresolved", "class": None, "negated": False, "missing": ["adapter_error"]}
    else:
        try:
            decision = check_binding(raw)
        except DecisionError:
            health.record(False)
            decision = {"status": "unresolved", "class": None, "negated": False, "missing": ["malformed_decision"]}
        else:
            health.record(True)
    return {"id": record["id"], **decision}


def _strip_gold_refs(row: dict[str, Any]) -> int:
    """Removes the gold-only REFs from a settled binding's evidence (a non-gold-backed adapter cannot know them) and
    returns how many were removed. A binding left without evidence keeps the derived REFs of its market."""
    refs = row.get("evidence")
    if not refs:
        return 0
    kept = [ref for ref in refs if not GOLD_ONLY_REF.fullmatch(ref)]
    if len(kept) == len(refs):
        return 0
    if kept:
        row["evidence"] = kept
    else:
        del row["evidence"]
    return len(refs) - len(kept)


def _settle_relation(raw: Any, health: _AdapterHealth) -> tuple[str, float | None]:
    """(relation, conf) of one answer; a malformed answer is UNRESOLVED (booked by _book_errors for an AdapterError)."""
    if isinstance(raw, AdapterError):
        return "UNRESOLVED", None
    try:
        rel, conf = check_relation_decision(raw)
    except DecisionError:
        health.record(False)
        return "UNRESOLVED", None
    health.record(True)
    return rel, conf


_NAMED = {  # allowed (a, b) truth values of each relation, read as a to b
    "EQUIV": frozenset({(0, 0), (1, 1)}),
    "IMPLIES": frozenset({(0, 0), (0, 1), (1, 1)}),
    "IMPLIED_BY": frozenset({(0, 0), (1, 0), (1, 1)}),
    "EXCLUDES": frozenset({(0, 0), (0, 1), (1, 0)}),
    "NONE": frozenset({(0, 0), (0, 1), (1, 0), (1, 1)}),
}
_SWAP = {"IMPLIES": "IMPLIED_BY", "IMPLIED_BY": "IMPLIES"}  # the same relation read from b to a


def _complement_table() -> dict[tuple[str, int, int], str]:
    """(relation, flip a, flip b) -> the relation of (a XOR flip a, b XOR flip b) given the relation of (a, b). The
    result is the smallest named relation containing every image pair, so it is entailed by the original relation
    and the complement bindings, and as tight as the vocabulary allows. Exact cases: EQUIV both flipped (EQUIV),
    IMPLIES flipping b (EXCLUDES), IMPLIES both (IMPLIED_BY), IMPLIED_BY flipping a (EXCLUDES), IMPLIED_BY both
    (IMPLIES), EXCLUDES flipping one (IMPLIED_BY or IMPLIES). Inexact, weaker than the image: EQUIV flipping one is
    XOR, whose tightest name is EXCLUDES; IMPLIES flipping a, IMPLIED_BY flipping b and EXCLUDES both are OR, NONE.
    UNRESOLVED stays UNRESOLVED."""
    table = {("UNRESOLVED", fa, fb): "UNRESOLVED" for fa in (0, 1) for fb in (0, 1)}
    for rel, allowed in _NAMED.items():
        for fa in (0, 1):
            for fb in (0, 1):
                image = {(a ^ fa, b ^ fb) for a, b in allowed}
                table[rel, fa, fb] = min((name for name, s in _NAMED.items() if image <= s), key=lambda n: len(_NAMED[n]))
    return table


_COMPLEMENT = _complement_table()


def _complement_rows(i: int, j: int, rel: str, mate: array) -> list[tuple[int, int, str]]:
    """The three record pairs that the representative (i, j), i < j, with relation rel stands for, as (x, y, relation)
    with x < y: the other outcome of i's market, of j's market, or of both, in place of i or j."""
    rows = []
    for fa, fb in ((1, 0), (0, 1), (1, 1)):
        x = mate[i] if fa else i
        y = mate[j] if fb else j
        r = _COMPLEMENT[rel, fa, fb]
        if x > y:
            x, y, r = y, x, _SWAP.get(r, r)
        rows.append((x, y, r))
    return rows


DERIVING = frozenset({"EQUIV", "IMPLIES", "IMPLIED_BY", "EXCLUDES"})  # answers a representative can derive from
# the values of checkpoints written before the flags
FLAG_DEFAULTS = {"complement_fallback": "all", "pair_order": "index", "candidates_sha256": None, "candidates_gold_informed": False}
COMPLEMENT_FALLBACKS = ("all", "flipped", "none")  # what a representative with no named answer asks (--complement-fallback)
_FALLBACK_PAIRS = {"all": (0, 1, 2), "flipped": (2,), "none": ()}  # its complement pairs asked: indices into _complement_pairs
PAIR_ORDERS = ("index", "score")  # the order the candidate pairs are asked in (--pair-order)
PARTIAL_FILE = "PARTIAL.json"  # written beside a complete build that stopped at a cap or a budget (see partial_reasons)
HISTORY_FILE = "checkpoints.jsonl"  # keep_work builds: every checkpoint's state, one line each (see mentat_bench.ceilings)
PARTIAL_REASONS = ("budget_stopped", "pairs_capped", "calls_capped", "group_budget_stopped", "deferred_unresolved")


def _complement_pairs(i: int, j: int, mate: array) -> list[tuple[int, int]]:
    """The three record pairs a representative (i, j) stands for, as (x, y) with x < y, in the order of _complement_rows."""
    pairs = []
    for fa, fb in ((1, 0), (0, 1), (1, 1)):
        x = mate[i] if fa else i
        y = mate[j] if fb else j
        pairs.append((x, y) if x < y else (y, x))
    return pairs


def partial_reasons(summary: dict[str, Any]) -> list[str]:
    """Why a build's theory is partial: the caps and budgets that stopped it (empty for a complete build)."""
    return [reason for reason in PARTIAL_REASONS if summary.get(reason)]


def build_status(summary: dict[str, Any]) -> str:
    """complete, incomplete (adapter failure), or the first partial reason."""
    if summary.get("adapter_fatal") or summary.get("groups_fatal"):
        return "incomplete"
    reasons = partial_reasons(summary)
    return reasons[0] if reasons else "complete"


def _complement_mates(unit_of: array, n_units: int, cls_id: array, neg: bytearray) -> array:
    """mate[i]: the other outcome of record i when i's market is binary (two records) and both are bound to one class
    with opposite negation (so their meanings are complements); -1 otherwise."""
    mate = array("i", [-1]) * len(unit_of)
    start, rows = _csr(unit_of, n_units)
    for u in range(n_units):
        if start[u + 1] - start[u] != 2:
            continue
        i, j = rows[start[u]], rows[start[u] + 1]
        if cls_id[i] >= 0 and cls_id[i] == cls_id[j] and neg[i] != neg[j]:
            mate[i], mate[j] = j, i
    return mate


def _dedup_role(i: int, j: int, mate: array | None, unit_of: array) -> str:
    """"plain" (asked as it is), "representative" (asked; its complement pairs are derived) or "derived" (not asked).
    i < j. Sibling outcomes of one market are always plain."""
    if mate is None or mate[i] < 0 or mate[j] < 0 or unit_of[i] == unit_of[j]:
        return "plain"
    return "representative" if i < mate[i] and j < mate[j] else "derived"


COMPLEMENT_PAIRS = 3  # the record pairs a representative stands for, besides its own


def _whole_units(roles: list[str], fallback_calls: int, budget: int) -> tuple[int, bool]:
    """(length, cut) under --max-calls: the longest prefix of a batch that ends at a unit boundary and whose decisions fit
    `budget`. A unit is a plain pair or a representative with the derived pairs after it. Derived pairs are never asked; a
    representative is charged its own decision and the fallbacks a NONE answer may ask, so no call is refused in the middle
    of a unit. `cut` says the budget left a unit out."""
    starts = sorted({0, len(roles), *(k for k, role in enumerate(roles) if role != "derived")})
    end, spent = 0, 0
    for start, nxt in zip(starts, starts[1:]):
        cost = 0 if roles[start] == "derived" else 1 + (fallback_calls if roles[start] == "representative" else 0)
        if spent + cost > budget:
            return end, True
        spent += cost
        end = nxt
    return end, False


def _pairs_ahead(roles: list[str], pending: int) -> int:
    """The derived pairs counted but not yet passed by the cursor, after a batch with these roles. A representative is
    counted with its complement pairs when it is decided, so its derived pairs past the batch are counted now. `pending`
    is what the previous batch left: its representative's derived pairs, which open this batch (never a representative
    themselves), so they are passed first."""
    last = max((k for k, role in enumerate(roles) if role != "derived"), default=None)
    if last is None:
        return max(0, pending - len(roles))
    if roles[last] != "representative":
        return 0
    return max(0, COMPLEMENT_PAIRS - (len(roles) - 1 - last))


def _complete_prefix(roles: list[str], rels: list[tuple[str, float | None]], got: int, fallback_calls: int) -> tuple[int, int, int]:
    """(candidates, asked pairs, fallback pairs) of the complete units of a batch under --max-calls. A unit is complete when
    its answers are in: a plain pair's answer, or a representative's answer and, unless that answer is named, its
    fallbacks (`fallback_calls` each; `got` answers were given). Derived pairs need no answer of their own."""
    cut = asks = fallbacks = 0
    k = 0
    while k < len(roles):
        end = k + 1
        while end < len(roles) and roles[end] == "derived":
            end += 1
        if roles[k] != "derived":
            if asks == len(rels):
                break
            if roles[k] == "representative" and rels[asks][0] not in DERIVING:
                if fallbacks + fallback_calls > got:
                    break
                fallbacks += fallback_calls
            asks += 1
        cut = k = end
    return cut, asks, fallbacks


def _relation_row(i: int, j: int, rel: str, rep_line: int, conf: float | None) -> str:
    """One relations.jsonl line: [i, j, rel] for a plain or representative row, [i, j, rel, rep_line] for a derived row (the
    line of its representative). A decision's conf is a fifth field when the child sent one (old logs have none)."""
    row = [i, j, rel] if rep_line < 0 else [i, j, rel, rep_line]
    if conf is not None:
        row = [i, j, rel, rep_line, conf]
    return json.dumps(row)


def _relation_expr(rel: str, a: str, b: str) -> dict[str, Any] | None:
    if rel == "EQUIV":
        return {"op": "iff", "a": ir.var(a), "b": ir.var(b)}
    if rel == "IMPLIES":
        return {"op": "implies", "a": ir.var(a), "b": ir.var(b)}
    if rel == "IMPLIED_BY":
        return {"op": "implies", "a": ir.var(b), "b": ir.var(a)}
    if rel == "EXCLUDES":
        return {"op": "atmost", "k": 1, "args": [ir.var(a), ir.var(b)]}
    return None


class _RecordStore:
    """Records spilled to a JSONL file with an offset table: sequential scans and random access
    without keeping the texts in memory."""

    def __init__(self, path: Path):
        self._path = path
        self._fh = open(path, "w+b")
        self._reader: Any = None  # a read-only handle for get(): a read-write handle slows down with use (measured)
        self.offsets = array("Q")

    def append(self, record: dict[str, Any]) -> None:
        self._fh.seek(0, os.SEEK_END)
        self.offsets.append(self._fh.tell())
        self._fh.write(ir.dumps(record).encode("utf-8") + b"\n")

    @classmethod
    def reopen(cls, path: Path) -> "_RecordStore":
        """The store of an existing records file (a resumed build), without truncating it."""
        store = cls.__new__(cls)
        store._path = path
        store._fh = store._reader = open(path, "rb")  # read-only: a resumed build only reads its records
        store.offsets = array("Q")
        position = 0
        for line in store._fh:
            store.offsets.append(position)
            position += len(line)
        return store

    def __len__(self) -> int:
        return len(self.offsets)

    def get(self, i: int) -> dict[str, Any]:
        if self._reader is None:  # all appends are flushed before the first read (the records are complete by then)
            self._fh.flush()
            self._reader = open(self._path, "rb")
        self._reader.seek(self.offsets[i])
        return json.loads(self._reader.readline())

    def __iter__(self) -> Iterator[dict[str, Any]]:
        self._fh.flush()
        self._fh.seek(0)
        for line in self._fh:
            yield json.loads(line)

    def close(self) -> None:
        if self._reader is not None and self._reader is not self._fh:
            self._reader.close()
        self._fh.close()


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    replace_with_retry(tmp, path)


def _append(fh: Any, lines: Iterable[str]) -> int:
    """Appends lines, flushes to disk, returns the new valid length of the log. The length is the file's size, not
    fh.tell(): a log opened in append mode, then cut back by _apply_resume_logs (a resume), has a stale position."""
    for line in lines:
        fh.write(line + "\n")
    fh.flush()
    os.fsync(fh.fileno())
    return os.fstat(fh.fileno()).st_size


def _jsonl_prefix(path: Path, nbytes: int) -> Iterator[Any]:
    """Rows of a JSONL log, limited to its first `nbytes` bytes (the checkpointed prefix). Nothing is read for 0 bytes,
    so a log that a checkpoint predates (no group log in old work dirs) needs no file."""
    if nbytes <= 0:
        return
    with open(path, "rb") as fh:
        remaining = nbytes
        while remaining > 0:
            line = fh.readline()
            if not line:
                break
            remaining -= len(line)
            yield json.loads(line)


def _positive_int(name: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer, got {value!r}")
    return value


class _Replay:
    """A checkpoint replayed compactly: decided rows as IDs, class ids and negation, plus the accepted EQUIV
    merges. Binding rows and constraints are streamed from the work logs on every export."""

    def __init__(self, work: Path, ckpt: dict[str, Any], strip_gold: bool = False):
        self.work, self.ckpt, self.strip_gold = work, ckpt, strip_gold
        self.ids: list[str] = []
        names: dict[str, int] = {}
        self.cls_id, self.neg = array("i"), bytearray()
        self.gold_refs_stripped = 0  # gold-only REFs of a build from before the guard, removed on every export (rows)
        for row in _jsonl_prefix(work / "decided.jsonl", ckpt["decided_bytes"]):
            if strip_gold:
                self.gold_refs_stripped += _strip_gold_refs(row)
            self.ids.append(row["id"])
            bound = row["status"] == "bound"
            self.cls_id.append(names.setdefault(row["class"], len(names)) if bound else -1)
            self.neg.append(bool(row["negated"]))
        self.cls_name = list(names)
        self.uf = ParityUnionFind()
        for i, j, rel, _ in self.relations():
            if rel == "EQUIV" and self.cls_id[i] >= 0 and self.cls_id[j] >= 0:
                self.uf.union(self.cls_name[self.cls_id[i]], self.cls_name[self.cls_id[j]], self.neg[i] != self.neg[j])

    def relations(self) -> Iterator[tuple[int, int, str, int]]:
        """(i, j, relation, representative line) per relation row; the representative line is -1 for a plain or
        representative row, and the line index of its representative for a derived row."""
        for row in _jsonl_prefix(self.work / "relations.jsonl", self.ckpt["relations_bytes"]):
            yield row[0], row[1], row[2], row[3] if len(row) > 3 else -1

    def relation_confidences(self) -> Iterator[float | None]:
        """The optional conf of each relation row, in the order of relations() (the fifth field; None when absent)."""
        for row in _jsonl_prefix(self.work / "relations.jsonl", self.ckpt["relations_bytes"]):
            yield row[4] if len(row) > 4 else None

    def _normalised(self, i: int) -> tuple[str, bool]:
        root, parity = self.uf.find(self.cls_name[self.cls_id[i]])
        return root, parity != bool(self.neg[i])

    def rows(self) -> Iterator[dict[str, Any]]:
        for row in _jsonl_prefix(self.work / "decided.jsonl", self.ckpt["decided_bytes"]):
            if self.strip_gold:
                _strip_gold_refs(row)
            if row["status"] == "bound":
                root, parity = self.uf.find(row["class"])
                row = {**row, "class": root, "negated": parity != row["negated"]}
            yield row


class _Unit:
    """One constraint of the theory: an accepted relation or group (an ordered chain has one unit per link).
    `clauses` is the 2-CNF part checked in process; `deferred` marks a remainder only mb-engine checks; bit i of
    `mask` is set when the unit holds in interpretation i (-1: in every interpretation, which is all a relation has).
    `members` (record indices) is set on AMO-shaped units, so that their literals can be checked after EQUIV merges."""

    __slots__ = ("id", "expr", "mask", "clauses", "deferred", "kind", "dropped", "down", "members", "rep", "conf", "atleast",
                 "atmost", "row", "pair", "floored", "cand")

    def __init__(
        self, cid: str, expr: dict[str, Any] | None, mask: int, clauses: list[tuple[int, int]], deferred: bool,
        kind: str, members: list[int] | None = None, rep: int = -1,
    ):
        self.id, self.expr, self.mask, self.clauses = cid, expr, mask, clauses
        self.deferred, self.kind, self.members = deferred, kind, members
        self.dropped = self.down = self.floored = False
        self.rep = rep
        self.row = -1  # the group row (groups.jsonl line number) of a group unit; -1 for a relation
        self.cand: str | None = None  # the candidate call (group_id) of a reading link: the settle's pair identity
        self.pair: tuple[int, int] | None = None  # the record pair of a relation unit (admission's tie-break)
        self.conf: float | None = None  # the decision's optional confidence (admission order); None when the child sent none
        # (k, record indices) of a deferred cardinality part (at least k true): admission checks it is still possible
        self.atleast: tuple[int, list[int]] | None = None
        # (k, record indices) of a deferred at most k part (at most k true): the per-unit check (BC2) needs its members
        self.atmost: tuple[int, list[int]] | None = None


def _amo(members: list[int]) -> list[tuple[int, int]]:
    """At most one of the outcomes (record indices): a clause (not x or not y) per pair."""
    return [(2 * x + 1, 2 * y + 1) for a, x in enumerate(members) for y in members[a + 1:]]


def _interp_combos(axes: dict[str, list[str]]) -> list[dict[str, str]]:
    """The interpretations: the cross product of the scope axes, in axis-name order; [{}] without axes."""
    names = sorted(axes)
    return [dict(zip(names, values)) for values in product(*(axes[a] for a in names))]


def _interp_names(combos: list[dict[str, str]]) -> list[str]:
    if not combos[0]:
        return ["base"]
    return ["|".join(f"{a}={v}" for a, v in sorted(combo.items())) for combo in combos]


def _scope_mask(scope: dict[str, Any] | None, combos: list[dict[str, str]]) -> int:
    """Bit i is set when interpretation i has the scope's axis value. -1 when the scope is absent or holds in all."""
    if scope is None:
        return -1
    mask = sum(1 << i for i, combo in enumerate(combos) if combo[scope["axis"]] in scope["values"])
    return -1 if mask == (1 << len(combos)) - 1 else mask


def _group_units(group: dict[str, Any], ids: list[str], combos: list[dict[str, str]]) -> list[_Unit]:
    """The units of one accepted group row (members are record indices). Compile table: exactly_one -> exactly k=1;
    at_most_one -> atmost k=1; at_least_k -> atleast k; at_most_k -> atmost k; ordered_chain -> one implies per link."""
    n, kind, k, members = group["n"], group["kind"], group["k"], group["members"]
    mask, gid, m = _scope_mask(group["scope"], combos), f"g{n:07d}", len(members)
    if kind == "ordered_chain":
        units = []
        for link in range(m - 1):
            a, b = members[link], members[link + 1]
            if group["direction"] == "reverse":
                a, b = b, a
            expr = {"op": "implies", "a": ir.var(ids[a]), "b": ir.var(ids[b])}
            units.append(_Unit(f"{gid}.{link}", expr, mask, _relation_clauses(a, b, "IMPLIES"), False, kind))
        return units
    args = [ir.var(ids[i]) for i in members]
    if kind == "exactly_one":
        alo = [(2 * members[0], 2 * members[1])] if m == 2 else []  # ALO over 3+ members is beyond 2-CNF
        unit = _Unit(gid, {"op": "exactly", "k": 1, "args": args}, mask, _amo(members) + alo, m > 2, kind, members)
        if m > 2:
            unit.atleast = (1, members)
        return [unit]
    if kind == "at_most_one" or (kind == "at_most_k" and k == 1):
        return [_Unit(gid, {"op": "atmost", "k": 1, "args": args}, mask, _amo(members), False, kind, members)]
    if kind == "at_most_k":
        unit = _Unit(gid, {"op": "atmost", "k": k, "args": args}, mask, [], k < m, kind)
        if k < m:
            unit.atmost = (k, members)
        return [unit]
    if k == m:  # every member holds: unit clauses
        clauses, deferred = [(2 * i, 2 * i) for i in members], False
    elif m == 2 and k == 1:
        clauses, deferred = [(2 * members[0], 2 * members[1])], False
    else:
        clauses, deferred = [], True
    unit = _Unit(gid, {"op": "atleast", "k": k, "args": args}, mask, clauses, deferred, kind)
    if deferred:
        unit.atleast = (k, members)
    return [unit]


def _units(replay: _Replay, work: Path, ckpt: dict[str, Any], combos: list[dict[str, str]]) -> list[_Unit]:
    """Relations first, then groups: the export order."""
    units = []
    ids = replay.ids
    for n, ((i, j, rel, rep), conf) in enumerate(zip(replay.relations(), replay.relation_confidences())):
        if rel == "EQUIV" and replay.cls_id[i] >= 0 and replay.cls_id[j] >= 0 and replay._normalised(i) != replay._normalised(j):
            raise RuntimeError(f"emitted EQUIV {ids[i]} <-> {ids[j]} contradicts the normalised class parity")
        kind = "derived" if rep >= 0 else "relation"
        unit = _Unit(f"p{n:07d}", _relation_expr(rel, ids[i], ids[j]), -1, _relation_clauses(i, j, rel), False, kind, rep=rep)
        unit.conf, unit.pair = conf, (i, j)
        units.append(unit)
    for group in _jsonl_prefix(work / "groups.jsonl", ckpt.get("groups_bytes", 0)):
        for unit in _group_units(group, ids, combos):
            unit.conf, unit.row, unit.cand = group.get("conf"), group["n"], group.get("cand")
            units.append(unit)
    return units


def _write_units(
    dest: Path, replay: _Replay, units: list[_Unit], names: list[str], usage: dict[str, Any], profile: dict[str, Any],
    include: Callable[[int, _Unit], bool] | None = None,
) -> int:
    """Writes a submission from the units (not dropped, or as `include` selects) and the profile. Returns the constraint count."""
    counter = {"n": 0}

    def constraints() -> Iterator[dict[str, Any]]:
        for n, unit in enumerate(units):
            if (not unit.dropped) if include is None else include(n, unit):
                counter["n"] += 1
                interps = None if unit.mask == -1 else [name for i, name in enumerate(names) if (unit.mask >> i) & 1]
                yield {"id": unit.id, "expr": unit.expr, "interps": interps}

    ir.write_submission(dest, replay.rows(), constraints(), names, usage)
    ir.write_json(dest / "profile.json", profile)
    return counter["n"]


def _write_full(
    dest: Path, replay: _Replay, constraints: Iterable[dict[str, Any]], names: list[str], usage: dict[str, Any], profile: dict[str, Any]
) -> int:
    """Writes a submission whose theory is a full replay (extra_theory): the constraints are taken verbatim. Its bindings
    are written sorted by outcome id: a query's default universe is the bindings in file order, and gold's bindings are
    sorted, so the replay's selection ids come out in gold's order (pairwise builds keep the record order)."""
    counter = {"n": 0}

    def counted() -> Iterator[dict[str, Any]]:
        for con in constraints:
            counter["n"] += 1
            yield con

    ir.write_submission(dest, sorted(replay.rows(), key=lambda row: row["id"]), counted(), names, usage)
    ir.write_json(dest / "profile.json", profile)
    return counter["n"]


def _scc(adj: list[list[int]]) -> list[int]:
    """Strongly connected component id of every node (iterative Tarjan)."""
    n = len(adj)
    index = [-1] * n
    low = [0] * n
    on_stack = [False] * n
    comp = [-1] * n
    stack: list[int] = []
    counter = components = 0
    for root in range(n):
        if index[root] != -1:
            continue
        work = [(root, 0)]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack[root] = True
        while work:
            v, pos = work[-1]
            if pos < len(adj[v]):
                work[-1] = (v, pos + 1)
                w = adj[v][pos]
                if index[w] == -1:
                    index[w] = low[w] = counter
                    counter += 1
                    stack.append(w)
                    on_stack[w] = True
                    work.append((w, 0))
                elif on_stack[w]:
                    low[v] = min(low[v], index[w])
            else:
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[v])
                if low[v] == index[v]:
                    while True:
                        w = stack.pop()
                        on_stack[w] = False
                        comp[w] = components
                        if w == v:
                            break
                    components += 1
    return comp


def _unsat_2cnf(n_vars: int, clauses: list[tuple[int, int]]) -> bool:
    """Literal 2v is variable v, 2v + 1 its negation; a clause (x or y) is an edge from not-x to y and from not-y to x.
    The 2-CNF is unsatisfiable exactly when some variable shares a strongly connected component with its negation."""
    adj: list[list[int]] = [[] for _ in range(2 * n_vars)]
    for x, y in clauses:
        adj[x ^ 1].append(y)
        adj[y ^ 1].append(x)
    comp = _scc(adj)
    return any(comp[2 * v] == comp[2 * v + 1] for v in range(n_vars))


def _relation_clauses(i: int, j: int, rel: str) -> list[tuple[int, int]]:
    a, b = 2 * i, 2 * j
    if rel == "IMPLIES":
        return [(a ^ 1, b)]
    if rel == "IMPLIED_BY":
        return [(b ^ 1, a)]
    if rel == "EXCLUDES":
        return [(a ^ 1, b ^ 1)]
    if rel == "EQUIV":
        return [(a ^ 1, b), (a, b ^ 1)]
    return []


class _BaseCheck:
    """The base theory as one 2-CNF per interpretation over outcome variables. Bound outcome i equals its class's
    defining outcome f (the first bound row of that class, as write_submission defines aliases) XOR the two
    negation flags; each unit adds its 2-CNF part in the interpretations it holds in. Satisfiability is exact and
    linear in the theory. Without `units`, the relation log of `replay` is the theory (one interpretation)."""

    def __init__(self, replay: Any, units: list[_Unit] | None = None, n_interps: int = 1):
        self.n = len(replay.ids)
        self.binding: list[tuple[int, int]] = []
        defining: dict[str, tuple[int, bool]] = {}
        for k, row in enumerate(replay.rows()):
            if row["status"] != "bound":
                continue
            f, neg_f = defining.setdefault(row["class"], (k, bool(row["negated"])))
            m = 2 * f + (neg_f != bool(row["negated"]))  # literal the binding of k must equal
            self.binding += [(2 * k + 1, m), (2 * k, m ^ 1)]
        if units is None:
            units = [_Unit(f"p{n:07d}", None, -1, _relation_clauses(i, j, rel), False, "relation")
                     for n, (i, j, rel, _) in enumerate(replay.relations())]
        self.units = units
        self.n_interps = n_interps

    def clauses(self, interp: int, dropped: set[int], k: int) -> list[tuple[int, int, int]]:
        """(x, y, unit index) of every active clause of interpretation `interp` over the units below k; the binding
        clauses carry -1 (they are never dropped)."""
        out = [(x, y, -1) for x, y in self.binding]
        for n in range(k):
            unit = self.units[n]
            if n not in dropped and not unit.dropped and (unit.mask >> interp) & 1:
                out.extend((x, y, n) for x, y in unit.clauses)
        return out

    def unsat(self, k: int, dropped: set[int], interp: int = 0) -> bool:
        """Is interpretation `interp` unsatisfiable with the units numbered below k, minus `dropped` and the units
        already dropped (their flag) or no longer holding in `interp`?"""
        clauses = list(self.binding)
        for n in range(k):
            unit = self.units[n]
            if n not in dropped and not unit.dropped and (unit.mask >> interp) & 1:
                clauses.extend(unit.clauses)
        return _unsat_2cnf(self.n, clauses)


def _path_labels(adj_t: list[list[int]], adj_l: list[list[int]], comp: list[int], src: int, dst: int) -> list[int]:
    """Labels of the edges of a shortest path src -> dst inside src's strongly connected component (breadth-first search).
    The search stops as soon as dst is discovered: its parent is fixed at that moment, and the path only follows parents
    of nodes discovered before it, so the path is the one a search that runs to the end would give."""
    parent: dict[int, tuple[int, int] | None] = {src: None}
    queue = deque([src])
    component = comp[src]
    while queue:
        node = queue.popleft()
        for target, label in zip(adj_t[node], adj_l[node]):
            if target not in parent and comp[target] == component:
                parent[target] = (node, label)
                if target == dst:
                    labels = []
                    while parent[target] is not None:
                        target, label = parent[target]
                        labels.append(label)
                    return labels
                queue.append(target)
    raise ValueError("dst is not reachable from src inside their component")


def _conflict_cycle(n_vars: int, clauses: list[tuple[int, int, int]]) -> tuple[list[int] | None, int]:
    """(labels of one conflict cycle, number of unsatisfied variables) of a 2-CNF whose clauses carry their unit's label
    (-1 for bindings). The cycle is x -> ... -> not x -> ... -> x inside one strongly connected component, found by BFS
    inside the component; its labels are the units on it. (None, 0) when the 2-CNF is satisfiable. One call is one
    strongly-connected-component pass over the theory."""
    adj_t: list[list[int]] = [[] for _ in range(2 * n_vars)]
    adj_l: list[list[int]] = [[] for _ in range(2 * n_vars)]
    for x, y, label in clauses:
        adj_t[x ^ 1].append(y)
        adj_l[x ^ 1].append(label)
        adj_t[y ^ 1].append(x)
        adj_l[y ^ 1].append(label)
    comp = _scc(adj_t)
    bad = [v for v in range(n_vars) if comp[2 * v] == comp[2 * v + 1]]
    if not bad:
        return None, 0
    v = bad[0]
    labels = _path_labels(adj_t, adj_l, comp, 2 * v, 2 * v + 1) + _path_labels(adj_t, adj_l, comp, 2 * v + 1, 2 * v)
    return labels, len(bad)


PRUNE_CYCLES_PER_PASS = 256  # conflict cycles extracted (and repaired) in one pass of the base repair


def _conflict_cycles(n_vars: int, clauses: list[tuple[int, int, int]],
                     limit: int = PRUNE_CYCLES_PER_PASS) -> tuple[list[list[int]], int]:
    """(up to `limit` conflict cycles, number of unsatisfied variables) of a 2-CNF: one cycle for each of the first
    unsatisfied variables in index order (see _conflict_cycle). Each cycle is the list of unit labels on it. One pass over
    the theory, plus one short search per cycle."""
    adj_t: list[list[int]] = [[] for _ in range(2 * n_vars)]
    adj_l: list[list[int]] = [[] for _ in range(2 * n_vars)]
    for x, y, label in clauses:
        adj_t[x ^ 1].append(y)
        adj_l[x ^ 1].append(label)
        adj_t[y ^ 1].append(x)
        adj_l[y ^ 1].append(label)
    comp = _scc(adj_t)
    cycles: list[list[int]] = []
    bad = 0
    for v in range(n_vars):
        if comp[2 * v] != comp[2 * v + 1]:
            continue
        bad += 1
        if len(cycles) < limit:
            cycles.append(_path_labels(adj_t, adj_l, comp, 2 * v, 2 * v + 1) + _path_labels(adj_t, adj_l, comp, 2 * v + 1, 2 * v))
    return cycles, bad


class _DropLedger:
    """Counts the repairs of the base: relation drops (representatives and plain relations) and group drops (drops and
    cleared interpretation bits), and enforces the caps. Each cap is a count and a rate (the share of the relations
    asked, or of the groups committed); exceeding either raises BaseInconsistentError with the count and the rate."""

    def __init__(self, relation_cap: int, group_cap: int, relation_frac: float | None, asked: int,
                 group_frac: float | None = None, groups_asked: int = 0):
        self.relation_cap, self.group_cap = relation_cap, group_cap
        self.relation_frac, self.asked = relation_frac, asked
        self.group_frac, self.groups_asked = group_frac, groups_asked
        self.relation = self.group = 0
        self._group_rows: set[int] = set()  # a group is one drop however many of its units are dropped

    def drop_relation(self) -> None:
        self.relation += 1
        rate = self.relation / self.asked if self.asked else 0.0
        if self.relation > self.relation_cap:
            raise BaseInconsistentError(
                f"the base needed {self.relation} relation drops (contradiction rate {rate:.1%} of {self.asked} asked "
                f"relations), over --max-relation-drops {self.relation_cap}; the theory is not usable as built"
            )
        if self.relation_frac is not None and self.asked and rate > self.relation_frac:
            raise BaseInconsistentError(
                f"the base needed {self.relation} relation drops of {self.asked} asked relations (contradiction rate "
                f"{rate:.1%}), over the limit of {self.relation_frac:.0%} (--max-relation-drop-frac); the model's relations "
                "contradict the bindings too often for the theory to be used as built"
            )

    def drop_group(self, row: int | None = None) -> None:
        """One group drop per group row: a row already counted (another of its units was dropped) adds nothing."""
        if row is not None:
            if row in self._group_rows:
                return
            self._group_rows.add(row)
        self.group += 1
        rate = self.group / self.groups_asked if self.groups_asked else 0.0
        if self.group > self.group_cap:
            raise BaseInconsistentError(
                f"the base needed {self.group} group drops (rate {rate:.1%} of {self.groups_asked} committed groups), over "
                f"--max-group-drops {self.group_cap}; the theory is not usable as built"
            )
        if self.group_frac is not None and self.groups_asked and rate > self.group_frac:
            raise BaseInconsistentError(
                f"the base needed {self.group} group drops of {self.groups_asked} committed groups (rate {rate:.1%}), over "
                f"the limit of {self.group_frac:.0%} (--max-group-drop-frac); the model's groups contradict the bindings too "
                "often for the theory to be used as built"
            )


class _PruneProgress:
    """Progress lines on stderr, at most one per PRUNE_PROGRESS_EVERY seconds, so a slow repair does not look like a hang."""

    def __init__(self) -> None:
        self.t0 = self.last = time.monotonic()

    def maybe(self, describe: Callable[[], str]) -> None:
        now = time.monotonic()
        if now - self.last >= PRUNE_PROGRESS_EVERY:
            self.last = now
            print(f"finalize: repairing the base after {now - self.t0:.0f} s: {describe()}", file=sys.stderr, flush=True)


def _children(check: _BaseCheck) -> dict[int, list[int]]:
    """Derived units of each representative (unit index -> indices)."""
    children: dict[int, list[int]] = {}
    for n, unit in enumerate(check.units):
        if unit.rep >= 0:
            children.setdefault(unit.rep, []).append(n)
    return children


def _drop_culprit(check: _BaseCheck, dropped: set[int], children: dict[int, list[int]], culprit: int, bit: int,
                  ledger: _DropLedger) -> None:
    """Repairs one culprit. A derived relation is a consequence of its representative, so the representative goes with
    its derived rows. A scoped group only loses this interpretation (its bit is cleared); any other unit is dropped."""
    target = check.units[culprit].rep if check.units[culprit].rep >= 0 else culprit
    unit = check.units[target]
    if unit.mask != -1 and unit.mask & ~bit:
        unit.mask &= ~bit
        ledger.drop_group(unit.row if unit.row >= 0 else None)
        return
    dropped.add(target)
    dropped.update(children.get(target, ()))
    if unit.kind == "relation":
        ledger.drop_relation()
    else:
        ledger.drop_group(unit.row if unit.row >= 0 else None)


def _prune_scc(check: _BaseCheck, ledger: _DropLedger, time_left: Callable[[], float], progress: _PruneProgress,
               trace: list | None) -> set[int]:
    """Repair by conflict-cycle extraction. Each pass finds conflict cycles x -> ... -> not x -> ... -> x (shortest paths in
    their component) for up to PRUNE_CYCLES_PER_PASS unsatisfied variables (_conflict_cycles) and repairs the culprit of each: the relation on the cycle
    with the highest index (a deterministic tie-break between contradicting relations; a group only when the cycle has no
    relation). The culprits of one pass are repaired together, then the pass is repeated. The number of passes follows the
    depth of the conflicts, not the number of repairs: one full pass per repair made a real base (thousands of conflicts)
    take hours. A culprit is repaired only while every unit of its cycle still holds (an earlier repair of the pass may have
    broken it). A repaired unit lies on a conflict cycle of the state it was chosen from, so the result is consistent.
    A derived culprit drops its representative together with all of the representative's derived rows, and those rows may
    lie off the cycle. MB_PRUNE=bisect is the other method, and both are checked for consistency."""
    dropped, children = set(), _children(check)
    n_units = len(check.units)
    for interp in range(check.n_interps):
        bit = 1 << interp
        if check.unsat(0, dropped, interp):
            raise BaseInconsistentError("the bindings alone are inconsistent; no constraint can be dropped")
        while True:
            cycles, conflicts = _conflict_cycles(check.n, check.clauses(interp, dropped, n_units))
            if not cycles:
                break
            _heartbeat()
            progress.maybe(lambda: f"{ledger.relation} relation and {ledger.group} group drops so far, "
                                   f"{conflicts} unsatisfied variables in this pass ({len(cycles)} cycles repaired now)")
            if time_left() <= 0:
                raise BaseCheckIncomplete("the build deadline passed before the base theory was verified consistent")
            culprits: list[tuple[int, tuple[int, ...]]] = []
            for labels in cycles:
                units_on_cycle = [label for label in labels if label >= 0]
                if not units_on_cycle:
                    raise BaseInconsistentError("the bindings alone are inconsistent; no constraint can be dropped")
                relations_on_cycle = [n for n in units_on_cycle if check.units[n].kind in ("relation", "derived")]
                culprit = max(relations_on_cycle) if relations_on_cycle else max(units_on_cycle)
                culprits.append((culprit, tuple(sorted(set(units_on_cycle)))))
            for culprit, cycle in sorted(set(culprits)):
                if not _cycle_live(check, cycle, dropped, interp):  # an earlier repair of this pass already broke this cycle
                    continue
                if trace is not None:
                    trace.append((interp, cycle, culprit))
                _drop_culprit(check, dropped, children, culprit, bit, ledger)
    return dropped


def _cycle_live(check: _BaseCheck, labels: tuple[int, ...], dropped: set[int], interp: int) -> bool:
    """True when every unit on the cycle still holds in interpretation `interp`: not dropped, and its bit is still set. The
    cycles of one pass are computed on the state at its start, so a culprit can be gone by the time its turn comes."""
    for n in labels:
        if n < 0:
            continue
        unit = check.units[n]
        if n in dropped or unit.dropped or not (unit.mask >> interp) & 1:
            return False
    return True


def _prune_bisect(check: _BaseCheck, ledger: _DropLedger, time_left: Callable[[], float], progress: _PruneProgress,
                  trace: list | None) -> set[int]:
    """Repair by prefix bisection (MB_PRUNE=bisect, kept for equivalence tests): per interpretation, each round finds the
    smallest inconsistent prefix of the unit log by bisection (about log2(units) 2-CNF checks) and repairs its last unit."""
    dropped, children = set(), _children(check)
    n_units = len(check.units)
    for interp in range(check.n_interps):
        bit = 1 << interp
        if check.unsat(0, dropped, interp):
            raise BaseInconsistentError("the bindings alone are inconsistent; no constraint can be dropped")
        while check.unsat(n_units, dropped, interp):
            progress.maybe(lambda: f"{ledger.relation} relation and {ledger.group} group drops so far (bisection)")
            if time_left() <= 0:
                raise BaseCheckIncomplete("the build deadline passed before the base theory was verified consistent")
            lo, hi = 0, n_units  # unsat(hi) holds, consistent(lo) holds
            while hi - lo > 1:
                mid = (lo + hi) // 2
                if check.unsat(mid, dropped, interp):
                    hi = mid
                else:
                    lo = mid
            if trace is not None:
                trace.append((interp, (hi - 1,), hi - 1))
            _drop_culprit(check, dropped, children, hi - 1, bit, ledger)
    return dropped


def _prune_in_process(
    check: _BaseCheck, relation_cap: int, group_cap: int, time_left: Callable[[], float], *,
    relation_frac: float | None = None, asked: int = 0, method: str | None = None, trace: list | None = None,
    group_frac: float | None = None, groups_asked: int = 0, ledger: _DropLedger | None = None,
) -> set[int]:
    """Repairs the base until every interpretation's 2-CNF is satisfiable; returns the dropped unit indices. `method`
    (default: the MB_PRUNE environment variable, else "scc") selects conflict-cycle extraction or the prefix bisection.
    Both repair a unit on a conflict and keep the base consistent; they differ only in which culprit is repaired. The
    caps are relation_cap (drops), relation_frac (drops as a share of the `asked` relations), group_cap and group_frac
    (group drops as a share of the `groups_asked` committed groups). `ledger` is the drop ledger to count against (the
    caller's, shared with the engine stage); by default a new ledger with these caps."""
    method = method or os.environ.get("MB_PRUNE", "scc")
    if method not in PRUNE_METHODS:
        raise ValueError(f"MB_PRUNE must be one of {PRUNE_METHODS}, got {method!r}")
    if ledger is None:
        ledger = _DropLedger(relation_cap, group_cap, relation_frac, asked, group_frac, groups_asked)
    progress = _PruneProgress()
    run = _prune_scc if method == "scc" else _prune_bisect
    return run(check, ledger, time_left, progress, trace)


def _engine_verdict(out_dir: Path, work: Path, budget_s: float) -> tuple[str, str]:
    """mb-engine's verdict on a written submission: one eval with an empty query file, bounded by the time left in the
    build deadline. (status, detail): "engine" (accepted), "rejected" (the engine finds the base inconsistent; detail is
    its message), "skipped" (no engine), "skipped_deadline". Other engine errors propagate (EngineInfraError keeps its type)."""
    if not engine.engine_available():
        warnings.warn("mb-engine not found: engine confirmation of the base skipped (set MB_ENGINE)", stacklevel=3)
        return "skipped", ""
    if budget_s <= 0:
        return "skipped_deadline", ""
    queries, answers = work / "confirm_queries.jsonl", work / "confirm_answers.jsonl"
    queries.write_text("", encoding="utf-8")
    try:
        engine.eval_submission(out_dir, queries, answers, timeout_s=min(budget_s, threading.TIMEOUT_MAX))
    except engine.EngineError as exc:
        if isinstance(exc, engine.EngineInfraError):
            raise
        if "inconsistent_base" in str(exc):
            return "rejected", str(exc)
        if exc.returncode is None:  # the engine overran the time left in the build deadline
            return "skipped_deadline", ""
        raise
    finally:
        queries.unlink(missing_ok=True)
        answers.unlink(missing_ok=True)
    return "engine", ""


class _EngineRejected(BaseInconsistentError):
    """mb-engine rejects the base and no deferred unit localizes the rejection. The admitted base may be repaired instead."""


def _engine_stats() -> dict[str, Any]:
    """The counters of an engine settle: rounds, probes, localized drops, the deferred parts a sealed localization left
    unresolved, and the seconds spent writing and in mb-engine. They cover the settle's own probes only; a refused admitted
    settle resets them before the repair fallback's settle."""
    return {"rounds": 0, "probes": 0, "localized_drops": 0, "deferred_unresolved": 0, "engine_seconds": 0.0,
            "write_seconds": 0.0}


def _probe(
    dest: Path, work: Path, replay: _Replay, units: list[_Unit], names: list[str], usage: dict[str, Any],
    profile: dict[str, Any], time_left: Callable[[], float], stats: dict[str, Any],
    include: Callable[[int, _Unit], bool] | None = None,
) -> tuple[str, str]:
    """One engine probe: writes the units `include` selects (default: the ones not dropped) to `dest` and returns
    mb-engine's verdict on them (_engine_verdict). `stats` counts the probe and its write and engine seconds."""
    _heartbeat()
    t0 = time.monotonic()
    _write_units(dest, replay, units, names, usage, profile, include)
    t1 = time.monotonic()
    status, detail = _engine_verdict(dest, work, time_left())
    stats["probes"] += 1
    stats["write_seconds"] = round(stats["write_seconds"] + t1 - t0, 3)
    stats["engine_seconds"] = round(stats["engine_seconds"] + time.monotonic() - t1, 3)
    return status, detail


def _engine_rejects(
    units: list[_Unit], replay: _Replay, work: Path, names: list[str], profile: dict[str, Any], kept: set[int],
    time_left: Callable[[], float], stats: dict[str, Any],
) -> bool:
    """Does mb-engine reject the submission when only the deferred units numbered in `kept` are present? A probe that
    cannot be decided before the deadline is BaseCheckIncomplete: it is never taken as an acceptance."""
    probe = work / "probe"
    shutil.rmtree(probe, ignore_errors=True)
    try:
        usage = {"calls": [], "wall_seconds": 0.0, "peak_rss_mb": 0.0}
        status, _ = _probe(probe, work, replay, units, names, usage, profile, time_left, stats,
                           include=lambda n, u: not u.dropped and (not u.deferred or n in kept))
        if status in ("skipped_deadline", "skipped"):
            raise BaseCheckIncomplete("the build deadline passed before mb-engine could localize the base")
        return status == "rejected"
    finally:
        shutil.rmtree(probe, ignore_errors=True)


def _settle_with_engine(
    units: list[_Unit], replay: _Replay, work: Path, out_dir: Path, names: list[str], profile: dict[str, Any],
    usage_of: Callable[[], dict[str, Any]], time_left: Callable[[], float], ledger: _DropLedger | None = None,
    stats: dict[str, int] | None = None,
) -> str:
    """Writes the submission and has mb-engine confirm its base. On a rejection the engine is first asked about the
    base without any deferred unit (a rejection there is final: _EngineRejected). Otherwise galloping, then bisection over
    the deferred units, probing the engine on partial submissions, finds the first deferred unit that makes the base
    inconsistent. An exactly_one is downgraded to at_most_one, any other unit is dropped and counted against the group cap
    of `ledger`. At most MAX_GROUP_DROPS rounds. When the rounds run out or the deadline stops the localization, the base
    is sealed by _seal_unresolved (or the stop is raised as before). `stats` receives the rounds, the probes (engine runs),
    the localized drops and the seconds spent writing and in the engine (this settle's probes only)."""
    stats = _engine_stats() if stats is None else stats
    # lo: the engine accepts the base with deferred[:lo] present; base_ok: it accepts the base without any deferred unit.
    # The culprit of a round is deferred[lo], and a drop removes only that unit, so deferred[:lo] and the base are
    # unchanged and both still hold. A downgrade adds an at_most_one to the base, so both restart.
    lo, base_ok, probe_s = 0, False, 0.0

    def localize_time() -> float:  # the time left less one base probe: the seal's own probe (_seal_unresolved) still fits
        return time_left() - probe_s

    while True:
        t0 = time.monotonic()
        status, detail = _probe(out_dir, work, replay, units, names, usage_of(), profile, time_left, stats)
        probe_s = time.monotonic() - t0
        if status != "rejected":
            return status
        deferred = [n for n, unit in enumerate(units) if unit.deferred and not unit.dropped]
        stats["rounds"] += 1
        if lo >= len(deferred):  # only a nondeterministic engine gets here: start over from the base
            lo, base_ok = 0, False
        message = f"mb-engine finds the base inconsistent where the in-process check does not: {detail}"
        if not deferred:
            raise _EngineRejected(message)
        if stats["rounds"] > MAX_GROUP_DROPS:
            return _seal_unresolved(units, replay, work, out_dir, names, profile, usage_of, time_left, ledger, stats,
                                    _EngineRejected(message))
        try:
            if not base_ok and _engine_rejects(units, replay, work, names, profile, set(), localize_time, stats):
                raise _EngineRejected(message)
            base_ok = True  # the base without deferred units is accepted (drops keep it so)
            hi, step = len(deferred), 1  # the engine rejects with deferred[:hi] present: the full submission, probed above
            while hi - lo > 1 and lo + step < hi:  # gallop from the bound: the next culprit is often just past it
                probe_at = lo + step
                if _engine_rejects(units, replay, work, names, profile, set(deferred[:probe_at]), localize_time, stats):
                    hi = probe_at
                else:
                    lo, step = probe_at, 2 * step
            while hi - lo > 1:
                mid = (lo + hi) // 2
                if _engine_rejects(units, replay, work, names, profile, set(deferred[:mid]), localize_time, stats):
                    hi = mid
                else:
                    lo = mid
        except BaseCheckIncomplete as stop:  # the deadline stopped the localization
            return _seal_unresolved(units, replay, work, out_dir, names, profile, usage_of, time_left, ledger, stats, stop)
        unit = units[deferred[lo]]  # hi = lo + 1: the first deferred unit that makes the base inconsistent
        unit.deferred = False
        if unit.kind == "exactly_one":
            unit.expr = {"op": "atmost", "k": 1, "args": unit.expr["args"]}
            unit.down = True
            lo, base_ok = 0, False
        else:
            unit.dropped = True
            stats["localized_drops"] += 1
            if ledger is not None:  # a localized drop is a group drop: the group cap applies (BaseInconsistentError at the cap)
                ledger.drop_group(unit.row if unit.row >= 0 else None)


def _seal_unresolved(
    units: list[_Unit], replay: _Replay, work: Path, out_dir: Path, names: list[str], profile: dict[str, Any],
    usage_of: Callable[[], dict[str, Any]], time_left: Callable[[], float], ledger: _DropLedger | None,
    stats: dict[str, Any], why: Exception,
) -> str:
    """The seal of a stopped localization: every deferred part still live is dropped (an exactly_one is downgraded, as the
    localization does), and the base is probed once. An accepted base is sealed, with the parts left unresolved counted in
    stats["deferred_unresolved"] (a PARTIAL reason). Otherwise `why` (the round cap's _EngineRejected, or the deadline's
    BaseCheckIncomplete) is raised, as before. A probe the deadline skips raises BaseCheckIncomplete, whatever `why` is, so
    the post-hoc repair (which the round cap's _EngineRejected starts) does not run past the deadline. The drops are counted
    in the group ledger only once the probe accepts the base: a drop that overflows the group cap refuses the seal (`why`,
    so the post-hoc repair still runs and the cap decides there)."""
    remaining = [n for n, unit in enumerate(units) if unit.deferred and not unit.dropped]
    dropped_rows = []
    for n in remaining:
        unit = units[n]
        unit.deferred = False
        if unit.kind == "exactly_one":
            unit.expr = {"op": "atmost", "k": 1, "args": unit.expr["args"]}
            unit.down = True
        else:
            unit.dropped = True
            dropped_rows.append(unit.row if unit.row >= 0 else None)
    status, _ = _probe(out_dir, work, replay, units, names, usage_of(), profile, time_left, stats)
    if status == "skipped_deadline":
        raise BaseCheckIncomplete("the build deadline passed before mb-engine could confirm the sealed base")
    if status != "engine":
        raise why
    try:
        if ledger is not None:
            for row in dropped_rows:
                ledger.drop_group(row)
    except BaseInconsistentError:  # over the group cap: the seal is refused, and the caller's failure stands
        raise why
    stats["deferred_unresolved"] = len(remaining)
    return status


def _drop_infeasible_deferred(check: _BaseCheck, units: list[_Unit], ledger: _DropLedger,
                              closure: Any = None) -> tuple[list[int], list[int]]:
    """BC2: drops, before the engine is asked, the live deferred parts that are impossible against N0, the bindings and the
    non-deferred live 2-CNF units (the base every probe of the localization contains). An at_most_k is impossible when more
    than k of its members are true in every model of N0 (admit.at_most_possible); an at_least_k or exactly_one when fewer
    than k members are not forced false by N0 (admit.at_least_possible). An at_most_k or at_least_k is dropped (a group drop,
    counted by the ledger). An exactly_one keeps its at_most_one and loses its at_least_one (a downgrade, as the localization
    does). Why this equals the localization's result, whenever the localization finishes: a part impossible against N0 is
    refused by every probe that contains it, since each probe's base contains N0, so the localization's first culprit is always
    that part or one before it, and the part is removed at the same point of the sequence. An exactly_one's at most one is
    implied by N0 (all its members are forced false when its at least one is impossible), so downgrading it early adds nothing
    any probe does not already have. The deferred parts' own clauses are left out of N0 on purpose: a deferred exactly_one's
    at most one is in a probe only while its unit is in the prefix, so counting it in N0 would test parts the localization has
    not yet got to. `closure` (the admitted base's closure) is used only when no live deferred unit has clauses, since the
    admitted closure holds the deferred at most ones too; otherwise N0's closure is built here. Returns (dropped, downgraded):
    the indices of the units this check dropped and of those it downgraded (the localization's own downgrades are not in
    either). The caller releases its reference to `closure` when N0 is rebuilt, so two matrices are never alive together.
    The caller checked that the base is satisfiable and that the theory is unscoped (one interpretation)."""
    live = [unit for unit in units if unit.deferred and not unit.dropped]
    if not live:  # nothing to check: no closure is built
        return [], []
    from .admit import Closure, at_least_possible, at_most_possible  # numpy: loaded when the check runs

    if closure is None or any(unit.clauses for unit in live):
        closure = Closure(check.n)
        for x, y in check.binding:
            closure.add_clause(x, y)
        for unit in units:
            if not unit.dropped and not unit.deferred and unit.rep < 0:  # N0: derived units are implied by their representatives
                for x, y in unit.clauses:
                    closure.add_clause(x, y)
    dropped, downgraded = [], []
    for n, unit in enumerate(units):
        if not unit.deferred or unit.dropped:
            continue
        if unit.atmost is not None:
            k, members = unit.atmost
            possible = at_most_possible(closure, [2 * i for i in members], k)
        elif unit.atleast is not None:
            k, members = unit.atleast
            possible = at_least_possible(closure, [2 * i for i in members], k)
        else:
            continue
        if possible:
            continue
        unit.deferred = False
        if unit.kind == "exactly_one":
            unit.expr = {"op": "atmost", "k": 1, "args": unit.expr["args"]}
            unit.down = True
            downgraded.append(n)
        else:
            unit.dropped = True
            dropped.append(n)
            ledger.drop_group(unit.row if unit.row >= 0 else None)
    return dropped, downgraded


SIBLING_KINDS = ("exactly_one", "at_most_one")  # admission tier A: sibling-grade groups, admitted before the relations


def _is_sibling(unit: _Unit) -> bool:
    """Tier A: exactly_one, at_most_one, and at_most_k with k 1 (the same pairwise at-most-one part)."""
    return unit.kind in SIBLING_KINDS or (unit.kind == "at_most_k" and unit.expr is not None and unit.expr.get("k") == 1)


def _admission_order(units: list[_Unit], pair_key: Callable[[int], tuple] | None = None) -> list[int]:
    """The units admission considers, in order. Tier A: the sibling-grade groups (_is_sibling), in log order. Tier B: the
    relation decisions. A decision with a conf comes first, highest conf first; a decision without conf follows in the
    order `pair_key(n)` gives (the study's ptype/jaccard tie-break, see _pair_tiebreak), or in log order when there is none.
    Tier C: the other groups by conf (no conf last, in log order). Derived units are not listed: they follow their
    representative. The order is a function of the log and the records alone, so the admitted set is deterministic."""
    def relation_rank(n: int) -> tuple:
        conf = units[n].conf
        if conf is not None:
            return (0, -conf, (0, 0.0), n)
        return (1, (0, 0.0), pair_key(n) if pair_key is not None else (0, 0.0), n)

    def group_rank(n: int) -> tuple:
        conf = units[n].conf
        return (0, -conf, (0, 0.0), n) if conf is not None else (1, 0.0, (0, 0.0), n)

    live = [n for n, unit in enumerate(units) if not unit.dropped]
    tier_a = [n for n in live if _is_sibling(units[n])]
    tier_b = sorted((n for n in live if units[n].kind == "relation"), key=relation_rank)
    tier_c = sorted((n for n in live if not _is_sibling(units[n]) and units[n].kind not in ("relation", "derived")),
                    key=group_rank)
    return tier_a + tier_b + tier_c


def _mark_floored(units: list[_Unit], floor: float | None, check: _BaseCheck) -> set[int]:
    """The relations whose conf is below the relation floor, and their derived units, marked floored and dropped: the floor
    holds on every path, and it is not a contradiction (the summary counts it as floor_dropped_relations)."""
    if floor is None:
        return set()
    children = _children(check)
    out: set[int] = set()
    for n, unit in enumerate(units):
        if unit.kind == "relation" and unit.conf is not None and unit.conf < floor and not unit.dropped:
            out.add(n)
            out.update(children.get(n, ()))
    for n in out:
        units[n].dropped = units[n].floored = True
    return out


def _at_least_literals(unit: _Unit) -> tuple[int, list[int]] | None:
    """(k, the literals 'member true') of a deferred cardinality part, for admission; None when the unit has none."""
    return None if unit.atleast is None else (unit.atleast[0], [2 * i for i in unit.atleast[1]])


def _pair_tiebreak(ids: list[str], records: Iterable[dict[str, Any]]) -> Callable[[int, int], tuple]:
    """The study's order for relations without conf (ptype, then question overlap): (0) the two outcomes are in one market,
    (1) they share an event title, (2) otherwise; then the token-set Jaccard of their questions, highest first. Returns
    key(i, j) -> (ptype, -jaccard)."""
    tokens: list[set[str]] = []
    titles: list[Any] = []
    for rec in records:
        tokens.append(token_set(rec["question"]))
        titles.append(rec.get("event_title"))
    markets = [oid.rsplit(":", 1)[0] for oid in ids]

    def key(i: int, j: int) -> tuple:
        if markets[i] == markets[j]:
            ptype = 0
        elif titles[i] is not None and titles[i] == titles[j]:
            ptype = 1
        else:
            ptype = 2
        union = len(tokens[i] | tokens[j])
        return (ptype, -(len(tokens[i] & tokens[j]) / union if union else 0.0))

    return key


def _admit_units(check: _BaseCheck, units: list[_Unit], ledger: _DropLedger, floor: float | None,
                 time_left: Callable[[], float] | None = None,
                 pair_key: Callable[[int, int], tuple] | None = None) -> dict[str, Any] | None:
    """The base by admission (harness/admit.py). Units are admitted in _admission_order: a unit that would make the bindings
    plus the admitted units unsatisfiable (or, for a deferred cardinality part, leave fewer than k of its members possibly
    true) is refused, and so is a relation whose conf is below `floor`. A floored or refused representative takes its
    derived units. Refusals are drops counted by the ledger, which raises at the caps; floored relations are not (they are
    counted apart, and they are not contradictions). Returns {"dropped": unit indices (refused and floored), "floored":
    the floored ones, "rejected_relations": n, "rejected_groups": n (group rows), "closure": the admitted base's closure (the
    per-unit check reads it)}, or None when the admitted set fails the 2-CNF check (a bug: the caller repairs instead). Raises ImportError when numpy is missing, MemoryError when the bit
    matrix does not fit (both: the caller repairs), and BaseCheckIncomplete when the deadline passes."""
    from .admit import admit_closure  # numpy: loaded only when admission runs

    if check.unsat(0, set(), 0):
        raise BaseInconsistentError("the bindings alone are inconsistent; no constraint can be dropped")
    order = _admission_order(units, None if pair_key is None else
                             (lambda n: pair_key(*units[n].pair) if units[n].pair is not None else (0, 0.0)))
    floored_reps = {n for n in order if floor is not None and units[n].kind == "relation" and units[n].conf is not None
                    and units[n].conf < floor}
    considered = [n for n in order if n not in floored_reps]

    def tick(flags: list[bool]) -> None:  # a refusal and a passed deadline stop the admission (as the repair does)
        if time_left is not None and time_left() <= 0 and not all(flags):
            raise BaseCheckIncomplete("the build deadline passed during the base admission")

    flags, closure = admit_closure(check.n, check.binding, [units[n].clauses for n in considered],
                                   [_at_least_literals(units[n]) for n in considered], tick)
    rejected = {n for n, admitted in zip(considered, flags) if not admitted}
    # as the repair does: a base that needed repairs after the deadline is not verified in time (a consistent one is)
    if rejected and time_left is not None and time_left() <= 0:
        raise BaseCheckIncomplete("the build deadline passed before the base theory was verified consistent")
    children = _children(check)
    dropped: set[int] = set()
    floored: set[int] = set()
    rel_count, groups_rows = 0, set()
    for n in order:  # the ledger counts in admission order, so a cap raises at the same rejection every time
        if n in floored_reps or n in rejected:
            dropped.add(n)
            dropped.update(children.get(n, ()))
            if n in floored_reps:
                floored.add(n)
                floored.update(children.get(n, ()))
            else:
                if units[n].kind == "relation":
                    rel_count += 1
                    ledger.drop_relation()
                else:
                    groups_rows.add(units[n].row)
                    ledger.drop_group(units[n].row if units[n].row >= 0 else None)
    if check.unsat(len(units), dropped, 0):
        return None
    floor_count = sum(1 for n in floored if units[n].kind == "relation")
    return {"dropped": dropped, "floored": floored, "rejected_relations": rel_count,
            "rejected_groups": len(groups_rows), "floored_relations": floor_count, "closure": closure}


def _admit_build(check: _BaseCheck, units: list[_Unit], ledger: _DropLedger, floor: float | None,
                 time_left: Callable[[], float], work: Path, ids: list[str]) -> dict[str, Any] | None:
    """_admit_units with the study's tie-break for relations without conf (_pair_tiebreak); the records come from the work dir."""
    store = _RecordStore.reopen(work / "records.jsonl")
    try:
        pair_key = _pair_tiebreak(ids, islice(store, len(ids)))  # records.jsonl may hold rows past the checkpoint (not truncated)
    finally:
        store.close()
    return _admit_units(check, units, ledger, floor, time_left, pair_key)


def _walk_items(units: list[_Unit], scoped: list[int]) -> list[list[int]]:
    """The items of the settle walk, in log order. The two links of a reading pair are one item, so the pair is kept or lost
    as a whole: a pair is exactly two links of one candidate call (unit.cand) over the same two outcomes with the same bits.
    Any other unit is an item of its own. The keys are computed once, before the walk changes any mask."""
    keys = {n: (units[n].cand, frozenset((units[n].expr["a"]["id"], units[n].expr["b"]["id"])), units[n].mask)
            if units[n].kind == "ordered_chain" and units[n].cand is not None else None for n in scoped}
    count = Counter(key for key in keys.values() if key is not None)
    pairs: dict[tuple, list[int]] = {}
    for n in scoped:
        if keys[n] is not None and count[keys[n]] == 2:
            pairs.setdefault(keys[n], []).append(n)
    items, taken = [], set()
    for n in scoped:
        key = keys[n]
        if key is not None and count[key] == 2:
            if key not in taken:
                taken.add(key)
                items.append(pairs[key])
        else:
            items.append([n])
    return items


def _admit_item(closure: Any, clauses: list[tuple[int, int]], at_least: list[tuple[int, list[int]]]) -> bool:
    """Admits one walk item all or nothing (Closure.try_unit), and then keeps it only if every at-least part still possible
    stays possible (admission's `need`, for a list of parts). The closure is restored on a refusal."""
    from .admit import at_least_possible  # numpy: loaded when the check runs

    closure._begin(undo=True)  # the private undo of Closure.try_unit, extended over the at-least check
    admitted = True
    for x, y in clauses:
        if closure.forced_false(x) and closure.forced_false(y):
            admitted = False
            break
        closure.add_clause(x, y)
    if admitted:
        admitted = all(at_least_possible(closure, lits, k) for k, lits in at_least)
    closure._end(keep=admitted)
    return admitted


def _settle_scoped(check: _BaseCheck, units: list[_Unit], ledger: _DropLedger, time_left: Callable[[], float],
                   admitted_dropped: set[int]) -> tuple[set[int], int]:
    """Settles the scoped units of a candidates build against the admitted base (the unscoped units admission kept, not
    `admitted_dropped`). Per interpretation: when the base with that interpretation's scoped units is unsatisfiable, or an
    unscoped deferred at-least part becomes impossible, the scoped units are walked in log order (_walk_items), and an item
    that cannot join the base and the items kept before it loses that interpretation's bit (_drop_culprit: a unit with no
    bit left is dropped). Returns (the units this settle dropped, the bits it cleared). The base is satisfiable (admission
    checked it). Each closure is released before the next is built; the deadline stops the walk (BaseCheckIncomplete)."""
    from .admit import Closure, at_least_possible  # numpy: loaded when the check runs

    gone, children, cleared = set(admitted_dropped), _children(check), 0

    def closure_of(parts: list[int]) -> Any:  # the bindings and the clauses of `parts`, added unchecked (satisfiable)
        closure = Closure(check.n)
        for x, y in check.binding:
            closure.add_clause(x, y)
        for n in parts:
            for x, y in units[n].clauses:
                closure.add_clause(x, y)
        return closure

    base = [n for n, unit in enumerate(units) if unit.mask == -1 and not unit.dropped and n not in gone]
    probe = closure_of(base)
    # the unscoped deferred at-least parts the base alone leaves possible: the only ones a scoped unit can make impossible
    atleast = [parts for parts in (_at_least_literals(units[n]) for n in base if units[n].deferred and units[n].atleast is not None)
               if at_least_possible(probe, parts[1], parts[0])]
    probe = None
    for interp in range(check.n_interps):
        bit = 1 << interp
        scoped = [n for n, unit in enumerate(units)
                  if unit.mask != -1 and not unit.dropped and n not in gone and (unit.mask >> interp) & 1]
        if not scoped:
            continue
        if not check.unsat(len(units), gone, interp):  # satisfiable: only an at-least part can be the conflict
            if not atleast:
                continue
            closure = closure_of(base + scoped)
            held = all(at_least_possible(closure, lits, k) for k, lits in atleast)
            closure = None
            if held:
                continue
        if time_left() <= 0:
            raise BaseCheckIncomplete("the build deadline passed during the scoped settle")
        closure = closure_of(base)
        for item in _walk_items(units, scoped):
            if time_left() <= 0:
                raise BaseCheckIncomplete("the build deadline passed during the scoped settle")
            clauses = [clause for n in item for clause in units[n].clauses]
            if _admit_item(closure, clauses, atleast):
                continue
            for n in item:
                _drop_culprit(check, gone, children, n, bit, ledger)
                if n not in gone:  # the unit keeps its other bits: this interpretation's bit is cleared
                    cleared += 1
        closure = None
    settled = gone - set(admitted_dropped)
    return settled, cleared


def _admission_knobs(policy: dict[str, Any] | None) -> dict[str, Any]:
    """The admission knobs a build's policy records. A policy without the "admit" key is a pre-admission build: its base was
    repaired after the build, so it is read as admit False (the other knobs have their unset values)."""
    policy = policy or {}
    return {"admit": policy.get("admit", False), "admit_max_literals": policy.get("admit_max_literals", ADMIT_MAX_LITERALS),
            "relation_floor": policy.get("relation_floor"), "repair_budget_s": policy.get("repair_budget_s")}


def _check_admit_knobs(admit: Any, admit_max_literals: Any, relation_floor: Any, repair_budget_s: Any) -> None:
    """Refuses a bad admission or relation-floor or repair-budget knob (build checks it before any model call). The floor
    and the budget apply to the post-hoc repair too, so --no-admit takes them."""
    if not isinstance(admit, bool):
        raise ValueError(f"admit must be a boolean, got {admit!r}")
    _positive_int("admit_max_literals", admit_max_literals)
    if relation_floor is not None and (isinstance(relation_floor, bool) or not isinstance(relation_floor, (int, float))
                                       or not math.isfinite(relation_floor)):
        raise ValueError(f"relation_floor must be a finite number or None, got {relation_floor!r}")
    if repair_budget_s is not None and (isinstance(repair_budget_s, bool) or not isinstance(repair_budget_s, (int, float))
                                        or not (math.isfinite(repair_budget_s) and repair_budget_s > 0)):
        raise ValueError(f"repair_budget_s must be a positive number of seconds or None, got {repair_budget_s!r}")


def _tree(out: Path) -> set[str]:
    """Every path under `out`, relative and POSIX-style (empty when `out` is not a directory)."""
    return {path.relative_to(out).as_posix() for path in out.rglob("*")} if out.is_dir() else set()


def _remove_added(out: Path, before: set[str]) -> None:
    """Removes the paths under `out` that were not there before (deepest first): what a failed finalize wrote."""
    for rel in sorted(_tree(out) - before, key=lambda s: s.count("/"), reverse=True):
        path = out / rel
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)


def finalize_checkpoint(
    work: str | Path,
    out_dir: str | Path,
    profile: dict[str, Any],
    extra_theory: dict[str, Any] | None = None,
    *,
    probe: bool = True,
    max_relation_drops: int = MAX_RELATION_DROPS,
    max_relation_drop_frac: float = MAX_RELATION_DROP_FRAC,
    max_group_drops: int = MAX_GROUP_DROPS_DEFAULT,
    max_group_drop_frac: float = MAX_GROUP_DROP_FRAC,
    admit: bool | None = None,
    admit_max_literals: int | None = None,
    relation_floor: float | None = None,
    repair_budget_s: float | None = None,
) -> dict[str, Any]:
    """Materialise the latest checkpoint in `work` as a submission directory. Replays the accepted relation and group
    logs, so no model state is needed. Pairwise checkpoints first drop AMO-shaped groups whose members became one
    literal after the EQUIV merges. Then the base is made consistent by admission (`admit`, harness/admit.py: the units
    in _admission_order, each admitted unless it contradicts the bindings and the admitted units; `relation_floor` drops
    the relations with a conf below it) when 2 x outcomes <= admit_max_literals and the theory is unscoped, or it is a
    candidates build whose scoped units are plain 2-CNF (they are held out of admission and settled after it, _settle_scoped,
    admit_mode admit). Otherwise,
    or when admission is refused, the post-hoc repair (_prune_in_process: relation drops up to max_relation_drops, group
    drops up to max_group_drops). The repair's budget `repair_budget_s` (None: until the build deadline) is checked between
    repair passes; when it runs out the repair stops with RepairBudgetStopped and nothing is written. Then the
    written submission is confirmed by mb-engine, with deferred group parts localized if the engine rejects it; past the
    build deadline that confirmation is skipped (base_probe skipped_deadline) and the build is still complete. An engine
    rejection of an admitted base falls back to the repair, within the repair budget (`probe=False` skips the checks and
    the engine). The deadline stops admission only after a unit was refused: a consistent admitted base is sealed even
    past the deadline. The repair budget bounds only the post-hoc repair: it applies where the repair runs (admission refused
    or not applicable, and the engine fallback), and an exhausted budget raises RepairBudgetStopped.
    The admission knobs (admit, admit_max_literals, relation_floor, repair_budget_s) left None are those of the checkpoint's
    policy. None is therefore not a way to clear a recorded relation_floor or repair_budget_s (there is no sentinel): a
    value given here replaces the recorded one. A checkpoint without "admit" in its policy is a pre-admission build
    (admit False). Returns the build summary."""
    out = Path(out_dir)
    existed, before = out.exists(), _tree(out)
    try:
        return _finalize(work, out, profile, extra_theory, probe=probe, max_relation_drops=max_relation_drops, max_relation_drop_frac=max_relation_drop_frac, max_group_drops=max_group_drops, max_group_drop_frac=max_group_drop_frac, admit=admit, admit_max_literals=admit_max_literals, relation_floor=relation_floor, repair_budget_s=repair_budget_s)
    except BaseException:  # a raise after the writes (the engine stage, the budget) leaves no submission behind
        if existed:
            _remove_added(out, before)
        else:
            shutil.rmtree(out, ignore_errors=True)
        raise


def _finalize(
    work: str | Path,
    out_dir: str | Path,
    profile: dict[str, Any],
    extra_theory: dict[str, Any] | None = None,
    *,
    probe: bool = True,
    max_relation_drops: int = MAX_RELATION_DROPS,
    max_relation_drop_frac: float = MAX_RELATION_DROP_FRAC,
    max_group_drops: int = MAX_GROUP_DROPS_DEFAULT,
    max_group_drop_frac: float = MAX_GROUP_DROP_FRAC,
    admit: bool | None = None,
    admit_max_literals: int | None = None,
    relation_floor: float | None = None,
    repair_budget_s: float | None = None,
) -> dict[str, Any]:
    """The work of finalize_checkpoint (see it). It may raise after writing into out_dir: that is cleaned up there."""
    t0 = time.monotonic()
    work, out_dir = Path(work), Path(out_dir)
    ckpt = _load_checkpoint(work / "checkpoint.json")
    resolved = _admission_knobs(ckpt.get("policy"))
    admit = resolved["admit"] if admit is None else admit
    admit_max_literals = resolved["admit_max_literals"] if admit_max_literals is None else admit_max_literals
    relation_floor = resolved["relation_floor"] if relation_floor is None else relation_floor
    repair_budget_s = resolved["repair_budget_s"] if repair_budget_s is None else repair_budget_s
    _check_admit_knobs(admit, admit_max_literals, relation_floor, repair_budget_s)
    budget_s = float(profile["build_deadline_s"]) - float(ckpt["wall_seconds"])

    def time_left() -> float:
        return budget_s - (time.monotonic() - t0)

    def budgeted(started: float) -> Callable[[], float]:  # the build deadline, or the repair budget from `started` if sooner
        def left() -> float:
            return time_left() if repair_budget_s is None else min(time_left(), repair_budget_s - (time.monotonic() - started))
        return left

    def repair_stop(exc: BaseCheckIncomplete, started: float) -> BaseCheckIncomplete:
        """The repair's own stop: RepairBudgetStopped when its budget ran out (not the build deadline), else `exc`."""
        if repair_budget_s is not None and time.monotonic() - started >= repair_budget_s:
            return RepairBudgetStopped(f"the repair budget of {repair_budget_s} s passed before the base theory was verified "
                                       "consistent", deadline_passed=time_left() <= 0)
        return exc

    gold_backed = _gold_backed(ckpt.get("policy"))
    if extra_theory is not None and not gold_backed:
        raise ValueError("a full theory replay is only written for a gold-backed build (its policy says it is not)")
    replay = _Replay(work, ckpt, strip_gold=not gold_backed)
    spend = {"calls": 0, "items": 0, "usd": 0.0}
    for call in _jsonl_prefix(work / "usage_calls.jsonl", ckpt["usage_bytes"]):
        spend["calls"] += 1
        spend["items"] += call.get("items", 1)
        spend["usd"] += call["cost_usd"]

    def usage_of() -> dict[str, Any]:
        usage = {
            "calls": _jsonl_prefix(work / "usage_calls.jsonl", ckpt["usage_bytes"]),
            "wall_seconds": ckpt["wall_seconds"],
            "peak_rss_mb": ckpt["peak_rss_mb"],
        }
        if ckpt.get("torn_usage_rows"):  # a resume cut a torn final row of the usage log (its placeholder is a call)
            usage["torn_usage_rows"] = ckpt["torn_usage_rows"]
        return usage

    units: list[_Unit] = []
    prune_method, prune_seconds = os.environ.get("MB_PRUNE", "scc"), 0.0
    admit_mode = "not_applicable" if extra_theory is not None else "not_run"  # admit, repair (and why), or not run
    settle = settle_ran = False  # settle: a candidates build's scoped units are settled after admission (see _settle_scoped)
    settle_cleared = settle_dropped = 0
    admit_rel = admit_grp = 0  # units admission rejected (drops)
    infeasible_dropped = infeasible_downgraded = []  # BC2: the units dropped and downgraded before the engine
    infeasible_skipped = None  # why the per-unit check did not run ("deadline", "numpy", "memory"); None otherwise
    admit_seconds = 0.0
    floor_count = 0  # relations the relation floor dropped (not contradictions)
    engine_stats = _engine_stats()
    masks_before: list[int] = []  # the interpretation masks before the repair (a cleared bit is a repair too)
    if extra_theory is not None:
        names = list(extra_theory["interpretations"])
        extra = ({"id": c["id"], "expr": c["expr"], "interps": c["interps"], "provenance": c.get("provenance")}
                 for c in extra_theory["constraints"])
        n_written = _write_full(out_dir, replay, extra, names, usage_of(), profile)
        base_probe = "not_applicable"
    else:
        combos = _interp_combos(ckpt.get("axes") or {})
        names = _interp_names(combos)
        units = _units(replay, work, ckpt, combos)
        for unit in units:  # EQUIV merges are known only now: AMO-shaped units with one literal twice are dropped
            if unit.members is None:
                continue
            literals = [replay._normalised(i) for i in unit.members if replay.cls_id[i] >= 0]
            if len(set(literals)) < len(literals):
                unit.dropped = True
        base_probe = "not_probed"
        for unit in units:  # a derived relation never outlives its representative (units are in log order)
            if unit.rep >= 0 and units[unit.rep].dropped:
                unit.dropped = True
        if probe:
            masks_before = [unit.mask for unit in units]
            check = _BaseCheck(replay, units, len(names))
            asked, groups_asked = int(ckpt["relations_committed"]), int(ckpt.get("groups_committed", 0))
            snapshot = [(unit.dropped, unit.down, unit.deferred, unit.expr, unit.floored, unit.mask) for unit in units]

            def restore_units() -> None:  # the units as the snapshot took them, before admission and the settle touched them
                for unit, (was_dropped, down, deferred, expr, was_floored, mask) in zip(units, snapshot):
                    unit.dropped, unit.down, unit.deferred, unit.expr, unit.floored, unit.mask = (
                        was_dropped, down, deferred, expr, was_floored, mask)

            # a scoped theory is admitted only on a candidates build whose scoped units are plain 2-CNF: they are held out of
            # admission and settled after it (_settle_scoped). Other scoped and huge theories are repaired.
            scoped = [n for n, unit in enumerate(units) if unit.mask != -1 and not unit.dropped]
            settle = admit and bool(scoped) and bool((ckpt.get("policy") or {}).get("candidates_gold_informed")) and not any(
                units[n].deferred for n in scoped)
            unscoped = not ckpt.get("axes")
            fits = (unscoped or settle) and 2 * len(replay.ids) <= admit_max_literals

            def drop_ledger() -> _DropLedger:
                return _DropLedger(max_relation_drops, max_group_drops, max_relation_drop_frac, asked, max_group_drop_frac,
                                   groups_asked)

            def drop_infeasible(closure: Any) -> tuple[list[int], list[int]]:  # BC2 on the base the engine is asked about
                nonlocal infeasible_skipped
                # unscoped and within the bound; without mb-engine: unverified
                if not unscoped or not fits or not engine.engine_available():
                    return [], []
                if not any(unit.deferred and not unit.dropped for unit in units):  # nothing to check: not a skip
                    return [], []
                reason = None
                if time_left() <= 0:
                    reason = "deadline"
                else:
                    try:
                        return _drop_infeasible_deferred(check, units, ledger, closure)
                    except ImportError:
                        reason = "numpy"
                    except MemoryError:
                        reason = "memory"
                infeasible_skipped = reason  # the localization handles these parts, as before this check
                print(f"finalize: the per-unit check of the deferred parts is skipped ({reason}); the engine localizes them",
                      file=sys.stderr, flush=True)
                return [], []

            admit_mode = "repair" if not admit else "repair_scoped" if ckpt.get("axes") else "repair_size"
            admitted, base_closure = None, None
            ledger = drop_ledger()
            if admit and fits:
                t_admit = time.monotonic()
                held = scoped if settle else []  # admission takes the base; the scoped units are settled after it
                for n in held:
                    units[n].dropped = True
                try:
                    admitted = _admit_build(check, units, ledger, relation_floor, time_left, work, replay.ids)
                    admit_mode = "admit" if admitted is not None else "repair_unverified"
                except ImportError:  # numpy is missing: the bit matrix cannot be built
                    admit_mode = "repair_no_numpy"
                except MemoryError:  # the bit matrix does not fit in memory
                    admit_mode = "repair_no_memory"
                finally:
                    for n in held:
                        units[n].dropped = False
                admit_seconds = round(time.monotonic() - t_admit, 3)
                base_closure = admitted.pop("closure") if admitted is not None else None  # BC2 reads it, then it is released
                if settle and admitted is not None:
                    base_closure = None  # released before the settle builds its own closures
                    try:
                        settled, settle_cleared = _settle_scoped(check, units, ledger, time_left, admitted["dropped"])
                    except MemoryError:  # the closures do not fit: the post-hoc repair, from the units as the snapshot has them
                        restore_units()
                        admitted, admit_mode = None, "repair_no_memory"
                    else:
                        admitted["dropped"] = admitted["dropped"] | settled
                        settle_dropped, settle_ran = len(settled), True
            floored: set[int] = set()
            if admitted is None:
                ledger = drop_ledger()  # the repair and the engine localization count against one fresh ledger (the group cap)
                floored = _mark_floored(units, relation_floor, check)  # the relation floor holds on the repair path too
                floor_count = sum(1 for n in floored if units[n].kind == "relation")
                repair_t0 = time.monotonic()
                try:
                    dropped = _prune_in_process(
                        check, max_relation_drops, max_group_drops, budgeted(repair_t0),
                        relation_frac=max_relation_drop_frac, asked=asked, group_frac=max_group_drop_frac, groups_asked=groups_asked,
                        ledger=ledger,
                    )
                except BaseCheckIncomplete as exc:  # the budget or the deadline stopped the repair: nothing is written
                    raise repair_stop(exc, repair_t0)
                prune_seconds = round(time.monotonic() - repair_t0, 3)
            if admitted is not None:
                dropped, floored = admitted["dropped"], admitted["floored"]
                admit_rel, admit_grp = admitted["rejected_relations"], admitted["rejected_groups"]
            for n in floored:
                units[n].floored = True
            for n in dropped:
                units[n].dropped = True
            # the floored relations of the repair (marked before admission) and of admission: the units say which
            floor_count = sum(1 for unit in units if unit.floored and unit.kind == "relation")
            # the admitted closure is kept only when N0 is not rebuilt: a rebuild releases it first (two matrices never coexist)
            reuse = None if any(unit.clauses for unit in units if unit.deferred and not unit.dropped) else base_closure
            base_closure = None
            infeasible_dropped, infeasible_downgraded = drop_infeasible(reuse)
            reuse = None
            try:
                base_probe = _settle_with_engine(units, replay, work, out_dir, names, profile, usage_of, time_left, ledger,
                                                 engine_stats)
            except _EngineRejected:
                if admit_mode != "admit":
                    raise
                # mb-engine refuses the admitted base (no deferred unit localizes it): restore the units, repair post hoc
                restore_units()
                admit_mode, admit_rel, admit_grp, floor_count = "repair_after_engine", 0, 0, 0
                settle_ran, settle_cleared, settle_dropped = False, 0, 0  # the settle's bits were restored with the units
                ledger = drop_ledger()
                floored = _mark_floored(units, relation_floor, check)
                floor_count = sum(1 for n in floored if units[n].kind == "relation")
                t_prune = time.monotonic()
                try:
                    for n in _prune_in_process(
                        check, max_relation_drops, max_group_drops, budgeted(t_prune), relation_frac=max_relation_drop_frac,
                        asked=asked, group_frac=max_group_drop_frac, groups_asked=groups_asked, ledger=ledger,
                    ):
                        units[n].dropped = True
                except BaseCheckIncomplete as exc:  # the repair budget applies here too: it is measured from this repair
                    raise repair_stop(exc, t_prune)
                prune_seconds = round(time.monotonic() - t_prune, 3)
                engine_stats.update(_engine_stats())  # the summary counts this settle's engine work only
                infeasible_dropped, infeasible_downgraded = drop_infeasible(None)  # the repaired base: its closure is built here
                base_probe = _settle_with_engine(units, replay, work, out_dir, names, profile, usage_of, time_left, ledger,
                                                 engine_stats)
        else:
            _write_units(out_dir, replay, units, names, usage_of(), profile)
        n_written = sum(not unit.dropped for unit in units)
    if ckpt.get("policy") is not None:  # the admission knobs applied by this finalize join the build's own (sealed) policy
        applied = extra_theory is None and probe  # admission runs only on the pairwise path with the in-process check
        knobs = {"admit": admit if applied else None, "admit_max_literals": admit_max_literals if applied else None,
                 "relation_floor": relation_floor if applied else None, "repair_budget_s": repair_budget_s if applied else None,
                 "max_relation_drops": max_relation_drops if applied else None,
                 "max_relation_drop_frac": max_relation_drop_frac if applied else None,
                 "max_group_drops": max_group_drops if applied else None,
                 "max_group_drop_frac": max_group_drop_frac if applied else None}
        policy = {key: value for key, value in ckpt["policy"].items() if key not in ("profile", "corpus")}  # profile.json holds the profile
        extended = {"budget_extended": ckpt["budget_extended"]} if "budget_extended" in ckpt else {}
        if ckpt.get("decisions_complete") is False:  # decisions.jsonl lacks the decisions made before a legacy checkpoint
            extended["decisions_complete"] = False
        ir.write_json(out_dir / "build_policy.json", {**FLAG_DEFAULTS, **policy, **knobs, **extended})
    # drops are counted per relation and per group row; a floored relation is not a drop (it is floor_dropped_relations)
    relations_dropped = sum(1 for unit in units if unit.dropped and not unit.floored and unit.kind == "relation")
    group_rows_dropped = len({unit.row if unit.row >= 0 else ("unit", n) for n, unit in enumerate(units)
                              if unit.dropped and unit.kind not in ("relation", "derived")})
    summary = {
        "mode": "full_replay" if extra_theory is not None else "pairwise",
        "outcomes": len(replay.ids),
        "candidate_pairs": ckpt["pairs_total"],
        "blocking_recall": ckpt.get("blocking_recall"),
        "gold_pairs": ckpt.get("gold_pairs"),
        "relations_committed": ckpt["relations_committed"],
        "equiv_rejected": ckpt["equiv_rejected"],
        "relations_rejected": ckpt.get("relation_rejected", 0),
        "constraints": n_written,
        "relations_dropped": relations_dropped,
        "relations_asked": ckpt["relations_committed"],
        "contradiction_rate": round(relations_dropped / ckpt["relations_committed"], 6) if ckpt["relations_committed"] else 0.0,
        "floor_dropped_relations": floor_count,
        "engine_rounds": engine_stats["rounds"],
        "engine_probes": engine_stats["probes"],
        "engine_localized_drops": engine_stats["localized_drops"],
        "deferred_infeasible_dropped": len(infeasible_dropped),  # BC2: dropped against N0, before the engine
        "deferred_infeasible_downgraded": len(infeasible_downgraded),  # BC2: exactly_ones downgraded against N0 (not localization's)
        "deferred_check_skipped": infeasible_skipped,  # "deadline", "numpy" or "memory" when the per-unit check did not run
        "deferred_unresolved": engine_stats["deferred_unresolved"],  # BC1: left unresolved by a sealed localization (partial)
        "engine_seconds": engine_stats["engine_seconds"],
        "write_seconds": engine_stats["write_seconds"],
        "finalize_seconds": round(time.monotonic() - t0, 3),
        "prune_method": prune_method,
        "prune_seconds": prune_seconds,
        "admit_mode": admit_mode,
        "admit_rejected_relations": admit_rel,
        "admit_rejected_groups": admit_grp,
        "admit_seconds": admit_seconds,
        "resumed": ckpt.get("resumes", 0) > 0,
        "prior_calls": ckpt.get("prior_calls", 0),
        "derived_dropped": sum(1 for unit in units if unit.dropped and not unit.floored and unit.kind == "derived"),
        "base_check": "in_process" if extra_theory is None and probe else "not_run",
        "base_probe": base_probe,
        "interpretations": names,
        "budget_stopped": ckpt["budget_stopped"],
        "adapter_fatal": ckpt.get("adapter_fatal", False),
        "calls": spend["calls"],
        "items": spend["items"],
        "pairs_derived": ckpt.get("pairs_derived", 0),
        "pairs_derived_none": ckpt.get("pairs_derived_none", 0),
        "dedup_representatives": ckpt.get("dedup_representatives", 0),
        "dedup_named": ckpt.get("dedup_named", 0),
        "dedup_undecided": ckpt.get("dedup_undecided", 0),
        "dedup_none": ckpt.get("dedup_none", 0),
        "dedup_fallback_asked": ckpt.get("dedup_fallback_asked", 0),
        "dedup_fallback_skipped": ckpt.get("dedup_fallback_skipped", 0),  # complement pairs the --complement-fallback policy left unasked
        "pairs_asked_cap": ckpt.get("pairs_asked_cap"),
        "pairs_considered": ckpt.get("pairs_considered", 0),
        "groups_disabled_reason": ckpt.get("groups_disabled_reason"),
        "pairs_capped": ckpt.get("pairs_capped", False),
        "calls_capped": ckpt.get("calls_capped", False),
        "spend_usd": round(spend["usd"], 6),
        "wall_seconds": ckpt["wall_seconds"],
        "phase_seconds": ckpt.get("phase_seconds", {}),  # timing: the build phases' gateway seconds and their calls' seconds
        "phase_call_seconds": ckpt.get("phase_call_seconds", {}),
        # the reserve the build applied (its checkpoint); older checkpoints predate it and take the profile's
        "finalize_reserve_s": ckpt.get("finalize_reserve_s", round(FINALIZE_RESERVE_FRAC * float(profile["build_deadline_s"]), 3)),
        "out_dir": str(out_dir),
        "group_candidates": ckpt.get("group_candidates", 0),
        "groups_committed": ckpt.get("groups_committed", 0),
        "groups_rejected": ckpt.get("groups_rejected", 0),
        "group_errors": ckpt.get("group_errors", 0),
        "group_scope_rejected": ckpt.get("group_scope_rejected", 0),
        "group_budget_stopped": ckpt.get("group_budget_stopped", False),
        "groups_dropped": group_rows_dropped,  # group rows: a group with a dropped unit is one drop
        "groups_dropped_pct": round(100 * group_rows_dropped / ckpt["groups_committed"], 2) if ckpt.get("groups_committed", 0) else 0.0,
        # a base is repaired when a relation or group was dropped (not floored), or a group lost an interpretation
        "base_repaired": any((unit.dropped and not unit.floored) or unit.down for unit in units)
                         or any(unit.mask != mask for unit, mask in zip(units, masks_before)),
        "groups_downgraded": sum(1 for unit in units if unit.down),
        "group_recall": ckpt.get("group_recall"),
        "gold_groups": ckpt.get("gold_groups"),
        "pairs_skipped_by_groups": ckpt.get("pairs_skipped_by_groups", 0),
        "axes": ckpt.get("axes") or {},
        "groups_fatal": ckpt.get("groups_fatal", False),
        "gold_refs_rejected": ckpt.get("gold_refs_rejected", 0) + replay.gold_refs_stripped,
    }
    if settle_ran:  # the scoped settle of a candidates build that ran: its cleared interpretation bits and its dropped units
        summary["settle_cleared_bits"], summary["settle_dropped"] = settle_cleared, settle_dropped
    for key in ("budget_extended", "stopped_in", "torn_usage_rows"):  # only when a resume extended the limits, or a stop stays final
        if ckpt.get(key):
            summary[key] = ckpt[key]
    if ckpt.get("decisions_complete") is False:
        summary["decisions_complete"] = False
    reasons = partial_reasons(summary)
    if reasons and not (summary["adapter_fatal"] or summary["groups_fatal"]):  # the marker travels with the submission
        ir.write_json(out_dir / PARTIAL_FILE, {"partial": True, "reasons": reasons})
    return summary


def gold_pairs_from_theory(theory: dict[str, Any]) -> list[tuple[str, str]]:
    """Outcome-ID pairs of the pairwise gold relations of a theory: implies and iff between two variables, and
    atmost with k=1 over two variables. The reference for `build(gold_pairs=...)`."""
    pairs = []
    for con in theory["constraints"]:
        expr = con["expr"]
        if expr["op"] in ("implies", "iff") and expr["a"]["op"] == "var" and expr["b"]["op"] == "var":
            pairs.append((expr["a"]["id"], expr["b"]["id"]))
        elif expr["op"] == "atmost" and expr["k"] == 1 and len(expr["args"]) == 2 and all(x["op"] == "var" for x in expr["args"]):
            pairs.append((expr["args"][0]["id"], expr["args"][1]["id"]))
    return pairs


def gold_groups_from_theory(theory: dict[str, Any]) -> list[dict[str, Any]]:
    """Cardinality groups of a theory over outcome variables: exactly k=1, atmost, atleast, and `or` (at least one),
    with two or more members. Each is {kind, k, members, interps}. The reference for group_recall."""
    kinds = {"exactly": "exactly_one", "atmost": "at_most_one", "atleast": "at_least_k", "or": "at_least_k"}
    groups = []
    for con in theory["constraints"]:
        expr = con["expr"]
        args = expr.get("args") or []
        if expr["op"] not in kinds or len(args) < 2 or not all(x["op"] == "var" for x in args):
            continue
        k = expr.get("k", 1)
        if expr["op"] == "exactly" and k != 1:
            continue
        kind = kinds[expr["op"]]
        if kind == "at_most_one" and k != 1:
            kind = "at_most_k"
        groups.append({"kind": kind, "k": k, "members": [x["id"] for x in args], "interps": con.get("interps")})
    return groups


def _gold_record_pairs(store: _RecordStore, gold_pairs: Iterable[tuple[str, str]]) -> set[tuple[int, int]]:
    """Gold pairs of outcome IDs as sorted record-index pairs. Pairs naming an unknown outcome are ignored."""
    pairs = {tuple(sorted(p)) for p in gold_pairs}
    wanted = {oid for pair in pairs for oid in pair}
    position = {rec["id"]: i for i, rec in enumerate(store) if rec["id"] in wanted}
    return {tuple(sorted((position[a], position[b]))) for a, b in pairs if a in position and b in position}


class _GroupRecall:
    """Share of gold groups contained in some candidate set. Candidates are fed one at a time; a gold group with a
    member outside the corpus can never be recalled."""

    def __init__(self, gold: list[dict[str, Any]], position: dict[str, int]):
        self.members: list[frozenset[int] | None] = []
        self.by_member: dict[int, list[int]] = {}
        for g, group in enumerate(gold):
            if all(m in position for m in group["members"]):
                members = frozenset(position[m] for m in group["members"])
                self.members.append(members)
                for i in members:
                    self.by_member.setdefault(i, []).append(g)
            else:
                self.members.append(None)
        self.hit = [False] * len(self.members)

    def feed(self, candidate: list[int]) -> None:
        cand = set(candidate)
        for i in candidate:
            for g in self.by_member.get(i, ()):
                if not self.hit[g] and self.members[g] <= cand:
                    self.hit[g] = True

    def value(self) -> float | None:
        return round(sum(self.hit) / len(self.hit), 6) if self.hit else None


def _pair_stream(path: Path) -> Iterator[tuple[int, int]]:
    """Candidate record pairs of pairs.bin, in order."""
    with open(path, "rb") as pf:
        while True:
            chunk = array("I")
            try:
                chunk.fromfile(pf, 1 << 17)
            except EOFError:
                pass  # short final chunk
            if not chunk:
                break
            for k in range(0, len(chunk), 2):
                yield chunk[k], chunk[k + 1]


def _group_key(row: dict[str, Any]) -> tuple:
    """Identity of an accepted group: the same kind, k, direction, members (in chain order) and scope is one group, and a
    reading link also its candidate call (cand: two reading rows may name the mutual pair, each with its own readings)."""
    members = tuple(row["members"]) if row["kind"] == "ordered_chain" else tuple(sorted(row["members"]))
    return (row["kind"], row["k"], row["direction"], members, json.dumps(row["scope"], sort_keys=True), row.get("cand"))


def _settle_group(
    raw: Any, ctx: dict[str, Any], health: _AdapterHealth, tally: Counter,
) -> tuple[list[dict[str, Any]], bool]:
    """(checked decisions of one group candidate, failed) from its answer. A failed call is one group error (booked by
    _book_errors); a non-list answer is one group error and one failed decision; each invalid decision is a rejection
    (a scope problem is also a scope rejection). A reading answer is normalized by _reading_items instead."""
    if isinstance(raw, AdapterError):
        return [], True
    if not isinstance(raw, list):
        health.record(False)
        return [], True
    health.record(True)
    if ctx["source"] == "reading":  # the picks of base.reading_picks: non-chain and malformed siblings are ignored
        items, rejected = _reading_items(raw, [m["id"] for m in ctx["members"]])
        tally["groups_rejected"] += rejected
        return items, False
    member_ids = {m["id"] for m in ctx["members"]}
    items = []
    for decision in raw:
        try:
            items.append(check_group(decision, member_ids))
        except ScopeError:
            tally["groups_rejected"] += 1
            tally["group_scope_rejected"] += 1
        except DecisionError:
            tally["groups_rejected"] += 1
    return items, False


def _reading_items(raw: list[Any], members: list[str]) -> tuple[list[dict[str, Any]], int]:
    """The units of a reading answer, normalized by base.reading_picks (the scorer's rule): for each reading with an option
    pick c, the canonical pair [u, c] and [c, u] scoped to that reading, so an unscoped or two-reading pair becomes one
    scoped pair per reading. Both links come from this one candidate call (the settle pairs them). "none" compiles nothing;
    "invalid" compiles nothing and is one rejection. An answer with no valid decision and a malformed one is one rejection
    too (the scorer's __missing__: no pick is read); malformed siblings of a valid decision are ignored. The raw answer is
    logged unchanged (decisions.jsonl)."""
    u, member_ids = members[0], set(members)
    valid = malformed = 0
    for group in raw:
        try:
            check_group(group, members)
            valid += 1
        except DecisionError:
            malformed += 1
    if malformed and not valid:
        return [], 1
    items, rejected = [], 0
    for reading, pick in reading_picks(raw, members).items():
        if pick == "invalid":
            rejected += 1
        elif pick != "none":
            scope = {"axis": "reading", "values": [reading], "of": ["alt", "base"]}
            for pair in ([u, pick], [pick, u]):
                items.append(check_group({"kind": "ordered_chain", "members": pair, "scope": scope}, member_ids))
    return items, rejected


CONTEXT_MAX_CHARS = 2000  # the longest rule card a corpus row may carry (checked while the build streams the records)


def _context(rec: dict[str, Any]) -> dict[str, Any]:
    """The outcome context an adapter sees (bindings, relations and group members): no gold, no other fields. The optional
    `context` (a league rule card) is there only when the record has one, so the requests of other records keep their bytes."""
    ctx = {"id": rec["id"], "question": rec["question"], "label": rec.get("label"), "event_title": rec.get("event_title")}
    if rec.get("context") is not None:
        ctx["context"] = rec["context"]
    return ctx


CANDIDATE_KEYS = {"decide_binding": {"qid", "op", "id"}, "decide_relation": {"qid", "op", "a", "b"},
                  "decide_group": {"qid", "op", "members", "source"}}  # docs/mentat_hard_contracts.md, Curated suite
CANDIDATE_GROUP_SOURCES = ("curated", "reading")  # a reading row (members [u, o1..o4]) asks the reading-scoped chains of u


def _question_ids(q: dict[str, Any]) -> list[Any]:
    """The outcome ids a curated question names."""
    return [q["id"]] if q["op"] == "decide_binding" else [q["a"], q["b"]] if q["op"] == "decide_relation" else q["members"]


def load_candidates(path: str | Path, ids: set[str] | None = None) -> tuple[list[dict[str, Any]], str]:
    """The questions of a curated candidates.jsonl in file order, and the file's sha256. ValueError on a file inside a
    `gold` directory (a build reads public files only), on any line off the contract's schema (unique qids, one binding
    question per outcome, a relation's a != b, a group's 2..GROUP_MAX_MEMBERS distinct members, a reading group exactly 5), and, when `ids` (the
    corpus outcome ids) is given, on an id outside the corpus."""
    path = Path(path)
    if any(part.lower() == "gold" for part in path.resolve().parent.parts):
        raise ValueError(f"candidates file {path} is inside a gold directory; the build reads public files only")
    data = path.read_bytes()
    questions: list[dict[str, Any]] = []
    qids: set[str] = set()
    bound: set[str] = set()
    for number, line in enumerate(data.decode("utf-8").splitlines(), 1):
        where = f"candidates line {number}"
        try:
            q = json.loads(line)
        except ValueError as exc:
            raise ValueError(f"{where} is not JSON ({exc})") from None
        if not isinstance(q, dict) or q.get("op") not in CANDIDATE_KEYS:
            raise ValueError(f"{where}: op must be one of {sorted(CANDIDATE_KEYS)}")
        if set(q) != CANDIDATE_KEYS[q["op"]]:
            raise ValueError(f"{where}: a {q['op']} question has exactly the keys {sorted(CANDIDATE_KEYS[q['op']])}")
        if not isinstance(q["qid"], str) or not q["qid"] or q["qid"] in qids:
            raise ValueError(f"{where}: qid must be a non-empty string, unique in the file")
        qids.add(q["qid"])
        if q["op"] == "decide_group" and (not isinstance(q["members"], list) or q["source"] not in CANDIDATE_GROUP_SOURCES):
            raise ValueError(f"{where}: a group question has a members list and source 'curated' or 'reading'")
        if q["op"] == "decide_group" and q["source"] == "reading" and len(q["members"]) != 5:
            raise ValueError(f"{where}: a reading question has exactly 5 members (the proposition and 4 options)")
        refs = _question_ids(q)
        if not all(isinstance(ref, str) for ref in refs):
            raise ValueError(f"{where}: outcome ids are strings")
        if q["op"] == "decide_binding":
            if q["id"] in bound:
                raise ValueError(f"{where}: a second binding question for {q['id']!r}")
            bound.add(q["id"])
        elif q["op"] == "decide_relation" and q["a"] == q["b"]:
            raise ValueError(f"{where}: a relation question needs a != b")
        elif q["op"] == "decide_group" and not (2 <= len(refs) <= GROUP_MAX_MEMBERS and len(set(refs)) == len(refs)):
            raise ValueError(f"{where}: a group question has 2..{GROUP_MAX_MEMBERS} distinct members")
        if ids is not None and any(ref not in ids for ref in refs):
            raise ValueError(f"{where}: {next(ref for ref in refs if ref not in ids)!r} is not a corpus outcome")
        questions.append(q)
    return questions, hashlib.sha256(data).hexdigest()


def _decision_line(op: str, qid: str | None, raw: Any, *, a: str | None = None, b: str | None = None,
                   oid: str | None = None, members: list[str] | None = None) -> str:
    """One decisions.jsonl line (docs/mentat_hard_contracts.md, Raw decision log): the question exactly as asked and the
    adapter's raw answer. An item the gateway gave up on has decision null and its error; a malformed answer is logged as
    it came. probs is the decision's, or the `.probs` of a group decision list."""
    error = None
    if isinstance(raw, AdapterError):
        decision, error = None, f"{type(raw).__name__}: {raw}"
    elif isinstance(raw, list):
        decision = {"groups": [dict(g) if isinstance(g, dict) else g for g in raw]}
    elif op == "decide_relation" and not isinstance(raw, dict):
        decision = {"relation": raw}  # a bare relation name
    else:
        decision = raw
    conf = raw.get("conf") if isinstance(raw, dict) else getattr(raw, "conf", None)
    probs = raw.get("probs") if isinstance(raw, dict) else getattr(raw, "probs", None)
    row = {"qid": qid, "op": op, "a": a, "b": b, "id": oid, "members": members, "decision": decision, "conf": conf,
           "probs": probs, "error": error}
    return json.dumps(row, ensure_ascii=False, default=str)


def _admit(
    item: dict[str, Any], source: str, position: dict[str, int], cls_id: array, neg: bytearray, axes: dict[str, list[str]],
    max_interps: int, seen: set[tuple], pending: set[tuple], tally: Counter, cand: str | None = None,
) -> dict[str, Any] | None:
    """The row to log for a checked group decision, or None: a duplicate is skipped silently, an inadmissible one
    (two bound members of one literal under an AMO-shaped kind, two bound members of one class in a chain, or scope axes
    beyond the caps) is counted as rejected. Accepting it widens `axes` in place. EQUIV merges come later, so a literal
    collision that only appears after a merge is caught at finalize (AMO-shaped units are dropped there)."""
    members = [position[m] for m in item["members"]]
    kind, scope = item["kind"], item["scope"]
    if kind in ("exactly_one", "at_most_one") or (kind == "at_most_k" and item["k"] == 1):
        bound = [(cls_id[i], neg[i]) for i in members if cls_id[i] >= 0]
        if len(set(bound)) < len(bound):
            tally["groups_rejected"] += 1
            return None
    elif kind == "ordered_chain":  # admission only: a degenerate chain (same-class duplicate, or p and not-p) is rejected here,
        # not a contradiction; a class shared through a later EQUIV merge is not checked at finalize
        classes = [cls_id[i] for i in members if cls_id[i] >= 0]
        if len(set(classes)) < len(classes):
            tally["groups_rejected"] += 1
            return None
    row = {
        "kind": kind, "k": item["k"], "direction": item["direction"],
        "members": members if kind == "ordered_chain" else sorted(members), "scope": scope, "source": source,
    }
    if item.get("conf") is not None:  # the optional confidence (absent from the rows of a child that sends none)
        row["conf"] = item["conf"]
    if cand is not None:  # a reading answer's links share their candidate call (the settle's pair identity)
        row["cand"] = cand
    key = _group_key(row)
    if key in seen or key in pending:
        return None
    if scope is not None:
        widened = {**axes, scope["axis"]: sorted(set(axes.get(scope["axis"], [])) | set(scope["of"]))}
        if len(widened) > MAX_GROUP_AXES or math.prod(len(v) for v in widened.values()) > max_interps:
            tally["groups_rejected"] += 1
            tally["group_scope_rejected"] += 1
            return None
        axes.clear()
        axes.update(widened)
    pending.add(key)
    return row


PHASE_ORDER = ("bindings", "blocking", "groups", "groups_done", "relations")  # the phases of a build, in order
PHASE_OP = {"bindings": "decide_binding", "groups": "decide_group", "relations": "decide_relation"}  # a phase's calls' op
RESUME_POLICY_KEYS = ("adapter", "groups", "group_sources", "group_max_members", "group_budget_frac", "max_group_calls",
                      "max_df", "top_k", "complement_dedup", "complement_fallback", "pair_order", "batch_size", "max_pairs",
                      "max_calls", "admit", "admit_max_literals", "relation_floor", "repair_budget_s", "candidates_sha256")
RESUME_LIMIT_KEYS = ("spend_ceiling_usd", "build_deadline_s")  # the profile keys a resume may change (see _check_resume_profile)
RESUME_STOP_PHASE = {"blocking": "bindings", "groups": "groups"}  # where a resume that clears a stop re-enters (its phase)


def _past(resume_phase: str | None, phase: str) -> bool:
    """Is `phase` complete in a checkpoint of phase `resume_phase` (None: a fresh build, nothing complete)?"""
    return resume_phase is not None and PHASE_ORDER.index(resume_phase) >= PHASE_ORDER.index(phase)


def _load_checkpoint(path: Path) -> dict[str, Any]:
    """The checkpoint object in `path` (OSError when it cannot be read). Anything else is CorruptCheckpoint."""
    try:
        ckpt = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise CorruptCheckpoint(f"{path} is corrupt ({exc}): the checkpoint cannot be resumed or finalized") from exc
    if not isinstance(ckpt, dict):
        raise CorruptCheckpoint(f"{path} is not a checkpoint object: the checkpoint cannot be resumed or finalized")
    problem = _checkpoint_problem(ckpt)
    if problem is not None:
        raise CorruptCheckpoint(f"{path} is corrupt ({problem}): the checkpoint cannot be resumed or finalized")
    return ckpt


def _read_resume_checkpoint(work: Path) -> dict[str, Any]:
    path = work / "checkpoint.json"
    if not path.is_file():
        raise ValueError(f"nothing to resume: {work} has no checkpoint (a build that stopped before its first checkpoint "
                         "must be rebuilt)")
    return _load_checkpoint(path)


def _check_resume_policy(old: dict[str, Any], new: dict[str, Any], adapter: Any) -> None:
    """A resumed build must make the decisions the checkpoint was made with: the same policy knobs and the same model."""
    old = {**FLAG_DEFAULTS, **old}  # a checkpoint from before these flags: the defaults
    old = {**old, **_admission_knobs(old)}  # a pre-admission checkpoint (no "admit" key) resumes as admit False
    for key in RESUME_POLICY_KEYS:
        if old.get(key) != new.get(key):
            hint = ""
            if key == "admit":  # a checkpoint of a build without admission (or from before it) resumes with --no-admit
                hint = " (a checkpoint without admission resumes with --no-admit)" if old[key] is False else \
                    " (resume without --no-admit)"
            raise ValueError(f"cannot resume: the checkpoint was built with {key}={old.get(key)!r}, the request has "
                             f"{key}={new.get(key)!r}; resume with the same flags or start a new build{hint}")
    if old.get("model") is not None and new.get("model") is not None and old["model"] != new["model"]:
        raise ValueError("cannot resume: the child's model block differs from the one the checkpoint was built with")


def _corpus_digest(records: Iterable[Any]) -> tuple[str, int]:
    """sha256 of the outcome records in order, one canonical JSON line each: the corpus identity, and the number of records
    that carry a context. A fresh build computes it while it reads the records (build), a resume computes it over the
    records it is given and compares it with the checkpoint's (a resume reads the corpus once more, and makes no model call)."""
    digest = hashlib.sha256()
    cards = 0
    for record in records:
        digest.update((ir.dumps(record) + "\n").encode("utf-8"))
        cards += isinstance(record, dict) and record.get("context") is not None
    return digest.hexdigest(), cards


def _check_resume_profile(old: dict[str, Any], new: dict[str, Any]) -> None:
    """A resumed build keeps its profile (max_interpretations and the rest bound what the decisions were made under), except
    the two limits a resume may extend (RESUME_LIMIT_KEYS). A limit may not shrink: a stop is cleared only by larger limits."""
    for key in RESUME_LIMIT_KEYS:
        if old.get(key) is not None and new.get(key) is not None and float(new[key]) < float(old[key]):
            raise ValueError(f"cannot resume: {key} is {new[key]!r}, below the {old[key]!r} the checkpoint was built with; "
                             "a resume may only extend the limits (spend_ceiling_usd, build_deadline_s)")
    for key in sorted(set(old) | set(new)):
        if key not in RESUME_LIMIT_KEYS and old.get(key) != new.get(key):
            raise ValueError(f"cannot resume: the checkpoint was built with profile {key}={old.get(key)!r}, the request has "
                             f"{key}={new.get(key)!r}; resume with the same profile ({' and '.join(RESUME_LIMIT_KEYS)} may change)")


RESUME_LOG_KEYS = (("decided.jsonl", "decided_bytes"), ("relations.jsonl", "relations_bytes"), ("groups.jsonl", "groups_bytes"),
                   ("decisions.jsonl", "decisions_bytes"))


def _check_log_prefix(path: Path, nbytes: int, name: str) -> None:
    """The checkpointed prefix of a decision log is whole JSON rows that end exactly at its checkpointed length."""
    if nbytes <= 0:
        return
    seen = 0
    with open(path, "rb") as fh:
        for number, raw in enumerate(fh, 1):
            if seen >= nbytes:
                break
            seen += len(raw)
            try:
                json.loads(raw)
            except ValueError as exc:
                raise ValueError(f"cannot resume: {name} row {number} is corrupt ({exc}); the checkpointed rows must parse") from exc
    if seen != nbytes:
        raise ValueError(f"cannot resume: the rows of {name} do not end at the checkpoint's {nbytes} bytes")


def _plan_resume_logs(work: Path, ckpt: dict[str, Any], model: str, batch_size: int) -> dict[str, Any]:
    """Checks every log of a resume before any of them is changed: each decision log is at least its checkpointed length
    and its checkpointed prefix parses, and the usage log is valid (see _read_usage_plan). Returns the usage plan. Nothing
    is written here."""
    for name, key in RESUME_LOG_KEYS:
        path, nbytes = work / name, int(ckpt.get(key, 0))
        size = path.stat().st_size if path.exists() else 0
        if size < nbytes:
            raise ValueError(f"cannot resume: {name} is shorter than the checkpoint says ({size} < {nbytes} bytes)")
        _check_log_prefix(path, nbytes, name)
    plan = _read_usage_plan(work / "usage_calls.jsonl", int(ckpt.get("usage_bytes", 0)), model, batch_size)
    cut = plan["placeholder"]
    if cut is not None and "op" not in cut and PHASE_OP.get(ckpt.get("phase")):
        cut["op"] = PHASE_OP[ckpt["phase"]]  # shortcut: a torn first row has no row before it, so it takes the checkpoint's phase op
    return plan


def _read_usage_plan(path: Path, checkpoint_bytes: int, model: str, batch_size: int) -> dict[str, Any]:
    """The usage log of a resume, read in full, and the plan for it. Every row is a paid call, the ones after the
    checkpoint too: their items and spend count, and their seconds are added to the clock the resume starts from (the
    checkpoint's wall time does not include them). A kill mid-write can leave an incomplete final row. One that does not
    parse is cut and replaced by a placeholder of `batch_size` items (a batch is one usage row of at most batch_size
    items), cost unknown and counted as 0, marked torn. One that parses but has no newline gets the newline. A row that is
    not a valid usage entry, or an unparseable row that is not the last one, raises ValueError before anything changes."""
    plan: dict[str, Any] = {"calls": 0, "items": 0, "usd": 0.0, "torn": 0, "discarded_seconds": 0.0, "cut": None,
                            "newline": False, "placeholder": None}
    if not path.exists():
        if checkpoint_bytes > 0:  # the checkpoint counts paid calls in it: without the log they cannot be charged
            raise ValueError(f"cannot resume: usage_calls.jsonl is missing, but the checkpoint counts {checkpoint_bytes} bytes "
                             "of it; its paid calls cannot be charged")
        return plan
    size = path.stat().st_size
    if size < checkpoint_bytes:
        raise ValueError(f"cannot resume: usage_calls.jsonl is shorter than the checkpoint says ({size} < {checkpoint_bytes} bytes)")
    bad_line, bad_at, offset, last_op = 0, 0, 0, None
    with open(path, "rb") as fh:
        for number, raw in enumerate(fh, 1):
            if bad_line:  # the unparseable row was followed by another row
                raise ValueError(f"cannot resume: usage_calls.jsonl row {bad_line} is corrupt and is not its last row (a torn "
                                 "tail is only the final row); the resume is refused")
            try:
                call = json.loads(raw)
            except ValueError:  # JSONDecodeError and UnicodeDecodeError
                if offset < checkpoint_bytes:  # inside the checkpointed rows: damage, not a torn tail (which lies past them)
                    raise ValueError(f"cannot resume: usage_calls.jsonl row {number} is corrupt inside the checkpointed rows; "
                                     "a torn tail lies past the checkpoint, so the file is damaged and the resume is "
                                     "refused") from None
                bad_line, bad_at = number, offset
            else:
                try:
                    fields = check_usage(call)
                    if call.get("kind") not in USAGE_KINDS:
                        raise AdapterError(f"unknown kind {call.get('kind')!r}")
                    if "op" in call and call["op"] not in USAGE_OPS:
                        raise AdapterError(f"unknown op {call['op']!r}")
                    seconds = finite_number(call, "seconds")
                    if not isinstance(call.get("retry", False), bool):
                        raise AdapterError("retry must be a boolean")
                except AdapterError as exc:
                    raise ValueError(f"cannot resume: usage_calls.jsonl row {number} is not a valid usage entry: {exc}") from exc
                plan["calls"] += 1
                last_op = call.get("op")
                plan["items"] += fields.get("items", 1)
                plan["usd"] += fields["cost_usd"]
                plan["torn"] += bool(call.get("torn"))
                if offset >= checkpoint_bytes:  # past the checkpoint: a call the killed run made but did not commit
                    plan["discarded_seconds"] += seconds
                plan["newline"] = not raw.endswith(b"\n")
            offset += len(raw)
    if bad_line:
        plan.update(cut=bad_at, newline=False)
        plan["placeholder"] = {"model": model, "kind": "local", "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0,
                               "seconds": 0.0, "retry": False, "items": batch_size, "torn": True}
        if last_op is not None:  # shortcut: the op of the row before the cut (the cut row's own op is lost); report.py leaves torn rows out
            plan["placeholder"]["op"] = last_op
        plan["calls"] += 1
        plan["items"] += batch_size
        plan["torn"] += 1
    return plan


def _apply_resume_logs(work: Path, ckpt: dict[str, Any], plan: dict[str, Any]) -> None:
    """Cuts the decision logs back to their checkpointed lengths (rows past the last checkpoint are not committed) and
    repairs the usage log as _read_usage_plan planned. Called only once _plan_resume_logs has accepted the resume."""
    for name, key in RESUME_LOG_KEYS:
        if (work / name).exists():
            os.truncate(work / name, int(ckpt.get(key, 0)))
    usage = work / "usage_calls.jsonl"
    if plan["cut"] is not None:
        os.truncate(usage, plan["cut"])
        with open(usage, "a", encoding="utf-8", newline="\n") as fh:
            _append(fh, [ir.dumps(plan["placeholder"])])
    elif plan["newline"]:
        with open(usage, "ab") as fh:
            fh.write(b"\n")


def _truncate_logs(work: Path, ckpt: dict[str, Any]) -> None:
    """Cuts every log back to its checkpointed length (a derived cut of a work dir, see ceilings: its logs end at the
    checkpoint). A resume does not use it: its usage rows are charged, not cut (see _apply_resume_logs)."""
    for name, key in (*RESUME_LOG_KEYS, ("usage_calls.jsonl", "usage_bytes")):
        path, nbytes = work / name, int(ckpt.get(key, 0))
        size = path.stat().st_size if path.exists() else 0
        if size < nbytes:
            raise ValueError(f"cannot cut {name}: it is shorter than the checkpoint says ({size} < {nbytes} bytes)")
        if path.exists():
            os.truncate(path, nbytes)


def _restore_phase1(work: Path, state: dict[str, Any]) -> tuple[_Units, dict[str, int], array, bytearray]:
    """The bindings phase's compact state (market units, class ids, negation), replayed from the decided log."""
    units = _Units()
    cls_names: dict[str, int] = {}
    cls_id, neg = array("i"), bytearray()
    for row in _jsonl_prefix(work / "decided.jsonl", state["decided_bytes"]):
        units.add(market_key(row["id"], len(units.unit_of)))
        bound = row["status"] == "bound"
        cls_id.append(cls_names.setdefault(row["class"], len(cls_names)) if bound else -1)
        neg.append(bool(row["negated"]))
    return units, cls_names, cls_id, neg


def _restore_classes(work: Path, state: dict[str, Any], names: list[str], cls_id: array, neg: bytearray) -> _ClassParity:
    """The class parity state, replayed from the accepted relation rows in log order (a rejected row changed nothing)."""
    classes = _ClassParity()
    for row in _jsonl_prefix(work / "relations.jsonl", state["relations_bytes"]):
        i, j, rel = row[0], row[1], row[2]
        bound = cls_id[i] >= 0 and cls_id[j] >= 0
        if rel == "EQUIV" and bound:
            classes.equiv(names[cls_id[i]], names[cls_id[j]], neg[i] != neg[j])
        elif rel in _ONE_SIDED and bound:
            classes.one_sided(names[cls_id[i]], bool(neg[i]), names[cls_id[j]], bool(neg[j]), rel)
    return classes


STALL_TRACE_EVERY = 900  # seconds without progress before a build writes the stacks of its threads to stall_trace.txt
_last_beat = [0.0]  # monotonic time of the last progress of the running build (see _heartbeat)
_stall: dict[str, Any] = {"file": None, "stop": None, "thread": None}


def _heartbeat() -> None:
    """Progress of the running build: its decision loops, checkpoints and repair passes call this. The stall watch writes a
    trace only when no heartbeat came for STALL_TRACE_EVERY seconds, so a long repair that makes progress leaves nothing."""
    _last_beat[0] = time.monotonic()


def _arm_stall_watch(work: Path) -> None:
    """Starts the stall watch for the build in `work`: a thread that, when STALL_TRACE_EVERY seconds pass without a
    heartbeat, writes the stacks of all threads to <work>/stall_trace.txt (again every STALL_TRACE_EVERY seconds while it
    stalls). Stopped by _disarm_stall when the build returns or raises."""
    _disarm_stall()
    fh = open(work / "stall_trace.txt", "w", encoding="utf-8")
    stop = threading.Event()
    _heartbeat()

    def watch() -> None:
        last_dump = None
        while not stop.wait(max(0.01, min(1.0, STALL_TRACE_EVERY / 4))):
            now = time.monotonic()
            quiet = now - _last_beat[0]
            if quiet >= STALL_TRACE_EVERY and (last_dump is None or now - last_dump >= STALL_TRACE_EVERY):
                faulthandler.dump_traceback(file=fh, all_threads=True)
                last_dump = now

    thread = threading.Thread(target=watch, name="stall-watch", daemon=True)
    thread.start()
    _stall.update(file=fh, stop=stop, thread=thread)


def _disarm_stall() -> None:
    """Stops the stall watch and closes its file (an open file cannot be removed on Windows)."""
    if _stall["stop"] is not None:
        _stall["stop"].set()
        _stall["thread"].join(timeout=5)
    if _stall["file"] is not None:
        _stall["file"].close()
    _stall.update(file=None, stop=None, thread=None)


def _watched(build_fn: Any) -> Any:
    """Disarms the stall watch when a build returns or raises."""

    @functools.wraps(build_fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return build_fn(*args, **kwargs)
        finally:
            _disarm_stall()

    return wrapper


def check_max_calls(max_calls: int, outcomes: int) -> None:
    """Refuses a fresh build's --max-calls below its outcome count: each outcome is one binding call, and a cap cannot cut
    them, so the cap must cover them. Called before any model call (by build, and by the CLI before the adapter starts)."""
    if max_calls < outcomes:
        raise ValueError(f"max_calls {max_calls} is below the {outcomes} binding calls this build makes (one per outcome); "
                         f"raise it to at least {outcomes}")


@_watched
def build(
    records: Iterable[dict[str, Any]],
    adapter: Any,
    out_dir: str | Path,
    profile: dict[str, Any],
    *,
    checkpoint_every: int = CHECKPOINT_EVERY,
    max_retries: int = 2,
    max_df: int = MAX_DF,
    gold_pairs: Iterable[tuple[str, str]] | None = None,
    gold_groups: list[dict[str, Any]] | None = None,
    max_consecutive_errors: int = MAX_CONSECUTIVE_ERRORS,
    max_relation_drops: int = MAX_RELATION_DROPS,
    use_groups: bool = True,
    group_sources: Iterable[str] = DEFAULT_GROUP_SOURCES,
    group_max_members: int = GROUP_MAX_MEMBERS,
    group_budget_frac: float = GROUP_BUDGET_FRAC,
    max_group_calls: int = MAX_GROUP_CALLS,
    batch_size: int = BATCH_SIZE,
    top_k: int = TOP_K,
    complement_dedup: bool = True,
    complement_fallback: str = "all",
    pair_order: str = "index",
    max_pairs: int | None = None,
    max_calls: int | None = None,
    outcomes: int | None = None,
    max_relation_drop_frac: float = MAX_RELATION_DROP_FRAC,
    resume: bool = False,
    max_group_drops: int = MAX_GROUP_DROPS_DEFAULT,
    max_group_drop_frac: float = MAX_GROUP_DROP_FRAC,
    keep_work: bool = False,
    admit: bool = True,
    admit_max_literals: int = ADMIT_MAX_LITERALS,
    relation_floor: float | None = None,
    repair_budget_s: float | None = None,
    candidates: str | Path | None = None,
) -> dict[str, Any]:
    """Build a submission directory. records: an iterable of dicts with id, question, label, event_title (and context, optional)
    (consumed once, so it may be a generator over a database cursor).
    batch_size, top_k, complement_dedup, complement_fallback, pair_order, max_pairs and max_calls are the cost controls
    (see the module docstring).
    gold_pairs / gold_groups: optional gold relations and groups (gold_pairs_from_theory, gold_groups_from_theory),
    used only for metrics: the summary then reports blocking_recall and group_recall, the shares of them that are
    candidates of this run. Nothing from them reaches an adapter.
    use_groups: ask group decisions when the adapter supports them (decide_group and supports_groups). group_sources
    and group_max_members choose the candidate sets; an adapter's advertised max_members caps group_max_members.
    group_budget_frac and max_group_calls bound the group phase alone (see the module docstring).
    Raises AdapterFatal when the adapter fails repeatedly before any checkpoint (nothing is written). A failure
    later in the group or relation phase seals nothing: the latest checkpoint is kept and the summary has
    adapter_fatal true. Refuses a non-empty out_dir before any model call (builds are write-once).
    keep_work: keep the work dir after a successful build, with HISTORY_FILE holding every checkpoint, so that
    mentat_bench.ceilings can derive lower-ceiling cells from it (build --keep-work).
    outcomes: the number of records (the binding calls, one per outcome). A fresh build with max_calls needs it, and refuses
    a cap below it (ValueError) before any call; records is consumed once, so the caller counts it.
    candidates: a curated candidates.jsonl (load_candidates). Its relation questions replace blocking (asked in file order,
    each as (a, b) as written), its group questions are the only group candidates (source "file", or "reading" for a reading
    row), and complement dedup is off; the bindings are unchanged. Every build logs each question it asked and the raw answer to
    <work>/decisions.jsonl (qid null without candidates)."""
    _check_admit_knobs(admit, admit_max_literals, relation_floor, repair_budget_s)
    checkpoint_every = _positive_int("checkpoint_every", checkpoint_every)
    max_df = _positive_int("max_df", max_df)
    max_consecutive_errors = _positive_int("max_consecutive_errors", max_consecutive_errors)
    max_relation_drops = _positive_int("max_relation_drops", max_relation_drops)
    max_group_drops = _positive_int("max_group_drops", max_group_drops)
    group_max_members = _positive_int("group_max_members", group_max_members)
    max_group_calls = _positive_int("max_group_calls", max_group_calls)
    batch_size = _positive_int("batch_size", batch_size)
    top_k = _positive_int("top_k", top_k)
    if complement_fallback not in COMPLEMENT_FALLBACKS:
        raise ValueError(f"complement_fallback must be one of {COMPLEMENT_FALLBACKS}, got {complement_fallback!r}")
    if pair_order not in PAIR_ORDERS:
        raise ValueError(f"pair_order must be one of {PAIR_ORDERS}, got {pair_order!r}")
    if max_pairs is not None:
        max_pairs = _positive_int("max_pairs", max_pairs)
    if max_calls is not None:
        max_calls = _positive_int("max_calls", max_calls)
        if not resume:  # a fresh build makes one binding call per outcome, and the cap cannot cut them: it must cover them
            if outcomes is None:
                raise ValueError("max_calls needs outcomes, the number of outcome records (one binding call each)")
            check_max_calls(max_calls, outcomes)
    if group_max_members > GROUP_MAX_MEMBERS_LIMIT:
        raise ValueError(f"group_max_members must be at most {GROUP_MAX_MEMBERS_LIMIT}, got {group_max_members}")
    if isinstance(group_budget_frac, bool) or not isinstance(group_budget_frac, (int, float)) or not 0 < group_budget_frac <= 1:
        raise ValueError(f"group_budget_frac must be in (0, 1], got {group_budget_frac!r}")
    group_sources = tuple(group_sources)
    if not set(group_sources) <= set(GROUP_SOURCES):
        raise ValueError(f"unknown group sources {sorted(set(group_sources) - set(GROUP_SOURCES))}")
    cand: dict[str, Any] | None = None
    cand_sha = None
    if candidates is not None:  # gold-informed questions: no blocking, the file's groups only, no complement dedup
        if hasattr(adapter, "extra_theory"):
            raise ValueError("candidates do not apply to a full-replay adapter (it asks no question)")
        records = list(records)  # shortcut: the corpus is held in memory to check the ids first (curated suites are small)
        questions, cand_sha = load_candidates(candidates, {r.get("id") for r in records if isinstance(r, dict)})
        cand = {op: [q for q in questions if q["op"] == op] for op in CANDIDATE_KEYS}
        cand["qid_of"] = {q["id"]: q["qid"] for q in cand["decide_binding"]}
        group_sources, complement_dedup = ("file",), False
        # every curated group is asked: no group share of the budget, and calls for all of them (the deadline still applies)
        group_budget_frac, max_group_calls = 1.0, max(max_group_calls, len(cand["decide_group"]))
    if hasattr(adapter, "extra_theory") and not getattr(adapter, "supplies_gold_refs", False):
        # a replayed theory carries its own provenance, which may name gold-only REFs: gold-backed adapters only
        raise ValueError(f"{getattr(adapter, 'name', adapter)!r} is not gold-backed and may not replay a full theory")
    out_dir = Path(out_dir)
    if out_dir.exists() and any(out_dir.iterdir()):
        raise FileExistsError(f"{out_dir} is not empty; builds are write-once, choose a new output directory")
    work = out_dir.with_name(out_dir.name + ".build")
    if work.exists() and any(work.iterdir()) and not resume:
        raise FileExistsError(f"{work} is a leftover build; build --resume continues it, or remove it")
    gw = Gateway(  # the gateway's deadline is the model-call stop: finalize keeps the reserve (FINALIZE_RESERVE_FRAC)
        adapter,
        spend_ceiling_usd=profile["spend_ceiling_usd"],
        build_deadline_s=float(profile["build_deadline_s"]) * (1 - FINALIZE_RESERVE_FRAC),
        max_retries=max_retries,
        profile_deadline_s=float(profile["build_deadline_s"]),
    )
    health = _AdapterHealth(max_consecutive_errors)
    full_replay = hasattr(adapter, "extra_theory")
    gold_backed = bool(getattr(adapter, "supplies_gold_refs", False))  # only oracle and gold_view may write gold-only REFs
    advertised = getattr(adapter, "advertised_group_max_members", None)
    effective_max = group_max_members if advertised is None else min(group_max_members, int(advertised))
    group_ok = (
        bool(use_groups) and not full_replay and callable(getattr(adapter, "decide_group", None))
        and bool(getattr(adapter, "supports_groups", True))
    )
    max_interps = int(profile.get("max_interpretations", 8))
    if cand is not None and group_ok:
        wide = next((q for q in cand["decide_group"] if len(q["members"]) > effective_max), None)
        if wide is not None:
            raise ValueError(f"candidate group {wide['qid']} has {len(wide['members'])} members, above the "
                             f"{effective_max} this build's group decisions may name")
    groups_off = None if group_ok else (
        "flag" if not use_groups else "full_replay" if full_replay
        else getattr(adapter, "groups_disabled_reason", None) or "adapter_no_groups")
    requested = {"adapter": str(getattr(adapter, "name", type(adapter).__name__)), "gold_backed": gold_backed,
                 "groups": group_ok, "group_sources": list(group_sources),
                 "group_max_members": effective_max, "group_budget_frac": float(group_budget_frac),
                 "max_group_calls": max_group_calls, "max_df": max_df,
                 "max_relation_drops": max_relation_drops, "max_relation_drop_frac": float(max_relation_drop_frac),
                 "max_group_drops": max_group_drops, "max_group_drop_frac": float(max_group_drop_frac), "top_k": top_k,
                 "admit": admit, "admit_max_literals": admit_max_literals, "relation_floor": relation_floor,
                 "repair_budget_s": repair_budget_s,
                 "complement_dedup": bool(complement_dedup) and not full_replay,
                 "complement_fallback": complement_fallback, "pair_order": pair_order,
                 "batch_size": batch_size if getattr(adapter, "supports_batch", False) else 1,
                 "groups_disabled_reason": groups_off,
                 "model": getattr(adapter, "hello_model", None),
                 "max_pairs": max_pairs, "max_calls": max_calls, "candidates_sha256": cand_sha,
                 "candidates_gold_informed": cand is not None, "profile": dict(profile)}
    if resume:
        ckpt = _read_resume_checkpoint(work)
        _check_resume_policy(ckpt.get("policy") or {}, requested, adapter)
        old_profile = (ckpt.get("policy") or {}).get("profile")  # missing (a checkpoint from before it was recorded): accepted
        if old_profile is not None:
            _check_resume_profile(old_profile, profile)
        policy_max = (ckpt.get("policy") or {}).get("max_calls")
        if policy_max is not None:  # a checkpoint from before the cap was enforced may hold fewer items than bindings
            bindings = sum(1 for _ in _jsonl_prefix(work / "decided.jsonl", int(ckpt["decided_bytes"])))
            if policy_max < bindings:
                raise ValueError(f"cannot resume: the checkpoint was built with max_calls {policy_max}, below its {bindings} "
                                 "binding calls (a build from before the cap was enforced); start a new build")
        corpus, cards = _corpus_digest(records)  # the incoming records: a different corpus would be stamped on the decided outcomes
        old_corpus = (ckpt.get("policy") or {}).get("corpus")  # missing (a checkpoint from before it was recorded): accepted
        if old_corpus is not None and old_corpus != corpus:
            raise ValueError("cannot resume: the corpus differs from the one the checkpoint was built on (its outcome records "
                             "differ); resume with the same corpus, or start a new build")
        if old_corpus is None and cards:  # without a recorded digest, a rule card the checkpoint never saw cannot be ruled out
            raise ValueError("cannot resume: the checkpoint predates the corpus record and the corpus has context rows; "
                             "start a new build")
        plan = _plan_resume_logs(work, ckpt, requested["adapter"] or "adapter", int(requested["batch_size"]))
        # every check above and the usage log passed: nothing is changed before this point
    work.mkdir(parents=True, exist_ok=True)
    _arm_stall_watch(work)
    store = _RecordStore.reopen(work / "records.jsonl") if resume else _RecordStore(work / "records.jsonl")
    mode = "a" if resume else "w"
    decided_fh = open(work / "decided.jsonl", mode, encoding="utf-8", newline="\n")
    relations_fh = open(work / "relations.jsonl", mode, encoding="utf-8", newline="\n")
    groups_fh = open(work / "groups.jsonl", mode, encoding="utf-8", newline="\n")
    usage_fh = open(work / "usage_calls.jsonl", mode, encoding="utf-8", newline="\n")
    decisions_fh = open(work / "decisions.jsonl", mode, encoding="utf-8", newline="\n")
    prior = {"calls": 0, "items": 0, "usd": 0.0}  # what an earlier run of this build already spent
    resume_phase: str | None = None
    if resume:
        _apply_resume_logs(work, ckpt, plan)
        prior.update(calls=plan["calls"], items=plan["items"], usd=plan["usd"])
        resume_phase = ckpt.get("phase") or ("relations" if ckpt.get("pairs_total") else "bindings")
        state: dict[str, Any] = ckpt
        if "decisions_bytes" not in ckpt:  # a checkpoint from before decisions.jsonl: the log holds only what is asked now
            state["decisions_complete"] = False
        state["resumes"] = ckpt.get("resumes", 0) + 1
        state["prior_calls"] = prior["calls"]
        if plan["torn"]:  # the placeholder rows of torn final rows, counted from the log (an earlier resume's too)
            state["torn_usage_rows"] = plan["torn"]
        else:
            state.pop("torn_usage_rows", None)
        gw.spend_usd = prior["usd"]  # the spend ceiling and the group budget count what was spent before
        offset_s = float(ckpt.get("wall_seconds", 0.0)) + plan["discarded_seconds"]  # the discarded calls' seconds too
        gw.elapsed_offset_s = offset_s  # and so does the deadline
        if state.get("policy") is not None:  # the profile and corpus this resume runs under: what the next resume checks against
            state["policy"] = {**state["policy"], "profile": dict(profile), "corpus": corpus}
        state["finalize_reserve_s"] = round(FINALIZE_RESERVE_FRAC * float(profile["build_deadline_s"]), 3)
        # a budget stop is cleared only when the larger limits leave headroom; a checkpoint without stopped_in (written
        # before it) stays final, since its stopped phase is unknown
        headroom = (prior["usd"] < float(profile["spend_ceiling_usd"])
                    and offset_s < (1 - FINALIZE_RESERVE_FRAC) * float(profile["build_deadline_s"]))
        if state.get("budget_stopped") and "stopped_in" in state and headroom:  # decreased limits were refused above
            state["budget_stopped"] = False
            stopped_in = state.pop("stopped_in")
            extension = {"old": {key: (old_profile or {}).get(key) for key in RESUME_LIMIT_KEYS},
                         "new": {key: profile.get(key) for key in RESUME_LIMIT_KEYS}, "stopped_in": stopped_in}
            state["budget_extended"] = [*state.get("budget_extended", []), extension]  # one entry per cleared stop
            resume_phase = RESUME_STOP_PHASE.get(stopped_in, resume_phase)
    else:
        state = {"decided_bytes": 0, "relations_bytes": 0, "groups_bytes": 0, "usage_bytes": 0, "decisions_bytes": 0,
                 "pairs_total": 0, "relations_committed": 0, "equiv_rejected": 0, "budget_stopped": False,
                 "adapter_fatal": False, "constraints": 0, "relation_rejected": 0,
                 "blocking_recall": None, "gold_pairs": None,
                 "group_candidates": 0, "groups_committed": 0, "groups_rejected": 0, "group_errors": 0,
                 "group_scope_rejected": 0, "group_recall": None, "gold_groups": None, "axes": {}, "gold_refs_rejected": 0,
                 "pairs_skipped_by_groups": 0, "groups_fatal": False, "group_budget_stopped": False,
                 "pairs_derived": 0, "pairs_derived_none": 0, "pairs_capped": False, "calls_capped": False,
                 "dedup_representatives": 0, "dedup_named": 0, "dedup_undecided": 0, "dedup_none": 0,
                 "dedup_fallback_asked": 0, "dedup_fallback_skipped": 0, "pairs_asked_cap": max_pairs, "pairs_considered": 0,
                 "groups_disabled_reason": groups_off, "phase": None, "policy": requested,
                 "finalize_reserve_s": round(FINALIZE_RESERVE_FRAC * float(profile["build_deadline_s"]), 3)}
    pending = {"calls": 0}
    gw.items_left = None if max_calls is None else max_calls - prior["items"]  # the --max-calls budget left (retries included)
    # timing only (the summary): the gateway seconds of each phase, and the seconds of the calls made in it
    state.setdefault("phase_seconds", {})
    state.setdefault("phase_call_seconds", {})
    state.setdefault("dedup_fallback_skipped", 0)  # a checkpoint from before this counter
    state.setdefault("pairs_ahead", 0)
    phase_clock = {"name": None if resume_phase else "bindings", "t": gw.elapsed_s()}

    def mark(name: str | None) -> None:
        """Closes the timed phase (its seconds add up over resumes) and times the phase `name` from now (None: none)."""
        now = gw.elapsed_s()
        if phase_clock["name"] is not None:
            seconds = state["phase_seconds"]
            seconds[phase_clock["name"]] = round(seconds.get(phase_clock["name"], 0.0) + now - phase_clock["t"], 3)
        phase_clock.update(name=name, t=now)

    def write_call(call: dict[str, Any]) -> None:
        """One charged call, written and flushed at its commit (the gateway's sink): a kill in a retry loop keeps the paid
        rows written before it."""
        usage_fh.write(ir.dumps(call) + "\n")
        usage_fh.flush()
        pending["calls"] += 1
        if phase_clock["name"] is not None:
            calls = state["phase_call_seconds"]
            calls[phase_clock["name"]] = round(calls.get(phase_clock["name"], 0.0) + call.get("seconds", 0.0), 3)

    gw.sink = write_call

    def drain() -> None:
        """Heartbeat, and an fsync of the usage log every USAGE_FLUSH_EVERY calls (the calls are written at their commit)."""
        _heartbeat()
        if pending["calls"] >= USAGE_FLUSH_EVERY:
            _append(usage_fh, [])
            pending["calls"] = 0

    def checkpoint() -> None:
        """Flush every log, then publish the new valid lengths atomically."""
        _heartbeat()
        drain()
        state["usage_bytes"] = _append(usage_fh, [])
        pending["calls"] = 0
        state["wall_seconds"] = round(gw.elapsed_s(), 3)
        state["peak_rss_mb"] = round(peak_rss_mb(), 3)
        _atomic_write(work / "checkpoint.json", json.dumps(state, sort_keys=True))
        if keep_work:  # after the publish: a history line is always a checkpoint the logs were valid for
            with open(work / HISTORY_FILE, "a", encoding="utf-8", newline="\n") as fh:
                _append(fh, [json.dumps(state, sort_keys=True)])

    def abandon() -> None:
        """A bindings-phase failure. The work dir is removed only when its usage log holds no paid call. Otherwise it is kept
        (its paid calls are on disk, and there is no checkpoint to resume): its path and count are reported on stderr."""
        _disarm_stall()
        for fh in (decided_fh, relations_fh, groups_fh, usage_fh, decisions_fh):
            fh.close()
        store.close()
        paid = paid_calls(work)
        if paid:
            print(f"build stopped in the bindings phase after {paid} paid calls: the work dir {work} is kept. There is no "
                  "checkpoint to resume: move the work dir aside, then rebuild into a new output directory", file=sys.stderr)
        else:
            shutil.rmtree(work, ignore_errors=True)

    # Phase 1: bindings. Compact per-record state: market unit, class id (-1 = not bound) and negation.
    seen: set[str] = set()
    bind_batch = _batch_size(adapter, "decide_binding", batch_size)
    waiting: list[dict[str, Any]] = []  # records read, not yet decided
    settled = {"bindings": 0}  # bindings decided so far

    def settle_bindings() -> None:
        if not waiting:
            return
        if max_calls is not None:  # the bindings not yet asked, after this batch: a binding retry leaves them their items
            gw.reserve = max(0, outcomes - settled["bindings"] - len(waiting))
        raws = _decide_many(gw, "decide_binding", [(_context(record),) for record in waiting], bind_batch > 1)
        drain()
        _book_errors(raws, health)
        for record, raw in zip(waiting, raws):
            row = {**_settle_binding(raw, record, health), "event_title": record.get("event_title")}
            if not gold_backed:
                state["gold_refs_rejected"] = state.get("gold_refs_rejected", 0) + _strip_gold_refs(row)
            decided_fh.write(ir.dumps(row) + "\n")
            qid = cand["qid_of"].get(record["id"]) if cand is not None else None
            logged = raw
            if not gold_backed and isinstance(raw, dict) and "evidence" in raw:  # the log keeps no gold-only REF either
                logged = dict(raw)
                if isinstance(raw["evidence"], list) and all(isinstance(ref, str) for ref in raw["evidence"]):
                    _strip_gold_refs(logged)
                else:
                    del logged["evidence"]
            decisions_fh.write(_decision_line("decide_binding", qid, logged, oid=record["id"]) + "\n")
            bound = row["status"] == "bound"
            cls_id.append(cls_names.setdefault(row["class"], len(cls_names)) if bound else -1)
            neg.append(bool(row["negated"]))
        settled["bindings"] += len(waiting)
        waiting.clear()

    units, cls_names, cls_id, neg = (_restore_phase1(work, state) if resume_phase else (_Units(), {}, array("i"), bytearray()))
    if resume_phase is None:  # a resumed build has its bindings already
        corpus_hash = hashlib.sha256()  # the corpus identity a resume checks (see _corpus_digest)
        context_rows = 0
        try:
            for record in records:
                if not isinstance(record, dict):
                    raise ValueError("an outcome record is not an object")
                corpus_hash.update((ir.dumps(record) + "\n").encode("utf-8"))
                oid = ir.validate_outcome_id(record.get("id"))
                if not isinstance(record.get("question"), str):  # the adapters' context needs it (see _context)
                    raise ValueError(f"outcome {oid} has no question text")
                if record.get("context") is not None:  # the rule card: checked here, while streaming, like the question
                    if not isinstance(record["context"], str):
                        raise ValueError(f"outcome {oid} has a context that is not a string")
                    if len(record["context"]) > CONTEXT_MAX_CHARS:
                        raise ValueError(f"outcome {oid} has a context of {len(record['context'])} characters, above the "
                                         f"{CONTEXT_MAX_CHARS} allowed")
                    context_rows += 1
                if oid in seen:
                    raise ValueError(f"duplicate outcome id {oid}")
                seen.add(oid)
                units.add(market_key(oid, len(units.unit_of)))
                store.append(record)
                waiting.append(record)
                if len(waiting) >= bind_batch:
                    settle_bindings()
            settle_bindings()
        except BudgetExceeded as exc:
            abandon()
            raise BuildIncomplete(f"bindings phase stopped by budget: {exc}") from exc
        except BaseException:  # any failure before the first checkpoint: nothing to resume, the work dir is removed
            abandon()
            raise
        finally:
            gw.reserve = 0  # the bindings are asked (or abandoned): the later retries may use the rest of the cap
        if max_calls is not None and settled["bindings"] != outcomes:  # --max-calls was checked against another count
            for fh in (decided_fh, relations_fh, groups_fh, usage_fh, decisions_fh):  # the work dir and its usage log stay as they are
                fh.close()
            store.close()
            raise BindingCountMismatch(f"max_calls was checked against {outcomes} outcomes, but the bindings read "
                                       f"{settled['bindings']}; the usage log and work dir are kept")
        requested["corpus"] = corpus_hash.hexdigest()  # state["policy"] is this dict
        if context_rows:  # absent when the corpus has no rule card, so the policy of a build without one keeps its bytes
            requested["context_rows"] = context_rows
        seen.clear()
        state["decided_bytes"] = _append(decided_fh, [])
        state["decisions_bytes"] = _append(decisions_fh, [])
        state["phase"] = "bindings"
        checkpoint()  # first valid checkpoint: all bindings, no relations
    mark("blocking")

    # Phase 2: blocking over markets. Deadline-checked; a stop keeps the pairs found so far.
    pairs_path = work / "pairs.bin"
    if not full_replay and not _past(resume_phase, "blocking"):
        def blocking_stop() -> bool:
            if gw.elapsed_s() >= gw.build_deadline_s:
                state["budget_stopped"] = True
            return state["budget_stopped"]

        def unit_questions() -> Iterator[str]:
            for k, rec in enumerate(store):
                if units.is_first[k]:
                    yield rec["question"]

        gold: set[tuple[int, int]] | None = None
        if cand is not None:  # the curated relation questions, in file order, each (a, b) as written: no blocking
            row_of = {rec["id"]: i for i, rec in enumerate(store)}
            candidates = iter([(row_of[q["a"]], row_of[q["b"]]) for q in cand["decide_relation"]])
        else:
            if gold_pairs is not None:
                gold = _gold_record_pairs(store, gold_pairs)
                state["gold_pairs"] = len(gold)
            start, rows = _csr(units.unit_of, units.count)
            if pair_order == "score":  # the market pairs by blocking score, highest first; siblings stay first (not blocked)
                unit_pairs = _score_order(_scored_candidate_pairs(unit_questions, units.count, max_df=max_df, top_k=top_k,
                                                                  stop=blocking_stop, scored=True))
            else:
                unit_pairs = iter_candidate_pairs(unit_questions, units.count, max_df=max_df, top_k=top_k, stop=blocking_stop)
            candidates = chain(_sibling_pairs(start, rows, units.count), _record_pairs(unit_pairs, start, rows))
        found: set[tuple[int, int]] = set()
        buf = array("I")
        with open(pairs_path, "wb") as pf:
            for i, j in candidates:
                buf.extend((i, j))
                if gold is not None and (i, j) in gold:
                    found.add((i, j))
                if len(buf) >= 1 << 20:
                    buf.tofile(pf)
                    del buf[:]
            buf.tofile(pf)
        state["pairs_total"] = os.path.getsize(pairs_path) // 8
        state["phase"] = "blocking"
        if state["budget_stopped"]:  # pairs.bin is cut short: a resume that clears the stop runs blocking again
            state["stopped_in"] = "blocking"
        if gold:
            state["blocking_recall"] = round(len(found) / len(gold), 6)
        checkpoint()

    mark("groups")
    # Gold group recall (metrics only; no adapter is asked): the share of gold groups inside some candidate.
    position: dict[str, int] | None = None
    if gold_groups is not None or group_ok:
        position = {rec["id"]: i for i, rec in enumerate(store)}

    def candidate_groups(pairs: Iterable[tuple[int, int]]) -> Iterator[tuple[str, list[int]]]:
        """The group candidates: the file's group questions (members in file order), else group_candidates."""
        if cand is not None:
            return ((("reading" if q["source"] == "reading" else "file"), [position[m] for m in q["members"]])
                    for q in cand["decide_group"])
        return group_candidates(store, group_sources, effective_max, pairs=pairs)

    if gold_groups is not None and not _past(resume_phase, "groups"):
        recall = _GroupRecall(gold_groups, position)
        for _, members in candidate_groups(() if full_replay else _pair_stream(pairs_path)):
            recall.feed(members)
        state["gold_groups"] = len(gold_groups)
        state["group_recall"] = recall.value()

    # Phase 2b: groups, in batches of checkpoint_every candidate calls; each completed batch is a checkpoint. The
    # group phase has its own share of the budget (group_budget_frac of the spend ceiling and of the deadline left at
    # its start, and max_group_calls candidates): hitting it ends this phase only.
    if group_ok and not state["budget_stopped"] and not _past(resume_phase, "groups_done"):
        group_health = _AdapterHealth(max_consecutive_errors)
        state["phase"] = "groups"
        group_batch = min(_batch_size(adapter, "decide_group", batch_size), checkpoint_every)
        chunk: list[tuple[str, list[int], dict[str, Any], str | None]] = []  # candidates waiting for the next batch call
        batch_log: list[str] = []  # their decisions.jsonl lines, written at the commit
        committed_keys: set[tuple] = {_group_key(row) for row in _jsonl_prefix(work / "groups.jsonl", state["groups_bytes"])}
        axes_next = {axis: list(values) for axis, values in state["axes"].items()}
        batch_rows: list[dict[str, Any]] = []
        batch_keys: set[tuple] = set()
        batch_tally: Counter = Counter()
        batch_asked = 0
        # the share counts from the phase's first entry, kept in the checkpoint across resumes (a legacy checkpoint: this entry)
        for key, default in (("groups_bytes", 0), ("group_candidates", 0), ("groups_committed", 0), ("group_errors", 0),
                             ("groups_rejected", 0), ("group_scope_rejected", 0), ("groups_fatal", False),
                             ("group_budget_stopped", False), ("axes", {})):
            state.setdefault(key, default)  # a checkpoint from before the group phase starts the phase with these
        spend0 = state.setdefault("group_spend0", gw.spend_usd)
        t0 = state.setdefault("group_t0", gw.elapsed_s())
        # paid calls after the last committed group decision (a discarded batch, paid and asked again) are real spend: they
        # count toward the spend ceiling, but not toward this phase's share, which measures the decisions it committed
        state["discarded_spend"] = state.get("discarded_spend", 0.0) + gw.spend_usd - state.get("group_committed_spend", gw.spend_usd)
        state["group_committed_spend"] = gw.spend_usd
        checkpoint()  # the group phase's keys are persisted at its entry: a crash before its first commit resumes with them
        spend_cap = group_budget_frac * float(profile["spend_ceiling_usd"])
        time_end = t0 + group_budget_frac * max(0.0, gw.build_deadline_s - t0)
        state.setdefault("group_cursor", state["group_candidates"])  # the committed candidates (legacy: all counted)
        skip = state["group_cursor"]  # a resumed group phase asks the rest
        state["group_candidates"] = skip  # the discarded batch of a stop is counted again when it is asked
        group_calls = skip

        def commit_groups() -> None:
            nonlocal batch_asked
            lines = []
            for row in batch_rows:
                row["n"] = state["groups_committed"]
                state["groups_committed"] += 1
                lines.append(ir.dumps(row))
                committed_keys.add(_group_key(row))
            state["groups_bytes"] = _append(groups_fh, lines)
            state["decisions_bytes"] = _append(decisions_fh, batch_log)
            batch_log.clear()
            state["group_committed_spend"] = gw.spend_usd  # the spend up to the last committed decision
            for name, value in batch_tally.items():
                state[name] += value
            state["axes"] = {axis: list(values) for axis, values in axes_next.items()}
            state["group_cursor"] = state["group_candidates"]  # every candidate counted so far is decided and committed
            batch_rows.clear()
            batch_keys.clear()
            batch_tally.clear()
            batch_asked = 0
            checkpoint()

        def ask_chunk() -> None:
            """Asks the waiting candidates in one batch call; their decisions are admitted in candidate order."""
            raws = _decide_many(gw, "decide_group", [(ctx,) for _, _, ctx, _ in chunk], group_batch > 1)
            drain()
            _book_errors(raws, group_health)
            for (source, _, ctx, qid), raw in zip(chunk, raws):
                batch_log.append(_decision_line("decide_group", qid, raw, members=[m["id"] for m in ctx["members"]]))
                items, failed = _settle_group(raw, ctx, group_health, batch_tally)
                state["group_errors"] += int(failed)
                for item in items:
                    row = _admit(item, source, position, cls_id, neg, axes_next, max_interps,
                                 committed_keys, batch_keys, batch_tally, ctx["group_id"] if source == "reading" else None)
                    if row is not None:
                        batch_rows.append(row)
            chunk.clear()

        try:
            for index, (source, members) in enumerate(candidate_groups(_pair_stream(pairs_path))):
                if index < skip:
                    continue
                if (group_calls >= max_group_calls or gw.spend_usd - spend0 - state["discarded_spend"] >= spend_cap
                        or gw.elapsed_s() >= time_end):
                    state["group_budget_stopped"] = True
                    break
                if gw.items_left is not None and gw.items_left - len(chunk) <= 0:
                    state["calls_capped"] = True
                    break
                group_calls += 1
                batch_asked += 1
                state["group_candidates"] += 1  # counted at the call, so discarded batches still count
                ctx = {"group_id": f"c{state['group_candidates']:07d}", "source": source,
                       "members": [_context(store.get(i)) for i in members]}
                chunk.append((source, members, ctx, cand["decide_group"][index]["qid"] if cand is not None else None))
                if len(chunk) >= group_batch or batch_asked >= checkpoint_every:
                    ask_chunk()
                    if batch_asked >= checkpoint_every:
                        commit_groups()
            if chunk:
                ask_chunk()
            commit_groups()
        except BudgetExceeded:
            state["budget_stopped"] = True  # the global budget: the unfinished batch is discarded
            state["stopped_in"] = "groups"
        except AdapterFatal:
            # like adapter_fatal in the relation phase: the unfinished batch is discarded, nothing is sealed
            state["groups_fatal"] = True
            state["adapter_fatal"] = True

    mark("relations")
    state["phase"] = "groups_done"
    checkpoint()  # the group phase's end is persisted: a resume of a group-share stop does not re-enter the phase
    # Phase 3: relations in batches; each completed batch is a checkpoint. Every candidate pair is asked: groups do
    # not cover pairs (a group can be dropped or downgraded later, and its pairs may carry other relations).
    if state["pairs_total"] and not state["budget_stopped"] and not state["adapter_fatal"]:
        names = list(cls_names)
        classes = _restore_classes(work, state, names, cls_id, neg)
        rel_batch = _batch_size(adapter, "decide_relation", batch_size)
        mate = _complement_mates(units.unit_of, units.count, cls_id, neg) if complement_dedup and not full_replay else None
        if max_pairs is not None and state["pairs_total"] > max_pairs:
            state["pairs_capped"] = True
        # the cursor: pairs_considered less the derived pairs counted past it (pairs_ahead; 0 in a build before that key)
        processed = int(state["pairs_considered"]) - int(state.get("pairs_ahead", 0))
        stop = False  # stop: the max_pairs or max_calls cap has been reached
        state["phase"] = "relations"

        def ask_pairs(pairs: list[tuple[int, int]],
                      log: list[tuple[str, str, Any]]) -> tuple[list[tuple[str, float | None]], bool]:
            """Relations of record pairs, in calls of up to rel_batch and within max_calls. Returns the (relation, conf) of
            the pairs asked, in order, and whether max_calls stopped the asking. `log` gets (a id, b id, raw) of each."""
            answers: list[tuple[str, float | None]] = []
            s = 0
            while s < len(pairs):
                size = rel_batch if gw.items_left is None else min(rel_batch, gw.items_left)
                if size <= 0:
                    return answers, True
                part = pairs[s:s + size]
                s += len(part)
                ctxs = [(_context(store.get(x)), _context(store.get(y))) for x, y in part]
                raws = _decide_many(gw, "decide_relation", ctxs, rel_batch > 1)
                drain()
                _book_errors(raws, health)
                answers.extend(_settle_relation(raw, health) for raw in raws)
                log.extend((a["id"], b["id"], raw) for (a, b), raw in zip(ctxs, raws))
            return answers, False

        try:
            with open(pairs_path, "rb") as pf:
                pf.seek(8 * processed)  # the pairs before the cursor are decided (8 bytes a pair)
                while not stop:
                    ids = array("I")
                    try:
                        ids.fromfile(pf, 2 * checkpoint_every)
                    except EOFError:
                        pass  # short final batch
                    if not ids:
                        break
                    cands = [(ids[k], ids[k + 1]) for k in range(0, len(ids), 2)]
                    roles = [_dedup_role(i, j, mate, units.unit_of) for i, j in cands]
                    limit = len(cands) if max_pairs is None else max(0, min(len(cands), max_pairs - processed))
                    keep, budget_cut = limit, False
                    if max_calls is not None:  # whole units only: a representative's fallbacks are reserved with it
                        keep, budget_cut = _whole_units(roles[:limit], len(_FALLBACK_PAIRS[complement_fallback]), gw.items_left)
                    if keep < len(cands) or (max_pairs is not None and processed + keep >= max_pairs):
                        stop = True  # max_pairs or max_calls ends the relation phase after this batch
                    if budget_cut:
                        state["calls_capped"] = True
                    cands, roles = cands[:keep], roles[:keep]
                    asks = [(i, j, role == "representative") for (i, j), role in zip(cands, roles) if role != "derived"]
                    rel_log: list[tuple[str, str, Any]] = []  # (a id, b id, raw) per pair asked, then per fallback asked
                    fallback_log: list[tuple[str, str, Any]] = []
                    rels, _ = ask_pairs([(i, j) for i, j, _ in asks], rel_log)  # rels: (relation, conf) per pair asked
                    answered = asks[: len(rels)]
                    # M1: a representative's complement pairs are derived only from a named answer; otherwise they are asked
                    # (--complement-fallback: all three, the both-flipped one, or none; the others are skipped and counted)
                    fallback: list[tuple[int, int]] = []
                    for (i, j, rep), (rel, _) in zip(answered, rels):
                        if rep and rel not in DERIVING:
                            pairs = _complement_pairs(i, j, mate)
                            fallback.extend(pairs[k] for k in _FALLBACK_PAIRS[complement_fallback])
                    got, _ = ask_pairs(fallback, fallback_log)  # a retry can take calls the fallbacks were reserved with
                    # --max-calls keeps the batch's complete units; the first incomplete unit and what follows are not
                    # committed (their paid answers stay in usage.json), and the phase stops
                    cut, n_asks, n_fallbacks = _complete_prefix(roles, rels, len(got), len(_FALLBACK_PAIRS[complement_fallback]))
                    if cut < len(cands):
                        state["calls_capped"] = True
                        stop = True
                    cands, roles = cands[:cut], roles[:cut]
                    answered, rels = answered[:n_asks], rels[:n_asks]
                    fallback, got = fallback[:n_fallbacks], got[:n_fallbacks]
                    ahead = _pairs_ahead(roles, int(state.get("pairs_ahead", 0)))  # counted derived pairs past this batch
                    skipped = sum(len(_complement_pairs(i, j, mate)) - len(_FALLBACK_PAIRS[complement_fallback])
                                  for (i, j, rep), (rel, _) in zip(answered, rels) if rep and rel not in DERIVING)
                    fallback_rel = dict(zip(fallback, got))
                    # (x, y, relation, representative position, kind, conf): a derived row carries its representative's conf
                    decided: list[tuple[int, int, str, int, str, float | None]] = []
                    reps = named = undecided = none = derived_named = derived_none = 0
                    for (i, j, rep), (rel, conf) in zip(answered, rels):
                        if not rep:
                            decided.append((i, j, rel, -1, "plain", conf))
                            continue
                        pos = len(decided)
                        decided.append((i, j, rel, -1, "rep", conf))
                        reps += 1
                        if rel in DERIVING:
                            named += 1
                            for x, y, r in _complement_rows(i, j, rel, mate):
                                decided.append((x, y, r, pos, "derived", conf))
                                derived_named += r in DERIVING
                                derived_none += r not in DERIVING
                        else:
                            undecided += rel == "UNRESOLVED"
                            none += rel == "NONE"
                            for x, y in _complement_pairs(i, j, mate):
                                if (x, y) in fallback_rel:
                                    frel, fconf = fallback_rel[(x, y)]
                                    decided.append((x, y, frel, -1, "fallback", fconf))
                    base = state["constraints"]
                    lines, rep_line = [], {}
                    rejected = contradicted = 0
                    for pos, (i, j, rel, rep, kind, conf) in enumerate(decided):
                        if kind == "derived" and rep not in rep_line:  # its representative was rejected or has no line
                            continue
                        bound = cls_id[i] >= 0 and cls_id[j] >= 0
                        if rel == "EQUIV" and bound:
                            if not classes.equiv(names[cls_id[i]], names[cls_id[j]], neg[i] != neg[j]):
                                rejected += 1
                                continue
                        elif rel in _ONE_SIDED and bound:
                            if not classes.one_sided(names[cls_id[i]], bool(neg[i]), names[cls_id[j]], bool(neg[j]), rel):
                                contradicted += 1
                                continue
                        if _relation_expr(rel, "a", "b") is None:
                            continue
                        if kind == "rep":
                            rep_line[pos] = base + len(lines)
                        lines.append(_relation_row(i, j, rel, rep_line[rep] if kind == "derived" else -1, conf))
                    state["relations_bytes"] = _append(relations_fh, lines)
                    # the committed questions as asked; with candidates every pair is plain, so ask n is pair processed + n
                    qids = [q["qid"] for q in cand["decide_relation"][processed:processed + n_asks]] if cand is not None else []
                    log_lines = [_decision_line("decide_relation", qids[n] if qids else None, raw, a=a, b=b)
                                 for n, (a, b, raw) in enumerate(rel_log[:n_asks])]
                    log_lines += [_decision_line("decide_relation", None, raw, a=a, b=b)
                                  for a, b, raw in fallback_log[:n_fallbacks]]
                    state["decisions_bytes"] = _append(decisions_fh, log_lines)
                    state["constraints"] += len(lines)
                    state["relations_committed"] += len(rels) + len(got)
                    state["pairs_derived"] += derived_named
                    state["pairs_derived_none"] += derived_none
                    state["dedup_representatives"] += reps
                    state["dedup_named"] += named
                    state["dedup_undecided"] += undecided
                    state["dedup_none"] += none
                    state["dedup_fallback_asked"] += len(got)
                    state["dedup_fallback_skipped"] += skipped
                    state["equiv_rejected"] += rejected
                    state["relation_rejected"] += contradicted
                    # the cursor moves with the rows: a discarded batch leaves it (and what it counted) where it was
                    processed += cut
                    state["pairs_considered"] = processed + ahead
                    state["pairs_ahead"] = ahead
                    checkpoint()
        except BudgetExceeded:
            state["budget_stopped"] = True  # the unfinished batch is discarded
            state["stopped_in"] = "relations"
        except AdapterFatal:
            state["adapter_fatal"] = True  # the unfinished batch is discarded; the latest checkpoint is sealed
    mark(None)
    checkpoint()
    for fh in (decided_fh, relations_fh, groups_fh, usage_fh, decisions_fh):
        fh.close()
    store.close()
    summary = finalize_checkpoint(
        work, out_dir, profile, adapter.extra_theory() if full_replay else None, max_relation_drops=max_relation_drops,
        max_relation_drop_frac=max_relation_drop_frac, max_group_drops=max_group_drops,
        max_group_drop_frac=max_group_drop_frac, admit=admit, admit_max_literals=admit_max_literals,
        relation_floor=relation_floor, repair_budget_s=repair_budget_s,
    )
    _disarm_stall()
    if not keep_work:
        shutil.rmtree(work, ignore_errors=True)
    return summary


