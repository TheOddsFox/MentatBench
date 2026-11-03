"""The reasoning reference sample (docs/audits/v0.2.0-reasoning-reference/build_sample.py): a re-run gives the committed bytes,
the allocation floors and totals (300 relations, 200 groups), the weights sum to the populations, the depth-4 cell is taken whole,
the slices partition the sample within the budget, and the sample holds no gold, qid or outcome id."""

import json
import re
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "docs" / "audits" / "v0.2.0-reasoning-reference"
# Other audits ship a build_sample module too: import ours fresh and leave neither it nor the folder behind.
sys.modules.pop("build_sample", None)
sys.path.insert(0, str(AUDIT))
import build_sample as BS  # noqa: E402

sys.path[:] = [p for p in sys.path if not p or Path(p).resolve() != AUDIT]
sys.modules.pop("build_sample", None)


def _rows(name):
    return [json.loads(line) for line in (AUDIT / name).read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture(scope="module")
def design():
    return json.loads((AUDIT / "slices.json").read_text(encoding="utf-8"))


def test_rerun_gives_the_committed_bytes(tmp_path):
    BS.main(tmp_path)
    for name in ("sample.jsonl", "key.jsonl", "slices.json"):
        assert (tmp_path / name).read_bytes() == (AUDIT / name).read_bytes(), name


def test_allocate_keeps_the_floor_and_the_total():
    sizes = {"a": 100, "b": 3, "c": 50, "d": 7}
    alloc = BS.allocate(sizes, 20, 5)
    assert sum(alloc.values()) == 20
    assert alloc["b"] == 3  # the whole cell when it is smaller than the floor
    assert all(min(sizes[c], 5) <= alloc[c] <= sizes[c] for c in sizes)
    assert alloc == BS.allocate(dict(reversed(list(sizes.items()))), 20, 5)  # the order of the sizes does not matter


def test_totals_and_floors_of_the_design(design):
    cells = design["cells"]
    rel = {c: v for c, v in cells.items() if v["op"] == "decide_relation"}
    grp = {c: v for c, v in cells.items() if v["op"] == "decide_group"}
    assert sum(v["sample"] for v in rel.values()) == 300
    assert sum(v["sample"] for v in grp.values()) == 200
    assert sum(v["population"] for v in rel.values()) == 6000
    assert sum(v["population"] for v in grp.values()) == 1000
    for c, v in rel.items():
        if c != BS.POOLED:
            assert v["sample"] >= min(v["population"], BS.RELATION_FLOOR), c
    for c, v in grp.items():
        assert v["sample"] >= min(v["population"], BS.GROUP_FLOOR), c
    for c, v in cells.items():
        assert 0 < v["sample"] <= v["population"], c


def test_the_recorded_allocation_is_the_rule_applied_to_the_populations(design):
    cells = design["cells"]
    rel = {c: v["population"] for c, v in cells.items() if v["op"] == "decide_relation" and c != BS.POOLED}
    grp = {c: v["population"] for c, v in cells.items() if v["op"] == "decide_group"}
    rel_alloc = BS.allocate(rel, 300 - cells[BS.POOLED]["sample"], BS.RELATION_FLOOR)
    grp_alloc = BS.allocate(grp, 200, BS.GROUP_FLOOR)
    for c, n in {**rel_alloc, **grp_alloc}.items():
        assert cells[c]["sample"] == n, c


def test_the_depth_four_items_form_one_cell_taken_whole(design):
    pooled = design["cells"][BS.POOLED]
    assert pooled["population"] == 6 and pooled["sample"] == 6 and pooled["weight"] == 1.0
    key = _rows("key.jsonl")
    assert sum(r["cell"] == BS.POOLED for r in key) == 6


def test_weights_are_population_over_sample_and_sum_to_the_populations(design):
    key = _rows("key.jsonl")
    for c, v in design["cells"].items():
        rows = [r for r in key if r["cell"] == c]
        assert len(rows) == v["sample"], c
        assert all(r["weight"] == v["population"] / v["sample"] for r in rows), c
        assert abs(sum(r["weight"] for r in rows) - v["population"]) < 1e-9, c
    for op, total in (("decide_relation", 6000), ("decide_group", 1000)):
        assert abs(sum(r["weight"] for r in key if r["op"] == op) - total) < 1e-9


def test_key_rows_are_in_sample_order_with_opaque_ids_and_the_fixture_leagues():
    sample, key = _rows("sample.jsonl"), _rows("key.jsonl")
    assert [r["id"] for r in sample] == [f"H{i:03d}" for i in range(1, 501)]
    assert [r["id"] for r in key] == [r["id"] for r in sample]
    assert len({r["qid"] for r in key}) == 500
    assert Counter(r["op"] for r in key) == {"decide_relation": 300, "decide_group": 200}
    assert len({r["league"] for r in key}) == 18
    relations = [r for r in key if r["op"] == "decide_relation"]
    groups = [r for r in key if r["op"] == "decide_group"]
    assert all(r["n_members"] is None for r in relations) and all(2 <= r["n_members"] <= 8 for r in groups)


def test_slices_partition_the_sample_within_the_budget(design):
    sample = {r["id"]: r for r in _rows("sample.jsonl")}
    slices = design["slices"]
    ids = [i for s in slices for i in s["ids"]]
    assert sorted(ids) == sorted(sample) and len(ids) == len(set(ids)) == 500
    for s in slices:
        assert 1 <= s["items"] <= BS.MAX_ITEMS and s["items"] == len(s["ids"])
        assert s["prompt_chars"] == sum(len(sample[i]["prompt"]) for i in s["ids"])
        assert s["prompt_chars"] <= design["budget_chars"]
        assert s["relations"] + s["groups"] == s["items"]


def test_the_sample_holds_no_gold_no_qid_and_no_outcome_id():
    sample = _rows("sample.jsonl")
    assert all(set(r) == {"id", "type", "prompt", "answer_format"} for r in sample)
    text = (AUDIT / "sample.jsonl").read_text(encoding="utf-8")
    assert not re.search(r"\b[rgb]\d{5}\b", text), "a qid"
    assert "mh:" not in text, "an outcome id"
    for r in sample:
        assert "League rules:\n" in r["prompt"]
        assert r["type"] == "group" or "Which relation holds between A and B?" in r["prompt"]
