#!/usr/bin/env python3
"""Token Wars leaderboard with persistent, key-scoped storage.

Endpoints
  POST /api/submissions   submit a run summary (header x-submit-key if LEADERBOARD_SUBMIT_KEY is set)
  GET  /api/leaderboard   ranking: best VALID submission per team, lowest cost_per_success_usd first
  GET  /api/submissions   raw list of stored submissions (debugging)
  GET  /api/submissions/export  download all submissions, including strategies (x-admin-key)
  POST /api/leaderboard/reset   clear the selected board (x-admin-key + x-submit-key)
  GET  /api/leaderboards   list boards (public; the page loads them on open)
  POST /api/leaderboards   create a board (x-admin-key)
  GET  /healthz           liveness probe
  GET  /                  auto-refreshing leaderboard page

Environment
  PORT                    listen port (default 8080)
  DATA_DIR                local development storage (default ./data)
  LEADERBOARD_STORAGE_URL Blob service URL; when set, never fall back to local disk
  LEADERBOARD_SUBMIT_KEY  optional initial board key, registered at startup
  LEADERBOARD_ADMIN_KEY   separate coach-only secret for export and reset (disabled if unset)
  MIN_PASS_RATE           validity gate (default 0.85)
  MIN_ITEMS               minimum number of workload items for a valid run (default 100)
"""
import hmac
import json
import math
import os
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from azure.core.exceptions import AzureError
from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
from azure.storage.blob import BlobServiceClient

from storage import BlobStore, BoardNotFound, LocalStore, StorageConflict

PORT = int(os.environ.get("PORT", "8080"))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.getcwd(), "data"))
SUBMIT_KEY = os.environ.get("LEADERBOARD_SUBMIT_KEY", "")
ADMIN_KEY = os.environ.get("LEADERBOARD_ADMIN_KEY", "")
MIN_PASS_RATE = float(os.environ.get("MIN_PASS_RATE", "0.85"))
MIN_ITEMS = int(os.environ.get("MIN_ITEMS", "100"))
MAX_BODY = 1_000_000
STORAGE_URL = os.environ.get("LEADERBOARD_STORAGE_URL", "")

store = LocalStore(DATA_DIR)


def initialize_storage():
    global store
    if STORAGE_URL:
        credential = (DefaultAzureCredential() if os.environ.get("LEADERBOARD_STORAGE_AUTH") == "local"
                      else ManagedIdentityCredential(client_id=os.environ.get("AZURE_CLIENT_ID")))
        store = BlobStore(BlobServiceClient(STORAGE_URL, credential=credential))
    if SUBMIT_KEY:
        if SUBMIT_KEY == ADMIN_KEY:
            raise ValueError("The initial leaderboardKey must differ from LEADERBOARD_ADMIN_KEY")
        store.create(validate_key(SUBMIT_KEY))


# ----------------------------------------------------------------------------- domain
def validate_key(key):
    if not isinstance(key, str) or not key or len(key) > 128 or any(
            not 33 <= ord(char) <= 126 for char in key):
        raise ValueError("leaderboardKey must contain 1-128 printable ASCII characters without spaces")
    return key


def _num(d, key, default=0.0):
    v = d.get(key, default)
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def validate_submission(body):
    """Return (record, error). The record is what we store."""
    if not isinstance(body, dict):
        return None, "body must be a JSON object"
    if body.get("mock") is True:
        return None, "mock runs are not accepted on the leaderboard — run against Azure (TOKENWARS_MOCK=0)"
    team = str(body.get("team") or "").strip()[:40]
    if not team or team == "team-name":
        return None, "missing team name (set TOKENWARS_TEAM)"
    summary = body.get("summary")
    if not isinstance(summary, dict):
        return None, "missing summary object"
    for key in ("items", "successes", "pass_rate", "total_cost_usd", "cost_per_success_usd",
                "input_tokens", "cached_input_tokens", "output_tokens", "cache_hits_exact",
                "cache_hits_semantic", "escalations", "latency_p50_ms", "latency_p95_ms", "judge_cost_usd"):
        if key not in summary:
            continue
        value = summary[key]
        try:
            if not math.isfinite(float(value)) or float(value) < 0:
                return None, f"summary.{key} must be a finite non-negative number"
        except (TypeError, ValueError, OverflowError):
            return None, f"summary.{key} must be a finite non-negative number"
    for k in ("items", "pass_rate", "total_cost_usd", "cost_per_success_usd"):
        if k not in summary:
            return None, f"summary.{k} is required"
    items = int(_num(summary, "items"))
    pass_rate = _num(summary, "pass_rate")
    if pass_rate > 1:
        return None, "summary.pass_rate must be between 0 and 1"
    reasons = []
    if pass_rate < MIN_PASS_RATE:
        reasons.append(f"pass rate {pass_rate:.0%} below {MIN_PASS_RATE:.0%}")
    if items < MIN_ITEMS:
        reasons.append(f"partial run ({items}/{MIN_ITEMS} items)")
    rec = {
        "id": uuid.uuid4().hex[:12],
        "received_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "team": team,
        "language": str(body.get("language") or "?")[:10],
        "variant": str(body.get("variant") or "?")[:12],
        "timestamp": str(body.get("timestamp") or "")[:40],
        "strategy": body.get("strategy") if isinstance(body.get("strategy"), dict) else {},
        "summary": {
            "items": items,
            "successes": int(_num(summary, "successes")),
            "pass_rate": pass_rate,
            "total_cost_usd": _num(summary, "total_cost_usd"),
            "cost_per_success_usd": _num(summary, "cost_per_success_usd"),
            "input_tokens": int(_num(summary, "input_tokens")),
            "cached_input_tokens": int(_num(summary, "cached_input_tokens")),
            "output_tokens": int(_num(summary, "output_tokens")),
            "cache_hits_exact": int(_num(summary, "cache_hits_exact")),
            "cache_hits_semantic": int(_num(summary, "cache_hits_semantic")),
            "escalations": int(_num(summary, "escalations")),
            "latency_p50_ms": _num(summary, "latency_p50_ms"),
            "latency_p95_ms": _num(summary, "latency_p95_ms"),
            "judge_cost_usd": _num(summary, "judge_cost_usd"),
            "calls_by_model": summary.get("calls_by_model") if isinstance(summary.get("calls_by_model"), dict) else {},
        },
        "valid": not reasons,
        "invalid_reason": "; ".join(reasons) or None,
    }
    return rec, None


def _rank_key(s):
    # lowest cost per success, then highest pass rate, then lowest p95 latency, then earliest submission
    m = s["summary"]
    return (m["cost_per_success_usd"], -m["pass_rate"], m["latency_p95_ms"], s["received_at"])


def build_leaderboard(subs):
    by_team = {}
    for s in subs:
        by_team.setdefault(s["team"].lower(), []).append(s)
    ranked, invalid = [], []
    for runs in by_team.values():
        runs.sort(key=lambda s: s["received_at"])
        first = runs[0]["summary"]["cost_per_success_usd"]
        valid_runs = [s for s in runs if s["valid"]]
        best = min(valid_runs, key=_rank_key) if valid_runs else max(
            runs, key=lambda s: (s["summary"]["pass_rate"], s["received_at"]))
        m = best["summary"]
        row = {
            "team": best["team"],
            "language": best["language"],
            "variant": best["variant"],
            "cost_per_success_usd": m["cost_per_success_usd"],
            "pass_rate": m["pass_rate"],
            "total_cost_usd": m["total_cost_usd"],
            "latency_p95_ms": m["latency_p95_ms"],
            "cache_hits": m["cache_hits_exact"] + m["cache_hits_semantic"],
            "cache_hits_exact": m["cache_hits_exact"],
            "cache_hits_semantic": m["cache_hits_semantic"],
            "escalations": m["escalations"],
            "items": m["items"],
            "submissions": len(runs),
            "first_cost_per_success_usd": first,
            "improvement_pct": round((first - m["cost_per_success_usd"]) / first * 100, 1) if first > 0 else None,
            "submitted_at": best["received_at"],
            "valid": best["valid"],
            "invalid_reason": best["invalid_reason"],
        }
        (ranked if valid_runs else invalid).append(row)
    ranked.sort(key=lambda r: (r["cost_per_success_usd"], -r["pass_rate"], r["latency_p95_ms"], r["submitted_at"]))
    for i, r in enumerate(ranked, 1):
        r["rank"] = i
    invalid.sort(key=lambda r: (-r["pass_rate"], r["cost_per_success_usd"]))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "min_pass_rate": MIN_PASS_RATE,
        "min_items": MIN_ITEMS,
        "total_submissions": len(subs),
        "teams": len(by_team),
        "ranked": ranked,
        "invalid": invalid,
    }


# ----------------------------------------------------------------------------- http
class Handler(BaseHTTPRequestHandler):
    server_version = "TokenWarsLeaderboard/1.0"

    def log_message(self, fmt, *args):  # concise access log
        print(f"[{self.log_date_time_string()}] {self.address_string()} {fmt % args}", flush=True)

    def _send(self, code, payload, content_type="application/json; charset=utf-8", headers=None):
        body = payload if isinstance(payload, bytes) else json.dumps(payload, indent=1).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _authorize_admin(self):
        if not ADMIN_KEY:
            self._send(503, {"error": "Administration is disabled. Configure LEADERBOARD_ADMIN_KEY on the server."})
            return False
        key = self.headers.get("x-admin-key", "")
        if not hmac.compare_digest(key.encode("utf-8"), ADMIN_KEY.encode("utf-8")):
            self._send(401, {"error": "Invalid or missing admin key."})
            return False
        return True

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, x-submit-key, x-admin-key")
        self.end_headers()

    def do_GET(self):
        self._handle(self._get)

    def do_POST(self):
        self._handle(self._post)

    def _handle(self, action):
        try:
            action()
        except BoardNotFound:
            self._send(404, {"error": "Unknown leaderboardKey. Ask the coach to create it first."})
        except StorageConflict as exc:
            self.log_error("Storage contention: %s", exc)
            self._send(503, {"error": "Leaderboard is busy. Please retry."})
        except (OSError, ValueError, KeyError, TypeError, AzureError) as exc:
            self.log_error("Leaderboard storage operation failed: %s", exc)
            self._send(500, {"error": "Could not access leaderboard storage. Check the server logs."})

    def _selected_key(self):
        key = self.headers.get("x-submit-key", "")
        try:
            return validate_key(key)
        except ValueError:
            self._send(401, {"accepted": False, "error": "invalid or missing x-submit-key"})
            return None

    def _body(self):
        try:
            body = json.loads(self._request_body.decode("utf-8"))
            if not isinstance(body, dict):
                raise ValueError()
            return body
        except (ValueError, UnicodeDecodeError):
            self._send(400, {"accepted": False, "error": "invalid JSON or empty/too large body"})
            return None

    def _get(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            page = PAGE.replace("__ADMIN_ENABLED__", json.dumps(bool(ADMIN_KEY)))
            return self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
        if path == "/healthz":
            return self._send(200, {"status": "ok"})
        if path == "/api/leaderboards":
            return self._send(200, {"leaderboardKeys": store.list_keys()})
        if path not in ("/api/leaderboard", "/api/submissions", "/api/submissions/export"):
            return self._send(404, {"error": "not found"})
        if path == "/api/submissions/export" and not self._authorize_admin():
            return
        key = self._selected_key()
        if key is None:
            return
        subs = store.load(key)
        if path == "/api/leaderboard":
            return self._send(200, build_leaderboard(subs))
        if path == "/api/submissions":
            return self._send(200, [{k: v for k, v in s.items() if k != "strategy"} for s in subs])
        if path == "/api/submissions/export":
            filename = datetime.now(timezone.utc).strftime("tokenwars-submissions-%Y%m%d-%H%M%S.json")
            return self._send(200, subs, headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    def _post(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._send(400, {"error": "invalid Content-Length"})
        if length < 0 or length > MAX_BODY:
            return self._send(400, {"error": "body too large or invalid Content-Length"})
        # Consume bounded bodies before rejecting auth to avoid TCP resets on Windows.
        self._request_body = self.rfile.read(length)
        path = self.path.split("?", 1)[0]
        if path == "/api/leaderboards":
            if not self._authorize_admin():
                return
            body = self._body()
            if body is None:
                return
            try:
                key = validate_key(body.get("leaderboardKey") if isinstance(body, dict) else None)
                if key == ADMIN_KEY:
                    raise ValueError("leaderboardKey must be different from the admin key")
            except ValueError as exc:
                return self._send(400, {"error": str(exc)})
            created = store.create(key)
            return self._send(201 if created else 409, {"created": created} if created else
                              {"error": "That leaderboardKey already exists."})
        if path == "/api/leaderboard/reset":
            if not self._authorize_admin():
                return
            key = self._selected_key()
            if key is None:
                return
            store.reset(key)
            return self._send(200, {"reset": True})
        if path != "/api/submissions":
            return self._send(404, {"error": "not found"})
        key = self._selected_key()
        if key is None:
            return
        body = self._body()
        if body is None:
            return
        rec, err = validate_submission(body)
        if err:
            return self._send(400, {"accepted": False, "error": err})
        subs = store.append(key, rec)
        board = build_leaderboard(subs)
        row = next((r for r in board["ranked"] if r["team"].lower() == rec["team"].lower()), None)
        msg = (f"Accepted. {rec['team']} is ranked #{row['rank']} of {len(board['ranked'])} "
               f"(best cost/success ${row['cost_per_success_usd']:.5f})." if row and rec["valid"] else
               f"Accepted but INVALID for ranking: {rec['invalid_reason']}.")
        return self._send(201, {"accepted": True, "id": rec["id"], "valid": rec["valid"],
                                "invalid_reason": rec["invalid_reason"], "rank": row["rank"] if row else None,
                                "message": msg})


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Token Wars — Leaderboard</title>
<style>
:root{--bg:#07060f;--panel:#0f0d22cc;--cyan:#00f0ff;--pink:#ff2bd6;--lime:#b6ff3b;--amber:#ffb800;--red:#ff4d6d;--txt:#e8e6ff;--dim:#8a86b8}
*{box-sizing:border-box}html,body{margin:0;min-height:100%;background:var(--bg);color:var(--txt);font-family:"Segoe UI",system-ui,-apple-system,sans-serif}
body{background:radial-gradient(1200px 600px at 10% -10%,#2b0a4a 0%,transparent 60%),radial-gradient(900px 500px at 110% 10%,#002b3a 0%,transparent 60%),var(--bg);overflow-x:hidden}
.grid{position:fixed;inset:0;pointer-events:none;background-image:linear-gradient(#ff2bd614 1px,transparent 1px),linear-gradient(90deg,#00f0ff12 1px,transparent 1px);background-size:48px 48px;mask-image:linear-gradient(to bottom,transparent,#000 30%,#000 70%,transparent);animation:drift 20s linear infinite}
@keyframes drift{to{background-position:0 48px,48px 0}}
header{text-align:center;padding:34px 16px 8px;position:relative}
h1{margin:0;font-size:clamp(40px,7vw,86px);letter-spacing:.12em;font-weight:900;text-transform:uppercase;
 background:linear-gradient(90deg,var(--cyan),var(--pink),var(--amber));-webkit-background-clip:text;background-clip:text;color:transparent;
 filter:drop-shadow(0 0 12px #ff2bd688) drop-shadow(0 0 28px #00f0ff55);animation:flicker 6s infinite}
@keyframes flicker{0%,92%,100%{opacity:1}93%{opacity:.55}94%{opacity:1}96%{opacity:.7}}
.sub{color:var(--dim);letter-spacing:.3em;text-transform:uppercase;font-size:13px;margin-top:6px}
.stats{display:flex;gap:14px;justify-content:center;flex-wrap:wrap;margin:22px auto 8px;max-width:1200px;padding:0 16px}
.stat{background:var(--panel);border:1px solid #ffffff1a;border-radius:14px;padding:12px 20px;min-width:170px;text-align:center;box-shadow:0 0 18px #00f0ff22 inset}
.stat b{display:block;font-size:26px;color:var(--cyan);text-shadow:0 0 10px var(--cyan)}.stat span{font-size:11px;color:var(--dim);letter-spacing:.18em;text-transform:uppercase}
main{max-width:1280px;margin:12px auto 40px;padding:0 16px}
.actions{display:flex;justify-content:flex-end;gap:12px;flex-wrap:wrap;margin:18px 0 8px}
button{font:inherit;font-weight:600;color:var(--cyan);background:var(--panel);border:1px solid var(--cyan);border-radius:8px;padding:10px 16px;cursor:pointer}
button.danger{color:var(--red);border-color:var(--red)}button:disabled{opacity:.45;cursor:not-allowed}
button:focus-visible,input:focus-visible,select:focus-visible{outline:2px solid var(--amber);outline-offset:3px}
.board-controls{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-top:18px}
.board-controls input,.board-controls select{font:inherit;color:var(--txt);background:#0f0d22;border:1px solid var(--dim);border-radius:6px;padding:10px;max-width:100%}
.action-status{min-height:1.5em;text-align:right;font-size:13px}
dialog{color:var(--txt);background:#0f0d22;border:1px solid var(--cyan);border-radius:14px;padding:24px;max-width:440px;width:calc(100% - 32px)}
dialog::backdrop{background:#000a}dialog h2{color:var(--cyan);margin:0 0 16px}
dialog p{line-height:1.5}dialog label{display:block;margin-bottom:8px}
dialog input{width:100%;font:inherit;color:var(--txt);background:var(--bg);border:1px solid var(--dim);border-radius:6px;padding:10px}
dialog .actions{margin-bottom:0}
table{width:100%;border-collapse:separate;border-spacing:0 8px}
th{font-size:11px;letter-spacing:.16em;text-transform:uppercase;color:var(--dim);text-align:right;padding:6px 12px;font-weight:600}
th:nth-child(-n+3){text-align:left}
td{background:var(--panel);padding:14px 12px;text-align:right;font-variant-numeric:tabular-nums;border-top:1px solid #ffffff12;border-bottom:1px solid #ffffff12;backdrop-filter:blur(6px)}
td:first-child{border-left:1px solid #ffffff12;border-radius:12px 0 0 12px;text-align:center;width:64px}
td:last-child{border-right:1px solid #ffffff12;border-radius:0 12px 12px 0}
td:nth-child(2),td:nth-child(3){text-align:left}
tr.row{transition:transform .25s}tr.row:hover td{background:#1a1640dd}
.rank{font-size:24px;font-weight:900;color:var(--dim)}
tr.r1 td{box-shadow:0 0 22px #ffb80055;border-color:#ffb80088}tr.r1 .rank{color:var(--amber);text-shadow:0 0 12px var(--amber)}
tr.r2 .rank{color:#d7e3ff;text-shadow:0 0 10px #d7e3ff}tr.r3 .rank{color:#ff9a5a;text-shadow:0 0 10px #ff9a5a}
.team{font-weight:800;font-size:18px}.lang{display:inline-block;font-size:11px;padding:3px 8px;border-radius:99px;border:1px solid;letter-spacing:.1em;text-transform:uppercase}
.python{color:var(--lime);border-color:#b6ff3b88}.dotnet{color:var(--pink);border-color:#ff2bd688}
.cps{font-size:20px;font-weight:900;color:var(--cyan);text-shadow:0 0 10px #00f0ffaa}
.good{color:var(--lime)}.bad{color:var(--red)}.dim{color:var(--dim)}
.bar{height:6px;border-radius:9px;background:#ffffff14;margin-top:6px;overflow:hidden}.bar i{display:block;height:100%;background:linear-gradient(90deg,var(--pink),var(--cyan))}
h2{font-size:13px;letter-spacing:.25em;text-transform:uppercase;color:var(--red);margin:34px 0 4px;text-shadow:0 0 8px #ff4d6d88}
.invalid td{opacity:.6}.empty{text-align:center;color:var(--dim);padding:40px;font-size:18px}
footer{text-align:center;color:var(--dim);font-size:12px;padding:0 0 30px}
.pulse{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--lime);box-shadow:0 0 10px var(--lime);margin-right:6px;animation:p 1.6s infinite}@keyframes p{50%{opacity:.25}}
</style></head><body><div class="grid"></div>
<header><h1>Token Wars</h1><div class="sub">Build · Route · Optimize — lowest cost per successful answer wins</div></header>
<section class="stats">
 <div class="stat"><b id="s-teams">–</b><span>teams</span></div>
 <div class="stat"><b id="s-subs">–</b><span>submissions</span></div>
 <div class="stat"><b id="s-best">–</b><span>best cost / success</span></div>
 <div class="stat"><b id="s-impr">–</b><span>best improvement</span></div>
</section>
<main>
 <div class="board-controls">
  <label for="leaderboard-select">Leaderboard</label>
  <select id="leaderboard-select" disabled><option value="">Select a leaderboard</option></select>
  <button id="add-board" type="button">Add leaderboardKey</button>
 </div>
 <div class="actions" aria-label="Leaderboard administration">
  <button id="download" type="button">Download submissions</button>
  <button id="reset" type="button" class="danger">Reset leaderboard</button>
 </div>
 <div id="action-status" class="action-status dim" role="status" aria-live="polite"></div>
 <table><thead><tr><th>#</th><th>Team</th><th>Lang</th><th>Cost / success</th><th>Pass rate</th><th>Total cost</th><th>p95 latency</th><th>Cache hits</th><th>Runs</th><th>vs first run</th></tr></thead>
 <tbody id="ranked"><tr><td colspan="10" class="empty">Waiting for the first valid submission…</td></tr></tbody></table>
 <div id="inv-wrap" style="display:none"><h2>Below the quality bar (not ranked)</h2>
 <table><tbody id="invalid"></tbody></table></div>
</main>
<dialog id="admin-dialog" aria-labelledby="admin-title" aria-describedby="admin-description">
 <form id="admin-form">
  <h2 id="admin-title">Download submissions</h2>
  <p id="admin-description"></p>
  <div id="new-key-field" hidden>
   <label for="new-key">New leaderboardKey (share only with this event's participants)</label>
   <input id="new-key" type="text" maxlength="128" autocomplete="off" spellcheck="false" autocapitalize="off">
  </div>
  <label for="admin-key">Admin key</label>
  <input id="admin-key" type="password" required autocomplete="off" autofocus>
  <p id="admin-error" class="bad" role="alert"></p>
  <div class="actions">
   <button id="admin-cancel" type="button">Cancel</button>
   <button id="admin-confirm" type="submit">Download</button>
  </div>
 </form>
</dialog>
<footer><span class="pulse"></span>live · refreshes every 10 s · <span id="updated">–</span> · valid = pass rate ≥ <span id="gate">85%</span> on the full workload</footer>
<script>
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const usd=(v,d)=>"$"+Number(v||0).toFixed(d);
const pct=v=>(Number(v||0)*100).toFixed(1)+"%";
function row(r,invalid){
  const impr=r.improvement_pct==null?'<span class="dim">–</span>':(r.improvement_pct>0?`<span class="good">▼ ${r.improvement_pct.toFixed(1)}%</span>`:(r.improvement_pct<0?`<span class="bad">▲ ${(-r.improvement_pct).toFixed(1)}%</span>`:'<span class="dim">±0%</span>'));
  const lang=(r.language||"?").toLowerCase();
  const cls=invalid?'row invalid':'row r'+r.rank;
  return `<tr class="${cls}"><td><span class="rank">${invalid?'✖':r.rank}</span></td>
  <td><span class="team">${esc(r.team)}</span>${invalid?`<div class="bad" style="font-size:12px">${esc(r.invalid_reason)}</div>`:''}</td>
  <td><span class="lang ${esc(lang)}">${esc(lang)}</span></td>
  <td><span class="cps">${usd(r.cost_per_success_usd,5)}</span></td>
  <td><span class="${r.pass_rate>=0.85?'good':'bad'}">${pct(r.pass_rate)}</span><div class="bar"><i style="width:${Math.min(100,r.pass_rate*100)}%"></i></div></td>
  <td>${usd(r.total_cost_usd,4)}</td><td>${Math.round(r.latency_p95_ms||0).toLocaleString()} ms</td>
  <td>${r.cache_hits} <span class="dim" style="font-size:11px">(${r.cache_hits_exact}e/${r.cache_hits_semantic}s)</span></td>
  <td>${r.submissions}</td><td>${impr}</td></tr>`;
}
let refreshVersion=0;
let selectedKey='';
function renderBoard(d){
    document.getElementById('s-teams').textContent=d.teams;
    document.getElementById('s-subs').textContent=d.total_submissions;
    document.getElementById('s-best').textContent=d.ranked.length?usd(d.ranked[0].cost_per_success_usd,5):'–';
    const imps=d.ranked.map(r=>r.improvement_pct).filter(v=>v!=null);
    document.getElementById('s-impr').textContent=imps.length?Math.max(...imps).toFixed(1)+'%':'–';
    document.getElementById('gate').textContent=Math.round(d.min_pass_rate*100)+'%';
    document.getElementById('ranked').innerHTML=d.ranked.length?d.ranked.map(r=>row(r,false)).join(''):'<tr><td colspan="10" class="empty">Waiting for the first valid submission…</td></tr>';
    document.getElementById('inv-wrap').style.display=d.invalid.length?'block':'none';
    document.getElementById('invalid').innerHTML=d.invalid.map(r=>row(r,true)).join('');
}
async function refresh(){
  const version=++refreshVersion;
  if(!selectedKey){
    document.getElementById('updated').textContent='select a leaderboard';
    return false;
  }
  try{
    const response=await fetch('/api/leaderboard',{headers:{'x-submit-key':selectedKey},cache:'no-store'});
    if(!response.ok){
      const error=await response.json();
      throw new Error(error.error||'Could not refresh leaderboard.');
    }
    const d=await response.json();
    if(version!==refreshVersion)return false;
    renderBoard(d);
    document.getElementById('updated').textContent='updated '+new Date().toLocaleTimeString();
    return true;
  }catch(e){
    if(version===refreshVersion)document.getElementById('updated').textContent=e.message+' – retrying';
    return false;
  }
}
const adminEnabled=__ADMIN_ENABLED__;
const downloadButton=document.getElementById('download');
const resetButton=document.getElementById('reset');
const addButton=document.getElementById('add-board');
const boardSelect=document.getElementById('leaderboard-select');
const newKey=document.getElementById('new-key');
const adminDialog=document.getElementById('admin-dialog');
const adminKey=document.getElementById('admin-key');
const adminConfirm=document.getElementById('admin-confirm');
const adminCancel=document.getElementById('admin-cancel');
const adminError=document.getElementById('admin-error');
const actionStatus=document.getElementById('action-status');
let adminAction=null;
let adminBusy=false;
let actionBoard='';
function updateControls(){
  downloadButton.disabled=resetButton.disabled=!adminEnabled||adminBusy||!selectedKey;
  addButton.disabled=!adminEnabled||adminBusy;
  boardSelect.disabled=adminBusy||!boardSelect.options.length||!boardSelect.value;
}
const SELECTION_KEY='tokenwars.leaderboard';
function rememberSelection(key){
  try{key?sessionStorage.setItem(SELECTION_KEY,key):sessionStorage.removeItem(SELECTION_KEY);}catch{}
}
function rememberedSelection(){
  try{return sessionStorage.getItem(SELECTION_KEY)||'';}catch{return '';}
}
function selectBoard(key){
  selectedKey=key;
  rememberSelection(key);
  ++refreshVersion;
  actionStatus.textContent='';
  renderBoard({teams:0,total_submissions:0,ranked:[],invalid:[],min_pass_rate:0.85});
  updateControls();
  return refresh();
}
function addOption(key,label=key){
  if(!Array.from(boardSelect.options).some(option=>option.value===key)){
    boardSelect.add(new Option(label,key));
  }
}
boardSelect.addEventListener('change',()=>selectBoard(boardSelect.value));
async function loadBoards(){
  try{
    const response=await fetch('/api/leaderboards',{cache:'no-store'});
    if(!response.ok){
      const error=await response.json();
      throw new Error(error.error||`Could not load leaderboards (${response.status}).`);
    }
    const keys=(await response.json()).leaderboardKeys;
    if(!keys.length){
      boardSelect.replaceChildren(new Option('No leaderboards yet',''));
      await selectBoard('');
      actionStatus.textContent='No leaderboards yet. Use Add leaderboardKey.';
      return false;
    }
    boardSelect.replaceChildren();
    keys.forEach(boardKey=>addOption(boardKey));
    const saved=rememberedSelection();
    const target=keys.includes(saved)?saved:keys[0];
    boardSelect.value=target;
    return selectBoard(target);
  }catch(error){
    actionStatus.textContent=error.message;
    return false;
  }
}
function showAdmin(action){
  adminAction=action;
  actionBoard=selectedKey;
  const resetting=action==='reset';
  const creating=action==='create';
  const titles={reset:'Reset leaderboard',download:'Download submissions',create:'Add leaderboardKey'};
  document.getElementById('admin-title').textContent=titles[action];
  document.getElementById('admin-description').textContent={
    reset:'Permanently delete ALL submissions in the SELECTED leaderboard only? Other leaderboards are not affected. This cannot be undone. Download a backup first.',
    download:'Download all submissions in the selected leaderboard as JSON, including strategies and valid and invalid runs.',
    create:'Create an empty private container, ready for participant submissions. Existing leaderboards will not be changed.'
  }[action];
  document.getElementById('new-key-field').hidden=!creating;
  newKey.required=creating;
  newKey.value='';
  adminConfirm.textContent=resetting?'Confirm reset':titles[action];
  adminConfirm.classList.toggle('danger',resetting);
  adminError.textContent='';
  adminKey.value='';
  adminDialog.showModal();
}
updateControls();
downloadButton.addEventListener('click',()=>showAdmin('download'));
resetButton.addEventListener('click',()=>showAdmin('reset'));
addButton.addEventListener('click',()=>showAdmin('create'));
adminCancel.addEventListener('click',()=>adminDialog.close());
adminDialog.addEventListener('cancel',event=>{if(adminBusy)event.preventDefault();});
adminDialog.addEventListener('close',()=>{adminKey.value='';newKey.value='';});
document.getElementById('admin-form').addEventListener('submit',async event=>{
  event.preventDefault();
  if(adminBusy)return;
  adminBusy=true;
  adminConfirm.disabled=adminCancel.disabled=adminKey.disabled=newKey.disabled=true;
  updateControls();
  adminError.textContent='';
  actionStatus.textContent='';
  const key=adminKey.value;
  adminKey.value='';
  try{
    const resetting=adminAction==='reset';
    const creating=adminAction==='create';
    const createdKey=newKey.value;
    const response=await fetch(creating?'/api/leaderboards':resetting?'/api/leaderboard/reset':'/api/submissions/export',{
      method:resetting||creating?'POST':'GET',
      headers:{'x-admin-key':key,...(creating?{'Content-Type':'application/json'}:{'x-submit-key':actionBoard})},
      ...(creating?{body:JSON.stringify({leaderboardKey:createdKey})}:{}),cache:'no-store'
    });
    if(!response.ok){
      const error=await response.json();
      throw new Error(error.error||`Request failed (${response.status}).`);
    }
    if(creating){
      Array.from(boardSelect.options).filter(option=>!option.value).forEach(option=>option.remove());
      addOption(createdKey);
      boardSelect.value=createdKey;
      const refreshed=await selectBoard(createdKey);
      actionStatus.textContent=refreshed?'Leaderboard created and selected.':'Leaderboard created, but the page could not refresh.';
    }else if(resetting){
      const refreshed=await refresh();
      actionStatus.textContent=refreshed?'Selected leaderboard reset.':
        'Leaderboard reset, but the page could not refresh. Reload the page.';
    }else{
      const blob=await response.blob();
      const url=URL.createObjectURL(blob);
      const link=document.createElement('a');
      const filename=/filename="([^"]+)"/.exec(response.headers.get('Content-Disposition')||'');
      link.href=url;
      link.download=filename?filename[1]:'tokenwars-submissions.json';
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(()=>URL.revokeObjectURL(url),1000);
      actionStatus.textContent='Submissions download started.';
    }
    adminDialog.close();
  }catch(error){
    adminError.textContent=error.message;
  }finally{
    adminBusy=false;
    adminConfirm.disabled=adminCancel.disabled=adminKey.disabled=newKey.disabled=false;
    updateControls();
    if(adminDialog.open)adminKey.focus();
  }
});
loadBoards().then(()=>{
  if(!adminEnabled)actionStatus.textContent='Administration disabled: configure LEADERBOARD_ADMIN_KEY on the server.';
});
setInterval(refresh,10000);
</script></body></html>
"""


def main():
    initialize_storage()
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Token Wars leaderboard on http://0.0.0.0:{PORT}  "
          f"(storage: {'Azure Blob' if STORAGE_URL else DATA_DIR})", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
