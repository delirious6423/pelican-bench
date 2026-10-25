# Data transparency update — 2026-10-25

## State

User authorized publication to GitHub as `v0.1.0`, including a version tag and GitHub Release.
Release notes cover benchmark data and AI judgments; privacy hardening remains in the data/methods documentation.
No inference, regrading, server or GPU task.
Original experiment files were read only. The pre-existing draft `harness/analyze.py` and scored CSV were
incorporated, with file-handle cleanup in the analysis helper and richer evidence fields in the CSV.

Start with [release notes](CHANGELOG.md), [results](docs/RESULTS.md), [AI judging](docs/JUDGING.md) and
[data/privacy methods](docs/data/README.md).

## Included evidence

- 120 scored outcomes, 119 unchanged rendered PNGs/SVG sources, one explicit failed outcome.
- 136 raw request/response records, including separately labelled compatibility/warmup records.
- 595 unchanged AI ratings: five judges on the same 119 completed images; readable per-image explanations.
- 480 quick-eval records and 120 effort-probe records; methods, configs, provenance and rebased telemetry.
- Approximately 17.2 MB of data files and complete gallery PNGs.

## Privacy gate

Exports stage outside the repo, then pass privacy and integrity checks before promotion. Tests verify that a
rejected export does not modify the destination. Original roots are replaced with role-based placeholders;
exact timestamps, process/request identities, network bindings, command/log snippets and unneeded environment
settings are removed. Timestamp fields are relative to sweep start; cumulative system telemetry is rebased.
Only the eight tested checkpoints remain in the catalogue. All PNGs were checked for ancillary metadata.
Declared transformations and the remaining privacy/provenance limits are in the data README.

This is data minimization plus automated/adversarial fixture checks, not a guarantee that arbitrary prose
contains no identifying information. Future dataset publications still require explicit approval.

## Validation

On Linux x64, pure offline checks only:

```sh
python3 -I -W error harness/test_publish.py
python3 -I harness/publish.py --check
(cd docs && sha256sum -c data/SHA256SUMS)
git diff --check
```

- PASS: 20 tests, including injected privacy leaks, compressed payload scanning, missing/duplicate judges,
  mismatched CSV values, all local documentation links, gallery coverage and staged-export rejection.
- PASS: regenerated reports and checksum manifest match; 418 independent `sha256sum` checks, zero non-OK.
- PASS: separate source-to-export comparisons for 136 requests/reasoning/answers/timing outcomes,
  595 complete AI rating objects, 120 human scores and all 119 PNG/SVG pairs.
- PASS: source config hashes match recorded provenance before export transformations.
- NOT APPLICABLE: serving, GPU inference, interactive grading, renderer and cross-platform app acceptance;
  none of those behaviors changed. No browser visual acceptance was run for the static chart/pages.
- Cleanup: no services/processes/VMs started; exporter staging directories are temporary and removed.

## Remaining limitations

The historical EXL3 patch and harness source snapshot are not bundled; recorded hashes alone cannot reconstruct
them. AI identity labels come from the historical session report, not embedded immutable backend/model IDs.
Blinding audit history is disclosed, not newly independently certified. Historical inferential statistics were
removed rather than presented as reproduced. The new reports are descriptive and keep the human/AI denominators separate.
