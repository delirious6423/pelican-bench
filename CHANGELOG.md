# Release notes

## v0.1.0 — 2026-10-25 — data transparency and AI judgments

The full recorded experiment is now available to inspect: **eight quants, three efforts, five seeds each**.
This release shares existing results; no new inference or AI grading was run.

### What's included

- [All 120 outcomes](docs/SEEDS.md): individual scores, times, token counts and completion status, plus a
  [score-versus-time plot](docs/images/score-time.svg).
- [119 rendered images and the failed outcome](docs/GALLERY.md), with a separate [blind gallery](docs/BLIND-GALLERY.md).
- [595 AI ratings](docs/AI-RATINGS.md): five judges' scores, rubric sub-scores and explanations beside each image,
  plus [human-versus-AI comparisons](docs/AI-RESULTS.md).
- [480 existing quick-eval responses and 120 effort probes](docs/QUICK-EVAL.md).
- [Raw requests, responses, SVG sources, resource measurements and model configurations](docs/data/README.md),
  with [methods and limitations](docs/METHODS.md).

### Interpreting the results

Human and AI scores remain separate: the human graded all 120 outcomes; the AI judges rated the 119 completed
images. IQ3_S high averaged **6.0 including its token-cap failure**, versus **7.25 among four completed images**.
The results describe one prompt on one machine, not a general model ranking.

### Downloads

The release's attached **sweep-1-cells.csv** contains all 120 scored outcomes. The source ZIP and tar.gz include
the complete dataset, images, galleries, analysis scripts and documentation.
