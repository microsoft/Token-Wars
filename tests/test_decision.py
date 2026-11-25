"""Offline contract/integration tests. Never calls Azure or reads the repository's .env."""
from __future__ import annotations

import json
import html
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
DOTNET = os.environ.get("TOKENWARS_TEST_DOTNET")


class Gateway:
    def __init__(self, label="SIMPLE", probability=0.95, verdicts=None, replies=None,
                 malformed=None, throttle=False):
        self.requests = []
        self.label = label
        self.probability = probability
        self.verdicts = iter(verdicts or [1.0] * 10)
        self.replies = iter(replies or ["Returns are accepted within 30 days."] * 10)
        self.malformed = malformed
        self.throttle = throttle
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                gateway.requests.append((self.path, body, dict(self.headers)))
                if self.path == "/openai/v1/decisions" and gateway.throttle:
                    gateway.throttle = False
                    self.send_response(429)
                    self.send_header("Retry-After", "0")
                    self.end_headers()
                    self.wfile.write(b'{"error":"test throttle"}')
                    return
                if self.path == "/openai/v1/decisions":
                    if "tier" in body["questions"]:
                        alternatives = [key for key in ("SIMPLE", "STANDARD", "COMPLEX") if key != gateway.label]
                        probabilities = {key: (1 - gateway.probability) / 2 for key in alternatives}
                        probabilities[gateway.label] = gateway.probability
                        answers = {"tier": {"type": "choice", "choice": gateway.label, "probabilities": probabilities}}
                    elif "acceptable" in body["questions"]:
                        answers = {"acceptable": {"type": "noul", "noul": next(gateway.verdicts)}}
                    else:
                        answers = {"ok": {"type": "noul", "noul": 1.0}}
                    payload = {"answers": answers, "usage": {"input_tokens": 100, "output_tokens": 2}}
                    if gateway.malformed == "answers":
                        payload["answers"] = {}
                    elif gateway.malformed == "usage":
                        payload["usage"] = {"input_tokens": 100}
                    elif gateway.malformed == "probability":
                        for answer in answers.values():
                            if answer["type"] == "noul":
                                answer["noul"] = 1.1
                            else:
                                answer["probabilities"][gateway.label] = -0.1
                elif self.path.endswith("/embeddings"):
                    payload = {"data": [{"index": 0, "embedding": [1.0, 0.0]}], "usage": {"prompt_tokens": 1}}
                else:
                    payload = {"choices": [{"message": {"content": next(gateway.replies)}, "finish_reason": "stop"}],
                               "usage": {"prompt_tokens": 20, "completion_tokens": 4}}
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(payload).encode())

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/openai/v1/"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class AppTests:
    language = "python"

    def execute(self, gateway, strategy, *, variant="solution", count=1, command="run", missing_price=False):
        with tempfile.TemporaryDirectory(prefix="tokenwars-decision-") as folder:
            root = Path(folder)
            for sub in ("config", "data", "knowledge-base", "prompts"):
                (root / "shared" / sub).mkdir(parents=True)
            registry = json.loads((ROOT / "shared" / "config" / "models.example.json").read_text())
            registry["models"] = {k: v for k, v in registry["models"].items()
                                  if k in ("premium", "balanced", "economy", "decision", "embedding", "judge")}
            for model in registry["models"].values():
                model.update(base_url=gateway.url, api_key_env="TEST_GATEWAY_KEY")
            registry["gateway"] = {"base_url": gateway.url, "api_key_env": "TEST_GATEWAY_KEY"}
            (root / "shared" / "config" / "models.json").write_text(json.dumps(registry))
            pricing = json.loads((ROOT / "shared" / "config" / "pricing.json").read_text())
            if missing_price:
                del pricing["models"][registry["models"]["decision"]["pricing_key"]]
            (root / "shared" / "config" / "pricing.json").write_text(json.dumps(pricing))
            (root / "shared" / "knowledge-base" / "returns.md").write_text("# Returns\n## Window\nReturns: 30 days.\n")
            (root / "shared" / "prompts" / "system-baseline.md").write_text(
                "Customer: {{customer_id}}\nKB: {{knowledge_base}}\nOrders: {{orders}}\nToday: {{now}}")
            (root / "shared" / "data" / "orders.json").write_text("[]")
            items = [{"id": str(i), "customer_id": "C1", "question": "What is the return window?",
                      "category": "FORBIDDEN_CATEGORY", "difficulty": "FORBIDDEN_DIFFICULTY",
                      "reference_answer": "FORBIDDEN_REFERENCE", "must_include": ["FORBIDDEN_FACT"], "compare": True}
                     for i in range(count)]
            (root / "shared" / "data" / "workload.jsonl").write_text("\n".join(map(json.dumps, items)))
            settings = {"default_model": "economy", "concurrency": 1, **strategy}
            (root / "strategy.json").write_text(json.dumps(settings))
            env = {**os.environ, "TOKENWARS_ROOT": str(root), "TOKENWARS_MOCK": "0",
                   "TEST_GATEWAY_KEY": "offline-test", "PYTHONDONTWRITEBYTECODE": "1"}
            if self.language == "python":
                env["PYTHONPATH"] = str(ROOT / "python" / variant)
                invocation = [sys.executable, "-m", "tokenwars"]
            else:
                invocation = [DOTNET, str(ROOT / "dotnet" / variant / "TokenWars" / "bin" / "Debug" / "net8.0" / "TokenWars.dll")]
            args = [command] + (["--no-judge"] if command in ("run", "compare") else [])
            if command == "compare":
                args += ["--models", "economy,decision"]
            completed = subprocess.run(invocation + args, cwd=root, env=env, capture_output=True, text=True,
                                       encoding="utf-8", timeout=45)
            results = sorted((root / "results").glob("*.json"))
            payload = json.loads(results[-1].read_text(encoding="utf-8")) if results else None
            return completed, payload

    def assert_ok(self, completed):
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_routing_all_tiers_and_threshold_boundary(self):
        for label, tier in (("SIMPLE", "economy"), ("STANDARD", "balanced"), ("COMPLEX", "premium")):
            with self.subTest(label=label), Gateway(label=label, probability=0.8) as gateway:
                run, result = self.execute(gateway, {"routing": "decision"})
                self.assert_ok(run)
                self.assertEqual(result["items"][0]["model_path"], [tier])
                self.assertEqual(result["summary"]["calls_by_model"]["decision"]["calls"], 1)
                self.assertAlmostEqual(result["summary"]["calls_by_model"]["decision"]["cost_usd"], 102 * 0.042 / 1e6, places=8)
                body = gateway.requests[0][1]
                self.assertEqual(set(body), {"model", "state", "questions"})
                self.assertEqual(body["model"], "decision")
                self.assertEqual(set(body["state"]), {"question"})
                self.assertNotIn("FORBIDDEN", json.dumps(gateway.requests))
                self.assertEqual(gateway.requests[0][2]["api-key"], "offline-test")

    def test_uncertain_route_falls_back_to_premium(self):
        with Gateway(probability=0.5) as gateway:
            run, result = self.execute(gateway, {"routing": "decision"})
            self.assert_ok(run)
            self.assertEqual(result["items"][0]["model_path"], ["premium"])
            self.assertTrue(result["items"][0]["decision_calls"][0]["fallback"])
            self.assertIn("below threshold", run.stderr)

    def test_verification_rejects_until_premium_then_caches_final_answer(self):
        with Gateway(verdicts=[0.2, 0.3], replies=["bad economy", "bad balanced", "final premium"]) as gateway:
            run, result = self.execute(gateway, {"verify_answers": True, "escalation": True, "exact_cache": True}, count=2)
            self.assert_ok(run)
            self.assertEqual(result["items"][0]["model_path"], ["economy", "balanced", "premium"])
            self.assertEqual(result["items"][0]["answer"], "final premium")
            self.assertEqual(result["items"][1]["cache"], "exact")
            self.assertEqual(result["items"][1]["answer"], "final premium")
            self.assertEqual(result["items"][1]["decision_calls"], [])
            self.assertEqual(result["summary"]["calls_by_model"]["decision"]["calls"], 2)
            self.assertEqual(len(gateway.requests), 5)
            verification = [body for path, body, _ in gateway.requests if path.endswith("decisions")]
            self.assertTrue(all(body["model"] == "decision" for body in verification))
            self.assertIn("Returns: 30 days.", verification[0]["state"]["context"])
            self.assertEqual(verification[0]["state"]["answer"], "bad economy")
            self.assertEqual(verification[1]["state"]["answer"], "bad balanced")
            self.assertNotIn("FORBIDDEN", json.dumps(verification))

    def test_both_features_work_together(self):
        with Gateway(verdicts=[0.8]) as gateway:
            run, result = self.execute(gateway, {"routing": "decision", "verify_answers": True, "escalation": True})
            self.assert_ok(run)
            self.assertEqual(result["items"][0]["model_path"], ["economy"])
            self.assertEqual([d["purpose"] for d in result["items"][0]["decision_calls"]], ["routing", "verification"])
            self.assertTrue(result["items"][0]["decision_calls"][1]["accepted"])

    def test_explicit_escalate_and_empty_answer_skip_verification(self):
        for text in ("ESCALATE", ""):
            with self.subTest(text=text), Gateway(replies=[text, "good balanced"], verdicts=[1.0]) as gateway:
                run, result = self.execute(gateway, {"verify_answers": True, "escalation": True})
                self.assert_ok(run)
                self.assertEqual(result["items"][0]["model_path"], ["economy", "balanced"])
                self.assertEqual(result["summary"]["calls_by_model"]["decision"]["calls"], 1)

    def test_premium_and_disabled_features_make_no_decision_calls(self):
        for settings in ({}, {"default_model": "premium", "verify_answers": True, "escalation": True}):
            with self.subTest(settings=settings), Gateway() as gateway:
                run, result = self.execute(gateway, settings)
                self.assert_ok(run)
                self.assertNotIn("decision", result["summary"]["calls_by_model"])
                self.assertEqual(len(gateway.requests), 1)

    def test_invalid_answers_fail_item_but_usage_is_counted(self):
        for malformed in ("answers", "probability"):
            with self.subTest(malformed=malformed), Gateway(malformed=malformed) as gateway:
                run, result = self.execute(gateway, {"routing": "decision"})
                self.assert_ok(run)
                self.assertTrue(result["items"][0]["error"])
                self.assertGreater(result["summary"]["total_cost_usd"], 0)
                self.assertEqual(len(gateway.requests), 1)

    def test_missing_usage_aborts_run_without_results(self):
        with Gateway(malformed="usage") as gateway:
            run, result = self.execute(gateway, {"routing": "decision"})
            self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
            self.assertIn("not scoreable", run.stderr)
            self.assertIsNone(result)

    def test_invalid_settings_and_missing_price_fail_before_spend(self):
        cases = [({"verify_answers": True}, False), ({"decision_threshold": 1.1}, False),
                 ({"decision_threshold": "0.8"}, False), ({"verify_answers": "false"}, False),
                 ({"verify_answers": True, "escalation": "true"}, False),
                 ({"routing": "decision"}, True), ({"default_model": "decision"}, False)]
        for settings, missing_price in cases:
            with self.subTest(settings=settings, price=missing_price), Gateway() as gateway:
                run, result = self.execute(gateway, settings, missing_price=missing_price)
                self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
                self.assertEqual(gateway.requests, [])

    def test_starter_optional_todos_fail_before_spend_even_with_premium(self):
        for settings, todo in (({"routing": "decision"}, "3.2b"),
                               ({"default_model": "premium", "verify_answers": True, "escalation": True}, "3.3b")):
            with self.subTest(todo=todo), Gateway() as gateway:
                run, result = self.execute(gateway, settings, variant="starter")
                self.assertEqual(run.returncode, 2)
                self.assertIn(todo, run.stderr)
                self.assertEqual(gateway.requests, [])

    def test_starter_baseline_still_works(self):
        with Gateway() as gateway:
            run, result = self.execute(gateway, {}, variant="starter")
            self.assert_ok(run)
            self.assertEqual(len(gateway.requests), 1)
            self.assertEqual(result["items"][0]["decision_calls"], [])

    def test_doctor_uses_native_decision_contract(self):
        with Gateway() as gateway:
            run, _ = self.execute(gateway, {}, command="doctor")
            self.assert_ok(run)
            decisions = [b for p, b, _ in gateway.requests if p.endswith("decisions")]
            self.assertEqual(len(decisions), 1)
            self.assertEqual(decisions[0]["questions"]["ok"]["type"], "noul")

    def test_compare_disables_verification_and_skips_decision_model(self):
        with Gateway() as gateway:
            run, _ = self.execute(gateway, {"routing": "decision", "verify_answers": True, "escalation": True}, command="compare")
            self.assert_ok(run)
            self.assertEqual(len(gateway.requests), 1)
            self.assertTrue(gateway.requests[0][0].endswith("chat/completions"))

    def test_decision_retries_throttle_on_same_operation(self):
        with Gateway(throttle=True) as gateway:
            run, result = self.execute(gateway, {"routing": "decision", "retry_on_throttle": True})
            self.assert_ok(run)
            self.assertEqual([p for p, _, _ in gateway.requests],
                             ["/openai/v1/decisions", "/openai/v1/decisions", "/openai/v1/chat/completions"])
            self.assertEqual(result["summary"]["calls_by_model"]["decision"]["calls"], 1)


class PythonDecisionTests(AppTests, unittest.TestCase):
    pass


class GuideTests(unittest.TestCase):
    def test_architecture_shows_required_decision_model_and_optional_hooks(self):
        guide = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
        svg = ET.fromstring(re.search(r'<svg class="arch".*?</svg>', guide, re.S).group())
        nodes = {element.get("id"): element for element in svg.iter() if element.get("id")}
        model = nodes["models-decision"]
        self.assertIn(model, list(nodes["model-layer"]))
        model_text = " ".join(model.itertext())
        self.assertIn("Microsoft-Decision-1.1", model_text)
        self.assertIn("Required model", model_text)
        self.assertIn("deployment: decision", model_text)
        for node_id, todo in (("decision-routing", "3.2b"), ("decision-verification", "3.3b")):
            self.assertIn("optional", nodes[node_id].get("class").split())
            self.assertIn(todo, " ".join(nodes[node_id].itertext()))
        for path_id in ("decision-route-call", "decision-model-call", "decision-verification-call",
                        "verification-accept", "verification-reject", "verification-bypass",
                        "escalation-retry", "cache-hit"):
            self.assertTrue(nodes[path_id].get("d"), path_id)
        description = "".join(nodes["arch-d"].itertext())
        for behavior in ("exact context", "low-probability fallback", "Premium answers",
                         "cache hit skips routing", "off by default"):
            self.assertIn(behavior, description)

    def test_optional_solutions_match_source_and_have_navigation_and_prompts(self):
        guide = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
        for step, python_file, dotnet_file in (("3.2b", "router.py", "Router.cs"), ("3.3b", "pipeline.py", "Pipeline.cs")):
            self.assertIn(f'data-sub="{step}"', guide)
            self.assertIn(f'"{step}": {{', guide)
            card = re.search(r'<article[^>]+id="step-' + re.escape(step) + r'".*?</article>', guide, re.S).group()
            self.assertIn("Optional", card)
            self.assertIn(f'data-solution-step="{step}"', card)
            snippets = re.findall(r"<pre><code>(.*?)</code></pre>", card, re.S)
            self.assertEqual(len(snippets), 2)
            for code, source in zip(snippets, (ROOT / "python" / "solution" / "tokenwars" / python_file,
                                              ROOT / "dotnet" / "solution" / "TokenWars" / dotnet_file)):
                self.assertIn(html.unescape(code), source.read_text(encoding="utf-8"))

    def test_embedded_policy_solutions_preserve_native_usage_and_routes(self):
        guide = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
        for file in ("ai-gateway-starter.xml", "ai-gateway-multiprovider.xml"):
            blocks = re.findall(r'<pre data-edit-file="infra/policies/' + re.escape(file)
                                + r'" data-start-line="\d+" data-end-line="\d+"><code>(.*?)</code></pre>', guide, re.S)
            native_usage = [html.unescape(b) for b in blocks if "input_tokens" in b]
            self.assertTrue(native_usage, file)
            self.assertTrue(all("output_tokens" in b for b in native_usage))
            if "multiprovider" in file:
                self.assertIn('/systemone', native_usage[0])


@unittest.skipUnless(DOTNET, "Set TOKENWARS_TEST_DOTNET to a .NET 8 host after building both tracks.")
class DotnetDecisionTests(AppTests, unittest.TestCase):
    language = "dotnet"


@unittest.skipUnless(shutil.which("terraform"), "Terraform is required to render policy templates.")
class PolicyTests(unittest.TestCase):
    def test_all_policy_templates_render_and_route_decisions(self):
        for optional in (False, True):
            with self.subTest(optional=optional), tempfile.TemporaryDirectory(prefix="tokenwars-policy-") as folder:
                variables = dict(backend_id="primary", pool_backend_id="pool", pool_models=["premium", "balanced", "economy"],
                                 tokens_per_minute=20000, fireworks_backend_id="fireworks", fireworks_auth="api_key",
                                 fireworks_models=["FW-model"] if optional else [], selfhosted_enabled=optional,
                                 selfhosted_model="llama", selfhosted_backend_id="ollama", failover_model_map={"premium": "FW-model"},
                                 decision_backend_id="decision-backend", decision_model="decision",
                                 decision_requests_per_minute=600)
                template_dir = json.dumps((ROOT / "infra" / "policies").as_posix())
                expression = ('jsonencode({for f in ["ai-gateway-starter.xml","ai-gateway-solution.xml",'
                              '"ai-gateway-multiprovider.xml"] : f => templatefile('
                              + template_dir[:-1] + '/${f}", jsondecode(' + json.dumps(json.dumps(variables)) + '))})')
                run = subprocess.run(["terraform", f"-chdir={folder}", "console"], input=expression + "\n",
                                     capture_output=True, text=True, timeout=45)
                self.assertEqual(run.returncode, 0, run.stderr)
                rendered = json.loads(json.loads(run.stdout))
                for name, xml in rendered.items():
                    policy = ET.fromstring(xml)
                    branches = [w for w in policy.findall("./inbound/choose/when")
                                if w.attrib["condition"] == '@(context.Operation.Id == "decisions")']
                    self.assertEqual(len(branches), 1, name)
                    branch = branches[0]
                    self.assertEqual(branch.find("./choose/when").get("condition"),
                                     '@((string)context.Variables["tw-model"] != "decision")')
                    self.assertEqual(branch.find("set-backend-service").get("backend-id"), "decision-backend")
                    self.assertEqual(branch.find("rewrite-uri").get("template"), "/systemone")
                    self.assertEqual(branch.find("rate-limit-by-key").get("calls"), "600")
                    self.assertIsNotNone(branch.find("authentication-managed-identity"))
                    self.assertEqual(branch.find(".//set-status").get("code"), "400")
                    self.assertIsNone(branch.find(".//llm-token-limit"))
                    self.assertIn('["input_tokens"]', policy.find("./outbound/set-variable").get("value"))
                    if "multiprovider" in name:
                        failover = policy.find("./backend/retry/set-variable")
                        self.assertIn('context.Operation.Id == "chat-completions"', failover.get("value"))


if __name__ == "__main__":
    unittest.main()
