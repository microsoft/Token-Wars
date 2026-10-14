#!/usr/bin/env python3
"""Register the fine-tuned deployment as model key `custom` in shared/config/models.json (coach machine only).

After this, the workshop apps can use it like any other key:
  python -m tokenwars compare --models balanced,premium,custom      (Python, in python/solution)
  dotnet run -- compare --models balanced,premium,custom            (.NET, in dotnet/solution/TokenWars)

  python register_custom_model.py --deployment bytecart-ft                       # base_url/key env copied from `premium`
  python register_custom_model.py --deployment bytecart-ft --resource my-foundry --hourly-cost 0.65 --write-pricing
  python register_custom_model.py --remove                                       # after the demo

models.json is git-ignored (terraform output). pricing.json is tracked: --write-pricing edits YOUR LOCAL COPY only –
do not commit the `custom-finetuned` key (revert with `git checkout shared/config/pricing.json`).
Inference uses required APIM. Deploy on the primary Foundry resource, or configure a gateway backend/model route
for another resource first; registering a base_url does not configure that route.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys

from kit_common import KitError, find_root, read_json, write_json

DEFAULT_PRICING_KEY = "custom-finetuned"
# Ministral-3B (2411) fine-tuned, Global Standard serverless – Azure pricing page, checked 2026-10-09.
DEFAULT_PRICES = {"input_per_1m": 0.05, "output_per_1m": 0.15, "hourly": 0.65}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", help="repo root (default: TOKENWARS_ROOT or auto-detect)")
    p.add_argument("--key", default="custom", help="model key in models.json (default custom)")
    p.add_argument("--deployment", help="deployment name of the fine-tuned model (required unless --remove)")
    p.add_argument("--base-url", help="OpenAI v1 base URL, e.g. https://<acct>.services.ai.azure.com/openai/v1/")
    p.add_argument("--resource", help="Foundry account name; builds https://<resource>.services.ai.azure.com/openai/v1/")
    p.add_argument("--api-key-env", help="backend key metadata (default: premium's api_key_env); inference uses the APIM key")
    p.add_argument("--pricing-key", default=DEFAULT_PRICING_KEY)
    p.add_argument("--hourly-cost", type=float, default=DEFAULT_PRICES["hourly"],
                   help="hosting fee USD/hour (informational; shown by compare as 'hosting $/h'), default 0.65")
    p.add_argument("--max-tokens-param", choices=["max_tokens", "max_completion_tokens"], default="max_tokens")
    p.add_argument("--no-temperature", action="store_true", help="set supports_temperature=false")
    p.add_argument("--write-pricing", action="store_true",
                   help="also add/update the pricing key in shared/config/pricing.json (local copy, do not commit)")
    p.add_argument("--input-price", type=float, default=DEFAULT_PRICES["input_per_1m"], help="USD per 1M input tokens")
    p.add_argument("--cached-input-price", type=float, help="USD per 1M cached input tokens (default = input price)")
    p.add_argument("--output-price", type=float, default=DEFAULT_PRICES["output_per_1m"], help="USD per 1M output tokens")
    p.add_argument("--remove", action="store_true", help="remove the key from models.json (and pricing with --write-pricing)")
    p.add_argument("--dry-run", action="store_true", help="print the result, write nothing")
    args = p.parse_args(argv)

    try:
        root = find_root(args.root)
    except KitError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 2
    config_dir = root / "shared" / "config"
    models_path = config_dir / "models.json"
    example_path = config_dir / "models.example.json"
    pricing_path = config_dir / "pricing.json"

    if models_path.exists():
        data = read_json(models_path)
    elif example_path.exists():
        data = copy.deepcopy(read_json(example_path))
        print(f"⚠️  {models_path.name} not found – creating it from {example_path.name} (placeholders!). "
              "Run terraform in the coach subscription for real endpoints.", file=sys.stderr)
    else:
        data = {"models": {}, "gateway": None}
        print(f"⚠️  Neither models.json nor models.example.json found – creating a minimal models.json.", file=sys.stderr)
    data.setdefault("models", {})
    models = data["models"]

    if args.remove:
        removed = models.pop(args.key, None)
        print(f"{'🗑️  Removed' if removed else 'ℹ️  No'} '{args.key}' entry in {models_path}")
        if not args.dry_run and removed:
            write_json(models_path, data)
        if args.write_pricing and pricing_path.exists():
            pricing = read_json(pricing_path)
            if (pricing.get("models") or {}).pop(args.pricing_key, None) is not None:
                print(f"🗑️  Removed '{args.pricing_key}' from {pricing_path}")
                if not args.dry_run:
                    write_json(pricing_path, pricing)
        return 0

    if not args.deployment:
        print("❌ --deployment is required", file=sys.stderr)
        return 2

    premium = models.get("premium") or {}
    if args.base_url:
        base_url = args.base_url
    elif args.resource:
        base_url = f"https://{args.resource}.services.ai.azure.com/openai/v1/"
    elif premium.get("base_url") and "<" not in premium["base_url"]:
        base_url = premium["base_url"]
    else:
        print("❌ No usable base_url: pass --base-url or --resource (premium's base_url is missing or a placeholder).",
              file=sys.stderr)
        return 2
    if not base_url.endswith("/"):
        base_url += "/"

    entry = {
        "deployment": args.deployment,
        "base_url": base_url,
        "api_key_env": args.api_key_env or premium.get("api_key_env") or "AZURE_AI_API_KEY",
        "pricing_key": args.pricing_key,
        "type": "chat",
        "via_gateway": True,
        "max_tokens_param": args.max_tokens_param,
        "supports_temperature": not args.no_temperature,
        "extra_body": {},
        "extra_headers": {},
        "hourly_cost_usd": args.hourly_cost,
    }
    previous = models.get(args.key)
    models[args.key] = entry
    action = "Updated" if previous else "Added"
    print(f"✅ {action} '{args.key}' in {models_path}:")
    print(json.dumps(entry, indent=2))
    if not args.dry_run:
        write_json(models_path, data)

    pricing = read_json(pricing_path) if pricing_path.exists() else {"currency": "USD", "models": {}}
    pricing.setdefault("models", {})
    if args.write_pricing:
        cached = args.cached_input_price if args.cached_input_price is not None else args.input_price
        pricing["models"][args.pricing_key] = {
            "input_per_1m": args.input_price, "cached_input_per_1m": cached, "output_per_1m": args.output_price,
            "hourly_cost_usd": args.hourly_cost,
            "note": "Coach demo only (Own the Weights). Hosting fee is billed per hour and is NOT part of the score.",
        }
        print(f"✅ Wrote pricing key '{args.pricing_key}' to {pricing_path} "
              "(LOCAL COPY – do not commit; revert with: git checkout shared/config/pricing.json)")
        if not args.dry_run:
            write_json(pricing_path, pricing)
    elif args.pricing_key not in pricing["models"]:
        print(f"⚠️  pricing.json has no '{args.pricing_key}' key – the apps will count '{args.key}' calls as $0. "
              "Re-run with --write-pricing (local copy) for a fair compare.", file=sys.stderr)
    if args.dry_run:
        print("(dry run – nothing written)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
