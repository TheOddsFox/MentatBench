"""MEASUREMENT ONLY (reads <fixture>/gold; product code never does). The relation floor of one System One model, by the
pre-declared rule of 2026-10-26, computed from a dev build's kept work dir and a dev fixture's gold (never toy10k).

Rule: the floor is the lowest conf threshold t at which the model's named relations reach gold-entailed precision >= TARGET
(default 0.8). The candidates are the distinct confs of the named decisions, ascending. Precision is not monotone in t, so
this is the lowest t that meets the target, not a threshold above which the target holds. If no t meets it (or no decision
is named), the model names no relations: the harness argument is sys.float_info.max, a finite floor that every conf is
below. A toy10k conf can exceed the dev maximum, so no dev-derived bound is used. If a relation has no conf and no t meets
the target, no floor can silence it: no harness argument is printed and the exit status is 1.

Population: the named decisions of relations.jsonl (rep == -1: plain, representative and fallback rows; NONE is never
written), pre-admission, as the child returned them. Binding-rejected EQUIV and one-sided rows are never written there
(fixed.py build: equiv_rejected / relation_rejected), so they are not in the population; their counts are printed. Derived
complement rows are not counted in the precision (as analyze.py's decision table). They carry their representative's conf,
the harness drops them with it (fixed._mark_floored, children), and their precision at the chosen floor is a diagnostic.

Floor semantics (harness/fixed.py _mark_floored and _admit_units, both `conf < floor`): a relation is kept iff conf >= t. A
relation without conf is never floored: it is kept at every t, and it is part of every kept set. The harness argument is
`--relation-floor REPR` (build) or finalize_checkpoint(relation_floor=t); repr round-trips the float exactly.

2-CNF lower bound: gold entailment is the 2-CNF closure of gold/theory.json plus the gold bindings (gold_oracle.Gold), over
the corpus ids and every other id the gold names (a dev fixture's gold can cover more ids than its corpus). Constraints
outside 2-CNF are ignored (counted in gold_ignored_ops). Entailed counts are a sound lower bound, so the precision is a
lower bound too: on toy10k (48 atleast and 48 atmost ops ignored) the kept precision may be understated, and the floor may
differ from the one that full gold would give. toy_native has no ignored ops. The fixture is identified by its corrections
stamp when it is a registered version; otherwise by the sha256 of corpus.jsonl and gold/theory.json.

Partial builds: the checkpoint's caps and budgets (fixed.partial_reasons) and the sibling submission's PARTIAL.json are
recorded as partial_reasons. They are not refused: a capped dev build is a sample of the pairs.

Label mode (--labels): the same rule over the decisions of a curated suite run (decisions.jsonl) judged by its suite labels
(gold/suite_labels.jsonl). A named decision (EQUIV, IMPLIES, IMPLIED_BY or EXCLUDES) is correct iff it equals the label of
its candidate, in the candidate's order (decision_scoring.load orients a reversed (a, b)): a named EQUIV on an IMPLIES label is
wrong, exactly. NONE and missing decisions are not named, so they are not in the population. The harness rule is the same
(kept iff conf >= t, conf-less always kept), and so are the outputs and exit status. A conf that is not a finite number makes
the decision unanswered, so it is not named (counted as invalid_conf); a conf absent at the top level is the decision's own.

usage: python tools/eval/floor_calibrate.py WORK_DIR --fixture-dir FIXTURE_DIR [--target 0.8] [--json OUT]
       python tools/eval/floor_calibrate.py --labels SUITE_LABELS --decisions DECISIONS --candidates CANDIDATES [--target 0.8] [--json OUT]
WORK_DIR: a kept build work dir (checkpoint.json, decided.jsonl, relations.jsonl); FIXTURE_DIR: a fixtures/<name> directory.
"""
import argparse
import hashlib
import json
import sys
from collections import defaultdict, namedtuple
from fractions import Fraction
from pathlib import Path

from mentat_bench import corrections, decision_scoring, ir
from mentat_bench.harness import fixed as F

sys.path.insert(0, str(Path(__file__).parent))
from gold_oracle import Gold  # noqa: E402

OPS = ("IMPLIES", "IMPLIED_BY", "EXCLUDES", "EQUIV")
NO_RELATION_FLOOR = sys.float_info.max  # names no relation: every conf is below it, and it passes _check_admit_knobs
# one relation row: index (its relations.jsonl line), conf (None when the child sent none), rel, gold-entailed (0/1), ids
Row = namedtuple("Row", "index conf rel entailed ids")


def load_gold(fixture_dir: Path) -> tuple[list[str], Gold]:
    """(corpus ids, gold): the gold over the corpus ids (indices 0..n-1) and the other ids the gold's theory or bindings name."""
    cid = [r["id"] for r in ir.read_jsonl(fixture_dir / "corpus.jsonl")]
    theory = json.loads((fixture_dir / "gold" / "theory.json").read_text(encoding="utf-8"))
    bound = [json.loads(line)["id"] for line in open(fixture_dir / "gold" / "bindings.jsonl", encoding="utf-8") if line.strip()]
    extra = sorted((_var_ids(theory, set()) | set(bound)) - set(cid))
    return cid, Gold(fixture_dir, cid + extra)


def _var_ids(node, out: set) -> set:
    if isinstance(node, dict):
        if node.get("op") == "var":
            out.add(node["id"])
        for value in node.values():
            _var_ids(value, out)
    elif isinstance(node, list):
        for value in node:
            _var_ids(value, out)
    return out


def population(work: Path, fixture_dir: Path) -> tuple[list[Row], list[Row], Gold, dict]:
    """(decisions, derived rows, gold, checkpoint) of a kept work dir, in relations.jsonl order."""
    ckpt = json.loads((work / "checkpoint.json").read_text(encoding="utf-8"))
    cid, gold = load_gold(fixture_dir)
    cidx = {x: k for k, x in enumerate(cid)}
    to_c = [cidx[r["id"]] for r in F._jsonl_prefix(work / "decided.jsonl", ckpt["decided_bytes"])]  # decided row -> corpus
    decisions, derived = [], []
    for n, row in enumerate(F._jsonl_prefix(work / "relations.jsonl", ckpt["relations_bytes"])):
        i, j, rel = to_c[row[0]], to_c[row[1]], row[2]
        rep = row[3] if len(row) > 3 else -1
        r = Row(n, row[4] if len(row) > 4 else None, rel, int(gold.entailed(i, j, rel)), (cid[i], cid[j]))
        (decisions if rep == -1 else derived).append(r)
    return decisions, derived, gold, ckpt


def _ratio(k: int, n: int) -> float | None:
    return round(k / n, 4) if n else None


def _zero() -> dict:
    return {op: [0, 0] for op in OPS}  # op -> [kept, gold-entailed]


def _point(t: float | None, counts: dict) -> dict:
    kept = sum(n for n, _ in counts.values())
    entailed = sum(k for _, k in counts.values())
    return {"t": t, "kept": kept, "entailed": entailed, "precision": _ratio(entailed, kept),
            "per_op": {op: {"kept": n, "entailed": k, "precision": _ratio(k, n)} for op, (n, k) in counts.items()}}


def kept(rows: list[Row], t: float | None) -> list[Row]:
    """The rows the harness keeps at floor t: conf >= t, and every row without conf (t None keeps all)."""
    return [r for r in rows if t is None or r.conf is None or r.conf >= t]


def sweep(rows: list[Row]) -> tuple[list[dict], dict]:
    """The curve over the distinct confs, ascending: point t keeps kept(rows, t). Returns (curve, no_floor)."""
    base, by_conf = _zero(), defaultdict(_zero)
    for r in rows:
        counts = base if r.conf is None else by_conf[r.conf]
        counts[r.rel][0] += 1
        counts[r.rel][1] += r.entailed
    acc = {op: list(v) for op, v in base.items()}
    curve = []
    for conf in sorted(by_conf, reverse=True):  # each step adds the confs at and above conf
        for op, (n, k) in by_conf[conf].items():
            acc[op][0] += n
            acc[op][1] += k
        curve.append(_point(conf, acc))
    curve.reverse()
    return curve, _point(None, acc)


def choose(curve: list[dict], target: float) -> dict | None:
    """The lowest t whose kept set reaches the target precision (exact on the counts); None when no t does."""
    goal = Fraction(str(target))
    return next((p for p in curve if p["kept"] and Fraction(p["entailed"], p["kept"]) >= goal), None)


def _fixture_record(fixture_dir: Path) -> dict:
    try:
        return {"fixture_stamp": corrections.stamp_for(fixture_dir, hashes=corrections.content_hashes(fixture_dir)),
                "fixture_sha256": None}
    except corrections.CorrectionError:  # not a registered fixture version (a dev fixture)
        return {"fixture_stamp": None, "fixture_sha256": {
            rel: hashlib.sha256((fixture_dir / rel).read_bytes()).hexdigest() for rel in ("corpus.jsonl", "gold/theory.json")}}


def _partial_reasons(work: Path, ckpt: dict) -> list[str]:
    reasons = list(F.partial_reasons(ckpt))
    if work.name.endswith(".build"):  # the sibling submission of a kept work dir: its PARTIAL.json, when written
        pj = work.with_name(work.name[: -len(".build")]) / F.PARTIAL_FILE
        if pj.is_file():
            reasons += [r for r in json.loads(pj.read_text(encoding="utf-8"))["reasons"] if r not in reasons]
    return reasons


def calibrate(work: Path, fixture_dir: Path, target: float = 0.8) -> dict:
    decisions, derived, gold, ckpt = population(work, fixture_dir)
    confs = [r.conf for r in decisions if r.conf is not None]
    conf_less = len(decisions) - len(confs)
    curve, no_floor = sweep(decisions)
    hit = choose(curve, target)
    t = hit["t"] if hit else NO_RELATION_FLOOR
    kept_derived = kept(derived, t)
    derived_at_floor = {"floor": t, "rows": len(derived), "kept": len(kept_derived),
                        "entailed_kept": sum(r.entailed for r in kept_derived),
                        "precision": _ratio(sum(r.entailed for r in kept_derived), len(kept_derived))}
    harness = None if (not hit and conf_less) else f"--relation-floor {t!r}"
    return {
        "work": str(work), "fixture": str(fixture_dir), **_fixture_record(fixture_dir), "target": target,
        "partial_reasons": _partial_reasons(work, ckpt),
        "population": {"named_decisions": len(decisions), "with_conf": len(confs), "without_conf": conf_less,
                       "derived_rows": len(derived), "equiv_rejected": ckpt.get("equiv_rejected", 0),
                       "relation_rejected": ckpt.get("relation_rejected", 0)},
        "gold_ignored_ops": gold.ignored,
        "no_floor": no_floor,
        "curve": curve,
        "names_relations": hit is not None,
        "floor": hit["t"] if hit else None,
        "chosen": hit,
        "harness_args": harness,
        "none_floor": {"t": NO_RELATION_FLOOR, "conf_less_kept": conf_less},
        "derived_at_floor": derived_at_floor,
    }


def calibrate_labels(labels: Path, decisions: Path, candidates: Path, target: float = 0.8) -> dict:
    """The label mode: the curve and the lowest-floor rule over the named relation decisions of a suite run. Row.entailed is
    1 when the named relation is correct (equal to the oriented label), so the sweep and choose are the 2-CNF mode's."""
    items, counts = decision_scoring.load(labels, candidates, decisions)
    named = [it for it in items if it["op"] == "decide_relation" and it["pred"] in decision_scoring.NAMED]
    rows = [Row(n, it["conf"], it["pred"], int(it["pred"] == it["truth"]), (it["qid"],)) for n, it in enumerate(named)]
    confs = [r.conf for r in rows if r.conf is not None]
    conf_less = len(rows) - len(confs)
    curve, no_floor = sweep(rows)
    hit = choose(curve, target)
    t = hit["t"] if hit else NO_RELATION_FLOOR
    harness = None if (not hit and conf_less) else f"--relation-floor {t!r}"
    return {
        "labels": str(labels), "decisions": str(decisions), "candidates": str(candidates), "target": target,
        "counts": counts,
        "population": {"named_decisions": len(rows), "with_conf": len(confs), "without_conf": conf_less},
        "no_floor": no_floor,
        "curve": curve,
        "names_relations": hit is not None,
        "floor": hit["t"] if hit else None,
        "chosen": hit,
        "harness_args": harness,
        "none_floor": {"t": NO_RELATION_FLOOR, "conf_less_kept": conf_less},
    }


def _fmt(p: dict) -> str:
    ops = ", ".join(f"{op} {v['precision']} ({v['entailed']}/{v['kept']})" for op, v in p["per_op"].items() if v["kept"])
    return f"kept {p['kept']}, entailed {p['entailed']}, precision {p['precision']}  [{ops}]"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("work", type=Path, nargs="?")
    ap.add_argument("--fixture-dir", type=Path)
    ap.add_argument("--labels", type=Path, help="label mode: suite labels (with --decisions and --candidates)")
    ap.add_argument("--decisions", type=Path)
    ap.add_argument("--candidates", type=Path)
    ap.add_argument("--target", type=float, default=0.8)
    ap.add_argument("--json", type=Path)
    a = ap.parse_args(argv)
    if a.labels:
        if a.work or a.fixture_dir or not (a.decisions and a.candidates):
            ap.error("--labels takes --decisions and --candidates, and neither WORK_DIR nor --fixture-dir")
        out = calibrate_labels(a.labels, a.decisions, a.candidates, a.target)
    elif a.work and a.fixture_dir:
        out = calibrate(a.work, a.fixture_dir, a.target)
    else:
        ap.error("give WORK_DIR and --fixture-dir, or --labels with --decisions and --candidates")
    if a.json:
        a.json.write_text(json.dumps(out, indent=1), encoding="utf-8")
    pop = out["population"]
    if a.labels:
        c = out["counts"]
        print(f"named relation decisions {pop['named_decisions']} ({pop['with_conf']} with conf, {pop['without_conf']} without); "
              f"labels {c['labels']}, decisions {c['decisions']}, oriented {c['oriented']}, invalid conf {c['invalid_conf']}, "
              f"torn final line {c['torn_final_line']}")
    else:
        print(f"named relation decisions {pop['named_decisions']} ({pop['with_conf']} with conf, {pop['without_conf']} without); "
              f"derived rows {pop['derived_rows']} (not counted; kept with their representative)")
        print(f"not in the population (rejected at build): equiv {pop['equiv_rejected']}, relation {pop['relation_rejected']}")
        print(f"partial: {out['partial_reasons'] or 'no'}; gold ignored ops {out['gold_ignored_ops']}")
    if out["no_floor"]["kept"]:
        print(f"no floor: {_fmt(out['no_floor'])}")
    if out["names_relations"]:
        print(f"target {a.target}: floor {out['floor']!r}: {_fmt(out['chosen'])}")
    else:
        print(f"target {a.target}: no threshold reaches it; the model names no relations")
    if out["harness_args"]:
        print(f"harness: {out['harness_args']}")
    else:
        print(f"error: {pop['without_conf']} named relations have no conf and the floor never drops them: no harness argument")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
