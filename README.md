# pelican-bench

A local, reproducible version of the "SVG of a pelican riding a bicycle" test for comparing quantized LLMs, plus a
blind grading game you can play on your phone.

It runs every model x thinking effort (low / medium / high) x 5 seeds against your own inference server, saves
every raw response, extracts the SVG with a fixed rule, renders it in a sandbox, and gives you a shuffled gallery
with the model names hidden. You grade on your phone, then join the grades with the reveal key.

<p>
<img src="docs/images/gyro-m-tq2_0-high-s44.png" width="200">
<img src="docs/images/td-exl3-2.68-h6-ng8-high-s44.png" width="200">
<img src="docs/images/sc-exl3-3.05-h8-high-s44.png" width="200">
</p>

Example run and results: [docs/RESULTS.md](docs/RESULTS.md) (8 quants of one model, 120 pelicans, graded blind).

## What is in here

| path | what it does |
|---|---|
| `harness/sweep.py` | runs the matrix, one model at a time: compat probe, warm-up, shuffled scored cells, quick-eval core; resumable (`state.json`); pauses on GPU heat or foreign GPU load |
| `harness/serverctl.py` | starts/stops one server per model as an owned user systemd unit on a loopback port (written for [Strata](https://github.com/Niko1221/Strata); adapt `MODELS` and `build_config` for your engine) |
| `harness/pelican.py` | the prompt, SVG extraction rule `v1-first-balanced-svg`, unsafe-construct flags |
| `harness/monitor.py` | 1 s sampler: RAM, swap, CPU, disk reads, GPU (nvidia-smi), per-request resource windows |
| `harness/render.py`, `rasterize.py` | renders SVG to PNG inside bubblewrap: no network, no home, no scripts, rlimits |
| `harness/gallery.py` | blind gallery (PNG only), empty scoring sheet, separate reveal key |
| `harness/grade.py` | **the grading game**: mobile web page, tap 1-10 per pelican, autosaves to the PC |
| `harness/report.py` | per-run CSV/JSONL, per model x effort tables (latency, tokens, decode speed, resources) |
| `harness/test_harness.py` | unit tests with a fake streaming server (sandbox render tests need `bwrap`) |
| `quick-eval/` | short machine-scored probes (recall, arithmetic, strict format, repetition, effort) |

Python 3.10+, standard library only. Linux (the renderer needs `bwrap` and GdkPixbuf with the librsvg loader:
`sudo apt install bubblewrap python3-gi gir1.2-gdkpixbuf-2.0 librsvg2-common`). GPU stats and the heat/foreign-GPU pauses need `nvidia-smi`; without it they are skipped.
Re-tokenized token counts in the report need Strata's `tools/strata_tokenizer.py` and the `regex` package.

## Setup

Everything is written under `$PELICAN_ROOT` (default `./pelican-data`). Engine and checkpoint locations are
environment variables read at the top of `harness/serverctl.py`; edit `MODELS` there for your checkpoints.

```sh
export PELICAN_ROOT=/big/disk/pelican-data
export STRATA_SRC=... STRATA_EXE=... STRATA_PY=...      # official engine
export EXL3_STRATA_SRC=... EXL3_STRATA_EXE=... EXL3_STRATA_PY=... EXL3_BASE_CONFIG=...   # optional EXL3 build
export STRATA_MTP_RT=...                                  # MTP draft runtime dir (dense.txt, draft_vocab.bin)
export GGUF_DIR=... SC_EXL3_DIR=...                       # checkpoints (HF cache is used for the rest)
export PELICAN_DATA_DEV=nvme0n1 PELICAN_NVME_STATE=/sys/class/nvme/nvme0/state   # disk to watch (optional)
```

What the official-engine launches also expect (see `build_config()`):

- a Strata pack per GGUF model at `$PELICAN_ROOT/packs/<name>/` (built with Strata's pack tooling from the GGUF),
  including a `tokenizer/` subfolder; the expert profile at `$STRATA_SRC/data/expert-profile.bin`;
- for EXL3 models, `EXL3_BASE_CONFIG` is a working server config for that build: its `args` must contain `--exl3`
  and `--ple-exl3`, which the harness repoints per model. Its `exe` is used unless `EXL3_STRATA_EXE` is set;
- `BUILD.json` next to the official binary is recorded if present (optional).

The disk watch aborts the run if the NVMe controller state stops being `live`; if `PELICAN_NVME_STATE` does not
exist the check is skipped.

Using a different engine (llama.cpp, vLLM, ...): the harness was written for Strata, so expect to adapt it.
The places that assume Strata:

- `serverctl.py`: `MODELS`, `build_config()` and `start()` (launch command; keep `BASE_URL`), and `provenance()`,
  which hashes Strata's `serve/` files and reads its effort table;
- `sweep.py`: blocks a model whose effort table does not map low/medium/high to low/medium/xhigh (`effort_table_ok`);
- `report.py`: `tokenizer_for()` re-tokenizes with Strata's tokenizer tool.

The client itself speaks the OpenAI streaming API and reads reasoning from `reasoning_content` or inline `<think>`
blocks. Strata extras (`reasoning_budget_tokens`, `/metrics` correlation) are recorded when present.

## Run

```sh
cd harness
python3 -I test_harness.py                         # unit tests (fake server)
python3 -I sweep.py pilot --run pilot-1             # compat for every model + effort pilot on two
python3 -I sweep.py sweep --run sweep-1 --deadline 2700
python3 -I sweep.py probes --run probes-1           # optional effort probes
python3 -I gallery.py --run sweep-1                 # render + blind gallery + reveal key
python3 -I report.py --run sweep-1                  # tables in runs/sweep-1/report/
```

Long runs: start `sweep.py` under `systemd-run --user` so it survives your terminal; `status.sh sweep-1` prints
progress. Set the token ceiling and deadline from your pilot (`MAX_TOKENS` in `sweep.py`): high effort can need far
more than 32k tokens.

## The grading game

```sh
python3 -I grade.py --run sweep-1 --host 127.0.0.1 --port 18094
```

Open it on your phone. Every pelican is a card with a big 1-10 row (sub-scores and notes optional); taps autosave
to the PC and to the phone, so you can stop and resume. Model names and the reveal key are never served. Scores land
in `runs/<run>/grade/scores-latest.json` and `gallery/scoring-sheet-filled.csv`.

To reach it from a phone, bind `--host` to an address only you can reach: your Tailscale/WireGuard IP, or use an SSH
tunnel. Avoid binding to a LAN or public address you do not control: there is no login.

Phone copies are kept per gallery, and the PC copy wins when you reopen the page, so an old phone cannot
overwrite newer grades. Saves need a per-session token embedded in the page, so other websites cannot post to it.

After grading, join the scores with `runs/<run>/reveal/reveal-key.csv` yourself. The repo has no analysis script;
[docs/RESULTS.md](docs/RESULTS.md) describes what we ran (effort and model effects with permutation tests,
bootstrap CIs).

## Rules the harness enforces

- Exact prompt, no system message, explicit sampling (temperature 1.0, top_p 0.95, top_k 20, min_p 0), seeds
  11/22/33/44/55 as replicate draws.
- Same token ceiling and wall deadline for every model; if you change them after the pilot, change them for all.
- First complete `<svg>...</svg>` in the visible answer; nothing is repaired; unclosed or missing SVGs are results.
- Model output is never executed. SVGs are only rasterized in the sandbox; the gallery shows PNGs.
- Every request is saved raw, including failures, with its timings, tokens, finish reason and resource window.

## License

MIT, see [LICENSE](LICENSE). Model outputs in `docs/images/` were generated by the models named in their file names.
