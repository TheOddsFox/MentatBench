"""Blocking recall by top-k: python -m mentat_bench.harness.topk_sweep [--fixture DIR ...] [--k K ...] [--target 0.98].

For each fixture (default fixtures/toy10k) and each K, the lexical blocking of `candidate_pairs`
with top_k=K (the same blocking as `build`, unit-level, sibling outcomes included) is compared with the gold pairs of
the fixture's theory (implies, iff and at-most-one over two outcomes; gold_pairs_from_theory). Prints recall and
candidate count per K, the smallest K whose recall reaches the target per fixture, and the smallest K that reaches it
on every fixture. Gold is read here only; nothing is asked of an adapter."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .. import ir
from .fixed import candidate_pairs, gold_pairs_from_theory

REPO = Path(__file__).resolve().parents[3]
DEFAULT_FIXTURES = (REPO / "fixtures" / "toy10k",)
DEFAULT_KS = (8, 16, 24, 32, 48, 53, 54, 64, 96, 128, 192, 256)
TARGET = 0.98


def load_fixture(fixture: Path) -> tuple[list[dict[str, Any]], list[tuple[str, str]]]:
    """Corpus records (as build reads them) and the gold outcome-ID pairs of the fixture's theory."""
    records = [{"id": r["id"], "question": r["text"], "label": r["label"], "event_title": r.get("event_title"),
                "context": r.get("context")}
               for r in ir.read_jsonl(fixture / "corpus.jsonl")]
    theory = ir.read_json(fixture / "gold" / "theory.json")
    return records, gold_pairs_from_theory(theory)


def recall_by_k(records: list[dict[str, Any]], gold_pairs: list[tuple[str, str]], ks: list[int]) -> list[dict[str, Any]]:
    """One row per K: blocking recall of the gold pairs (pairs naming an unknown outcome are ignored), candidates."""
    position = {rec["id"]: i for i, rec in enumerate(records)}
    gold = {tuple(sorted((position[a], position[b]))) for a, b in gold_pairs if a in position and b in position}
    rows = []
    for k in ks:
        candidates = set(candidate_pairs(records, top_k=k))
        rows.append({
            "top_k": k,
            "blocking_recall": round(len(gold & candidates) / len(gold), 6) if gold else None,
            "candidates": len(candidates),
            "gold_pairs": len(gold),
        })
    return rows


def smallest_k(rows: list[dict[str, Any]], target: float) -> int | None:
    return next((row["top_k"] for row in rows if row["blocking_recall"] is not None and row["blocking_recall"] >= target), None)


def sweep(fixtures: list[Path], ks: list[int], target: float) -> dict[str, Any]:
    result: dict[str, Any] = {"target": target, "fixtures": {}}
    for fixture in fixtures:
        records, gold_pairs = load_fixture(fixture)
        rows = recall_by_k(records, gold_pairs, ks)
        result["fixtures"][fixture.name] = {"outcomes": len(records), "rows": rows, "smallest_k": smallest_k(rows, target)}
    def reaches(rows: list[dict[str, Any]], k: int) -> bool:
        recall = next(row["blocking_recall"] for row in rows if row["top_k"] == k)
        return recall is not None and recall >= target

    result["smallest_k_all"] = next((k for k in ks if all(reaches(f["rows"], k) for f in result["fixtures"].values())), None)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m mentat_bench.harness.topk_sweep", description=__doc__.splitlines()[0])
    parser.add_argument("--fixture", action="append", type=Path, help="fixture directory (repeatable; default toy10k)")
    parser.add_argument("--k", action="append", type=int, help="top-k values to try (repeatable)")
    parser.add_argument("--target", type=float, default=TARGET, help="blocking recall to reach (default 0.98)")
    args = parser.parse_args(argv)
    fixtures = args.fixture or list(DEFAULT_FIXTURES)
    ks = sorted(set(args.k or DEFAULT_KS))
    if any(k < 1 for k in ks) or not 0 < args.target <= 1:
        print("top-k values must be positive and the target in (0, 1]", file=sys.stderr)
        return 2
    result = sweep(fixtures, ks, args.target)
    for name, block in result["fixtures"].items():
        print(f"{name} ({block['outcomes']} outcomes)")
        print(f"  {'top_k':>6} {'blocking_recall':>16} {'candidates':>11} {'gold_pairs':>11}")
        for row in block["rows"]:
            print(f"  {row['top_k']:>6} {row['blocking_recall']:>16} {row['candidates']:>11} {row['gold_pairs']:>11}")
        print(f"  smallest top_k with recall >= {args.target}: {block['smallest_k']}")
    print(f"smallest top_k with recall >= {args.target} on every fixture: {result['smallest_k_all']}")
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
