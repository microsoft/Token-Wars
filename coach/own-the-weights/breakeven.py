#!/usr/bin/env python3
"""Break-even calculator: per-token models vs a fine-tuned small model with an hourly hosting fee.

Monthly cost table for
  * premium with retrieval   (pricing.json, key from models.json `premium` or gpt-5.6-sol)
  * balanced with retrieval  (pricing.json, key from models.json `balanced` or gpt-5.6-terra)
  * fine-tuned small model, short prompt  (per-token + hourly hosting fee × hours/month)
  * optional Fireworks per-token model (if its key is in pricing.json or --fireworks-price is given)
and the monthly volume above which the fine-tuned model beats balanced. PTU is printed ONLY as a reference line.

  python breakeven.py                                   # 1M requests/month, 1,200 in / 120 out
  python breakeven.py --requests 100000                 # small volume: hosting fee dominates
  python breakeven.py --ft-base qwen3-32b --ft-input-tokens 1200
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from kit_common import KitError, find_root, read_json

HOURS_PER_MONTH = 730

# Fine-tuning prices (USD, Global training / Global Standard serverless deployment) from the Azure "Foundry Models
# pricing – Fine-tuning models" page, checked 2026-10-09. Hosting = per hour while the deployment exists.
FT_PRESETS: dict[str, dict[str, Any]] = {
    "ministral-3b": {"label": "Ministral-3B (2411) FT", "training_per_1m": 1.00, "hourly": 0.65,
                     "input_per_1m": 0.05, "output_per_1m": 0.15, "verified": True},
    "qwen3-32b": {"label": "Qwen3-32B FT", "training_per_1m": 3.20, "hourly": 0.30,
                  "input_per_1m": 0.30, "output_per_1m": 1.20, "verified": True},
    "llama-3.3-70b": {"label": "Llama-3.3-70B FT", "training_per_1m": 4.50, "hourly": 0.30,
                      "input_per_1m": 0.71, "output_per_1m": 0.71, "verified": True},
    # VALIDATE: gpt-oss-20b fine-tuning prices were not listed on the pricing page – illustrative values only.
    "gpt-oss-20b": {"label": "gpt-oss-20b FT (illustrative)", "training_per_1m": 1.50, "hourly": 1.70,
                    "input_per_1m": 0.07, "output_per_1m": 0.30, "verified": False},
}
# Standard Global short-context snapshot; Sol input/output promotion runs through at least 2026-11-30.
FALLBACK_PRICES = {
    "gpt-5.6-sol": {"input_per_1m": 4.00, "cached_input_per_1m": 0.50, "output_per_1m": 20.00},
    "gpt-5.6-terra": {"input_per_1m": 2.00, "cached_input_per_1m": 0.20, "output_per_1m": 12.00},
}


def _prices(pricing: dict[str, Any], key: str) -> dict[str, float] | None:
    entry = (pricing.get("models") or {}).get(key) or FALLBACK_PRICES.get(key)
    if entry is None:
        return None
    inp = float(entry.get("input_per_1m", 0))
    return {"input_per_1m": inp, "cached_input_per_1m": float(entry.get("cached_input_per_1m", inp)),
            "output_per_1m": float(entry.get("output_per_1m", 0))}


def per_request(prices: dict[str, float], in_tok: float, out_tok: float, cached_share: float) -> float:
    cached = in_tok * cached_share
    return ((in_tok - cached) * prices["input_per_1m"] + cached * prices["cached_input_per_1m"]
            + out_tok * prices["output_per_1m"]) / 1_000_000


def money(x: float) -> str:
    if x == 0:
        return "-"
    return f"${x:,.2f}" if abs(x) < 100 else f"${x:,.0f}"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--requests", type=int, default=1_000_000, help="requests per month (default 1,000,000)")
    p.add_argument("--input-tokens", type=float, default=1200, help="avg input tokens with retrieval (default 1200)")
    p.add_argument("--output-tokens", type=float, default=120, help="avg output tokens (default 120)")
    p.add_argument("--cached-share", type=float, default=0.0,
                   help="share of input tokens served from the prompt cache for per-token models (0..1, default 0)")
    p.add_argument("--premium-key", help="pricing.json key for premium (default: models.json premium.pricing_key or gpt-5.6-sol)")
    p.add_argument("--balanced-key", help="pricing.json key for balanced (default: models.json balanced.pricing_key or gpt-5.6-terra)")
    p.add_argument("--ft-base", choices=sorted(FT_PRESETS), default="ministral-3b", help="fine-tuning price preset")
    p.add_argument("--ft-input-tokens", type=float, default=800,
                   help="avg input tokens of the fine-tuned model's SHORT prompt (default 800)")
    p.add_argument("--ft-output-tokens", type=float, help="avg output tokens of the fine-tuned model (default = --output-tokens)")
    p.add_argument("--ft-input-price", type=float, help="override USD per 1M input tokens for the fine-tuned model")
    p.add_argument("--ft-output-price", type=float, help="override USD per 1M output tokens for the fine-tuned model")
    p.add_argument("--ft-hourly", type=float, help="override hosting fee USD/hour for the fine-tuned deployment")
    p.add_argument("--hours", type=float, default=HOURS_PER_MONTH, help="hosted hours per month (default 730)")
    p.add_argument("--training-tokens", type=float, default=250_000,
                   help="tokens in train.jsonl (see manifest.json; ~250k for 360 examples)")
    p.add_argument("--epochs", type=int, default=3)
    p.add_argument("--fireworks-key", default="fw-glm-5.3-flash",
                   help="pricing.json key of an optional Fireworks per-token model (skipped if absent)")
    p.add_argument("--fireworks-price", help='manual Fireworks price "input,output" USD per 1M (overrides pricing.json)')
    p.add_argument("--no-fireworks", action="store_true")
    p.add_argument("--ptu-units", type=float, default=15, help="PTU reference line: units (illustrative minimum)")
    p.add_argument("--ptu-hourly", type=float, default=1.00, help="PTU reference line: USD per PTU-hour (illustrative)")
    p.add_argument("--no-ptu", action="store_true")
    p.add_argument("--root", help="repo root (default: TOKENWARS_ROOT or auto-detect)")
    p.add_argument("--json", action="store_true", help="print machine-readable JSON instead of the table")
    args = p.parse_args(argv)

    pricing: dict[str, Any] = {}
    models: dict[str, Any] = {}
    source = "built-in fallback prices"
    try:
        root = find_root(args.root)
        pricing_path = root / "shared" / "config" / "pricing.json"
        if pricing_path.exists():
            pricing = read_json(pricing_path)
            source = "shared/config/pricing.json"
        models_path = root / "shared" / "config" / "models.json"
        if models_path.exists():
            models = read_json(models_path).get("models") or {}
    except KitError as exc:
        print(f"⚠️  {exc} – using built-in prices.", file=sys.stderr)

    premium_key = args.premium_key or (models.get("premium") or {}).get("pricing_key") or "gpt-5.6-sol"
    balanced_key = args.balanced_key or (models.get("balanced") or {}).get("pricing_key") or "gpt-5.6-terra"
    premium, balanced = _prices(pricing, premium_key), _prices(pricing, balanced_key)
    if premium is None or balanced is None:
        print(f"❌ No price for {premium_key if premium is None else balanced_key} in {source}", file=sys.stderr)
        return 2

    preset = dict(FT_PRESETS[args.ft_base])
    ft_source = f"preset {args.ft_base}"
    custom_price = (pricing.get("models") or {}).get("custom-finetuned")
    if custom_price and args.ft_base == "ministral-3b" and not any(
            v is not None for v in (args.ft_input_price, args.ft_output_price, args.ft_hourly)):
        preset.update({k: float(custom_price[k]) for k in ("input_per_1m", "output_per_1m") if k in custom_price})
        if "hourly_cost_usd" in custom_price:
            preset["hourly"] = float(custom_price["hourly_cost_usd"])
        preset["label"], preset["verified"], ft_source = "custom-finetuned (pricing.json)", True, "pricing.json custom-finetuned"
    custom_model = models.get("custom") or {}
    if args.ft_hourly is None and float(custom_model.get("hourly_cost_usd") or 0) > 0 and ft_source.startswith("preset"):
        preset["hourly"] = float(custom_model["hourly_cost_usd"])
        ft_source += " + models.json custom.hourly_cost_usd"
    for attr, key in (("ft_input_price", "input_per_1m"), ("ft_output_price", "output_per_1m"), ("ft_hourly", "hourly")):
        if getattr(args, attr) is not None:
            preset[key] = getattr(args, attr)
            preset["verified"] = True
            ft_source = "CLI overrides"
    ft_prices = {"input_per_1m": preset["input_per_1m"], "cached_input_per_1m": preset["input_per_1m"],
                 "output_per_1m": preset["output_per_1m"]}
    ft_out = args.output_tokens if args.ft_output_tokens is None else args.ft_output_tokens

    rows: list[dict[str, Any]] = []

    def add(name: str, prices: dict[str, float], in_tok: float, out_tok: float, hosting: float, cached: float,
            note: str = "") -> dict[str, Any]:
        per_req = per_request(prices, in_tok, out_tok, cached)
        row = {"option": name, "input_per_1m": prices["input_per_1m"], "output_per_1m": prices["output_per_1m"],
               "in_tokens": in_tok, "out_tokens": out_tok, "per_request_usd": per_req,
               "token_cost_usd": per_req * args.requests, "hosting_usd": hosting,
               "total_usd": per_req * args.requests + hosting, "note": note}
        rows.append(row)
        return row

    add(f"premium + retrieval ({premium_key})", premium, args.input_tokens, args.output_tokens, 0.0, args.cached_share)
    balanced_row = add(f"balanced + retrieval ({balanced_key})", balanced, args.input_tokens, args.output_tokens, 0.0, args.cached_share)
    hosting = preset["hourly"] * args.hours
    ft_row = add(f"{preset['label']}, short prompt", ft_prices, args.ft_input_tokens, ft_out, hosting, 0.0,
                 f"hosting ${preset['hourly']}/h × {args.hours:g} h")

    fw_note = ""
    if not args.no_fireworks:
        fw = None
        if args.fireworks_price:
            try:
                fin, fout = (float(x) for x in args.fireworks_price.split(","))
            except ValueError:
                print('❌ --fireworks-price must be "input,output"', file=sys.stderr)
                return 2
            fw = {"input_per_1m": fin, "cached_input_per_1m": fin, "output_per_1m": fout}
        elif args.fireworks_key in (pricing.get("models") or {}):
            fw = _prices(pricing, args.fireworks_key)
            if pricing["models"][args.fireworks_key].get("verified") is False:
                fw_note = "price not verified"
        else:
            fw_note = f'(Fireworks skipped: "{args.fireworks_key}" not in pricing.json; use --fireworks-price in,out)'
        if fw:
            add(f"Fireworks per-token ({args.fireworks_key if not args.fireworks_price else 'manual'})", fw,
                args.input_tokens, args.output_tokens, 0.0, args.cached_share, fw_note)
            fw_note = ""

    def breakeven_vs(row: dict[str, Any]) -> float | None:
        saving = row["per_request_usd"] - ft_row["per_request_usd"]
        return hosting / saving if saving > 0 else None

    breakeven = breakeven_vs(balanced_row)
    breakevens = {r["option"]: breakeven_vs(r) for r in rows if r is not ft_row}
    training_cost = args.training_tokens * args.epochs * preset["training_per_1m"] / 1_000_000
    ptu = args.ptu_units * args.ptu_hourly * args.hours

    if args.json:
        print(json.dumps({"requests_per_month": args.requests, "pricing_source": source, "ft_price_source": ft_source,
                          "rows": rows, "breakeven_vs_balanced_requests_per_month": breakeven,
                          "breakeven_requests_per_month": breakevens,
                          "training_one_off_usd": training_cost,
                          "ptu_reference_usd": None if args.no_ptu else ptu}, indent=2))
        return 0

    print(f"Token Wars – Own the Weights break-even · {args.requests:,} requests/month · "
          f"{args.input_tokens:,.0f} in / {args.output_tokens:,.0f} out tokens (fine-tuned: {args.ft_input_tokens:,.0f} / {ft_out:,.0f})"
          + (f" · cached share {args.cached_share:.0%}" if args.cached_share else ""))
    print(f"Prices: {source}; fine-tuned: {ft_source}. List prices, USD – illustrative, check before the event.\n")
    header = f"{'Option':<52} {'$/1M in':>8} {'$/1M out':>9} {'tokens $/mo':>12} {'hosting $/mo':>13} {'TOTAL $/mo':>11} {'$/1k req':>9}"
    print(header)
    print("-" * len(header))
    best = min(r["total_usd"] for r in rows)
    for r in rows:
        star = " ◀ cheapest" if r["total_usd"] == best else ""
        print(f"{r['option'][:52]:<52} {r['input_per_1m']:>8.3f} {r['output_per_1m']:>9.3f} {money(r['token_cost_usd']):>12} "
              f"{money(r['hosting_usd']):>13} {money(r['total_usd']):>11} {r['per_request_usd'] * 1000:>9.4f}{star}")
    print("-" * len(header))
    if not preset["verified"]:
        print(f"⚠️  {preset['label']}: prices are illustrative (VALIDATE on the pricing page).")
    if fw_note:
        print(fw_note)
    for r in rows:
        if "not verified" in r["note"]:
            print(f"⚠️  {r['option']}: price not verified (pricing.json verified=false).")
    if breakeven is None:
        print("Break-even vs balanced: never – the fine-tuned model is not cheaper per request.")
    else:
        print(f"Break-even vs balanced: {breakeven:,.0f} requests/month (≈ {breakeven / 30:,.0f}/day). "
              "Above that the fine-tuned model wins; below it the hosting fee dominates.")
    for name, value in breakevens.items():
        if name != balanced_row["option"]:
            print(f"  vs {name}: " + ("never" if value is None else f"{value:,.0f} requests/month"))
    print(f"One-off training: {args.training_tokens:,.0f} tokens × {args.epochs} epochs × ${preset['training_per_1m']}/1M "
          f"≈ {money(training_cost)} (plus teacher-generation tokens).")
    print("Hosting is billed per hour while the deployment EXISTS, even with zero traffic – delete it after the demo.")
    if not args.no_ptu:
        print(f"PTU (for reference, not recommended for this workshop): {args.ptu_units:g} PTU × ${args.ptu_hourly}/PTU-h × "
              f"{args.hours:g} h ≈ {money(ptu)}/month regardless of volume (illustrative rate – VALIDATE).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
