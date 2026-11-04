# Transfer pilot: results

Run on 2026-11-04 under [`PREREG.md`](PREREG.md), which was merged before the first judge call. **Exploratory.** The pilot
changes no leaderboard row, score or rank. It asks one pre-registered question: does the v0.2.0 System One order of the 17
models carry over from the synthetic Mentat-Hard test set to items built from real Polymarket questions?

- **The statistics** below are in [`results.json`](results.json), written by `analyse.py score` from
  [`predictions.json`](predictions.json). Judge agreement and status counts are in
  [`consensus_summary.json`](consensus_summary.json), and the selection counts are in [`selection_report.json`](selection_report.json).
- **Every departure** from the pre-registration is in [`deviations.md`](deviations.md) (D1 to D9).
- **The question text is not published.** Polymarket's terms do not grant redistribution, so the repository holds market ids and sha256
  hashes. `rehydrate.py` refetches the texts by id from Polymarket's public Gamma API and checks each hash.

## Answer

**On these 200 items, under these conditions, the order carries over** (the pre-registered reading). The Spearman correlation
between the 17 models' pilot headlines and their v0.2.0 headlines is **ρ = 0.821**, with a 95% cluster-bootstrap interval of
**[0.772, 0.890]**. The interval comes from 2,000 draws over 89 clusters. The lower bound is above the pre-registered threshold of 0.6.

| Statistic | Point | 95% interval |
|---|---|---|
| Spearman ρ, headline (primary) | **0.821** | [0.772, 0.890] |
| Kendall τ-b, headline | 0.618 | [0.574, 0.735] |
| Spearman ρ, relation MCC only | 0.838 | [0.735, 0.865] |
| Spearman ρ, group MCC only | 0.311 | [0.032, 0.464] |
| v0.2.0 Holm-resolved headline pairs that keep their order | 101 of 117 (86%) | |

**The agreement comes from the relation task.** On relations, the order holds nearly as well as on the headline. On groups, the
correlation is weak, and its interval nearly reaches 0. Group MCC is low for every model on both sets: the best pilot group MCC is
0.211, and the v0.2.0 group scores are also compressed. Small differences there are mostly noise. This matches the earlier
group-item analysis (`docs/audits/v0.2.0-group-items/`).

**Sensitivity analyses.** All three pre-registered sensitivity analyses give the same reading:

| Analysis | Items (relations, groups) | ρ | 95% interval | Reading |
|---|---|---|---|---|
| Unanimous judges only | 116, 70 | 0.821 | [0.752, 0.885] | carries over |
| Excluding items the reviewer changed | 120, 80 | 0.821 | [0.772, 0.890] | carries over |
| Excluding trivial items | 108, 80 | 0.824 | [0.740, 0.880] | carries over |

"Excluding changed" equals the primary analysis. All 11 spot-check items the reviewer changed were changed to UNCLEAR, so they
were already dropped before the selection.

## The 17 models

Headline is the mean of relation and group MCC. Pilot intervals are cluster-bootstrap 95% intervals. Ranks are by point
estimate.

| v0.2.0 rank | Model | v0.2.0 headline | Pilot headline [95% CI] | Pilot rank | Pilot relation MCC | Pilot group MCC | Relation accuracy | Group accuracy |
|---|---|---|---|---|---|---|---|---|
| 1 | D3 27B | 0.521 | 0.557 [0.475, 0.623] | 1 | 0.905 | 0.208 | 93.3% | 31.2% |
| 2 | D3 9B | 0.274 | 0.400 [0.337, 0.471] | 4 | 0.629 | 0.171 | 74.2% | 35.0% |
| 3 | Pplx Decider 27B | 0.257 | 0.424 [0.359, 0.494] | 2 | 0.694 | 0.155 | 78.3% | 22.5% |
| 4 | Kev 9B | 0.236 | 0.206 [0.131, 0.275] | 12 | 0.377 | 0.035 | 50.8% | 15.0% |
| 5 | D3 4B | 0.232 | 0.348 [0.286, 0.422] | 7 | 0.668 | 0.029 | 76.7% | 17.5% |
| 6 | Clef 27B | 0.208 | 0.405 [0.339, 0.469] | 3 | 0.599 | 0.211 | 70.0% | 31.2% |
| 7 | Nimble-v3 9B | 0.200 | 0.337 [0.256, 0.409] | 8 | 0.507 | 0.167 | 65.0% | 23.8% |
| 8 | Nimble 9B | 0.177 | 0.359 [0.302, 0.418] | 6 | 0.627 | 0.090 | 73.3% | 17.5% |
| 9 | Clef-Flash 9B | 0.173 | 0.370 [0.302, 0.441] | 5 | 0.589 | 0.151 | 70.8% | 26.2% |
| 10 | TEV1 4B | 0.142 | 0.219 [0.148, 0.289] | 11 | 0.463 | -0.025 | 63.3% | 21.2% |
| 11 | Kev 4B | 0.137 | 0.170 [0.088, 0.234] | 13 | 0.222 | 0.117 | 44.2% | 31.2% |
| 12 | D1 3B | 0.071 | 0.227 [0.145, 0.307] | 10 | 0.401 | 0.053 | 58.3% | 27.5% |
| 13 | D3 2B | 0.052 | 0.259 [0.190, 0.324] | 9 | 0.460 | 0.058 | 62.5% | 26.2% |
| 14 | Kev 0.8B | 0.052 | 0.059 [0.019, 0.103] | 16 | 0.118 | 0.000 | 32.5% | 32.5% |
| 15 | TEV1 0.8B | 0.000 | 0.027 [-0.032, 0.085] | 17 | 0.069 | -0.014 | 31.7% | 5.0% |
| 16 | D1-Omni 600M | -0.003 | 0.089 [0.039, 0.138] | 15 | 0.059 | 0.120 | 23.3% | 15.0% |
| 17 | Laya 0.4B | -0.020 | 0.105 [0.044, 0.179] | 14 | 0.100 | 0.111 | 40.0% | 17.5% |

**Baselines** (scale anchors, not in the correlation):
- **Rules baseline (`heuristic`):** pilot headline 0.221 [0.115, 0.290] (relation MCC 0.336, group MCC 0.106). Its v0.2.0 headline is
  0.140.
- **Lexical baseline:** 0.000. As published, it answers no group items.

**Observations.** These are exploratory and were not pre-registered.
- **D3 27B leads on both sets.** On the pilot its relation MCC (0.905) is far above every other model.
- **Most other models score higher on the pilot than on v0.2.0.** The pilot items are a different mix, for example many date and
  threshold ladders whose logic is visible in the wording. Headline levels are therefore not comparable across the two sets; only the
  order is.
- **Kev 9B moves the most:** from 4th to 12th. Its pilot relation MCC is 0.377, while the four models ranked next to it on v0.2.0
  (2nd, 3rd, 5th and 6th) score 0.60 to 0.69 on the pilot. Kev 4B and Kev 0.8B also rank lower on the pilot.
- **The rules baseline holds the same place on both sets.** It scores above 7 of the 17 models on the pilot and on v0.2.0 alike.

## How the items were made and labelled

- **Source.** The source was a frozen pull of 2,100 high-volume Polymarket events created since 2026-05-03, and the candidate pool was
  built from it:
  - 560 items: 400 relations and 160 groups;
  - drawn by structural type: same-event alternatives, threshold and date ladders, Yes/No of one market, same-topic and random
    pairs, event fields, mixed sets and decoys;
  - 131 clusters of related events among the pool items, 89 of them among the 200 selected items. The bootstrap resamples those 89.
- **Judging.** Three separate sessions of Claude Haiku 5.5 judged every item from the question text and outcome label alone, with no tools
  and in separate `claude -p` calls. All 36 calls were valid on the first attempt. Fleiss' kappa was 0.930 on relations and 0.789 on
  groups.
  - 488 items were unanimous, 41 had a 2–1 majority, 3 had two agreeing valid answers, 3 had three different answers, and 25 were
    dropped as UNCLEAR by majority.
- **Review.** The maintainer reviewed the 3 all-different items and a seeded 10% spot check of 54 items.
  - 11 spot-check labels changed, all to UNCLEAR. Nine were the reviewer's own answers, and T0085 and T0525 were consistency
    revisions (D3). The first nine already exceeded the limit of 2, which triggered the pre-registered escalation, and every remaining
    2–1 item (33) was reviewed too.
  - 90 items were reviewed in all.
  - The review was AI-assisted, then checked and owned by the maintainer, and eight answers were revised after a consistency check
    (`deviations.md` D1 to D5).
- **Selection.** After review, 503 items were labelled and 57 dropped. The seeded selection drew 120 relations and 80 groups.
  - Relations: EXCLUDES 36, IMPLIES or IMPLIED_BY 36, NONE 36, and 12 trivial Yes/No pairs.
  - Groups: none 26, at_least_k 15, at_least_k with at_most_k 13, at_most_k 12, ordered_chain 8, at_most_one 6.
  - No relation was labelled EQUIV after review, so the pilot says nothing about that class.
- **Runs.**
  - **Models:** the 17 models ran with each v0.2.0 cell's own child, profile, build flags and relation floor, on Ollama 0.40.13 with
    CUDA, with the tags pinned by digest. The engine binary is the one the v0.2.0 model cells record.
  - **Build step only:** runcell itself cannot run a fixture without exam queries, so only its build step ran (D6).
  - **First attempts excluded:** they ran on the Vulkan backend by mistake and were excluded before any prediction was computed (D9).
  - **Completeness:** every model answered all 200 items, with no item errors.
  - **Mapping:** decisions were mapped to classes with `decision_scoring`'s own helpers (D7). No decision had impossible group bounds,
    so the one difference from v0.2.0's mapping did not arise.

## Limits

- **The labels are not exact gold.** They come from model judges plus an AI-assisted human review of 90 items. SPEC.md §7 asks for
  independent annotation and adjudication. The reviewer used a stricter ambiguity standard than the judges, and the roughly 470 items
  the reviewer did not see keep the judges' readings. Typical cases are "reach" or "dip to" read as thresholds, and undated fixtures
  read as the dated match. The unanimous-only analysis gives the same reading.
- **The question text alone.** Models and judges saw the question text and outcome label, not the market rules. The models also saw
  each event's title, but only in the binding prompt, as in v0.2.0. Judges did not see it.
- **Memorisation is possible.** The questions are public and recent, and the models' training data is not known.
- **One pilot.** These are 200 items in 89 clusters, from one snapshot of one venue. The relation floors were calibrated on the
  v0.2.0 test fixture and carried over unchanged.
- **One run per model,** as on the leaderboard. The v0.2.0 run-to-run and Ollama-version shifts are far smaller than the pilot
  intervals.

## Data

Polymarket market and event ids are used under Polymarket's public API. No question text is redistributed.
