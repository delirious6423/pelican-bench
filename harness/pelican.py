"""Pelican request mode on top of quick_eval's streaming client (no second client), plus SVG extraction rules.

Extraction rule (fixed before any scored run, v1):
  1. Look only at the visible answer (reasoning removed by the client; inline <think> blocks too).
  2. Find the first `<svg` start tag (case-insensitive, followed by whitespace, `>` or `/`).
  3. From there, count nested `<svg ...>` openings and `</svg>` closings (self-closing `<svg .../>` does not nest);
     the SVG ends at the `</svg>` that brings the depth back to 0. That substring, byte for byte, is the SVG.
  4. No closing tag -> status "unclosed" (incomplete; never repaired or auto-closed). No `<svg` -> status "none".
Nothing is added (no xmlns, no XML declaration, no closing tags). Validity and rendering are judged separately.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "quick-eval"))
import quick_eval as qe  # noqa: E402  - the existing streaming client, sampling and effort tables

PELICAN_PROMPT = "Generate an SVG of a pelican riding a bicycle"
EXTRACTION_RULE_VERSION = "v1-first-balanced-svg"
HARNESS_VERSION = "pelican-harness-1"

_SVG_TAG = re.compile(r"<(/?)svg(?=[\s>/])([^>]*)>", re.I)


def extract_svg(answer: str) -> dict:
    m = re.search(r"<svg(?=[\s>/])", answer, re.I)
    if not m:
        return {"status": "none", "svg": None, "start": None, "end": None, "n_svg_starts": 0}
    depth, end = 0, None
    for t in _SVG_TAG.finditer(answer, m.start()):
        closing, attrs = t.group(1) == "/", t.group(2)
        if closing:
            depth -= 1
        elif not attrs.rstrip().endswith("/"):
            depth += 1
        elif depth == 0:                          # a self-closing root <svg/>: complete on its own
            end = t.end()
            break
        if depth == 0:
            end = t.end()
            break
    n_starts = len(re.findall(r"<svg(?=[\s>/])", answer, re.I))
    if end is None:
        return {"status": "unclosed", "svg": answer[m.start():], "start": m.start(), "end": None, "n_svg_starts": n_starts}
    return {"status": "complete", "svg": answer[m.start():end], "start": m.start(), "end": end, "n_svg_starts": n_starts}


# Static flags: reported, never "fixed". Rendering happens only in the scriptless, network-less sandbox anyway.
_FLAGS = {
    "script": re.compile(r"<script\b", re.I),
    "event_handler": re.compile(r"\son[a-z]+\s*=", re.I),
    "foreign_object": re.compile(r"<foreignObject\b", re.I),
    "javascript_url": re.compile(r"javascript\s*:", re.I),
    "entity_decl": re.compile(r"<!ENTITY", re.I),
    "doctype": re.compile(r"<!DOCTYPE", re.I),
    "embed_object_iframe": re.compile(r"<(iframe|object|embed)\b", re.I),
    "css_import": re.compile(r"@import", re.I),
    "data_uri": re.compile(r"""(?:href|src)\s*=\s*["']\s*data:""", re.I),
}
_REF = re.compile(r"""(?:xlink:href|href|src)\s*=\s*["']\s*([^"'\s]+)""", re.I)
_CSS_URL = re.compile(r"url\(\s*['\"]?\s*([^)'\"\s]+)", re.I)


def svg_flags(svg: str | None) -> dict:
    if not svg:
        return {}
    flags = {k: len(p.findall(svg)) for k, p in _FLAGS.items() if p.search(svg)}
    ext = [u for u in _REF.findall(svg) + _CSS_URL.findall(svg) if not u.startswith("#") and not u.lower().startswith("data:")]
    if ext:
        flags["external_ref"] = sorted(set(ext))[:10]
    flags["has_xmlns_svg"] = 'xmlns="http://www.w3.org/2000/svg"' in svg or "xmlns='http://www.w3.org/2000/svg'" in svg
    flags["bytes"] = len(svg.encode())
    return flags


UNSAFE_KEYS = ("script", "event_handler", "foreign_object", "javascript_url", "entity_decl", "embed_object_iframe",
               "css_import", "external_ref")


def build_body(model: str, effort: str, seed: int, max_tokens: int) -> dict:
    """Exact upstream prompt, no system message, explicit sampling, native effort, explicit zero thinking budget."""
    return qe.build_body(model, {"prompt": PELICAN_PROMPT, "check": {"type": "none"}}, effort, 0, max_tokens, seed,
                         sampling=qe.DEFAULT_SAMPLING)


def correlate_history(metrics: dict, t_wall0: float, t_wall1: float, usage: dict | None) -> dict:
    """Pick /metrics' history entry for this request: requests are serialized, so it is the newest entry whose start
    lies in [t0-2, t1] and whose output_tokens equals the stream's completion_tokens. Never guess: return the
    reason when nothing matches."""
    hist = metrics.get("requests") or []                  # newest first
    want = (usage or {}).get("completion_tokens")
    cands = [h for h in hist if isinstance(h.get("time"), (int, float)) and t_wall0 - 2 <= h["time"] <= t_wall1 + 1]
    for h in cands:
        if want is None or h.get("output_tokens") == want:
            return {"match": "time+tokens" if want is not None else "time-only", "entry": h}
    return {"match": None, "reason": f"{len(cands)} entries in window, none with output_tokens={want}",
            "newest": hist[0] if hist else None}
