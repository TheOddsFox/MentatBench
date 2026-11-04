"""The transfer pilot (docs/audits/v0.2.0-transfer-pilot/), on synthetic data only: no network, and no question, label, title or slug that
appears in any snapshot. Nothing here writes into the repository or into mb-runs: every output goes to a temporary folder.

Covered: the English test; the thresholds (index numbers, dates, scores); the caps and dedupe; the pool's determinism and structural
types; the child's instruction cap (a relation the child would refuse is redrawn); mixed-group order; the published rows (hashes only);
the judge items (no event title); the slices; the rehydrate checks (texts, event titles, slices, the repository guard); the fetch paging
and manifest; the judge protocol (canonical answers, reason codes, the call record, pending calls, raw_missing, schema-error repeats,
re-derivation, the stdin hash, the freeze guard, the input hashes, the capture runner); and the analysis (consensus, the review sheet
recomputed from the consensus and the seeded draw, escalation, selection, the fixture guard, statistics, and a full synthetic pipeline)."""

import csv
import datetime
import hashlib
import json
import random
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "docs" / "audits" / "v0.2.0-transfer-pilot"
# Other audits ship modules with the same names (score, run_slice): import ours fresh and leave neither the modules nor the folder
# behind, so a later `import score` in another audit's script finds its own.
_SHARED = ("score", "run_slice", "build_pool", "rehydrate", "fetch", "analyse")
for _name in _SHARED:
    sys.modules.pop(_name, None)
sys.path.insert(0, str(AUDIT))
import analyse as AN  # noqa: E402
import build_pool as BP  # noqa: E402
import fetch as FE  # noqa: E402
import rehydrate as RH  # noqa: E402
import run_slice as RS  # noqa: E402
import score as SC  # noqa: E402

sys.path[:] = [p for p in sys.path if not p or Path(p).resolve() != AUDIT]
for _name in _SHARED:
    sys.modules.pop(_name, None)

SMALL_RELATIONS = {"same_event_alt": 4, "threshold_ladder": 3, "date_ladder": 2, "trivial": 2, "same_topic": 3,
                   "random_cross": 3}
SMALL_GROUPS = {"event_fields": 2, "ladder": 2, "mixed": 2, "decoy": 2}
CREATED = "2026-05-10T00:00:00Z"
MODELS = ["d3_27b-q8_0", "d3_9b-q8_0", "pplx-decider_27b", "kev_9b-bf16", "d3_4b-q8_0", "clef_27b-q8_0", "nimble-v3_9b-q8_0",
          "nimble_9b-bf16", "clef-flash_9b", "tev1_4b-q8_0", "kev_4b-q8_0", "d1_3b-q8_0", "d3_2b-q8_0", "kev_0.8b-q8_0",
          "tev1_0.8b-q8_0", "d1-omni_600m-q8_0", "laya_latest"]


def mk(mid, question, labels=("Yes", "No"), title=""):
    return {"id": str(mid), "question": question, "outcomes": list(labels), "groupItemTitle": title, "createdAt": CREATED,
            "closed": False}


def ev(eid, volume, markets, series=None, category="harbor", title="Title Harbor Event"):
    return {"id": str(eid), "slug": f"event-{eid}", "title": title, "createdAt": CREATED, "seriesSlug": series,
            "negRisk": False, "volume": float(volume), "tags": [{"label": category.title(), "slug": category}],
            "markets": markets}


LONG = "Will " + "x" * 240 + " happen?"


def synthetic_events():
    return [
        ev(101, 900, [mk(1011, "Will Alpha Vale reach 100 points by June 30?"),
                      mk(1012, "Will Alpha Vale reach 110 points by June 30?"),
                      mk(1013, "Will Alpha Vale reach 120 points by June 30?"),
                      mk(1014, "Will Alpha Vale finish top by June 30?")], series="s-a", title="Title Vale Season"),
        ev(102, 800, [mk(1021, "Will Beta Quay finish by May 31?"), mk(1022, "Will Beta Quay finish by December 31?")],
           series="s-a", title="Title Quay Season"),
        ev(103, 700, [mk(1031, "Round 1 total marks Over/Under 36.5?", ("Over", "Under")),
                      mk(1032, "Round 1 total marks Over/Under 45.5?", ("Over", "Under"))], series="s-a", title="Title Marks Event"),
        ev(104, 600, [mk(1041, "Will Omega Fen win the round?")], series="s-a", title="Title Fen Event"),
        ev(105, 500, [mk(1051, "Will Gamma Ork be the next Prime Minister?"), mk(1052, "Will Delta Ork be the next Prime Minister?"),
                      mk(1053, "¿Quién ganará el partido de la liga local este año?")], series="s-b", category="council",
           title="Title Ork Council"),
        ev(106, 400, [mk(1061, "Will Epsilon Lark win the election?"), mk(1062, LONG)], category="council", title="Title Lark Event"),
        ev(107, 300, [mk(1071, "Will Zinc Index be above 100,000 on May 15?"),
                      mk(1072, "Will Zinc Index be above 110,000 on May 15?")], category="index", title="Title Zinc Event"),
        ev(108, 200, [mk(1081, "Will Ether Ruin reach 5,000 by July 1?")], category="index", title="Title Ruin Event"),
        ev(109, 100, [mk(1091, "Will Epsilon Lark win the election?")], category="council", title="Title Lark Copy"),
        ev(110, 50, [mk(1101, "Will Zeta Mire launch by June?"), mk(1102, "Will Zeta Mire sell by June?", ("Yes", "No", "Maybe"))],
           series="s-c", category="tech", title="Title Mire Event"),
        ev(111, 40, [mk(1111, "Will Eta Mire launch by July?")], series="s-c", category="tech", title="Title Mire Two"),
    ]


def build(events=None):
    return BP.build_pool(events if events is not None else synthetic_events())


@pytest.fixture
def small_quotas(monkeypatch):
    monkeypatch.setattr(BP, "RELATION_QUOTA", dict(SMALL_RELATIONS))
    monkeypatch.setattr(BP, "GROUP_QUOTA", dict(SMALL_GROUPS))


@pytest.fixture
def result(small_quotas):
    return build()


def outcome_map(result):
    return {t["outcome"]: t for t in result["texts"]}


def mask_numbers(text):
    import re
    return re.sub(r"\d[\d,.]*", "#", text)


# ---- build_pool: the English test, the thresholds, the filters, the caps


def test_english_heuristic():
    assert BP.is_english("Will Quill Harbor win the final?")
    assert BP.is_english("Round 3 total marks Over/Under 39.5?")
    assert BP.is_english("Vexa Ostrum vs. Lorn: O/U 0.5")
    assert not BP.is_english("¿Quién ganará el partido de la liga local este año?")
    assert not BP.is_english("Пройдет ли матч между командами сегодня?")


def test_thresholds_need_the_number_in_the_question_outside_dates_scores_and_indices():
    value, template = BP.threshold_of({"groupItemTitle": "50", "question": "Will Vela cut rates by 50 basis points?"})
    assert value == 50.0
    assert template == BP.threshold_of({"groupItemTitle": "25", "question": "Will Vela cut rates by 25 basis points?"})[1]
    assert BP.threshold_of({"groupItemTitle": "2-1", "question": "Exact Score: Ostra 2 - 1 Lorn?"}) == (None, None)
    assert BP.threshold_of({"groupItemTitle": "30", "question": "Will Vela list by June 30?"}) == (None, None)  # a date
    assert BP.threshold_of({"groupItemTitle": "3", "question": "Will Ostra beat Player 3 in the final?"}) == (None, None)
    assert BP.threshold_of({"groupItemTitle": "16", "question": "Will Ostra win the Round of 16 match?"}) == (None, None)
    assert BP.threshold_of({"groupItemTitle": "1", "question": "Will Ostra beat the Q1 leader?"}) == (None, None)
    assert BP.threshold_of({"groupItemTitle": "3", "question": "Will Ostra finish 3rd in the table?"}) == (None, None)
    assert BP.threshold_of({"groupItemTitle": "", "question": "Will Zinc be above 100,000 on May 15?"})[0] == 100000.0
    assert BP.threshold_of({"groupItemTitle": "", "question": "Will Zinc be above 100,000 on May 15?"})[1] == \
        BP.threshold_of({"groupItemTitle": "", "question": "Will Zinc be above 110,000 on May 15?"})[1]


def test_period_markers_and_ordinals_after_a_number_make_it_an_index():
    def flags(question):
        return [BP._is_index_number(question, m.start(), m.end()) for m in BP.NUMBER.finditer(question)]
    assert flags("Will Epsilon exceed 500 in 1Q?") == [False, True]
    assert flags("Will Epsilon lead 2H by 4?") == [True, False]  # 2H is the period; 4 is the level
    assert flags("Will Eta win 1OT?") == [True]
    assert flags("Will Eta exceed 40 in 3P?") == [False, True]
    assert flags("Will Eta finish 2nd or above 40?") == [True, False]
    assert flags("Will Theta run 5h?") == [False]  # lowercase h is hours, not a period
    assert BP.threshold_of({"groupItemTitle": "1", "question": "Will Iota score in 1Q?"}) == (None, None)


def test_date_templates_need_three_words():
    assert BP.date_of({"question": "Will Beta Quay finish by May 31?"}) == ("may 31", "will beta quay finish by datemask")
    assert BP.date_of({"question": "Will Beta Quay finish by December 31?"})[1] == "will beta quay finish by datemask"
    assert BP.date_of({"question": "Will it rain? I may leave."}) == (None, None)
    assert BP.date_of({"question": "June 30 Lark?"}) == ("june 30", None)


def test_filters_drop_the_right_markets():
    assert BP.ineligible(mk(1, LONG)) == "question over 230 characters"
    assert BP.ineligible(mk(2, "¿Quién ganará el partido de la liga local este año?")) == "not English"
    assert BP.ineligible({"id": "3", "question": "Will it?", "outcomes": ["Yes"]}).startswith("outcomes")
    assert BP.ineligible({"id": "4", "question": "  ", "outcomes": ["Yes", "No"]}) == "no question"
    assert BP.ineligible(mk(5, "Will Zeta Mire launch by June?")) is None


def test_caps_hold_on_the_final_set_and_no_question_is_kept_twice():
    events = []
    for i in range(30):
        cat = "sports" if i < 12 else f"c{i % 7}"
        markets = [mk(10000 + i, f"Will Harbor {i} finish by June {i % 20 + 1}?")]
        if i in (25, 29):
            markets.append(mk(20000 + i, "Will Shared Lark win the cup?"))
        events.append(ev(i, 1000 - i, markets, series=f"s{i % 9}" if i % 3 else None, category=cat))
    stats = Counter()
    kept = BP.select_events(events, stats)
    series = Counter(e["series"] for e in kept if e["series"] is not None)
    assert max(series.values()) <= BP.SERIES_CAP
    cats = Counter(e["category"] for e in kept)
    assert max(cats.values()) <= BP.CATEGORY_SHARE * len(kept)
    questions = [BP.norm(m["question"]) for e in kept for m in e["markets"]]
    assert len(questions) == len(set(questions))
    assert stats["event dropped: series cap"] > 0


def test_cap_categories_drops_the_lowest_volume_of_the_largest():
    cats = ["sports"] * 5 + ["a", "b", "c"]
    events = [{"id": str(i), "volume": float(100 - i), "series": None, "category": cat, "markets": []} for i, cat in enumerate(cats)]
    stats = Counter()
    kept = BP.cap_categories(events, stats)
    assert [e["id"] for e in kept] == ["0", "5", "6", "7"]
    assert stats["event dropped: category cap"] == 4


def test_the_category_cap_refuses_to_empty_a_pool_with_fewer_than_four_categories():
    events = [{"id": str(i), "volume": float(10 - i), "series": None, "category": "a" if i < 2 else "b", "markets": []}
              for i in range(4)]
    with pytest.raises(ValueError, match="four categories"):
        BP.cap_categories(events, Counter())


def test_pool_builds_identical_bytes_twice_and_ignores_input_order(small_quotas):
    first, second = build(), build()
    assert first["pool_bytes"] == second["pool_bytes"]
    assert first["slices"] == second["slices"] and first["texts"] == second["texts"] and first["judge"] == second["judge"]
    events = synthetic_events()
    assert build(events)["pool_bytes"] == build(list(reversed(events)))["pool_bytes"]


def test_filters_and_dedupe_in_the_pool(result):
    texts = list(outcome_map(result).values())
    assert texts and all(len(t["question"]) <= BP.MAX_QUESTION for t in texts)
    assert not any("quién" in t["question"] for t in texts)
    by_question = {}
    for t in texts:
        by_question.setdefault(BP.norm(t["question"]), set()).add(t["market"])
    assert all(len(markets) == 1 for markets in by_question.values())
    epsilon = {t["market"] for t in texts if BP.norm(t["question"]) == "will epsilon lark win the election"}
    assert len(epsilon) == 1
    assert result["summary"]["stats"]["market dropped: not English"] >= 1


def test_structural_types_have_their_properties(result):
    texts = outcome_map(result)
    assert {r["structural_type"] for r in result["rows"]} == set(BP.RELATION_QUOTA) | set(BP.GROUP_QUOTA)
    for row in result["rows"]:
        if row["op"] == "decide_relation":
            a, b = texts[row["a"]], texts[row["b"]]
            kind = row["structural_type"]
            if kind == "trivial":
                assert a["market"] == b["market"] and a["label"] != b["label"]
            elif kind == "same_event_alt":
                assert a["event"] == b["event"] and a["market"] != b["market"]
            elif kind == "threshold_ladder":
                assert a["event"] == b["event"] and mask_numbers(a["question"]) == mask_numbers(b["question"])
            elif kind == "date_ladder":
                assert a["event"] == b["event"] and a["market"] != b["market"]
            else:
                assert a["market"] != b["market"]
        else:
            members = [texts[m] for m in row["members"]]
            assert len({m["market"] for m in members}) == len(members)
            if row["structural_type"] == "event_fields":
                assert len({m["event"] for m in members}) == 1
            if row["structural_type"] == "mixed":
                assert len({m["event"] for m in members}) == 2 and len({m["cluster"] for m in members}) == 1 and row["span"] is False


def test_the_child_would_refuse_a_relation_so_the_pool_redraws_it(small_quotas, monkeypatch):
    monkeypatch.setattr(BP.CHILD, "MAX_INSTRUCTION_CHARS", 105)  # the synthetic relations render at 100 to 110 characters
    result = build()
    stats = result["summary"]["stats"]
    rejected = [v for k, v in stats.items() if k.startswith("relation rejected")]
    assert rejected and rejected[0] > 0
    texts = outcome_map(result)
    for row in result["rows"]:
        if row["op"] == "decide_relation":
            a, b = texts[row["a"]], texts[row["b"]]
            length = len(BP.CHILD._relation_question({"id": a["outcome"], "question": a["question"], "label": a["label"]},
                                                     {"id": b["outcome"], "question": b["question"], "label": b["label"]})["instructions"])
            assert length <= 105


def test_index_numbers_are_not_thresholds_in_group_ladders_either(result):
    assert BP.threshold_of({"groupItemTitle": "", "question": "Will Ostra finish top in Map 2 of the series?"}) == (None, None)
    assert BP.threshold_of({"groupItemTitle": "", "question": "Will Ostra score over 20 points in Game 2?"})[0] == 20.0


def test_a_mixed_group_is_never_two_contiguous_blocks(small_quotas, monkeypatch):
    monkeypatch.setattr(BP, "GROUP_QUOTA", {"event_fields": 0, "ladder": 0, "mixed": 12, "decoy": 0})
    events = []
    for i in range(8):  # eight events in four series of two, each in its own category (the cap needs at least four categories)
        events.append(ev(300 + i, 900 - i, [mk(3000 + 10 * i + j, f"Will Ostra {i} win match {j}?") for j in range(4)],
                         series=f"s-mix-{i // 2}", category=f"cat{i}", title=f"Title Mix {i}"))
    result = build(events)
    texts = outcome_map(result)
    checked = 0
    for row in result["rows"]:
        if row["structural_type"] == "mixed" and len(row["members"]) >= 3:
            events_in_order = [texts[m]["event"] for m in row["members"]]
            runs = 1 + sum(events_in_order[i] != events_in_order[i - 1] for i in range(1, len(events_in_order)))
            assert runs != 2
            checked += 1
    assert checked >= 1


def test_the_alternating_fallback_has_three_runs():
    members = [{"event": "A", "id": "a1"}, {"event": "A", "id": "a2"}, {"event": "B", "id": "b1"}]
    order = BP._alternate(members)
    assert sorted(m["id"] for m in order) == ["a1", "a2", "b1"]
    assert BP._runs(order) >= 3


def test_published_rows_hold_hashes_and_ids_only(result):
    allowed = {"id", "op", "structural_type", "cluster", "span", "text_sha256", "a", "b", "members"}
    strings = [t["question"] for t in result["texts"]] + [t["label"] for t in result["texts"]] + [
        t["event_title"] for t in result["texts"]]
    text = result["pool_bytes"].decode("utf-8")
    for line in text.splitlines():
        row = json.loads(line)
        assert set(row) <= allowed
        for hashes in row["text_sha256"].values():
            assert set(hashes) == {"question", "label", "event", "event_title"}
            assert all(len(hashes[k]) == 64 for k in ("question", "label", "event_title"))
    assert not any(s in text for s in strings if len(s) > 6)


def test_text_problems_detect_a_changed_title_or_question(result):
    texts = outcome_map(result)
    rows = [json.loads(line) for line in result["pool_bytes"].decode("utf-8").splitlines()]
    assert BP.text_problems(rows, texts) == []
    victim = next(iter(texts))
    changed = dict(texts)
    changed[victim] = {**texts[victim], "event_title": "Another title"}
    assert any(victim in p for p in BP.text_problems(rows, changed))


def test_judge_prompts_carry_no_event_title(result):
    titles = {t["event_title"] for t in result["texts"]}
    for item in result["judge"]:
        assert not any(title in item["prompt"] for title in titles)


def test_slices_partition_the_items_and_publish_their_sha256(result):
    ids = [j["id"] for j in result["judge"]]
    listed = [i for s in result["slices"]["slices"] for i in s["ids"]]
    assert sorted(listed) == sorted(ids) and len(listed) == len(set(listed))
    sizes = [s["items"] for s in result["slices"]["slices"]]
    assert max(sizes) - min(sizes) <= 1 and max(sizes) <= BP.MAX_ITEMS_PER_SLICE
    judge = {j["id"]: j for j in result["judge"]}
    for s in result["slices"]["slices"]:
        assert s["sha256"] == hashlib.sha256(BP.judge_slice_bytes(s["ids"], judge)).hexdigest()
    assert result["slices"]["pool_sha256"] == hashlib.sha256(result["pool_bytes"]).hexdigest()


def test_slice_sizes_allocation():
    assert BP.slice_sizes(560) == [47] * 8 + [46] * 4
    assert BP.slice_sizes(50) == [50] and BP.slice_sizes(51) == [26, 25] and BP.slice_sizes(0) == []


def test_write_outputs_writes_the_summary_and_local_files(result, tmp_path):
    BP.write_outputs(result, pool=tmp_path / "pool.jsonl", slices=tmp_path / "slices.json", summary=tmp_path / "summary.json",
                     texts=tmp_path / "texts.jsonl", judge_dir=tmp_path / "judge")
    assert (tmp_path / "pool.jsonl").read_bytes() == result["pool_bytes"]
    assert json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))["items"] == result["summary"]["items"]
    for s in result["slices"]["slices"]:
        assert (tmp_path / "judge" / f"slice{s['slice']}.jsonl").read_bytes().count(b"\n") == s["items"]


# ---- rehydrate


def market_docs(result):
    """The market documents that the synthetic pool's texts describe, in the API's shape (labels as a JSON string)."""
    docs = {}
    for t in result["texts"]:
        doc = docs.setdefault(t["market"], {"question": t["question"], "labels": {}})
        doc["labels"][int(t["outcome"].split(":")[2])] = t["label"]
    return {m: {"question": d["question"],
                "outcomes": json.dumps([d["labels"].get(i) for i in range(max(d["labels"]) + 1)])} for m, d in docs.items()}


def test_rehydrate_verifies_hashes_and_rebuilds_the_judge_input(result, tmp_path):
    texts, problems = RH.verify(result["rows"], market_docs(result))
    assert problems == [] and len(texts) == len(result["texts"])
    judge = {j["id"]: j for j in result["judge"]}
    rebuilt = RH.rebuild(result["rows"], result["slices"], texts)
    assert RH.slice_problems(rebuilt, result["slices"], tmp_path / "no-local-copy") == []
    for s in result["slices"]["slices"]:
        assert rebuilt[s["slice"]] == BP.judge_slice_bytes(s["ids"], judge)


def test_rehydrate_restores_and_verifies_event_titles(result):
    events = {str(t["event"]): {"title": t["event_title"]} for t in result["texts"]}
    titles, problems = RH.verify_titles(result["rows"], events)
    assert problems == [] and len(titles) == len(result["texts"])
    wrong = dict(events)
    first_event = next(iter(wrong))
    wrong[first_event] = {"title": "A different title"}
    _, problems = RH.verify_titles(result["rows"], wrong)
    assert problems and all("title" in p for p in problems)


def test_rehydrate_reports_changed_texts_and_missing_markets(result):
    markets = market_docs(result)
    victim = next(iter(result["rows"][0]["text_sha256"]))
    market_id, index = RH.parse_outcome_id(victim)
    texts, problems = RH.verify(result["rows"], {**markets, market_id: {**markets[market_id], "question": "Changed question?"}})
    assert victim not in texts and any(p.startswith(victim) and "question" in p for p in problems)
    labels = json.loads(markets[market_id]["outcomes"])
    labels[index] = "Something else"
    texts, problems = RH.verify(result["rows"], {**markets, market_id: {**markets[market_id], "outcomes": json.dumps(labels)}})
    assert victim not in texts and any(p.startswith(victim) and "label" in p for p in problems)
    _, problems = RH.verify(result["rows"], {k: v for k, v in markets.items() if k != market_id})
    assert any(p.startswith(victim) and "missing" in p for p in problems)


def test_rehydrate_flags_a_rebuilt_slice_that_differs(result, tmp_path):
    texts, _ = RH.verify(result["rows"], market_docs(result))
    rebuilt = RH.rebuild(result["rows"], result["slices"], texts)
    local = tmp_path / "slices"
    local.mkdir()
    first = result["slices"]["slices"][0]["slice"]
    (local / f"slice{first}.jsonl").write_bytes(rebuilt[first] + b"tampered\n")
    assert any(f"slice {first}" in p and "local" in p for p in RH.slice_problems(rebuilt, result["slices"], local))
    tampered = json.loads(json.dumps(result["slices"]))
    tampered["slices"][0]["sha256"] = "0" * 64
    assert any("slices.json" in p for p in RH.slice_problems(rebuilt, tampered, tmp_path / "none"))


def test_rehydrate_refuses_an_output_inside_the_repository(capsys):
    assert RH.main(["--out", str(AUDIT / "rehydrated-here")]) == 1
    assert "inside the repository" in capsys.readouterr().err


# ---- fetch


def test_fetch_selects_events_by_created_time_and_pages_cleanly():
    raw = [{"id": 1, "createdAt": "2026-05-04T00:00:00Z"}, {"id": 2, "createdAt": "2026-05-02T23:59:59Z"},
           {"id": 1, "createdAt": "2026-05-04T00:00:00Z"}, {"id": 3, "createdAt": None}]
    kept, counts = FE.select_events(raw)
    assert [e["id"] for e in kept] == ["1"]
    assert counts == {"events_returned": 4, "duplicate_events": 1, "events_without_created_time": 1, "events_created_before_cutoff": 1}
    assert FE.query_url(200).endswith("order=volume&ascending=false&start_date_min=2026-05-03T00:00:00Z")
    pages = {0: [{"id": i} for i in range(100)], 100: [{"id": i} for i in range(100, 150)]}

    def get(url):
        return 200, json.dumps(pages[int(url.split("offset=")[1].split("&")[0])]).encode("utf-8")

    rows, page_record, stop = FE.fetch_events(get)
    assert len(rows) == 150 and page_record[-1] == {"offset": 100, "returned": 50} and stop["reason"].startswith("short page")

    def refused(url):
        if "offset=2000" in url:
            return 422, b'{"type":"validation error","error":"offset too large, use /events/keyset for deeper pagination"}'
        return 200, json.dumps([{"id": i} for i in range(100)]).encode("utf-8")

    _, page_record, stop = FE.fetch_events(refused)
    assert stop["reason"] == "offset cap" and stop["offset"] == 2000 and len(page_record) == 20
    with pytest.raises(FE.FetchError):
        FE.fetch_events(lambda url: (500, b"oops"))


def test_manifest_publishes_hashes_of_slugs_and_no_text():
    raw = [{"id": 1, "createdAt": "2026-05-04T00:00:00Z", "volume": 1, "seriesSlug": "secret-series-name",
            "tags": [{"label": "Secret Label", "slug": "secret-category-slug"}], "title": "Secret Title Words",
            "markets": [{"id": "9", "question": "Will Secret Question Text happen?", "outcomes": '["Yes", "No"]'}]}]
    events, counts = FE.select_events(raw)
    doc = FE.manifest(events, counts, [], {"reason": "short page"}, "0" * 64, "2026-11-04T00:00:00+00:00")
    text = json.dumps(doc)
    for secret in ("secret-series-name", "secret-category-slug", "Secret Title Words", "Secret Question Text", "Secret Label"):
        assert secret not in text
    row = doc["events"][0]
    assert row["series_sha256"] == FE.sha256_text("secret-series-name")
    assert "series_slug" not in row and "category_slug" not in row and row["market_count"] == 1


# ---- score.py: canonical answers, reason codes, the call lifecycle


def test_canonical_answers_keep_only_the_fields_the_protocol_reads():
    assert SC.canonical_answer("decide_relation", None, {"relation": "EQUIV"}) == {"relation": "EQUIV"}
    assert SC.canonical_answer("decide_relation", None, {"relation": "Will Velmar close above 120? EQUIV"}) == {"invalid": True}
    assert SC.canonical_answer("decide_group", 3, {"kind": "UNCLEAR", "lower": 0, "upper": 3, "chain": []}) == {"kind": "UNCLEAR"}
    assert SC.canonical_answer("decide_group", 3, {"kind": "ordered_chain", "lower": 0, "upper": 3, "chain": [3, 1, 2]}) == \
        {"kind": "ordered_chain", "lower": 0, "upper": 3, "chain": [3, 1, 2]}
    assert SC.canonical_answer("decide_group", 3, {"kind": "ordered_chain", "lower": 0, "upper": 3, "chain": ["Vela", 1]}) == \
        {"invalid": True}
    assert SC.canonical_answer("decide_group", 3, {"kind": "exactly_one", "lower": 1, "upper": 1, "chain": [], "note": "x"}) == \
        {"kind": "exactly_one", "lower": 1, "upper": 1, "chain": []}
    assert SC.canonical_answer("decide_group", 3, {"kind": "bogus", "lower": 1, "upper": 1}) == {"invalid": True}
    assert SC.canonical_answer("decide_group", 3, {"kind": "none", "lower": True, "upper": 3}) == {"invalid": True}


def test_normalised_classes_match_the_decision_scoring_classes():
    assert SC.normalised("decide_relation", None, {"relation": "NONE"}) == "NONE"
    assert SC.normalised("decide_relation", None, {"invalid": True}) == "__missing__"
    assert SC.normalised("decide_group", 3, {"kind": "UNCLEAR"}) == "UNCLEAR"
    assert SC.normalised("decide_group", 3, {"kind": "none", "lower": 0, "upper": 3, "chain": []}) == "none"
    assert SC.normalised("decide_group", 4, {"kind": "at_least_k", "lower": 2, "upper": 4, "chain": []}) == "at_least_k:2"
    assert SC.normalised("decide_group", 3, {"kind": "ordered_chain", "lower": 0, "upper": 3, "chain": []}) == "ordered_chain"
    assert SC.normalised("decide_group", 3, {"kind": "exactly_one", "lower": 2, "upper": 1, "chain": []}) == "__missing__"


@pytest.fixture
def judge_env(tmp_path, monkeypatch):
    """A judge folder in a temporary directory: the frozen check passes, the slices 1 and 2 are synthetic with their sha256 published (their
    ids are the real slices' ids), and the pool is the real published pool (ids and types only). Calls are closed at once (no grace)."""
    monkeypatch.setattr(SC, "check_frozen", lambda: None)
    monkeypatch.setattr(SC, "TIMEOUT_S", 0)  # the in-flight grace (TIMEOUT_S + CALL_GRACE_S) has its own test
    monkeypatch.setattr(SC, "CALL_GRACE_S", 0)
    local, base = tmp_path / "local", tmp_path / "base"
    synthetic, items_of = {}, {}
    for s in (1, 2):
        ids = SC.slice_ids(s)
        items = "".join(json.dumps({"id": i, "type": "x", "prompt": "p", "answer_format": "{}"}) + "\n" for i in ids).encode("utf-8")
        (local / "slices").mkdir(parents=True, exist_ok=True)
        (local / "slices" / f"slice{s}.jsonl").write_bytes(items)
        synthetic[s] = {"slice": s, "ids": ids, "sha256": hashlib.sha256(items).hexdigest()}
        items_of[s] = items
    monkeypatch.setattr(SC, "load_slices", lambda: synthetic)
    return {"local": local, "base": base, "ids": SC.slice_ids(1), "pool": SC.load_pool(), "items": items_of[1]}


def canonical_raw(env, answer_of=None, slice_no=1):
    """A raw output with one answer per id of the slice (the answers are the relations' NONE and the groups' none, unless given)."""
    def default(i):
        if env["pool"][i]["op"] == "decide_relation":
            return {"relation": "NONE"}
        return {"kind": "none", "lower": 0, "upper": env["pool"][i]["n"], "chain": []}
    answer_of = answer_of or default
    body = "\n".join(json.dumps({"id": i, "answer": answer_of(i)}) for i in SC.slice_ids(slice_no))
    return json.dumps({"is_error": False, "subtype": "success", "num_turns": 1,
                       "result": "Reasoning.\n```jsonl\n" + body + "\n```"}).encode("utf-8")


def call(env, attempt, raw, stdin=None, inst=1, slice_no=1):
    """What run_slice does: the calling line and the stdin, the call, then the called line with the raw output."""
    stdin = stdin if stdin is not None else SC.stdin_of(slice_no, env["local"])
    SC.record_calling(inst, slice_no, attempt, stdin, env["local"], env["base"])
    SC.record_called(inst, slice_no, attempt, raw, env["local"], env["base"])
    return SC.raw_path(inst, slice_no, attempt, env["local"])


def test_valid_call_is_extracted_and_rederived(judge_env):
    env = judge_env
    call(env, 1, canonical_raw(env))
    entry = SC.extract(1, 1, 1, base=env["base"], local=env["local"])
    assert entry["verdict"] == "valid" and entry["raw"] == "attempt1.json"
    text = SC.answers_path(1, 1, env["base"]).read_text(encoding="utf-8")
    assert text.count("\n") == len(env["ids"])
    SC.verify_ledger(SC.read_ledger(env["base"]), env["local"], env["base"])
    SC.answers_path(1, 1, env["base"]).write_text(text.replace("NONE", "EQUIV", 1), encoding="utf-8")
    with pytest.raises(SC.Refused, match="re-derived"):
        SC.verify_ledger(SC.read_ledger(env["base"]), env["local"], env["base"])


def test_a_pending_call_blocks_a_new_call_and_a_deleted_raw_file_is_void(judge_env):
    env = judge_env
    path = call(env, 1, canonical_raw(env))
    ledger = SC.read_ledger(env["base"])
    with pytest.raises(SC.Refused, match="no closing line"):
        SC.check_allowed(ledger, 1, 1, 1)  # the call is on the record: it used the attempt
    path.unlink()  # the raw output is gone: the next extract closes the attempt as void
    entry = SC.extract(1, 1, 1, base=env["base"], local=env["local"])
    assert entry["verdict"] == "void" and entry["reason"] == "raw_missing"
    SC.check_allowed(SC.read_ledger(env["base"]), 1, 1, 2)  # attempt 2 is next, after a void


def test_extract_without_a_call_is_refused(judge_env):
    with pytest.raises(SC.Refused, match="no pending call"):
        SC.extract(1, 1, 1, base=judge_env["base"], local=judge_env["local"])


def test_a_void_reason_is_a_code_and_quoted_judge_text_never_reaches_the_ledger_or_answers(judge_env):
    env = judge_env
    phrase = "Invented Judge Phrase 4417 about Ostra"
    lines = [json.dumps({"id": env["ids"][0], "answer": {"relation": "NONE"}}), phrase]
    raw = json.dumps({"is_error": False, "subtype": "success", "num_turns": 1,
                      "result": "```jsonl\n" + "\n".join(lines) + "\n```"}).encode("utf-8")
    call(env, 1, raw)
    entry = SC.extract(1, 1, 1, base=env["base"], local=env["local"])
    assert entry["verdict"] == "void" and entry["reason"] == "line_not_json:2"
    ledger_text = (env["base"] / "judge" / "ledger.jsonl").read_text(encoding="utf-8")
    assert phrase not in ledger_text
    full = [json.dumps({"id": i, "answer": {"relation": phrase} if k == 0 else {"relation": "NONE"}})
            for k, i in enumerate(env["ids"])]
    doc = json.dumps({"is_error": False, "subtype": "success", "num_turns": 1,
                      "result": "```jsonl\n" + "\n".join(full) + "\n```"}).encode("utf-8")
    out = SC.canonical_lines(doc, env["ids"])
    assert phrase not in "\n".join(out) and '"invalid": true' in out[0]


def test_schema_errors_are_closing_lines_and_the_second_closes_the_attempt_as_void(judge_env):
    env = judge_env
    call(env, 1, b"not json at all")
    first = SC.extract(1, 1, 1, base=env["base"], local=env["local"])
    assert first["verdict"] == "schema_error" and first["reason"] == "not_json"
    assert first["raw"] == "attempt1.schema-error-1.json"
    assert not SC.raw_path(1, 1, 1, env["local"]).exists()
    call(env, 1, b"[]")
    second = SC.extract(1, 1, 1, base=env["base"], local=env["local"])
    assert second["verdict"] == "void" and second["reason"] == "schema_error_repeats"
    ledger = SC.read_ledger(env["base"])
    SC.verify_ledger(ledger, env["local"], env["base"])
    SC.check_allowed(ledger, 1, 1, 2)  # the attempt is void: attempt 2 is next
    with pytest.raises(SC.Refused, match="next attempt"):
        SC.check_allowed(ledger, 1, 1, 1)


def test_an_error_object_without_a_result_field_is_a_void_not_a_schema_error(judge_env):
    raw = json.dumps({"is_error": True, "subtype": "error_during_execution"}).encode("utf-8")
    with pytest.raises(SC.Void) as exc:
        SC.canonical_lines(raw, judge_env["ids"])
    assert str(exc.value) == "error_result"


def test_a_changed_stdin_or_raw_output_closes_the_attempt_as_void_and_still_verifies(judge_env):
    env = judge_env
    call(env, 1, canonical_raw(env), stdin=b"a different stdin")
    entry = SC.extract(1, 1, 1, base=env["base"], local=env["local"])
    assert entry["verdict"] == "void" and entry["reason"] == "stdin_mismatch"
    SC.verify_ledger(SC.read_ledger(env["base"]), env["local"], env["base"])
    call(env, 2, canonical_raw(env))
    SC.raw_path(1, 1, 2, env["local"]).write_bytes(b"changed after the call")
    entry = SC.extract(1, 1, 2, base=env["base"], local=env["local"])
    assert entry["verdict"] == "void" and entry["reason"] == "raw_mismatch"
    SC.verify_ledger(SC.read_ledger(env["base"]), env["local"], env["base"])


def test_an_interrupted_call_is_raw_missing_and_a_saved_output_without_its_line_is_raw_unrecorded(judge_env):
    env = judge_env
    SC.record_calling(1, 1, 1, SC.stdin_of(1, env["local"]), env["local"], env["base"])  # interrupted: no called line, no raw output
    entry = SC.extract(1, 1, 1, base=env["base"], local=env["local"])
    assert entry["verdict"] == "void" and entry["reason"] == "raw_missing"
    SC.record_calling(1, 1, 2, SC.stdin_of(1, env["local"]), env["local"], env["base"])
    SC.raw_path(1, 1, 2, env["local"]).write_bytes(canonical_raw(env))  # saved, but the called line was never written
    entry = SC.extract(1, 1, 2, base=env["base"], local=env["local"])
    assert entry["verdict"] == "void" and entry["reason"] == "raw_unrecorded"
    SC.verify_ledger(SC.read_ledger(env["base"]), env["local"], env["base"])


def test_the_preflight_gate_keeps_every_other_call_closed_until_a_pass(judge_env):
    env = judge_env
    with pytest.raises(SC.Refused, match="preflight"):
        SC.check_allowed([], 2, 1, 1)
    SC.check_allowed([], 1, 1, 1)  # the first call is the one the gate inspects
    call(env, 1, b"not json")
    failed = SC.preflight(base=env["base"], local=env["local"])
    assert failed["result"] == "fail" and failed["reason"] == "not_json"
    with pytest.raises(SC.Refused, match="preflight"):
        SC.check_allowed(SC.read_ledger(env["base"]), 2, 1, 1)
    SC.extract(1, 1, 1, base=env["base"], local=env["local"])  # the failed output is closed as a schema error
    call(env, 1, canonical_raw(env))
    passed = SC.preflight(base=env["base"], local=env["local"])
    assert passed["result"] == "pass"
    SC.check_allowed(SC.read_ledger(env["base"]), 2, 1, 1)
    with pytest.raises(SC.Refused, match="already passed"):
        SC.preflight(base=env["base"], local=env["local"])


ERROR_OBJECT = json.dumps({"is_error": True}).encode("utf-8")  # an error object with no subtype: a void, error_result


def test_a_double_void_while_the_gate_is_closed_stops_the_pilot(judge_env):
    env = judge_env
    call(env, 1, ERROR_OBJECT)
    assert SC.extract(1, 1, 1, base=env["base"], local=env["local"])["reason"] == "error_result"
    assert SC.preflight(base=env["base"], local=env["local"])["result"] == "fail"
    call(env, 2, ERROR_OBJECT)
    assert SC.extract(1, 1, 2, base=env["base"], local=env["local"])["reason"] == "error_result"
    ledger = SC.read_ledger(env["base"])
    assert ledger[-1]["verdict"] == "stop" and ledger[-1]["reason"] == "double_void_gated"
    with pytest.raises(SC.Refused, match="stopped: double_void_gated"):
        SC.check_allowed(ledger, 1, 2, 1)
    with pytest.raises(SC.Refused, match="stopped"):
        SC.check_allowed(ledger, 2, 1, 1)
    with pytest.raises(SC.Refused, match="stopped"):
        SC.extract(1, 2, 1, base=env["base"], local=env["local"])


def test_slice_2_waits_for_slice_1_to_settle_and_a_valid_retry_opens_it(judge_env):
    env = judge_env
    call(env, 1, ERROR_OBJECT)
    assert SC.extract(1, 1, 1, base=env["base"], local=env["local"])["reason"] == "error_result"  # one void: not settled
    with pytest.raises(SC.Refused, match="next slice is 1"):
        SC.check_allowed(SC.read_ledger(env["base"]), 1, 2, 1)
    SC.check_allowed(SC.read_ledger(env["base"]), 1, 1, 2)  # attempt 2 of slice 1 is next
    call(env, 2, canonical_raw(env))
    assert SC.extract(1, 1, 2, base=env["base"], local=env["local"])["verdict"] == "valid"
    assert SC.preflight(base=env["base"], local=env["local"])["result"] == "pass"
    ledger = SC.read_ledger(env["base"])
    SC.check_allowed(ledger, 1, 2, 1)
    SC.check_allowed(ledger, 2, 1, 1)  # the gate is open


def test_preflight_fails_a_raw_output_that_a_schema_error_moved_aside(judge_env):
    env = judge_env
    call(env, 1, b"not json")
    assert SC.extract(1, 1, 1, base=env["base"], local=env["local"])["verdict"] == "schema_error"
    assert not SC.raw_path(1, 1, 1, env["local"]).exists()  # moved aside
    failed = SC.preflight(base=env["base"], local=env["local"])
    assert failed["result"] == "fail" and failed["reason"] == "not_json"


def test_a_called_line_after_its_attempt_closed_stops_the_pilot(judge_env, monkeypatch):
    env = judge_env
    old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=SC.TIMEOUT_S + SC.CALL_GRACE_S + 1)).isoformat(
        timespec="seconds")
    monkeypatch.setattr(SC, "now", lambda: old)
    SC.record_calling(1, 1, 1, SC.stdin_of(1, env["local"]), env["local"], env["base"])
    assert SC.extract(1, 1, 1, base=env["base"], local=env["local"])["reason"] == "raw_missing"
    SC.record_called(1, 1, 1, canonical_raw(env), env["local"], env["base"])  # the subprocess returned after the close
    ledger = SC.read_ledger(env["base"])
    with pytest.raises(SC.Refused, match="stopped: late_call"):
        SC.check_allowed(ledger, 1, 1, 2)
    with pytest.raises(SC.Refused, match="stopped: late_call"):
        SC.extract(1, 1, 1, base=env["base"], local=env["local"])
    assert SC.read_ledger(env["base"])[-1]["verdict"] == "stop" and SC.read_ledger(env["base"])[-1]["reason"] == "late_call"


def test_a_schema_error_whose_close_was_interrupted_after_the_rename_is_finished_by_extract(judge_env):
    env = judge_env
    call(env, 1, b"not json")
    path = SC.raw_path(1, 1, 1, env["local"])
    path.rename(path.with_name("attempt1.schema-error-1.json"))  # the close stopped after the rename, before its line
    entry = SC.extract(1, 1, 1, base=env["base"], local=env["local"])
    assert entry["verdict"] == "schema_error" and entry["reason"] == "not_json" and entry["raw"] == "attempt1.schema-error-1.json"
    SC.verify_ledger(SC.read_ledger(env["base"]), env["local"], env["base"])


def test_a_torn_schema_error_pair_is_closed_identically_in_score_and_analyse(monkeypatch):
    monkeypatch.setattr(SC, "load_slices", lambda: {1: {"slice": 1, "ids": [], "sha256": ""}})

    def line(inst, attempt, verdict):
        return {"inst": inst, "slice": 1, "attempt": attempt, "verdict": verdict, "reason": "x"}
    pairs = [line(1, 1, "schema_error"), line(1, 1, "schema_error"), line(1, 2, "schema_error"), line(1, 2, "schema_error")]
    ledger = pairs + [line(2, 1, "valid"), line(3, 1, "valid")]
    assert SC.closed_attempts(ledger, 1, 1) == {1, 2} and SC.settled(ledger, 1, 1)
    AN.check_complete(ledger)  # inst 1 is double void by two torn pairs; instances 2 and 3 are accepted
    with pytest.raises(AN.AnalyseError, match="not settled"):  # one torn pair closes one attempt only
        AN.check_complete(pairs[:2] + [line(2, 1, "valid"), line(3, 1, "valid")])


def test_a_calling_line_younger_than_the_timeout_and_grace_is_never_closed(judge_env, monkeypatch):
    env = judge_env
    monkeypatch.setattr(SC, "TIMEOUT_S", 1800)  # the production window: 1,800 + 120 seconds
    monkeypatch.setattr(SC, "CALL_GRACE_S", 120)
    old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=1800 + 121)).isoformat(timespec="seconds")
    monkeypatch.setattr(SC, "now", lambda: old)
    SC.record_calling(1, 1, 1, SC.stdin_of(1, env["local"]), env["local"], env["base"])  # started long ago, never returned
    assert SC.extract(1, 1, 1, base=env["base"], local=env["local"])["reason"] == "raw_missing"
    fresh = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    monkeypatch.setattr(SC, "now", lambda: fresh)
    SC.record_calling(1, 1, 2, SC.stdin_of(1, env["local"]), env["local"], env["base"])  # in flight now
    with pytest.raises(SC.Refused, match="in flight"):
        SC.extract(1, 1, 2, base=env["base"], local=env["local"])
    assert [e["verdict"] for e in SC.read_ledger(env["base"])] == ["calling", "void", "calling"]


def test_a_torn_ledger_line_is_refused_with_its_line_number(judge_env):
    env = judge_env
    ledger_file = env["base"] / "judge" / "ledger.jsonl"
    ledger_file.parent.mkdir(parents=True)
    ledger_file.write_text('{"inst": 1, "slice": 1, "attempt": 1, "verdict": "void", "reason": "x"}\n{"inst": 1, "slic', encoding="utf-8")
    with pytest.raises(SC.Refused, match="line 2 is not JSON"):
        SC.read_ledger(env["base"])


def test_two_schema_error_lines_without_their_void_still_close_the_attempt():
    ledger = [{"inst": 1, "slice": 1, "attempt": 1, "verdict": "schema_error", "reason": "not_json"}] * 2  # a torn append lost the void
    SC.check_allowed(ledger, 1, 1, 2)
    with pytest.raises(SC.Refused, match="next attempt is 2"):
        SC.check_allowed(ledger, 1, 1, 1)


def test_an_error_flag_is_read_before_the_subtype():
    assert SC.parse_raw(json.dumps({"is_error": True}).encode("utf-8")) == {"is_error": True}
    with pytest.raises(SC.SchemaError, match="missing:subtype"):
        SC.parse_raw(json.dumps({"is_error": False}).encode("utf-8"))


def test_check_allowed_refuses_unknown_instances_slices_and_a_wrong_attempt():
    with pytest.raises(SC.Refused, match="instance"):
        SC.check_allowed([], 4, 1, 1)
    with pytest.raises(SC.Refused, match="slices.json"):
        SC.check_allowed([], 1, 999, 1)
    with pytest.raises(SC.Refused, match="next attempt"):
        SC.check_allowed([], 1, 1, 2)


def test_the_input_of_an_unknown_slice_is_refused_not_a_crash(tmp_path):
    with pytest.raises(SC.Refused, match="not in slices.json"):
        SC.stdin_of(999, tmp_path)


def test_stdin_refuses_items_that_do_not_match_the_published_sha256(tmp_path, monkeypatch):
    local = tmp_path / "local"
    (local / "slices").mkdir(parents=True)
    items = json.dumps({"id": "T0001", "type": "relation", "prompt": "p", "answer_format": "{}"}).encode("utf-8") + b"\n"
    (local / "slices" / "slice1.jsonl").write_bytes(items)
    monkeypatch.setattr(SC, "load_slices", lambda: {1: {"slice": 1, "ids": ["T0001"], "sha256": "0" * 64}})
    with pytest.raises(SC.Refused, match="sha256"):
        SC.stdin_of(1, local)
    monkeypatch.setattr(SC, "load_slices", lambda: {1: {"slice": 1, "ids": ["T0001"], "sha256": hashlib.sha256(items).hexdigest()}})
    assert SC.stdin_of(1, local).endswith(items)


def test_freeze_refuses_once_anything_under_judge_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(SC, "HERE", tmp_path / "audit")
    monkeypatch.setattr(SC, "FROZEN", tmp_path / "audit" / "frozen.json")
    monkeypatch.setattr(SC, "JUDGE_LOCAL", tmp_path / "no-local-judge")
    (tmp_path / "audit" / "judge").mkdir(parents=True)
    with pytest.raises(SC.Refused, match="fixed"):
        SC.freeze()
    (tmp_path / "audit" / "judge").rmdir()
    (tmp_path / "no-local-judge" / "raw").mkdir(parents=True)
    with pytest.raises(SC.Refused, match="fixed"):
        SC.freeze()


def test_the_frozen_check_covers_the_files_and_the_listed_inputs(tmp_path, monkeypatch):
    root = tmp_path / "root"
    audit = tmp_path / "audit"
    for name in SC.FROZEN_FILES:
        (audit / name).parent.mkdir(parents=True, exist_ok=True)
        (audit / name).write_text(f"content of {name}\n", encoding="utf-8")
    for name in SC.FROZEN_INPUTS:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(f"input {name}\n", encoding="utf-8")
    monkeypatch.setattr(SC, "HERE", audit)
    monkeypatch.setattr(SC, "ROOT", root)
    monkeypatch.setattr(SC, "FROZEN", audit / "frozen.json")
    monkeypatch.setattr(SC, "JUDGE_LOCAL", tmp_path / "no-local-judge")
    with pytest.raises(SC.Refused):
        SC.check_frozen()
    doc = SC.freeze()
    assert sorted(doc["inputs"]) == sorted(SC.FROZEN_INPUTS)
    SC.check_frozen()
    (root / "results/v0.2.0/analysis.json").write_text("changed\n", encoding="utf-8")
    with pytest.raises(SC.Refused, match="analysis.json"):
        SC.check_frozen()


def test_capture_runner_with_patched_subprocess(tmp_path, monkeypatch):
    workdir = tmp_path / "empty"
    workdir.mkdir()
    (tmp_path / "raw").mkdir()
    monkeypatch.setattr(SC, "check_frozen", lambda: None)
    monkeypatch.setattr(SC, "read_ledger", lambda base=None: [{"verdict": "preflight", "result": "pass", "inst": 1, "slice": 1,
                                                               "attempt": 1}])
    monkeypatch.setattr(SC, "verify_ledger", lambda ledger, local=None, base=None: None)
    monkeypatch.setattr(SC, "raw_path", lambda inst, slice_no, attempt, loc=None, name=None: tmp_path / "raw" / f"attempt{attempt}.json")
    monkeypatch.setattr(SC, "stdin_of", lambda slice_no, local=None: b"stdin bytes")
    calls = []
    monkeypatch.setattr(SC, "record_calling", lambda inst, slice_no, attempt, stdin, local=None, base=None: calls.append(("calling", stdin)))
    monkeypatch.setattr(SC, "record_called", lambda inst, slice_no, attempt, raw, local=None, base=None: calls.append(("called", raw)))
    monkeypatch.setattr(RS.shutil, "which", lambda name: "/usr/bin/claude")
    seen = {}

    class Done:
        stdout = b'{"ok": true}'
        stderr = b""
        returncode = 0

    def fake_run(argv, input, cwd, env, capture_output, check, timeout):
        seen.update(argv=argv, input=input, cwd=cwd, env=env, timeout=timeout)
        return Done()

    monkeypatch.setattr(RS.subprocess, "run", fake_run)
    out, code = RS.capture(1, 2, 1, workdir)
    assert code == 0 and calls == [("calling", b"stdin bytes"), ("called", b'{"ok": true}')]
    assert seen["input"] == b"stdin bytes" and seen["cwd"] == workdir.resolve() and seen["timeout"] == SC.TIMEOUT_S
    assert seen["argv"][1:] == RS.CLAUDE_ARGS and seen["env"]["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"


def test_a_timed_out_call_stays_pending(tmp_path, monkeypatch):
    workdir = tmp_path / "empty"
    workdir.mkdir()
    (tmp_path / "raw").mkdir()
    monkeypatch.setattr(SC, "check_frozen", lambda: None)
    monkeypatch.setattr(SC, "read_ledger", lambda base=None: [{"verdict": "preflight", "result": "pass", "inst": 1, "slice": 1,
                                                               "attempt": 1}])
    monkeypatch.setattr(SC, "verify_ledger", lambda ledger, local=None, base=None: None)
    monkeypatch.setattr(SC, "raw_path", lambda inst, slice_no, attempt, loc=None, name=None: tmp_path / "raw" / f"attempt{attempt}.json")
    monkeypatch.setattr(SC, "stdin_of", lambda slice_no, local=None: b"stdin bytes")
    calls = []
    monkeypatch.setattr(SC, "record_calling", lambda inst, slice_no, attempt, stdin, local=None, base=None: calls.append("calling"))
    monkeypatch.setattr(SC, "record_called", lambda inst, slice_no, attempt, raw, local=None, base=None: calls.append("called"))
    monkeypatch.setattr(RS.shutil, "which", lambda name: "/usr/bin/claude")

    def hangs(argv, input, cwd, env, capture_output, check, timeout):
        raise RS.subprocess.TimeoutExpired(argv, timeout)

    monkeypatch.setattr(RS.subprocess, "run", hangs)
    with pytest.raises(SC.Refused, match="stays pending"):
        RS.capture(1, 2, 1, workdir)
    assert calls == ["calling"]


def test_workdir_must_be_empty_and_outside_the_repository(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert RS.check_workdir(empty) == empty.resolve()
    (empty / "file.txt").write_text("x", encoding="utf-8")
    with pytest.raises(SC.Refused, match="not empty"):
        RS.check_workdir(empty)
    with pytest.raises(SC.Refused, match="inside the repository"):
        RS.check_workdir(AUDIT)


def test_published_frozen_hashes_match_the_files_and_inputs():
    frozen = json.loads((AUDIT / "frozen.json").read_text(encoding="utf-8"))
    assert sorted(frozen["files"]) == sorted(SC.FROZEN_FILES) and sorted(frozen["inputs"]) == sorted(SC.FROZEN_INPUTS)
    for name, digest in frozen["files"].items():
        assert hashlib.sha256((AUDIT / name).read_bytes()).hexdigest() == digest, name
    for name, digest in frozen["inputs"].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest, name


# ---- analysis


def test_consensus_rules_and_reasons():
    assert AN.consensus_of(["NONE", "NONE", "NONE"]) == ("unanimous", "NONE")
    assert AN.consensus_of(["NONE", "NONE", "EQUIV"]) == ("majority", "NONE")
    assert AN.consensus_of(["NONE", "EQUIV", "IMPLIES"]) == ("review", None)
    assert AN.consensus_of(["UNCLEAR", "UNCLEAR", "UNCLEAR"]) == ("dropped", None)
    assert AN.consensus_of(["UNCLEAR", "UNCLEAR", "NONE"]) == ("dropped", None)
    assert AN.consensus_of(["UNCLEAR", "NONE", "NONE"]) == ("majority", "NONE")
    assert AN.consensus_of(["EXCLUDES", "EXCLUDES", "__missing__"]) == ("two_agree", "EXCLUDES")
    assert AN.consensus_of(["NONE", "EXCLUDES", "__missing__"]) == ("review", None)
    assert AN.consensus_of(["UNCLEAR", "UNCLEAR", "__missing__"]) == ("dropped", None)
    assert AN.consensus_of(["__missing__", "__missing__", "NONE"]) == ("review", None)
    assert AN.review_reason(["NONE", "EQUIV", "IMPLIES"]) == "all_different"
    assert AN.review_reason(["NONE", "EXCLUDES", "__missing__"]) == "two_disagree"
    assert AN.review_reason(["NONE", "__missing__", "__missing__"]) == "fewer_than_two"


def test_valid_labels_are_the_exact_vocabulary():
    assert AN.valid_labels("decide_relation", None) == {"EQUIV", "IMPLIES", "IMPLIED_BY", "EXCLUDES", "NONE", "UNCLEAR"}
    grp = AN.valid_labels("decide_group", 3)
    assert {"none", "exactly_one", "at_most_one", "at_least_k:2", "at_least_k:3", "at_most_k:2", "ordered_chain", "UNCLEAR"} <= grp
    assert "maybe" not in grp and "at_least_k:0" not in grp and "at_least_k:4" not in grp


def test_allocate_follows_the_reference_rule():
    assert AN.allocate({"a": 100, "b": 3, "c": 50, "d": 7}, 20) == {"a": 6, "b": 3, "c": 6, "d": 5}
    assert AN.allocate({"a": 2, "b": 0}, 5) == {"a": 2, "b": 0}
    assert AN.allocate({}, 5) == {}


def test_spearman_kendall_and_the_constant_rule():
    assert AN.spearman([1, 2, 3], [10, 20, 30]) == pytest.approx(1.0)
    assert AN.spearman([1, 2, 3], [30, 20, 10]) == pytest.approx(-1.0)
    assert AN.spearman([1, 1, 1], [1, 2, 3]) == 0.0
    assert AN.kendall_tau_b([1, 2, 3], [1, 2, 3]) == pytest.approx(1.0)
    assert AN.kendall_tau_b([1, 1, 2], [1, 2, 3]) == pytest.approx(0.8164965809277261)
    assert AN.kendall_tau_b([1, 1], [2, 2]) == 0.0


def test_reading_rule():
    assert AN.reading(0.6, 0.9) == "carries over"
    assert AN.reading(0.1, 0.59) == "does not carry over"
    assert AN.reading(0.2, 0.7) == "inconclusive"


def test_mcc_of_no_pairs_and_constant_marginal_is_zero():
    assert AN._mcc([], [], {}) == 0.0
    items = [{"id": "x", "gold": "NONE", "cluster": "C1", "op": "decide_relation"}]
    assert AN._mcc(items, [1], {"x": "NONE"}) == 0.0


def statistics_fixture():
    rng = random.Random(7)
    labels = ["EQUIV", "IMPLIES", "EXCLUDES", "NONE"]
    items, preds = [], {}
    names = [f"model-{k}" for k in range(5)]
    for k in range(40):
        items.append({"id": f"T{k:04d}", "op": "decide_relation", "gold": labels[k % 4], "cluster": f"C{k % 6:04d}"})
    for k in range(10):
        items.append({"id": f"G{k:04d}", "op": "decide_group", "gold": "none" if k % 2 else "exactly_one", "cluster": f"C{k % 6:04d}"})
    for m, name in enumerate(names):
        preds[name] = {it["id"]: (it["gold"] if rng.random() < 0.3 + 0.15 * m else rng.choice(labels + ["none"])) for it in items}
    v020 = {name: {"relation": 0.5 + 0.1 * m, "group": 0.2 + 0.05 * (m % 3), "headline": (0.5 + 0.1 * m + 0.2 + 0.05 * (m % 3)) / 2}
            for m, name in enumerate(names)}
    return items, preds, v020


def test_statistics_are_deterministic_paired_and_report_the_anchors():
    items, preds, v020 = statistics_fixture()
    resolved = [{"a": "model-4", "b": "model-0", "higher": "model-4"}]
    anchors = {"lexical": {it["id"]: "none" for it in items}}
    first = AN.statistics(items, preds, v020, resolved, B=60, seed=0, anchors=anchors)
    assert first == AN.statistics(items, preds, v020, resolved, B=60, seed=0, anchors=anchors)
    assert first["primary"]["reading"] in ("carries over", "does not carry over", "inconclusive")
    assert first["items"] == {"relation": 40, "group": 10, "clusters": 6}
    assert set(first["models"]) == set(preds) and set(first["anchors"]) == {"lexical"}


def test_a_zero_pilot_difference_does_not_keep_the_order():
    items, preds, v020 = statistics_fixture()
    for name in preds:
        preds[name] = {k: "NONE" for k in preds[name]}
    out = AN.statistics(items, preds, v020, [{"a": "model-0", "b": "model-1", "higher": "model-0"}], B=10)
    assert out["secondary"]["resolved_pairs"] == {"pairs": 1, "kept_order": 0, "share": 0.0}


# ---- the synthetic pipeline: real hashes, real ledger, real answers


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")


def synthetic_pool(tmp_path, monkeypatch, relations=8, groups=True):
    """By default eight relations (T0001-T0006 same-event, T0007-T0008 trivial) and four groups (T0009-T0011, T0012 a decoy in another
    cluster). The pool file, the local texts and their published hashes all come from the same data."""
    rows, texts = [], []

    def add_outcome(oid, cluster):
        market = oid.split(":")[1]
        texts.append({"outcome": oid, "market": market, "event": f"E{market}", "cluster": cluster,
                      "event_title": f"Invented event {market}", "question": f"Invented question about {oid}?",
                      "label": "Yes" if oid.endswith(":0") else "No"})
        t = texts[-1]
        return {"question": BP.sha256_text(t["question"]), "label": BP.sha256_text(t["label"]), "event": t["event"],
                "event_title": BP.sha256_text(t["event_title"])}

    for k in range(1, relations + 1):
        kind = "trivial" if k >= 7 else "same_event_alt"
        a, b = f"pm:{100 + k}:0", f"pm:{100 + k}:1"
        hashes = {a: add_outcome(a, "C0001"), b: add_outcome(b, "C0001")}
        rows.append({"id": f"T{k:04d}", "op": "decide_relation", "structural_type": kind, "cluster": "C0001", "span": False,
                     "a": a, "b": b, "text_sha256": hashes})
    for k in range(9, 13 if groups else 9):
        members = [f"pm:{200 + k}:0", f"pm:{210 + k}:0", f"pm:{220 + k}:1"]
        cluster = "C0002" if k == 12 else "C0001"
        hashes = {m: add_outcome(m, cluster) for m in members}
        rows.append({"id": f"T{k:04d}", "op": "decide_group", "structural_type": "decoy" if k == 12 else "event_fields",
                     "cluster": "C0001" if k < 12 else "C0002", "span": k == 12, "members": members, "text_sha256": hashes})
    pool_file = tmp_path / "pool.jsonl"
    write_jsonl(pool_file, rows)
    local = tmp_path / "local"
    write_jsonl(local / "pool_texts.jsonl", texts)
    monkeypatch.setattr(AN.SC, "POOL", pool_file)
    monkeypatch.setattr(AN, "LOCAL", local)
    return {r["id"]: r for r in rows}, local


def judge_plan():
    """The three instances' classes per item (the pipeline's designed disagreements)."""
    return {
        "T0001": ("NONE", "NONE", "NONE"), "T0002": ("NONE", "NONE", "EQUIV"), "T0003": ("NONE", "EQUIV", "IMPLIES"),
        "T0004": ("UNCLEAR", "UNCLEAR", "UNCLEAR"), "T0005": ("EXCLUDES", "EXCLUDES", "invalid"),
        "T0006": ("NONE", "EXCLUDES", "invalid"), "T0007": ("EXCLUDES", "EXCLUDES", "EXCLUDES"),
        "T0008": ("EXCLUDES", "EXCLUDES", "EXCLUDES"),
        "T0009": ("none", "none", "none"), "T0010": ("exactly_one", "exactly_one", "none"),
        "T0011": ("at_least_k:2", "at_least_k:2", "at_least_k:2"), "T0012": ("none", "at_most_one", "UNCLEAR"),
    }


def plan_answer(oid, cls, n):
    """The raw answer for one session's class: a relation (n is None) or a group of n members."""
    if n is None:
        return {"relation": cls} if cls != "invalid" else {"relation": "MAYBE"}
    if cls == "invalid":
        return {"relation": "MAYBE"}
    if cls == "UNCLEAR":
        return {"kind": "UNCLEAR"}
    if cls == "none":
        return {"kind": "none", "lower": 0, "upper": n, "chain": []}
    if cls == "at_most_one":
        return {"kind": "at_most_one", "lower": 0, "upper": 1, "chain": []}
    if cls == "exactly_one":
        return {"kind": "exactly_one", "lower": 1, "upper": 1, "chain": []}
    return {"kind": "at_least_k", "lower": 2, "upper": n, "chain": []}


def judge_everything(tmp_path, monkeypatch, pool, classes=None):
    """Three valid sessions on slice 1, each through the calling and called lines, a raw output and extract; the preflight runs after the
    first call. classes(oid, session) gives a session's class; by default it is judge_plan()."""
    plan = judge_plan()
    classes = classes or (lambda oid, inst: plan[oid][inst - 1])
    ids = sorted(pool)
    items = "".join(json.dumps({"id": i, "type": "x", "prompt": "p", "answer_format": "{}"}) + "\n" for i in ids).encode("utf-8")
    local, base = tmp_path / "judge-local", tmp_path / "base"
    (local / "slices").mkdir(parents=True)
    (local / "slices" / "slice1.jsonl").write_bytes(items)
    monkeypatch.setattr(SC, "check_frozen", lambda: None)
    monkeypatch.setattr(SC, "load_slices", lambda: {1: {"slice": 1, "ids": ids, "sha256": hashlib.sha256(items).hexdigest()}})
    for inst in (1, 2, 3):
        body = "\n".join(json.dumps({"id": oid, "answer": plan_answer(oid, classes(oid, inst),
                                                                       len(pool[oid]["members"]) if "members" in pool[oid] else None)})
                         for oid in ids)
        raw = json.dumps({"is_error": False, "subtype": "success", "num_turns": 1,
                          "result": "```jsonl\n" + body + "\n```"}).encode("utf-8")
        SC.record_calling(inst, 1, 1, SC.stdin_of(1, local), local, base)
        SC.record_called(inst, 1, 1, raw, local, base)
        if inst == 1:
            assert SC.preflight(base=base, local=local)["result"] == "pass"
        assert SC.extract(inst, 1, 1, base=base, local=local)["verdict"] == "valid"
    return base, local


def test_the_whole_pipeline_on_synthetic_data(tmp_path, monkeypatch):
    pool, _ = synthetic_pool(tmp_path, monkeypatch)
    base, local = judge_everything(tmp_path, monkeypatch, pool)
    summary = AN.run_consensus(base=base, sheet=local / "review" / "review.csv", local=local)
    assert summary["statuses"]["dropped"] == 1 and summary["statuses"]["review"] == 3
    consensus = {r["id"]: r for r in map(json.loads, (base / "consensus.jsonl").read_text().splitlines())}
    assert consensus["T0001"]["status"] == "unanimous" and consensus["T0005"]["status"] == "two_agree"
    assert consensus["T0004"]["status"] == "dropped" and consensus["T0003"]["status"] == "review"
    assert json.loads((base / "consensus_summary.json").read_text())["inputs"]["ledger.jsonl"]

    sheet = local / "review" / "review.csv"
    with open(sheet, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader([line for line in fh if not line.startswith("#")]))
    given = {"T0003": "NONE", "T0006": "UNCLEAR", "T0012": "at_most_one"}
    for r in rows:
        r["your_label"] = given.get(r["id"], r["consensus"]) if r["reason"] != "spot_check" else r["consensus"]
    AN.write_sheet(sheet, rows)
    with pytest.raises(AN.AnalyseError, match="already has labels"):
        AN.run_consensus(base=base, sheet=sheet, local=local)
    applied = AN.run_review(base=base, review_dir=local / "review", local=local)
    assert applied["changed_spot_check"] == 0
    assert AN.run_review(base=base, review_dir=local / "review", local=local) == applied  # a re-run reproduces the recorded labels
    final = {r["id"]: r for r in map(json.loads, (base / "final_labels.jsonl").read_text().splitlines())}
    assert final["T0003"]["final"] == "NONE" and final["T0006"]["final"] is None
    assert final["T0012"]["final"] == "at_most_one" and final["T0001"]["final"] == "NONE"
    reviews = [json.loads(line) for line in (base / "review_labels.jsonl").read_text().splitlines()]
    assert all(r["sheet_sha256"] and r["sheet"] == "review.csv" for r in reviews)

    report = AN.run_select(base)
    assert AN.run_select(base) == report  # the same labels give the same sample
    chosen = [json.loads(line)["id"] for line in (base / "selection.jsonl").read_text().splitlines()]
    assert report["relation_seats"]["drawn"] <= 120 and report["relations"]["trivial"]["drawn"] <= AN.TRIVIAL_CAP
    assert report["inputs"]["final_labels.jsonl"]
    assert set(chosen) <= {k for k, v in final.items() if v["final"] is not None}

    AN.run_fixture(base=base, local=tmp_path / "fixture-local")
    corpus = [json.loads(line) for line in (tmp_path / "fixture-local" / "corpus.jsonl").read_text().splitlines()]
    assert corpus and all("event_title" in c and c["controlled"] is True for c in corpus)
    fixture = json.loads((base / "fixture" / "fixture.json").read_text(encoding="utf-8"))
    assert fixture["controlled"] is False and fixture["generator"] == "polymarket-gamma-snapshot"
    assert not (base / "fixture" / "corpus.jsonl").exists()
    manifest = json.loads((base / "fixture_manifest.json").read_text(encoding="utf-8"))
    assert set(manifest["files"]) == {"candidates.jsonl", "gold/suite_labels.jsonl", "gold/meta.jsonl", "fixture.json"}
    assert manifest["inputs"]["selection.jsonl"]
    text = "".join(p.read_text(encoding="utf-8") for p in (base / "fixture").rglob("*.jsonl"))
    assert "Invented question" not in text and "Invented event" not in text

    predictions = {}
    for m_index, name in enumerate(MODELS + ["lexical", "heuristic"]):
        predictions[name] = {i: (final[i]["final"] if m_index % 2 == 0 else ("NONE" if pool[i]["op"] == "decide_relation" else "none"))
                             for i in chosen}
    path = tmp_path / "predictions.json"
    path.write_text(json.dumps(predictions), encoding="utf-8")
    result = AN.run_score(path, base=base, B=20, local=local)
    assert result["main"]["primary"]["reading"] in ("carries over", "does not carry over", "inconclusive")
    assert result["main"]["items"]["relation"] + result["main"]["items"]["group"] == len(chosen)
    assert set(result["sensitivity"]) == {"unanimous_only", "excluding_changed", "excluding_trivial"}
    assert all("items" in v for v in result["sensitivity"].values())
    assert set(result["main"]["anchors"]) == {"lexical", "heuristic"}
    assert result["double_void"]["sessions"] == 0 and result["inputs"]["predictions"]
    assert AN.run_score(path, base=base, B=20, local=local) == result

    group_id = next(i for i in chosen if pool[i]["op"] == "decide_group")
    bad = json.loads(json.dumps(predictions))
    bad["lexical"][group_id] = "NONE"  # a relation label on a group
    (tmp_path / "bad.json").write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(AN.AnalyseError, match="not a label of that item"):
        AN.run_score(tmp_path / "bad.json", base=base, B=5, local=local)
    partial = dict(predictions)
    partial["lexical"] = {i: "none" for i in chosen[1:]}
    (tmp_path / "partial.json").write_text(json.dumps(partial), encoding="utf-8")
    with pytest.raises(AN.AnalyseError, match="lexical"):
        AN.run_score(tmp_path / "partial.json", base=base, B=5, local=local)
    missing_model = {k: v for k, v in predictions.items() if k != MODELS[0]}
    (tmp_path / "missing.json").write_text(json.dumps(missing_model), encoding="utf-8")
    with pytest.raises(AN.AnalyseError, match="lack"):
        AN.run_score(tmp_path / "missing.json", base=base, B=5, local=local)

    # the recorded consensus, the labels and the fixture must all still agree with the ledger and the answers
    consensus_path = base / "consensus.jsonl"
    tampered = consensus_path.read_text(encoding="utf-8").replace('"status": "unanimous"', '"status": "majority"', 1)
    consensus_path.write_text(tampered, encoding="utf-8")
    with pytest.raises(AN.AnalyseError, match="differs from the consensus"):
        AN.run_score(path, base=base, B=5, local=local)


def test_a_review_sheet_must_match_the_consensus_and_its_draw(tmp_path, monkeypatch):
    pool, _ = synthetic_pool(tmp_path, monkeypatch)
    base, local = judge_everything(tmp_path, monkeypatch, pool)
    sheet = local / "review" / "review.csv"
    AN.run_consensus(base=base, sheet=sheet, local=local)
    with open(sheet, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader([line for line in fh if not line.startswith("#")]))
    for r in rows:
        r["your_label"] = r["consensus"] if r["reason"] == "spot_check" else ("NONE" if r["op"] == "decide_relation" else "none")
    tampered = [dict(r) for r in rows]
    tampered[0]["reason"] = "spot_check" if tampered[0]["reason"] != "spot_check" else "all_different"
    AN.write_sheet(sheet, tampered)
    with pytest.raises(AN.AnalyseError, match="reason"):
        AN.run_review(base=base, review_dir=local / "review", local=local)
    AN.write_sheet(sheet, rows[1:])  # a missing row
    with pytest.raises(AN.AnalyseError, match="missing"):
        AN.run_review(base=base, review_dir=local / "review", local=local)
    extra = rows + [{**rows[0], "id": "T0099"}]  # an id the pool does not have
    AN.write_sheet(sheet, extra)
    with pytest.raises(AN.AnalyseError, match="unknown id"):
        AN.run_review(base=base, review_dir=local / "review", local=local)
    labelled_unreviewed = rows + [{**rows[0], "id": "T0001", "reason": "all_different"}]  # a unanimous item, not in the sheet
    AN.write_sheet(sheet, labelled_unreviewed)
    with pytest.raises(AN.AnalyseError, match="appears twice|extra"):
        AN.run_review(base=base, review_dir=local / "review", local=local)


def test_the_escalation_sheet_is_required_and_holds_exactly_the_remaining_2_1_items(tmp_path, monkeypatch):
    # forty relations, each majority NONE (two sessions NONE, one EQUIV): ceil(10% of 40) = 4 spot-check items
    pool, _ = synthetic_pool(tmp_path, monkeypatch, relations=40, groups=False)
    base, local = judge_everything(tmp_path, monkeypatch, pool, classes=lambda oid, inst: "EQUIV" if inst == 3 else "NONE")
    sheet = local / "review" / "review.csv"
    AN.run_consensus(base=base, sheet=sheet, local=local)
    consensus = {r["id"]: r for r in read_jsonl_rows(base / "consensus.jsonl")}
    review, spot = AN.expected_sheet(consensus)
    assert not review and len(spot) == 4
    spot_ids = sorted(spot)
    labels = {oid: ("EXCLUDES" if k < 3 else "NONE") for k, oid in enumerate(spot_ids)}  # three of four changed
    with open(sheet, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader([line for line in fh if not line.startswith("#")]))
    for r in rows:
        r["your_label"] = labels[r["id"]]
    AN.write_sheet(sheet, rows)
    with pytest.raises(AN.AnalyseError, match="escalation sheet"):
        AN.run_review(base=base, review_dir=local / "review", local=local)
    escalation = local / "review" / "review_escalation.csv"
    assert escalation.exists()
    with open(escalation, encoding="utf-8", newline="") as fh:
        esc_rows = list(csv.DictReader([line for line in fh if not line.startswith("#")]))
    assert {r["id"] for r in esc_rows} == set(pool) - set(spot_ids)  # every remaining 2-1 item, and only those
    assert {r["reason"] for r in esc_rows} == {"escalation"}
    for r in esc_rows:
        r["your_label"] = "NONE"
    AN.write_sheet(escalation, esc_rows[1:])  # an incomplete escalation sheet is refused
    with pytest.raises(AN.AnalyseError, match="does not hold exactly"):
        AN.run_review(base=base, review_dir=local / "review", local=local)
    AN.write_sheet(escalation, esc_rows)
    summary = AN.run_review(base=base, review_dir=local / "review", local=local)
    assert summary["changed_spot_check"] == 3
    final = {r["id"]: r["final"] for r in map(json.loads, (base / "final_labels.jsonl").read_text().splitlines())}
    assert final[spot_ids[0]] == "EXCLUDES" and final[sorted(set(pool) - set(spot_ids))[0]] == "NONE"


def read_jsonl_rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_the_fixture_is_never_built_inside_the_repository(tmp_path):
    with pytest.raises(AN.AnalyseError, match="inside the repository"):
        AN.run_fixture(base=tmp_path, local=AUDIT / "not-here")


def test_load_texts_refuses_texts_that_do_not_match_the_pool(tmp_path, monkeypatch):
    pool, local = synthetic_pool(tmp_path, monkeypatch)
    assert AN.load_texts()
    lines = (local / "pool_texts.jsonl").read_text(encoding="utf-8").splitlines()
    lines[0] = lines[0].replace("Invented question", "Changed question")
    (local / "pool_texts.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(AN.AnalyseError, match="does not match"):
        AN.load_texts()


def test_placeholder_markets_are_dropped_and_counted(small_quotas):
    assert BP.ineligible(mk(1, "Placeholder 41 announced as the next Harbor captain?")) == "placeholder market"
    assert BP.ineligible(mk(2, "Will Quill Harbor win?", title="placeholder 3")) == "placeholder market"
    events = synthetic_events() + [ev(900, 5000, [mk(9001, "Placeholder 4 wins the cup?"), mk(9002, "Will Vale Harbor reach 7 by June?")],
                                      category="harbor")]
    result = build(events)
    assert result["summary"]["stats"]["market dropped: placeholder market"] == 1
    assert not any("placeholder" in t["question"].lower() for t in result["texts"])


def test_review_sheets_and_the_fixture_inside_the_repository_are_refused(tmp_path):
    with pytest.raises(AN.AnalyseError, match="inside the repository"):
        AN.run_review(base=tmp_path, review_dir=AUDIT / "review-here")
    with pytest.raises(AN.AnalyseError, match="inside the repository"):
        AN.run_consensus(base=tmp_path, sheet=AUDIT / "review.csv", local=tmp_path)
    with pytest.raises(AN.AnalyseError, match="inside the repository"):
        AN.run_fixture(base=tmp_path, local=AUDIT / "fixture-here")


def test_the_recorded_inputs_include_the_rules_the_judges_and_the_runs_read():
    for path in ("tools/reproduce.py", "profiles/mentat-hard-v1.json", "python/mentat_bench/children/ollama_models.json",
                 "results/v0.2.0/baselines.json", "results/v0.2.0/cells/d3_27b-q8_0.json"):
        assert path in SC.FROZEN_INPUTS
    assert len([p for p in SC.FROZEN_INPUTS if "/cells/" in p]) == 17 and len(SC.FROZEN_INPUTS) == 27
