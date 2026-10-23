# Example run: 8 quants of Qwen3.8-Flash-Next on a laptop GPU

RTX 4090 Laptop GPU (16 GB), ~64 GB RAM, Linux. [Strata](https://github.com/Niko1221/Strata) v0.2.3 for GGUF/Gyro,
the [exl3-lane fork](https://github.com/sociolog/Strata/tree/exl3-lane) for EXL3. Thinking on, 3 efforts x 5 seeds
x 8 models = 120 pelicans. Ceiling 49152 tokens incl. reasoning, deadline 2700 s, temperature 1.0, top_p 0.95,
top_k 20. One person graded blind on a phone (overall 1-10); models revealed afterwards.

## Findings

- **Effort dominates.** Mean grade: high 6.50 (95% CI 5.65-7.28), medium 3.62, low 3.38; holds inside every model
  (stratified permutation p < 0.0001).
- **Models separate only at high effort** (pooled p = 0.12; high p = 0.0003).
- **Best: Gyro-M TQ2_0 at high, 8.8/10**, tied with the top three EXL3 quants but 1.7-2.4x faster per image.
- 119/120 complete SVGs; one hit the ceiling after 9 minutes of reasoning (not a loop).
- Quick-eval core (60 items, medium): 57-60/60 for all quants; it did not predict drawing quality.

| model, high effort | mean | grades (seeds 11-55) | median time |
|---|---|---|---|
| Gyro-M TQ2_0 | **8.8** | 9 7 8 10 10 | 6:37 |
| turboderp EXL3 2.68bpw h6 | 8.4 | 8 8 8 10 8 | 15:40 |
| EXL3 3.05bpw h8 | 8.0 | 8 8 7 9 8 | 11:03 |
| EXL3 2.05bpw h8 | 7.8 | 8 8 8 6 9 | 10:43 |
| GGUF IQ3_S | 6.0 | 8 6 1 8 7 (1 = ceiling hit) | 7:35 |
| Gyro-S TQ1_0 | 5.4 | 8 2 6 3 8 | 3:57 |
| turboderp EXL3 2.18bpw h6 | 4.6 | 3 3 3 6 8 | 10:18 |
| GGUF IQ3_XXS | 3.0 | 3 6 1 4 1 | 4:12 |

## Median time per image (m:ss, n = 5)

| model | low | medium | high | decode tok/s, low / high |
|---|---|---|---|---|
| Gyro-S TQ1_0 | 0:31 | 0:41 | 3:57 | 96.7 / 85.1 |
| GGUF IQ3_XXS | 0:37 | 0:35 | 4:12 | 95.9 / 76.3 |
| GGUF IQ3_S | 0:39 | 0:55 | 7:35 | 85.6 / 71.6 |
| Gyro-M TQ2_0 | 0:39 | 0:41 | 6:37 | 74.6 / 64.3 |
| EXL3 2.05bpw h8 | 0:58 | 1:24 | 10:43 | 47.3 / 42.5 |
| turboderp EXL3 2.18bpw h6 | 1:42 | 2:02 | 10:18 | 42.0 / 35.6 |
| EXL3 3.05bpw h8 | 1:21 | 2:01 | 11:03 | 36.6 / 32.4 |
| turboderp EXL3 2.68bpw h6 | 1:34 | 1:28 | 15:40 | 36.8 / 31.9 |

High effort is almost all thinking: 3.5-11.5 minutes before the first visible token. 6 of 40 high runs needed
more than 32768 tokens.

## Caveats

- Two engines, so speed mixes format and engine.
- Weights are memory-mapped; under RAM pressure runs re-read up to 121 GB per model across five high runs.
- One grader, n = 5 per cell, overall score only; pairwise p-values uncorrected.
- A 1.5 GiB renderer memory limit killed one text+filter SVG; now 4 GiB, with a regression test.

## Best high-effort image per model

<p>
<img src="images/gyro-m-tq2_0-high-s44.png" width="180"> <img src="images/td-exl3-2.68-h6-ng8-high-s44.png" width="180">
<img src="images/sc-exl3-3.05-h8-high-s44.png" width="180"> <img src="images/sc-exl3-2.05-h8-high-s55.png" width="180">
<img src="images/gguf-iq3_s-high-s11.png" width="180"> <img src="images/gyro-s-tq1_0-high-s11.png" width="180">
<img src="images/td-exl3-2.18-h6-ng8-high-s55.png" width="180"> <img src="images/gguf-iq3_xxs-high-s22.png" width="180">
</p>
