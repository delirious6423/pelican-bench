# Methods and reproducibility

[Results](RESULTS.md) · [Data inventory](data/README.md) · [AI judging](JUDGING.md)

## Scope and machine

Eight quantized checkpoints of Qwen3.8-Flash-Next; three requested efforts; seeds 11, 22, 33, 44, 55.
RTX 4090 Laptop GPU, 16 GB VRAM, approximately 64 GB physical RAM (about 61 GiB reported by Linux).
This is an independent local experiment, not a quant maker's model-card evaluation.
Attribution and checkpoint repositories are in the [README](../README.md#built-on).

The sweep elapsed 8 h 38 m 47 s on October 23–24, 2026; human grading followed.
Exact wall-clock dates/times and timezone-bearing metadata are omitted from the public artifacts.
All observations in this release come from saved files; none were regenerated through a model server.

## Request and actual prompt

Client prompt, exactly:

> Generate an SVG of a pelican riding a bicycle

No client-supplied system message. **The engine's template inserts effort-specific system text for low and
xhigh**, so “no system message” does not mean the rendered model input contains none.
The archived [official](data/sweep-1/prompt-render/prompt-render-official-0.2.3.json) and
[fork](data/sweep-1/prompt-render/prompt-render-fork-exl3pack.json) evidence contains rendered prompts and hashes.

| Setting | Value |
|---|---|
| Requested effort → resolved effort | low → low; medium → medium; high → xhigh |
| Thinking | Enabled |
| Reasoning budget sent | 0: no additional runner reasoning cap |
| Completion cap | 49,152 tokens, reasoning plus visible answer |
| Wall deadline | 2,700 seconds |
| Idle timeout | 600 seconds |
| Temperature / top_p / top_k / min_p | 1.0 / 0.95 / 20 / 0 |
| Presence / frequency / repetition penalty | 0 / 0 / 1 |
| Context / KV | 65,536 / int8 |

The cap was raised uniformly from a provisional 32,768 after pilot observations, before the scored sweep.
The [manifest](data/sweep-1/manifest.json) records the decision, sampling, order and harness-file hashes.
Actual requests are included with every raw response.

## Ordering, exclusions and denominators

One model was loaded at a time, in the order recorded by the manifest. Within each model, scored cells were
shuffled with efforts interleaved. One compatibility check and one warmup per model were excluded.
The response log therefore has **136 records: 120 scored, eight compatibility, eight warmup**.
This sweep has one attempt for each scored cell. Public records retain attempt IDs.

Five seeds are repeated draws from each configuration, not matched randomness across engines.
Model runs occurred in blocks rather than a randomized cross-model interleave: time/order effects remain possible.
A failure stays in the all-outcome human summary. Missing AI judgments are not zeroes and are not dropped
silently from a mixed-denominator comparison.

## Timing and resources

- `wall_s`: client request duration, not model load time or total sweep elapsed time.
- `ttft_s`: first generated text, including reasoning. Not necessarily a visible answer.
- `t_first_answer_s`: first visible answer text; includes the preceding reasoning delay.
- `engine_decode_tok_s`: engine-reported decode rate, not completion tokens divided by total wall time.
- Completion counts come from the saved usage/engine records. Re-tokenized reasoning and answer counts in
  the metrics export are estimates and are labelled `*_retok`.
- Per-request resource windows and the compressed approximately one-second telemetry are included.
  Timestamp fields use seconds relative to sweep start, not Unix time. System disk, VM and CPU-busy cumulative
  telemetry counters are rebased to the first sample so pre-experiment activity is not disclosed.
  System RAM/swap/disk counters are not exclusively attributable to the model.
  GPU power is device power, not wall-socket energy.
- All 120 scored requests have a recorded `metrics_match=time+tokens`. This is a correlation check
  between the client and engine records, not a new accuracy measurement.

The engines memory-map weights. Disk reads, available RAM, cache state, speculative decoding and different
engine implementations contribute to the observed speed. Do not label the time ratios as pure quant-format effects.

## Extraction, rendering and grading

Rule `v1-first-balanced-svg`: first complete SVG in visible output; no repair.
Unclosed SVGs remain failed outcomes. Unsafe constructs are flagged independently of visual quality.
Only sandbox-rendered PNGs were presented to graders.

Final renderer: gdk-pixbuf SVG loader / librsvg 2.61.3, render version `v2-as4096`.
One complete text/filter SVG exceeded the earlier 1.5 GiB address-space limit; the limit was increased to
4 GiB and all outputs rerendered. The final records contain 119 successful renders and one unclosed SVG
that was not rendered. The old renderer's failure is not counted as a second generation failure.

The human graded a shuffled, opaque-code gallery on a phone, with model identities hidden until afterwards.
Only overall 1–10 scores were recorded. The human gave the incomplete-output tile 1.
The later AI experiment is documented separately in [JUDGING.md](JUDGING.md).

## Provenance and remaining gaps

- [Checkpoint catalogue](data/sweep-1/catalogue.json): repository/revision information, formats, sizes and
  available file hashes. Some pre-existing downloads were **not hashed**; null is not a verified digest.
- [Per-model provenance](data/sweep-1/provenance/): engine/server/template hashes, build and load details.
- [Server configs](data/sweep-1/configs/): recorded flags and cache/model path relationships.
  The exporter verifies each original config's SHA-256 against the recorded provenance before including it.
- The EXL3 fork was locally modified. Its commit and diff hash are recorded, but **the corresponding
  historical patch is not included**. A hash alone cannot reconstruct those edits.
- The original harness-file hashes are preserved. The current portable repo is not claimed to be byte-identical
  to the private historical harness snapshot; this release does not include that snapshot's source.
- Judge identities are session-reported; individual rating files do not contain immutable model version IDs,
  model request/response envelopes or inference settings. See [judge provenance](JUDGING.md#provenance).

Thus the release supports **offline result reproduction and artifact inspection**, not a claim of bit-for-bit
inference reproducibility. Identical prompt, seed and limits cannot ensure identical draws across engines.
