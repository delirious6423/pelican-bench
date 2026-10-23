#!/usr/bin/env python3
"""Strata quant evaluation orchestrator: pilot and full sweep, one model at a time, on an owned loopback server.

  python3 -I sweep.py pilot --run pilot-1
  python3 -I sweep.py sweep --run sweep-1 --deadline 3600 [--effort-probes]

Phases per model: start server (cold load, timed) -> compat probe -> warm-up (excluded) -> pilot or scored cells ->
quick-eval core tier (sweep) -> stop. Every request is recorded (raw text, timings, usage, correlated engine
metrics, resource window). Model errors, invalid SVGs and cap hits are results: never retried. Only an
infrastructure abort (storage alarm, server crash) may re-run a cell once on resume, with attempt numbers recorded.
Gates before each request: storage health (stop everything), GPU temperature (pause), foreign GPU compute (pause,
then mark). Generated content is never executed; SVG rendering happens later in render.py's sandbox.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import random
import signal
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import monitor as mon_mod  # noqa: E402
import pelican as pel  # noqa: E402
import serverctl as sc  # noqa: E402

qe = pel.qe
ROOT = Path(os.environ.get("PELICAN_ROOT", "pelican-data")).resolve()
SEEDS = (11, 22, 33, 44, 55)
EFFORTS = ("low", "medium", "high")
WARMUP_SEED, PILOT_SEEDS = 7, {"low": 101, "medium": 102, "high": 103}
MAX_TOKENS = 49152  # 32768 truncated high-effort runs in our pilot; set it from your own pilot
QE_CORE = "recall,arith,format,repeat"
QE_PILOT_ITEMS = "recall-01,arith-mul-0,format-sort,repeat-"
HOT_C, COOL_C = 87.0, 80.0
FOREIGN_GPU_MIB = 256


class StorageAlarm(Exception):
    pass


class Runner:
    def __init__(self, a):
        self.a = a
        self.dir = ROOT / "runs" / a.run
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "pelican/raw").mkdir(parents=True, exist_ok=True)
        (self.dir / "pelican/svg").mkdir(parents=True, exist_ok=True)
        self.state_path = self.dir / "state.json"
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {"models": {}, "cells": {}}
        self.events = open(self.dir / "events.log", "a", encoding="utf-8")
        self.mon = mon_mod.Monitor(self.dir / "resources.jsonl", unit=sc.UNIT, hot_c=HOT_C).start()
        self.hash_cache = {}
        self.stopping = False
        signal.signal(signal.SIGTERM, self._sigterm)

    def _sigterm(self, *_):
        self.stopping = True
        self.log("SIGTERM: stopping after the current request; the server will be stopped")
        raise KeyboardInterrupt

    def log(self, msg):
        line = f"{dt.datetime.now().astimezone().isoformat(timespec='seconds')} {msg}"
        print(line, flush=True)
        self.events.write(line + "\n")
        self.events.flush()

    def save_state(self):
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=1))
        tmp.replace(self.state_path)

    def append(self, name, rec):
        with open(self.dir / name, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")

    # ------------------------------------------------------------------ gates
    def gate(self) -> dict:
        """Before a request: storage -> abort; heat -> pause; foreign GPU compute -> pause, then mark."""
        bad = self.mon.check_kernel() + list(self.mon.storage_alarm)
        state = mon_mod.nvme_state()
        if state not in ("live", "n/a") or bad:
            raise StorageAlarm(f"nvme state={state!r}; " + " | ".join(bad[:5]))
        info = {"paused_hot_s": 0.0, "paused_foreign_s": 0.0, "contention": []}
        t0 = time.monotonic()
        if self.mon.hot():
            self.log(f"GPU at {self.mon.last_gpu.get('temp_c')} C: pausing until < {COOL_C} C (max 15 min)")
            while time.monotonic() - t0 < 900 and (self.mon.last_gpu.get("temp_c") or 0) >= COOL_C:
                time.sleep(5)
            info["paused_hot_s"] = round(time.monotonic() - t0, 1)
        t1 = time.monotonic()
        while True:
            ours = sc.unit_pids()
            foreign = [x for x in mon_mod.gpu_compute_apps() if x["pid"] not in ours and (x["mib"] or 0) >= FOREIGN_GPU_MIB]
            if not foreign:
                break
            if time.monotonic() - t1 > 900:
                info["contention"].append({"foreign_gpu": foreign})
                self.log(f"foreign GPU compute still present after 15 min: {foreign}; continuing, run marked")
                break
            if info["paused_foreign_s"] == 0:
                self.log(f"foreign GPU compute present {foreign}: pausing our work (max 15 min)")
            time.sleep(10)
            info["paused_foreign_s"] = round(time.monotonic() - t1, 1)
        return info

    # ------------------------------------------------------------------ one request
    def request(self, model_id, phase, effort, seed, cell_id, deadline, attempt=1, max_tokens=MAX_TOKENS) -> dict:
        g = self.gate()
        body = pel.build_body(model_id, effort, seed, max_tokens)
        t_wall0 = time.time()
        res = qe.stream_chat(sc.BASE_URL, body, timeout=self.a.idle_timeout, deadline=deadline)
        t_wall1 = time.time()
        idle = sc.wait_idle(300) if res.get("abort") else None
        rec = self.record(model_id, phase, effort, seed, cell_id, body, res, t_wall0, t_wall1, deadline, g, attempt)
        rec["post_abort_idle"] = idle
        if sc.unit_active() != "active":
            rec["server_died"] = True
            rec["server_log_tail"] = sc.tail(Path(sc.LOGS / f"server-{model_id}.log"), 0, 60)
        ex = pel.extract_svg(res["answer"])
        rec["svg_status"] = ex["status"]
        rec["svg_n_starts"] = ex["n_svg_starts"]
        rec["svg_flags"] = pel.svg_flags(ex["svg"])
        rec["svg_unsafe"] = sorted(k for k in pel.UNSAFE_KEYS if k in rec["svg_flags"])
        rec["extraction_rule"] = pel.EXTRACTION_RULE_VERSION
        raw_dir = self.dir / "pelican/raw" / model_id
        raw_dir.mkdir(parents=True, exist_ok=True)
        name = f"{cell_id}-a{attempt}"
        full = {**rec, "reasoning": res["reasoning"], "answer": res["answer"]}
        (raw_dir / f"{name}.json").write_text(json.dumps(full, ensure_ascii=False, indent=1, default=str))
        if ex["svg"] is not None:
            sd = self.dir / "pelican/svg" / model_id
            sd.mkdir(parents=True, exist_ok=True)
            suffix = ".svg" if ex["status"] == "complete" else ".unclosed.svg.txt"
            (sd / f"{name}{suffix}").write_text(ex["svg"], encoding="utf-8")
            rec["svg_file"] = str(sd / f"{name}{suffix}")
        rec["raw_file"] = str(raw_dir / f"{name}.json")
        self.append("pelican/runs.jsonl", rec)
        self.log(f"[{model_id}] {phase} {cell_id} a{attempt}: finish={rec['finish_reason']} abort={rec['abort']} "
                 f"tok={rec['completion_tokens']} wall={rec['wall_s']:.0f}s ttft={rec['ttft_s']} "
                 f"decode={rec['engine_decode_tok_s']} svg={rec['svg_status']} unsafe={rec['svg_unsafe']}")
        return rec

    def record(self, model_id, phase, effort, seed, cell_id, body, res, t_wall0, t_wall1, deadline, gate, attempt):
        usage = res.get("usage") or {}
        timings = res.get("timings") or {}
        try:
            met = sc.get_json("/metrics", timeout=10)
            corr = pel.correlate_history(met, t_wall0, t_wall1, usage)
        except (OSError, ValueError) as e:
            corr = {"match": None, "reason": f"/metrics failed: {e}"}
        h = corr.get("entry") or {}
        comp = usage.get("completion_tokens")
        tft, tdone, tfa = res.get("t_first_text"), res.get("t_done"), res.get("t_first_answer")
        gen_span = (tdone - tft) if (tft is not None and tdone) else None
        return {
            "run": self.a.run, "harness": pel.HARNESS_VERSION, "phase": phase, "model_id": model_id,
            "engine": sc.MODELS[model_id]["engine"], "cell_id": cell_id, "attempt": attempt,
            "requested_effort": effort, "resolved_effort": qe.RESOLVED_EFFORT[effort], "seed": seed,
            "sampling": {k: body[k] for k in qe.DEFAULT_SAMPLING}, "max_tokens": body["max_tokens"],
            "budget_sent": body.get("reasoning_budget_tokens"), "deadline_s": deadline,
            "idle_timeout_s": self.a.idle_timeout, "request": body,
            "t_wall_start": t_wall0, "t_wall_end": t_wall1, "ts": dt.datetime.fromtimestamp(t_wall0).astimezone().isoformat(),
            "chunk_id": res.get("chunk_id"), "finish_reason": res.get("finish_reason"),
            "cap_hit": res.get("finish_reason") == "length", "abort": res.get("abort"), "error": res.get("error"),
            "done_seen": res.get("done_seen"), "inline_think": res.get("inline_think"),
            # client clock (monotonic, from request start)
            "t_first_byte_s": res.get("t_first_byte"), "ttft_s": tft, "t_first_reasoning_s": res.get("t_first_reasoning"),
            "t_first_answer_s": tfa, "wall_s": tdone,
            "thinking_phase_s": (tfa - tft) if (tfa is not None and tft is not None) else None,
            "answer_phase_s": (tdone - tfa) if (tfa is not None and tdone) else None,
            # tokens (server usage is the authority for generated = reasoning + answer; never added to anything)
            "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": comp, "total_tokens": usage.get("total_tokens"),
            "cached_tokens": (usage.get("prompt_tokens_details") or {}).get("cached_tokens"),
            "reasoning_chars": len(res.get("reasoning") or ""), "answer_chars": len(res.get("answer") or ""),
            # engine clock (server `timings`, llama.cpp names) and request-correlated /metrics history entry
            "server_timings": timings, "engine_decode_tok_s": timings.get("predicted_per_second"),
            "engine_prefill_tok_s": timings.get("prompt_per_second"), "prefill_ms": timings.get("prompt_ms"),
            "decode_ms": timings.get("predicted_ms"), "prompt_cache_n": timings.get("cache_n"),
            "draft_n": timings.get("draft_n"), "draft_accepted": timings.get("draft_n_accepted"),
            "metrics_match": corr.get("match"), "metrics_reason": corr.get("reason"), "metrics_entry": h or None,
            "history_decode_tok_s": h.get("decode_tok_s"), "engine_generated": h.get("engine_generated"),
            "first_engine_token_s": h.get("first_engine_token_s"), "reasoning_recoveries": h.get("reasoning_recoveries"),
            # client throughput, labelled by denominator (NOT engine decode speed)
            "client_tok_s_after_first_text": round(comp / gen_span, 2) if comp and gen_span else None,
            "client_tok_s_wall": round(comp / tdone, 2) if comp and tdone else None,
            "gate": gate, "resources": self.mon.window(t_wall0, t_wall1),
        }

    # ------------------------------------------------------------------ model lifecycle
    def start_model(self, model_id) -> dict | None:
        ms = self.state["models"].setdefault(model_id, {})
        self.log(f"[{model_id}] starting server ({sc.MODELS[model_id]['engine']})")
        self.gate()
        t_wall = time.time()
        load = sc.start(model_id)
        load["resources"] = self.mon.window(t_wall, time.time())
        if not load.get("ok") and sc.MODELS[model_id]["engine"] == "fork" and \
                any(s in (load.get("log_tail") or "").lower() for s in ("out of memory", "vram", "cuda error", "too large")):
            self.log(f"[{model_id}] load failed with a VRAM-type error at the tuned fixed tier; one retry with auto tier")
            self.append("loads.jsonl", {"phase": self.a.mode, **load})
            sc.stop()
            t_wall = time.time()
            load = sc.start(model_id, vram_tier="auto")
            load["resources"] = self.mon.window(t_wall, time.time())
        self.append("loads.jsonl", {"phase": self.a.mode, **load})
        if not load.get("ok"):
            self.log(f"[{model_id}] LOAD FAILED: {load.get('error')}; marking blocked; log tail saved")
            ms["status"] = "load-failed"
            ms["error"] = load.get("error")
            self.save_state()
            sc.stop()
            return None
        prov = sc.provenance(model_id, load["config"], load["launch"], self.hash_cache)
        prov["load"] = {k: load.get(k) for k in ("t_listen_s", "t_ready_s", "listener_pids", "vram_tier_override")}
        prov["engine_info"] = load.get("engine_info")
        (self.dir / "provenance").mkdir(exist_ok=True)
        (self.dir / "provenance" / f"{model_id}.json").write_text(json.dumps(prov, indent=1, default=str))
        if not prov["effort_table_ok"]:
            self.log(f"[{model_id}] effort table differs from low/medium/high->xhigh: {prov['effort_table']}; blocked")
            ms["status"] = "effort-table-mismatch"
            self.save_state()
            sc.stop()
            return None
        ms["load_s"] = load.get("t_ready_s")
        self.log(f"[{model_id}] ready in {load.get('t_ready_s'):.0f}s; VRAM max {load['resources'].get('vram_used_mib')}")
        return load

    def compat(self, model_id, deadline) -> dict:
        """Short generation proof (excluded): pelican prompt at low effort capped at 512 tokens."""
        return self.request(model_id, "compat", "low", WARMUP_SEED, f"{model_id}-compat", deadline, max_tokens=512)

    def quick_eval(self, model_id, only=None, deadline=1800, out_name="quick-eval"):
        out = self.dir / out_name / model_id
        if (out / "summary.json").exists():
            self.log(f"[{model_id}] quick-eval already complete in {out}")
            return
        out.mkdir(parents=True, exist_ok=True)
        if (out / "runs.jsonl").exists():          # an interrupted earlier attempt: keep it, start the tier afresh
            (out / "runs.jsonl").rename(out / f"runs.partial-{int(time.time())}.jsonl")
            self.log(f"[{model_id}] quick-eval: earlier partial attempt moved aside (re-run from the start)")
        argv = ["--base-url", sc.BASE_URL, "--model", model_id, "--expect-model", model_id, "--label", model_id,
                "--categories", QE_CORE, "--effort", "medium", "--budget", str(self.a.qe_budget),
                "--max-tokens", str(self.a.qe_max_tokens), "--samples", "1", "--seeds", "11", "--no-warmup",
                "--deadline", str(deadline), "--timeout", str(self.a.idle_timeout), "--out", str(out)]
        if only:
            argv += ["--only", only]

        def annotate(rec, res, t_wall0):
            t1 = time.time()
            try:
                corr = pel.correlate_history(sc.get_json("/metrics", timeout=10), t_wall0, t1, res.get("usage"))
            except (OSError, ValueError) as e:
                corr = {"match": None, "reason": str(e)}
            extra = {"model_id": model_id, "run": self.a.run, "metrics_match": corr.get("match"),
                     "metrics_entry": corr.get("entry"), "resources": self.mon.window(t_wall0, t1),
                     "engine_decode_tok_s": (res.get("timings") or {}).get("predicted_per_second")}
            if res.get("abort"):
                extra["post_abort_idle"] = sc.wait_idle(300)
            extra["gate_next"] = self.gate()          # storage/heat/contention before the next item
            return extra
        self.log(f"[{model_id}] quick-eval {only or QE_CORE} (medium, budget {self.a.qe_budget}, max {self.a.qe_max_tokens})")
        qe.main(argv, annotate=annotate)

    def effort_probes(self, model_id, deadline):
        out = self.dir / "effort-probes" / model_id
        if (out / "summary.json").exists():
            return
        out.mkdir(parents=True, exist_ok=True)
        if (out / "runs.jsonl").exists():
            (out / "runs.jsonl").rename(out / f"runs.partial-{int(time.time())}.jsonl")
        argv = ["--base-url", sc.BASE_URL, "--model", model_id, "--expect-model", model_id, "--label", model_id,
                "--categories", "effort", "--efforts", "low,medium,high", "--effort-budget", "0",
                "--effort-max-tokens", str(MAX_TOKENS), "--samples", "1", "--seeds", "11", "--no-warmup",
                "--deadline", str(deadline), "--timeout", str(self.a.idle_timeout), "--out", str(out)]
        self.log(f"[{model_id}] effort probes (5 items x 3 efforts, budget 0 explicit, max {MAX_TOKENS})")
        qe.main(argv, annotate=lambda rec, res, t0: {"resources": self.mon.window(t0, time.time()),
                                                      "gate_next": self.gate(),
                                                      **({"post_abort_idle": sc.wait_idle(300)} if res.get("abort") else {})})

    # ------------------------------------------------------------------ plans
    def cells(self, model_id):
        plan = [(e, s) for e in EFFORTS for s in SEEDS]
        rng = random.Random(int(hashlib.sha256(model_id.encode()).hexdigest()[:8], 16))
        rng.shuffle(plan)                       # interleave efforts so drift affects all cells alike
        return plan

    def run_model(self, model_id, first: bool):
        ms = self.state["models"].setdefault(model_id, {})
        if ms.get("status") in ("done", "load-failed", "effort-table-mismatch") and not self.a.retry_blocked:
            self.log(f"[{model_id}] skipping: {ms['status']}")
            return
        if self.load_model(model_id) is None:
            return
        try:
            dl = self.a.deadline
            c = self.compat(model_id, dl)
            if c.get("error") and not c.get("completion_tokens"):
                ms["status"] = "compat-failed"
                ms["error"] = c.get("error")
                self.log(f"[{model_id}] COMPAT FAILED: {c.get('error')}; marking failed")
                return
            pilot_models = self.a.effort_pilot_models.split(",")
            if self.a.mode == "pilot" and model_id not in pilot_models:
                ms["status"] = "done"                  # compatibility pilot only: load + generation probe
                self.save_state()
                return
            # one excluded warm-up per server load (a restarted server is cold again)
            ms["warmups"] = ms.get("warmups", 0) + 1
            self.request(model_id, "warmup", "low", WARMUP_SEED, f"{model_id}-warmup{ms['warmups']}", dl)
            self.save_state()
            if self.a.mode == "probes":                # separate pass after the core sweep (effort probes only)
                self.effort_probes(model_id, dl)
                ms["status"] = "done"
                self.save_state()
                return
            if self.a.mode == "pilot":
                efforts = EFFORTS if model_id == pilot_models[0] else ("high",)
                for e in efforts:
                    cid = f"{model_id}-pilot-{e}"
                    if cid not in self.state["cells"]:
                        r = self.request(model_id, "pilot", e, PILOT_SEEDS[e], cid, dl)
                        self.state["cells"][cid] = {"status": "done", "abort": r.get("abort")}
                        self.save_state()
                self.quick_eval(model_id, only=QE_PILOT_ITEMS, deadline=self.a.qe_deadline, out_name="quick-eval-pilot")
            else:
                for e, s in self.cells(model_id):
                    cid = f"{model_id}-{e}-s{s}"
                    prev = self.state["cells"].get(cid)
                    if prev and (prev["status"] == "done" or prev.get("attempt", 1) >= 2):
                        continue
                    attempt = (prev or {}).get("attempt", 0) + 1
                    self.state["cells"][cid] = {"status": "started", "attempt": attempt}
                    self.save_state()
                    r = self.request(model_id, "scored", e, s, cid, dl, attempt=attempt)
                    infra = bool(r.get("server_died"))
                    self.state["cells"][cid] = {"status": "infra-abort" if infra else "done", "attempt": attempt,
                                                "abort": r.get("abort")}
                    self.save_state()
                    if infra:
                        self.log(f"[{model_id}] server died during {cid}; restarting once")
                        sc.stop()
                        if self.load_model(model_id) is None:
                            return
                self.quick_eval(model_id, deadline=self.a.qe_deadline)
                if self.a.effort_probes:
                    self.effort_probes(model_id, dl)
            ms["status"] = "done"
            self.save_state()
        finally:
            st = sc.stop()
            self.log(f"[{model_id}] server stopped ({st})")

    def load_model(self, model_id):
        ms = self.state["models"].setdefault(model_id, {})
        ms["loads"] = ms.get("loads", 0) + 1
        if ms["loads"] > 3:
            ms["status"] = "too-many-loads"
            self.save_state()
            return None
        return self.start_model(model_id)

    def main(self):
        models = self.a.models.split(",") if self.a.models else list(sc.MODELS)
        manifest = {"run": self.a.run, "mode": self.a.mode, "started": dt.datetime.now().astimezone().isoformat(),
                    "argv": sys.argv, "models": models, "seeds": SEEDS, "efforts": EFFORTS, "max_tokens": MAX_TOKENS,
                    "deadline_s": self.a.deadline, "idle_timeout_s": self.a.idle_timeout, "prompt": pel.PELICAN_PROMPT,
                    "sampling": qe.DEFAULT_SAMPLING, "resolved_effort": qe.RESOLVED_EFFORT, "budget_sent": 0,
                    "extraction_rule": pel.EXTRACTION_RULE_VERSION, "harness": pel.HARNESS_VERSION,
                    "qe": {"categories": QE_CORE, "effort": "medium", "budget": self.a.qe_budget,
                           "max_tokens": self.a.qe_max_tokens, "deadline_s": self.a.qe_deadline, "seed": 11},
                    "warmup": {"effort": "low", "seed": WARMUP_SEED, "excluded": True},
                    "gates": {"hot_c": HOT_C, "cool_c": COOL_C, "foreign_gpu_mib": FOREIGN_GPU_MIB},
                    "harness_files": {p.name: sc.sha256_file(p) for p in sorted(HERE.glob("*.py"))} |
                    {"quick_eval.py": sc.sha256_file(HERE.parent / "quick-eval/quick_eval.py"),
                     "items.jsonl": sc.sha256_file(HERE.parent / "quick-eval/items.jsonl")},
                    "note": self.a.note}
        self.append("manifest.jsonl", manifest)
        others = sc.other_strata_units()
        if others:
            self.log(f"other Strata units are active ({others}); not touching them; aborting to avoid GPU contention")
            return 2
        if sc.unit_active() in ("active", "activating"):
            self.log("stale strata-pelican-serve unit from an earlier run of ours: stopping it")
            sc.stop()
        rc = 0
        try:
            for i, m in enumerate(models):
                if self.stopping:
                    break
                try:
                    self.run_model(m, first=(i == 0))
                except StorageAlarm:
                    raise
                except KeyboardInterrupt:
                    raise
                except Exception as e:  # noqa: BLE001 - one model's harness error must not stop the others
                    self.log(f"[{m}] HARNESS ERROR {type(e).__name__}: {e}\n{traceback.format_exc()}")
                    self.state["models"].setdefault(m, {})["status"] = f"harness-error: {e}"
                    self.save_state()
                    rc = 1
        except StorageAlarm as e:
            self.log(f"STORAGE ALARM: {e}. Stopping all disk-intensive work (our server stopped). No repair attempted.")
            (self.dir / "STORAGE-ALARM.txt").write_text(str(e))
            rc = 3
        except KeyboardInterrupt:
            self.log("interrupted")
            rc = 130
        finally:
            sc.stop()
            self.mon.stop()
            self.log(f"run {self.a.run} finished rc={rc}")
            (self.dir / "FINISHED").write_text(f"rc={rc} {dt.datetime.now().astimezone().isoformat()}\n")
        return rc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("pilot", "sweep", "probes"))
    ap.add_argument("--run", required=True)
    ap.add_argument("--models", help="comma list (default: all, in serverctl.MODELS order)")
    ap.add_argument("--deadline", type=float, default=3600, help="per pelican request total wall limit, s")
    ap.add_argument("--idle-timeout", type=float, default=600)
    ap.add_argument("--qe-budget", type=int, default=1536)
    ap.add_argument("--qe-max-tokens", type=int, default=2560)
    ap.add_argument("--qe-deadline", type=float, default=900)
    ap.add_argument("--effort-probes", action="store_true")
    ap.add_argument("--effort-pilot-models", default="sc-exl3-2.05-h8,gyro-s-tq1_0",
                    help="pilot: these get warm-up + effort pilot (first: low/medium/high, others: high); the rest compat only")
    ap.add_argument("--retry-blocked", action="store_true")
    ap.add_argument("--note", default="")
    a = ap.parse_args(argv)
    return Runner(a).main()


if __name__ == "__main__":
    sys.exit(main())
