# Token Wars — Leaderboard

A tiny, dependency-free (Python standard library only) leaderboard for the Token Wars microhack.
Teams submit their run summaries with `python -m tokenwars run --submit` / `dotnet run -- run --submit`;
the big screen shows the ranking at `/` (auto-refresh every 10 s).

## Ranking rules
- Only runs over the **full workload** (100 items, `MIN_ITEMS`) with a **pass rate ≥ 85 %** (`MIN_PASS_RATE`) are valid.
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
| GET | `/healthz` | Liveness probe. |
| GET | `/` | Neon leaderboard page. |

## Configuration
| Variable | Default | Meaning |
|---|---|---|
| `PORT` | `8080` | Listen port |
| `DATA_DIR` | `./data` | Where `submissions.json` is stored |
| `LEADERBOARD_SUBMIT_KEY` | *(empty = open)* | Shared secret teams put in `TOKENWARS_LEADERBOARD_KEY` |
| `MIN_PASS_RATE` | `0.85` | Validity gate |
| `MIN_ITEMS` | `100` | Minimum items for a valid run (set to `20` for a dry run with `--limit 20`) |

## Run locally
```bash
cd leaderboard
python3 server.py                                  # http://localhost:8080
# or with a submit key and a custom port
PORT=9000 LEADERBOARD_SUBMIT_KEY=tokenwars python3 server.py
```
Test a submission (PowerShell users: use `Invoke-RestMethod`):
```bash
curl -X POST http://localhost:8080/api/submissions \
  -H "content-type: application/json" -H "x-submit-key: tokenwars" \
  -d '{"team":"demo","language":"python","variant":"starter","mock":false,
       "summary":{"items":100,"successes":90,"pass_rate":0.9,"total_cost_usd":3.2,"cost_per_success_usd":0.0356,
                  "latency_p95_ms":9000,"cache_hits_exact":0,"cache_hits_semantic":0}}'
```
Reset the board: stop the server and delete `data/submissions.json`.

## Deploy to Azure Container Apps (one command)
From this folder (requires Azure CLI with the `containerapp` extension; the image is built in the cloud from the Dockerfile):
```bash
az containerapp up --name tokenwars-leaderboard --resource-group rg-tokenwars-leaderboard --location spaincentral --source . --ingress external --target-port 8080 --env-vars LEADERBOARD_SUBMIT_KEY=tokenwars
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
- Download the results after the event: `GET /api/submissions`.
- Clean up: `az containerapp delete --name tokenwars-leaderboard --resource-group <rg>` (or delete the resource group).
