"""Unit tests for quick_eval: checkers, item bank, synthesized items, and SSE streaming against a fake server.
No model, loopback only:  python3 -I test_quick_eval.py
"""
import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import quick_eval as qe  # noqa: E402


class CheckerTests(unittest.TestCase):
    def c(self, check, answer, response=None):
        return qe.check({"check": check}, answer, response or {})[0]

    def test_last_int(self):
        self.assertTrue(self.c({"type": "last_int", "expected": 12345678}, "The answer is 12,345,678."))
        self.assertFalse(self.c({"type": "last_int", "expected": 5}, "5 then 6"))
        self.assertFalse(self.c({"type": "last_int", "expected": 5}, "none"))

    def test_contains(self):
        self.assertTrue(self.c({"type": "contains_any", "expected": ["Dvorak", "Dvořák"]}, "Antonín Dvořák"))
        self.assertFalse(self.c({"type": "contains_all", "expected": ["1", "2"]}, "only 1"))

    def test_word_count_and_chars(self):
        self.assertTrue(self.c({"type": "word_count", "expected": 3}, "one two three."))
        self.assertFalse(self.c({"type": "no_char", "chars": "e"}, "Sunrise"))
        self.assertTrue(self.c({"type": "no_char", "chars": "e"}, "A rosy dawn lights up our sky."))
        self.assertTrue(self.c({"type": "lowercase"}, "all of this text stays lowercase here."))
        self.assertFalse(self.c({"type": "lowercase"}, "Capital letter here, which is long enough."))

    def test_json_schema(self):
        schema = {"name": "str", "age": "int", "tags": ["str", 3]}
        self.assertTrue(self.c({"type": "json_schema", "schema": schema}, '{"name":"a","age":3,"tags":["x","y","z"]}'))
        self.assertFalse(self.c({"type": "json_schema", "schema": schema}, '{"name":"a","age":"3","tags":["x","y","z"]}'))
        self.assertFalse(self.c({"type": "json_schema", "schema": schema}, 'Sure! {"name":"a","age":3,"tags":["x","y","z"]}'))
        nested = {"coords": {"lat": "float", "lon": "float"}, "coastal": "bool"}
        self.assertTrue(self.c({"type": "json_schema", "schema": nested}, '{"coords":{"lat":1.5,"lon":2},"coastal":false}'))
        self.assertFalse(self.c({"type": "json_schema", "schema": {"age": "int"}}, '{"age": true}'))

    def test_lists(self):
        self.assertTrue(self.c({"type": "unique_lines", "min": 3}, "cat\ndog\nemu"))
        self.assertFalse(self.c({"type": "unique_lines", "min": 3}, "cat\ndog\nCat"))
        self.assertTrue(self.c({"type": "numbered_list", "expected": 2}, "1. a\n2. b"))
        self.assertFalse(self.c({"type": "numbered_list", "expected": 2}, "1. a\n2. b\n3. c"))
        self.assertTrue(self.c({"type": "csv", "rows": 2, "cols": 2}, "a,b\n1,2"))

    def test_zhtw(self):
        chk = {"type": "zhtw", "required_any": [["央行", "中央銀行"], ["利率"]]}
        self.assertTrue(self.c(chk, "央行維持利率不變。"))
        self.assertFalse(self.c(chk, "央行维持利率不变。"))  # simplified leakage
        self.assertFalse(self.c(chk, "央行維持政策不變。"))  # missing term

    def test_tool_call(self):
        chk = {"type": "tool_call", "name": "f", "args": {"city": "Kaohsiung", "n": 5}}
        good = {"tool_calls": [{"function": {"name": "f", "arguments": '{"city": "kaohsiung", "n": 5.0}'}}]}
        self.assertTrue(self.c(chk, "", good))
        extra = {"tool_calls": [{"function": {"name": "f", "arguments": '{"city": "Kaohsiung", "n": 5, "x": 1}'}}]}
        self.assertFalse(self.c(chk, "", extra))
        self.assertFalse(self.c(chk, "I would call f", {"tool_calls": []}))

    def test_run_code_check(self):
        item = {"check": {"type": "code", "tests": ["assert f(2) == 4"]}}
        self.assertTrue(qe.run_code_check(item, "```python\ndef f(x):\n    return x * 2\n```")[0])
        self.assertFalse(qe.run_code_check(item, "```python\ndef f(x):\n    return x\n```")[0])
        ok, why = qe.run_code_check(item, "```python\nimport time\ndef f(x):\n    time.sleep(30)\n```", timeout=2)
        self.assertFalse(ok)
        self.assertIn("timeout", why)


class ItemBankTests(unittest.TestCase):
    def test_static_items_parse_and_self_check(self):
        items = qe.load_static_items()
        ids = [i["id"] for i in items]
        self.assertEqual(len(ids), len(set(ids)), "duplicate item ids")
        cats = {i["category"] for i in items}
        self.assertTrue(cats <= set(qe.ALL_CATEGORIES), cats)
        # every non-code checker must accept a clearly wrong answer without raising
        for i in items:
            if i["check"]["type"] != "code":
                ok, _ = qe.check(i, "zzz", {})
                self.assertFalse(ok, i["id"])

    def test_synth_arith_is_deterministic_and_correct(self):
        a, b = qe.synth_arith(), qe.synth_arith()
        self.assertEqual([x["prompt"] for x in a], [x["prompt"] for x in b])
        self.assertEqual(len(a), 20)
        for item in a:
            self.assertTrue(qe.check(item, str(item["check"]["expected"]), {})[0])

    def test_synth_context_contains_needles(self):
        items = qe.synth_context([8000])
        item = items[0]
        for code in item["check"]["expected"]:
            self.assertIn(code, item["prompt"])
        self.assertGreater(len(item["prompt"].split()), 5000)


class _FakeStrata(BaseHTTPRequestHandler):
    """Streams reasoning_content then content, with usage/timings on the last chunk, like Strata's server."""
    body_log = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"data": [{"id": "fake-model"}]}).encode())

    def do_POST(self):
        n = int(self.headers["Content-Length"])
        body = json.loads(self.rfile.read(n))
        _FakeStrata.body_log.append(body)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()

        def ev(delta, finish=None, extra=None):
            d = {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            d.update(extra or {})
            self.wfile.write(f"data: {json.dumps(d)}\n\n".encode())
            self.wfile.flush()

        prompt = body["messages"][-1]["content"]
        ev({"role": "assistant", "content": ""})
        if prompt.startswith("MODE:"):
            return self._mode(prompt, ev)
        ev({"reasoning_content": "thinking..."})
        if body.get("tools"):
            ev({"tool_calls": [{"index": 0, "id": "c1", "type": "function",
                                "function": {"name": "get_forecast", "arguments": '{"city": "Kao'}}]})
            ev({"tool_calls": [{"index": 0, "function": {"arguments": 'hsiung", "day": "tomorrow", "unit": "celsius"}'}}]})
            finish = "tool_calls"
        else:
            ev({"content": "42"})
            finish = "stop"
        ev({}, finish, {"usage": {"prompt_tokens": 10, "completion_tokens": 7, "total_tokens": 17},
                        "timings": {"predicted_per_second": 33.3}})
        self.wfile.write(b"data: [DONE]\n\n")

    def _mode(self, prompt, ev):
        import time as _t
        mode = prompt.split()[0][5:]
        if mode == "truncate":                    # connection closes mid-answer: no finish_reason, no [DONE]
            ev({"content": "partial <svg"})
            return
        if mode == "slow":                        # keeps streaming past the client's deadline
            for _ in range(40):
                ev({"content": "x"})
                _t.sleep(0.1)
        elif mode == "inline":                    # thinking inline in content, answer after </think>
            ev({"content": "<thi"})
            ev({"content": "nk>step one "})
            _t.sleep(0.2)
            ev({"content": "step two</think>"})
            _t.sleep(0.2)
            ev({"content": "\n\nANSWER"})
        elif mode == "unclosed":                  # cut off while still thinking
            ev({"content": "<think>still going"})
            ev({}, "length", {"usage": {"prompt_tokens": 3, "completion_tokens": 9, "total_tokens": 12}})
            self.wfile.write(b"data: [DONE]\n\n")
            return
        elif mode == "error":
            self.wfile.write(b'data: {"error": {"message": "engine died"}}\n\n')
            return
        ev({}, "stop", {"usage": {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8}})
        self.wfile.write(b"data: [DONE]\n\n")


class StreamingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), _FakeStrata)
        cls.base = f"http://127.0.0.1:{cls.srv.server_port}/v1"
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def test_discover(self):
        self.assertEqual(qe.discover(self.base), (self.base, "fake-model"))

    def test_stream_text(self):
        item = {"prompt": "x", "check": {"type": "last_int", "expected": 42}}
        body = qe.build_body("fake-model", item, "high", 1536, 2560, 11, 0.0)
        self.assertEqual(body["reasoning_effort"], "high")
        self.assertEqual(body["reasoning_budget_tokens"], 1536)
        r = qe.stream_chat(self.base, body, timeout=5)
        self.assertIsNone(r["error"])
        self.assertEqual(r["answer"], "42")
        self.assertEqual(r["reasoning"], "thinking...")
        self.assertEqual(r["finish_reason"], "stop")
        self.assertEqual(r["usage"]["completion_tokens"], 7)
        self.assertEqual(r["timings"]["predicted_per_second"], 33.3)
        self.assertIsNotNone(r["t_first_reasoning"])
        self.assertLessEqual(r["t_first_reasoning"], r["t_first_answer"])
        self.assertTrue(qe.check(item, r["answer"], r)[0])

    def test_stream_tool_call(self):
        item = qe.load_static_items()
        item = next(i for i in item if i["id"] == "tool-weather")
        body = qe.build_body("fake-model", item, "medium", 0, 512, 1, 0.0)
        # regression: an explicit zero budget is transmitted so it overrides a configured budget
        self.assertEqual(body["reasoning_budget_tokens"], 0)
        r = qe.stream_chat(self.base, body, timeout=5)
        self.assertEqual(r["finish_reason"], "tool_calls")
        ok, why = qe.check(item, r["answer"], r)
        self.assertTrue(ok, why)

    def test_inline_think_stripped(self):
        self.assertEqual(qe.split_inline_think("<think>hmm</think>7"), ("hmm", "7", True))
        self.assertEqual(qe.split_inline_think("<think>cut off"), ("cut off", "", True))
        self.assertEqual(qe.split_inline_think("pre</think>ans"), ("pre", "ans", True))
        self.assertEqual(qe.split_inline_think("plain"), ("", "plain", False))

    def _mode(self, mode, deadline=None, timeout=5):
        body = qe.build_body("fake-model", {"prompt": f"MODE:{mode} go"}, "medium", 0, 64, 11)
        return qe.stream_chat(self.base, body, timeout=timeout, deadline=deadline)

    def test_truncated_stream_is_not_a_completion(self):
        r = self._mode("truncate")
        self.assertEqual(r["abort"], "truncated")
        self.assertIsNone(r["finish_reason"])
        self.assertFalse(r["done_seen"])
        self.assertIn("without finish_reason", r["error"])

    def test_total_deadline_enforced(self):
        r = self._mode("slow", deadline=0.8, timeout=10)
        self.assertEqual(r["abort"], "deadline")
        self.assertLess(r["t_done"], 2.5)
        self.assertIn("deadline", r["error"])

    def test_inline_think_timing(self):
        r = self._mode("inline")
        self.assertIsNone(r["error"])
        self.assertTrue(r["inline_think"])
        self.assertEqual(r["answer"], "ANSWER")
        self.assertIn("step one step two", r["reasoning"])
        self.assertIsNotNone(r["t_first_text"])
        self.assertGreaterEqual(r["t_first_answer"] - r["t_first_text"], 0.35)

    def test_unclosed_think_scores_no_answer(self):
        r = self._mode("unclosed")
        self.assertEqual(r["finish_reason"], "length")
        self.assertEqual(r["answer"], "")
        self.assertIsNone(r["t_first_answer"])

    def test_sse_error_event(self):
        r = self._mode("error")
        self.assertIn("engine died", r["error"])

    def test_role_only_chunk_is_not_ttft(self):
        r = self._mode("inline")
        # the role-only first chunk (content "") arrives first but is not generated text
        self.assertLessEqual(r["t_first_byte"], r["t_first_text"])
        self.assertGreater(r["n_events"], 1)

    def test_summary_and_markdown(self):
        recs = [{"id": "effort-x", "category": "effort", "effort": e, "seed": 1, "passed": p, "detail": "", "finish_reason": "stop",
                 "cap_hit": False, "wall_s": 1.0, "ttft_s": 0.1, "completion_tokens": 5, "reasoning_chars": 10, "error": None,
                 "answer": a, "prompt_tokens": 3} for e, p, a in (("low", False, "17"), ("high", True, "18"))]
        s = qe.summarize(recs, {"label": "t", "model": "m", "base_url": "u", "efforts": ["low", "high"], "budget": 1,
                                "max_tokens": 2, "effort_budget": 0, "effort_max_tokens": 49152, "seeds": [1],
                                "started": "now"})
        self.assertEqual(s["categories"]["effort"]["passed"], 1)
        self.assertEqual(s["effort_stability"][0]["distinct_answers"], 2)
        md = qe.render_markdown(s)
        self.assertIn("| effort | 1/2 |", md)
        self.assertIn("effort stability", md)
        # regression: the header showed only the non-effort budget/max, misreporting the effort settings actually sent
        self.assertIn("effort items: budget 0, max_tokens 49152", md)


class CorrectionTests(unittest.TestCase):
    """Regression tests for sampling, scoring and budget corrections."""

    def test_whole_term_matching(self):
        self.assertTrue(qe._has_term("The symbol is W.", "W"))
        self.assertFalse(qe._has_term("It is wolfram", "W"))
        self.assertFalse(qe._has_term("142", "42"))
        self.assertTrue(qe._has_term("answer: 42", "42"))
        self.assertTrue(qe._has_term("measured in webers", "weber"))
        self.assertTrue(qe._has_term("台灣最高峰是玉山。", "玉山"))
        self.assertTrue(qe._has_term("Antonín Dvořák", "Dvořák"))
        self.assertFalse(qe._has_term("Hickeyville", "Hickey"))
        item = next(i for i in qe.load_static_items() if i["id"] == "recall-01")
        self.assertFalse(qe.check(item, "Tungsten, also called wolfram", {})[0])
        self.assertTrue(qe.check(item, "W", {})[0])

    def test_explicit_sampling_and_seed(self):
        body = qe.build_body("m", {"prompt": "x"}, "high", None, 100, 22)
        for k, v in qe.DEFAULT_SAMPLING.items():
            self.assertEqual(body[k], v)
        self.assertEqual((body["temperature"], body["top_p"], body["top_k"]), (1.0, 0.95, 20))
        self.assertNotIn("reasoning_budget_tokens", body)       # None = deliberately not sent
        self.assertEqual(body["messages"], [{"role": "user", "content": "x"}])
        with self.assertRaises(ValueError):
            qe.build_body("m", {"prompt": "x"}, "low", 0, 10, 0)
        self.assertEqual(qe.RESOLVED_EFFORT["high"], "xhigh")

    def test_cli_defaults_are_model_card_not_greedy(self):
        import argparse
        captured = {}
        orig = qe.run
        qe.run = lambda a, annotate=None: captured.setdefault("a", a) and 0
        try:
            qe.main(["--base-url", "http://127.0.0.1:1/v1"])
        finally:
            qe.run = orig
        a = captured["a"]
        self.assertEqual((a.temperature, a.top_p, a.top_k, a.min_p), (1.0, 0.95, 20, 0.0))
        self.assertEqual((a.effort_budget, a.effort_max_tokens), (0, 32768))


class RunBudgetTests(unittest.TestCase):
    """run(): effort items get the native-effort budget (explicit 0), other items the bounded tier budget."""

    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), _FakeStrata)
        cls.base = f"http://127.0.0.1:{cls.srv.server_port}/v1"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def test_effort_items_are_not_capped_by_tier_budget(self):
        import tempfile
        _FakeStrata.body_log.clear()
        with tempfile.TemporaryDirectory() as td:
            rc = qe.main(["--base-url", self.base, "--categories", "effort,recall", "--only", "recall-01,effort-",
                          "--efforts", "low,high", "--no-warmup", "--out", td])
            self.assertEqual(rc, 0)
            recs = [json.loads(l) for l in open(Path(td) / "runs.jsonl")]
        eff = [b for b in _FakeStrata.body_log if b["messages"][-1]["content"] != "Say OK." and
               any(r["category"] == "effort" and r["request"] == b for r in recs)]
        self.assertTrue(eff)
        for b in eff:
            self.assertEqual(b["reasoning_budget_tokens"], 0)
            self.assertEqual(b["max_tokens"], 32768)
        rec = [r for r in recs if r["category"] == "recall"][0]
        self.assertEqual(rec["request"]["reasoning_budget_tokens"], 1536)
        self.assertEqual(rec["request"]["temperature"], 1.0)
        self.assertEqual({r["resolved_effort"] for r in recs if r["category"] == "effort"}, {"low", "xhigh"})


if __name__ == "__main__":
    unittest.main()
