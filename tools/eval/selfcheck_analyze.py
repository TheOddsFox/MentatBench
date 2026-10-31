"""Self-check for tools/eval/analyze.py (MEASUREMENT ONLY; not collected by pytest). Builds a 4-record synthetic fixture,
work dir and finalize dir, runs analyze.py on it, and checks the per-op tables (per_op, per_op_retained, by_conf,
same_market_rows) against hand-computed values. usage: python tools/eval/selfcheck_analyze.py"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "python"))
from mentat_bench.harness import fixed as F  # noqa: E402

IDS = ["A:1", "A:2", "B:1", "B:2"]  # two markets, A and B
# gold: IMPLIES(0,1) (implies 0 -> 1) and EXCLUDES(0,2) (at most one of 0, 2); nothing else.
THEORY = {"interpretations": ["I0"], "constraints": [
    {"expr": {"op": "implies", "a": {"op": "var", "id": IDS[0]}, "b": {"op": "var", "id": IDS[1]}}},
    {"expr": {"op": "atmost", "k": 1, "args": [{"op": "var", "id": IDS[0]}, {"op": "var", "id": IDS[2]}]}},
]}
# (i, j, op, rep, conf, dropped by admission); rep -1 is a decision, otherwise a derived row
ROWS = [
    (0, 1, "IMPLIES", -1, 4.5, False),     # gold entails it
    (1, 0, "IMPLIED_BY", -1, 4.5, False),  # gold entails it (same fact as IMPLIES(0,1))
    (0, 2, "EXCLUDES", -1, 6.2, False),    # gold entails it
    (0, 3, "IMPLIES", -1, 6.2, True),      # not entailed; dropped
    (2, 3, "EXCLUDES", -1, None, False),   # not entailed
    (0, 1, "IMPLIES", 0, None, False),     # derived; entailed
]
# (n, entailed) per key, from the rows above
EXPECT = {
    "per_op": {"decision:IMPLIES": (2, 1), "decision:IMPLIED_BY": (1, 1), "decision:EXCLUDES": (2, 1),
               "decision:ALL": (5, 3), "derived:IMPLIES": (1, 1), "derived:ALL": (1, 1)},
    "per_op_retained": {"decision:IMPLIES": (1, 1), "decision:IMPLIED_BY": (1, 1), "decision:EXCLUDES": (2, 1),
                        "decision:ALL": (4, 3), "derived:IMPLIES": (1, 1), "derived:ALL": (1, 1)},
    "by_conf": {"4-5:IMPLIES": (1, 1), "4-5:IMPLIED_BY": (1, 1), "4-5:ALL": (2, 2), "6+:IMPLIES": (1, 0),
                "6+:EXCLUDES": (1, 1), "6+:ALL": (2, 1), "none:EXCLUDES": (1, 0), "none:ALL": (1, 0)},
    "same_market_rows": {"decision:IMPLIES": (1, 1), "decision:IMPLIED_BY": (1, 1), "decision:EXCLUDES": (1, 0),
                         "decision:ALL": (3, 2), "derived:IMPLIES": (1, 1), "derived:ALL": (1, 1)},
}


def jsonl(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path.stat().st_size


def main():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        fx, work, frun = tmp / "fx", tmp / "work", tmp / "frun"
        for d in (fx / "gold", work, frun):
            d.mkdir(parents=True)
        corpus = [{"id": x, "text": f"question {k}", "label": "alt", "event_title": None} for k, x in enumerate(IDS)]
        jsonl(fx / "corpus.jsonl", corpus)
        (fx / "gold" / "theory.json").write_text(json.dumps(THEORY), encoding="utf-8")
        jsonl(fx / "gold" / "bindings.jsonl", [{"id": x, "status": "unresolved"} for x in IDS])
        dec_bytes = jsonl(work / "decided.jsonl", [{"id": x, "status": "unresolved"} for x in IDS])
        rel_bytes = jsonl(work / "relations.jsonl", [[i, j, op, rep, conf] if conf is not None else [i, j, op, rep]
                                                       for i, j, op, rep, conf, _ in ROWS])
        jsonl(work / "groups.jsonl", [])
        cands = F.candidate_pairs([{"id": c["id"], "question": c["text"], "label": c["label"],
                                    "event_title": None} for c in corpus], top_k=24)
        ckpt = {"decided_bytes": dec_bytes, "relations_bytes": rel_bytes, "groups_bytes": 0,
                "relations_committed": 10, "pairs_considered": len(cands)}
        (work / "checkpoint.json").write_text(json.dumps(ckpt), encoding="utf-8")
        (frun / "units.json").write_text(json.dumps({"rel_dropped": [r[5] for r in ROWS], "groups": []}))
        (frun / "finalize_result.json").write_text(json.dumps({"summary": {}}))
        out = tmp / "out.json"
        env = {**os.environ, "PYTHONPATH": str(REPO / "python")}
        run = subprocess.run([sys.executable, str(REPO / "tools" / "eval" / "analyze.py"), "selfcheck", str(work),
                              str(frun), str(out), "--fixture-dir", str(fx)], env=env, capture_output=True, text=True)
        if run.returncode:
            sys.exit(f"analyze.py exited {run.returncode}:\n{run.stderr}")
        res = json.loads(out.read_text(encoding="utf-8"))
    for table, expect in EXPECT.items():
        assert set(res[table]) == set(expect), (table, sorted(res[table]))
        for key, (n, e) in expect.items():
            got = res[table][key]
            assert got == {"n": n, "entailed": e, "precision": round(e / n, 4)}, (table, key, got)
    print("analyze.py per-op self-check: OK")


if __name__ == "__main__":
    main()
