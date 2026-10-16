#!/usr/bin/env python3
"""Standalone Token Wars takeaway quiz. Run with Python 3.12 or later."""

import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from fractions import Fraction
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import qr

ROOT = Path(__file__).resolve().parent
MAX_BODY = 16_384
MAX_DISCARD = 1_048_576
LOG = logging.getLogger("tokenwars.quiz")


class RequestError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


@lru_cache(maxsize=4)
def _qr_svg(public_url):
    return qr.svg(public_url + "/join").encode()


def read_json(path):
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def validate_config(config, teams):
    def text(value, maximum):
        return isinstance(value, str) and 0 < len(value.strip()) <= maximum

    if not isinstance(config, dict) or not text(config.get("title"), 100):
        raise ValueError("questions.json must contain a title (1-100 characters).")
    questions = config.get("questions")
    if not isinstance(questions, list) or not 1 <= len(questions) <= 5:
        raise ValueError("Configure between 1 and 5 questions.")
    ids = set()
    for question in questions:
        if not isinstance(question, dict):
            raise ValueError("Each question must be an object.")
        qid = question.get("id")
        if not text(qid, 60) or qid in ids:
            raise ValueError("Question IDs must be non-empty and unique.")
        ids.add(qid)
        for field, maximum in (("challenge", 100), ("prompt", 500), ("takeaway", 1000)):
            if not text(question.get(field), maximum):
                raise ValueError(f"Question {qid}: invalid {field}.")
        options = question.get("options")
        if (not isinstance(options, list) or not 2 <= len(options) <= 6
                or not all(text(option, 300) for option in options)
                or len(set(options)) != len(options)):
            raise ValueError(f"Question {qid}: provide 2-6 distinct answer options.")
        answer = question.get("correct_option")
        if type(answer) is not int or not 0 <= answer < len(options):
            raise ValueError(f"Question {qid}: correct_option must be a zero-based option index.")
    if (not isinstance(teams, list) or not 1 <= len(teams) <= 100
            or not all(text(team, 40) and team == team.strip() for team in teams)
            or len({team.casefold() for team in teams}) != len(teams)):
        raise ValueError("teams.json must contain 1-100 unique team names (1-40 characters).")


class QuizStore:
    def __init__(self, data_dir, config_path=ROOT / "questions.json", teams_path=ROOT / "teams.json"):
        self.config = read_json(Path(config_path))
        self.teams = read_json(Path(teams_path))
        validate_config(self.config, self.teams)
        self.fingerprint = hashlib.sha256(
            json.dumps([self.config, self.teams], sort_keys=True).encode()
        ).hexdigest()
        self.path = Path(data_dir) / "quiz.json"
        self.lock = threading.Lock()
        with self._locked():
            self._load()

    @contextmanager
    def _locked(self):
        # Azure Files locks also serialize old/new replicas during a revision handover.
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.with_suffix(".lock").open("a+b") as lock_file:
                if os.name == "nt":
                    import msvcrt

                    if lock_file.tell() == 0:
                        lock_file.write(b"\0")
                        lock_file.flush()
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    if os.name == "nt":
                        lock_file.seek(0)
                        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _fresh(self):
        return {"event_id": uuid.uuid4().hex, "phase": "open",
                "fingerprint": self.fingerprint, "submissions": []}

    def _load(self):
        if not self.path.exists():
            state = self._fresh()
            self._save(state)
            return state
        state = read_json(self.path)
        if (not isinstance(state, dict) or state.get("phase") not in ("open", "closed", "revealed")
                or not isinstance(state.get("submissions"), list)
                or not isinstance(state.get("event_id"), str)):
            raise ValueError("Invalid quiz data. Restore a valid exported backup; do not overwrite it.")
        if state.get("fingerprint") != self.fingerprint:
            if state["submissions"]:
                raise ValueError("Quiz configuration changed with stored responses. Restore the "
                                 "previous configuration, export and reset before editing it.")
            state = self._fresh()
            self._save(state)
        return state

    def _save(self, state):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=True, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)

    def snapshot(self, participant_id=None):
        with self._locked():
            state = self._load()
        questions = self.config["questions"]
        revealed = state["phase"] == "revealed"
        public_questions = [
            {key: value for key, value in question.items()
             if key in ("id", "challenge", "prompt", "options")
             or (revealed and key in ("correct_option", "takeaway"))}
            for question in questions
        ]
        rows = []
        for team in self.teams:
            submissions = [s for s in state["submissions"] if s["team"] == team]
            correct = sum(s["correct_count"] for s in submissions)
            possible = len(submissions) * len(questions)
            rows.append({"team": team, "participants": len(submissions),
                         "correct_count": correct, "answer_count": possible,
                         "accuracy": correct / possible if possible else None})
        rows.sort(key=lambda row: (
            -Fraction(row["correct_count"], row["answer_count"]) if row["answer_count"] else 1,
            row["team"].casefold()))
        previous = None
        rank = 0
        for index, row in enumerate(rows, 1):
            ratio = (Fraction(row["correct_count"], row["answer_count"])
                     if row["answer_count"] else None)
            if ratio is not None and ratio != previous:
                rank = index
            row["rank"] = rank if ratio is not None else None
            previous = ratio
        receipt = next((self._receipt(s, revealed) for s in state["submissions"]
                        if s["participant_id"] == participant_id), None)
        result = {
            "title": self.config["title"], "event_id": state["event_id"],
            "phase": state["phase"], "questions": public_questions, "teams": self.teams,
            "leaderboard": rows, "participants": len(state["submissions"]),
            "correct_count": sum(row["correct_count"] for row in rows), "receipt": receipt,
        }
        if revealed:
            result["question_results"] = [
                {"id": q["id"], "correct_count": sum(
                    s["answers"][q["id"]] == q["correct_option"] for s in state["submissions"]),
                 "participants": len(state["submissions"])}
                for q in questions
            ]
        return result

    @staticmethod
    def _receipt(submission, revealed):
        receipt = {"team": submission["team"], "received_at": submission["received_at"]}
        if revealed:
            receipt.update(correct_count=submission["correct_count"], answers=submission["answers"])
        return receipt

    def submit(self, body):
        if not isinstance(body, dict):
            raise RequestError("The request must be a JSON object.")
        participant_id = body.get("participant_id")
        try:
            if not isinstance(participant_id, str) or str(uuid.UUID(participant_id)) != participant_id:
                raise ValueError
        except ValueError:
            raise RequestError("A valid participant ID is required.") from None
        team = body.get("team")
        if team not in self.teams:
            raise RequestError("Choose a team from the list.")
        answers = body.get("answers")
        questions = self.config["questions"]
        if not isinstance(answers, dict) or set(answers) != {q["id"] for q in questions}:
            raise RequestError("Answer every question exactly once.")
        for question in questions:
            answer = answers[question["id"]]
            if type(answer) is not int or not 0 <= answer < len(question["options"]):
                raise RequestError("Each answer must be a valid option index.")
        with self._locked():
            state = self._load()
            if body.get("event_id") != state["event_id"]:
                raise RequestError("This event has changed. Reload the page before answering.", 409)
            previous = next((s for s in state["submissions"]
                             if s["participant_id"] == participant_id), None)
            if previous:
                if previous["team"] != team or previous["answers"] != answers:
                    raise RequestError("Your response has already been recorded and cannot be changed.", 409)
                return self._receipt(previous, state["phase"] == "revealed"), False
            if state["phase"] != "open":
                raise RequestError("Responses are closed. Watch the screen for the takeaways.", 409)
            submission = {
                "participant_id": participant_id, "team": team, "answers": answers,
                "correct_count": sum(answers[q["id"]] == q["correct_option"] for q in questions),
                "received_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            state["submissions"].append(submission)
            self._save(state)
            return self._receipt(submission, False), True

    def administer(self, action, event_id):
        with self._locked():
            state = self._load()
            if event_id != state["event_id"]:
                raise RequestError("This event has changed. Refresh the dashboard and try again.", 409)
            if action == "close":
                if state["phase"] != "open":
                    raise RequestError("Only an open quiz can be closed.", 409)
                state["phase"] = "closed"
            elif action == "reveal":
                if state["phase"] != "closed":
                    raise RequestError("Close responses before revealing the answers.", 409)
                state["phase"] = "revealed"
            elif action == "reset":
                state = self._fresh()
            else:
                raise RequestError("Unknown coach action.", 404)
            self._save(state)
        return {"event_id": state["event_id"], "phase": state["phase"]}

    def export(self):
        with self._locked():
            state = self._load()
        return {**state, "quiz": self.config, "teams": self.teams}


class QuizServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, store, admin_key="", public_url=""):
        self.store = store
        self.admin_key = admin_key
        super().__init__(address, Handler)
        self.public_url = public_url.rstrip("/") or f"http://localhost:{self.server_port}"
        parts = urlsplit(self.public_url)
        if (parts.scheme not in ("http", "https") or not parts.netloc or parts.username
                or parts.password or parts.path or parts.query or parts.fragment):
            self.server_close()
            raise ValueError("PUBLIC_URL must be an absolute HTTP(S) origin without a path.")
        try:
            _qr_svg(self.public_url)
        except ValueError:
            self.server_close()
            raise


class Handler(BaseHTTPRequestHandler):
    server_version = "TokenWarsQuiz/1.0"

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def _send(self, status, payload, content_type="application/json; charset=utf-8", headers=None):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; script-src 'self'; style-src 'self'; "
                         "img-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
                         "base-uri 'none'; form-action 'self'")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def _json(self):
        if self.headers.get("Transfer-Encoding"):
            raise RequestError("Transfer-Encoding is not supported.", 400)
        if self.headers.get_content_type() != "application/json":
            raise RequestError("Use Content-Type: application/json.", 415)
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            raise RequestError("Invalid Content-Length.") from None
        if length < 0:
            raise RequestError("Content-Length is required.", 411)
        if length > MAX_BODY:
            raise RequestError("Request body is too large.", 413)
        try:
            data = self.rfile.read(length)
            self._body_consumed = True
            if len(data) != length:
                raise RequestError("Incomplete request body.")
            return json.loads(data)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise RequestError("Invalid JSON body.") from None

    def _discard_body(self):
        # Closing a socket with unread request data makes some clients (notably on
        # Windows) see a connection reset instead of the error response.
        if self._body_consumed or self.headers.get("Transfer-Encoding"):
            return
        try:
            remaining = int(self.headers.get("Content-Length", "0"))
            if remaining > MAX_DISCARD:
                return
            while remaining > 0:
                chunk = self.rfile.read(min(remaining, 65_536))
                if not chunk:
                    break
                remaining -= len(chunk)
        except (ValueError, OSError):
            pass

    def _admin(self):
        if not self.server.admin_key:
            raise RequestError("Coach controls are disabled. Configure QUIZ_ADMIN_KEY.", 503)
        if not hmac.compare_digest(self.headers.get("x-admin-key", "").encode(),
                                   self.server.admin_key.encode()):
            raise RequestError("Invalid coach key.", 401)

    def _run(self, method):
        self._body_consumed = False
        try:
            self._route(method)
        except RequestError as error:
            self.close_connection = True
            self._discard_body()
            self._send(error.status, {"error": str(error)})
        except TimeoutError:
            self.close_connection = True
            self._send(408, {"error": "Request timed out."})
        except (OSError, ValueError, KeyError, TypeError):
            LOG.exception("Quiz request failed: %s %s", method, urlsplit(self.path).path)
            self._send(500, {"error": "Quiz storage or configuration failed. Contact the coach."})

    def do_GET(self):
        self._run("GET")

    def do_POST(self):
        self._run("POST")

    def _route(self, method):
        url = urlsplit(self.path)
        path = url.path
        if method == "POST":
            origin = self.headers.get("Origin")
            if origin and origin != self.server.public_url:
                raise RequestError("Cross-origin requests are not allowed.", 403)
            if path.startswith("/api/admin/"):
                self._admin()
            body = self._json()
            if path == "/api/responses":
                receipt, created = self.server.store.submit(body)
                self._send(201 if created else 200, {"accepted": True, "receipt": receipt})
                return
            if path in ("/api/admin/close", "/api/admin/reveal", "/api/admin/reset"):
                if not isinstance(body, dict):
                    raise RequestError("The request must be a JSON object.")
                self._send(200, self.server.store.administer(path.rsplit("/", 1)[1],
                                                           body.get("event_id")))
                return
        elif method == "GET":
            if path == "/healthz":
                self.server.store.snapshot()
                self._send(200, {"status": "ok"})
                return
            if path == "/api/state":
                params = parse_qs(url.query)
                self._send(200, {**self.server.store.snapshot(
                    params.get("participant_id", [None])[0]),
                    "join_url": self.server.public_url + "/join",
                    "admin_enabled": bool(self.server.admin_key)})
                return
            if path == "/api/admin/export":
                self._admin()
                self._send(200, self.server.store.export(), headers={
                    "Content-Disposition": 'attachment; filename="tokenwars-quiz-results.json"'})
                return
            if path == "/qr.svg":
                self._send(200, _qr_svg(self.server.public_url), "image/svg+xml")
                return
            assets = {"/": ("index.html", "text/html"), "/join": ("index.html", "text/html"),
                      "/app.js": ("app.js", "text/javascript"),
                      "/style.css": ("style.css", "text/css")}
            if path in assets:
                filename, content_type = assets[path]
                self._send(200, (ROOT / "static" / filename).read_bytes(),
                           content_type + "; charset=utf-8")
                return
        raise RequestError("Not found.", 404)


def main():
    logging.basicConfig(level=logging.INFO)
    store = QuizStore(os.environ.get("DATA_DIR", str(ROOT / "data")))
    server = QuizServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), store,
                        os.environ.get("QUIZ_ADMIN_KEY", ""), os.environ.get("PUBLIC_URL", ""))
    LOG.info("Dashboard: %s | Participants: %s/join", server.public_url, server.public_url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOG.info("Stopping quiz server.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
