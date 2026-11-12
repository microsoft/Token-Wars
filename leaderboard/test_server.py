import http.client
import json
import tempfile
import threading
import unittest
from unittest.mock import patch

import server
from storage import LocalStore


class LeaderboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join()

    def setUp(self):
        self.data = tempfile.TemporaryDirectory()
        self.addCleanup(self.data.cleanup)
        self.settings = patch.multiple(
            server, DATA_DIR=self.data.name, store=LocalStore(self.data.name),
            ADMIN_KEY="coach-only-test-key", SUBMIT_KEY="team-test-key",
        )
        self.settings.start()
        self.addCleanup(self.settings.stop)
        server.store.create(server.SUBMIT_KEY)

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection(*self.httpd.server_address, timeout=5)
        try:
            payload = json.dumps(body) if body is not None else None
            if headers is None:
                headers = {"x-submit-key": server.SUBMIT_KEY}
            connection.request(method, path, body=payload, headers=headers)
            response = connection.getresponse()
            content = response.read()
            result = content.decode("utf-8") if path in ("/", "/index.html") else json.loads(content)
            return response.status, dict(response.getheaders()), result
        finally:
            connection.close()

    def submit(self, team="Team One", pass_rate=0.9, cost=0.01, key=None):
        body = {
            "team": team, "language": "python", "variant": "solution",
            "strategy": {"concurrency": 2, "routing": {"simple": "economy"}},
            "summary": {
                "items": 100, "successes": int(100 * pass_rate), "pass_rate": pass_rate,
                "total_cost_usd": 0.9, "cost_per_success_usd": cost,
            },
        }
        status, _, result = self.request(
            "POST", "/api/submissions", body, {"x-submit-key": key or server.SUBMIT_KEY})
        self.assertEqual(status, 201)
        self.assertTrue(result["accepted"])
        return result

    def admin_request(self, method, path):
        return self.request(method, path, headers={
            "x-admin-key": server.ADMIN_KEY, "x-submit-key": server.SUBMIT_KEY})

    def load(self):
        return server.store.load(server.SUBMIT_KEY)

    def test_admin_endpoints_require_separate_key_and_preserve_data(self):
        self.submit()
        for method, path in (("GET", "/api/submissions/export"), ("POST", "/api/leaderboard/reset")):
            for headers in ({}, {"x-submit-key": server.SUBMIT_KEY},
                            {"x-admin-key": server.SUBMIT_KEY}, {"x-admin-key": "wrong"}):
                with self.subTest(method=method, headers=headers):
                    status, _, result = self.request(method, path, headers=headers)
                    self.assertEqual(status, 401)
                    self.assertIn("error", result)
        self.assertEqual(len(self.load()), 1)

    def test_admin_endpoints_disabled_without_configuration(self):
        self.submit()
        with patch.object(server, "ADMIN_KEY", ""):
            for method, path in (("GET", "/api/submissions/export"), ("POST", "/api/leaderboard/reset")):
                status, _, result = self.admin_request(method, path)
                self.assertEqual(status, 503)
                self.assertIn("LEADERBOARD_ADMIN_KEY", result["error"])
            _, _, page = self.request("GET", "/")
            self.assertIn("const adminEnabled=false;", page)
        self.assertEqual(len(self.load()), 1)

    def test_export_downloads_every_run_and_strategy_without_mutation(self):
        self.submit()
        self.submit(cost=0.005)
        self.submit(team="Below bar", pass_rate=0.5)
        before = self.load()
        status, headers, result = self.admin_request("GET", "/api/submissions/export")
        self.assertEqual(status, 200)
        self.assertEqual(result, before)
        self.assertEqual(len(result), 3)
        self.assertEqual(sum(s["valid"] for s in result), 2)
        self.assertEqual(result[0]["strategy"]["concurrency"], 2)
        self.assertRegex(headers["Content-Disposition"],
                         r'^attachment; filename="tokenwars-submissions-\d{8}-\d{6}\.json"$')
        self.assertIn("application/json", headers["Content-Type"])
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(self.load(), before)
        _, _, public = self.request("GET", "/api/submissions")
        self.assertTrue(all("strategy" not in s for s in public))

    def test_export_empty_board(self):
        status, _, result = self.admin_request("GET", "/api/submissions/export")
        self.assertEqual(status, 200)
        self.assertEqual(result, [])

    def test_reset_clears_all_runs_persistently_and_allows_new_submissions(self):
        self.submit()
        self.submit(team="Below bar", pass_rate=0.5)
        for _ in range(2):
            status, _, result = self.admin_request("POST", "/api/leaderboard/reset")
            self.assertEqual(status, 200)
            self.assertEqual(result, {"reset": True})
            self.assertEqual(LocalStore(self.data.name).load(server.SUBMIT_KEY), [])
        _, _, board = self.request("GET", "/api/leaderboard")
        self.assertEqual(board["ranked"], [])
        self.assertEqual(board["invalid"], [])
        self.assertEqual(board["teams"], 0)
        self.assertEqual(board["total_submissions"], 0)
        self.assertEqual(board["min_pass_rate"], server.MIN_PASS_RATE)
        self.assertEqual(board["min_items"], server.MIN_ITEMS)
        self.assertEqual(self.submit(team="New event")["rank"], 1)
        self.assertEqual(len(self.load()), 1)

    def test_get_cannot_reset(self):
        self.submit()
        status, _, _ = self.admin_request("GET", "/api/leaderboard/reset")
        self.assertEqual(status, 404)
        self.assertEqual(len(self.load()), 1)

    def test_storage_failures_report_errors_without_claiming_success(self):
        self.submit()
        before = self.load()
        with patch.object(server.store, "reset", side_effect=PermissionError("test write failure")):
            status, _, result = self.admin_request("POST", "/api/leaderboard/reset")
        self.assertEqual(status, 500)
        self.assertIn("error", result)
        self.assertNotIn("reset", result)
        self.assertEqual(self.load(), before)
        for error in (OSError("test read failure"), ValueError("invalid stored JSON")):
            with patch.object(server.store, "load", side_effect=error):
                status, _, result = self.admin_request("GET", "/api/submissions/export")
            self.assertEqual(status, 500)
            self.assertIn("error", result)

    def test_submission_auth_and_ranking_still_work(self):
        status, _, _ = self.request("POST", "/api/submissions", {}, {"x-admin-key": server.ADMIN_KEY})
        self.assertEqual(status, 401)
        self.submit()
        self.submit(cost=0.005)
        self.submit(team="Below bar", pass_rate=0.5)
        _, _, board = self.request("GET", "/api/leaderboard")
        self.assertEqual(board["total_submissions"], 3)
        self.assertEqual(board["teams"], 2)
        self.assertEqual(board["ranked"][0]["cost_per_success_usd"], 0.005)
        self.assertEqual(board["ranked"][0]["submissions"], 2)
        self.assertEqual(board["invalid"][0]["team"], "Below bar")

    def test_page_renders_controls_without_exposing_keys(self):
        for path in ("/", "/index.html"):
            status, _, page = self.request("GET", path)
            self.assertEqual(status, 200)
            self.assertIn('id="download"', page)
            self.assertIn('id="reset"', page)
            self.assertIn('<input id="admin-key" type="password"', page)
            self.assertIn('<input id="new-key" type="text"', page)
            self.assertIn("const adminEnabled=true;", page)
            self.assertNotIn("__ADMIN_ENABLED__", page)
            self.assertNotIn(server.ADMIN_KEY, page)
            self.assertNotIn(server.SUBMIT_KEY, page)
            self.assertIn('id="leaderboard-select"', page)
            self.assertIn('id="add-board"', page)
            self.assertNotIn('id="view-key"', page)
            self.assertNotIn('id="list-boards"', page)

    def test_preflight_allows_admin_header(self):
        status, headers, _ = self.request_options()
        self.assertEqual(status, 204)
        self.assertIn("x-admin-key", headers["Access-Control-Allow-Headers"])

    def test_anyone_can_list_but_only_coach_can_create_keys(self):
        status, _, result = self.request("GET", "/api/leaderboards")
        self.assertEqual(status, 200)
        self.assertEqual(result["leaderboardKeys"], [server.SUBMIT_KEY])
        for headers in ({}, {"x-submit-key": server.SUBMIT_KEY},
                        {"x-admin-key": server.SUBMIT_KEY}):
            status, _, _ = self.request("POST", "/api/leaderboards", {"leaderboardKey": "other-event"}, headers)
            self.assertEqual(status, 401)
        self.assertEqual(server.store.list_keys(), [server.SUBMIT_KEY])
        with patch.object(server, "ADMIN_KEY", ""):
            self.assertEqual(self.request("GET", "/api/leaderboards")[0], 200)
            status, _, _ = self.request("POST", "/api/leaderboards", {"leaderboardKey": "other-event"})
            self.assertEqual(status, 503)

    def create_board(self, key):
        return self.request("POST", "/api/leaderboards", {"leaderboardKey": key},
                            {"x-admin-key": server.ADMIN_KEY})

    def test_creation_is_empty_and_duplicate_never_erases_runs(self):
        self.assertEqual(self.create_board("Event/2026:Key")[0], 201)
        self.assertEqual(server.store.load("Event/2026:Key"), [])
        self.submit(key="Event/2026:Key")
        status, _, _ = self.create_board("Event/2026:Key")
        self.assertEqual(status, 409)
        self.assertEqual(len(server.store.load("Event/2026:Key")), 1)
        status, _, result = self.admin_request("GET", "/api/leaderboards")
        self.assertEqual(status, 200)
        self.assertEqual(result["leaderboardKeys"], sorted([server.SUBMIT_KEY, "Event/2026:Key"]))

    def test_boards_are_isolated_across_ranking_public_export_and_reset(self):
        other = "another-event"
        self.create_board(other)
        self.submit(team="Same team", cost=0.01)
        self.submit(team="Same team", cost=0.001, key=other)
        for path in ("/api/leaderboard", "/api/submissions", "/api/submissions/export"):
            headers = {"x-submit-key": other, "x-admin-key": server.ADMIN_KEY}
            status, _, result = self.request("GET", path, headers=headers)
            self.assertEqual(status, 200)
            if path == "/api/leaderboard":
                self.assertEqual(result["total_submissions"], 1)
                self.assertEqual(result["ranked"][0]["cost_per_success_usd"], 0.001)
            else:
                self.assertEqual(len(result), 1)
                self.assertEqual(result[0]["summary"]["cost_per_success_usd"], 0.001)
        self.admin_request("POST", "/api/leaderboard/reset")
        self.assertEqual(self.load(), [])
        self.assertEqual(len(server.store.load(other)), 1)
        self.assertIn(server.SUBMIT_KEY, server.store.list_keys())
        self.submit(team="Same team")
        _, _, board = self.request("GET", "/api/leaderboard")
        self.assertEqual(board["ranked"][0]["improvement_pct"], 0)

    def test_missing_and_unknown_keys_never_create_boards(self):
        for method, path in (("GET", "/api/leaderboard"), ("GET", "/api/submissions"),
                             ("GET", "/api/submissions/export"), ("POST", "/api/leaderboard/reset")):
            for headers, expected in (({"x-admin-key": server.ADMIN_KEY}, 401),
                                      ({"x-admin-key": server.ADMIN_KEY, "x-submit-key": "unknown"}, 404)):
                self.assertEqual(self.request(method, path, headers=headers)[0], expected)
        self.assertEqual(self.request("POST", "/api/submissions", {
            "team": "unknown", "summary": {
                "items": 100, "pass_rate": 0.9, "total_cost_usd": 1, "cost_per_success_usd": 0.01}},
            {"x-submit-key": "unknown"})[0], 404)
        self.assertEqual(server.store.list_keys(), [server.SUBMIT_KEY])

    def test_persistence_after_reopening_and_bootstrap_does_not_reset(self):
        self.submit()
        before = self.load()
        with patch.object(server, "store", LocalStore(self.data.name)), patch.object(server, "STORAGE_URL", ""):
            server.initialize_storage()
            self.assertEqual(self.load(), before)
        self.assertEqual(self.load(), before)

    def test_invalid_key_creation_and_malformed_payloads_do_not_change_storage(self):
        for key in ("", "with spaces", "../bad\nkey", "x" * 129, server.ADMIN_KEY, None):
            self.assertEqual(self.create_board(key)[0], 400)
        for body in ([], "bad", 1):
            self.assertEqual(self.request("POST", "/api/submissions", body)[0], 400)
        self.assertEqual(self.load(), [])

    def test_nonfinite_metrics_cannot_poison_persistent_storage(self):
        for value in (float("nan"), float("inf"), -0.5, 1.1):
            status, _, _ = self.request("POST", "/api/submissions", {
                "team": "bad", "summary": {"items": 100, "pass_rate": value,
                                         "total_cost_usd": 1, "cost_per_success_usd": 0.01}})
            self.assertEqual(status, 400)
        self.assertEqual(self.load(), [])

    def test_submission_and_list_storage_failures_are_explicit(self):
        for method in ("load", "list_keys", "create"):
            with patch.object(server.store, method, side_effect=OSError("test storage failure")):
                if method == "load":
                    status, _, result = self.request("GET", "/api/leaderboard")
                elif method == "create":
                    status, _, result = self.create_board("failed")
                else:
                    status, _, result = self.admin_request("GET", "/api/leaderboards")
                self.assertEqual(status, 500)
                self.assertIn("error", result)
        with patch.object(server.store, "append", side_effect=OSError("test storage failure")):
            status, _, result = self.request("POST", "/api/submissions", {
                "team": "bad", "summary": {"items": 100, "pass_rate": 0.9,
                                         "total_cost_usd": 1, "cost_per_success_usd": 0.01}})
            self.assertEqual(status, 500)
            self.assertNotIn("accepted", result)
        self.assertEqual(self.load(), [])

    def request_options(self):
        connection = http.client.HTTPConnection(*self.httpd.server_address, timeout=5)
        try:
            connection.request("OPTIONS", "/api/leaderboard/reset")
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
