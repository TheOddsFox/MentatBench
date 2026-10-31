"""Leakage checks (spec s10: leakage checks; plan Step 5). What must never reach a submission or a model, and what the
public fixtures may show:

(a) `build` with a non-oracle adapter never opens a file under <fixture>/gold or the --gold directory (audit hook;
    a positive control shows the hook sees the oracle's gold reads);
(b) corpus rows carry only the public fields (id, text, label, controlled, event_title, and the optional context);
(c) no query or corpus field carries a gold-revealing word, and the query numbering does not predict the decision class;
(d) every fixture directory holds only its public files;
(e) sealed submissions contain no gold paths;
(f) a local build sets no proxy environment and the harness imports no HTTP client outside the child allowlist.
(g) a non-gold-backed adapter never receives more than the public outcome context, and its binding evidence never carries
    a gold-only REF (axiom or event id): they are stripped and counted; the gold-backed adapters keep theirs.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

from mentat_bench import cli, ir
from mentat_bench.adapters.base import usage
from mentat_bench.adapters.mock import GoldViewAdapter, OracleAdapter
from mentat_bench.harness import fixed
from mentat_bench.harness.fixed import GOLD_ONLY_REF, _strip_gold_refs
from mentat_bench.manifest import DEFAULT_PROFILE

REPO = Path(__file__).resolve().parents[1]
PYTHON_DIR = REPO / "python"
FIXTURES = REPO / "fixtures"
TOY10K = FIXTURES / "toy10k"
FIXTURE_NAMES = sorted(p.name for p in FIXTURES.iterdir() if p.is_dir() and p.name != "manifests")
CORPUS_FIELDS = {"id", "text", "label", "controlled", "event_title", "context"}  # context: the optional league rule card
DENY_WORDS = re.compile(r"\b(gold|answer|decision|sat|unsat|premises)\b", re.IGNORECASE)
# candidates.jsonl: the public question list of a curated suite (Mentat-Hard, docs/mentat_hard_contracts.md)
PUBLIC_TOP_LEVEL = {"corpus.jsonl", "queries.jsonl", "queries_extra.jsonl", "fixture.json", "candidates.jsonl", "gold"}

# A child that records its environment (for check f) and answers every request as unresolved / no relation.
PROBE_CHILD = r'''
import json, os, sys
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    json.dump(sorted(os.environ), fh)
for line in sys.stdin:
    req = json.loads(line)
    if req["op"] == "decide_relation":
        out = {"relation": "NONE"}
    else:
        out = {"status": "unresolved", "class": None, "negated": False, "missing": ["no_source"]}
    out["id"] = req["id"]
    print(json.dumps(out), flush=True)
'''


# ---- (a) audit of file opens during a build ------------------------------------------------------------------------

_OPENED: list[str] | None = None


def _audit(event: str, args: tuple) -> None:
    if event == "open" and _OPENED is not None and args and isinstance(args[0], (str, bytes, os.PathLike)):
        path = os.fsdecode(os.fspath(args[0]))
        _OPENED.append(os.path.normcase(os.path.abspath(path)))


sys.addaudithook(_audit)  # audit hooks cannot be removed, so the hook only records while _OPENED is set


@contextmanager
def _watch_opens():
    global _OPENED
    _OPENED = []
    try:
        yield _OPENED
    finally:
        _OPENED = None


def _under(paths: list[str], root: Path) -> list[str]:
    base = os.path.normcase(os.path.abspath(root))
    return [p for p in paths if p == base or p.startswith(base + os.sep)]


@pytest.fixture(scope="module")
def small_corpus(tmp_path_factory) -> Path:
    """The first 400 rows of the retained toy10k fixture, written to a temp corpus."""
    lines = (TOY10K / "corpus.jsonl").read_text(encoding="utf-8").splitlines()[:400]
    path = tmp_path_factory.mktemp("corpus") / "corpus.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def probe(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("probe") / "probe_child.py"
    path.write_text(PROBE_CHILD, encoding="utf-8")
    return path


def _adapter_args(kind: str, probe: Path, env_out: Path) -> list[str]:
    if kind == "empty":
        return ["--adapter", "empty"]
    if kind == "local":
        return ["--adapter", "local", "--local-arg", sys.executable, "--local-arg", str(probe), "--local-arg", str(env_out)]
    module = {"heuristic": "mentat_bench.children.heuristic_child", "lexical": "mentat_bench.children.lexical_child"}[kind]
    return ["--adapter", "local", "--local-arg", sys.executable, "--local-arg=-m", "--local-arg", module]


ADAPTERS = [
    "empty",
    pytest.param("local", marks=pytest.mark.slow),  # each child build is ~20 s; the full run covers them
    "heuristic",  # pure Python child, no model: kept in the default run as the child-process gold-leakage check
    pytest.param("lexical", marks=[pytest.mark.slow,
                                   pytest.mark.skipif(importlib.util.find_spec("mentat_bench.children.lexical_child") is None,
                                                      reason="lexical child not present")]),
]


@pytest.mark.parametrize("kind", ADAPTERS)
def test_build_never_opens_gold_for_non_oracle_adapters(kind, tmp_path, small_corpus, probe, monkeypatch):
    gold = TOY10K / "gold"
    fixture_gold = gold.resolve()
    monkeypatch.setenv("PYTHONPATH", str(PYTHON_DIR) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    monkeypatch.setenv("MB_ENGINE", str(tmp_path / "no-such-engine"))  # no engine: the base check is skipped, with a warning
    out = tmp_path / "sub"
    argv = ["build", *_adapter_args(kind, probe, tmp_path / "child_env.json"), "--corpus", str(small_corpus),
            "--gold", str(gold), "--out", str(out)]
    with _watch_opens() as opened:
        code = cli.main(argv)
    assert code == 0, f"{kind} build failed with exit {code}"
    assert (out / "theory.json").is_file()
    assert _under(opened, gold) == [] and _under(opened, fixture_gold) == [], "a non-oracle build read gold"


@pytest.mark.parametrize("kind", ["empty", "heuristic"])
def test_build_on_a_mentat_hard_split_never_opens_gold(kind, tmp_path, probe, monkeypatch):
    fixture = FIXTURES / "mentat_hard_dev"
    corpus = tmp_path / "corpus.jsonl"  # the first 300 rows of the split (hash order: outcomes of every league)
    corpus.write_text("\n".join((fixture / "corpus.jsonl").read_text(encoding="utf-8").splitlines()[:300]) + "\n",
                      encoding="utf-8")
    gold = fixture / "gold"
    monkeypatch.setenv("PYTHONPATH", str(PYTHON_DIR) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    monkeypatch.setenv("MB_ENGINE", str(tmp_path / "no-such-engine"))
    out = tmp_path / "sub"
    argv = ["build", *_adapter_args(kind, probe, tmp_path / "child_env.json"), "--corpus", str(corpus),
            "--gold", str(gold), "--out", str(out)]
    with _watch_opens() as opened:
        code = cli.main(argv)
    assert code == 0, f"{kind} build failed with exit {code}"
    assert _under(opened, gold) == [] and _under(opened, gold.resolve()) == [], "a non-oracle build read gold"


def test_oracle_control_reads_gold_under_the_same_hook():
    # Positive control: the audit hook records the oracle's reads of the gold directory.
    gold = TOY10K / "gold"
    with _watch_opens() as opened:
        OracleAdapter(gold)
    assert _under(opened, gold), "the audit hook did not see the oracle's reads"


# ---- (b) corpus fields ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_corpus_rows_carry_only_public_fields(name):
    corpus = FIXTURES / name / "corpus.jsonl"
    for number, line in enumerate(corpus.read_text(encoding="utf-8").splitlines(), 1):
        row = json.loads(line)
        extra = set(row) - CORPUS_FIELDS
        assert not extra, f"{name} corpus line {number} has fields {sorted(extra)}"
        assert {"id", "text"} <= set(row), f"{name} corpus line {number} lacks id or text"


# ---- (c) words and numbering -----------------------------------------------------------------------------------------

def _strings(value):
    """Every key and string value of a JSON value, recursively."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, str):
        yield value


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _queries(name: str) -> list[dict]:
    files = [FIXTURES / name / f for f in ("queries.jsonl", "queries_extra.jsonl") if (FIXTURES / name / f).is_file()]
    return [q for f in files for q in _rows(f)]


def _answers(name: str) -> dict[str, dict]:
    gold = FIXTURES / name / "gold"
    files = [gold / f for f in ("answers.jsonl", "answers_extra.jsonl") if (gold / f).is_file()]
    return {a["qid"]: a for f in files for a in _rows(f)}


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_queries_and_corpus_carry_no_gold_revealing_words(name):
    for record in [*_queries(name), *_rows(FIXTURES / name / "corpus.jsonl")]:
        for text in _strings(record):
            assert not DENY_WORDS.search(text), f"{name}: {text[:80]!r} in {record.get('qid', record.get('id'))}"


def test_the_word_check_reads_the_context_of_a_corpus_row():
    """The rule card is corpus text: the scan above reads every string of a row, its context included."""
    row = {"id": "x", "text": "q", "label": "Yes", "controlled": True, "event_title": None, "context": "The gold reserve rule."}
    assert any(DENY_WORDS.search(text) for text in _strings(row))


SUITE_NAMES = [n for n in FIXTURE_NAMES if (FIXTURES / n / "candidates.jsonl").is_file()]


@pytest.mark.parametrize("name", SUITE_NAMES)
def test_suite_candidates_carry_no_gold_revealing_words(name):
    for record in _rows(FIXTURES / name / "candidates.jsonl"):
        for text in _strings(record):
            assert not DENY_WORDS.search(text), f"{name}: {text[:80]!r} in {record['qid']}"


@pytest.mark.parametrize("name", SUITE_NAMES)
def test_suite_candidate_numbering_does_not_predict_the_label(name):
    """Spearman of the candidate number against the label class per op (unresolved binding, NONE relation, none group),
    and Yes/No outcomes of one market are not neighbours in the binding numbering."""
    cands = _rows(FIXTURES / name / "candidates.jsonl")
    labels = {r["qid"]: r for r in _rows(FIXTURES / name / "gold" / "suite_labels.jsonl")}
    for prefix, flag in (("b", "unresolved"), ("r", "NONE"), ("g", "none")):
        qids = [c["qid"] for c in cands if c["qid"].startswith(prefix)]
        numbers = [int(q[1:]) for q in qids]
        assert abs(_spearman(numbers, [labels[q]["label"] == flag for q in qids])) < 0.3, f"{name} {prefix}"
    markets = [c["id"].split(":")[1] for c in cands if c["op"] == "decide_binding"]
    adjacent = sum(a == b for a, b in zip(markets, markets[1:])) / (len(markets) - 1)
    assert adjacent < 0.05, f"{name}: {adjacent:.0%} of neighbouring binding questions share a market"


def _positive(decision: str | None) -> int:
    return 1 if decision in ("SAT", "true") else 0


def _spearman(xs: list[float], ys: list[float]) -> float:
    def ranks(values):
        order = sorted(range(len(values)), key=lambda i: values[i])
        out = [0.0] * len(values)
        i = 0
        while i < len(values):
            j = i
            while j + 1 < len(values) and values[order[j + 1]] == values[order[i]]:
                j += 1
            for k in range(i, j + 1):
                out[order[k]] = (i + j) / 2 + 1
            i = j + 1
        return out

    rx, ry = ranks(xs), ranks(ys)
    n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx, vy = sum((a - mx) ** 2 for a in rx), sum((b - my) ** 2 for b in ry)
    return cov / (vx * vy) ** 0.5 if vx and vy else 0.0


def _decision_sequences(name: str):
    """(family, qids in numbering order, decisions) for the families with a three-way decision class."""
    queries, answers = _queries(name), _answers(name)
    for family in ("feasibility", "partition"):
        qids = [q["qid"] for q in queries if q["family"] == family]
        yield family, qids, [answers[q].get("decision") for q in qids]


def _index(qid: str) -> int:
    return int(qid.rsplit("-", 1)[1])


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_numbering_does_not_predict_the_decision_class(name):
    # Spearman rank correlation of the query number with "decision is SAT/true" and with "decision is UNRESOLVED".
    # The fixtures' documented mix is fixed (4:4:2 per ten), so only the order can carry information; |rho| < 0.8.
    for family, qids, decisions in _decision_sequences(name):
        numbers = [_index(q) for q in qids]
        positive = [_positive(d) for d in decisions]
        unresolved = [1 if d == "UNRESOLVED" else 0 for d in decisions]
        assert abs(_spearman(numbers, positive)) < 0.8, f"{name} {family}: numbering predicts SAT/true"
        assert abs(_spearman(numbers, unresolved)) < 0.8, f"{name} {family}: numbering predicts UNRESOLVED"


def _has_exact_period(seq: list) -> bool:
    n = len(seq)
    return any(all(seq[i] == seq[i + p] for i in range(n - p)) for p in range(1, n // 2 + 1))


PERIOD_LAGS = range(2, 21)
# Margin above the chance match rate. The chance rate of a lag is sum(f_c^2) over the class frequencies f_c. At a
# lag p with m = n - p pairs the match rate has standard deviation about sqrt(chance * (1 - chance) / m). A lag is used
# only with at least MIN_PAIRS pairs: with 6 pairs (a family of ten, lag 4) the standard deviation is about 0.2, which
# is larger than the margin, so such lags only produce noise (toy10k partition, lag 4, excess 0.31 is one of them).
# With n = 50 or 60 the weakest lag used (20) has 30 or 40 pairs, so the margin 0.25 is about 2.9 to 3.3 standard
# deviations there; below n = 30 the margin is weaker. Families of ten have no usable lag, so for them the check is
# vacuous and the exact-period check and the mix check carry the weight.
PERIOD_MARGIN = 0.25
MIN_PAIRS = 10


def _period_excess(seq: list) -> tuple[float, int]:
    """(largest excess match rate over chance, lag where it occurs) over lags 2..20 with at least MIN_PAIRS pairs.
    Returns (-inf, 0) when no lag qualifies."""
    n = len(seq)
    counts: dict = {}
    for d in seq:
        counts[d] = counts.get(d, 0) + 1
    chance = sum((c / n) ** 2 for c in counts.values())
    best = (float("-inf"), 0)
    for p in PERIOD_LAGS:
        pairs = n - p
        if pairs < MIN_PAIRS:
            break
        rate = sum(1 for i in range(pairs) if seq[i] == seq[i + p]) / pairs
        best = max(best, (rate - chance, p))
    return best


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_decision_order_has_no_exact_period(name):
    for family, _qids, decisions in _decision_sequences(name):
        assert not _has_exact_period(decisions), f"{name} {family}: decisions repeat with a period in numbering order"


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_decision_order_has_no_period_structure(name):
    """Match rate of decisions at lags 2..20 (with at least MIN_PAIRS pairs) in numbering order must stay within
    PERIOD_MARGIN of the chance rate. This also guards against answer patterns that repeat in numbering order."""
    for family, _qids, decisions in _decision_sequences(name):
        excess, lag = _period_excess(decisions)
        assert excess <= PERIOD_MARGIN, f"{name} {family}: decisions match at lag {lag} with excess {excess:.3f}"
        assert excess > float("-inf") or len(decisions) < 2 * MIN_PAIRS, f"{name} {family}: no lag has {MIN_PAIRS} pairs"


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_decision_mix_follows_the_documented_design(name):
    # Spec 2:2:1 per ten: 4 SAT/true, 4 UNSAT/false, 2 UNRESOLVED in each family of ten (fixture notes).
    for family, _qids, decisions in _decision_sequences(name):
        blocks = len(decisions) // 10
        assert blocks >= 1 and len(decisions) % 10 == 0, f"{name} {family}: {len(decisions)} queries"
        counts = {k: decisions.count(k) for k in ("SAT", "true", "UNSAT", "false", "UNRESOLVED")}
        assert counts["SAT"] + counts["true"] == 4 * blocks, f"{name} {family}: {counts}"
        assert counts["UNSAT"] + counts["false"] == 4 * blocks, f"{name} {family}: {counts}"
        assert counts["UNRESOLVED"] == 2 * blocks, f"{name} {family}: {counts}"


# ---- (d) fixture directories -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_fixture_directories_hold_only_public_files(name):
    entries = {p.name for p in (FIXTURES / name).iterdir()}
    assert entries <= PUBLIC_TOP_LEVEL, f"{name} has unexpected entries {sorted(entries - PUBLIC_TOP_LEVEL)}"
    assert {"corpus.jsonl", "queries.jsonl", "fixture.json", "gold"} <= entries, f"{name} lacks a required file"
    assert (FIXTURES / name / "gold").is_dir()


# ---- (e) sealed submissions -----------------------------------------------------------------------------------------------

def test_sealed_submission_contains_no_gold_paths(tmp_path, small_corpus, probe, monkeypatch):
    from mentat_bench.seal import SEAL_FILE

    gold = TOY10K / "gold"
    monkeypatch.setenv("PYTHONPATH", str(PYTHON_DIR) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    monkeypatch.setenv("MB_ENGINE", str(tmp_path / "no-such-engine"))
    out = tmp_path / "sub"
    assert cli.main(["build", "--adapter", "empty", "--corpus", str(small_corpus), "--gold", str(gold), "--out", str(out)]) == 0
    assert cli.main(["seal", "--submission", str(out)]) == 0
    sealed = json.loads((out / SEAL_FILE).read_text(encoding="utf-8"))["files"]
    assert not [name for name in sealed if "gold" in name.lower()], "a sealed file is named after gold"
    markers = {str(gold.resolve()), gold.resolve().as_posix(), "/gold/"}
    for name in sealed:
        text = (out / name).read_text(encoding="utf-8", errors="replace")
        for marker in markers:
            assert marker not in text, f"{name} contains the gold path {marker!r}"


# ---- (f) network ------------------------------------------------------------------------------------------------------------

PROXY_KEYS = ("http_proxy", "https_proxy", "all_proxy", "no_proxy")
HTTP_IMPORT = re.compile(r"^\s*(?:import|from)\s+(requests|urllib|httpx|aiohttp|socket|http|ftplib|huggingface_hub)\b", re.M)
HTTP_CALL = re.compile(r"\bsnapshot_download\b|\burlopen\b")
# Children are separate, metered model processes that may load pinned weights (declared in models.json); nothing else may.
# systemone_child talks only to a loopback model server (MB_S1_URL), like the other model children.
NETWORK_ALLOWLIST = {"children/hf_child.py", "children/embed_child.py", "children/systemone_child.py"}


# A child that records its own argv and environment (keys and values) for the child-side checks below.
ARGV_ENV_CHILD = r'''
import json, os, sys
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    json.dump({"argv": sys.argv, "env": dict(os.environ)}, fh)
for line in sys.stdin:
    req = json.loads(line)
    out = {"relation": "NONE"} if req["op"] == "decide_relation" else {
        "status": "unresolved", "class": None, "negated": False, "missing": ["no_source"]}
    out["id"] = req["id"]
    print(json.dumps(out), flush=True)
'''

PROXY_VARIABLES = {
    "HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9", "ALL_PROXY": "http://127.0.0.1:9",
    "NO_PROXY": "", "http_proxy": "http://127.0.0.1:9", "https_proxy": "http://127.0.0.1:9",
}


@pytest.mark.slow
def test_local_child_gets_no_proxy_variables_even_when_the_parent_has_them(tmp_path, small_corpus, probe):
    env = {**os.environ, **PROXY_VARIABLES, "PYTHONPATH": str(PYTHON_DIR), "CUDA_VISIBLE_DEVICES": "",
           "MB_ENGINE": str(tmp_path / "no-such-engine")}
    child_env = tmp_path / "child_env.json"
    proc = subprocess.run(
        [sys.executable, "-m", "mentat_bench", "build", *_adapter_args("local", probe, child_env),
         "--corpus", str(small_corpus), "--out", str(tmp_path / "sub")],
        env=env, capture_output=True, text=True, timeout=600,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    seen = json.loads(child_env.read_text(encoding="utf-8"))
    assert not [k for k in seen if any(p in k.lower() for p in PROXY_KEYS)], "the child received a proxy variable"


@pytest.mark.slow
def test_local_child_is_not_handed_the_gold_directory(tmp_path, small_corpus, monkeypatch):
    """The build's child gets neither the gold path nor the gold directory in its argv or environment. LIMIT: the
    audit hook sees only the parent's opens, and a local child is an arbitrary process that can find the fixture on
    disk; the harness does not sandbox the children (they are the models under test)."""
    gold = (TOY10K / "gold").resolve()
    child = tmp_path / "argv_env_child.py"
    child.write_text(ARGV_ENV_CHILD, encoding="utf-8")
    seen_path = tmp_path / "child_seen.json"
    monkeypatch.setenv("PYTHONPATH", str(PYTHON_DIR) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    monkeypatch.setenv("MB_ENGINE", str(tmp_path / "no-such-engine"))
    argv = ["build", "--adapter", "local", "--local-arg", sys.executable, "--local-arg", str(child),
            "--local-arg", str(seen_path), "--corpus", str(small_corpus), "--gold", str(gold), "--out", str(tmp_path / "sub")]
    assert cli.main(argv) == 0
    seen = json.loads(seen_path.read_text(encoding="utf-8"))
    text = json.dumps(seen)
    needle = os.path.normcase(str(gold))
    assert needle not in os.path.normcase(text), "the child saw the gold directory in its argv or environment"


def test_harness_code_imports_no_http_client_outside_the_allowlist():
    offenders = []
    for path in sorted((PYTHON_DIR / "mentat_bench").rglob("*.py")):
        rel = path.relative_to(PYTHON_DIR / "mentat_bench").as_posix()
        if rel in NETWORK_ALLOWLIST:
            continue
        text = path.read_text(encoding="utf-8")
        if HTTP_IMPORT.search(text) or HTTP_CALL.search(text):
            offenders.append(rel)
    assert offenders == [], f"HTTP client code outside the child allowlist: {offenders}"


# The HF child loads pinned weights, so its run needs torch and the model cache. This static check runs everywhere: its
# source never names gold or a fixture directory, so it cannot read either (the child's runs are covered by test_hf_child).
GOLD_OR_FIXTURE_NAME = re.compile(r"gold|fixtures?[\\/]", re.I)


def test_hf_child_source_never_names_gold_or_a_fixture_directory():
    text = (PYTHON_DIR / "mentat_bench" / "children" / "hf_child.py").read_text(encoding="utf-8")
    assert not GOLD_OR_FIXTURE_NAME.search(text), "the HF child names gold or a fixture directory"


# ---- (g) gold-only REFs ---------------------------------------------------------------------------------------------------

def _public_records(n: int) -> list[dict]:
    rows = sorted(ir.read_jsonl(TOY10K / "corpus.jsonl"), key=lambda r: r["id"])[:n]
    return [{"id": r["id"], "question": r["text"], "label": r["label"], "event_title": r.get("event_title")} for r in rows]


class _GuessingAdapter:
    """A non-gold adapter that writes the gold-only REFs it cannot know, next to a question REF it can."""

    name = "guessing"

    def __init__(self):
        self.contexts: list[set[str]] = []
        self.guessed = 0

    def decide_binding(self, outcome_context):
        self.contexts.append(set(outcome_context))
        mid, idx = outcome_context["id"].split(":")[1], outcome_context["id"].rsplit(":", 1)[1]
        if idx == "1":
            guesses = ["ax:binary_complement", "src:event:212039:event_title"]
            self.guessed += len(guesses)
            return {"status": "bound", "class": f"C{mid}", "negated": False, "missing": [],
                    "evidence": [f"src:{mid}:question", *guesses]}, usage(self.name)
        self.guessed += 1
        return {"status": "unresolved", "class": None, "negated": False, "missing": ["no_date"],
                "evidence": ["ax:made_up"]}, usage(self.name)

    def decide_relation(self, a_context, b_context):
        return "NONE", usage(self.name)


def test_gold_only_refs_are_stripped_and_counted_for_a_non_gold_adapter(tmp_path):
    adapter = _GuessingAdapter()
    summary = fixed.build(_public_records(60), adapter, tmp_path / "sub", DEFAULT_PROFILE)
    assert summary["gold_refs_rejected"] == adapter.guessed > 0
    rows = ir.read_jsonl(tmp_path / "sub" / "bindings.jsonl")
    assert any(row["status"] == "bound" for row in rows)
    for row in rows:
        assert not [r for r in row["evidence"] if GOLD_ONLY_REF.fullmatch(r)], row["id"]
        assert row["evidence"], "a binding lost all its REFs"  # the derived question and label REFs remain
    for prov in ir.read_jsonl(tmp_path / "sub" / "provenance.jsonl"):
        assert not GOLD_ONLY_REF.fullmatch(prov["ref"]), prov["ref"]


def test_strip_keeps_public_refs_and_drops_an_emptied_evidence_list():
    row = {"evidence": ["src:m1:question", "ax:a", "src:event:7:event_title", "src:m1:event_title"]}
    assert _strip_gold_refs(row) == 2 and row["evidence"] == ["src:m1:question", "src:m1:event_title"]
    only_gold = {"evidence": ["ax:a"]}
    assert _strip_gold_refs(only_gold) == 1 and "evidence" not in only_gold  # the derived REFs apply again
    assert _strip_gold_refs({"status": "unresolved"}) == 0


def test_a_non_gold_adapter_receives_only_the_public_outcome_context(tmp_path):
    records = [dict(r, gold_evidence=["ax:leaked"], event_id=212039, context="Rule card: four teams.") for r in _public_records(20)]
    adapter = _GuessingAdapter()
    fixed.build(records, adapter, tmp_path / "sub", DEFAULT_PROFILE)
    assert adapter.contexts and all(keys == {"id", "question", "label", "event_title", "context"} for keys in adapter.contexts)


def test_gold_backed_adapters_keep_their_gold_refs(tmp_path):
    gold = tmp_path / "gold"
    gold.mkdir()
    ids = ("toy:m1:1", "toy:m1:2")
    evidence = ["src:m1:question", "ax:binary_complement"]
    rows = [
        {"id": ids[0], "status": "bound", "class": "A", "negated": False, "missing": [], "evidence": evidence},
        {"id": ids[1], "status": "bound", "class": "A", "negated": True, "missing": [], "evidence": evidence},
    ]
    constraint = {"id": "c1", "expr": {"op": "iff", "a": ir.var(ids[0]),
                                           "b": {"op": "not", "a": ir.var(ids[1])}},
                  "interps": None, "provenance": ["ax:binary_complement"]}
    ir.write_submission(gold, rows, [constraint], ["base"],
                        {"calls": [], "wall_seconds": 0.0, "peak_rss_mb": 0.0})
    assert OracleAdapter.supplies_gold_refs and GoldViewAdapter.supplies_gold_refs
    records = [{"id": oid, "question": "Will Ember win the cup?", "label": label, "event_title": "Cup"}
               for oid, label in zip(ids, ("Yes", "No"))]
    gold_rows = {r["id"]: r for r in ir.read_jsonl(gold / "bindings.jsonl")}
    summary = fixed.build(records, GoldViewAdapter(gold), tmp_path / "sub", DEFAULT_PROFILE)
    assert summary["gold_refs_rejected"] == 0
    written = {r["id"]: r["evidence"] for r in ir.read_jsonl(tmp_path / "sub" / "bindings.jsonl")}
    gold_only = [oid for oid in written if gold_rows[oid].get("evidence") and any(
        GOLD_ONLY_REF.fullmatch(ref) for ref in gold_rows[oid]["evidence"])]
    assert gold_only, "the window holds no gold binding with an axiom or event REF"
    for oid in gold_only:
        assert set(written[oid]) == set(gold_rows[oid]["evidence"]), oid


class _RelationSpy(_GuessingAdapter):
    """Binds like _GuessingAdapter and records the context of every relation question."""

    def __init__(self):
        super().__init__()
        self.relation_contexts: list[set[str]] = []

    def decide_relation(self, a_context, b_context):
        self.relation_contexts += [set(a_context), set(b_context)]
        return "NONE", usage(self.name)


def test_relation_decisions_receive_only_the_public_outcome_context(tmp_path):
    records = [dict(r, gold_evidence=["ax:leaked"]) for r in _public_records(60)]
    adapter = _RelationSpy()
    fixed.build(records, adapter, tmp_path / "sub", DEFAULT_PROFILE)
    assert adapter.relation_contexts, "the window asked no relation"
    assert all(keys == {"id", "question", "label", "event_title"} for keys in adapter.relation_contexts)


def test_a_market_id_named_event_keeps_its_derived_refs(tmp_path):
    """src:event:event_title is the derived title REF of a market named 'event'; only src:event:<id>:event_title is gold's."""
    rows = [{"id": "polymarket:event:1", "question": "Will A win?", "label": "Yes", "event_title": "E"}]

    class Evidence:
        name = "evidence"

        def decide_binding(self, ctx):
            return {"status": "bound", "class": "C1", "negated": False, "missing": [],
                    "evidence": ["src:event:event_title", "ax:made_up"]}, usage(self.name)

        def decide_relation(self, a, b):
            return "NONE", usage(self.name)

    summary = fixed.build(rows, Evidence(), tmp_path / "sub", DEFAULT_PROFILE)
    assert summary["gold_refs_rejected"] == 1  # the ax: REF only
    written = next(iter(ir.read_jsonl(tmp_path / "sub" / "bindings.jsonl")))["evidence"]
    assert "src:event:event_title" in written and "ax:made_up" not in written


def test_a_non_gold_adapter_may_not_replay_a_theory(tmp_path):
    class Replayer(_GuessingAdapter):
        name = "replayer"

        def extra_theory(self):
            return {"interpretations": ["base"], "constraints": []}

    with pytest.raises(ValueError, match="not gold-backed"):
        fixed.build(_public_records(5), Replayer(), tmp_path / "sub", DEFAULT_PROFILE)
    assert not (tmp_path / "sub").exists()  # refused before anything is written


def test_a_non_gold_adapter_named_oracle_is_not_gold_backed(tmp_path):
    """The gold status is the adapter's flag, recorded in the policy; a name does not grant it. The impostor's gold-only
    REFs are stripped and counted, and its policy says it is not gold-backed (so a resume strips them as well)."""
    impostor = type("Impostor", (_GuessingAdapter,), {"name": "oracle"})()
    summary = fixed.build(_public_records(20), impostor, tmp_path / "sub", DEFAULT_PROFILE)
    assert summary["gold_refs_rejected"] == impostor.guessed > 0
    policy = ir.read_json(tmp_path / "sub" / "build_policy.json")
    assert policy["adapter"] == "oracle" and policy["gold_backed"] is False


def test_the_policy_boolean_decides_gold_status_and_legacy_policies_fall_back_to_the_name():
    assert fixed._gold_backed({"gold_backed": True, "adapter": "local"}) is True  # the recorded flag wins
    assert fixed._gold_backed({"gold_backed": False, "adapter": "oracle"}) is False  # a divergent name does not grant it
    assert fixed._gold_backed({"adapter": "oracle"}) is True  # a build before gold_backed was recorded
    assert fixed._gold_backed({"adapter": "local"}) is False
    assert fixed._gold_backed({}) is False and fixed._gold_backed(None) is False  # no policy: not gold


def test_a_record_without_an_id_is_refused_and_leaves_nothing_behind(tmp_path):
    from mentat_bench.adapters.mock import EmptyAdapter

    for bad in ([{"question": "Will it rain?"}], [{"id": None, "question": "q"}], [{"id": 7, "question": "q"}], ["not an object"]):
        out = tmp_path / f"sub{len(list(tmp_path.iterdir()))}"
        with pytest.raises(ValueError):
            fixed.build(bad, EmptyAdapter(), out, DEFAULT_PROFILE)
        assert not out.exists() and not out.with_name(out.name + ".build").exists()


def test_the_gold_backed_flags_are_explicit_per_class():
    from mentat_bench.adapters.mock import EmptyAdapter, _GoldAdapter

    assert OracleAdapter.supplies_gold_refs is True and GoldViewAdapter.supplies_gold_refs is True
    assert EmptyAdapter.supplies_gold_refs is False and _GoldAdapter.supplies_gold_refs is False


def test_a_build_refuses_a_record_without_question_text(tmp_path):
    from mentat_bench.adapters.mock import EmptyAdapter

    with pytest.raises(ValueError, match="no question text"):
        fixed.build([{"id": "polymarket:m1:1", "label": "Yes"}], EmptyAdapter(), tmp_path / "sub", DEFAULT_PROFILE)
    assert not (tmp_path / "sub").exists() and not (tmp_path / "sub.build").exists()  # abandoned: nothing is left behind


def test_a_resumed_build_from_before_the_guard_loses_its_gold_refs(tmp_path):
    """A checkpoint written before the guard may hold gold-only REFs in its decided log. Replaying it for a non-gold build
    strips them (and counts them), so the export is clean too."""
    work = tmp_path / "work"
    work.mkdir()
    row = {"id": "polymarket:m1:1", "status": "bound", "class": "C1", "negated": False, "missing": [], "event_title": "E",
           "evidence": ["src:m1:question", "ax:binary_complement", "src:event:212039:event_title"]}
    (work / "decided.jsonl").write_text(json.dumps(row, sort_keys=True) + "\n", encoding="utf-8")
    (work / "relations.jsonl").write_text("", encoding="utf-8")
    ckpt = {"decided_bytes": (work / "decided.jsonl").stat().st_size, "relations_bytes": 0}
    replay = fixed._Replay(work, ckpt, strip_gold=True)
    (exported,) = list(replay.rows())
    assert exported["evidence"] == ["src:m1:question"]
    assert replay.gold_refs_stripped == 2
    kept = fixed._Replay(work, ckpt, strip_gold=False)  # a gold-backed build keeps them
    assert list(kept.rows())[0]["evidence"] == row["evidence"]


def test_finalize_refuses_a_theory_replay_unless_the_checkpoint_is_gold_backed(tmp_path):
    """Direct call: finalize_checkpoint(extra_theory=...) on a non-gold checkpoint is refused before anything is written."""
    from mentat_bench.adapters.mock import EmptyAdapter

    fixed.build(_public_records(10), EmptyAdapter(), tmp_path / "sub", DEFAULT_PROFILE, keep_work=True)
    work = tmp_path / "sub.build"
    theory = {"interpretations": ["base"], "constraints": []}
    with pytest.raises(ValueError, match="gold-backed"):
        fixed.finalize_checkpoint(work, tmp_path / "replayed", DEFAULT_PROFILE, extra_theory=theory)
    assert not (tmp_path / "replayed").exists()

