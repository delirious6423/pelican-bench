# Release notes

## v0.1.0 — 2026-10-25 — data transparency and AI judgments

This update exposes existing October 23–24 results. **No new inference or AI grading was run.**
No model ranking is claimed beyond the observed prompt, machine and grading procedure.

### Results and evidence

- Add [all 120 seed-level scores with individual times](docs/SEEDS.md), token counts, finish reasons,
  truncation/render status, a [score-versus-time plot](docs/images/score-time.svg) and [machine-readable data](docs/data/README.md).
- Add [all 119 rendered images and the failed outcome](docs/GALLERY.md), a separate
  [blind view](docs/BLIND-GALLERY.md), full raw requests/responses and SVG source as text.
- Separate completion reliability from finished-image quality. IQ3_S high: one cap hit in five,
  mean 6.0 including the failure versus 7.25 across four completed images.
- Include saved resource telemetry, model/configuration provenance, rendered prompts and checksums.
- Expose [480 existing quick-eval responses and 120 effort probes](docs/QUICK-EVAL.md), including failed items.

### AI judgment update

- Include **595 existing ratings from five AI judges on 119 completed images**, with original rubric
  sub-scores, explanations, orders and code mapping. A [per-image comparison page](docs/AI-RATINGS.md)
  shows all five ratings beside each image.
- Add [human/AI agreement and per-model comparisons](docs/AI-RESULTS.md), with
  [blinding, provenance and denominator limitations](docs/JUDGING.md).
- Keep human and AI results separate. Do not impute a judgment for the incomplete SVG or pool correlated
  ratings into extra generation samples.

### Corrections and reproducibility

- Replace “best” with highest observed human mean, and remove equivalence/“statistically tied” claims.
- Correct the blanket effort claim: IQ3_XXS medium mean 3.4 exceeds its high mean of 3.0.
- Correct Gyro-M's median-time ratios against the three leading EXL3 configurations to 1.62–2.36.
- Document requested high → resolved xhigh, template-inserted effort system text, engine/configuration
  confounds, the separate renderer-memory fix, and unavailable historical patch/version evidence.
- Remove historical inferential claims that are not reproduced by the included descriptive analysis.
- Add an offline evidence exporter, reproducible reports, checksums and data-integrity/regression tests.
- No changes to generation, serving, rendering or interactive grading behavior.

The tagged source archive includes the dataset, galleries, analysis scripts and documentation.
