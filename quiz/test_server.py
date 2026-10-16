import concurrent.futures
import copy
import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid
import xml.etree.ElementTree as ET

import qr
import server


class QuizTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        # Tests use fixed teams so coaches can edit quiz/teams.json freely.
        fixtures = tempfile.TemporaryDirectory()
        self.addCleanup(fixtures.cleanup)
        teams_path = Path(fixtures.name) / "teams.json"
        teams_path.write_text(json.dumps(["Team 1", "Team 2", "Team 3", "Team 4"]), encoding="utf-8")
        defaults = patch.object(server.QuizStore.__init__, "__defaults__",
                                (server.ROOT / "questions.json", teams_path))
        defaults.start()
        self.addCleanup(defaults.stop)
        self.store = server.QuizStore(self.directory.name)
        self.httpd = server.QuizServer(("127.0.0.1", 0), self.store, "coach-test-key")
        self.thread = threading.Thread(target=self.httpd.serve_forever)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.event_id = self.store.snapshot()["event_id"]
        self.correct = {q["id"]: q["correct_option"] for q in self.store.config["questions"]}

    def stop_server(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()

    def request(self, method, path, body=None, headers=None, raw=False):
        connection = http.client.HTTPConnection(*self.httpd.server_address, timeout=10)
        try:
            data = body if raw else json.dumps(body) if body is not None else None
            request_headers = {"Content-Type": "application/json", **(headers or {})}
            connection.request(method, path, data, request_headers)
            response = connection.getresponse()
            payload = response.read()
            content_type = response.getheader("Content-Type", "")
            result = json.loads(payload) if "application/json" in content_type else payload.decode()
            return response.status, dict(response.getheaders()), result
        finally:
            connection.close()

    def body(self, team="Team 1", answers=None, participant_id=None):
        return {"event_id": self.event_id, "participant_id": participant_id or str(uuid.uuid4()),
                "team": team, "answers": self.correct.copy() if answers is None else answers}

    def submit(self, **kwargs):
        body = self.body(**kwargs)
        status, _, result = self.request("POST", "/api/responses", body)
        self.assertEqual(status, 201, result)
        return body

    def admin(self, action, event_id=None):
        return self.request("POST", "/api/admin/" + action,
                            {"event_id": event_id or self.event_id},
                            {"x-admin-key": "coach-test-key"})

    def test_full_event_flow_and_hidden_answers(self):
        body = self.submit()
        _, _, public = self.request("GET", "/api/state?participant_id=" + body["participant_id"])
        self.assertEqual(public["phase"], "open")
        self.assertEqual(public["participants"], 1)
        self.assertEqual(public["correct_count"], 5)
        self.assertEqual(public["receipt"]["team"], "Team 1")
        for secret in ("correct_option", "takeaway", "correct_count", "answers"):
            if secret in ("correct_count", "answers"):
                self.assertNotIn(secret, public["receipt"])
            else:
                self.assertTrue(all(secret not in question for question in public["questions"]))
        self.assertNotIn("question_results", public)
        self.assertEqual(self.admin("reveal")[0], 409)
        self.assertEqual(self.admin("close")[0], 200)
        self.assertEqual(self.request("POST", "/api/responses", self.body())[0], 409)
        _, _, closed = self.request("GET", "/api/state")
        self.assertTrue(all("correct_option" not in q for q in closed["questions"]))
        self.assertEqual(self.admin("reveal")[0], 200)
        _, _, revealed = self.request("GET", "/api/state?participant_id=" + body["participant_id"])
        self.assertEqual(revealed["phase"], "revealed")
        self.assertEqual(revealed["receipt"]["correct_count"], 5)
        self.assertEqual(revealed["receipt"]["answers"], self.correct)
        self.assertEqual(revealed["question_results"][0]["correct_count"], 1)
        self.assertTrue(all(q["takeaway"] for q in revealed["questions"]))
        self.assertEqual(self.admin("reveal")[0], 409)
        self.assertEqual(self.admin("close")[0], 409)

    def test_ranking_normalizes_team_size_and_shares_exact_ties(self):
        self.submit(team="Team 1")
        self.submit(team="Team 2")
        self.submit(team="Team 2")
        wrong = {q["id"]: (q["correct_option"] + 1) % len(q["options"])
                 for q in self.store.config["questions"]}
        wrong["token-diet"] = self.correct["token-diet"]
        self.submit(team="Team 3", answers=wrong)
        board = self.store.snapshot()["leaderboard"]
        self.assertEqual([(r["team"], r["rank"]) for r in board],
                         [("Team 1", 1), ("Team 2", 1), ("Team 3", 3), ("Team 4", None)])
        self.assertEqual(board[0]["accuracy"], board[1]["accuracy"])
        self.assertEqual(board[1]["correct_count"], 10)
        self.assertEqual(board[2]["accuracy"], 0.2)
        self.assertIsNone(board[3]["accuracy"])

    def test_retry_is_idempotent_but_changes_are_rejected(self):
        body = self.submit()
        self.assertEqual(self.request("POST", "/api/responses", body)[0], 200)
        changed = copy.deepcopy(body)
        changed["team"] = "Team 2"
        self.assertEqual(self.request("POST", "/api/responses", changed)[0], 409)
        changed = copy.deepcopy(body)
        changed["answers"]["token-diet"] = 0
        self.assertEqual(self.request("POST", "/api/responses", changed)[0], 409)
        self.admin("close")
        self.assertEqual(self.request("POST", "/api/responses", body)[0], 200)
        self.assertEqual(self.store.snapshot()["participants"], 1)

    def test_server_scores_instead_of_trusting_client(self):
        answers = self.correct.copy()
        answers["token-diet"] = 0
        body = self.body(answers=answers)
        body["correct_count"] = 5
        body["score"] = 100
        self.assertEqual(self.request("POST", "/api/responses", body)[0], 201)
        self.assertEqual(self.store.snapshot()["correct_count"], 4)

    def test_rejects_invalid_submissions_without_writes(self):
        candidates = [None, [], "text", {}, self.body(team="unknown"),
                      self.body(participant_id="not-a-uuid"), self.body(answers={})]
        for value in (True, -1, 4, "1", 1.5, None, [], {}):
            candidate = self.body()
            candidate["answers"]["token-diet"] = value
            candidates.append(candidate)
        extra = self.body()
        extra["answers"]["extra"] = 0
        candidates.append(extra)
        for candidate in candidates:
            with self.subTest(candidate=candidate):
                status, _, result = self.request("POST", "/api/responses", candidate)
                self.assertEqual(status, 400, result)
                self.assertIn("error", result)
        self.assertEqual(self.store.snapshot()["participants"], 0)

    def test_strict_http_input_and_origin_checks(self):
        self.assertEqual(self.request("POST", "/api/responses", "{bad", raw=True)[0], 400)
        self.assertEqual(self.request("POST", "/api/responses", b"\xff", raw=True)[0], 400)
        self.assertEqual(self.request("POST", "/api/responses", " " * 17_000, raw=True)[0], 413)
        self.assertEqual(self.request("POST", "/api/responses", self.body(),
                                      {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request("POST", "/api/responses", self.body(),
                                      {"Origin": "https://untrusted.example"})[0], 403)
        self.assertEqual(self.request("POST", "/api/responses", self.body(),
                                      {"Origin": self.httpd.public_url})[0], 201)

    def test_all_coach_routes_require_key_and_are_disabled_without_it(self):
        for method, path in [("GET", "/api/admin/export")] + [
                ("POST", "/api/admin/" + action) for action in ("close", "reveal", "reset")]:
            for headers in ({}, {"x-admin-key": "wrong"}):
                with self.subTest(path=path, headers=headers):
                    self.assertEqual(self.request(method, path, {}, headers)[0], 401)
            with patch.object(self.httpd, "admin_key", ""):
                self.assertEqual(self.request(method, path, {},
                                              {"x-admin-key": "coach-test-key"})[0], 503)
        page = self.request("GET", "/")[2]
        self.assertNotIn("coach-test-key", page)
        self.assertEqual(self.store.snapshot()["phase"], "open")

    def test_export_is_complete_and_reset_is_persistent(self):
        body = self.submit()
        _, headers, exported = self.request("GET", "/api/admin/export",
                                            headers={"x-admin-key": "coach-test-key"})
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertEqual(exported["submissions"][0]["answers"], self.correct)
        self.assertEqual(exported["quiz"], self.store.config)
        self.assertEqual(self.store.snapshot()["participants"], 1)
        self.admin("close")
        self.admin("reveal")
        status, _, result = self.admin("reset")
        self.assertEqual(status, 200)
        self.assertNotEqual(result["event_id"], self.event_id)
        reloaded = server.QuizStore(self.directory.name)
        self.assertEqual(reloaded.snapshot()["phase"], "open")
        self.assertEqual(reloaded.snapshot()["participants"], 0)
        self.assertEqual(self.request("POST", "/api/responses", body)[0], 409)
        self.assertEqual(self.admin("close")[0], 409)
        self.event_id = result["event_id"]
        self.submit(participant_id=body["participant_id"])
        self.assertEqual(self.store.snapshot()["participants"], 1)

    def test_restart_preserves_responses_phase_and_event_id(self):
        self.submit()
        self.admin("close")
        self.admin("reveal")
        restored = server.QuizStore(self.directory.name)
        self.assertEqual(restored.snapshot(), self.store.snapshot())

    def test_storage_failures_never_claim_success(self):
        self.submit()
        before = self.store.export()
        with patch.object(self.store, "_save", side_effect=PermissionError("test write error")):
            for method, path, body, headers in (
                ("POST", "/api/responses", self.body(), {}),
                ("POST", "/api/admin/reset", {"event_id": self.event_id},
                 {"x-admin-key": "coach-test-key"}),
            ):
                with self.assertLogs("tokenwars.quiz", level="ERROR"):
                    status, _, result = self.request(method, path, body, headers)
                self.assertEqual(status, 500)
                self.assertIn("error", result)
                self.assertNotIn("accepted", result)
        self.assertEqual(self.store.export(), before)
        with patch.object(self.store, "_load", side_effect=ValueError("corrupt stored JSON")):
            for path in ("/api/state", "/healthz", "/api/admin/export"):
                with self.assertLogs("tokenwars.quiz", level="ERROR"):
                    status, _, result = self.request("GET", path, headers={"x-admin-key": "coach-test-key"})
                self.assertEqual(status, 500)
                self.assertIn("error", result)

    def test_atomic_write_failure_preserves_previous_event(self):
        self.submit()
        before = self.store.path.read_bytes()
        with patch.object(os, "replace", side_effect=PermissionError("test rename error")):
            with self.assertRaises(PermissionError):
                self.store.submit(self.body())
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_simultaneous_submissions_and_retries_do_not_lose_votes(self):
        bodies = [self.body() for _ in range(12)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(self.store.submit, bodies + bodies))
        self.assertEqual(sum(created for _, created in results), 12)
        self.assertEqual(self.store.snapshot()["participants"], 12)
        self.assertEqual(self.store.snapshot()["correct_count"], 60)

    def test_separate_store_instances_share_a_file_lock(self):
        other = server.QuizStore(self.directory.name)
        bodies = [self.body() for _ in range(12)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit((self.store if i % 2 else other).submit, body)
                       for i, body in enumerate(bodies)]
            for future in futures:
                future.result()
        self.assertEqual(other.snapshot()["participants"], 12)

    def test_pages_assets_and_qr_encode_configured_join_url(self):
        self.httpd.public_url = "https://quiz.example.com"
        for path in ("/", "/join", "/app.js", "/style.css"):
            status, headers, content = self.request("GET", path)
            self.assertEqual(status, 200)
            self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertTrue(content)
        status, headers, svg = self.request("GET", "/qr.svg")
        self.assertEqual(status, 200)
        self.assertIn("image/svg+xml", headers["Content-Type"])
        self.assertTrue(ET.fromstring(svg).tag.endswith("svg"))
        self.assertEqual(svg, qr.svg("https://quiz.example.com/join"))
        self.assertEqual(self.request("GET", "/questions.json")[0], 404)
        self.assertEqual(self.request("GET", "/teams.json")[0], 404)
        self.assertEqual(self.request("GET", "/../server.py")[0], 404)
        self.assertEqual(self.request("GET", "/api/admin/reset")[0], 404)
        self.assertEqual(self.request("POST", "/unknown", {})[0], 404)

    def test_configuration_change_with_responses_fails_without_destroying_data(self):
        self.submit()
        before = self.store.path.read_bytes()
        changed = copy.deepcopy(self.store.config)
        changed["questions"][0]["correct_option"] = 0
        config = Path(self.directory.name) / "questions.json"
        config.write_text(json.dumps(changed), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "configuration changed"):
            server.QuizStore(self.directory.name, config_path=config)
        self.assertEqual(self.store.path.read_bytes(), before)
        self.admin("reset")
        updated = server.QuizStore(self.directory.name, config_path=config)
        self.assertEqual(updated.snapshot()["participants"], 0)
        self.assertEqual(updated.config, changed)

    def test_configuration_enforces_question_limits_and_answer_shape(self):
        valid = self.store.config
        for count in (1, 5):
            config = {**valid, "questions": valid["questions"][:count]}
            server.validate_config(config, ["A"])
        cases = []
        for count in (0, 6):
            config = copy.deepcopy(valid)
            config["questions"] = (valid["questions"] + [valid["questions"][0]])[:count]
            cases.append(config)
        for value in (True, -1, 4, "1", None):
            config = copy.deepcopy(valid)
            config["questions"][0]["correct_option"] = value
            cases.append(config)
        config = copy.deepcopy(valid)
        config["questions"][1]["id"] = config["questions"][0]["id"]
        cases.append(config)
        for config in cases:
            with self.subTest(config=config):
                with self.assertRaises(ValueError):
                    server.validate_config(config, ["A"])
        for teams in ([], ["A", "a"], [" A"], [1], "A"):
            with self.assertRaises(ValueError):
                server.validate_config(valid, teams)

    def test_single_question_event_is_scored_and_ranked_out_of_one(self):
        config = {**self.store.config, "questions": self.store.config["questions"][:1]}
        config_path = Path(self.directory.name) / "one-question.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        one = server.QuizStore(Path(self.directory.name) / "one", config_path)
        data = one.snapshot()
        body = self.body(answers={"token-diet": 1})
        body["event_id"] = data["event_id"]
        one.submit(body)
        result = one.snapshot()
        self.assertEqual(len(result["questions"]), 1)
        self.assertEqual(result["leaderboard"][0]["answer_count"], 1)
        self.assertEqual(result["leaderboard"][0]["accuracy"], 1)


if __name__ == "__main__":
    unittest.main()
