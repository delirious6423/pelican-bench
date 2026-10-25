# Existing non-pelican evaluations

Generated from the saved item-level records; no additional inference.

Quick-eval: medium effort, seed 11, reasoning budget 1536, completion cap 2560. One draw per item.

| Model | Passed | Arithmetic | Format | Recall | Repetition | Cap hits | Errors/aborts |
|---|---:|---:|---:|---:|---:|---:|---:|
| GGUF IQ3_S | 59/60 | 20/20 | 15/15 | 21/22 | 3/3 | 0 | 0 |
| GGUF IQ3_XXS | 60/60 | 20/20 | 15/15 | 22/22 | 3/3 | 0 | 0 |
| Gyro-M TQ2_0 | 57/60 | 18/20 | 14/15 | 22/22 | 3/3 | 0 | 0 |
| Gyro-S TQ1_0 | 57/60 | 17/20 | 15/15 | 22/22 | 3/3 | 0 | 0 |
| EXL3-SC 2.05bpw h8 | 58/60 | 18/20 | 15/15 | 22/22 | 3/3 | 0 | 0 |
| EXL3-SC 3.05bpw h8 | 60/60 | 20/20 | 15/15 | 22/22 | 3/3 | 0 | 0 |
| turboderp EXL3 2.18bpw h6 | 59/60 | 19/20 | 15/15 | 22/22 | 3/3 | 0 | 0 |
| turboderp EXL3 2.68bpw h6 | 59/60 | 20/20 | 15/15 | 21/22 | 3/3 | 0 | 0 |

These near-saturated scores do not reproduce the human drawing-quality ranking. They are not evidence of equal general capability.

## Failed items

| Model | Item | Expected/scoring detail |
|---|---|---|
| gguf-iq3_s | recall-22 | expected one of [&#x27;John Williams&#x27;] |
| gyro-m-tq2_0 | format-reverse | expected &#x27;noitazitnauq&#x27; |
| gyro-m-tq2_0 | arith-days-1 | expected 3919, got 3921 |
| gyro-m-tq2_0 | arith-days-3 | expected 1274, got 1244 |
| gyro-s-tq1_0 | arith-days-0 | expected 2602, got 2607 |
| gyro-s-tq1_0 | arith-days-1 | expected 3919, got 3918 |
| gyro-s-tq1_0 | arith-days-3 | expected 1274, got 1214 |
| sc-exl3-2.05-h8 | arith-days-1 | expected 3919, got 3889 |
| sc-exl3-2.05-h8 | arith-days-2 | expected 1357, got 1367 |
| td-exl3-2.18-h6-ng8 | arith-mod-2 | expected 944, got 584 |
| td-exl3-2.68-h6-ng8 | recall-22 | expected one of [&#x27;John Williams&#x27;] |

[Full requests, answers and scoring details](data/sweep-1/quick-eval.jsonl).

## Effort probes

Five short items × three efforts × eight models = **120/120 passed**. Seed 11, reasoning budget 0, completion cap 49152.
These items were too easy to distinguish the tested settings. Do not interpret their perfect scores as broad reasoning equivalence.

[All probe responses](data/sweep-1/effort-probes.jsonl) · [Archived item definitions](data/sweep-1/quick-eval-items.jsonl)
