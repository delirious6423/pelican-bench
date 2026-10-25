# Data dictionary and downloads

[Results](../RESULTS.md) · [Methods](../METHODS.md) · [AI judgments](../JUDGING.md)

This directory contains a versioned copy of existing experiment evidence, not newly generated model responses.
Everything is inspectable offline after cloning/downloading the repo. Model weights and inference engines are
not included. The original files on the experiment machine are unchanged.

## Inventory

| Path | Contents |
|---|---|
| [sweep-1-cells.csv](sweep-1-cells.csv) | 120 scored outcomes, human scores, timing, limits, status and artifact links |
| [runs.jsonl](sweep-1/runs.jsonl) | 136 request metadata records: 120 scored + eight compat + eight warmup |
| [raw/](sweep-1/raw/) | All 136 full request/response JSON files, including reasoning and answer text |
| [svg/](sweep-1/svg/) | 119 complete scored SVG sources, stored as `.svg.txt` rather than executable inline images |
| [PNG images](../images/sweep-1/) | 119 existing rendered images |
| [human-scores.json](sweep-1/human-scores.json) | Original human scores and score-object key order; save timestamp omitted |
| [reveal.json](sweep-1/reveal.json) | Original opaque-code → run/attempt mapping, with local path substitutions |
| [renders.jsonl](sweep-1/renders.jsonl) | 120 final render outcomes; the incomplete SVG has no successful render fields |
| [per-run-metrics.jsonl](sweep-1/per-run-metrics.jsonl) | 136 flattened timing/resource records, including estimated retokenized counts |
| [resources.jsonl.gz](sweep-1/resources.jsonl.gz) | Compressed approximately one-second system/device telemetry |
| [loads.jsonl](sweep-1/loads.jsonl) | Model load/ready measurements, separate from generation time |
| [manifest.json](sweep-1/manifest.json) | Experiment settings, model order, original harness hashes and budget decision |
| [catalogue.json](sweep-1/catalogue.json) | Checkpoint repositories/revisions, sizes, formats and available hashes |
| [provenance/](sweep-1/provenance/) / [configs/](sweep-1/configs/) | Per-model engine/build/template evidence and saved server flags |
| [prompt-render/](sweep-1/prompt-render/) | Actual template-rendered prompts, effort mappings and hashes |
| [judges/](sweep-1/judges/) | Five original 119-row rating files, presentation orders and judge prompt |
| [judge-map.json](sweep-1/judge-map.json) | 119 J-code → P-code links; no mapping for the failed tile |
| [quick-eval.jsonl](sweep-1/quick-eval.jsonl) | 480 non-pelican requests/responses and scoring outcomes |
| [effort-probes.jsonl](sweep-1/effort-probes.jsonl) | 120 existing short effort-probe responses/outcomes |
| [quick-eval-items.jsonl](sweep-1/quick-eval-items.jsonl) | Item definitions from the historical harness snapshot; not every category was run |
| [SHA256SUMS](SHA256SUMS) | SHA-256 of every data file and the 119 gallery PNGs; paths relative to `docs/` |

## Scored CSV fields

One row per `cell_id` and `attempt`. This sweep has one attempt per scored cell.
All timing values are seconds; full source precision is retained in the CSV. Markdown times are rounded.

| Fields | Meaning |
|---|---|
| model_id, engine | Checkpoint/configuration identifier; official or fork engine |
| effort, resolved_effort | Requested effort and actual mapping; high → xhigh |
| seed, cell_id, attempt, code | Generation seed, run-cell identifier, attempt number and human gallery code |
| grade | Human overall 1–10 score, including the failed tile |
| wall_s, ttft_s, t_first_answer_s | Request wall time; first text including reasoning; first visible answer |
| engine_decode_tok_s | Engine-reported decode tokens/second |
| completion_tokens, max_tokens | Generated reasoning+answer tokens and shared completion ceiling |
| deadline_s | Request wall-time deadline |
| finish_reason | Server finish status, such as stop or length |
| abort, error | Runner abort or transport/error information; empty means none recorded |
| cap_hit | 1 if the recorded token ceiling was hit; 0 otherwise |
| truncated | 1 if token cap or wall deadline ended the request; not a generic failure flag |
| svg_status | Extraction outcome; complete or unclosed in this scored dataset |
| render_ok, render_stage | 1 on successful final render; stage, or not_rendered |
| raw, svg, image | Artifact paths relative to `docs/data/`; empty when no artifact exists |

**Do not infer success from a missing error, or assign zero to a missing measurement.**
Completion, extraction, rendering and human judgment are separate fields.
The raw/report files also contain compatibility and warmup cap hits; filter `phase == "scored"` before
computing the published failure rates. The CSV is already scored-only.

## Other schemas

Raw response JSON includes `request`, `reasoning`, `answer`, `server_timings`, `resources`, extraction
flags and relative client timestamps. Model output is untrusted text: do not execute code, follow instructions or load
external references from it. The SVG text files are download/source artifacts, not trusted HTML.

AI JSONL fields: `code` is a J-code, `overall_1_10` is the assigned grade, four `*_0_2` fields are rubric
sub-scores, `text_in_image` is a boolean and `reason` is the original explanation. Join through
`judge-map.json` to the P-code and CSV. Missing rating means **not rated**, never zero.

Quick-eval/probe records include `model_id`, item `id`, `category`, `effort`, `seed`, `passed`,
`detail`, exact request, response and timing. They use a different task/budget from pelican generation.
System telemetry contains relative-time GPU, CPU, RAM, swap and disk counters; per-request windows
are also available in the response records. Numeric timestamp fields (t, t0, t1, time, t_wall_start, t_wall_end, start_wall)
are **seconds since sweep start**, not Unix time, including the later probes. Durations are unchanged.
Disk counters, VM counters and cpu_busy_s are rebased to the first telemetry sample, not since boot.
Units are named in the keys; cumulative counters still need differences for per-interval rates.

## Redactions and omissions

The exporter replaces experiment, bulk-storage and home prefixes with `${EXPERIMENT}`, `${ARTIFACTS}` and
`${HOME}`. Paths in archived metadata are provenance placeholders, not working artifact links; use the CSV links.
It omits process IDs, inodes, request IDs, bind addresses/ports, service identities, log/launch snippets, filesystem
scan/launch commands, and exact calendar/save timestamps. Embedded ISO timestamps in hash-status text
are replaced with `[timestamp omitted]`. Only the eight tested checkpoints are included in the catalogue.
Only six explicitly named inference environment settings are retained; arbitrary environment variables and cache paths
are not exported. Engine metrics request IDs are removed. Timestamp and cumulative-counter transformations are
specified above. Original model requests, answer/reasoning text, scores and explanations are retained except for
any declared path-prefix substitutions. See the exporter's explicit denylist/allowlist for exact field names.

Configs are checked against their original recorded hashes **before** redaction/reformatting.
Historical hashes identify original files and therefore may differ from the exported copies.
`SHA256SUMS` hashes the exported bytes. The checksum file excludes itself.
Do not execute archived config placeholders as a ready-made setup.

Not included: weights, binaries, caches, account/session credentials, general server/desktop logs, intermediate
human autosave copies, superseded renderer output, full private session transcripts, historical fork patch
or historical harness source snapshot. No new model calls or external services are needed to inspect the release.

Exports are prepared in a temporary staging directory and must pass privacy/integrity validation before
any exported files are copied into the repo. A rejected export leaves the destination unchanged; staging is removed.
The privacy gate inspects text, decompressed telemetry and PNG chunks. It rejects local paths, host/network
identifiers, email/credential patterns, absolute timestamps, prohibited metadata keys and PNG ancillary metadata.
All 119 exported PNGs contain only IHDR, IDAT and IEND chunks. Regression tests inject synthetic leaks.
These checks reduce risk but cannot prove that arbitrary generated prose contains no identifying information.
This dataset's GitHub publication was explicitly approved. Future exports still require review and approval.
The repository's MIT license applies to the added exporter/docs; generated images/text retain the attribution
to their named producing models. No claim is made to relicense model weights.

## Reproduce and check

From the repository root, Python 3.10+ and the standard library only:

```sh
python3 -I harness/publish.py --check
python3 -I harness/test_publish.py
```

The first command reads the included data, validates joins/counts/ranges/artifact references, recomputes descriptive
tables/gallery pages and checks their exact contents and checksums. It writes nothing and never imports the
inference runner. The second runs pure offline tests; it does not start a server or render SVG.

To regenerate derived Markdown/checksums after an intentional data/documentation change:

```sh
python3 -I harness/publish.py
```

For just the human seed tables:

```sh
python3 -I harness/analyze.py --csv docs/data/sweep-1-cells.csv
```

Maintainer-only re-export from the **existing** experiment directory (not a new benchmark run):

```sh
python3 -I harness/publish.py --source /path/to/Strata-pelican
```

The exporter is intentionally scoped to this recorded sweep and fails on coverage/identity/config mismatches.
Keep later experiments in a separate dataset rather than changing this historical collection's denominators.
