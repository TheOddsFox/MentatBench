"""How closely the reasoning sample's weighted values track the full test set, for the 17 v0.2.0 models: each model's sample-weighted
MCC (from results.json, written by score.py) against its published full-set MCC (results/v0.2.0/leaderboard.json), for the relation,
group and headline MCC. Writes representativeness.json, deterministically. DESCRIPTIVE ONLY: the counts describe these 500 items;
the intervals are conditional on them and exclude item-sampling variance.

usage: python -B representativeness.py       (after score.py; from any directory)

Per metric: the Spearman correlation of the sample and full-set values across the 17 models, the mean and the maximum absolute
difference, and how many of the 17 full-set values lie inside the model's 95% league-bootstrap interval on the sample.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
import score as SC  # noqa: E402  (the same ranking and rounding as score.py)

METRICS = ("relation", "group", "headline")


def document(results: dict[str, Any], leaderboard: dict[str, Any]) -> str:
    """The bytes of representativeness.json for a results document (score.run) and the leaderboard."""
    published = {m["id"]: m["metrics"] for m in leaderboard["models"]}
    models = [m["id"] for m in sorted(leaderboard["models"], key=lambda m: m["rank"])]
    rows: dict[str, Any] = {}
    for m in models:
        sample = results["models"][m]["weighted"]
        rows[m] = {}
        for metric in METRICS:
            full = published[m][metric]["point"]
            lo, hi = sample[metric]["lo"], sample[metric]["hi"]
            rows[m][metric] = {"sample": sample[metric]["point"], "full_set": full, "sample_lo": lo, "sample_hi": hi,
                               "full_set_in_sample_interval": lo is not None and hi is not None and lo <= full <= hi,
                               "difference": round(sample[metric]["point"] - full, 6)}
    summary = {}
    for metric in METRICS:
        s = [rows[m][metric]["sample"] for m in models]
        f = [rows[m][metric]["full_set"] for m in models]
        d = [abs(a - b) for a, b in zip(s, f)]
        summary[metric] = {"spearman": SC._round(SC.spearman(s, f)), "mean_abs_difference": SC._round(sum(d) / len(d)),
                           "max_abs_difference": SC._round(max(d)),
                           "full_set_inside_sample_interval": sum(rows[m][metric]["full_set_in_sample_interval"] for m in models),
                           "of": len(models)}
    doc = {"schema": "mentat-bench-reasoning-representativeness/1", "source": "results.json (score.py), leaderboard.json",
           "status": results["status"], "items": results["design"]["items"],
           "interval": results["bootstrap"]["interval"],
           "descriptive": "Descriptive only. The intervals are conditional on the 500 sampled items (they exclude item-sampling "
                          "variance); the inside counts describe this sample and are not a test of representativeness.",
           "models": rows, "summary": summary}
    return json.dumps(doc, indent=1, sort_keys=True, allow_nan=False) + "\n"


def main() -> None:
    results = json.loads((HERE / "results.json").read_text(encoding="utf-8"))
    leaderboard = json.loads(SC.LEADERBOARD.read_text(encoding="utf-8"))
    text = document(results, leaderboard)
    (HERE / "representativeness.json").write_text(text, encoding="utf-8", newline="\n")
    for metric, values in json.loads(text)["summary"].items():
        print(metric, json.dumps(values, sort_keys=True))


if __name__ == "__main__":
    main()
