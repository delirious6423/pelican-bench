#!/usr/bin/env python3
"""Offline evidence: the exact prompt each engine renders for the pelican request at low/medium/high effort.

Uses the engine's own serve/frontend.py (EFFORT table -> template kwargs, ChatTemplate with its Jinja settings) and
the template file the server would load (tokenizer dir's chat_template.jinja, else serve/chat_template.jinja).
Run once per engine/template pair:  python3 -I render_prompts.py ENGINE_SRC TEMPLATE OUT.json
"""
import hashlib
import json
import sys
from pathlib import Path

PROMPT = "Generate an SVG of a pelican riding a bicycle"


def main() -> int:
    src, tpl, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    sys.path.insert(0, str(src))
    from serve import frontend  # the engine's own front end
    t = frontend.ChatTemplate(tpl)
    res = {"engine_src": str(src), "template": str(tpl),
           "template_sha256": hashlib.sha256(tpl.read_bytes()).hexdigest(),
           "frontend_sha256": hashlib.sha256((src / "serve/frontend.py").read_bytes()).hexdigest(),
           "effort_table": {k: v for k, v in frontend.EFFORT.items()}, "renders": {}}
    for eff in ("low", "medium", "high"):
        kw = frontend.effort_kwargs(eff)
        text = t.render([{"role": "user", "content": PROMPT}], **kw)
        res["renders"][eff] = {"template_kwargs": kw, "prompt": text,
                               "sha256": hashlib.sha256(text.encode()).hexdigest()}
    shas = {e: r["sha256"] for e, r in res["renders"].items()}
    res["distinct_renders"] = len(set(shas.values()))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=1, ensure_ascii=False))
    print(json.dumps({"template_sha256": res["template_sha256"][:12], "distinct": res["distinct_renders"],
                      **{e: (r["template_kwargs"], r["sha256"][:12]) for e, r in res["renders"].items()}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
