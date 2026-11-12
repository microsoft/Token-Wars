# Token Wars — Leaderboard

A small Python leaderboard for the Token Wars microhack, backed by Azure Blob Storage.
Teams submit their run summaries with `python -m tokenwars run --submit` / `dotnet run -- run --submit`;
the big screen shows the ranking at `/` (auto-refresh every 10 s).

## Coach controls
- The leaderboard selector is filled automatically when the page opens (no admin key needed). Selecting a key changes the displayed ranking, statistics, export and reset scope, and the selection is remembered for the browser tab session.
- **Warning:** the list is public, so anyone with the URL can see every leaderboardKey and submit results to any board. Rely on the URL not being shared widely, or restrict ingress, if that matters for your event.
- **Add leaderboardKey** asks for the admin key and a new event key, creates an empty private container and selects it. Duplicate creation returns `409` without modifying existing data. Share each key only with that event's participants.
- The ranking shown is the board selected in the combo; participants can open the page and pick their event. Existing Python/.NET submissions continue using `TOKENWARS_LEADERBOARD_KEY` as the `x-submit-key` header.
- **Download submissions** downloads a timestamped JSON file containing **all** stored runs in the selected leaderboard (valid and invalid, not just each team's best), including their strategies and metrics. Submitted source code is not stored.
- **Reset leaderboard** asks for confirmation and the admin key, then permanently removes **only the selected leaderboard's submissions and rankings**. The empty container/key remains ready for new submissions. Other leaderboards are unaffected. Download a backup first; there is no undo. No restart is needed.
- Coach actions require `LEADERBOARD_ADMIN_KEY`, entered in a password dialog for each action. Admin credentials are cleared after use and never stored. The selected leaderboardKey is kept in the tab's `sessionStorage` (not URLs or `localStorage`), so a reload restores it; closing the tab forgets it. Use HTTPS outside localhost.
- Set a strong admin key **different from** `LEADERBOARD_SUBMIT_KEY` and keep it coach-only. Without an admin key, the buttons are disabled and the administrative API returns `503`.

## Ranking rules
- Only runs over the **full workload** (100 items, `MIN_ITEMS`) with a **pass rate ≥ 85 %** (`MIN_PASS_RATE`) are valid.
- Concurrency is not a validity requirement: full runs with `strategy.concurrency` set to `2` or `4` are eligible under the same rules. Lower concurrency does not require lowering `MIN_ITEMS`.
- Each team is ranked by its **best valid run**: lowest `cost_per_success_usd`, then highest pass rate, then lowest p95 latency.
- Teams without a valid run are listed below the ranking with the reason (for example "pass rate 78% below 85%").
- Mock runs (`"mock": true`) are refused with HTTP 400.
- "vs first run" = improvement of the best valid cost/success compared with the team's first submission (usually the baseline).

## API
All scoped endpoints require `x-submit-key: <registered-leaderboardKey>`, including reads, export and reset.
There is no implicit default or cross-board export/reset. Missing/malformed keys return `401`; unknown keys return `404`.
Only the coach can register new keys; a participant submission never creates a container.

| Method | Path | Description |
|---|---|---|
| GET | `/api/leaderboards` | `{"leaderboardKeys": [...]}`; public (used by the page selector). |
| POST | `/api/leaderboards` | JSON `{"leaderboardKey":"<new-event-key>"}`; requires `x-admin-key`. Returns `201` when created, `409` if already registered, `400` for an invalid key. |
| POST | `/api/submissions` | JSON `{"team","language","variant","timestamp","mock","strategy","summary"}`. Returns `201` with rank/validity after durable storage, `400` for malformed/mock runs. |
| GET | `/api/leaderboard` | `{"ranked": [...], "invalid": [...], "teams", "total_submissions", ...}` |
| GET | `/api/submissions` | All stored submissions (without strategy), for debugging. |
| GET | `/api/submissions/export` | JSON attachment containing every stored submission, including strategy; requires `x-admin-key`. |
| POST | `/api/leaderboard/reset` | Permanently clear the selected board only; requires `x-admin-key`. Returns `200` with `{"reset": true}`. |
| GET | `/healthz` | Liveness probe. |
| GET | `/` | Neon leaderboard page. |

Administrative endpoints return `401` for a missing/wrong admin key or `503` when administration is disabled.
Storage failures return `500` and are logged, never acknowledged as successful submissions. Exhausted concurrent-write retries return `503`.
Keys are case-sensitive, 1-128 printable ASCII characters without spaces, and must differ from the admin key.

## Persistence
- Azure mode uses **one private container per leaderboardKey**, named `twlb-<SHA-256 prefix>` to support keys that are not valid Azure container names. Its private metadata records the key for the coach's selector.
- Accepted runs, including below-quality-bar runs, live in that container's `submissions.json`. Validity/ranking rules are unchanged. Empty containers are valid leaderboards even before the first submission.
- No expiry, TTL, background cleanup or lifecycle deletion is configured. Restarting, scaling or redeploying the app does not clear results. Conditional ETag writes prevent concurrent replicas from overwriting one another's accepted runs.
- The application removes submissions only on an explicit, authenticated **Reset leaderboard**. Azure owners can still delete data outside the application; Terraform guards against accidental storage destruction but cannot prevent owner actions.
- When `LEADERBOARD_STORAGE_URL` is configured, storage/auth failures **never fall back to ephemeral local disk**.
- Without that variable, local development uses atomic per-key files in `DATA_DIR` and requires exactly one server process. This mode is not persistent in Container Apps.
- Upgrading an old deployment: **export/download its results before replacing the container**. The old single-board `submissions.json` is not automatically migrated; retain the exported JSON as your archive. New submissions use the new key-scoped storage.

## Configuration
| Variable | Default | Meaning |
|---|---|---|
| `PORT` | `8080` | Listen port |
| `DATA_DIR` | `./data` | Per-key local files (development only) |
| `LEADERBOARD_STORAGE_URL` | *(empty = local development)* | Blob service endpoint, e.g. `https://<account>.blob.core.windows.net` |
| `LEADERBOARD_STORAGE_AUTH` | *(empty = managed identity)* | Set to `local` only when testing against Azure with `az login` / DefaultAzureCredential |
| `AZURE_CLIENT_ID` | *(empty = system-assigned identity)* | User-assigned managed identity client ID |
| `LEADERBOARD_SUBMIT_KEY` | *(empty = no initial board)* | Optional coach-configured initial key, created idempotently at startup; never clears an existing board |
| `LEADERBOARD_ADMIN_KEY` | *(empty = disabled)* | Separate coach-only secret for downloading complete submissions and resetting the board |
| `MIN_PASS_RATE` | `0.85` | Validity gate |
| `MIN_ITEMS` | `100` | Minimum items for a valid run (set to `20` for a dry run with `--limit 20`) |

## Run locally
```bash
cd leaderboard
pip install -r requirements.txt
python3 server.py                                  # http://localhost:8080
# or with a submit key and a custom port
PORT=9000 LEADERBOARD_SUBMIT_KEY=tokenwars LEADERBOARD_ADMIN_KEY="replace-with-a-strong-coach-only-key" python3 server.py
```
PowerShell:
```powershell
$env:LEADERBOARD_SUBMIT_KEY = "tokenwars"
$env:LEADERBOARD_ADMIN_KEY = "<coach-only-key>"
python -m pip install -r .\requirements.txt
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

Run the HTTP/storage tests from the repository root:
```bash
python -m unittest discover -s leaderboard -p "test_*.py"
```

For optional real SDK/ETag tests, run Azurite on an isolated local endpoint and set
`TOKENWARS_TEST_BLOB_CONNECTION_STRING` to its connection string before running the same command.
These tests create/delete only randomly named test containers; never point them at production storage.

## Deploy to Azure Container Apps
The Terraform templates in [`infra/leaderboard`](../infra/leaderboard) are a **separate root/state**, not part of the
disposable participant or shared-team stacks. They deploy Container Apps (HTTPS), a user-assigned managed identity,
app logs and a private Blob Storage account (ZRS, shared keys and public blob access disabled) in a separate data
resource group. The identity gets **Storage Blob Data Contributor** on that account; no storage key/connection string
is deployed. Coaches create empty per-key containers in the website; Terraform does not manage or expire them.

**One `terraform apply` does everything**: it creates a private Azure Container Registry (Basic, admin user disabled),
builds this folder's [Dockerfile](Dockerfile) **in the cloud** with `az acr build` (no local Docker needed), and deploys
the app pulling the image with its managed identity (`AcrPull`). The image tag is a hash of the app source, so the
next `terraform apply` after changing `server.py`/`storage.py`/`requirements.txt`/`Dockerfile` rebuilds and rolls out
automatically.

Prerequisites: Terraform >= 1.6, Azure CLI **logged in to the same subscription** (`az login`; the build runs with
your CLI credentials), and permission to create role assignments (Owner, or Contributor plus User Access Administrator).
Run from the repository root.

Bash (Linux, macOS, WSL, Cloud Shell):
```bash
az login
az account set --subscription <coach-subscription-id>
cd infra/leaderboard
cp terraform.tfvars.example terraform.tfvars        # set subscription_id (and location if needed)
read -rsp "Strong coach-only admin key (16+ characters): " TF_VAR_admin_key; echo
export TF_VAR_admin_key
# Optional initial key; otherwise use Add leaderboardKey in the web UI:
# read -rsp "Initial event key: " TF_VAR_initial_leaderboard_key; echo; export TF_VAR_initial_leaderboard_key
terraform init
terraform validate
terraform plan
terraform apply                                     # creates ACR -> builds image -> deploys app
terraform output leaderboard_url
unset TF_VAR_admin_key TF_VAR_initial_leaderboard_key
```

PowerShell:
```powershell
az login
az account set --subscription <coach-subscription-id>
Set-Location .\infra\leaderboard
Copy-Item terraform.tfvars.example terraform.tfvars  # set subscription_id (and location if needed)
$env:TF_VAR_admin_key = Read-Host "Strong coach-only admin key (16+ characters)" -MaskInput
# Optional initial key; otherwise use Add leaderboardKey in the web UI:
# $env:TF_VAR_initial_leaderboard_key = Read-Host "Initial event key" -MaskInput
terraform init
terraform validate
terraform plan
terraform apply                                      # creates ACR -> builds image -> deploys app
terraform output leaderboard_url
Remove-Item Env:\TF_VAR_admin_key -ErrorAction SilentlyContinue
Remove-Item Env:\TF_VAR_initial_leaderboard_key -ErrorAction SilentlyContinue
```

- `TF_VAR_admin_key` must be set again for every later `terraform plan`/`apply` (e.g. to roll out code changes).
- **Prebuilt image instead (optional):** if ACR Tasks are blocked in your subscription (some trial/sponsored
  subscriptions are), build and push the image elsewhere and set `container_image` (plus `container_registry_id` for a
  private ACR, which gets `AcrPull` for the app identity) in `terraform.tfvars`. Terraform then skips its own ACR and build.
- Protect Terraform state: it contains the admin key (and optional initial event key) even though the inputs are
  marked sensitive. Use a secured state backend in shared environments.
- Allow a few minutes for new role assignments to propagate. Storage failures appear in app logs/API responses; the
  app never falls back to local disk.

Then open the URL, choose **Add leaderboardKey**, enter a new event key and the coach admin key. Give each event's
teams the URL and **only their event key** (`leaderboard_url` / `leaderboard_key` in their `terraform.tfvars`, or):
```
TOKENWARS_LEADERBOARD_URL=https://<app>.<env>.<region>.azurecontainerapps.io
TOKENWARS_LEADERBOARD_KEY=<event-key-created-by-the-coach>
```
No Python/.NET client changes are needed. Download complete results with **Download submissions**, or
`GET /api/submissions/export` with `x-admin-key` + `x-submit-key`. The public `GET /api/submissions` omits strategies.

### Retention and cleanup
- There is **no time-based deletion policy**. Startup, `terraform apply` and image updates never reset a board.
- **Reset leaderboard** deletes only the selected board's submissions, keeps its container/key and does not affect other events.
- All resources, including the storage account, live in one resource group (`terraform output resource_group`).
- `terraform destroy` or deleting that resource group irreversibly loses all boards; download submissions first.
  Azure subscription owners can also delete data outside Terraform; use your organization's controls/backups as needed.
