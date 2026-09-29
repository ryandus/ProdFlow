# Matter Estimate — 120.0 GB, 6 months hosting

| Measure | Low | High |
|---|---:|---:|
| Documents collected | 480,000 | 720,000 |
| After de-duplication | 336,000 | 576,000 |
| Review set after culling | 117,600 | 288,000 |
| Review hours (incl. QC) | 2,352 | 7,680 |
| Processing cost | $3,600 | $9,000 |
| Hosting cost | $3,024 | $6,912 |
| Review cost | $141,120 | $614,400 |
| **Total** | **$147,744** | **$630,312** |

## Assumptions

| Assumption | Low | High |
|---|---:|---:|
| docs_per_gb | 4000 | 6000 |
| dedupe_reduction | 0.3 | 0.2 |
| culling_reduction | 0.65 | 0.5 |
| review_docs_per_hour | 55 | 45 |
| reviewer_rate_per_hour | 60 | 80 |
| qc_overhead | 0.1 | 0.2 |
| processing_per_gb | 30 | 75 |
| hosting_per_gb_month | 6 | 12 |

Planning ranges only; not a vendor quote. Low and High apply every assumption's favorable or unfavorable end together, so the spread is a bounding range, not a confidence interval. Override with `--assumptions file.json`.
