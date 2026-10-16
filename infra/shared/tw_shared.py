"""Shared helpers for deploy_teams.py and set_policy.py (stdlib only)."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent          # infra/shared
INFRA = HERE.parent                             # infra (coach stack)
TEAMS_TFVARS = HERE / "teams.auto.tfvars.json"
POLICY_TFVARS = HERE / "policies.auto.tfvars.json"
DEFAULT_POLICY = "policies/ai-gateway-starter.xml"
TEAM_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,19}$")

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # emoji status lines on legacy Windows consoles
    except (AttributeError, ValueError):
        pass


class SharedError(Exception):
    pass


def terraform() -> str:
    exe = shutil.which("terraform")
    if not exe:
        raise SharedError("terraform not found on PATH (needs >= 1.6).")
    return exe


def run_tf(args: list[str], cwd: Path = HERE, capture: bool = False) -> subprocess.CompletedProcess:
    cmd = [terraform(), *args]
    if not capture:
        print(f"\n$ terraform {' '.join(args)}   (in {cwd})", flush=True)
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=capture)


def coach_config() -> dict:
    """Read the non-public shared_mode_config output of the coach stack (infra/)."""
    if not (INFRA / "terraform.tfstate").exists():
        raise SharedError(f"No coach state at {INFRA / 'terraform.tfstate'}. Deploy the coach stack first "
                          "(cd infra && terraform apply).")
    res = run_tf(["output", "-json", "shared_mode_config"], cwd=INFRA, capture=True)
    if res.returncode != 0 or not res.stdout.strip():
        raise SharedError("The coach state has no 'shared_mode_config' output. Run `terraform apply` in infra/ "
                          "once with this version of the kit (it only adds the output), then retry.\n"
                          + (res.stderr or "").strip())
    cfg = json.loads(res.stdout)
    if not isinstance(cfg, dict) or "primary" not in cfg:
        raise SharedError("Unexpected shared_mode_config output; re-run `terraform apply` in infra/.")
    return cfg


def prepare_env(cfg: dict) -> None:
    sub = cfg.get("subscription_id") or ""
    current = os.environ.get("ARM_SUBSCRIPTION_ID")
    if current and current != sub:
        print(f"⚠️  ARM_SUBSCRIPTION_ID={current} differs from the coach subscription {sub}; using the coach's.")
    os.environ["ARM_SUBSCRIPTION_ID"] = sub


def tf_init() -> None:
    if run_tf(["init", "-input=false", "-upgrade=false"]).returncode != 0:
        raise SharedError("terraform init failed in infra/shared.")


def read_json(path: Path, default):
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    tmp.replace(path)


def current_teams() -> list[str]:
    return list(read_json(TEAMS_TFVARS, {}).get("teams", []))


def policy_assignments() -> dict:
    data = read_json(POLICY_TFVARS, {})
    return {
        "default_policy_file": data.get("default_policy_file", DEFAULT_POLICY),
        "team_policy_files": dict(data.get("team_policy_files", {})),
    }


def print_team_summary() -> None:
    res = run_tf(["output", "-json", "teams"], capture=True)
    if res.returncode != 0 or not res.stdout.strip():
        return
    teams = json.loads(res.stdout) or {}
    if not teams:
        print("\nNo team gateways in state.")
        return
    print("\nTeam gateways:")
    for name in sorted(teams):
        t = teams[name]
        policy = Path(t["policy_file"]).name
        print(f"  - {name:<20} {t['apim_name']:<34} policy={policy}")
        print(f"    {'':<20} files: {t['dotenv']}  |  {t['models_json']}")


def fail(exc: Exception) -> int:
    print(f"❌ {exc}", file=sys.stderr)
    return 2
