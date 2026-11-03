# Fresh-clone check, 2026-11-03

This records a check run on 2026-11-03 in a fresh clone of `https://github.com/TheOddsFox/MentatBench`, into an empty folder. The machine is the owner's Windows 11 computer, so this is a fresh-clone check, not a fresh machine. It was run by the owner, on the owner's machine and with the owner's accounts. It is **not an independent reproduction**; the [README](../../README.md#independent-reproduction) defines that term. The times and verdicts below are the owner's notes from the run. The `report.json` files are not committed here.

## Environment

| Item | Value |
|---|---|
| Operating system | Windows 11 Pro, 10.0.26300 |
| Python | 3.11.9, in `.venv` |
| Rust | rustc and cargo 1.99.1 |
| Ollama | 0.40.13 |
| git | 2.46.0 |
| Repository commit | `c741d64` (the default branch at the time of the clone) |
| GPU | 2 × NVIDIA RTX A6000 (49,140 MiB each), driver 576.02 |

## Steps and results

| Step | Command | Result | Time |
|---|---|---|---|
| Clone | `git clone https://github.com/TheOddsFox/MentatBench` into an empty folder | done | not recorded |
| Venv | `python -m venv .venv` | done | 38 s for these three steps together |
| Install | `.venv/Scripts/python -m pip install -e .` | done | (included above) |
| Engine | `cargo build --locked --release` | done (the machine's cargo registry cache was warm) | (included above) |
| Harness check | `gold_view` on `fixtures/mentat_hard_dev`, as in the README | headline 1.000 | 6 s |
| v0.1.0 cells | `python tools/reproduce.py run lexical heuristic` | `lexical`: exact; `heuristic`: exact | 8 s |
| v0.2.0 baseline | `python tools/reproduce.py run lexical --release v0.2.0` | refused: `no published cell 'lexical' in results/v0.2.0/cells` | not applicable |
| Model pulls | `ollama pull d1-omni:600m-q8_0`; `ollama pull laya:latest` | both pulled from the public registry; digests `1ba59f63e836…` and `1f196b378202…`, unchanged from the pins | not recorded |
| v0.2.0 model cells | `python tools/reproduce.py run d1-omni_600m-q8_0 laya_latest --release v0.2.0` | both exact; 0 flips of 10,716 decisions; laya abstained on over-length prompts, as published | 10 min 53 s in total |

The baseline refusal above is the state at the time of the check. The tool change that followed resolves a carried baseline to its v0.1.0 cell (see the README, Reproduce a published result). A follow-up run on the same machine on the same day, `python tools/reproduce.py run lexical heuristic --release v0.2.0`, gave `lexical`: exact and `heuristic`: exact, in 9 s. That follow-up run used this branch's uncommitted change to `tools/reproduce.py`; the commit `c741d64` does not contain the baseline resolution.

## Frictions

The check found seven frictions. The status column says what the change that accompanies this record does about each one.

| # | Friction | Status |
|---|---|---|
| 1 | The README's venv activation is POSIX-only (`source .venv/bin/activate`). | Windows activation is given in the README. |
| 2 | The README said Windows "has not been validated", though the reproduce path ran. | The README now records this check and says it is not independent. |
| 3 | No Ollama version was stated as a prerequisite. The v0.1.x cells were measured on 0.40.11 and the v0.2.0 cells on 0.40.13. | The README states both versions. |
| 4 | The README did not say how to get the model tags. | The README gives `ollama pull <tag>` and points to `children/ollama_models.json`. |
| 5 | Disk and GPU needs were not stated. | The README gives the sizes and the published GPUs. Other hardware is not checked. |
| 6 | The README's reproduce example was `d3_27b-q8_0` (29.5 GB, about 70 minutes), with no cheap first example. | The README gives `lexical heuristic` and `d1-omni_600m-q8_0 laya_latest --release v0.2.0` as first checks. |
| 7 | `--release v0.2.0` refused the baselines, which are carried from v0.1.0. | `tools/reproduce.py` resolves a carried baseline to the release that holds it, and the report names it as `measured_in`. |

One friction was found and not changed: the README's harness check uses POSIX syntax (`MB_ENGINE="$PWD/..." CUDA_VISIBLE_DEVICES= python ...`), which does not run unchanged in PowerShell.

## What this record does and does not show

- The check ran on the owner's machine with the owner's accounts. It is a fresh-clone check. It is not an independent reproduction.
- The verdicts are the tool's printed verdicts. The numbers come from the owner's notes.
- The check does not show that the v0.2.0 model results hold on other machines.
