#!/usr/bin/env python3
"""Per-run CSV/JSONL exports and per-model/per-effort summaries for a pilot or sweep run.

  <strata-venv>/bin/python -I report.py --run sweep-1   (needs `regex` for the Strata tokenizer)

Token accounting: the server's completion_tokens (reasoning + answer as generated) is the only authority for the
generated total. Strata's chat usage has no reasoning/answer split, so reasoning_tokens_retok and
answer_tokens_retok are RE-TOKENIZED ESTIMATES of the streamed text with the model's own tokenizer: labelled,
reported separately, never added to the server total.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import serverctl as sc  # noqa: E402

ROOT = Path(os.environ.get("PELICAN_ROOT", "pelican-data")).resolve()
TOK_TOOLS = sc.OFF_SRC / "tools"         # Strata's strata_tokenizer.py (needs the `regex` package)
_TOKS = {}


def tokenizer_for(model_id: str):
    cfg = json.loads((sc.CONFIGS / f"{model_id}.json").read_text())
    tdir = Path(cfg["tokenizer"])
    if tdir not in _TOKS:
        sys.path.insert(0, str(TOK_TOOLS))
        import strata_tokenizer as ST
        vocab = json.loads((tdir / "vocab.json").read_text(encoding="utf-8"))
        tokens = [None] * len(vocab)
        for t, i in vocab.items():
            tokens[i] = t
        merges = (tdir / "merges.txt").read_text(encoding="utf-8").split("\n")
        types = json.loads((tdir / "token_type.json").read_text())
        _TOKS[tdir] = ST.Tokenizer(tokens, merges, types)
    return _TOKS[tdir]


def med(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return round(statistics.median(xs), 2) if xs else None


def rng(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return [round(min(xs), 2), round(max(xs), 2)] if xs else None


def g(d, *path):
    for k in path:
        d = (d or {}).get(k) if isinstance(d, dict) else None
    return d


def load_runs(run: Path) -> list[dict]:
    recs = [json.loads(l) for l in open(run / "pelican/runs.jsonl", encoding="utf-8")]
    renders = {}
    rp = run / "renders.jsonl"
    if rp.exists():
        for l in open(rp, encoding="utf-8"):
            x = json.loads(l)
            renders[x["cell_id"]] = x
    for r in recs:
        raw = json.loads(Path(r["raw_file"]).read_text(encoding="utf-8"))
        try:
            tok = tokenizer_for(r["model_id"])
            r["reasoning_tokens_retok"] = len(tok.encode(raw.get("reasoning") or ""))
            r["answer_tokens_retok"] = len(tok.encode(raw.get("answer") or ""))
        except Exception as e:  # noqa: BLE001 - an estimate only
            r["reasoning_tokens_retok"] = r["answer_tokens_retok"] = None
            r["retok_error"] = str(e)
        x = renders.get(r["cell_id"]) if r.get("phase") == "scored" else None
        r["render_ok"] = bool(x and x.get("ok"))
        r["render_stage"] = x.get("stage") if x else None
        r["render_blank"] = (x.get("nonwhite_frac") or 0) < 0.002 if x and x.get("ok") else None
        r["xml_wellformed"] = x.get("wellformed") if x else None
    return recs


FLAT = ["run", "phase", "model_id", "engine", "cell_id", "attempt", "requested_effort", "resolved_effort", "seed",
        "ts", "finish_reason", "cap_hit", "abort", "error", "ttft_s", "t_first_byte_s", "t_first_reasoning_s",
        "t_first_answer_s", "wall_s", "thinking_phase_s", "answer_phase_s", "prompt_tokens", "completion_tokens",
        "total_tokens", "cached_tokens", "reasoning_tokens_retok", "answer_tokens_retok", "reasoning_chars",
        "answer_chars", "engine_decode_tok_s", "history_decode_tok_s", "engine_prefill_tok_s", "prefill_ms",
        "decode_ms", "prompt_cache_n", "draft_n", "draft_accepted", "engine_generated", "reasoning_recoveries",
        "metrics_match", "client_tok_s_after_first_text", "client_tok_s_wall", "svg_status", "svg_unsafe",
        "xml_wellformed", "render_ok", "render_stage", "render_blank", "max_tokens", "budget_sent", "deadline_s"]
RES = [("vram_max_mib", ("vram_used_mib", "max")), ("gpu_util_mean", ("gpu_util_pct", "mean")),
       ("gpu_temp_max_c", ("gpu_temp_c", "max")), ("gpu_power_mean_w", ("gpu_power_w", "mean")),
       ("gpu_sm_mhz_mean", ("gpu_sm_mhz", "mean")), ("gpu_mem_mhz_mean", ("gpu_mem_mhz", "mean")),
       ("cpu_pkg_max_c", ("cpu_pkg_c", "max")), ("mem_avail_min_gib", ("mem_available_gib", "min")),
       ("swap_used_max_gib", ("swap_used_gib", "max")), ("server_rss_max_gib", ("server_rss_gib", "max")),
       ("server_cpu_cores", ("server_cpu_cores",)), ("other_cpu_cores", ("other_cpu_cores",)),
       ("swap_in_pages", ("swap_in_pages",)), ("swap_out_pages", ("swap_out_pages",)),
       ("data_disk_read_gb", ("data_disk_read_gb",)), ("sys_disk_read_gb", ("sys_disk_read_gb",)),
       ("major_faults", ("major_faults",))]


def flat(r):
    out = {k: r.get(k) for k in FLAT}
    out["svg_unsafe"] = ";".join(r.get("svg_unsafe") or [])
    for name, path in RES:
        out[name] = g(r.get("resources"), *path)
    out["clock_event_reasons"] = json.dumps(g(r.get("resources"), "gpu_clock_event_reasons"))
    out["foreign_gpu_seen"] = json.dumps(g(r.get("resources"), "foreign_gpu_seen"))
    out["paused_hot_s"] = g(r.get("gate"), "paused_hot_s")
    out["paused_foreign_s"] = g(r.get("gate"), "paused_foreign_s")
    out["contention"] = json.dumps(g(r.get("gate"), "contention"))
    return out


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    return {
        "n": n, "finish": dict(sorted(defaultdict(int, {k: sum(1 for r in rows if r["finish_reason"] == k)
                                                        for k in {r["finish_reason"] for r in rows}}).items(), key=str)),
        "cap_hits": sum(bool(r["cap_hit"]) for r in rows), "deadline_aborts": sum(r["abort"] == "deadline" for r in rows),
        "other_aborts": sum(bool(r["abort"]) and r["abort"] != "deadline" for r in rows),
        "svg_complete": sum(r["svg_status"] == "complete" for r in rows),
        "svg_unclosed": sum(r["svg_status"] == "unclosed" for r in rows),
        "svg_none": sum(r["svg_status"] == "none" for r in rows),
        "xml_wellformed": sum(bool(r["xml_wellformed"]) for r in rows),
        "render_ok": sum(bool(r["render_ok"]) for r in rows), "render_blank": sum(bool(r["render_blank"]) for r in rows),
        "unsafe_flagged": sum(bool(r["svg_unsafe"]) for r in rows),
        "completion_tokens_med": med([r["completion_tokens"] for r in rows]),
        "completion_tokens_range": rng([r["completion_tokens"] for r in rows]),
        "reasoning_tokens_retok_med": med([r["reasoning_tokens_retok"] for r in rows]),
        "answer_tokens_retok_med": med([r["answer_tokens_retok"] for r in rows]),
        "ttft_s_med": med([r["ttft_s"] for r in rows]), "t_first_answer_s_med": med([r["t_first_answer_s"] for r in rows]),
        "wall_s_med": med([r["wall_s"] for r in rows]), "wall_s_range": rng([r["wall_s"] for r in rows]),
        "engine_decode_tok_s_med": med([r["engine_decode_tok_s"] for r in rows]),
        "engine_decode_tok_s_range": rng([r["engine_decode_tok_s"] for r in rows]),
        "client_tok_s_after_first_text_med": med([r["client_tok_s_after_first_text"] for r in rows]),
        "prefill_ms_med": med([r["prefill_ms"] for r in rows]),
        "draft_accept_rate": (round(sum(r["draft_accepted"] or 0 for r in rows) / sum(r["draft_n"] or 0 for r in rows), 3)
                              if sum(r["draft_n"] or 0 for r in rows) else None),
        "vram_max_mib": max([r["vram_max_mib"] for r in rows if r["vram_max_mib"]] or [None], key=lambda x: x or 0),
        "mem_avail_min_gib": min([r["mem_avail_min_gib"] for r in rows if r["mem_avail_min_gib"] is not None] or [None],
                                 key=lambda x: x if x is not None else 1e9),
        "swap_used_max_gib": max([r["swap_used_max_gib"] or 0 for r in rows] or [None]),
        "server_rss_max_gib": max([r["server_rss_max_gib"] or 0 for r in rows] or [None]),
        "gpu_temp_max_c": max([r["gpu_temp_max_c"] or 0 for r in rows] or [None]),
        "gpu_power_mean_w_med": med([r["gpu_power_mean_w"] for r in rows]),
        "gpu_sm_mhz_mean_med": med([r["gpu_sm_mhz_mean"] for r in rows]),
        "data_disk_read_gb_total": round(sum(r["data_disk_read_gb"] or 0 for r in rows), 2),
        "other_cpu_cores_med": med([r["other_cpu_cores"] for r in rows]),
        "metrics_unmatched": sum(r["metrics_match"] is None for r in rows),
        "contention_marked": sum(r["contention"] not in ("[]", "null") for r in rows),
    }


def quick_eval_table(run: Path, name="quick-eval") -> dict:
    out = {}
    d = run / name
    if not d.exists():
        return out
    for m in sorted(p for p in d.iterdir() if p.is_dir()):
        s = m / "summary.json"
        if s.exists():
            j = json.loads(s.read_text())
            out[m.name] = {"total": f"{j['total_passed']}/{j['total_runs']}", "categories": j["categories"],
                           "wall_s": j["total_wall_s"]}
        else:
            out[m.name] = {"status": "incomplete (no summary.json)"}
    return out


def _fmt(v):
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.1f}" if abs(v) >= 10 else f"{v:.2f}"
    if isinstance(v, list):
        return "-".join(_fmt(x) for x in v)
    return str(v)


def markdown(s: dict) -> str:
    L = [f"# Run {s['run']} - generated tables", "",
         "Token columns: `gen` = server completion_tokens (authority, reasoning+answer); `reas~`/`ans~` = re-tokenized "
         "estimates of the streamed reasoning/answer text (labelled estimates, not added to gen). Decode = engine clock "
         "(server timings.predicted_per_second). TTFT = first generated text incl. streamed reasoning; 1st ans = first "
         "visible answer text. Times in seconds.", "",
         "## Pelican per model x phase x effort", "",
         "| model | phase | effort | n | SVG ok/unclosed/none | render ok (blank) | unsafe | cap | deadline | gen med (range) "
         "| reas~ med | ans~ med | TTFT med | 1st ans med | wall med (range) | decode tok/s med (range) | draft acc | "
         "VRAM max MiB | RAM avail min GiB | swap max GiB | GPU max C | power med W | disk read GB |",
         "|" + "---|" * 23]
    for c in s["per_model_effort"]:
        L.append("| " + " | ".join(_fmt(x) for x in [
            c["model_id"], c["phase"], c["effort"], c["n"],
            f"{c['svg_complete']}/{c['svg_unclosed']}/{c['svg_none']}", f"{c['render_ok']} ({c['render_blank']})",
            c["unsafe_flagged"], c["cap_hits"], c["deadline_aborts"],
            f"{_fmt(c['completion_tokens_med'])} ({_fmt(c['completion_tokens_range'])})",
            c["reasoning_tokens_retok_med"], c["answer_tokens_retok_med"], c["ttft_s_med"], c["t_first_answer_s_med"],
            f"{_fmt(c['wall_s_med'])} ({_fmt(c['wall_s_range'])})",
            f"{_fmt(c['engine_decode_tok_s_med'])} ({_fmt(c['engine_decode_tok_s_range'])})", c["draft_accept_rate"],
            c["vram_max_mib"], c["mem_avail_min_gib"], c["swap_used_max_gib"], c["gpu_temp_max_c"],
            c["gpu_power_mean_w_med"], c["data_disk_read_gb_total"]]) + " |")
    L += ["", "## Server loads (cold start to ready)", "", "| model | ok | ready s | VRAM max MiB | data-disk read GB | "
          "RAM avail min GiB | tier override | error |", "|---|---|---|---|---|---|---|---|"]
    for l in s["loads"]:
        L.append("| " + " | ".join(_fmt(x) for x in [l["model_id"], l["ok"], l["t_ready_s"], l["load_vram_max_mib"],
                                                     l["load_data_disk_read_gb"], l["load_mem_avail_min_gib"],
                                                     l["vram_tier_override"], l["error"]]) + " |")
    for name in ("quick_eval", "quick_eval_pilot"):
        q = s.get(name) or {}
        if not q:
            continue
        cats = sorted({c for v in q.values() for c in (v.get("categories") or {})})
        L += ["", f"## {name.replace('_', '-')} (separate from pelican visual quality)", "",
              "| model | total | " + " | ".join(f"{c} pass (cap)" for c in cats) + " | wall s |",
              "|" + "---|" * (len(cats) + 3)]
        for m, v in q.items():
            cc = v.get("categories") or {}
            L.append("| " + " | ".join([m, v.get("total", v.get("status", "-"))] + [
                f"{cc[c]['passed']}/{cc[c]['runs']} ({cc[c]['cap_hits']})" if c in cc else "-" for c in cats]
                + [_fmt(v.get("wall_s"))]) + " |")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    a = ap.parse_args()
    run = ROOT / "runs" / a.run
    recs = load_runs(run)
    rows = [flat(r) for r in recs]
    out = run / "report"
    out.mkdir(exist_ok=True)
    with open(out / "per_run.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    with open(out / "per_run.jsonl", "w") as f:
        for r in rows:
            f.write(json.dumps(r, default=str) + "\n")
    by = defaultdict(list)
    for r in rows:
        by[(r["model_id"], r["phase"], r["requested_effort"])].append(r)
    cells = []
    for (m, ph, e), rs in sorted(by.items()):
        cells.append({"model_id": m, "phase": ph, "effort": e, **summarize(rs)})
    with open(out / "per_model_effort.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(cells[0]))
        w.writeheader()
        for c in cells:
            w.writerow({k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in c.items()})
    loads = [json.loads(l) for l in open(run / "loads.jsonl")] if (run / "loads.jsonl").exists() else []
    load_rows = [{"model_id": l["model_id"], "ok": l.get("ok"), "error": l.get("error"), "t_ready_s": l.get("t_ready_s"),
                  "vram_tier_override": l.get("vram_tier_override"),
                  "load_vram_max_mib": g(l.get("resources"), "vram_used_mib", "max"),
                  "load_data_disk_read_gb": g(l.get("resources"), "data_disk_read_gb"),
                  "load_mem_avail_min_gib": g(l.get("resources"), "mem_available_gib", "min")} for l in loads]
    qe = quick_eval_table(run)
    qep = quick_eval_table(run, "quick-eval-pilot")
    state = json.loads((run / "state.json").read_text()) if (run / "state.json").exists() else {}
    summary = {"run": a.run, "n_records": len(rows), "per_model_effort": cells, "loads": load_rows,
               "quick_eval": qe, "quick_eval_pilot": qep, "model_status": state.get("models")}
    (out / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    (out / "tables.md").write_text(markdown(summary), encoding="utf-8")
    print(json.dumps({"records": len(rows), "cells": len(cells), "loads": len(load_rows)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
