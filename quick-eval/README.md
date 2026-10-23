# quick-eval

Short-turn, machine-scored probes that expose where a low-bit local quant breaks. Companion to the pelican
harness in `../harness/`; it does not replace the pelican matrix.
Standard library only, one OpenAI-compatible server at a time, loopback only.

## Why these tests

| category | items | what it exposes | typical turn |
|---|---|---|---|
| recall | 22 | long-tail knowledge loss (hit first by quantization) | one line |
| arith | 20 (synthesized, values computed) | digit-level precision: 4x4 mul, 6-number sums, mod, date spans | one number |
| format | 15 | instruction drift: strict JSON, word counts, no-letter-e, CSV, sorting | short |
| repeat | 3 | looping / duplicates on 40-60 item enumerations | 60 lines |
| tool | 3 | function-call JSON fidelity with distractor tools | one call |
| zhtw | 3 | Traditional Chinese term retention and simplified-character leakage | one paragraph |
| effort | 5 x 3 efforts | does `reasoning_effort` change anything; answer flips between low/medium/high | one number |
| context | 3 sizes | multi-needle retrieval at ~8K/16K/32K prompt tokens, INT8 KV fidelity, TTFT scaling | three numbers |
| code | 10 | correctness under hidden asserts; **executes model code**, opt-in | one function |

Every run is capped: `reasoning_budget_tokens` (default 1536) plus `max_tokens` (default 2560). Cap hits are
counted and reported, not hidden. The reasoning channel is stripped before scoring.

## Run

Server discovery probes `127.0.0.1:8080/v1` then `:8093/v1` and takes the first model id. Override with
`--base-url` / `--model`. Output goes to `$PELICAN_ROOT/quick-eval/<timestamp>-<label>/`
(`runs.jsonl` raw records with full request, reasoning, answer, usage, server `timings`; `summary.json`;
`summary.md`).

```sh
cd quick-eval
python3 -I quick_eval.py --list                                   # the plan, no server needed
python3 -I quick_eval.py --label exl3-2.05bpw --categories recall,arith,format,repeat   # ~10 min tier
python3 -I quick_eval.py --label exl3-2.05bpw --categories tool,zhtw,effort
python3 -I quick_eval.py --label exl3-2.05bpw --categories context --context-sizes 8000,16000,32000,60000
python3 -I quick_eval.py --label exl3-2.05bpw --categories code --run-code
python3 -I quick_eval.py --label exl3-2.05bpw --samples 3        # 3 seeds per cell, all default categories
```

Useful flags: `--effort low|medium|high` (non-effort categories), `--budget 0` (no thinking budget),
`--only recall-0,arith-mul` (id substrings), `--stop-after 600` (wall-time guard), `--no-warmup`.
One excluded warm-up request runs first and is logged in `manifest.json`.

## Timing fields (per record)

- `ttft_s`: request start to first streamed `reasoning_content` or `content` chunk (monotonic clock, client side).
- `t_first_answer_s`: first visible answer token; long thinking shows up as the gap from `ttft_s`.
- `wall_s`, `completion_tokens` (server usage), `client_tok_per_s_total` = completion tokens / wall, which is
  **not** pure decode throughput. `server_timings` keeps the engine's own numbers when it sends them.
- Reasoning token counts are not in Strata's chat `usage`; `reasoning_chars` is a labeled proxy.

## Reading results

Compare quants on the same categories, same budget, same seeds. The intended order: recall, arith, format,
repeat first (all under about 10 minutes per model at 20-50 tok/s); context and code only on quants that
survive. A pass rate is for these items only, not a general leaderboard. Five effort items are enough to see
whether low/medium/high are honoured (reasoning length should scale) but not to rank reasoning quality.

## Safety notes

- `--run-code` executes model output on the host in a subprocess with `python3 -I -S`, empty environment,
  temp cwd, 768 MB address space, 10 s CPU, 1 MB file size, 16 processes. This is a limit, not a sandbox.
  Leave it off unless you accept that.
- Nothing here starts, stops or reconfigures Strata.

## Tests

```sh
python3 -I test_quick_eval.py      # 16 tests: checkers, item bank, synthesized items, fake SSE server
```
