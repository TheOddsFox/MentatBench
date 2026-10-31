"""Baseline table: python -m mentat_bench.baselines run --fixture DIR --out DIR [--cells LIST] [--top-k K] [--include-hf]

Cells run through runcell, one run directory each under --out (a new or empty directory):
  empty       the empty/unresolved theory (adapter empty)
  lexical     lexical retrieval (children.lexical_child, no groups)
  embedding   embeddings without semantic reasoning (children.embed_child on CPU, no groups; slow, batched)
  heuristic   deterministic parsing plus rules (children.heuristic_child)
  oracle      oracle meanings through the common engine (adapter oracle; reads gold, gold-informed)
  gold_view   oracle-retrieval DIAGNOSTIC (adapter gold_view; reads gold, gold-informed). It shows what the pipeline can
              express when every decision is right. It is listed apart from the table and is not a corpus score.
  hf          fixed model-to-theory pipeline (children.hf_child). Runs only with --include-hf, and needs MB_HF_MODEL and
              MB_HF_DEVICE in the environment (no defaults are assumed).

Every cell uses one --top-k (default 24, the plan's K for model cells). Each row records the K its build used, and the
run warns when the rows disagree with the request.

The synthetic baselines (score --baseline all-contingent, all-unresolved, constant-answer) are scored directly, without a
build. constant-answer and all-unresolved are gold-informed (they read gold answers); they are marked as such.

A failing cell or synthetic score is recorded in its row (status failed, with the error); the other rows are still
written. baselines.json and baselines.md hold the engine's path and sha256, the K of every row, the warnings and the rows.
Exit 0 when every row finished, 1 when a row failed, 2 for a usage error.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterator

from . import cli, engine, report, runcell, runcard, scoring
from .cli import BUILD_TOP_K

PKG_ROOT = Path(__file__).resolve().parents[1]  # python/: the import root child processes need for `-m mentat_bench...`
EXIT_OK, EXIT_FAIL, EXIT_USAGE = 0, 1, 2
DEFAULT_TOP_K = 24
CHEAP = ("empty", "lexical", "embedding", "heuristic", "gold_view", "oracle")
CELLS = (*CHEAP, "hf")
DIAGNOSTIC = ("gold_view",)
GOLD_READING = ("oracle", "gold_view")
ADAPTER = {"empty": "empty", "oracle": "oracle", "gold_view": "gold_view"}
CHILD = {
    "lexical": "mentat_bench.children.lexical_child",
    "embedding": "mentat_bench.children.embed_child",
    "heuristic": "mentat_bench.children.heuristic_child",
    "hf": "mentat_bench.children.hf_child",
}
LABEL = {
    "empty": "empty / unresolved theory",
    "lexical": "lexical retrieval",
    "embedding": "embeddings without semantic reasoning",
    "heuristic": "deterministic parsing + rules",
    "hf": "fixed model-to-theory pipeline (hf child)",
    "oracle": "oracle meanings through the common engine",
    "gold_view": "oracle-retrieval DIAGNOSTIC (not a corpus score)",
}
COLUMNS = (
    ("baseline", "label"), ("status", "status"), ("top-k", "top_k"), ("headline MCC", "decision_headline"),
    ("headline metric", "decision_headline_metric"), ("rel MCC (headline v1-v2, part v3)", "decision_rel_mcc"),
    ("bind MCC (secondary)", "decision_bind_mcc"), ("grp MCC (diagnostic v1-v2, part v3)", "decision_grp_mcc"),
    ("Q_N (repaired, diagnostic)", "Q_N"), ("strict Q_N (diagnostic)", "strict_Q_N"),
    ("weighted Q_N (diagnostic)", "weighted_Q_N"), ("anchor Q_N (diagnostic)", "anchor_Q_N"),
    ("active Q_N (diagnostic)", "active_Q_N"), ("exact", "exact_query_success"), ("false_ent", "false_entailment"),
    ("false_ref", "false_refutation"), ("base repaired", "base_repaired"), ("relations asked", "relations_asked"),
    ("calls", "calls"), ("items", "items"), ("wall s", "wall_seconds"), ("cost USD", "cost_usd"),
    ("gold-informed", "gold_informed"),
)


@contextlib.contextmanager
def _child_path() -> Iterator[None]:
    """Child processes import mentat_bench from python/ even when the package is not installed."""
    old = os.environ.get("PYTHONPATH")
    os.environ["PYTHONPATH"] = os.pathsep.join(p for p in (str(PKG_ROOT), old) if p)
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("PYTHONPATH", None)
        else:
            os.environ["PYTHONPATH"] = old


def cell_argv(cell: str, fixture: Path, out: Path, top_k: int, candidates: Path | None = None,
              profile: Path | None = None) -> list[str]:
    """runcell arguments of one cell. Child cells use this interpreter; lexical and embedding make no group decisions.
    With candidates (the curated candidates.jsonl), the cell asks its questions and scores its decision log. The oracle
    replays a full theory and asks no question (runcell refuses --candidates for it), and the empty theory makes no
    decision: neither gets candidates, so neither has a decision MCC."""
    common = ["--fixture", str(fixture), "--out", str(out), "--top-k", str(top_k)]
    if candidates is not None and cell not in ("empty", "oracle"):
        common += ["--candidates", str(candidates)]
    if profile is not None:  # runcell's own default is the toy profile: a Mentat-Hard run names its profile
        common += ["--profile", str(profile)]
    if cell in ADAPTER:
        return ["--adapter", ADAPTER[cell], *common]
    argv = ["--adapter", "local", f"--local-arg={sys.executable}", "--local-arg=-m", f"--local-arg={CHILD[cell]}"]
    if cell == "embedding":
        argv += ["--local-arg=--corpus", f"--local-arg={fixture / 'corpus.jsonl'}"]
    if cell in ("embedding", "hf"):  # model children load weights and calibrate before the hello; batches are slow on CPU
        argv += ["--local-timeout", "1800", "--hello-timeout", "3600"]
    if cell in ("lexical", "embedding"):
        argv.append("--no-groups")
    return [*argv, *common]


def engine_record() -> dict[str, Any]:
    """The engine that scored the cells: its path and the sha256 of the file the cells ran (MB_ENGINE or the default)."""
    path = engine.engine_path()
    name = runcard.rel(path)  # repository-relative, or <external>/NAME: a table holds no absolute path
    try:
        return {"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    except OSError as exc:
        return {"path": name, "sha256": None, "error": _scrub(str(exc), path)}


def _read(path: Path) -> dict[str, Any]:
    """A cell's JSON object. ValueError when the file is missing, unreadable or not an object."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"{path}: {exc}") from exc
    if not isinstance(doc, dict):
        raise ValueError(f"{path} is not a JSON object")
    return doc


HEADLINE_KEYS = ("decision_headline", "decision_headline_metric")  # the version 3 headline of a row (report.decision_metrics)


def _json_cells(cells: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The rows as the table's JSON writes them: the headline keys only for a version 3 row (headline metric relation_group_mcc).
    A version 1 or 2 row keeps the bytes it had before the version 3 headline. The markdown is written from the full rows."""
    return {name: row if row.get("decision_headline_metric") == "relation_group_mcc"
            else {k: v for k, v in row.items() if k not in HEADLINE_KEYS} for name, row in cells.items()}


def _failed_row(label: str, gold_informed: bool, error: str) -> dict[str, Any]:
    return {"label": label, "status": "failed", "error": error, "gold_informed": gold_informed, "top_k": None}


def _scrub(text: str, directory: Path) -> str:
    """An error text with the absolute cell directory replaced by its repository-relative (or <external>) name: a table
    holds no absolute path."""
    return text.replace(str(directory), runcard.rel(directory))


def _run_row(cell: str, run: Path) -> dict[str, Any]:
    gold_informed = cell in GOLD_READING
    try:
        row = report.run_row(run)
    except ValueError as exc:  # no score.json: the cell stopped before scoring
        return _failed_row(LABEL.get(cell, cell), gold_informed, _scrub(str(exc), run))
    return {**row, "label": LABEL[cell], "gold_informed": gold_informed}


def synthetic_row(name: str, fixture: Path) -> dict[str, Any]:
    gold = fixture / "gold"
    argv = ["score", "--queries", str(fixture / "queries.jsonl"), "--gold-answers", str(gold / "answers.jsonl"),
            "--baseline", name]
    if (gold / "bindings.jsonl").is_file():
        argv += ["--gold-bindings", str(gold / "bindings.jsonl")]
    if (gold / "query_design.jsonl").is_file():
        argv += ["--design", str(gold / "query_design.jsonl")]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli.main(argv)
    if code != 0:
        raise RuntimeError(f"score --baseline {name} exited {code}")
    score = json.loads(buf.getvalue())
    anchor, active = score.get("anchor") or {}, score.get("active") or {}
    return {
        "label": f"synthetic: {name}", "status": "synthetic", "top_k": None, "Q_N": score.get("Q_N"), "strict_Q_N": None,
        "weighted_Q_N": (score.get("weighted") or {}).get("Q_N"),
        "anchor_Q_N": anchor.get("Q_N") if anchor.get("Q_N") is not None else anchor.get("status"),
        "active_Q_N": active.get("Q_N") if active.get("Q_N") is not None else active.get("status"),
        "exact_query_success": score.get("exact_query_success"), "false_entailment": score.get("false_entailment"),
        "false_refutation": score.get("false_refutation"), "base_repaired": None, "relations_asked": None,
        "calls": 0, "items": 0, "wall_seconds": None, "cost_usd": 0.0,
        "gold_informed": name in scoring.GOLD_INFORMED_BASELINES,
    }


CHILD_MODULE_NAMES = {module: name for name, module in CHILD.items()}  # runcell's local_arg names the child module


def baseline_name(run: Path) -> str:
    """The baseline name of a cell directory: its adapter (empty, oracle, gold_view), else the name of its child module."""
    cell = _read(run / "cell.json")
    if cell.get("adapter") in ADAPTER:
        return cell["adapter"]
    for arg in cell.get("local_arg") or []:
        if arg in CHILD_MODULE_NAMES:
            return CHILD_MODULE_NAMES[arg]
    raise ValueError(f"{runcard.rel(run)} is not a baseline cell (adapter {cell.get('adapter')!r})")


def collect(runs: list[Path], top_k: int) -> dict[str, Any]:
    """The baseline table of existing cell directories in `runs` (nothing runs). Each cell is named by baseline_name, so the
    rules cell is "heuristic" in the table, whatever its directory is called. A name in two directories is refused; so is
    a table over several fixtures. Rows keep the K they were built with: topk_notes warns when they differ. Paths are
    repository-relative or <external>/NAME, as in run cards."""
    rows: dict[str, dict[str, Any]] = {}
    fixtures: set[str] = set()
    engines: set[tuple[Any, Any]] = set()
    for root in runs:
        for run in sorted(p for p in root.iterdir() if (p / "cell.json").is_file()):
            name = baseline_name(run)
            if name in rows:
                raise ValueError(f"{name} is in two directories: {rows[name]['run']} and {runcard.rel(run)}")
            cell = _read(run / "cell.json")
            fixtures.add(cell.get("fixture"))
            engine = (cell.get("runtime") or {}).get("engine") or {}
            engines.add((engine.get("path"), engine.get("sha256")))
            rows[name] = {**_run_row(name, run), "run": runcard.cell_ref(run)}
    if len(fixtures) != 1:
        raise ValueError(f"the cells are on {sorted(map(str, fixtures))}: a baseline table is over one fixture")
    notes = topk_notes(list(rows.values()), top_k)
    engine_info = {"path": None, "sha256": None}
    if len(engines) == 1:
        (engine_info["path"], engine_info["sha256"]), = engines
    else:
        notes.append(f"warning: the cells ran on {len(engines)} engines; the table has one engine record")
    return {"fixture": fixtures.pop(), "top_k": top_k, "engine": engine_info, "notes": notes,
            "cells": rows, "diagnostic": "gold_view is an oracle-retrieval diagnostic, not a corpus score"}


def _collect_cli(runs: list[Path], top_k: int, out: Path) -> int:
    if top_k < 1:
        print("--top-k must be at least 1", file=sys.stderr)
        return EXIT_USAGE
    try:
        doc = collect(runs, top_k)
    except (OSError, ValueError) as exc:
        print(f"baselines collect: {exc}", file=sys.stderr)
        return EXIT_USAGE
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({**doc, "cells": _json_cells(doc["cells"])}, indent=2, sort_keys=True, allow_nan=False) + "\n",
                   encoding="utf-8", newline="\n")
    main_rows = [row for name, row in doc["cells"].items() if name not in DIAGNOSTIC]
    diagnostic = [row for name, row in doc["cells"].items() if name in DIAGNOSTIC]
    print(markdown(main_rows, [], diagnostic, top_k, doc["fixture"], doc["engine"], doc["notes"]), end="")
    return EXIT_OK


def topk_notes(cells: list[dict[str, Any]], requested: int) -> list[str]:
    """Warnings when the rows' builds used another K than requested, and a note when the request differs from the build's
    own default (the plan's model cells use 24; the build's default is cli.BUILD_TOP_K)."""
    used = sorted({row["top_k"] for row in cells if row.get("top_k") is not None})
    notes = []
    if any(k != requested for k in used):
        notes.append(f"warning: rows used top-k {used}, this run asked for {requested}; the rows are not comparable")
    if requested != BUILD_TOP_K:
        notes.append(f"note: the build's own default is --top-k {BUILD_TOP_K}; every row here uses {requested}")
    return notes


def _cell_text(value: Any) -> str:
    if value is None or value == []:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.2f}" if abs(value) >= 0.01 or value == 0 else f"{value:.4g}"
    return str(value)


def markdown(cells: list[dict[str, Any]], synthetic: list[dict[str, Any]], diagnostic: list[dict[str, Any]],
             top_k: int, fixture: str, engine_info: dict[str, Any], notes: list[str]) -> str:
    head = "| " + " | ".join(name for name, _ in COLUMNS) + " |"
    rule = "|" + "|".join("---" for _ in COLUMNS) + "|"

    def block(rows: list[dict[str, Any]]) -> list[str]:
        return ["| " + " | ".join(_cell_text(row.get(key)) for _, key in COLUMNS) + " |" for row in rows]

    lines = [
        f"# Baselines on `{fixture}` (top-k {top_k})", "",
        f"Engine: `{engine_info.get('path')}` sha256 {engine_info.get('sha256') or 'unavailable'}.", "",
        *(f"- {note}" for note in notes), *([""] if notes else []),
        "Headline: the relation MCC for fixture versions 1 and 2 (owner decision 2026-10-29), and the mean of the relation and "
        "group MCCs for version 3 and later (owner decision 2026-10-30); headline metric says which. Binding MCC is secondary; "
        "reading MCC is a diagnostic; group MCC is a part of the version 3 headline and a diagnostic in versions 1 and 2. Every Q_N "
        "column is a diagnostic, never a ranking or a headline. Q_N (repaired) is the score after the base repair; strict "
        "Q_N is 0.0 for a repaired base. weighted Q_N uses global "
        "class weights. Synthetic rows have no build and no calls. gold-informed yes marks the rows that read gold answers "
        "(oracle, gold_view and the constant synthetic baselines): upper bounds, not gold-blind baselines. A dash means "
        "unknown or not applicable.", "",
        head, rule, *block(cells), *block(synthetic), "",
    ]
    if diagnostic:
        lines += [
            "## Oracle-retrieval diagnostic (not a corpus score)", "",
            "The gold_view row supplies gold bindings, relations and groups without a theory replay. It separates candidate "
            "loss from semantic mistakes. It does not replace the corpus score.", "",
            head, rule, *block(diagnostic), "",
        ]
    return "\n".join(lines)


def build(fixture: Path, out: Path, cells: list[str], top_k: int, candidates: Path | None = None,
          profile: Path | None = None) -> int:
    out.mkdir(parents=True, exist_ok=True)
    engine_info = engine_record()
    codes: dict[str, int] = {}
    rows: dict[str, dict[str, Any]] = {}
    for cell in cells:
        try:
            with _child_path():
                codes[cell] = runcell.main(cell_argv(cell, fixture, out / cell, top_k, candidates, profile))
        except Exception as exc:  # noqa: BLE001 - recorded as this cell's failure; the other rows are still written
            codes[cell] = EXIT_FAIL
            rows[cell] = _failed_row(LABEL[cell], cell in GOLD_READING, _scrub(f"{type(exc).__name__}: {exc}", out / cell))
            continue
        rows[cell] = _run_row(cell, out / cell)
        if codes[cell] != 0:
            rows[cell]["status"] = "failed"
            rows[cell]["error"] = f"runcell exit {codes[cell]}"
    synthetic: dict[str, dict[str, Any]] = {}
    for name in scoring.BASELINES:
        try:
            synthetic[name] = synthetic_row(name, fixture)
        except Exception as exc:  # noqa: BLE001 - recorded as this row's failure; the table is still written
            synthetic[name] = _failed_row(f"synthetic: {name}", name in scoring.GOLD_INFORMED_BASELINES,
                                          f"{type(exc).__name__}: {exc}")
    notes = topk_notes(list(rows.values()), top_k)
    if engine_info.get("sha256") is None:
        notes.append(f"warning: the engine could not be hashed ({engine_info.get('error')})")
    failed = [name for name, code in codes.items() if code != 0] + \
             [f"synthetic {name}" for name, row in synthetic.items() if row["status"] == "failed"]
    if failed:
        notes.append(f"failed rows: {', '.join(failed)}")
    main_rows = [rows[c] for c in cells if c not in DIAGNOSTIC]
    diagnostic = [rows[c] for c in cells if c in DIAGNOSTIC]
    synthetic_rows = list(synthetic.values())
    doc = {"fixture": fixture.name, "top_k": top_k, "engine": engine_info, "notes": notes, "exit_codes": codes,
           "cells": _json_cells({c: rows[c] for c in cells}), "synthetic": synthetic,
           "diagnostic": "gold_view is an oracle-retrieval diagnostic, not a corpus score"}
    (out / "baselines.json").write_text(json.dumps(doc, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8",
                                        newline="\n")
    text = markdown(main_rows, synthetic_rows, diagnostic, top_k, fixture.name, engine_info, notes)
    (out / "baselines.md").write_text(text, encoding="utf-8", newline="\n")
    for note in notes:
        if note.startswith("warning"):
            print(note, file=sys.stderr)
    print(text, end="")
    return EXIT_OK if not failed else EXIT_FAIL


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m mentat_bench.baselines", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="build, seal, evaluate and score the baseline cells of one fixture")
    run.add_argument("--fixture", required=True, type=Path, help="fixture directory (corpus.jsonl, queries.jsonl, gold/)")
    run.add_argument("--out", required=True, type=Path, help="new or empty directory for the cells and the table")
    run.add_argument("--cells", default=",".join(CHEAP),
                     help=f"comma-separated cells of {', '.join(CELLS)} (default: the cheap ones)")
    run.add_argument("--top-k", type=int, default=DEFAULT_TOP_K,
                     help="blocking partner markets per market for every cell (default 24, the plan's K for model cells)")
    run.add_argument("--include-hf", action="store_true", help="allow the hf cell (needs MB_HF_MODEL and MB_HF_DEVICE)")
    run.add_argument("--profile", type=Path, help="resource profile of every cell (runcell --profile; default: runcell's own, "
                                                  "the toy profile)")
    run.add_argument("--candidates", type=Path, help="curated candidates.jsonl: every cell asks its questions and is scored "
                                                     "on its decisions (runcell --candidates)")
    col = sub.add_parser("collect", help="the baseline table of existing cell directories (nothing is run)")
    col.add_argument("--runs", nargs="+", required=True, type=Path, help="directories that hold the baseline cell directories")
    col.add_argument("--top-k", required=True, type=int, help="the K the table is asked for; a row built with another K "
                                                              "is a warning in its notes")
    col.add_argument("--out", required=True, type=Path, help="the table (JSON); its markdown is printed")
    args = parser.parse_args(argv)
    if args.cmd == "collect":
        return _collect_cli(args.runs, args.top_k, args.out)
    if args.candidates is not None and not args.candidates.is_file():
        print(f"--candidates {args.candidates} is not a file", file=sys.stderr)
        return EXIT_USAGE

    cells = [c.strip() for c in args.cells.split(",") if c.strip()]
    unknown = [c for c in cells if c not in CELLS]
    if unknown or not cells:
        print(f"unknown or empty --cells: {unknown or args.cells!r}; choose from {', '.join(CELLS)}", file=sys.stderr)
        return EXIT_USAGE
    if "hf" in cells:
        missing = [name for name in ("MB_HF_MODEL", "MB_HF_DEVICE") if not os.environ.get(name)]
        if not args.include_hf or missing:
            print("the hf cell needs --include-hf and MB_HF_MODEL and MB_HF_DEVICE in the environment"
                  + (f" (unset: {', '.join(missing)})" if missing and args.include_hf else ""), file=sys.stderr)
            return EXIT_USAGE
    if args.top_k < 1:
        print("--top-k must be at least 1", file=sys.stderr)
        return EXIT_USAGE
    fixture, out = args.fixture, args.out
    if not (fixture / "corpus.jsonl").is_file() or not (fixture / "queries.jsonl").is_file() or not (fixture / "gold").is_dir():
        print(f"{fixture} needs corpus.jsonl, queries.jsonl and gold/", file=sys.stderr)
        return EXIT_USAGE
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        print(f"{out} exists and is not empty; the baselines need a new or empty directory", file=sys.stderr)
        return EXIT_USAGE
    return build(fixture, out, cells, args.top_k, args.candidates, args.profile)


if __name__ == "__main__":
    raise SystemExit(main())
