"""results/v0.2.2/build_bundle.py: the analysis release based on v0.2.1 (fast). The rows are v0.2.0's and v0.2.1's; profiles, reference and
analysis differ from v0.2.1 on the release line alone; transfer.json is the pilot's results.json, rounded, in leaderboard order, with no
rank; the index hand-over from v0.2.1."""

import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
V022 = ROOT / "results" / "v0.2.2"
V021 = ROOT / "results" / "v0.2.1"
V020 = ROOT / "results" / "v0.2.0"
PILOT = ROOT / "docs" / "audits" / "v0.2.0-transfer-pilot"
CONTRACT_KEYS = ["schema", "release", "based_on", "kind", "ranked", "title", "note", "deviations", "source", "items", "primary",
                 "secondary", "sensitivity", "models", "anchors", "conditions"]
BANNED = ("fixture", "outperform", "smarter", "superior", "beats", "better than", "general reasoning", "general capabilit",
          "public bundle", "test fixture", "v0.3", "measurement release", "internal release")  # the site's copy guard, as listed
IDS_ONLY = re.compile(r"[A-Za-z0-9_.+/ ,-]+")


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


B = _load("build_bundle_v022", V022 / "build_bundle.py")


def load(path: Path):
    return json.loads(path.read_bytes().decode("utf-8"))


def all_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from all_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from all_keys(v)


def all_strings(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from all_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from all_strings(v)
    elif isinstance(obj, str):
        yield obj


def pair(block: dict) -> dict:
    return {"point": round(block["point"], 6), "ci95": [round(block["lo"], 6), round(block["hi"], 6)]}


# ---------------------------------------------------------------- rebuild, rows and carried files

def test_rebuild_gives_the_committed_bytes():
    outputs = B.build_outputs()
    assert set(outputs) == {"leaderboard.json", "leaderboard.csv", "analysis.json", "profiles.json", "reference.json",
                            "transfer.json", "index.json"}
    for name, data in outputs.items():
        path = ROOT / "results" / "index.json" if name == "index.json" else V022 / name
        assert path.read_bytes() == data, name
    assert B.build(check_only=True) == 0


def test_leaderboard_rows_equal_v021_and_v020():
    v20, v21, v22 = load(V020 / "leaderboard.json"), load(V021 / "leaderboard.json"), load(V022 / "leaderboard.json")
    for key in ("models", "baselines", "ranking", "resolved_comparisons", "depth_buckets", "depth_n"):
        assert v22[key] == v21[key] == v20[key], key
    csv = (V022 / "leaderboard.csv").read_bytes()
    assert csv == (V021 / "leaderboard.csv").read_bytes() == (V020 / "leaderboard.csv").read_bytes()


def test_leaderboard_provenance_is_based_on_v021_without_record_from():
    lb = load(V022 / "leaderboard.json")
    assert lb["release"] == "v0.2.2" and lb["date"] == "2026-11-04"
    prov = lb["provenance"]
    assert "record_from" not in prov and prov["based_on_release"] == "v0.2.1"
    assert prov["bundle_builder"] == "results/v0.2.2/build_bundle.py"
    assert prov["analysis"] == {"path": "results/v0.2.2/analysis.json", "sha256": B.sha256((V022 / "analysis.json").read_bytes())}


@pytest.mark.parametrize("name", ["analysis.json", "profiles.json", "reference.json"])
def test_carried_file_differs_from_v021_only_on_the_release_line(name):
    old, new = (V021 / name).read_bytes(), (V022 / name).read_bytes()
    assert old.count(b'"release": "v0.2.1"') == 1
    assert new == old.replace(b'"release": "v0.2.1"', b'"release": "v0.2.2"')
    a, b = load(V021 / name), load(V022 / name)
    assert a.pop("release") == "v0.2.1" and b.pop("release") == "v0.2.2"
    assert a == b


def test_analysis_keeps_the_v020_base_of_its_chain():
    an = load(V022 / "analysis.json")
    assert an["release"] == "v0.2.2" and an["based_on"]["release"] == "v0.2.0"


# ---------------------------------------------------------------- transfer.json

def test_transfer_numbers_are_the_pilot_results_rounded():
    res, t = load(PILOT / "results.json"), load(V022 / "transfer.json")
    main = res["main"]
    assert t["source"] == {"path": "docs/audits/v0.2.0-transfer-pilot/results.json",
                           "sha256": B.sha256((PILOT / "results.json").read_bytes())}
    assert t["items"] == {"relations": main["items"]["relation"], "groups": main["items"]["group"],
                          "clusters": main["items"]["clusters"]}
    prim = main["primary"]
    assert t["primary"] == {"statistic": prim["statistic"], **pair(prim), "draws": prim["draws"], "reading": prim["reading"],
                            "threshold": 0.6}
    assert t["primary"]["point"] == 0.821078 and t["primary"]["ci95"] == [0.772059, 0.889706]
    sec, out = main["secondary"], t["secondary"]
    assert out["kendall_tau_b"] == pair(sec["kendall_tau_b"])
    assert out["rho_relation"] == pair(sec["rho_relation"]) and out["rho_group"] == pair(sec["rho_group"])
    assert out["resolved_pairs"] == {"pairs": sec["resolved_pairs"]["pairs"], "kept_order": sec["resolved_pairs"]["kept_order"]}
    assert [s["id"] for s in t["sensitivity"]] == ["unanimous_only", "excluding_changed", "excluding_trivial"]
    for s in t["sensitivity"]:
        src = res["sensitivity"][s["id"]]
        assert {k: s[k] for k in ("point", "ci95", "reading")} == {**pair(src), "reading": src["reading"]}, s["id"]
        assert (s["relations"], s["groups"]) == (src["items"]["relation"], src["items"]["group"]), s["id"]
    for a in t["anchors"]:
        src = main["anchors"][a["id"]]
        assert a["headline"] == pair(src["headline"])
        assert a["relation_mcc"] == round(src["relation_mcc"]["point"], 6) and a["group_mcc"] == round(src["group_mcc"]["point"], 6)
    head = {e["id"]: e["metrics"]["headline"]["point"] for e in load(V020 / "leaderboard.json")["models"]}
    assert len(t["models"]) == 17
    for m in t["models"]:
        src = main["models"][m["id"]]
        assert m["v020_headline"] == round(head[m["id"]], 6), m["id"]
        assert m["headline"] == pair(src["headline"]), m["id"]
        assert m["relation_mcc"] == round(src["relation_mcc"]["point"], 6), m["id"]
        assert m["group_mcc"] == round(src["group_mcc"]["point"], 6), m["id"]
        assert m["relation_accuracy"] == round(src["accuracy"]["decide_relation"], 6), m["id"]
        assert m["group_accuracy"] == round(src["accuracy"]["decide_group"], 6), m["id"]


def test_models_are_in_the_v020_leaderboard_rank_order():
    lb = load(V020 / "leaderboard.json")
    ranked = [e["id"] for e in sorted(lb["models"], key=lambda e: e["rank"])]
    t = load(V022 / "transfer.json")
    assert [m["id"] for m in t["models"]] == ranked == lb["ranking"] and len(ranked) == 17
    assert [a["id"] for a in t["anchors"]] == ["heuristic", "lexical"]


def test_transfer_is_unranked_has_no_rank_key_and_holds_only_ids_and_numbers():
    t = load(V022 / "transfer.json")
    assert t["ranked"] is False and t["kind"] == "transfer_check" and t["schema"] == "mentat-bench-transfer/1"
    assert "rank" not in set(all_keys(t))  # the key is named ranked (false), never rank
    assert not any(not IDS_ONLY.fullmatch(s) for s in all_strings({k: v for k, v in t.items() if k != "conditions"}))
    raw = (V022 / "transfer.json").read_bytes()
    assert b"\r" not in raw and raw == (json.dumps(t, indent=1, ensure_ascii=False) + "\n").encode("utf-8")
    assert list(t) == CONTRACT_KEYS


def test_conditions_are_plain_disclosures_without_banned_words():
    t = load(V022 / "transfer.json")
    assert 5 <= len(t["conditions"]) <= 14
    for condition in t["conditions"]:
        assert not any(word in condition.lower() for word in BANNED), condition
    assert not any(word in (V022 / "README.md").read_text(encoding="utf-8").lower() for word in BANNED)


def test_the_builder_refuses_a_banned_word_in_the_conditions(monkeypatch):
    monkeypatch.setattr(B, "CONDITIONS", [*B.CONDITIONS, "The test fixture was calibrated on the v0.2.0 set."])
    with pytest.raises(B.CheckError, match="banned word"):
        B.transfer_bytes((PILOT / "results.json").read_bytes(), load(V020 / "leaderboard.json"))


def test_the_builder_refuses_a_question_mark_in_the_free_text(monkeypatch):
    monkeypatch.setattr(B, "NOTE", "Will it rain? yes")
    with pytest.raises(B.CheckError, match="free text"):
        B.transfer_bytes((PILOT / "results.json").read_bytes(), load(V020 / "leaderboard.json"))


def test_reading_rule_is_the_prereg_rule_at_the_boundaries():
    assert B.reading_rule(0.6, 0.9) == "carries over"  # the lower bound at the threshold carries over
    assert B.reading_rule(0.59, 0.599) == "does not carry over"  # the upper bound below it does not
    assert B.reading_rule(0.59, 0.6) == "inconclusive"


def tampered(edit) -> bytes:
    res = load(PILOT / "results.json")
    edit(res)
    return json.dumps(res).encode("utf-8")


@pytest.mark.parametrize("edit, match", [
    (lambda r: r["main"]["primary"].update(reading="maybe"), "reading"),
    (lambda r: r["main"]["models"].pop("laya_latest"), "17 leaderboard models"),
    (lambda r: r["sensitivity"].pop("excluding_trivial"), "sensitivity"),
    (lambda r: r["main"]["primary"].update(statistic="pilot"), "statistic"),
    (lambda r: r["main"]["primary"].update(lo=0.5), "pre-registered rule"),  # a lower bound below 0.6 cannot read carries over
    (lambda r: r["sensitivity"]["unanimous_only"].update(lo=0.2), "unanimous_only"),
])
def test_builder_refuses_a_tampered_pilot(edit, match):
    with pytest.raises(B.CheckError, match=match):
        B.transfer_bytes(tampered(edit), load(V020 / "leaderboard.json"))


# ---------------------------------------------------------------- the index hand-over

def test_committed_index_names_v022_latest_and_v021_superseded_with_a_note():
    index = load(ROOT / "results" / "index.json")
    assert index["latest"] == "v0.2.2" and index["versions"][0]["version"] == "v0.2.2"
    entry = index["versions"][0]
    assert entry["status"] == "latest" and entry["superseded_by"] is None and entry["based_on"] == "v0.2.1"
    for key in ("analysis_json", "leaderboard_json", "leaderboard_csv", "profiles_json", "reference_json", "transfer_json", "readme",
                "builder"):
        assert entry[key].startswith("results/v0.2.2/"), key
    assert entry["predictions_dir"] == "results/v0.2.0/predictions" and entry["cells_dir"] == "results/v0.2.0/cells"
    v021 = next(v for v in index["versions"] if v["version"] == "v0.2.1")
    assert v021["status"] == "superseded" and v021["superseded_by"] == "v0.2.2" and "v0.2.2" in v021["note"]
    assert B.index_doc() == (ROOT / "results" / "index.json").read_bytes()  # the builder reproduces the committed index


def test_index_builder_takes_the_index_over_from_v021(tmp_path, monkeypatch):
    """From the index as it stood before v0.2.2 (v0.2.1 latest, no v0.2.2 entry, no note), the builder writes the committed index."""
    index = load(ROOT / "results" / "index.json")
    before = dict(index, latest="v0.2.1", versions=[])
    for v in index["versions"]:
        if v["version"] == "v0.2.2":
            continue
        if v["version"] == "v0.2.1":
            v = {k: x for k, x in v.items() if k != "note"} | {"status": "latest", "superseded_by": None}
        before["versions"].append(v)
    (tmp_path / "index.json").write_text(json.dumps(before), encoding="utf-8")
    monkeypatch.setattr(B, "RESULTS", tmp_path)
    assert B.index_doc() == (ROOT / "results" / "index.json").read_bytes()


def test_index_builder_leaves_the_index_alone_once_a_later_release_is_latest(tmp_path, monkeypatch):
    index = load(ROOT / "results" / "index.json")
    (tmp_path / "index.json").write_text(json.dumps(dict(index, latest="v1.0.0")), encoding="utf-8")
    monkeypatch.setattr(B, "RESULTS", tmp_path)
    assert B.index_doc() is None
