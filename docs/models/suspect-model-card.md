# Model card: suspect lead-priority model (`suspect-evidence-v1`)

**Purpose.** Rank people and organisations in a case by how much recorded evidence suggests they deserve early attention. Decision support only.

**Method.** A transparent weighted evidence model: features from the case graph, FIR roles, money flows, communications, co-location, alerts and recency are normalised, saturated per feature family and summed as log-odds, then squashed to 0–100. Every point of the score is explained. Weights are set by hand and documented in `ai_engine/suspects/scorer.py`; they are **not fitted to labels**, because the bundled data contains no usable ground-truth labels. The output is therefore a priority, not a calibrated probability. Officer decisions (confirm/dismiss) feed back into the ranking.

**No label leakage.** The behavioural component never looks at the subject's own FIR role; the role is a separate prior.

**Evaluation (synthetic simulator, 20 random worlds per setting; `python scripts/eval_suspects.py`).** The simulator was written by the same authors as the model, so these numbers show that the method works on the structure it was designed for, not how it will perform on real cases.

| Setting | Model (behavioural only) AUC | Model (with partial roles) AUC | PageRank AUC | Degree AUC |
|---|---|---|---|---|
| Easy | 0.98 | 0.99 | 0.999 | 0.87 |
| Standard | 0.92 | 0.96 | 0.91 | 0.74 |
| Hard (decoys, noise) | 0.64 | 0.79 | 0.42 | 0.51 |

On the easy setting plain PageRank is slightly better; on the harder settings the evidence model is clearly ahead. On the bundled demo case (5 planted people) the model puts 3 of 5 in its top five, against 2 of 5 for degree centrality: a smoke test only.

**Limitations.** Uses only what is in the case; missing data lowers confidence, not the score. Names that are not linked across FIRs are not merged. It cannot see the real world. Always verify against source records.
