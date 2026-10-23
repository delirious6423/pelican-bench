# pelican-bench

A local, blind version of Simon Willison's ["pelican riding a bicycle"](https://simonwillison.net/tags/pelican-riding-a-bicycle/)
test for comparing LLM quants, plus a phone grading game.

Each model x thinking effort (low/medium/high) x 5 seeds is run on your own server. Raw responses are saved, the SVG
is extracted by a fixed rule and rendered in a sandbox, and you grade a shuffled gallery with model names hidden.

<p>
<img src="docs/images/gyro-m-tq2_0-high-s44.png" width="200">
<img src="docs/images/td-exl3-2.68-h6-ng8-high-s44.png" width="200">
<img src="docs/images/sc-exl3-3.05-h8-high-s44.png" width="200">
</p>

Example: [docs/RESULTS.md](docs/RESULTS.md), 8 quants, 120 pelicans graded blind.

## Built on

| project | used for |
|---|---|
| [simonw/pelican-bicycle](https://github.com/simonw/pelican-bicycle) | the prompt and the idea |
| [Strata](https://github.com/Niko1221/Strata) | inference engine (official release, GGUF and Gyro quants) |
| [sociolog/Strata `exl3-lane`](https://github.com/sociolog/Strata/tree/exl3-lane) | Strata fork that runs EXL3 quants |
| [Qwen/Qwen3.8-Flash-Next](https://huggingface.co/Qwen/Qwen3.8-Flash-Next) | base model of every quant tested |
| [ISTA-DASLab GSQ-RCO GGUF](https://huggingface.co/ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF) | IQ3_XXS, IQ3_S |
| [agentionai Gyro GGUF](https://huggingface.co/agentionai/Qwen3.8-Flash-Next-Gyro-GGUF) | Gyro-S TQ1_0, Gyro-M TQ2_0 |
| [turboderp EXL3](https://huggingface.co/turboderp/Qwen3.8-Flash-Next-exl3) | 2.18 and 2.68 bpw |
| [scorpoon EXL3-SC](https://huggingface.co/scorpoon/Qwen3.8-Flash-Next-exl3-SC) | 2.05 and 3.05 bpw |
| [ExLlamaV3](https://github.com/turboderp-org/exllamav3) | EXL3 format |
| [bubblewrap](https://github.com/containers/bubblewrap), [librsvg](https://gitlab.gnome.org/GNOME/librsvg) | sandboxed SVG rendering |

## Contents

| path | does |
|---|---|
| `harness/sweep.py` | runs the matrix one model at a time: compat probe, warm-up, shuffled cells, quick-eval core; resumable; pauses on GPU heat or foreign GPU load |
| `harness/serverctl.py` | one Strata server per model as a user systemd unit on loopback; edit `MODELS` for your checkpoints |
| `harness/pelican.py` | prompt, SVG extraction rule, unsafe-construct flags |
| `harness/monitor.py` | 1 s sampler: RAM, swap, CPU, disk, GPU; resource window per request |
| `harness/render.py`, `rasterize.py` | SVG to PNG in bubblewrap: no network, no home, no scripts, rlimits |
| `harness/gallery.py` | blind PNG gallery, empty scoring sheet, separate reveal key |
| `harness/grade.py` | the grading game: phone page, tap 1-10, autosaves to the PC |
| `harness/report.py` | per-run CSV/JSONL and model x effort tables (latency, tokens, decode speed, resources) |
| `harness/test_harness.py` | unit tests against a fake streaming server |
| `quick-eval/` | short machine-scored probes (recall, arithmetic, format, repetition, effort) |

Python 3.10+, standard library only, Linux. Rendering needs
`sudo apt install bubblewrap python3-gi gir1.2-gdkpixbuf-2.0 librsvg2-common`. Optional: `nvidia-smi` for GPU stats
and pauses; Strata's `tools/strata_tokenizer.py` plus `regex` for re-tokenized counts.

## Setup

All output goes under `$PELICAN_ROOT` (default `./pelican-data`). Paths are env vars read at the top of
`serverctl.py`:

```sh
export PELICAN_ROOT=/big/disk/pelican-data
export STRATA_SRC=... STRATA_EXE=... STRATA_PY=... STRATA_MTP_RT=...   # official Strata + MTP runtime dir
export EXL3_STRATA_SRC=... EXL3_STRATA_PY=... EXL3_BASE_CONFIG=...      # EXL3 fork (EXL3_STRATA_EXE optional)
export GGUF_DIR=... SC_EXL3_DIR=...                                     # checkpoints; HF cache for the rest
export PELICAN_DATA_DEV=nvme0n1 PELICAN_NVME_STATE=/sys/class/nvme/nvme0/state   # optional disk watch
```

- GGUF models need a Strata pack at `$PELICAN_ROOT/packs/<name>/` with a `tokenizer/` folder, and
  `$STRATA_SRC/data/expert-profile.bin`.
- `EXL3_BASE_CONFIG` is a working fork config whose `args` include `--exl3` and `--ple-exl3`; the harness repoints
  them per model and uses its `exe` unless `EXL3_STRATA_EXE` is set.
- `BUILD.json` next to the official binary is recorded if present.
- The run aborts if the watched NVMe state leaves `live` or disappears. No state file at start: not watched.

Other engines (llama.cpp, vLLM, ...) need edits in `serverctl.py` (`MODELS`, `build_config()`, `start()`,
`provenance()`), the effort-table check in `sweep.py`, and `tokenizer_for()` in `report.py`. The client itself is
plain OpenAI streaming and reads reasoning from `reasoning_content` or inline `<think>`.

## Run

```sh
cd harness
python3 -I test_harness.py                          # unit tests (fake server)
python3 -I sweep.py pilot --run pilot-1             # compat for every model + effort pilot
python3 -I sweep.py sweep --run sweep-1 --deadline 2700
python3 -I sweep.py probes --run probes-1           # optional effort probes
python3 -I gallery.py --run sweep-1                 # render + blind gallery + reveal key
python3 -I report.py --run sweep-1                  # tables in runs/sweep-1/report/
```

Run long sweeps under `systemd-run --user`; `status.sh sweep-1` shows progress. Set `MAX_TOKENS` in `sweep.py` from
your pilot: high effort can exceed 32k tokens.

## Grading game

```sh
python3 -I grade.py --run sweep-1 --host 127.0.0.1 --port 18094
```

Each pelican is a card with a 1-10 row (sub-scores and notes optional). Grades autosave to the PC and the phone;
model names and the reveal key are never served. Scores land in `runs/<run>/grade/scores-latest.json` and
`gallery/scoring-sheet-filled.csv`.

- From a phone: bind `--host` to a Tailscale/WireGuard IP or use an SSH tunnel. There is no login, so never bind
  to a network you don't control.
- Phone copies are per gallery; on reopen the PC copy wins, except phone edits the PC never confirmed, which are
  re-sent. After a server restart the page asks for a reload; unsent edits stay on the phone.
- Saves need a per-session token, so other websites can't post grades.

Afterwards, join the scores with `runs/<run>/reveal/reveal-key.csv`. No analysis script is included;
[docs/RESULTS.md](docs/RESULTS.md) describes ours.

## Rules

- Exact prompt, no system message; temperature 1.0, top_p 0.95, top_k 20, min_p 0; seeds 11/22/33/44/55.
- Same token ceiling and deadline for every model; change one, change all.
- First complete `<svg>...</svg>` in the visible answer; nothing is repaired; missing or unclosed SVGs count.
- Model output is never executed; SVGs are only rasterized in the sandbox.
- Every request is saved raw with timings, tokens, finish reason and resource window.

## License

MIT, see [LICENSE](LICENSE). Images in `docs/images/` are outputs of the models named in their file names.
