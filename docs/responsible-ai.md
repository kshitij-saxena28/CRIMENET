# Responsible AI

The platform is decision-support only. It must not infer guilt, criminal intent or protected characteristics, or trigger enforcement. Every analytical signal exposes provenance, confidence, model/version and a human review state.

## What each component actually is

| Component | What it is | Validation |
|---|---|---|
| FIR extraction | Hand-written rules + local Tesseract OCR | Fixture tests only |
| Entity context scorer | TF-IDF + logistic regression, a *secondary* signal | Held-out split in `ai_engine/models/metrics.json`. The data are synthetic and templated, so the score is an optimistic upper bound, not real-world accuracy |
| Entity resolution | Char n-gram KNN + string similarity, hand-set weights and thresholds | None; always requires human confirmation |
| FIR interlinking | Shared-identifier rules | Deterministic; matches are leads, not identity |
| Anomaly detector | Isolation Forest + LOF on graph features, fitted per case | **Uncalibrated.** Thresholds (`ANOMALY_*`) are configurable heuristics. Scores are ranks against the case's own baseline, not probabilities, and hubs score high by construction |
| Translation | Glossary/phrase normalisation, not neural MT | Marked `limited` in `/models/status` |

## Safeguards

* The context model file is only loaded if its SHA-256 matches `MODEL_HASHES.json` (joblib files are pickles).
* Nothing enters the verified graph until an investigator reviews it.
* Before any operational use, measure precision/recall on authorised, labelled real FIRs and calibrate the anomaly thresholds.
