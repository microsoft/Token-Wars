# Token Wars — Leaderboard

A tiny, dependency-free (Python standard library only) leaderboard for the Token Wars microhack.
Teams submit their run summaries with `python -m tokenwars run --submit` / `dotnet run -- run --submit`;
the big screen shows the ranking at `/` (auto-refresh every 10 s).

## Coach controls
- **Download submissions** downloads a timestamped JSON file containing **all** stored runs (valid and invalid, not just each team's best), including their strategies and metrics. Submitted source code is not stored.
- **Reset leaderboard** asks for confirmation and the admin key, then permanently removes **all teams' submissions and rankings** and refreshes the page. Download a backup first; there is no undo. No restart is needed.
- Both controls require `LEADERBOARD_ADMIN_KEY`, entered in a password dialog for each action. The page does not store the key. Use HTTPS outside localhost.
- Set a strong admin key **different from** `LEADERBOARD_SUBMIT_KEY` and keep it coach-only. Without an admin key, the buttons are disabled and the administrative API returns `503`.

## Ranking rules
- Only runs over the **full workload** (100 items, `MIN_ITEMS`) with a **pass rate ≥ 85 %** (`MIN_PASS_RATE`) are valid.
- Concurrency is not a validity requirement: full runs with `strategy.concurrency` set to `2` or `4` are eligible under the same rules. Lower concurrency does not require lowering `MIN_ITEMS`.
- Each team is ranked by its **best valid run**: lowest `cost_per_success_usd`, then highest pass rate, then lowest p95 latency.
- Teams without a valid run are listed below the ranking with the reason (for example "pass rate 78% below 85%").
- Mock runs (`"mock": true`) are refused with HTTP 400.
- "vs first run" = improvement of the best valid cost/success compared with the team's first submission (usually the baseline).

## API
| Method | Path | Description |
|---|---|---|
| POST | `/api/submissions` | JSON `{"team","language","variant","timestamp","mock","strategy","summary"}`; header `x-submit-key` if `LEADERBOARD_SUBMIT_KEY` is set. Returns `201` with rank/validity, `400` invalid/mock, `401` wrong key. |
| GET | `/api/leaderboard` | `{"ranked": [...], "invalid": [...], "teams", "total_submissions", ...}` |
| GET | `/api/submissions` | All stored submissions (without strategy), for debugging. |
| GET | `/api/submissions/export` | JSON attachment containing every stored submission, including strategy; requires `x-admin-key`. |
| POST | `/api/leaderboard/reset` | Permanently clear all submissions; requires `x-admin-key`. Returns `200` with `{"reset": true}`. |
| GET | `/healthz` | Liveness probe. |
| GET | `/` | Neon leaderboard page. |

Both administrative endpoints return `401` for a missing/wrong admin key, `503` when administration is disabled, or `500` on a storage failure.

## Configuration
| Variable | Default | Meaning |
|---|---|---|
| `PORT` | `8080` | Listen port |
| `DATA_DIR` | `./data` | Where `submissions.json` is stored |
| `LEADERBOARD_SUBMIT_KEY` | *(empty = open)* | Shared secret teams put in `TOKENWARS_LEADERBOARD_KEY` |
| `LEADERBOARD_ADMIN_KEY` | *(empty = disabled)* | Separate coach-only secret for downloading complete submissions and resetting the board |
| `MIN_PASS_RATE` | `0.85` | Validity gate |
| `MIN_ITEMS` | `100` | Minimum items for a valid run (set to `20` for a dry run with `--limit 20`) |

## Run locally
```bash
cd leaderboard
python3 server.py                                  # http://localhost:8080
# or with a submit key and a custom port
PORT=9000 LEADERBOARD_SUBMIT_KEY=tokenwars LEADERBOARD_ADMIN_KEY="replace-with-a-strong-coach-only-key" python3 server.py
```
PowerShell:
```powershell
$env:LEADERBOARD_SUBMIT_KEY = "tokenwars"
$env:LEADERBOARD_ADMIN_KEY = "<coach-only-key>"
python .\server.py
```
Test a submission (PowerShell users: use `Invoke-RestMethod`):
```bash
curl -X POST http://localhost:8080/api/submissions \
  -H "content-type: application/json" -H "x-submit-key: tokenwars" \
  -d '{"team":"demo","language":"python","variant":"starter","mock":false,
       "summary":{"items":100,"successes":90,"pass_rate":0.9,"total_cost_usd":3.2,"cost_per_success_usd":0.0356,
                  "latency_p95_ms":9000,"cache_hits_exact":0,"cache_hits_semantic":0}}'
```
Use the page's **Download submissions** and **Reset leaderboard** buttons to manage the event.

Run the dependency-free HTTP integration tests from the repository root:
```bash
python -m unittest discover -s leaderboard -p "test_*.py"
```

## Deploy to Azure Container Apps (one command)
From this folder (requires Azure CLI with the `containerapp` extension; the image is built in the cloud from the Dockerfile):
```bash
az containerapp up --name tokenwars-leaderboard --resource-group rg-tokenwars-leaderboard --location spaincentral --source . --ingress external --target-port 8080 --env-vars LEADERBOARD_SUBMIT_KEY=tokenwars
```
After deployment, enable the coach controls using a separate Container Apps secret:
```bash
az containerapp secret set --name tokenwars-leaderboard --resource-group rg-tokenwars-leaderboard --secrets leaderboard-admin-key=<coach-only-key>
az containerapp update --name tokenwars-leaderboard --resource-group rg-tokenwars-leaderboard --set-env-vars LEADERBOARD_ADMIN_KEY=secretref:leaderboard-admin-key
```
The command prints the app URL (`https://tokenwars-leaderboard.<env>.<region>.azurecontainerapps.io`). Give teams:
```
TOKENWARS_LEADERBOARD_URL=https://tokenwars-leaderboard.<env>.<region>.azurecontainerapps.io
TOKENWARS_LEADERBOARD_KEY=tokenwars
```
Notes
- Data lives in the container file system. Keep the app at exactly one replica so it is not lost or split during the event:
  `az containerapp update --name tokenwars-leaderboard --resource-group <rg> --min-replicas 1 --max-replicas 1`.
  For a permanent board, mount an Azure Files volume at `/app/data`.
- Download complete results after the event with **Download submissions**, or `GET /api/submissions/export` with `x-admin-key`. The existing public `GET /api/submissions` still omits strategies.
- Clean up: `az containerapp delete --name tokenwars-leaderboard --resource-group <rg>` (or delete the resource group).
