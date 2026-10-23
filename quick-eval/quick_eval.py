#!/usr/bin/env python3
"""Quick limit-finding eval for local OpenAI-compatible LLM servers (Strata).

Design: every item is capped (reasoning budget + max_tokens) so one turn stays short at 20-50 tok/s,
every item is scored by code, reasoning is stripped before scoring, and raw request/response/timing
records are kept so nothing is graded by eye. Standard library only.

Usage (see README.md):
    python3 -I quick_eval.py --list
    python3 -I quick_eval.py --categories recall,arith,format,repeat --label 2.05bpw
    python3 -I quick_eval.py --categories context --context-sizes 8000,16000,32000
    python3 -I quick_eval.py --categories code --run-code      # executes model code in a limited subprocess
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import re
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_OUT_ROOT = Path(os.environ.get("PELICAN_ROOT", "pelican-data")) / "quick-eval"
DEFAULT_PORTS = (8080, 8093)
ALL_CATEGORIES = ("recall", "arith", "format", "repeat", "tool", "zhtw", "effort", "context", "code")

# --------------------------------------------------------------------------- items

def load_static_items() -> list[dict]:
    items = []
    with open(HERE / "items.jsonl", encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"items.jsonl line {n}: {exc}") from exc
            for key in ("id", "category", "prompt", "check"):
                if key not in item:
                    raise SystemExit(f"items.jsonl line {n}: missing {key!r}")
            items.append(item)
    return items


def _days_between(a: dt.date, b: dt.date) -> int:
    return abs((b - a).days)


def synth_arith(seed: int = 20261023) -> list[dict]:
    """Deterministic arithmetic items; the expected values are computed, never typed by hand."""
    rng = random.Random(seed)
    items = []
    suffix = " Reply with only the final number, no working."
    for i in range(8):
        a, b = rng.randint(1000, 9999), rng.randint(1000, 9999)
        items.append({"id": f"arith-mul-{i}", "category": "arith",
                      "prompt": f"Compute {a} × {b}.{suffix}",
                      "check": {"type": "last_int", "expected": a * b}})
    for i in range(4):
        nums = [rng.randint(100000, 999999) for _ in range(6)]
        items.append({"id": f"arith-sum-{i}", "category": "arith",
                      "prompt": "Add these numbers: " + ", ".join(map(str, nums)) + "." + suffix,
                      "check": {"type": "last_int", "expected": sum(nums)}})
    for i in range(4):
        a, m = rng.randint(10**7, 10**9), rng.randint(97, 997)
        items.append({"id": f"arith-mod-{i}", "category": "arith",
                      "prompt": f"What is {a} mod {m}?{suffix}",
                      "check": {"type": "last_int", "expected": a % m}})
    for i in range(4):
        d1 = dt.date(rng.randint(1990, 2030), rng.randint(1, 12), rng.randint(1, 28))
        d2 = d1 + dt.timedelta(days=rng.randint(40, 4000))
        items.append({"id": f"arith-days-{i}", "category": "arith",
                      "prompt": f"How many days are there from {d1.isoformat()} to {d2.isoformat()}?{suffix}",
                      "check": {"type": "last_int", "expected": _days_between(d1, d2)}})
    return items


_FILLER_WORDS = ("harbour lantern meadow copper quiet river basket window marble thunder orchard pencil "
                 "velvet canyon ribbon saddle pepper glacier tunnel feather anchor cabin violin mirror "
                 "granite shepherd compass kettle lagoon walnut ladder ember parcel sparrow timber").split()


def synth_context(sizes: list[int], seed: int = 7) -> list[dict]:
    """Multi-needle retrieval at roughly `size` prompt tokens (≈0.75 words per token for this filler)."""
    items = []
    for size in sizes:
        rng = random.Random(seed + size)
        words = int(size * 0.72)
        body = [rng.choice(_FILLER_WORDS) for _ in range(words)]
        codes = [rng.randint(10000, 99999) for _ in range(3)]
        needles = [f" The access code for the blue vault is {codes[0]}. ",
                   f" The access code for the green vault is {codes[1]}. ",
                   f" The access code for the red vault is {codes[2]}. "]
        for frac, needle in zip((0.10, 0.50, 0.90), needles):
            body.insert(int(len(body) * frac), needle)
        text = " ".join(body)
        items.append({"id": f"context-{size}", "category": "context",
                      "prompt": ("Below is a long document. After it, answer the question.\n\n" + text +
                                 "\n\nQuestion: list the access codes for the blue, green and red vaults, "
                                 "in that order, as three numbers separated by commas. Reply with only the numbers."),
                      "check": {"type": "contains_all", "expected": [str(c) for c in codes]}})
    return items


# --------------------------------------------------------------------------- checkers

_INT_RE = re.compile(r"-?\d[\d,]*")
# Frequent characters that exist only in simplified Chinese (their traditional forms differ).
_SIMPLIFIED = ("国发时电计这个为会对说经门车马书长问间东风广开关业产农动员维变际体来们过还进从样现实点学后"
               "应该种与资质医务议论设备网络数据语级环镜记忆机构华台湾银币预约险药验检测术"
               "并总统选举经济贸协调导领导权让认识亲观觉听觉视觉报纸书籍读写阅页关键词汇处于")


def _last_int(text: str) -> int | None:
    hits = _INT_RE.findall(text)
    if not hits:
        return None
    try:
        return int(hits[-1].replace(",", ""))
    except ValueError:
        return None


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def _has_term(text: str, term: str) -> bool:
    """Whole-term match on normalized text: an ASCII letter/digit may not touch the term's edges, so "w" does not match
    "wolfram" and "42" does not match "142". Alphabetic terms of 4+ letters may take a plural/possessive ending
    ("webers"). CJK and other non-ASCII edges stay substring matches (no word spaces to anchor on)."""
    t = _norm(term)
    if not t:
        return False
    pre = r"(?<![a-z0-9])" if re.match(r"[a-z0-9]", t) else ""
    post = ""
    if re.search(r"[a-z0-9]$", t):
        post = (r"(?:s|es|'s)?" if re.fullmatch(r"[a-z][a-z .'-]{3,}", t) else "") + r"(?![a-z0-9])"
    return re.search(pre + re.escape(t) + post, _norm(text)) is not None


def check(item: dict, answer: str, response: dict) -> tuple[bool, str]:
    """Return (passed, detail). `answer` is the visible answer with reasoning removed."""
    c = item["check"]
    t = c["type"]
    a = answer.strip()
    if t == "last_int":
        got = _last_int(a)
        return got == c["expected"], f"expected {c['expected']}, got {got}"
    if t == "contains_any":
        ok = any(_has_term(a, x) for x in c["expected"])
        return ok, f"expected one of {c['expected']}"
    if t == "contains_all":
        missing = [x for x in c["expected"] if not _has_term(a, x)]
        return not missing, f"missing {missing}" if missing else "all present"
    if t == "exact":
        return _norm(a) == _norm(c["expected"]), f"expected {c['expected']!r}"
    if t == "regex":
        return re.fullmatch(c["pattern"], a, re.S) is not None, f"pattern {c['pattern']}"
    if t == "word_count":
        n = len(re.findall(r"\b[\w'-]+\b", a))
        return n == c["expected"], f"expected {c['expected']} words, got {n}"
    if t == "no_char":
        bad = [ch for ch in c["chars"] if ch in a.lower()]
        if len(a) < 30:
            return False, "answer too short to count"
        return not bad, f"forbidden chars present: {bad}" if bad else "ok"
    if t == "lowercase":
        return a == a.lower() and len(a) > 20, "must be all lowercase and non-trivial"
    if t == "json_schema":
        try:
            obj = json.loads(a)
        except ValueError:
            m = re.search(r"\{.*\}", a, re.S)
            if not m:
                return False, "no JSON object found"
            try:
                obj = json.loads(m.group(0))
            except ValueError:
                return False, "invalid JSON"
            if c.get("strict", True):
                return False, "JSON wrapped in extra text"
        return _json_matches(obj, c["schema"])
    if t == "unique_lines":
        lines = [_norm(l.lstrip("-*0123456789. )")) for l in a.splitlines() if l.strip()]
        n, u = len(lines), len(set(lines))
        ok = n >= c["min"] and u == n
        return ok, f"{n} lines, {u} unique, need >= {c['min']} and no duplicates"
    if t == "numbered_list":
        n = len(re.findall(r"^\s*\d+[.)]\s+\S", a, re.M))
        return n == c["expected"], f"expected {c['expected']} numbered items, got {n}"
    if t == "csv":
        rows = [r for r in a.splitlines() if r.strip()]
        widths = [len(r.split(",")) for r in rows]
        ok = len(rows) == c["rows"] and all(w == c["cols"] for w in widths)
        return ok, f"{len(rows)} rows, widths {widths}"
    if t == "zhtw":
        n = a
        missing = [x for x in c["required_any"] if not any(alt in n for alt in x)]
        simp = sorted({ch for ch in n if ch in _SIMPLIFIED})
        ok = not missing and not simp
        return ok, f"missing terms {missing}; simplified chars {simp}"
    if t == "tool_call":
        calls = (response.get("tool_calls") or [])
        if not calls:
            return False, "no tool call emitted"
        call = calls[0]
        try:
            args = json.loads(call["function"]["arguments"]) if isinstance(call["function"]["arguments"], str) \
                else call["function"]["arguments"]
        except (ValueError, KeyError, TypeError):
            return False, "tool arguments not valid JSON"
        if call["function"].get("name") != c["name"]:
            return False, f"wrong function {call['function'].get('name')!r}"
        diffs = {k: (args.get(k), v) for k, v in c["args"].items() if not _loose_eq(args.get(k), v)}
        extra = set(args) - set(c["args"])
        return not diffs and not extra, f"arg diffs {diffs}, extra {sorted(extra)}"
    if t == "code":
        return False, "code items need --run-code"
    return False, f"unknown check type {t}"


def _loose_eq(a, b) -> bool:
    if isinstance(a, str) and isinstance(b, str):
        return _norm(a) == _norm(b)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) < 1e-9
    return a == b


def _json_matches(obj, schema) -> tuple[bool, str]:
    """Tiny schema: {"key": "str"|"int"|"float"|"bool"|["str", n]|{...}}."""
    if not isinstance(obj, dict):
        return False, "top level is not an object"
    if set(obj) != set(schema):
        return False, f"keys {sorted(obj)} != {sorted(schema)}"
    for k, want in schema.items():
        v = obj[k]
        if isinstance(want, dict):
            ok, why = _json_matches(v, want)
            if not ok:
                return False, f"{k}: {why}"
        elif isinstance(want, list):
            kind, n = want
            if not isinstance(v, list) or len(v) != n or not all(_is_kind(x, kind) for x in v):
                return False, f"{k}: expected list of {n} {kind}"
        elif not _is_kind(v, want):
            return False, f"{k}: expected {want}, got {type(v).__name__}"
    return True, "schema ok"


def _is_kind(v, kind: str) -> bool:
    return {"str": isinstance(v, str), "int": isinstance(v, int) and not isinstance(v, bool),
            "float": isinstance(v, (int, float)) and not isinstance(v, bool),
            "bool": isinstance(v, bool)}.get(kind, False)


_FENCE_RE = re.compile(r"```(?:python)?\s*\n(.*?)```", re.S)


def run_code_check(item: dict, answer: str, timeout: float = 10.0) -> tuple[bool, str]:
    """Execute model code plus the item's asserts in a limited, isolated subprocess.
    Only called with --run-code: this runs model-generated code on the host."""
    m = _FENCE_RE.search(answer)
    code = m.group(1) if m else answer
    tests = item["check"]["tests"]
    src = code + "\n\n" + "\n".join(tests) + "\nprint('__QE_OK__')\n"

    def limits():
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (768 << 20, 768 << 20))
        resource.setrlimit(resource.RLIMIT_CPU, (int(timeout), int(timeout)))
        resource.setrlimit(resource.RLIMIT_FSIZE, (1 << 20, 1 << 20))
        resource.setrlimit(resource.RLIMIT_NPROC, (16, 16))

    with tempfile.TemporaryDirectory(prefix="qe-code-") as td:
        path = Path(td) / "cand.py"
        path.write_text(src, encoding="utf-8")
        try:
            r = subprocess.run([sys.executable, "-I", "-S", str(path)], cwd=td, capture_output=True, text=True,
                               timeout=timeout, env={"PATH": "/usr/bin:/bin", "HOME": td}, preexec_fn=limits)
        except subprocess.TimeoutExpired:
            return False, "timeout"
    ok = r.returncode == 0 and "__QE_OK__" in r.stdout
    tail = (r.stderr or r.stdout).strip().splitlines()[-1:] or [""]
    return ok, "tests passed" if ok else f"exit {r.returncode}: {tail[0][:200]}"


# --------------------------------------------------------------------------- client

class StreamResult(dict):
    pass


def discover(base_url: str | None, timeout: float = 3.0) -> tuple[str, str]:
    """Return (base_url, model id). Tries the default ports when no base_url is given."""
    candidates = [base_url] if base_url else [f"http://127.0.0.1:{p}/v1" for p in DEFAULT_PORTS]
    errors = []
    for url in candidates:
        try:
            with urllib.request.urlopen(url.rstrip("/") + "/models", timeout=timeout) as resp:
                data = json.load(resp)
            models = [m["id"] for m in data.get("data", [])]
            if models:
                return url.rstrip("/"), models[0]
            errors.append(f"{url}: no models")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            errors.append(f"{url}: {exc}")
    raise SystemExit("no server found: " + "; ".join(errors))


def split_inline_think(text: str) -> tuple[str, str, bool]:
    """(reasoning, answer, had_inline) for content that carries <think> blocks inline. An unclosed block (a truncated
    reply) is all reasoning, so a cut-off thought is never scored as an answer."""
    if "<think>" not in text and "</think>" not in text:
        return "", text, False
    reasoning = "".join(re.findall(r"<think>(.*?)</think>", text, re.S))
    answer = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    if "<think>" in answer:                      # unclosed: everything after it is thought
        head, _, tail = answer.partition("<think>")
        reasoning += tail
        answer = head
    elif "</think>" in answer:                   # template opened the block in the prompt: text before is thought
        head, _, tail = answer.partition("</think>")
        reasoning = head + reasoning
        answer = tail
    return reasoning, answer.strip(), True


class _InlineThinkClock:
    """Incremental first-visible-answer detection for content that may open with an inline <think> block."""

    def __init__(self):
        self.raw, self.state, self.scan = "", "unknown", 0

    def feed(self, text: str) -> bool:
        """Add content; True once visible answer text (outside any think block) has appeared."""
        self.raw += text
        if self.state == "answer":
            return True
        if self.state == "unknown":
            s = self.raw.lstrip()
            if not s or (len(s) < 7 and "<think>".startswith(s)):
                return False                          # nothing yet, or an ambiguous '<thi' prefix
            if s.startswith("<think>"):
                self.state, self.scan = "thinking", self.raw.index("<think>") + 7
            else:
                self.state = "answer"
                return True
        if self.state == "thinking":
            i = self.raw.find("</think>", max(self.scan - 8, 0))
            if i < 0:
                self.scan = len(self.raw)
                return False
            self.state, self.scan = "after", i + 8
        if self.state == "after" and self.raw[self.scan:].strip():
            self.state = "answer"
            return True
        return False


def stream_chat(base_url: str, body: dict, timeout: float, deadline: float | None = None) -> StreamResult:
    """POST a streaming chat completion; return text, reasoning, timings (monotonic) and server usage.

    `timeout` is the socket idle timeout; `deadline` (seconds from the request start) is a real total wall limit,
    enforced by a watchdog that shuts the socket down. Times (seconds from request start, monotonic clock):
    t_first_byte (any bytes, incl. role-only/keep-alive), t_first_text (first non-empty reasoning or content, inline
    think included), t_first_reasoning, t_first_answer (first visible answer outside thinking), t_done.
    A stream that ends without finish_reason and [DONE] is reported as truncated, never as a normal completion."""
    import socket
    import threading
    req = urllib.request.Request(base_url + "/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Accept": "text/event-stream"})
    out = StreamResult(answer="", reasoning="", tool_calls=[], finish_reason=None, usage=None, timings=None,
                       error=None, abort=None, t_first_reasoning=None, t_first_answer=None, t_first_text=None,
                       t_done=None, t_first_byte=None, done_seen=False, chunk_id=None, n_events=0, inline_think=False)
    t0 = time.monotonic()
    tool_acc: dict[int, dict] = {}
    clock = _InlineThinkClock()
    content_raw = []
    fired = threading.Event()
    watchdog = None
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if deadline is not None:
                sock = getattr(getattr(getattr(resp, "fp", None), "raw", None), "_sock", None)

                def _kill():
                    fired.set()
                    try:
                        if sock is not None:
                            sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                remaining = deadline - (time.monotonic() - t0)
                watchdog = threading.Timer(max(remaining, 0.0), _kill)
                watchdog.daemon = True
                watchdog.start()
            for raw in resp:
                now = time.monotonic() - t0
                if out["t_first_byte"] is None:
                    out["t_first_byte"] = now
                if fired.is_set() or (deadline is not None and now > deadline):
                    fired.set()
                    break
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    out["done_seen"] = True
                    break
                try:
                    ev = json.loads(payload)
                except ValueError:
                    out["malformed_events"] = out.get("malformed_events", 0) + 1
                    continue
                out["n_events"] += 1
                if out["chunk_id"] is None and ev.get("id"):
                    out["chunk_id"] = ev["id"]
                if ev.get("error"):
                    out["error"] = str(ev["error"])
                    continue
                if ev.get("usage"):
                    out["usage"] = ev["usage"]
                if ev.get("timings"):
                    out["timings"] = ev["timings"]
                for ch in ev.get("choices", []):
                    d = ch.get("delta") or {}
                    r = d.get("reasoning_content")
                    if r:
                        if out["t_first_reasoning"] is None:
                            out["t_first_reasoning"] = now
                        if out["t_first_text"] is None:
                            out["t_first_text"] = now
                        out["reasoning"] += r
                    c = d.get("content")
                    if c:
                        if out["t_first_text"] is None and c.strip():
                            out["t_first_text"] = now
                        content_raw.append(c)
                        if clock.feed(c) and out["t_first_answer"] is None:
                            out["t_first_answer"] = now
                    for tc in d.get("tool_calls") or []:
                        slot = tool_acc.setdefault(tc.get("index", 0), {"id": tc.get("id"), "type": "function",
                                                                        "function": {"name": "", "arguments": ""}})
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["function"]["name"] = fn["name"]
                        if fn.get("arguments"):
                            slot["function"]["arguments"] += fn["arguments"]
                        if out["t_first_answer"] is None:
                            out["t_first_answer"] = now
                        if out["t_first_text"] is None:
                            out["t_first_text"] = now
                    if ch.get("finish_reason"):
                        out["finish_reason"] = ch["finish_reason"]
    except urllib.error.HTTPError as exc:
        out["error"] = f"HTTP {exc.code}: {exc.read(2000).decode('utf-8', 'replace')}"
        out["abort"] = "http"
    except (urllib.error.URLError, OSError, TimeoutError, ValueError) as exc:
        if not fired.is_set():
            out["error"] = f"{type(exc).__name__}: {exc}"
            out["abort"] = "idle-timeout" if isinstance(exc, (TimeoutError, socket.timeout)) else "connection"
    finally:
        if watchdog is not None:
            watchdog.cancel()
    out["t_done"] = time.monotonic() - t0
    if fired.is_set():
        out["abort"] = "deadline"
        out["error"] = f"deadline: stopped after {deadline:.0f}s wall (client closed the stream)"
    elif out["error"] is None and out["finish_reason"] is None:
        out["abort"] = "truncated"
        out["error"] = "stream ended without finish_reason" + ("" if out["done_seen"] else " or [DONE]")
    out["tool_calls"] = [tool_acc[k] for k in sorted(tool_acc)]
    out["answer"] = "".join(content_raw)
    # Some servers leave <think> blocks inline instead of reasoning_content; strip them before scoring.
    think, answer, inline = split_inline_think(out["answer"])
    if inline:
        out["reasoning"] += think
        out["answer"] = answer
        out["inline_think"] = True
    return out


# --------------------------------------------------------------------------- runner

# Qwen3.8-Flash-Next thinking-mode sampling (model card; Strata's MODEL_CARD_SAMPLING["qwen4exp"]["thinking"]). Every key
# is sent explicitly so neither the GUI's shared Chat settings nor a run config's `sampling` block can change a cell.
# min_p 0, penalties 0/1 are the engine's "off" values.
DEFAULT_SAMPLING = {"temperature": 1.0, "top_p": 0.95, "top_k": 20, "min_p": 0.0, "presence_penalty": 0.0,
                    "frequency_penalty": 0.0, "repetition_penalty": 1.0}
# What both Strata front ends (serve/frontend.py EFFORT, official 0.2.3 and the EXL3 fork) send to the template:
# "high" is the template's "xhigh". The pelican harness re-checks this table against each engine's source at launch.
RESOLVED_EFFORT = {"low": "low", "medium": "medium", "high": "xhigh", "xhigh": "xhigh"}


def build_body(model: str, item: dict, effort: str, budget: int | None, max_tokens: int, seed: int,
               temperature: float | None = None, sampling: dict | None = None) -> dict:
    """One chat request. `budget` is always transmitted when not None: 0 means "no thinking budget" and must be sent so
    it overrides a budget the server config may carry (an omitted field inherits the config's). Seeds must be > 0:
    Strata forwards only positive seeds."""
    if seed is None or seed <= 0:
        raise ValueError(f"seed must be a positive integer (Strata ignores 0 and negatives), got {seed!r}")
    s = dict(DEFAULT_SAMPLING if sampling is None else sampling)
    if temperature is not None:
        s["temperature"] = temperature
    body = {"model": model, "stream": True, "max_tokens": max_tokens, "seed": seed, **s,
            "reasoning_effort": effort,
            "messages": [{"role": "user", "content": item["prompt"]}]}
    if item.get("system"):
        body["messages"].insert(0, {"role": "system", "content": item["system"]})
    if budget is not None:
        if budget < 0:
            raise ValueError("budget must be >= 0 (0 = no thinking budget)")
        body["reasoning_budget_tokens"] = int(budget)
    if item.get("tools"):
        body["tools"] = item["tools"]
        body["tool_choice"] = "auto"
    if item.get("check", {}).get("type") == "json_schema":
        body["response_format"] = {"type": "json_object"} if item["check"].get("json_mode") else None
        if body["response_format"] is None:
            del body["response_format"]
    return body


def run(args, annotate=None) -> int:
    """`annotate(record, stream_result, wall_start)` may add fields to each record (the pelican harness uses it for
    request-correlated engine metrics and resource samples)."""
    base_url, served = discover(args.base_url)
    model = args.model or served
    if args.expect_model and served != args.expect_model:
        raise SystemExit(f"server at {base_url} serves {served!r}, expected {args.expect_model!r}")
    cats = args.categories.split(",") if args.categories else list(ALL_CATEGORIES)
    unknown = set(cats) - set(ALL_CATEGORIES)
    if unknown:
        raise SystemExit(f"unknown categories {sorted(unknown)}; choose from {ALL_CATEGORIES}")
    if "code" in cats and not args.run_code:
        print("note: 'code' items execute model output; skipping them without --run-code", file=sys.stderr)
        cats = [c for c in cats if c != "code"]

    items = [i for i in load_static_items() if i["category"] in cats]
    if "arith" in cats:
        items += synth_arith()
    if "context" in cats:
        items += synth_context([int(s) for s in args.context_sizes.split(",")])
    if args.only:
        pats = args.only.split(",")
        items = [i for i in items if any(p in i["id"] for p in pats)]
    if not items:
        raise SystemExit("no items selected")

    seeds = [int(s) for s in args.seeds.split(",")][: args.samples]
    sampling = {**DEFAULT_SAMPLING, "temperature": args.temperature, "top_p": args.top_p, "top_k": args.top_k,
                "min_p": args.min_p}
    efforts = args.efforts.split(",")
    stamp = dt.datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")
    label = args.label or re.sub(r"[^A-Za-z0-9._-]+", "_", model)[:60]
    out_dir = Path(args.out) if args.out else DEFAULT_OUT_ROOT / f"{stamp}-{label}"
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / "runs.jsonl"
    manifest = {"started": stamp, "base_url": base_url, "served_model": served, "model": model, "label": label,
                "efforts": efforts, "seeds": seeds, "budget": args.budget, "effort_budget": args.effort_budget,
                "max_tokens": args.max_tokens, "effort_max_tokens": args.effort_max_tokens, "deadline_s": args.deadline,
                "sampling": sampling, "resolved_effort_map": RESOLVED_EFFORT,
                "temperature": args.temperature, "categories": cats, "n_items": len(items),
                "context_sizes": args.context_sizes, "run_code": args.run_code, "argv": sys.argv}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    # one excluded warm-up so model load / cache effects don't land on the first scored item
    if not args.no_warmup:
        w = stream_chat(base_url, build_body(model, {"prompt": "Say OK.", "check": {"type": "exact"}},
                                             "low", 64, 256, seeds[0], sampling=sampling), args.timeout,
                        deadline=args.deadline)
        manifest["warmup"] = {"wall_s": w["t_done"], "error": w["error"], "usage": w["usage"]}
        (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
        if w["error"]:
            print(f"warm-up error: {w['error']}", file=sys.stderr)

    plan = []
    for item in items:
        item_efforts = efforts if item["category"] == "effort" else [args.effort]
        for eff in item_efforts:
            for seed in seeds:
                plan.append((item, eff, seed))
    rng = random.Random(seeds[0])
    rng.shuffle(plan)  # interleave categories/efforts so drift in the server affects all cells alike
    print(f"{model} @ {base_url}: {len(plan)} runs -> {out_dir}", file=sys.stderr)

    records = []
    t_start = time.monotonic()
    with open(raw_path, "a", encoding="utf-8") as raw:
        for n, (item, eff, seed) in enumerate(plan, 1):
            # Native effort comparisons must not be clipped by the short-tier thinking budget: effort items get their own
            # (default 0 = none, sent explicitly) and their own completion ceiling.
            is_effort = item["category"] == "effort"
            body = build_body(model, item, eff, args.effort_budget if is_effort else args.budget,
                              args.effort_max_tokens if is_effort else args.max_tokens, seed, sampling=sampling)
            t_wall0 = time.time()
            res = stream_chat(base_url, body, args.timeout, deadline=args.deadline)
            if res["error"]:
                passed, detail = False, f"error: {res['error'][:200]}"
            elif item["check"]["type"] == "code":
                passed, detail = run_code_check(item, res["answer"])
            else:
                passed, detail = check(item, res["answer"], res)
            usage = res["usage"] or {}
            comp = usage.get("completion_tokens")
            rec = {"id": item["id"], "category": item["category"], "effort": eff, "seed": seed,
                   "requested_effort": eff, "resolved_effort": RESOLVED_EFFORT.get(eff),
                   "budget_sent": body.get("reasoning_budget_tokens"), "max_tokens": body["max_tokens"],
                   "passed": passed, "detail": detail, "finish_reason": res["finish_reason"],
                   "cap_hit": res["finish_reason"] == "length", "abort": res.get("abort"),
                   "done_seen": res.get("done_seen"), "chunk_id": res.get("chunk_id"),
                   "wall_s": round(res["t_done"], 3), "t_first_byte_s": res["t_first_byte"],
                   "ttft_s": res.get("t_first_text"),
                   "t_first_reasoning_s": res["t_first_reasoning"],
                   "t_first_answer_s": res["t_first_answer"],
                   "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": comp,
                   "reasoning_chars": len(res["reasoning"]), "answer_chars": len(res["answer"]),
                   "client_tok_per_s_total": (round(comp / res["t_done"], 2) if comp and res["t_done"] else None),
                   "server_timings": res["timings"], "usage": usage, "inline_think": res.get("inline_think", False),
                   "error": res["error"], "request": body, "reasoning": res["reasoning"], "answer": res["answer"],
                   "tool_calls": res["tool_calls"], "ts": dt.datetime.now().astimezone().isoformat(),
                   "t_wall_start": t_wall0}
            if annotate is not None:
                rec.update(annotate(rec, res, t_wall0) or {})
            raw.write(json.dumps(rec, ensure_ascii=False) + "\n")
            raw.flush()
            records.append(rec)
            mark = "PASS" if passed else "FAIL"
            print(f"[{n:3}/{len(plan)}] {mark} {item['id']:<22} {eff:<6} seed={seed} {rec['wall_s']:6.1f}s "
                  f"tok={comp} cap={'Y' if rec['cap_hit'] else 'n'} {detail[:70]}", file=sys.stderr)
            if args.stop_after and time.monotonic() - t_start > args.stop_after:
                print("stop-after reached; remaining runs skipped (recorded in summary)", file=sys.stderr)
                manifest["skipped_runs"] = len(plan) - n
                break

    summary = summarize(records, manifest)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    md = render_markdown(summary)
    (out_dir / "summary.md").write_text(md)
    print(md)
    return 0


def _med(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 2) if xs else None


def summarize(records: list[dict], manifest: dict) -> dict:
    by_cat: dict[str, list[dict]] = {}
    for r in records:
        by_cat.setdefault(r["category"], []).append(r)
    cats = {}
    for cat, rs in sorted(by_cat.items()):
        cats[cat] = {"runs": len(rs), "passed": sum(r["passed"] for r in rs),
                     "pass_rate": round(sum(r["passed"] for r in rs) / len(rs), 3),
                     "cap_hits": sum(r["cap_hit"] for r in rs), "errors": sum(1 for r in rs if r["error"]),
                     "median_wall_s": _med([r["wall_s"] for r in rs]),
                     "median_ttft_s": _med([r["ttft_s"] for r in rs]),
                     "median_completion_tokens": _med([r["completion_tokens"] for r in rs]),
                     "median_reasoning_chars": _med([r["reasoning_chars"] for r in rs]),
                     "failed_ids": sorted({r["id"] for r in rs if not r["passed"]})}
    effort = {}
    for r in records:
        if r["category"] != "effort":
            continue
        effort.setdefault(r["id"], {}).setdefault(r["effort"], []).append(r)
    effort_rows = []
    for iid, per in sorted(effort.items()):
        row = {"id": iid}
        answers = set()
        for eff, rs in per.items():
            row[eff] = {"pass": sum(r["passed"] for r in rs), "n": len(rs),
                        "median_reasoning_chars": _med([r["reasoning_chars"] for r in rs]),
                        "median_wall_s": _med([r["wall_s"] for r in rs])}
            answers |= {_last_int(r["answer"]) for r in rs}
        row["distinct_answers"] = len(answers)
        effort_rows.append(row)
    ctx = [{"id": r["id"], "passed": r["passed"], "prompt_tokens": r["prompt_tokens"], "ttft_s": r["ttft_s"],
            "wall_s": r["wall_s"], "error": (r["error"] or "")[:120]} for r in records if r["category"] == "context"]
    return {"manifest": manifest, "total_runs": len(records), "total_passed": sum(r["passed"] for r in records),
            "total_wall_s": round(sum(r["wall_s"] for r in records), 1), "categories": cats,
            "effort_stability": effort_rows, "context_ladder": ctx}


def render_markdown(s: dict) -> str:
    m = s["manifest"]
    lines = [f"# quick-eval {m['label']} ({m['model']})", "",
             f"server {m['base_url']}, effort default `{m.get('efforts')}`, budget {m['budget']}, "
             f"max_tokens {m['max_tokens']}, seeds {m['seeds']}, started {m['started']}", "",
             *([f"effort items: budget {m.get('effort_budget')}, max_tokens {m.get('effort_max_tokens')} "
                "(the line above applies to the other categories)", ""] if "effort" in s["categories"] else []),
             f"**{s['total_passed']}/{s['total_runs']} passed, {s['total_wall_s']} s generation wall time**", "",
             "| category | pass | rate | cap hits | errors | med wall s | med TTFT s | med tokens | med reasoning chars |",
             "|---|---|---|---|---|---|---|---|---|"]
    for cat, c in s["categories"].items():
        lines.append(f"| {cat} | {c['passed']}/{c['runs']} | {c['pass_rate']} | {c['cap_hits']} | {c['errors']} | "
                     f"{c['median_wall_s']} | {c['median_ttft_s']} | {c['median_completion_tokens']} | "
                     f"{c['median_reasoning_chars']} |")
    if s["effort_stability"]:
        lines += ["", "## effort stability", "", "| item | low | medium | high | distinct answers |", "|---|---|---|---|---|"]
        for r in s["effort_stability"]:
            cell = lambda e: (f"{r[e]['pass']}/{r[e]['n']} ({r[e]['median_reasoning_chars']} ch)" if e in r else "-")
            lines.append(f"| {r['id']} | {cell('low')} | {cell('medium')} | {cell('high')} | {r['distinct_answers']} |")
    if s["context_ladder"]:
        lines += ["", "## context ladder", "", "| item | pass | prompt tokens | TTFT s | wall s | error |", "|---|---|---|---|---|---|"]
        for r in s["context_ladder"]:
            lines.append(f"| {r['id']} | {r['passed']} | {r['prompt_tokens']} | {r['ttft_s']} | {r['wall_s']} | {r['error']} |")
    failed = {cat: c["failed_ids"] for cat, c in s["categories"].items() if c["failed_ids"]}
    if failed:
        lines += ["", "## failed items", ""] + [f"- {cat}: {', '.join(ids)}" for cat, ids in failed.items()]
    return "\n".join(lines) + "\n"


def main(argv=None, annotate=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base-url", help="OpenAI-compatible base URL; default probes 127.0.0.1:8080 then :8093")
    p.add_argument("--model", help="model id to request; default: first id from /v1/models")
    p.add_argument("--label", help="short name for the output directory and report (e.g. exl3-2.05bpw)")
    p.add_argument("--categories", help="comma list of " + ",".join(ALL_CATEGORIES) + " (default all but code)")
    p.add_argument("--only", help="comma list of item id substrings to run")
    p.add_argument("--effort", default="medium", help="reasoning_effort for non-effort categories")
    p.add_argument("--efforts", default="low,medium,high", help="efforts for the effort-stability category")
    p.add_argument("--budget", type=int, default=1536,
                   help="reasoning_budget_tokens for non-effort items, always sent (0 = no budget, overrides config)")
    p.add_argument("--max-tokens", type=int, default=2560, help="max_tokens per run, reasoning included")
    p.add_argument("--effort-budget", type=int, default=0,
                   help="reasoning_budget_tokens for effort items (default 0 = none: native effort, not a budget)")
    p.add_argument("--effort-max-tokens", type=int, default=32768, help="max_tokens for effort items")
    p.add_argument("--deadline", type=float, default=1800, help="per-request total wall limit in seconds")
    p.add_argument("--temperature", type=float, default=DEFAULT_SAMPLING["temperature"])
    p.add_argument("--top-p", type=float, default=DEFAULT_SAMPLING["top_p"])
    p.add_argument("--top-k", type=int, default=DEFAULT_SAMPLING["top_k"])
    p.add_argument("--min-p", type=float, default=DEFAULT_SAMPLING["min_p"])
    p.add_argument("--expect-model", help="fail unless the server's first model id is exactly this")
    p.add_argument("--seeds", default="11,22,33,44,55")
    p.add_argument("--samples", type=int, default=1, help="how many of --seeds to use per cell")
    p.add_argument("--context-sizes", default="8000,16000,32000", help="approx prompt tokens for the context ladder")
    p.add_argument("--timeout", type=float, default=600, help="per-request socket idle timeout in seconds")
    p.add_argument("--stop-after", type=float, default=0, help="stop launching new runs after this many seconds")
    p.add_argument("--run-code", action="store_true", help="execute model code for 'code' items (limited subprocess)")
    p.add_argument("--no-warmup", action="store_true")
    p.add_argument("--out", help="output directory (default $PELICAN_ROOT/quick-eval/<stamp>-<label>)")
    p.add_argument("--list", action="store_true", help="print the item plan and exit")
    args = p.parse_args(argv)
    if args.list:
        items = load_static_items() + synth_arith() + synth_context([int(s) for s in args.context_sizes.split(",")])
        for i in items:
            print(f"{i['category']:<8} {i['id']:<22} {i['check']['type']:<14} {i['prompt'][:70]!r}")
        print(f"{len(items)} items", file=sys.stderr)
        return 0
    return run(args, annotate=annotate)


if __name__ == "__main__":
    sys.exit(main())
