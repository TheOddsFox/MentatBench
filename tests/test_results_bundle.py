"""The results bundle (results/): leaderboard numbers, CSV, index, README tables and links, privacy, predictions, cells.

Fast, and it reads only committed files (results/, docs/results/, release/, fixtures/mentat_hard_test/). It never reads runs/.
The builder (results/v0.1.0/build_bundle.py) regenerates the bundle from runs/ and exits 1 on any disagreement; these tests check the
committed bundle against the committed sources, and check the README's tables cell by cell against the leaderboard JSON.
"""

import csv
import gzip
import importlib.util
import io
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
LB_PATH = RESULTS / "v0.1.0" / "leaderboard.json"
README = RESULTS / "README.md"
MAIN = ROOT / "docs" / "results" / "mentat-hard-20261030.json"
PRIVATE = ROOT / "docs" / "results" / "mentat-hard-private-20261030.json"
TEST_FIXTURE = ROOT / "fixtures" / "mentat_hard_test"
GATES = ROOT / "release" / "v0.1.0" / "record" / "check_gates.md"
TOL = 1e-12  # the leaderboard copies the committed values, so they agree exactly

# The same pattern as build_bundle.py's ABSOLUTE (checked in test_builder_pattern_matches_the_test).
ABS_PATTERN = r"(?<![A-Za-z])[A-Za-z]:[\\/]|(?<![\w.])/(?:c|home|tmp|var|Users)/"
ABSOLUTE = re.compile(ABS_PATTERN)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.\w+")
# Strings that must not appear in any tracked text under docs/, results/, release/ or README.md.
PERSONAL = ("falty", "C:\\Users", "C:/Users", "/c/Users", "AppData", "scratchpad")
PATH_KEYS = {"readme", "leaderboard_json", "leaderboard_csv", "predictions_dir", "cells_dir", "runcards_dir", "builder",
             "release_manifest", "release_gate_output", "attestations", "results_md", "results_json", "private_results_md",
             "private_results_json", "prereg"}
BINARY_SUFFIXES = {".gz", ".parquet", ".bin", ".pyc", ".png", ".jpg", ".jpeg", ".pdf", ".zip", ".sqlite"}
NO_PREDICTIONS = {"empty", "oracle"}  # the two baselines that write no predictions file (their predictions field is null)


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def entries(lb: dict) -> list[dict]:
    return lb["models"] + lb["baselines"]


def close(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, list) or isinstance(b, list):
        return len(a) == len(b) and all(close(x, y) for x, y in zip(a, b))
    return abs(a - b) <= TOL


def same_block(lb_block, src_block) -> bool:
    """A leaderboard {point, ci95} block against a results block {point, ci95}."""
    if src_block is None:
        return lb_block is None
    return lb_block is not None and close(lb_block["point"], src_block["point"]) and close(lb_block["ci95"], src_block.get("ci95"))


def slug(heading: str) -> str:
    """The GitHub anchor of a heading: lower case, punctuation removed (underscores kept), spaces to hyphens."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def anchors(md_text: str) -> set[str]:
    return {slug(h) for h in re.findall(r"^#{1,6} (.+)$", md_text, re.M)}


def links(md_text: str) -> list[str]:
    return re.findall(r"\]\(([^)\s]+)\)", md_text)


def result_files() -> list[Path]:
    """Every file under results/, except bytecode caches (python writes them when the builder is imported)."""
    return sorted(p for p in RESULTS.rglob("*") if p.is_file() and "__pycache__" not in p.parts)


def text_of(path: Path) -> str:
    data = path.read_bytes()
    if path.suffix == ".gz":
        data = gzip.decompress(data)
    return data.decode("utf-8", errors="replace")


def load_builder():
    spec = importlib.util.spec_from_file_location("build_bundle", RESULTS / "v0.1.0" / "build_bundle.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ci3(block) -> str:
    """Test-side formatting of a metric: 'point [lo, hi]' to three decimals, or the point alone when there is no interval."""
    if block is None:
        return "—"
    if block.get("ci95") is None:
        return f"{block['point']:.3f}"
    lo, hi = block["ci95"]
    return f"{block['point']:.3f} [{lo:.3f}, {hi:.3f}]"


def ci_dp(block, dp: int) -> str:
    if block is None:
        return "—"
    if block.get("ci95") is None:
        return f"{block['point']:.{dp}f}"
    lo, hi = block["ci95"]
    return f"{block['point']:.{dp}f} [{lo:.{dp}f}, {hi:.{dp}f}]"


def f3(x) -> str:
    return "—" if x is None else f"{x:.3f}"


# ---------------------------------------------------------------- numbers

def test_leaderboard_numbers_equal_committed_results():
    lb, main = load(LB_PATH), load(MAIN)
    assert lb["ranking"] == main["ranking"]
    for e in lb["models"]:
        assert e["rank"] == main["ranking"].index(e["id"]) + 1
    for e in entries(lb):
        row = main["hf"] if e["id"] == "hf" else (main["models"].get(e["id"]) or main["baselines"].get(e["id"]))
        m = e["metrics"]
        assert same_block(m["headline"], row.get("headline")), e["id"]
        assert same_block(m["relation"], row.get("parts", {}).get("relation_mcc")), e["id"]
        assert same_block(m["group"], row.get("parts", {}).get("group_mcc")), e["id"]
        assert same_block(m["binding"], row.get("binding_mcc")), e["id"]
        assert same_block(m["reading"], row.get("diagnostics", {}).get("reading_mcc")), e["id"]
        assert same_block(m["held_out_relation"], row.get("diagnostics", {}).get("held_out_relation_mcc")), e["id"]
        assert same_block(m["seen_relation"], row.get("diagnostics", {}).get("seen_relation_mcc")), e["id"]
        assert same_block(m["q_n_repaired"], row["Q_N"]["repaired"]), e["id"]
        assert close(m["q_n_strict"]["point"], row["Q_N"]["strict"]), e["id"]
        gap, src = e["gap_to_reference"], row.get("gap_to_reference")
        if src is None:
            assert gap is None, e["id"]
        else:
            assert same_block(gap["headline"], src["headline"]), e["id"]
            assert same_block(gap["relation"], src["relation_mcc"]), e["id"]
            assert same_block(gap["group"], src["group_mcc"]), e["id"]
            assert same_block(gap["binding"], src["binding_mcc"]), e["id"]
            assert same_block(gap["q_n_repaired"], src["Q_N"]), e["id"]
        if e["dev"] is not None and "relation" in e["dev"]:
            assert close(e["dev"]["headline"], row["dev"]["headline"]), e["id"]
            assert close(e["dev"]["relation"], row["dev"]["relation_mcc"]), e["id"]
        if "depth" in row:
            assert e["depth_accuracy"] == row["depth"], e["id"]
    assert lb["headline_interval_overlaps"] == main["headline_interval_overlaps"]


def test_leaderboard_private_numbers_equal_private_results():
    lb, private = load(LB_PATH), load(PRIVATE)
    for e in entries(lb):
        if e["private"] is None:
            continue
        p = e["private"]
        src = private["ceilings"]["gold_view"] if e["id"] == "gold_view" else private["runs"][e["id"]]
        assert same_block(p["headline"], src["headline"]), e["id"]
        assert same_block(p["relation"], src["relation"]), e["id"]
        assert same_block(p["group"], src["group"]), e["id"]
        assert same_block(p["binding"], src.get("binding_secondary")), e["id"]
        if "public_test" in src:
            assert close(p["public_test_headline"], src["public_test"]["headline"]), e["id"]
            assert close(p["headline_private_minus_public"], src["headline_private_minus_public"]), e["id"]
    checks = lb["private_checks"]
    assert checks["score_decisions_match_ci"] is private["score_decisions_match_ci"] is True
    assert checks["spearman_rho_public_vs_private"] == private["order"]["spearman_rho"]
    assert checks["pairs_disjoint_in_both"] == private["order"]["pairs_disjoint_in_both"]
    assert abs(checks["max_abs_headline_change"]["value"]) <= 0.025
    assert checks["max_abs_headline_change"]["run"] == "clef-flash_9b"


def test_rank_is_the_headline_order():
    lb = load(LB_PATH)
    by_headline = [e["id"] for e in sorted(lb["models"], key=lambda e: -e["metrics"]["headline"]["point"])]
    assert by_headline == lb["ranking"] == [e["id"] for e in sorted(lb["models"], key=lambda e: e["rank"])]
    assert [e["rank"] for e in lb["models"]] == list(range(1, 16))
    assert all(e["rank"] is None for e in lb["baselines"])


def test_resolved_comparisons_follow_from_the_intervals():
    lb = load(LB_PATH)
    expected = 0
    for e in entries(lb):
        gap = e["gap_to_reference"]
        if gap is None:
            continue
        expected += sum(gap[stat] is not None for stat in ("headline", "relation", "group", "binding"))
    assert len(lb["resolved_comparisons"]) == expected
    for row in lb["resolved_comparisons"]:
        block = row["gap_reference_minus_run"]
        lo, hi = block["ci95"]
        excludes = not (lo <= 0 <= hi)
        assert row["excludes_zero"] is excludes, row["run"]
        assert row["higher"] == (None if not excludes else ("reference" if block["point"] > 0 else "run")), row["run"]


def test_flags_and_floor_fields():
    lb = load(LB_PATH)
    by_id = {e["id"]: e for e in entries(lb)}
    assert "base_not_repaired" in by_id["tev1_0.8b-q8_0"]["flags"]
    assert "abstains_for_length" in by_id["laya_latest"]["flags"]
    for name in ("tev1_0.8b-q8_0", "d1-omni_600m-q8_0"):
        assert by_id[name]["relation_floor_disabled"] is True and by_id[name]["relation_floor"] is None, name
        assert "names_no_relations_under_floor" in by_id[name]["flags"], name
    assert by_id["d3_27b-q8_0"]["relation_floor_disabled"] is False and by_id["d3_27b-q8_0"]["relation_floor"] > 0
    assert "1.7976931348623157e+308" not in LB_PATH.read_text(encoding="utf-8")
    for e in lb["models"]:
        assert e["names_relations_under_floor"] is (not e["relation_floor_disabled"]), e["id"]
        assert e["notes"] or not e["flags"], e["id"]


def test_csv_matches_json():
    lb = load(LB_PATH)
    rows = list(csv.DictReader(io.StringIO((RESULTS / "v0.1.0" / "leaderboard.csv").read_text(encoding="utf-8"))))
    assert len(rows) == len(entries(lb)) == 22
    for row, e in zip(rows, entries(lb)):
        for col in ("id", "display_name", "kind", "family", "ollama_tag", "digest", "params", "quant", "license"):
            assert (row[col] or None) == e[col], (e["id"], col)
        assert row["rank"] == ("" if e["rank"] is None else str(e["rank"])), e["id"]
        for col, value in csv_expected(e).items():
            if value is None:
                assert row[col] == "", (e["id"], col)
            elif isinstance(value, str):
                assert row[col] == value, (e["id"], col)
            else:
                assert abs(float(row[col]) - value) <= TOL, (e["id"], col)


def csv_expected(e: dict) -> dict:
    """The CSV value of each numeric column for one leaderboard entry (None where the CSV is blank)."""
    m, gap, private = e["metrics"], e["gap_to_reference"], e["private"]

    def point(block):
        return None if block is None else block["point"]

    def bound(block, i):
        return None if block is None or block["ci95"] is None else block["ci95"][i]

    gap_h = gap["headline"] if gap else None
    return {
        "size_bytes": e["size_bytes"],
        "headline_point": point(m["headline"]), "headline_lo": bound(m["headline"], 0), "headline_hi": bound(m["headline"], 1),
        "relation_point": point(m["relation"]), "relation_lo": bound(m["relation"], 0), "relation_hi": bound(m["relation"], 1),
        "group_point": point(m["group"]), "group_lo": bound(m["group"], 0), "group_hi": bound(m["group"], 1),
        "binding_point": point(m["binding"]), "binding_lo": bound(m["binding"], 0), "binding_hi": bound(m["binding"], 1),
        "reading_point": point(m["reading"]), "reading_lo": bound(m["reading"], 0), "reading_hi": bound(m["reading"], 1),
        "held_out_relation_point": point(m["held_out_relation"]), "held_out_relation_lo": bound(m["held_out_relation"], 0),
        "held_out_relation_hi": bound(m["held_out_relation"], 1), "seen_relation_point": point(m["seen_relation"]),
        "q_n_repaired_point": point(m["q_n_repaired"]), "q_n_repaired_lo": bound(m["q_n_repaired"], 0),
        "q_n_repaired_hi": bound(m["q_n_repaired"], 1), "q_n_strict": m["q_n_strict"]["point"],
        "gap_headline_point": point(gap_h), "gap_headline_lo": bound(gap_h, 0), "gap_headline_hi": bound(gap_h, 1),
        "dev_headline": None if e["dev"] is None else e["dev"].get("headline"), "relation_floor": e["relation_floor"],
        "private_headline_point": point(private["headline"]) if private else None,
        "private_headline_lo": bound(private["headline"], 0) if private else None,
        "private_headline_hi": bound(private["headline"], 1) if private else None,
        "build_wall_min": e["runtime"]["build_wall_min"],
    }


# ---------------------------------------------------------------- README tables against the JSON

def readme_tables() -> list[tuple[str, list[list[str]]]]:
    """(header line, body rows as cell lists) for every markdown table in the README."""
    tables, current = [], []
    for line in README.read_text(encoding="utf-8").splitlines() + [""]:
        if line.startswith("|"):
            current.append(line)
            continue
        if current:
            body = [[c.strip() for c in row.strip().strip("|").split("|")] for row in current[2:]]
            tables.append((current[0], body))
            current = []
    return tables


def plain(cell: str) -> str:
    """A table cell without its emphasis marks and flag symbols."""
    return cell.replace("*", "").replace("‡", "").replace("†", "").strip()


def table_for(prefix: str) -> list[list[str]]:
    found = [body for header, body in readme_tables() if header.startswith(prefix)]
    assert len(found) == 1, f"{len(found)} tables start with {prefix!r}"
    return found[0]


def test_readme_leaderboard_table_matches_json():
    lb = load(LB_PATH)
    rows = table_for("| Rank | Run id | Model")
    by_id = {plain(r[1]).strip("`"): r for r in rows}
    assert set(by_id) == {e["id"] for e in entries(lb)}
    for e in entries(lb):
        r = [plain(c) for c in by_id[e["id"]]]
        assert r[0] == ("—" if e["rank"] is None else str(e["rank"])), e["id"]
        assert r[2].startswith(e["display_name"]), e["id"]
        assert r[3] == (e["params"] or "—") and r[4] == (e["quant"] or "—"), e["id"]
        assert r[5] == ci3(e["metrics"]["headline"]), e["id"]
        assert r[6] == ci3(e["metrics"]["relation"]) and r[7] == ci3(e["metrics"]["group"]), e["id"]
        assert r[8] == ci3(e["metrics"]["binding"]), e["id"]
        if e["gap_to_reference"] is None:
            assert r[9] in ("reference", "—"), e["id"]
        else:
            assert r[9] == ci3(e["gap_to_reference"]["headline"]), e["id"]
        assert r[10] == (ci3(e["private"]["headline"]) if e["private"] else "—"), e["id"]
        build = e["runtime"]["build_wall_min"]
        assert r[11] == ("—" if build is None else str(build)), e["id"]
        if e["kind"] == "system_one_model":
            assert r[12] == (e["license"] or "not recorded"), e["id"]
        else:
            assert r[12] == (e["license"] or "—"), e["id"]
    for e in lb["models"]:
        for flag_name, symbol in (("base_not_repaired", "‡"), ("abstains_for_length", "†")):
            if flag_name in e["flags"]:
                assert symbol in by_id[e["id"]][2], (e["id"], symbol)
    assert plain(by_id["hf"][12]) == "Apache-2.0", "the hf licence is missing or in the wrong casing"


def test_readme_private_table_matches_json():
    lb = load(LB_PATH)
    rows = {plain(r[0]): r for r in table_for("| Run | Private headline")}
    for e in lb["models"] + [x for x in lb["baselines"] if x["id"] in ("heuristic", "gold_view")]:
        p = e["private"]
        r = rows[e["display_name"]]
        assert r[1] == ci3(p["headline"]) and r[2] == ci3(p["relation"]) and r[3] == ci3(p["group"]), e["id"]
        assert r[4] == ci3(p["binding"]), e["id"]
        assert r[5] == ("—" if p["public_test_headline"] is None else f3(p["public_test_headline"])), e["id"]
        d = p["headline_private_minus_public"]
        assert r[6] == ("—" if d is None else f"{d:+.3f}"), e["id"]


def test_readme_diagnostic_tables_match_json():
    lb = load(LB_PATH)
    by_name = {e["display_name"]: e for e in entries(lb)}
    for r in table_for("| Run | Reading MCC"):
        assert ci3(by_name[plain(r[0])]["metrics"]["reading"]) == plain(r[1]), r[0]
    for r in table_for("| Run | Seen relation MCC"):
        e = by_name[plain(r[0])]
        assert r[1] == f3(e["metrics"]["seen_relation"]["point"]), e["display_name"]
        assert r[2] == ci3(e["metrics"]["held_out_relation"]), e["display_name"]
        assert r[3] == f3(e["metrics"]["seen_relation"]["point"] - e["metrics"]["held_out_relation"]["point"]), e["display_name"]
    for r in table_for("| Run | Q_N, repaired"):
        e = by_name[plain(r[0])]
        assert plain(r[1]) == ci_dp(e["metrics"]["q_n_repaired"], 2), e["display_name"]
        assert plain(r[2]) == f"{e['metrics']['q_n_strict']['point']:.2f}", e["display_name"]


def test_readme_depth_table_matches_json():
    lb = load(LB_PATH)
    by_name = {e["display_name"]: e for e in entries(lb)}
    rows = table_for("| Run | 0 | 1 | 2")
    n = lb["depth_n"]
    buckets = ("0", "1", "2", "3", "4+", "NONE")
    assert [plain(c) for c in rows[0][1:7]] == [f"{n[k]:,}" for k in buckets]
    for r in rows[1:]:
        e = by_name[plain(r[0])]
        d = e["depth_accuracy"]
        assert [plain(c) for c in r[1:7]] == [f3(d[b]["accuracy"]) for b in buckets], e["id"]
        allc = sum(d[b]["correct"] for b in buckets) / sum(d[b]["n"] for b in buckets)
        assert plain(r[7]) == f3(allc), e["id"]


def test_readme_repair_speed_dev_family_tables_match_json():
    lb = load(LB_PATH)
    by_name = {e["display_name"]: e for e in entries(lb)}
    for r in table_for("| Run | Groups dropped"):
        rep = by_name[plain(r[0])]["repair"]
        assert plain(r[1]) == f"{rep['groups_dropped']:,} / {rep['groups_committed']:,} ({rep['groups_dropped_pct']:.2f}%)"
        assert plain(r[2]) == f"{rep['relations_dropped']:,}"
    for r in table_for("| Run | Build wall time"):
        assert plain(r[1]) == str(by_name[plain(r[0])]["runtime"]["build_wall_min"]), r[0]
    for r in table_for("| Run | Dev headline"):
        e = by_name[plain(r[0])]
        assert plain(r[1]) == f"{e['dev']['headline']:.4f}", e["id"]
        assert plain(r[2]) == f3(e["metrics"]["headline"]["point"]), e["id"]
        assert plain(r[3]) == f"{e['metrics']['headline']['point'] - e['dev']['headline']:+.4f}", e["id"]
    for r in table_for("| Model | Test headline"):
        point = next(p for p in lb["d3_size_curve"] if p["display_name"] == plain(r[0]))
        assert plain(r[1]) == ci3(point["headline"]), point["id"]
        assert plain(r[2]) == f3(point["relation"]) and plain(r[3]) == f3(point["group"]), point["id"]
        assert plain(r[4]) == f"{point['dev_headline']:.3f}", point["id"]


def test_readme_fixture_and_gate_tables_match_json():
    lb = load(LB_PATH)
    files = lb["provenance"]["fixture"]["files"]
    rows = table_for("| File (fixtures")
    assert len(rows) == len(files)
    for r in rows:
        path = plain(r[0]).strip("`")
        assert plain(r[1]) == f"{files[path]['bytes']:,}" and plain(r[2]).strip("`") == files[path]["sha256"], path
    gates = lb["release_gates"]["rows"]
    rows = table_for("| Gate | Name")
    assert [(int(plain(r[0])), plain(r[1]), plain(r[2])) for r in rows] == [(g["gate"], g["name"], g["status"]) for g in gates]


def test_readme_headline_strings_match_the_leaderboard():
    lb = load(LB_PATH)
    text = README.read_text(encoding="utf-8")
    for e in entries(lb):
        if e["metrics"]["headline"] is not None:
            assert ci3(e["metrics"]["headline"]) in text, e["id"]


# ---------------------------------------------------------------- index, links and pointers

def test_index_paths_exist_and_latest_is_consistent():
    idx = load(RESULTS / "index.json")
    versions = [v["version"] for v in idx["versions"]]
    latest = [v for v in idx["versions"] if v["status"] == "latest"]
    assert len(latest) == 1 and latest[0]["version"] == idx["latest"] == "v0.1.0"
    assert all(v["superseded_by"] in versions or v["superseded_by"] is None for v in idx["versions"])
    checked = 0
    for version in idx["versions"]:
        for key, value in version.items():
            if key in PATH_KEYS and value is not None:
                assert (ROOT / value).exists(), (version["version"], key, value)
                checked += 1
    assert checked >= 13  # the v0.1.0 entry alone has 13 file paths


def test_bundle_identity():
    """One release, v0.1.0: the leaderboard says so, each row's run card lives under release/v0.1.0/runcards/, and every
    cell's and row's predictions path exists (None for the two baselines that write no predictions). The index has no
    retired measurement key anywhere, and its v0.1.0 entry names the record by role, each path present."""
    lb = load(LB_PATH)
    assert lb["release"] == "v0.1.0"
    rows = {e["id"]: e for e in entries(lb)}
    for e in rows.values():
        assert e["runcard"] == f"release/v0.1.0/runcards/{e['id']}.json", e["id"]
        assert (ROOT / e["runcard"]).is_file(), e["id"]
        assert (ROOT / e["cell"]).is_file(), e["id"]
        if e["id"] in NO_PREDICTIONS:
            assert e["predictions"] is None, e["id"]
        else:
            assert e["predictions"] == f"results/v0.1.0/predictions/{e['id']}.jsonl.gz", e["id"]
            assert (ROOT / e["predictions"]).is_file(), e["id"]
    for path in sorted((RESULTS / "v0.1.0" / "cells").glob("*.json")):
        cell = load(path)
        assert cell["predictions"] == rows[path.stem]["predictions"], path.name
        if cell["predictions"] is not None:
            assert (ROOT / cell["predictions"]).is_file(), path.name
    index_text = (RESULTS / "index.json").read_text(encoding="utf-8")
    retired_key = "_".join(("measurement", "release"))  # built from parts: tests/test_labels.py scans this file's text
    assert retired_key not in index_text
    entry = next(v for v in json.loads(index_text)["versions"] if v["version"] == "v0.1.0")
    for role in ("prereg", "release_manifest", "release_gate_output", "attestations", "runcards_dir", "results_md",
                 "results_json", "private_results_md", "private_results_json"):
        assert entry.get(role) and (ROOT / entry[role]).exists(), role


def test_readme_links_resolve():
    text = README.read_text(encoding="utf-8")
    for link in links(text):
        if link.startswith(("http://", "https://")):
            continue
        path, _, anchor = link.partition("#")
        target = (RESULTS / path).resolve() if path else README
        assert target.exists(), link
        if anchor and target.suffix == ".md":
            assert anchor in anchors(target.read_text(encoding="utf-8")), link


def test_repository_pointers_resolve():
    root_readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "## Results" in root_readme and "results/README.md" in root_readme
    for doc in ("docs/results/mentat-hard-20261030.md", "docs/results/mentat-hard-private-20261030.md"):
        path = ROOT / doc
        head = path.read_text(encoding="utf-8").splitlines()[:6]
        pointer = [line for line in head if "results/README.md" in line]
        assert len(pointer) == 1, doc
        target = re.search(r"\]\(([^)]+)\)", pointer[0]).group(1)
        assert (path.parent / target).resolve().exists(), doc
    for link in links(root_readme):
        if link.startswith(("http://", "https://", "#")):
            continue
        assert (ROOT / link.partition("#")[0]).exists(), link


def test_gate_rows_match_the_gate_output():
    lb = load(LB_PATH)
    rows = [line.split("|")[1:-1] for line in GATES.read_text(encoding="utf-8").splitlines()
            if line.startswith("|") and not line.startswith("| gate") and not set(line) <= {"|", "-", " "}]
    assert len(rows) == len(lb["release_gates"]["rows"]) == 11
    for src, row in zip(rows, lb["release_gates"]["rows"]):
        assert src[0].strip() == row["name"] and src[1].strip() == row["status"], row["gate"]


# ---------------------------------------------------------------- privacy and hygiene

def test_builder_pattern_matches_the_test():
    assert load_builder().ABSOLUTE.pattern == ABS_PATTERN


def tracked_text_files() -> list[Path]:
    """Every text file under docs/, results/ and release/, and README.md. Binary suffixes are skipped."""
    found = [ROOT / "README.md"]
    for folder in ("docs", "results", "release"):
        found += [p for p in (ROOT / folder).rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    return sorted(p for p in found if p.suffix not in BINARY_SUFFIXES)


def test_no_local_paths_or_personal_strings_in_docs_results_release_or_readme():
    scanned = 0
    for path in tracked_text_files():
        rel = path.relative_to(ROOT).as_posix()
        text = text_of(path)
        scanned += 1
        for word in PERSONAL:
            assert word not in text, f"{rel}: contains {word!r}"
        assert not ABSOLUTE.search(text), f"{rel}: an absolute path"
        assert not EMAIL.search(text), f"{rel}: an email address"
    assert scanned > 100  # the scan reaches docs/, results/, release/ and the README


def test_no_private_split_items_in_results():
    """Every item id in the results is a public test id. The private split's ids are not in the repository, so none can appear."""
    public = set()
    for row in map(json.loads, (TEST_FIXTURE / "candidates.jsonl").read_text(encoding="utf-8").splitlines()):
        public.add(row["qid"])
        for key in ("id", "a", "b"):
            if row.get(key):
                public.add(row[key])
        public.update(row.get("members") or [])
    for row in map(json.loads, (TEST_FIXTURE / "gold" / "suite_labels.jsonl").read_text(encoding="utf-8").splitlines()):
        public.add(row["qid"])
    for path in result_files():
        for item in re.findall(r"mh:[0-9a-f]+:\d+", text_of(path)):
            assert item in public, f"{path.name}: {item} is not a public test id"
    assert "qid" not in json.dumps(load(PRIVATE)), "the private results carry item ids"


def test_bundle_size_within_budget():
    total = sum(p.stat().st_size for p in result_files())
    assert total <= 25 * 1024 * 1024, total


# ---------------------------------------------------------------- predictions and cells

def test_cells_and_predictions_cover_the_runs():
    lb = load(LB_PATH)
    cells = {p.stem for p in (RESULTS / "v0.1.0" / "cells").glob("*.json")}
    preds = {p.name[: -len(".jsonl.gz")] for p in (RESULTS / "v0.1.0" / "predictions").glob("*.jsonl.gz")}
    assert cells == {e["id"] for e in entries(lb)} and len(cells) == 22
    assert preds == {e["id"] for e in entries(lb) if e["predictions"]} and len(preds) == 20
    for e in entries(lb):
        assert (ROOT / e["cell"]).exists(), e["id"]


def test_predictions_are_deterministic_gzip():
    for path in (RESULTS / "v0.1.0" / "predictions").glob("*.jsonl.gz"):
        data = path.read_bytes()
        assert data[:2] == b"\x1f\x8b" and data[4:8] == b"\x00\x00\x00\x00", path.name


def test_predictions_rescore_to_the_cell_scores():
    """Each predictions file rebuilds the cell's own score_decisions.json (the same rule the builder applies)."""
    bundle = load_builder()
    br = bundle.load_build_results()
    lb = load(LB_PATH)
    for e in entries(lb):
        if not e["predictions"]:
            continue
        blob = (ROOT / e["predictions"]).read_bytes()
        rows = [json.loads(line) for line in gzip.decompress(blob).decode("utf-8").splitlines()]
        assert len(rows) == 10716, e["id"]
        committed = load(RESULTS / "v0.1.0" / "cells" / f"{e['id']}.json")
        assert bundle.same_scored(bundle.rescore(blob, 3), committed["score_decisions"], None, br), e["id"]
        if e["kind"] == "system_one_model":
            abstained = sum(r["abstained"] for r in rows if r["op"] == "decide_relation")
            assert abstained == committed["child_stats"]["ops"]["decide_relation"]["abstained_overlength"], e["id"]


def test_fixture_counts_match_the_results_document():
    counts = load(LB_PATH)["provenance"]["fixture"]["item_counts"]
    assert counts["relation_questions"] == 6000 and counts["relation_none"] == 1500
    assert counts["group_questions"] == 1184 and counts["reading_questions"] == 184
    assert counts["binding_questions"] == 3348 and counts["scored_items"] == 10716
