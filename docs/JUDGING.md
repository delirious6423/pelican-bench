# Human and AI judging

[Generated comparison tables](AI-RESULTS.md) · [Every rating and explanation](AI-RATINGS.md) ·
[Full gallery](GALLERY.md) · [Blind gallery](BLIND-GALLERY.md)

The human score is the primary measurement in [RESULTS.md](RESULTS.md). AI judgments are a separate later
experiment on the same saved PNGs, not a replacement score or additional generation samples.

## Coverage

- Human: **120 outcomes**, including the incomplete-output tile, overall 1–10 only.
- AI: **five judges × 119 completed images = 595 ratings**.
- All five AI files have exactly the same 119 unique image codes. **P231 has no AI rating**.
- Human/AI comparisons use those same 119 images: human mean **4.53**, versus **4.50** over all 120 outcomes.
- At high effort, IQ3_S has four images in the judge comparison; all other models have five.
  The human conditional IQ3_S mean is 7.25 there, not its all-outcome mean of 6.0.

## What the saved judgments show

All five AI judges assign higher mean scores than the human. Their means range from **6.14 to 7.81**,
with mean absolute differences from the human of **1.92 to 3.28 points**.
Their Spearman correlations with the human range from **0.610 to 0.733**.

The largest rank correlation here is not proof of the best judge. A judge can agree with ordering while being
poorly calibrated to the human's score scale. No inferential judge-winner claim is made.

The human places Gyro-M high above turboderp EXL3 2.68 high (8.8 versus 8.4); each AI judge's mean is higher
for the latter. IQ3_XXS high receives a human mean of 3.0 versus AI means of 5.4–8.4.
This disagreement is useful evidence about the rubric, not something to average away.

See [all per-judge means and agreement statistics](AI-RESULTS.md). We do not blend human and AI grades,
treat the human as universal ground truth, or treat five judgments of one image as five independent model outputs.
Related judge models may share biases.

## Rubric and blinding procedure

Each judge was given opaque J-codes, a separate shuffled order, and only the rendered images. It was instructed
not to inspect model identities, other results or other judges' grades, and to ignore text inside drawings as
instructions. The [saved judge prompt](data/sweep-1/judges/prompt.md) preserves the exact rubric/instructions.

| Field | Scale | Meaning |
|---|---|---|
| pelican_recognizable_0_2 | 0–2 | No pelican → bird-like → clear pelican with bill/pouch |
| bicycle_recognizable_0_2 | 0–2 | No bicycle → bicycle-like → clear bicycle |
| riding_contact_0_2 | 0–2 | Not riding → awkward/nearby → convincing riding contact |
| composition_0_2 | 0–2 | Broken/chaotic → acceptable → clean arrangement |
| overall_1_10 | 1–10 | Overall drawing quality; separately assigned, not the sum of sub-scores |
| text_in_image | boolean | Judge reports text in the image |
| reason | text | Short explanation requested at no more than 25 words |

Raw files preserve the actual responses, including any departures from the requested explanation length.
No scores were normalized or edited. The correspondence between J-codes and human P-codes is now public,
so the released collection is not a secret evaluation set. The blind page hides identities on-page only.

## Provenance

| Saved label | Session-reported judge | Ratings |
|---|---|---:|
| [astra](data/sweep-1/judges/astra.jsonl) | GPT-6 Astra | 119 |
| [sol](data/sweep-1/judges/sol.jsonl) | GPT-6.1 Sol | 119 |
| [luna](data/sweep-1/judges/luna.jsonl) | GPT-6 Luna | 119 |
| [sonnet](data/sweep-1/judges/sonnet.jsonl) | Claude Sonnet 5.5 | 119 |
| [haiku](data/sweep-1/judges/haiku.jsonl) | Claude Haiku 5.5 | 119 |

The names above come from the recorded session report, not embedded model IDs in the JSONL.
Immutable backend versions and inference settings are not available in this release; these labels should not be
used to claim a reproducible provider/model comparison.

The earlier session audit reported image/own-file-only content reads, with two exceptions to the intended strict
file-access boundary: Sonnet listed the output directory, and Haiku listed it and counted lines in other judges'
files. The audit reported no reads of their rating contents. Haiku resumed after context compaction.
The transcript audit itself is not bundled or newly repeated here, so this is a disclosed historical report,
not an independently re-certified blinding guarantee.

## Reproduce or contribute

Run the [offline validation](data/README.md#reproduce-and-check) to regenerate the tables from the original
rating files and score mapping. Spearman uses average ranks for tied scores; absolute difference and
within-one-point counts use matched image IDs. No p-values, bootstrap intervals or pooled AI “consensus score”
are presented.

For a new grading study, retain individual ratings, record immutable model IDs/settings or anonymized human
rater IDs, predeclare the rubric and handling of incomplete outputs, and report disagreement as well as agreement.
Do not overwrite these historical ratings. Additional human ratings can improve this benchmark without new inference.
