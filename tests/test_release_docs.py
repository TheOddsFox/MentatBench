"""The tier prose and the labels of README.md, results/README.md and results/SUMMARY.md. No stale sentence (one that the analysis has
overtaken), and every number the prose quotes is derived here from the committed analysis, leaderboard, verdicts and runs.json, then
looked for in the documents. A change to the data or to the prose fails a test until both agree."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "eval"))
import release_analysis as RA  # noqa: E402

DOCS = [ROOT / "README.md", ROOT / "results" / "README.md", ROOT / "results" / "SUMMARY.md"]
STALE = [
    "was not computed",                       # the Holm pairwise comparisons are computed (v0.1.1)
    "Only gaps to D3 27B are paired",
    "the order among the other models is untested",
    "Only gaps to the reference are paired",  # the v0.1.0 leaderboard note, replaced in v0.1.1
    "likely a shared group-kind convention",  # an untested hypothesis, not a finding
    "controls were flagged",                  # the controls are not flagged
    "Of those answers, 83 are in a logically unsatisfiable set on their own",  # a minimal set of at most three answers (v0.1.1)
    "The paired test is computed only against D3 27B",   # the v0.1.1 analysis pairs all 15 models (105 pairs)
    "Only paired gaps to D3 27B are tested",
    "exact 95% confidence interval 0% to",    # the interval is two-sided, to one decimal (v0.1.1)
    "an untested hypothesis, a shared misreading",  # replaced by the robustness sentences (exploratory.json)
    "9 of the 13 are group items with gold at_most_k where both answered none",
]
NUMBER_WORDS = {3: "three", 6: "six", 7: "seven", 9: "nine"}  # the prose spells these out


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _facts():
    analysis = _json(ROOT / "results" / "v0.1.1" / "analysis.json")
    lb = _json(ROOT / "results" / "v0.1.0" / "leaderboard.json")
    verdicts = _json(ROOT / "docs" / "audits" / "v0.1.1-item-audit" / "verdicts.json")
    expl = _json(ROOT / "docs" / "audits" / "v0.1.1-item-audit" / "exploratory.json")
    entries = {e["id"]: e for e in lb["models"] + lb["baselines"]}
    models = [e for e in lb["models"] if e["kind"] == "system_one_model"]
    d3 = "d3_27b-q8_0"
    others = [m for m in models if m["id"] != d3]
    rel_next = max(others, key=lambda m: m["metrics"]["relation"]["point"])
    grp_next = max(others, key=lambda m: m["metrics"]["group"]["point"])
    head_pairs = [p for p in analysis["pairwise"]["headline"] if d3 in (p["a"], p["b"])]
    drops = [m["metrics"]["held_out_relation"]["point"] - m["metrics"]["seen_relation"]["point"] for m in models]
    zeros = [m for m in models if m["metrics"]["q_n_strict"]["point"] == 0]
    odd = next(m for m in models if m["metrics"]["q_n_strict"]["point"] != 0)
    cons = analysis["consistency"]["runs"][d3]
    depth = analysis["depth"]["runs"][d3]
    named = depth["0"]["n"] + depth["1"]["n"] + depth["2"]["n"] + depth["3"]["n"] + depth["4+"]["n"]
    ref = analysis["binding_detection"]["runs"][d3]
    summary = analysis["robustness"]["summary"]
    return {
        "analysis": analysis, "lb": lb, "verdicts": verdicts, "expl": expl, "entries": entries,
        "rel_next": rel_next, "grp_next": grp_next, "head_pairs": head_pairs,
        "drops": drops, "zeros": zeros, "odd": odd, "cons": cons, "depth": depth, "named": named,
        "ref": ref, "summary": summary, "d3": d3,
    }


def _phrases(f):
    """(phrase, where) pairs: each phrase must appear in every document listed in where."""
    d3 = f["d3"]
    e = f["entries"]
    vf = RA.verdict_facts(f["verdicts"])
    by_group = f["verdicts"]["by_group"]
    n_resolved = sum(p["resolved"] for p in f["head_pairs"])
    seven = sum(1 for x in f["drops"] if x <= -0.05)
    ub = f"{100 * RA.upper_bound_zero_of(vf['n_controls']):.1f}"
    ub1 = f"{100 * RA.upper_bound_zero_one_sided_of(vf['n_controls']):.1f}"
    expl = f["expl"]
    akn = expl["b_suspect_labels"]["atmost_k_none"]
    slice_s = int(next(iter(akn["slices"])))
    agree = next(r for r in expl["a_gold_agreement_by_slice"]["slices"] if r["slice"] == slice_s)["judge1_gold_agree"]
    remaining = expl["d_exploratory_bound_reading"]["remaining_suspects"]
    discarded = expl["c_sensitivity_discarded_first_answers"]["flagged"]["suspect_label"]
    cons = f["cons"]
    share = 100 * cons["share"]
    lo, hi = (100 * x for x in cons["ci95"])
    pct = f"{share:.1f}%; 95% interval {lo:.1f}% to {hi:.1f}%"
    return [
        (f"Relation MCC {e[d3]['metrics']['relation']['point']:.3f}, against "
         f"{f['rel_next']['metrics']['relation']['point']:.3f} for the next model ({f['rel_next']['display_name']}); group MCC "
         f"{e[d3]['metrics']['group']['point']:.3f}, against {f['grp_next']['metrics']['group']['point']:.3f} for the next "
         f"({f['grp_next']['display_name']}).", ("README.md", "results/README.md", "results/SUMMARY.md")),
        (f"All {len(f['head_pairs'])} of its headline pairs are resolved, each in its favour.",
         ("README.md", "results/README.md", "results/SUMMARY.md")) if n_resolved == len(f["head_pairs"]) else ("never", ()),
        (f"({e[d3]['private']['headline']['point']:.3f}, against {e['d3_9b-q8_0']['private']['headline']['point']:.3f} for D3 9B)",
         ("README.md", "results/README.md", "results/SUMMARY.md")),
        (f"({e[d3]['metrics']['held_out_relation']['point']:.3f} and {e[d3]['metrics']['seen_relation']['point']:.3f}), but "
         f"{NUMBER_WORDS[seven]} of the {len(f['drops'])} models lose 0.05 or more",
         ("README.md", "results/README.md", "results/SUMMARY.md")),
        (f"Strict Q_N is 0 for {len(f['zeros'])} of the {len(f['drops'])} models ({f['odd']['id']} is the exception, at "
         f"{f['odd']['metrics']['q_n_strict']['point']:.1f})", ("README.md", "results/README.md", "results/SUMMARY.md")),
        (f"Local conflicts: {cons['conflicting']:,} of D3 27B's {cons['claim_bearing']:,} claim-bearing answers ({pct})",
         ("README.md", "results/README.md", "results/SUMMARY.md")),
        (f"{cons['conflicting_unsat']} of them belong to a minimal logically unsatisfiable set of at most "
         f"{NUMBER_WORDS[f['analysis']['consistency']['max_witness']]} answers",
         ("README.md", "results/README.md", "results/SUMMARY.md")),
        (f"Of the {f['named']:,} named relations, {f['depth']['0']['n'] + f['depth']['1']['n'] + f['depth']['2']['n']:,} are depth 0 to 2, "
         f"{f['depth']['3']['n']} are depth 3, and only {NUMBER_WORDS[f['depth']['4+']['n']]} are depth 4 or more, all in one league (test-L10)",
         ("README.md", "results/README.md", "results/SUMMARY.md")),
        (f"Pre-registered result: {vf['n_suspect']} of {vf['n_flagged_judged']} judged flagged items are suspect labels. "
         f"This result is fragile: {akn['n']} of the {vf['n_suspect']} are group items from one judge slice, where one judge gave the "
         "gold bounds under the kind label `none`, and one judge instance in that slice agreed with gold on only "
         f"{agree} of 100 items; with the discarded first answers for that slice the count is {discarded}. Under an exploratory "
         f"bound-based reading, {len(remaining)} candidates remain ({', '.join(remaining)}). None is a confirmed label error; no "
         "score changes.", ("README.md", "results/README.md", "results/SUMMARY.md")),
        (f"{by_group['all_wrong']['n']} all-wrong items, {by_group['inverted']['n']} sampled weak-over-strong items and "
         f"{by_group['control']['n']} unflagged controls",
         ("README.md", "results/README.md", "results/SUMMARY.md")),
        (f"0 of {vf['n_controls']} controls were labelled suspect (exact two-sided 95% interval 0% to {ub}%; one-sided upper bound "
         f"{ub1}%; only {vf['controls_groups']} controls are groups)",
         ("README.md", "results/README.md", "results/SUMMARY.md")) if vf["controls_suspect"] == 0 else ("never", ()),
        (f"covers {f['summary']['presentation']['n_models']} models (one seed, 20261101)",
         ("README.md", "results/README.md", "results/SUMMARY.md")),
        ("The first d3:9b repeat attempt ran on Ollama's Vulkan backend by mistake",
         ("README.md", "results/README.md", "results/SUMMARY.md")),
    ]


def _doc(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def facts():
    return _facts()


@pytest.mark.parametrize("path", DOCS, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_no_stale_sentence(path):
    text = _doc(path)
    for phrase in STALE:
        assert phrase not in text, f"{path.name} still says {phrase!r}"


def test_no_stale_sentence_in_the_bundle_note():
    lb = _json(ROOT / "results" / "v0.1.1" / "leaderboard.json")
    note = lb["resolved_comparisons_note"]
    for phrase in STALE:
        assert phrase not in note
    assert "Holm" in note and "analysis.json" in note


def test_tier_prose_numbers_match_the_committed_data(facts):
    for phrase, where in _phrases(facts):
        if where == ():
            pytest.fail(f"a number the prose quotes does not hold in the data: {phrase}")
        for name in where:
            doc = _doc(ROOT / name)
            assert phrase in doc, f"{name} does not contain: {phrase}"


def test_binding_sentence_is_derived_from_the_numbers(facts):
    ref = facts["ref"]
    lo, hi = ref["auroc"]["ci95"]
    sentence = (f"D3 27B's probabilities rank unresolved bindings above bound ones (AUROC {ref['auroc']['point']:.3f} "
                f"[{lo:.3f}, {hi:.3f}]), but its decisions flag {ref['flagged']} (MCC {ref['mcc']:.3f})")
    assert sentence in _doc(ROOT / "results" / "README.md")


def test_labels_for_baselines_and_the_ceiling_in_the_analysis_tables():
    text = _doc(ROOT / "results" / "README.md")
    section = text[text.index("<!-- analysis:start -->"):text.index("<!-- analysis:end -->")]
    assert "| gold_view (gold-backed ceiling) |" in section
    assert "| heuristic (baseline) |" in section and "| embedding (baseline) |" in section
    # the system_one models carry no label
    assert "| d3_27b-q8_0 |" in section and "d3_27b-q8_0 (baseline)" not in section


def test_exploratory_json_is_reproduced_by_the_script():
    """exploratory.py reads only the judging files and rebuilds exploratory.json byte for byte (the file's checks run here)."""
    audit = ROOT / "docs" / "audits" / "v0.1.1-item-audit"
    spec = importlib.util.spec_from_file_location("item_audit_exploratory", audit / "exploratory.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    doc = mod.build()
    assert doc["d_exploratory_bound_reading"]["remaining_suspects"] == ["g00453", "g00551", "r03602", "r04933"]
    assert mod.dumps(doc).encode("utf-8") == (audit / "exploratory.json").read_bytes()


def test_repeats_disclosure_and_shuffle_count_match_runs_json():
    runs = _json(ROOT / "results" / "v0.1.1" / "repeats" / "runs.json")
    shuffled = sorted({r["id"] for r in runs["runs"] if r["tag"] == "shuffle"})
    assert len(shuffled) == _facts()["summary"]["presentation"]["n_models"]
    for name in ("README.md", "results/README.md", "results/SUMMARY.md"):
        assert f"covers {len(shuffled)} models" in _doc(ROOT / name), name
