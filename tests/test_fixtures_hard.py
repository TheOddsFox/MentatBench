"""Tests for the Mentat-Hard generator (python -m mentat_bench.fixtures_hard).

Generation, language, split and schema tests need only the generator (full-size generation takes a few seconds).
The engine finalize and audit_brute tests need mb-engine and numpy and skip cleanly without them (MB_REQUIRE_ENGINE=1
turns the skip into a failure); the full-size engine and audit run is marked slow.
"""

import json
import re
from collections import Counter

import pytest

from mentat_bench import engine, fixtures_hard as fh, ir

needs_engine = pytest.mark.skipif(not engine.engine_available(),
                                  reason="mb-engine not built (set MB_ENGINE or build target/release)")
META_KEYS = {"id", "market", "league", "kind", "team", "t", "subgroup", "class", "family", "test_only", "paraphrase_of", "gloss"}
FILES = ("corpus.jsonl", "queries.jsonl", "fixture.json", "gold/bindings.jsonl", "gold/theory.json",
         "gold/provenance.jsonl", "gold/meta.jsonl", "gold/world.json", "gold/query_design.jsonl")
SMALL = 3


def _tree(root):
    return {name: (root / name).read_bytes() for name in FILES}


def _rows(path):
    return list(ir.read_jsonl(path))


@pytest.fixture(scope="module")
def full(tmp_path_factory):
    """Full-size dev and test splits (generation only)."""
    root = tmp_path_factory.mktemp("hard")
    for split in ("dev", "test"):
        fh.gen(root / split, split)
    return root


def test_same_args_give_same_bytes(tmp_path):
    fh.gen(tmp_path / "a", "dev", leagues=SMALL)
    fh.gen(tmp_path / "b", "dev", leagues=SMALL)
    assert _tree(tmp_path / "a") == _tree(tmp_path / "b")
    fh.gen(tmp_path / "c", "dev", seed=7, leagues=SMALL)
    # ids are salted with the seed, so compare the drawn worlds, not the bytes
    worlds = [[(lg["teams"], lg["scores"], lg["rules"]) for lg in ir.read_json(tmp_path / d / "gold" / "world.json")["leagues"]]
              for d in ("a", "c")]
    assert all(x != y for x, y in zip(*worlds))


def test_dev_and_test_share_no_ids_classes_or_names(full):
    seen = {}
    for split in ("dev", "test"):
        meta = _rows(full / split / "gold" / "meta.jsonl")
        world = ir.read_json(full / split / "gold" / "world.json")
        seen[split] = {
            "outcome": {m["id"] for m in meta}, "market": {m["market"] for m in meta}, "class": {m["class"] for m in meta},
            "team": {t for lg in world["leagues"] for t in lg["teams"]}, "league": {lg["name"] for lg in world["leagues"]},
            "pool": set(fh.name_pools(split, fh.SEEDS[split])[0]),
        }
    for key in seen["dev"]:
        assert not seen["dev"][key] & seen["test"][key], key


def test_families_dev_never_renders_held_out_and_test_uses_all(full):
    dev = Counter(m["family"] for m in _rows(full / "dev" / "gold" / "meta.jsonl"))
    test = Counter(m["family"] for m in _rows(full / "test" / "gold" / "meta.jsonl"))
    assert max(dev) <= fh.DEV_FAMILIES and set(dev) == set(range(1, 21))
    assert set(test) == set(range(1, 31))
    share = sum(v for f, v in test.items() if f > fh.DEV_FAMILIES) / sum(test.values())
    assert 0.28 <= share <= 0.39, share
    for m in _rows(full / "test" / "gold" / "meta.jsonl"):
        assert m["test_only"] == (m["family"] > fh.DEV_FAMILIES)


def test_held_out_wording_is_disjoint_from_dev_wording():
    for kind, (dev, held) in fh.WORDING.items():
        assert len(dev) == 10 and len(held) == 5 and not set(dev) & set(held), kind
        assert not set(fh.TITLES.get(kind, ((), ()))[0]) & set(fh.TITLES.get(kind, ((), ()))[1]), kind


def test_every_class_is_in_a_nontrivial_constraint(full):
    for split in ("dev", "test"):
        theory = ir.read_json(full / split / "gold" / "theory.json")
        linked = set()
        for c in theory["constraints"]:
            names = ir.expr_refs(c["expr"])
            if len(names) >= 2:
                linked |= names
        assert set(theory["aliases"]) <= linked


def test_meta_world_and_bindings_follow_the_contract(full):
    for split in ("dev", "test"):
        gold = full / split / "gold"
        meta = _rows(gold / "meta.jsonl")
        corpus = _rows(full / split / "corpus.jsonl")
        binds = {b["id"]: b for b in _rows(gold / "bindings.jsonl")}
        assert [m["id"] for m in meta] == [c["id"] for c in corpus] == list(binds)
        markets = {m["market"]: m for m in meta}
        for m in meta:
            assert set(m) == META_KEYS
            assert m["kind"] in ("thr", "win", "runner", "qual", "seed", "dated")
            assert (m["t"] is not None) == (m["kind"] == "thr") or m["kind"] == "dated"
            assert (m["subgroup"] is not None) == (m["kind"] == "seed")
            b = binds[m["id"]]
            if m["kind"] == "dated":
                assert b["status"] == "unresolved" and b["missing"] in (["no_date"], ["ambiguous_scope"])
            else:
                assert b["status"] == "bound" and b["class"] == m["class"]
            if m["paraphrase_of"] is not None:
                first = markets[m["paraphrase_of"]]
                assert first["class"] == m["class"] and first["family"] != m["family"]
        for c in corpus:
            assert set(c) - {"context"} == {"id", "text", "label", "event_title", "controlled"} and c["controlled"] is True
            assert c["context"]  # v3: every outcome carries its league's rule card
        world = ir.read_json(gold / "world.json")
        assert world["split"] == split and len(world["leagues"]) == fh.LEAGUES
        for lg in world["leagues"]:
            assert {"teams", "L", "scores", "rules", "subgroups", "paraphrase_pairs"} <= set(lg)
            assert 4 <= len(lg["teams"]) <= 6 and 5 <= lg["L"] <= 7
            assert all(0 <= lg["scores"][t] <= lg["L"] for t in lg["teams"])
            assert {1, 2, 5, 7, 8} <= {r["rule"] for r in lg["rules"]} and len(lg["subgroups"]) == fh.SUBGROUPS
            props = [p for r in lg["rules"] if r["rule"] == 8 for p in r["props"]]
            assert 2 <= len(props) <= 3 and all(isinstance(p["negated"], bool) for p in props)  # rule 8 is always on
            for g in lg["subgroups"]:
                assert {"id", "members", "named", "title"} <= set(g)
        assert {r["rule"] for lg in world["leagues"] for r in lg["rules"]} == set(range(1, 9))
        fx = ir.read_json(full / split / "fixture.json")
        assert fx["split"] == split and fx["outcomes"] == len(meta) and fx["markets"] == len(markets)
        assert not {"classes", "gold_constraints", "unresolved_outcomes", "interpretations"} & set(fx)  # gold-derived
        assert all(0 <= lg["fixed_share"] <= fh.MAX_FIXED for lg in world["leagues"])


def test_sizes_and_paraphrase_share(full):
    for split in ("dev", "test"):
        fx = ir.read_json(full / split / "fixture.json")
        assert 1200 <= fx["markets"] <= 1800 and fx["outcomes"] == 2 * fx["markets"]  # v3: glossed markets add ~175 per split
        meta = _rows(full / split / "gold" / "meta.jsonl")
        classes = {}
        for m in meta:
            if m["kind"] != "dated":
                classes.setdefault(m["league"], set()).add(m["class"])
        assert max(len(c) for c in classes.values()) <= 70  # one component per league, at most ~60-70 classes
        markets = Counter(m["class"] for m in meta if m["kind"] != "dated" and m["id"].endswith(":1") and not m["gloss"])
        per_class = Counter(markets.values())
        n_classes = len(ir.read_json(full / split / "gold" / "theory.json")["aliases"])
        assert 0.13 <= (per_class[2] + per_class[3]) / n_classes <= 0.21 and 0.13 <= per_class[3] / n_classes <= 0.21
        glossed = [m for m in meta if m["gloss"] and m["id"].endswith(":1")]  # v3: glossed markets of threshold and qualify classes
        assert glossed and {m["kind"] for m in glossed} <= {"thr", "qual"} and all(m["paraphrase_of"] for m in glossed)
        markets = Counter(m["class"] for m in meta if m["kind"] != "dated" and m["id"].endswith(":1"))  # all, for the pairs
        styles = {}
        for m in meta:
            if m["kind"] != "dated" and m["id"].endswith(":1"):
                style = fh.family_style(m["family"])
                styles.setdefault(m["class"], []).append((style[0], style[2]))
        assert all(len(set(s)) == len(s) for s in styles.values())  # every market of a class in its own style
        binds = _rows(full / split / "gold" / "bindings.jsonl")
        same = Counter((b["class"], b["negated"]) for b in binds if b["status"] == "bound")
        equiv = sum(n * (n - 1) // 2 for n in same.values())
        assert equiv >= 800, equiv  # same-meaning outcome pairs across a class's markets (unordered; ordered x2)
        world = ir.read_json(full / split / "gold" / "world.json")
        pairs = sum(len(lg["paraphrase_pairs"]) for lg in world["leagues"])
        assert pairs == sum(n * (n - 1) // 2 for n in markets.values())


def test_paraphrase_share_is_a_knob(tmp_path):
    res = fh.gen(tmp_path / "none", "dev", leagues=2, paraphrase_share=0.0, third_share=0.0)
    glossed = res["stats"]["glossed_markets"]  # each glossed market is a paraphrase of its class's one market
    assert res["stats"]["paraphrase_pairs"] == glossed and glossed > 0
    assert res["markets"] == res["classes"] + glossed + res["unresolved_outcomes"] // 2
    with pytest.raises(ValueError):
        fh.gen(tmp_path / "bad", "dev", leagues=1, paraphrase_share=0.1, third_share=0.2)


def test_questions_are_short_and_implicit_groups_are_unnamed(full):
    for split in ("dev", "test"):
        meta = {m["id"]: m for m in _rows(full / split / "gold" / "meta.jsonl")}
        world = ir.read_json(full / split / "gold" / "world.json")
        named = {g["id"]: g for lg in world["leagues"] for g in lg["subgroups"]}
        for c in _rows(full / split / "corpus.jsonl"):
            assert len(c["text"]) <= fh.MAX_QUESTION and " 1 points" not in c["text"]
            g = meta[c["id"]]["subgroup"]
            if g is not None:
                grp = named[g]
                assert ("Group " in c["text"] or "Group " in c["event_title"]) == grp["named"]
                if not grp["named"]:
                    assert grp["theme"] not in c["text"] and grp["theme"] not in c["event_title"]
        flags = [g["named"] for g in named.values()]
        assert 0.3 <= flags.count(True) / len(flags) <= 0.7  # groups alternate named / implicit within a league


def test_exam_mix(full):
    for split in ("dev", "test"):
        queries = _rows(full / split / "queries.jsonl")
        design = _rows(full / split / "gold" / "query_design.jsonl")
        assert Counter(q["family"] for q in queries) == Counter(fh.EXAM)
        tags = Counter((d["family"], d["tags"][0]) for d in design)
        assert tags[("feasibility", "unsat")] == 20 and tags[("feasibility", "sat")] == 20
        assert tags[("feasibility", "unresolved")] == 10
        assert tags[("partition", "true")] == 16 and tags[("partition", "false")] == 16  # the documented 4:4:2 mix
        assert tags[("partition", "unresolved")] == 8
        for q in queries:
            if q["family"] == "closure":
                assert 8 <= len(q["hyp"]) <= 16
        for d in design:
            if d["family"] == "closure":
                assert len(set(d["tags"]) - {"joint"}) >= 2  # premise categories (count blocks, score pins)
        assert {"named", "implicit"} <= {t for d in design if d["family"] == "partition" for t in d["tags"]}
        assert len({d["league"] for d in design}) == fh.LEAGUES


def _runs_z(seq):
    """Wald-Wolfowitz runs test z-score of a binary sequence (0 when one value is absent)."""
    n1, n2 = sum(seq), len(seq) - sum(seq)
    if not n1 or not n2:
        return 0.0
    runs = 1 + sum(a != b for a, b in zip(seq, seq[1:]))
    mean = 2 * n1 * n2 / (n1 + n2) + 1
    var = 2 * n1 * n2 * (2 * n1 * n2 - n1 - n2) / ((n1 + n2) ** 2 * (n1 + n2 - 1))
    return (runs - mean) / var ** 0.5


def test_exam_order_does_not_reveal_answers(full):
    for split in ("dev", "test"):
        design = _rows(full / split / "gold" / "query_design.jsonl")
        assert [d["qid"] for d in design] == [q["qid"] for q in _rows(full / split / "queries.jsonl")]
        for family in ("feasibility", "partition", "selection"):
            rows = [d for d in design if d["family"] == family]
            assert [d["qid"] for d in rows] == [f"{family}-{i:03d}" for i in range(1, len(rows) + 1)]
            seq = [d["tags"][0] for d in rows]
            for period in range(1, 11):  # no fixed cycle
                assert any(a != b for a, b in zip(seq, seq[period:])), (split, family, period)
            for value in set(seq):
                assert abs(_runs_z([x == value for x in seq])) < 3, (split, family, value)


def test_partition_claim_type_does_not_predict_the_answer(full):
    for split in ("dev", "test"):
        expect = {d["qid"]: (d["expect"] or {}).get("decision") for d in _rows(full / split / "gold" / "query_design.jsonl")}
        by_claim: dict[str, Counter] = {}
        for q in _rows(full / split / "queries.jsonl"):
            if q["family"] == "partition":
                by_claim.setdefault(q["claim"], Counter())[expect[q["qid"]]] += 1
        assert set(by_claim) == {"exclusive", "exhaustive", "partition"}
        for claim, cnt in by_claim.items():
            assert abs(cnt["true"] - cnt["false"]) <= 1 and cnt["UNRESOLVED"] >= 1, (split, claim, cnt)
        # the claim-only rule (majority answer of the claim type) on the decided items
        hits = sum(max(c["true"], c["false"]) for c in by_claim.values())
        total = sum(c["true"] + c["false"] for c in by_claim.values())
        assert hits / total <= 0.6, (split, hits / total)


def test_feasibility_proposition_kinds_do_not_predict_the_answer(full):
    """Leave-one-out classifiers on the kinds the questions name (easy to read off the wording): the exact kind
    multiset, and the set of kinds present, predict the answer no better than the majority answer + 0.10."""
    for split in ("dev", "test"):
        meta = {m["id"]: m for m in _rows(full / split / "gold" / "meta.jsonl")}
        expect = {d["qid"]: (d["expect"] or {}).get("decision") for d in _rows(full / split / "gold" / "query_design.jsonl")}
        rows = []
        for q in _rows(full / split / "queries.jsonl"):
            if q["family"] == "feasibility":
                kinds = Counter(meta[e["id"]]["kind"] for e in q["assume"])
                rows.append((tuple(sorted(kinds.items())), frozenset(kinds), expect[q["qid"]]))
        majority = max(Counter(r[2] for r in rows).values()) / len(rows)
        for key in (0, 1):
            hits = 0
            for i, r in enumerate(rows):
                overall = Counter(o[2] for j, o in enumerate(rows) if j != i)
                same = Counter(o[2] for j, o in enumerate(rows) if j != i and o[key] == r[key]) or overall
                hits += max(same, key=lambda lab: (same[lab], overall[lab])) == r[2]
            assert hits / len(rows) <= majority + 0.10, (split, key, hits / len(rows), majority)


def test_feasibility_size_and_category_count_do_not_predict_the_answer(full):
    cat = {"thr": "score", "win": "placement", "runner": "placement", "qual": "qualify", "seed": "overlap", "dated": "dated"}
    for split in ("dev", "test"):
        meta = {m["id"]: m for m in _rows(full / split / "gold" / "meta.jsonl")}
        expect = {d["qid"]: d["expect"]["decision"] for d in _rows(full / split / "gold" / "query_design.jsonl")
                  if d["family"] == "feasibility"}
        rows = []
        for q in _rows(full / split / "queries.jsonl"):
            if q["family"] == "feasibility" and expect[q["qid"]] in ("SAT", "UNSAT"):
                ids = [e["id"] for e in q["assume"]]
                rows.append((len(ids), len({cat[meta[i]["kind"]] for i in ids}), expect[q["qid"]]))
        rule = sum(("UNSAT" if c == 1 or n <= 3 else "SAT") == d for n, c, d in rows) / len(rows)
        assert abs(rule - 0.5) <= 0.15, (split, rule)  # "one category or <= 3 literals => UNSAT" is near chance
        best = max(sum(((n <= k) == below) == (d == "UNSAT") for n, _, d in rows)
                   for k in range(1, 15) for below in (True, False)) / len(rows)
        assert best <= 0.70, (split, best)  # no size threshold separates SAT from UNSAT
        # "contains an ambiguous outcome => UNRESOLVED": among such items the readings agree as often as not
        amb = [expect[q["qid"]] for q in _rows(full / split / "queries.jsonl")
               if q["family"] == "feasibility" and any(meta[e["id"]]["kind"] == "dated" for e in q["assume"])]
        share = sum(d == "UNRESOLVED" for d in amb) / len(amb)
        assert share <= 0.55 and {"SAT", "UNSAT"} <= set(amb), (split, share)


def test_a_league_over_the_fixed_cap_fails_loudly(tmp_path, monkeypatch):
    monkeypatch.setattr(fh, "TARGET_FIXED", -1.0)
    monkeypatch.setattr(fh, "MAX_FIXED", -1.0)  # no draw can fix at most a negative share
    monkeypatch.setattr(fh, "LEAGUE_TRIES", 2)
    with pytest.raises(ValueError, match="fixes at most"):
        fh.gen(tmp_path / "x", "dev", leagues=1)


def test_gold_world_satisfies_every_rule():
    pool, names = fh.name_pools("dev", fh.SEEDS["dev"])
    for li in range(4):
        lg = fh.League("dev", fh.SEEDS["dev"], li, names[li], pool)
        assert lg.check(lg.gold) and lg.bank[0] is lg.gold
        assert all(lg.check(w) for w in lg.bank)


def _finalize_and_audit(out):
    pytest.importorskip("numpy", reason="audit_brute needs numpy")
    res = fh.finalize(out)
    assert res["answers"] == len(_rows(out / "queries.jsonl"))
    rep = fh.audit(out)
    assert rep["failures"] == [], rep["failures"][:5]
    assert rep["summary"]["skipped"] == 0 and rep["summary"]["mismatch"] == 0
    return rep


@needs_engine
def test_no_split_has_a_constant_outcome(full):
    """Version 2 forces nothing: the exact engine finds no outcome that the base theory fixes (hard_suite.constants)."""
    from mentat_bench import hard_suite

    for split in ("dev", "test"):
        fx = hard_suite.Fixture(full / split)
        assert hard_suite.constants(fx, hard_suite._Engine(fx)) == set(), split
        world = ir.read_json(full / split / "gold" / "world.json")
        assert all(lg["fixed_share"] == 0 for lg in world["leagues"])
        assert {r["rule"] for lg in world["leagues"] for r in lg["rules"]} == set(range(1, 9))


@needs_engine
def test_small_split_finalizes_and_audits_with_zero_skips(tmp_path):
    for split in ("dev", "test"):
        out = tmp_path / split
        fh.gen(out, split, leagues=SMALL)
        rep = _finalize_and_audit(out)
        assert rep["components"] == SMALL
        summary = ir.read_json(out / "gold" / "summary.json")
        assert set(summary) >= {"answers", "premises", "verdict_reasons", "counts"}
        assert set(summary["counts"]) == {"classes", "gold_constraints", "unresolved_outcomes", "interpretations"}


@needs_engine
def test_cli_audit_exit_code(tmp_path):
    pytest.importorskip("numpy", reason="audit_brute needs numpy")
    out = tmp_path / "dev"
    assert fh.main(["gen", "--split", "dev", "--leagues", "1", "--out", str(out)]) == 0
    assert fh.main(["finalize", "--out", str(out)]) == 0
    assert fh.main(["audit", "--out", str(out)]) == 0
    answers = out / "gold" / "answers.jsonl"
    rows = _rows(answers)
    bad = next(r for r in rows if r.get("decision") in ("SAT", "UNSAT", "true", "false"))
    bad["decision"] = {"SAT": "UNSAT", "UNSAT": "SAT", "true": "false", "false": "true"}[bad["decision"]]
    answers.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    assert fh.main(["audit", "--out", str(out)]) == 1


def _fixed_share(out, tmp):
    """Share of outcomes whose value the base theory fixes (engine closure with an empty hypothesis)."""
    ir.write_jsonl(tmp / "q.jsonl", [{"qid": "base", "family": "closure", "hyp": [{"op": "true"}]}])
    engine.eval_submission(out / "gold", tmp / "q.jsonl", tmp / "a.jsonl")
    labels = next(ir.read_jsonl(tmp / "a.jsonl"))["labels"]
    return (len(labels["ENTAILED"]) + len(labels["REFUTED"])) / sum(len(v) for v in labels.values())


@needs_engine
def test_finalize_rolls_back_on_a_failed_expectation(tmp_path):
    out = tmp_path / "dev"
    fh.gen(out, "dev", leagues=1)
    design = out / "gold" / "query_design.jsonl"
    rows = _rows(design)
    row = next(r for r in rows if r["family"] == "feasibility")
    row["expect"]["decision"] = "UNRESOLVED" if row["expect"]["decision"] != "UNRESOLVED" else "SAT"
    ir.write_jsonl(design, rows)
    before = design.read_bytes()
    with pytest.raises(RuntimeError, match="exam checks failed"):
        fh.finalize(out)
    assert design.read_bytes() == before
    assert not any((out / "gold" / n).exists() for n in ("answers.jsonl", "verdicts.jsonl", "summary.json"))


@needs_engine
def test_generate_builds_the_complete_fixture_into_a_new_directory(tmp_path):
    pytest.importorskip("numpy", reason="audit_brute needs numpy")
    out = tmp_path / "fx"
    small = {"leagues": 1, "relations": 300, "groups": 40}  # the full suite (6,000 / 1,000) is for full splits
    fh.generate(out, name="mh_test_name", **small)
    files = {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()}
    assert files == set(fh.REQUIRED_FILES)
    fx = ir.read_json(out / "fixture.json")
    assert fx["name"] == "mh_test_name" and fx["seed"] == fh.SEEDS["dev"] == fh.SEED
    fh.generate(tmp_path / "fx_test", name="t", split="test", **small)  # the seed default is per split
    assert ir.read_json(tmp_path / "fx_test" / "fixture.json")["seed"] == fh.SEEDS["test"]
    with pytest.raises(FileExistsError):
        fh.generate(out, name="again", **small)
    with pytest.raises(RuntimeError):
        fh.generate(tmp_path / "bad", name="bad", leagues=0)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["fx", "fx_test"]  # no scratch directory is left behind


@needs_engine
def test_generate_refuses_a_split_with_an_uncovered_row(tmp_path, monkeypatch):
    """generate's self-check: a suite with an uncovered rule row (or a constant) leaves no directory behind."""
    pytest.importorskip("numpy", reason="audit_brute needs numpy")
    from mentat_bench import hard_suite

    real_build = hard_suite.build

    def build_with_a_gap(*args, **kwargs):
        res = real_build(*args, **kwargs)
        res["uncovered"] = 1
        return res

    monkeypatch.setattr(hard_suite, "build", build_with_a_gap)
    with pytest.raises(RuntimeError, match="uncovered"):
        fh.generate(tmp_path / "gap", name="gap", leagues=1, relations=300, groups=40)
    assert not (tmp_path / "gap").exists()


@pytest.mark.slow
@needs_engine
def test_full_splits_finalize_and_audit(full, tmp_path):
    import statistics

    for split in ("dev", "test"):
        rep = _finalize_and_audit(full / split)
        assert rep["components"] == fh.LEAGUES
        assert _fixed_share(full / split, tmp_path) <= 0.09
        material = [d["premises"] for d in _rows(full / split / "gold" / "query_design.jsonl") if d["family"] == "closure"]
        assert statistics.median(material) >= 6 and sum(m >= 8 for m in material) >= 0.25 * len(material)
        equiv, exactly_one = _suite_supply(full / split)
        assert equiv >= 800 and exactly_one >= 140, (split, equiv, exactly_one)


@pytest.mark.slow
@needs_engine
@pytest.mark.parametrize("split", ("dev", "test"))
def test_reading_strata_match_on_both_splits(split, tmp_path):
    """Explicit v3 reading-stratum pins: every ambiguous proposition has a decoy proposition on both splits, and the option
    wording families of the decoy stratum match the ambiguous one (decoy_shortfall 0, family_residual 0). The counts are the
    build's (docs/mentat_hard.md, section 3); a change to the worlds that moves them must update this test on purpose."""
    pytest.importorskip("numpy", reason="audit_brute needs numpy")
    from mentat_bench import hard_suite

    fh.gen(tmp_path / split, split)
    readings = hard_suite.build(tmp_path / split)["readings"]
    assert readings["decoy_shortfall"] == 0 and readings["family_residual"] == 0, split
    assert (readings["ambiguous"], readings["decoys"]) == {"dev": (100, 100), "test": (92, 92)}[split], split


@pytest.fixture(scope="module")
def leagues_by_split():
    """Every league of both splits, with its markets (which set its rule card and the corpus contexts)."""
    out = {}
    for split in ("dev", "test"):
        seed = fh.SEEDS[split]
        pool, names = fh.name_pools(split, seed)
        lgs = [fh._league(split, seed, li, names[li], pool) for li in range(fh.LEAGUES)]
        for lg in lgs:
            lg.markets = fh._markets(lg)
        out[split] = lgs
    return out


def _card_input(lg):
    return {k: v for k, v in lg.world_json().items() if k not in fh.CARD_HIDDEN}


def _atom(lg, e):
    """A literal of a constraint in league terms: (kind, team, t, subgroup, negated)."""
    neg = e["op"] == "not"
    if neg:
        e = e["a"]
    c = lg.classes[e["name"]]
    return (c["kind"], c["team"], c["t"], c["subgroup"], neg)


def _canon(lg, e):
    """A constraint expression in league terms: ("implies", a, b) or (op, k, sorted literals)."""
    if e["op"] == "implies":
        return ("implies", _atom(lg, e["a"]), _atom(lg, e["b"]))
    return (e["op"], e["k"], tuple(sorted(_atom(lg, a) for a in e["args"])))


def _expected(lg):
    """The constraints each card sentence must state, rebuilt from the league's own fields (not from the renderer), keyed by
    the sentence tag of card_sentences. Each value is the sorted list of the constraints' canonical forms."""
    T = lambda x, t: ("thr", x, t, None, False)  # noqa: E731
    W = lambda x: ("win", x, None, None, False)  # noqa: E731
    S = lambda x: ("runner", x, None, None, False)  # noqa: E731
    Q = lambda x, neg=False: ("qual", x, None, None, neg)  # noqa: E731
    teams, L = lg.teams, lg.L
    exp: dict[str, list] = {"header": []}
    exp["qualify"] = [("exactly", lg.k, tuple(sorted(Q(x) for x in teams)))] + \
        [("implies", Q(x), T(x, lg.q)) for x in teams]
    placement = [("exactly", 1, tuple(sorted(W(x) for x in teams))), ("exactly", 1, tuple(sorted(S(x) for x in teams)))]
    placement += [e for x in teams for e in (("atmost", 1, tuple(sorted([W(x), S(x)]))), ("implies", W(x), Q(x)),
                                             ("implies", S(x), Q(x)))]
    exp["placement"] = placement
    if lg.k == 2:
        exp["finalists"] = [("exactly", 1, tuple(sorted([W(x), S(x), Q(x, True)]))) for x in teams]
    for x, y in lg.conditionals:
        exp[f"conditional:{x}:{y}"] = [("implies", W(x), S(y))]
    if "count" in [r["type"] for r in lg.rules]:
        c = lg.count
        exp["count:atleast"] = [("atleast", c["atleast"]["k"], tuple(sorted(T(x, c["atleast"]["t"]) for x in teams)))]
        exp["count:atmost"] = [("atmost", c["atmost"]["k"], tuple(sorted(T(x, c["atmost"]["t"]) for x in teams)))]
    for x, y in lg.pairs:
        exp[f"outscores:{x}:{y}"] = [("implies", T(y, t), T(x, t + 1)) for t in range(1, L)]
    if lg.chain:
        for x, y in zip(lg.chain, lg.chain[1:]):
            exp[f"link:{x}:{y}"] = [("implies", T(y, t), T(x, t)) for t in range(1, L + 1)]
        exp["winner-bound"] = [("implies", W(x), T(x, lg.a)) for x in teams]
    multi = [x for x in teams if sum(x in g["members"] for g in lg.groups) > 1]
    for g in lg.groups:
        seed = lambda x: ("seed", x, None, g["id"], False)  # noqa: E731
        exp[f"group:{g['id']}"] = [("exactly", 1, tuple(sorted(seed(x) for x in g["members"])))] + \
            [("implies", seed(x), T(x, lg.seed_min[g["id"]])) for x in g["members"]]
    if multi:
        exp["group-atmost"] = [("atmost", 1, tuple(sorted(("seed", x, None, g["id"], False)
                                                             for g in lg.groups if x in g["members"])))
                               for x in multi]
    return exp


def test_rule_card_states_every_rule_2_to_7_constraint_exactly_once(leagues_by_split):
    for lgs in leagues_by_split.values():
        for lg in lgs:
            want = [i for i, c in enumerate(lg.cons) if 2 <= c["rule"] <= 7]
            assert lg.card_covered == want, lg.id  # sorted, and every index once: the sentences partition the constraints


def test_each_card_sentence_states_the_constraints_of_its_template(leagues_by_split):
    """The stronger check: every tagged sentence's constraints (the cons at its indices, read back in league terms) are
    exactly the constraints its template claims, with the template's arguments (members, chain links, the winner bound's a,
    k, q, the count's k and t, the outscores range), and every rule 2-7 sentence has a tag."""
    for lgs in leagues_by_split.values():
        for lg in lgs:
            sents = fh.card_sentences(_card_input(lg))
            exp = _expected(lg)
            assert {tag for tag, _, _ in sents if not tag.startswith("reading:")} == set(exp), lg.id
            for tag, _, idx in sents:
                if tag.startswith("reading:"):  # rule 8: no constraint group
                    assert idx == [], (lg.id, tag)
                    continue
                got = sorted(_canon(lg, lg.cons[i]["expr"]) for i in idx)
                assert got == sorted(exp[tag]), (lg.id, tag)


def test_rule_card_reads_the_rules_only(leagues_by_split):
    lg = leagues_by_split["dev"][0]
    world = lg.world_json()
    with pytest.raises(ValueError):  # the gold keys are refused, so the renderer cannot read them
        fh.render_card(world)
    rules_only = _card_input(lg)
    assert set(fh.CARD_HIDDEN).isdisjoint(rules_only) and fh.render_card(rules_only)[0] == lg.card


def test_rule_card_names_every_scope_phrase_the_markets_use(leagues_by_split):
    """Implicit markets use any of a group's three scope phrases: each phrase that a league's question text contains is in
    the league's card, and every group sentence lists all three phrases of its theme."""
    for lgs in leagues_by_split.values():
        for lg in lgs:
            texts = [r[0]["text"] for m in lg.markets for r in m["rows"]]
            used = {p for phrases in fh.THEME_PHRASES.values() for p in phrases if any(p in t for t in texts)}
            assert used and all(p in lg.card for p in used), lg.id
            for g in lg.groups:
                assert all(p in lg.card for p in fh.THEME_PHRASES[g["theme"]]), (lg.id, g["theme"])


def test_dev_cards_use_no_held_out_only_wording(leagues_by_split):
    """Held-out families (21-30) use wording that dev never does. A dev card must not contain a word that occurs only in the
    held-out wording and titles, apart from the function and unit words listed in ALLOWED (the card's "as many ... as", the
    point of the rule-2 and winner bounds, and "one" of "exactly one")."""
    words = lambda text: set(re.findall(r"[a-z]+", re.sub(r"\{[a-z]+\}", " ", text).lower()))  # noqa: E731
    dev_words, held_words = set(), set()
    for dev_lists, held_lists in list(fh.WORDING.values()) + list(fh.TITLES.values()):
        for text in dev_lists:
            dev_words |= words(text)
        for text in held_lists:
            held_words |= words(text)
    ALLOWED = {"as", "one", "point"}
    held_only = held_words - dev_words - ALLOWED
    for lg in leagues_by_split["dev"]:
        leaked = words(lg.card) & held_only
        assert not leaked, (lg.id, sorted(leaked))


def test_rule_card_avoids_leakage_words_and_is_short(leagues_by_split):
    deny = re.compile(r"\b(gold|answer|decision|sat|unsat|premises)\b", re.IGNORECASE)  # as test_leakage.DENY_WORDS
    cards = [lg.card for lgs in leagues_by_split.values() for lg in lgs]
    assert not [c for c in cards if deny.search(c)]
    assert max(len(c) for c in cards) <= fh.CARD_MAX == 2000  # the corpus cap


def test_ambiguous_readings_disagree_in_the_gold_world(leagues_by_split):
    for lgs in leagues_by_split.values():
        for lg in lgs:
            vals = lg.values(lg.gold)
            assert len({lg.classes[p["alt"]]["t"] for p in lg.props if p["type"] == "tophalf"}) <= 1, lg.id  # one M
            for p in lg.props:
                assert vals[p["base"]] != vals[p["alt"]], (lg.id, p["type"])
                if p["type"] == "break":  # the literal reading is one step above the final points, the alt is the final points
                    x = p["team"]
                    assert p["t"] == lg.scores[x] + 1 and lg.classes[p["alt"]]["t"] == lg.scores[x], (lg.id, x)


def test_each_outcome_carries_the_rule_card_of_its_league(full):
    for split in ("dev", "test"):
        worlds = {lg["id"]: lg for lg in ir.read_json(full / split / "gold" / "world.json")["leagues"]}
        cards = {lid: fh.render_card({k: v for k, v in lg.items() if k not in fh.CARD_HIDDEN})[0] for lid, lg in worlds.items()}
        league = {m["id"]: m["league"] for m in _rows(full / split / "gold" / "meta.jsonl")}
        for row in _rows(full / split / "corpus.jsonl"):
            assert row["context"] == cards[league[row["id"]]], row["id"]


def _suite_supply(out):
    """(same-meaning outcome pairs, exactly-one constraints) over outcomes the base theory leaves free: what the
    curated suite can draw EQUIV pairs and exactly_one sets from."""
    from mentat_bench import hard_suite

    fx = hard_suite.Fixture(out)
    const = hard_suite.constants(fx, hard_suite._Engine(fx))
    by = Counter()
    live_classes = set()
    for o in fx.ids:
        b = fx.bind[o]
        if b["status"] == "bound" and o not in const:
            by[(b["class"], b["negated"])] += 1
            live_classes.add(b["class"])
    exactly_one = 0
    for c in fx.theory["constraints"]:
        e = c["expr"]
        if e["op"] == "exactly" and e["k"] == 1:
            names = [ir.expr_refs(a).pop() for a in e["args"]]
            exactly_one += all(n in live_classes for n in names)
    return sum(n * (n - 1) // 2 for n in by.values()), exactly_one
