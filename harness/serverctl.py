"""Start/stop one Strata server per model on a dedicated loopback port, as an owned user systemd unit.

- Configs go to $PELICAN_ROOT/configs/<id>.json; yours are only read. EXL3 configs derive from $EXL3_BASE_CONFIG
  (checkpoint paths, port, log and name change); GGUF/Gyro use official Strata with one uniform profile.
- Strata's GUI settings file (<config>.shared-settings.json) must not exist, so it cannot apply.
- Unit `strata-pelican-serve.service` (KillMode=control-group); the port's listener must be in its cgroup before
  any request. Nothing else is ever stopped.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(os.environ.get("PELICAN_ROOT", "pelican-data")).resolve()
CONFIGS = ROOT / "configs"
LOGS = ROOT / "logs"
UNIT = "strata-pelican-serve.service"
HOST, PORT = "127.0.0.1", 18093
BASE_URL = f"http://{HOST}:{PORT}/v1"

# ---- EDIT FOR YOUR MACHINE: engine and checkpoint locations (set the env vars or change the defaults) ----
_E = os.environ.get
FORK_SRC = Path(_E("EXL3_STRATA_SRC", "/path/to/strata-exl3/source"))        # EXL3-capable Strata build
FORK_EXE = Path(_E("EXL3_STRATA_EXE", "/path/to/strata-exl3/build/strata"))
FORK_PY = Path(_E("EXL3_STRATA_PY", "/path/to/strata-exl3/venv/bin/python"))
FORK_USER_CFG = Path(_E("EXL3_BASE_CONFIG", "/path/to/your-exl3-server-config.json"))
OFF_SRC = Path(_E("STRATA_SRC", str(ROOT / "engines/strata-src")))           # official Strata release
OFF_EXE = Path(_E("STRATA_EXE", str(ROOT / "engines/strata-engine/strata")))
OFF_PY = Path(_E("STRATA_PY", "/path/to/strata/venv/bin/python"))
MTP_RT = Path(_E("STRATA_MTP_RT", "/path/to/mtp/rt"))
PACKS = ROOT / "packs"
HUB = Path(_E("HF_HUB_CACHE", str(Path.home() / ".cache/huggingface/hub")))
TD = HUB / "models--turboderp--Qwen3.8-Flash-Next-exl3/snapshots"
SC = Path(_E("SC_EXL3_DIR", "/path/to/sc-exl3-models"))
GYRO = HUB / "models--agentionai--Qwen3.8-Flash-Next-Gyro-GGUF/snapshots/273edc1d2ffae5241fe3cab69568d682dad4c718"
GG = Path(_E("GGUF_DIR", "/path/to/gguf-models"))

# Evaluation order: known-good baseline first, then alternate engines; the largest files last.
MODELS = {
    "sc-exl3-2.05-h8": {"engine": "fork", "dir": SC / "Qwen3.8-Flash-Next-2.05bpw_h8"},
    "gyro-s-tq1_0": {"engine": "official", "pack": PACKS / "gyro-s", "native": GYRO / "Qwen3.8-Flash-Next-Gyro-S-TQ1_0.gguf"},
    "td-exl3-2.18-h6-ng8": {"engine": "fork", "dir": TD / "7a1be2e7e365ffd26ff51bcfe9b34d90f05568f6"},
    "gguf-iq3_xxs": {"engine": "official", "pack": PACKS / "iq3_xxs",
                     "native": GG / "IQ3_XXS/Qwen3.8-Flash-Next-GSQ-RCO-IQ3_XXS-00001-of-00002.gguf",
                     "ple": GG / "IQ3_XXS/Qwen3.8-Flash-Next-GSQ-RCO-IQ3_XXS-00002-of-00002.gguf"},
    "sc-exl3-3.05-h8": {"engine": "fork", "dir": SC / "Qwen3.8-Flash-Next-3.05bpw_h8"},
    "gyro-m-tq2_0": {"engine": "official", "pack": PACKS / "gyro-m", "native": GYRO / "Qwen3.8-Flash-Next-Gyro-M-TQ2_0.gguf"},
    "td-exl3-2.68-h6-ng8": {"engine": "fork", "dir": TD / "d660113325e1eba20e760a9063580139bdbdfc22"},
    "gguf-iq3_s": {"engine": "official", "pack": PACKS / "iq3_s",
                   "native": GG / "IQ3_S/Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S-00001-of-00002.gguf",
                   "ple": GG / "IQ3_S/Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S-00002-of-00002.gguf"},
}
OFFICIAL_PROFILE = ["--expert-cache", "auto", "--prefill", "auto", "--spec", "4", "--spec-min-p", "0.5",
                    "--mtp", str(MTP_RT), "--max-context", "65536", "--kv", "int8"]
# Same-weight policy env for the EXL3 build: no routing changes.
FORK_POLICY_ENV = {"STRATA_EXL3_MISS_SHARE": "0", "STRATA_ROUTE_TAIL_SKIP": "0", "STRATA_ROUTE_RESIDENT": "0",
                   "STRATA_HC_REQ8": "0"}


def sha256_file(p: Path, cache: dict | None = None) -> str | None:
    p = Path(p)
    if not p.exists():
        return None
    key = (str(p.resolve()), p.stat().st_size, p.stat().st_mtime_ns)
    if cache is not None and key in cache:
        return cache[key]
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 22), b""):
            h.update(b)
    if cache is not None:
        cache[key] = h.hexdigest()
    return h.hexdigest()


def build_config(model_id: str, vram_tier: str | None = None) -> tuple[Path, dict, dict]:
    """Write the run config; return (path, config, launch spec)."""
    m = MODELS[model_id]
    CONFIGS.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    log = LOGS / f"server-{model_id}.log"
    if m["engine"] == "fork":
        user = json.loads(FORK_USER_CFG.read_text())
        args = list(user["args"])
        d = Path(m["dir"])
        args[args.index("--exl3") + 1] = str(d)
        args[args.index("--ple-exl3") + 1] = str(d / "ngram_embedding.safetensors")
        if vram_tier is not None:
            args[args.index("--exl3-vram-tier") + 1] = vram_tier
        cache = ROOT / "runtime-cache/fork"
        env = dict(user.get("env") or {})
        env.update(TMPDIR=str(cache / "tmp"), XDG_CACHE_HOME=str(cache / "cache"), CUDA_CACHE_PATH=str(cache / "cache/cuda"),
                   TORCH_EXTENSIONS_DIR=str(cache / "cache/torch"), TRITON_CACHE_DIR=str(cache / "cache/triton"),
                   **FORK_POLICY_ENV)
        for k in ("TMPDIR", "XDG_CACHE_HOME", "CUDA_CACHE_PATH", "TORCH_EXTENSIONS_DIR", "TRITON_CACHE_DIR"):
            Path(env[k]).mkdir(parents=True, exist_ok=True)
        cfg = {**user, "args": args, "model_name": model_id, "log": str(log), "host": HOST, "port": PORT, "env": env,
               "parallel": 1}
        if os.environ.get("EXL3_STRATA_EXE"):          # otherwise the base config's "exe" is used
            cfg["exe"] = str(FORK_EXE)
        spec = {"python": str(FORK_PY), "cwd": str(FORK_SRC), "env": {"PYTHONDONTWRITEBYTECODE": "1", **env},
                "engine": "fork", "exe": cfg["exe"]}
    else:
        args = ["--pack", str(m["pack"]), "--native", str(m["native"])]
        if m.get("ple"):
            args += ["--ple-gguf", str(m["ple"])]
        args += ["--expert-profile", str(OFF_SRC / "data/expert-profile.bin"), *OFFICIAL_PROFILE]
        cfg = {"exe": str(OFF_EXE), "args": args, "cwd": str(OFF_SRC), "tokenizer": str(Path(m["pack"]) / "tokenizer"),
               "model_name": model_id, "log": str(log), "host": HOST, "port": PORT, "parallel": 1,
               "env": {"CUDA_VISIBLE_DEVICES": "0"}}
        spec = {"python": str(OFF_PY), "cwd": str(OFF_SRC),
                "env": {"PYTHONDONTWRITEBYTECODE": "1", "CUDA_VISIBLE_DEVICES": "0",
                        "TMPDIR": str(ROOT / "runtime-cache/official/tmp")},
                "engine": "official", "exe": cfg["exe"]}
        Path(spec["env"]["TMPDIR"]).mkdir(parents=True, exist_ok=True)
    path = CONFIGS / f"{model_id}.json"
    path.write_text(json.dumps(cfg, indent=1))
    return path, cfg, spec


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=kw.pop("timeout", 60), **kw)


def unit_active() -> str:
    return _run(["systemctl", "--user", "is-active", UNIT]).stdout.strip()


def unit_pids() -> set[int]:
    base = Path(f"/sys/fs/cgroup/user.slice/user-{os.getuid()}.slice/user@{os.getuid()}.service/app.slice/{UNIT}")
    try:
        return {int(x) for x in (base / "cgroup.procs").read_text().split()}
    except OSError:
        return set()


def listener_pids(port: int = PORT) -> set[int]:
    out = _run(["ss", "-Hltnp", f"sport = :{port}"]).stdout
    return {int(x) for x in re.findall(r"pid=(\d+)", out)}


def other_strata_units() -> list[str]:
    out = _run(["systemctl", "--user", "list-units", "--no-legend", "--state=active", "strata*"]).stdout
    return [l.split()[0] for l in out.splitlines() if l.strip() and not l.split()[0].startswith("strata-pelican")]


def get_json(path: str, timeout: float = 5.0):
    with urllib.request.urlopen(f"http://{HOST}:{PORT}{path}", timeout=timeout) as r:
        return json.load(r)


def start(model_id: str, vram_tier: str | None = None, ready_timeout: float = 1800) -> dict:
    """Start the server for model_id; block until it serves. Returns a load record (never raises on load failure)."""
    if unit_active() in ("active", "activating", "deactivating"):
        raise RuntimeError(f"{UNIT} is already {unit_active()}: refusing to start a second model")
    if listener_pids():
        raise RuntimeError(f"port {PORT} is in use by pids {listener_pids()}: not ours to stop")
    cfg_path, cfg, spec = build_config(model_id, vram_tier)
    shared = Path(str(cfg_path.with_suffix("")) + ".shared-settings.json")
    if shared.exists():
        raise RuntimeError(f"{shared} exists: shared GUI defaults would apply to unspecified request fields")
    log = Path(cfg["log"])
    log_offset = log.stat().st_size if log.exists() else 0
    cmd = ["systemd-run", "--user", f"--unit={UNIT}", "--collect", "-p", "KillMode=control-group",
           "-p", "TimeoutStopSec=90", f"--working-directory={spec['cwd']}",
           "-p", f"StandardOutput=append:{LOGS / ('stdout-' + model_id + '.log')}",
           "-p", f"StandardError=append:{LOGS / ('stdout-' + model_id + '.log')}"]
    for k, v in spec["env"].items():
        cmd.append(f"--setenv={k}={v}")
    cmd += [spec["python"], "-m", "serve.server", "--engine", "strata", "--config", str(cfg_path),
            "--host", HOST, "--port", str(PORT)]
    if spec["engine"] == "official":
        cmd.append("--offline")            # no remote image fetches or MCP/tool subprocesses (fork predates the flag)
    t_wall = time.time()
    t0 = time.monotonic()
    r = _run(cmd)
    rec = {"model_id": model_id, "config_path": str(cfg_path), "config": cfg, "launch": spec, "cmd": cmd,
           "start_wall": t_wall, "systemd_run_rc": r.returncode, "systemd_run_err": r.stderr.strip()[-500:],
           "vram_tier_override": vram_tier, "log_offset": log_offset}
    if r.returncode != 0:
        rec.update(ok=False, error="systemd-run failed")
        return rec
    t_listen = t_models = None
    err = None
    while time.monotonic() - t0 < ready_timeout:
        state = unit_active()
        if state not in ("active", "activating"):
            err = f"server unit exited ({state}) during load"
            break
        if t_listen is None and listener_pids():
            t_listen = time.monotonic() - t0
        if t_listen is not None:
            try:
                ids = [m["id"] for m in get_json("/v1/models").get("data", [])]
                st = get_json("/v1/status")
                if model_id in ids and _loaded(st):
                    t_models = time.monotonic() - t0
                    rec["status"] = st
                    break
            except (OSError, ValueError):
                pass
        time.sleep(2)
    else:
        err = f"not ready after {ready_timeout:.0f}s"
    rec["t_listen_s"], rec["t_ready_s"] = t_listen, t_models
    if err or t_models is None:
        rec.update(ok=False, error=err or "not ready", log_tail=tail(log, log_offset))
        return rec
    lp, up = listener_pids(), unit_pids()
    if not lp or not lp <= up:
        rec.update(ok=False, error=f"listener pids {lp} not in our unit's cgroup {up}")
        stop()
        return rec
    rec.update(ok=True, listener_pids=sorted(lp), unit_pids=sorted(up))
    try:
        rec["props"] = get_json("/props")
    except (OSError, ValueError) as e:
        rec["props"] = {"error": str(e)}
    try:
        met = get_json("/metrics")
        rec["engine_info"] = met.get("engine")
    except (OSError, ValueError) as e:
        rec["engine_info"] = {"error": str(e)}
    rec["load_log"] = tail(log, log_offset, 400)
    return rec


def _loaded(st: dict) -> bool:
    """/v1/status says the engine is up (field names differ slightly between versions)."""
    s = json.dumps(st).lower()
    if '"loaded": true' in s or '"state": "idle"' in s or '"ready": true' in s:
        return True
    return False


def tail(path: Path, offset: int = 0, lines: int = 80) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read().decode("utf-8", "replace")
    except OSError:
        return ""
    return "\n".join(data.splitlines()[-lines:])


def stop(timeout: float = 120) -> dict:
    t0 = time.monotonic()
    state = unit_active()
    if state in ("inactive", "unknown", "failed", ""):
        return {"stopped": True, "was": state, "s": 0.0}
    _run(["systemctl", "--user", "stop", UNIT], timeout=timeout)
    while time.monotonic() - t0 < timeout:
        if unit_active() not in ("active", "activating", "deactivating") and not listener_pids():
            break
        time.sleep(1)
    _run(["systemctl", "--user", "reset-failed", UNIT])
    return {"stopped": not listener_pids(), "was": state, "s": round(time.monotonic() - t0, 1)}


def wait_idle(timeout: float = 300) -> dict:
    """After a client-side abort, wait until the engine reports idle (it should cancel on disconnect)."""
    t0 = time.monotonic()
    last = None
    while time.monotonic() - t0 < timeout:
        try:
            last = get_json("/metrics")["live"]["state"]
            if last == "idle":
                return {"idle": True, "waited_s": round(time.monotonic() - t0, 1)}
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(1)
    return {"idle": False, "waited_s": round(time.monotonic() - t0, 1), "state": last}


def git_state(src: Path) -> dict:
    head = _run(["git", "-C", str(src), "rev-parse", "HEAD"]).stdout.strip()
    diff = _run(["git", "-C", str(src), "diff", "HEAD", "--", "."], timeout=120).stdout
    untracked = _run(["git", "-C", str(src), "ls-files", "--others", "--exclude-standard", "--", "src", "include",
                      "serve", "tools", "CMakeLists.txt"]).stdout.split()
    return {"head": head, "diff_sha256": hashlib.sha256(diff.encode()).hexdigest(), "diff_bytes": len(diff),
            "modified": _run(["git", "-C", str(src), "diff", "--name-only", "HEAD"]).stdout.split(),
            "untracked_source": {u: sha256_file(src / u) for u in untracked if (src / u).is_file()}}


def _build_info(p: Path):
    """Release metadata shipped next to official engine binaries, if present (recorded as missing otherwise)."""
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError) as exc:
        return {"missing": f"{p}: {type(exc).__name__}"}


def provenance(model_id: str, cfg: dict, spec: dict, cache: dict) -> dict:
    """Non-secret identity of everything that shapes this model's outputs. Engine, front end, template, tokenizer,
    MTP and pack files are hashed; model weight shards are not rehashed (verify them when you download)."""
    src = Path(spec["cwd"])
    tok = Path(cfg["tokenizer"])
    tpl = tok / "chat_template.jinja"
    tpl = tpl if tpl.exists() else src / "serve/chat_template.jinja"
    frontend = (src / "serve/frontend.py").read_text()
    m = re.search(r"EFFORT = \{.*?\}", frontend, re.S)
    eff_ok = bool(m and '"low": "low"' in m.group(0) and '"medium": "medium"' in m.group(0)
                  and '"high": "xhigh"' in m.group(0))
    arg = cfg["args"]
    files = {"engine_exe": spec["exe"], "server_py": str(src / "serve/server.py"), "frontend_py": str(src / "serve/frontend.py"),
             "template": str(tpl), "tokenizer_json": str(tok / "tokenizer.json"), "mtp_dense_txt": str(MTP_RT / "dense.txt"),
             "mtp_draft_vocab": str(MTP_RT / "draft_vocab.bin")}
    for flag in ("--expert-profile", "--exl3-lane-profile"):
        if flag in arg:
            files["expert_profile"] = arg[arg.index(flag) + 1]
    if "--pack" in arg:
        pk = Path(arg[arg.index("--pack") + 1])
        files.update(pack_index=str(pk / "index.txt"), pack_native_experts=str(pk / "native_experts.txt"),
                     pack_conversions=str(pk / "conversions.json"))
    if "--exl3" in arg:
        d = Path(arg[arg.index("--exl3") + 1])
        files.update(exl3_config=str(d / "config.json"), exl3_quant_config=str(d / "quantization_config.json"),
                     exl3_index=str(d / "model.safetensors.index.json"))
    out = {"model_id": model_id, "engine": spec["engine"], "config_sha256": sha256_file(Path(CONFIGS / f"{model_id}.json")),
           "effort_table_ok": eff_ok, "effort_table": m.group(0) if m else None,
           "files": {k: {"path": v, "sha256": sha256_file(Path(v), cache)} for k, v in files.items()},
           "engine_version": _run([spec["exe"], "--version"], timeout=30).stdout.strip()[:200],
           "config_flags": {"reasoning_effort": cfg.get("reasoning_effort"), "reasoning_budget_tokens": cfg.get("reasoning_budget_tokens"),
                            "effort_position": cfg.get("effort_position"), "reasoning_loop_recovery": cfg.get("reasoning_loop_recovery"),
                            "reasoning_close_retry": cfg.get("reasoning_close_retry"), "sampling": cfg.get("sampling")}}
    if spec["engine"] == "fork":
        out["fork_git"] = git_state(src)
    else:
        out["official_git"] = {"head": _run(["git", "-C", str(src), "rev-parse", "HEAD"]).stdout.strip(),
                               "dirty": bool(_run(["git", "-C", str(src), "status", "--porcelain", "--untracked-files=no"]).stdout.strip()),
                               "engine_build": _build_info(Path(spec["exe"]).parent / "BUILD.json")}
    return out
