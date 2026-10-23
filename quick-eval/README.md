# quick-eval

Short machine-scored probes that show where a low-bit quant breaks. Complements the pelican harness, doesn't replace
it. Standard library only; one OpenAI-compatible server at a time.

| category | items | exposes |
|---|---|---|
| recall | 22 | long-tail knowledge loss |
| arith | 20 (synthesized) | digit precision: 4x4 multiply, sums, mod, date spans |
| format | 15 | instruction drift: strict JSON, word counts, no letter e, CSV, sorting |
| repeat | 3 | loops and duplicates in 40-60 item lists |
| tool | 3 | function-call JSON with distractor tools |
| zhtw | 3 | Traditional Chinese terms, simplified-character leakage |
| effort | 5 x 3 efforts | whether `reasoning_effort` changes anything; answer flips |
| context | 3 sizes | multi-needle retrieval at ~8K/16K/32K tokens, TTFT scaling |
| code | 10 | hidden asserts; **executes model code**, opt-in |

Every item is capped (`reasoning_budget_tokens` 1536 + `max_tokens` 2560 by default); cap hits are reported.
Reasoning is stripped before scoring.

## Run

Finds a server at `127.0.0.1:8080/v1`, then `:8093/v1` (override with `--base-url`, `--model`). Writes
`$PELICAN_ROOT/quick-eval/<stamp>-<label>/`: `runs.jsonl` (full raw records), `summary.json`, `summary.md`,
`manifest.json`.

```sh
cd quick-eval
python3 -I quick_eval.py --list                                         # plan only
python3 -I quick_eval.py --label q1 --categories recall,arith,format,repeat   # ~10 min core tier
python3 -I quick_eval.py --label q1 --categories tool,zhtw,effort
python3 -I quick_eval.py --label q1 --categories context --context-sizes 8000,16000,32000
python3 -I quick_eval.py --label q1 --categories code --run-code
python3 -I quick_eval.py --label q1 --samples 3                         # 3 seeds per cell
```

Flags: `--effort low|medium|high`, `--budget 0`, `--only recall-0,arith-mul`, `--stop-after 600`, `--no-warmup`.

## Fields

- `ttft_s`: first streamed reasoning or answer chunk; `t_first_answer_s`: first visible answer token.
- `client_tok_per_s_total` = completion tokens / wall time, not pure decode speed; `server_timings` keeps the
  engine's numbers when sent. `reasoning_chars` is a proxy where usage has no reasoning count.

Compare quants on the same categories, budget and seeds. Pass rates cover these items only. Five effort items
show whether efforts are honoured, not reasoning quality.

## Safety

`--run-code` runs model output on the host (`python3 -I -S`, empty env, temp dir, 768 MB, 10 s CPU, 16 processes).
That is a limit, not a sandbox; leave it off unless you accept that. Nothing here starts or stops servers.

## Tests

```sh
python3 -I test_quick_eval.py
```
