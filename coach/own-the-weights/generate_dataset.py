#!/usr/bin/env python3
"""Generate a supervised fine-tuning (SFT) dataset for the coach demo "Own the Weights".

Teacher = the `premium` model from shared/config/models.json (distillation via teacher-generated SFT data).
Questions and answers come FROM THE KNOWLEDGE BASE SECTIONS ONLY (no orders, no workload items).
Every example uses the exact message layout the apps send at inference time (SPEC 4.3, compact + cache-friendly):

  system    = compact system prompt (+ "Answer in English. Do not invent policies." – the apps add it for `custom`)
  user      = "Customer ID: …\nToday: 2026-09-15\n\nContext:\n<top-k keyword sections>\n\nQuestion: …"
  assistant = concise teacher answer

Filters: exact/near duplicates and evaluation leakage (token Jaccard >= 0.6 against any workload.jsonl question).
Output (in --out): train.jsonl, validation.jsonl (90/10), dropped.jsonl (audit), manifest.json.

  python generate_dataset.py --mock --out ./data            # offline, deterministic, no network
  python generate_dataset.py --n 400 --out ./data           # real: calls the premium deployment
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from kit_common import (KitError, KnowledgeBase, estimate_tokens, find_root, gateway_credentials, jaccard,
                        load_workload_questions, normalize_question, read_json, system_prompt, tokenize, user_message)

CUSTOMERS = [f"C{1001 + i}" for i in range(20)]
QUESTION_SYSTEM_PROMPT = (
    "You write realistic customer-support questions for ByteCart, an EU-based e-commerce marketplace (prices in EUR).\n"
    "Use ONLY facts contained in the knowledge-base section you are given. Mix: short FAQ-style questions, questions "
    "that describe a concrete personal situation (dates, amounts, membership status, product category), and a few that "
    "combine two rules or exceptions from the section. Vary wording and tone (casual, formal, a little frustrated).\n"
    "Never mention order IDs. Do not number the questions.\n"
    'Return ONLY JSON: {"questions": ["...", "..."]}'
)
# Out-of-scope questions teach the model the "connect you with a human agent" fallback instead of guessing.
OUT_OF_SCOPE = [
    "Do you offer car insurance together with my purchase?",
    "Can I book a hotel room through ByteCart?",
    "What is the stock price of ByteCart today?",
    "Can ByteCart repair my neighbour's washing machine that was bought elsewhere?",
    "Do you have a physical store in Lisbon where I can pick things up?",
    "Can I pay with Bitcoin at ByteCart?",
    "Is ByteCart hiring software engineers right now?",
    "Can you recommend a good laptop for gaming under 800 euros?",
    "Will ByteCart match a competitor's price if I find it cheaper?",
    "Can I sell my used car on ByteCart?",
    "Do you deliver to Antarctica research stations?",
    "Can you translate my product manual into Japanese?",
]
HANDOFF_ANSWER = ("I'm sorry, I don't have information about that in our support knowledge base. "
                  "I'll connect you with a human agent who can help.")
MOCK_TEMPLATES = [
    "What is the rule for {t}?",
    "Can you explain {t} at ByteCart?",
    "How does {t} work in {d}?",
    "I have a question about {t} - what do I need to know?",
    "Quick question on {d}: what applies to {t}?",
    "Where can I find details on {t}?",
    "As a ByteCart Plus member, does anything change for {t}?",
    "Hi, could you summarise the {t} policy for me?",
]


# --------------------------------------------------------------------------- teacher client (raw HTTP, SPEC 4.6)


class Teacher:
    def __init__(self, root: Path, model_key: str, max_tokens: int):
        import requests  # only needed in real mode

        self._requests = requests
        models_path = root / "shared" / "config" / "models.json"
        if not models_path.exists():
            raise KitError(f"{models_path} not found – deploy the coach infra first or use --mock.")
        registry = read_json(models_path)
        models = registry.get("models") or {}
        if model_key not in models:
            raise KitError(f'Teacher model key "{model_key}" is not in {models_path}')
        self.model = models[model_key]
        self.key = model_key
        if model_key == "judge" or self.model.get("via_gateway", True) is not True:
            raise KitError("Teacher inference must use APIM; choose a non-judge model with via_gateway=true.")
        if any(name.lower() in {"api-key", "authorization", "ocp-apim-subscription-key"}
               for name in (self.model.get("extra_headers") or {})):
            raise KitError("Teacher extra_headers must not override gateway credentials.")
        base, self.api_key = gateway_credentials(root, registry)
        self.url = base + "chat/completions"
        self.max_tokens = max_tokens
        pricing_path = root / "shared" / "config" / "pricing.json"
        prices = read_json(pricing_path).get("models", {}) if pricing_path.exists() else {}
        self.prices = prices.get(self.model.get("pricing_key", ""), {})
        self.usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}

    def chat(self, messages: list[dict[str, str]], temperature: float, json_mode: bool = False) -> tuple[str, str]:
        body: dict[str, Any] = {"model": self.model["deployment"], "messages": messages,
                                self.model.get("max_tokens_param") or "max_tokens": self.max_tokens}
        if self.model.get("supports_temperature", True):
            body["temperature"] = temperature
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        body.update(self.model.get("extra_body") or {})
        headers = {"Content-Type": "application/json", "api-key": self.api_key,
                   "Authorization": f"Bearer {self.api_key}", **(self.model.get("extra_headers") or {})}
        for attempt in range(6):
            resp = self._requests.post(self.url, data=json.dumps(body), headers=headers, timeout=90)
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < 5:
                wait = resp.headers.get("retry-after-ms")
                wait = float(wait) / 1000 if wait else float(resp.headers.get("retry-after") or 2 ** attempt)
                time.sleep(min(wait, 60) + random.random() * 0.5)
                continue
            if resp.status_code >= 400:
                raise KitError(f"Teacher HTTP {resp.status_code}: {resp.text[:300]}")
            data = resp.json()
            usage = data.get("usage") or {}
            self.usage["calls"] += 1
            self.usage["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
            self.usage["completion_tokens"] += int(usage.get("completion_tokens") or 0)
            choice = data["choices"][0]
            return (choice["message"].get("content") or "").strip(), choice.get("finish_reason") or ""
        raise KitError("Teacher: too many retries")

    def cost_usd(self) -> float:
        return (self.usage["prompt_tokens"] * float(self.prices.get("input_per_1m", 0))
                + self.usage["completion_tokens"] * float(self.prices.get("output_per_1m", 0))) / 1_000_000


# --------------------------------------------------------------------------- question candidates


def mock_questions(kb: KnowledgeBase, per_section: int, rng: random.Random) -> list[dict[str, Any]]:
    out = []
    for idx, sec in enumerate(kb.sections):
        title = sec["title"].lower()
        doc = sec["doc_title"]
        templates = MOCK_TEMPLATES[:]
        rng.shuffle(templates)
        facts = [s.strip() for s in sec["body"].replace("\n", " ").split(". ")
                 if any(ch.isdigit() for ch in s) and len(s.split()) >= 6]
        candidates = [tpl.format(t=title, d=doc) for tpl in templates]
        for fact in facts:
            words = fact.rstrip(".").split()
            candidates.append("Please confirm: " + " ".join(words[:14]) + ("…" if len(words) > 14 else "") + "?")
        for q in candidates[:per_section]:
            out.append({"question": q, "source": idx})
    return out


def teacher_questions(kb: KnowledgeBase, teacher: Teacher, per_section: int, concurrency: int) -> list[dict[str, Any]]:
    def one(idx: int) -> list[dict[str, Any]]:
        sec = kb.sections[idx]
        messages = [{"role": "system", "content": QUESTION_SYSTEM_PROMPT},
                    {"role": "user", "content": f"Write {per_section} questions.\n\nSection:\n{sec['text']}"}]
        try:
            text, _ = teacher.chat(messages, temperature=0.9, json_mode=True)
            qs = json.loads(text).get("questions") or []
        except (KitError, ValueError, AttributeError) as exc:
            print(f"   ⚠️  question generation failed for section {idx} ({sec['title']}): {exc}", file=sys.stderr)
            return []
        return [{"question": str(q).strip(), "source": idx} for q in qs if str(q).strip()]

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        results = list(pool.map(one, range(len(kb.sections))))
    return [q for chunk in results for q in chunk]


# --------------------------------------------------------------------------- filters


def filter_candidates(candidates: list[dict[str, Any]], workload: list[dict[str, str]], leak_threshold: float,
                      near_dup_threshold: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    workload_terms = [(w["id"], set(tokenize(w["question"]))) for w in workload]
    kept: list[dict[str, Any]] = []
    kept_terms: list[set[str]] = []
    seen: set[str] = set()
    dropped: list[dict[str, Any]] = []
    counts = {"candidates": len(candidates), "dropped_empty": 0, "dropped_exact_duplicate": 0,
              "dropped_near_duplicate": 0, "dropped_leakage": 0}
    for cand in candidates:
        q = cand["question"]
        norm = normalize_question(q)
        terms = set(tokenize(q))
        if not norm or not terms:
            counts["dropped_empty"] += 1
            dropped.append({**cand, "reason": "empty"})
            continue
        if norm in seen:
            counts["dropped_exact_duplicate"] += 1
            dropped.append({**cand, "reason": "exact_duplicate"})
            continue
        best_id, best = "", 0.0
        for wid, wterms in workload_terms:
            score = jaccard(terms, wterms)
            if score > best:
                best_id, best = wid, score
        if best >= leak_threshold:
            counts["dropped_leakage"] += 1
            dropped.append({**cand, "reason": "leakage", "workload_id": best_id, "jaccard": round(best, 3)})
            continue
        if any(jaccard(terms, other) >= near_dup_threshold for other in kept_terms):
            counts["dropped_near_duplicate"] += 1
            dropped.append({**cand, "reason": "near_duplicate"})
            continue
        seen.add(norm)
        kept_terms.append(terms)
        kept.append({**cand, "max_workload_jaccard": round(best, 3)})
    counts["kept"] = len(kept)
    return kept, dropped, counts


# --------------------------------------------------------------------------- main


def build_example(kb: KnowledgeBase, item: dict[str, Any], top_k: int, adaptation: bool) -> dict[str, Any]:
    context = kb.retrieve(item["question"], top_k)
    return {"messages": [{"role": "system", "content": system_prompt(adaptation)},
                         {"role": "user", "content": user_message(item["customer_id"], context, item["question"])}]}


def mock_answer(kb: KnowledgeBase, item: dict[str, Any], top_k: int) -> str:
    if item.get("out_of_scope"):
        return HANDOFF_ANSWER
    best = kb.sections[kb.top_k_indices(item["question"], top_k)[0]]
    sentences = [s.strip() for s in best["body"].replace("\n", " ").split(". ") if s.strip()]
    answer = ". ".join(sentences[:2]).rstrip(".") + "."
    return answer


def write_jsonl(path: Path, rows: list[dict[str, Any]], bom: bool) -> None:
    text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    path.write_text(text, encoding="utf-8-sig" if bom else "utf-8")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--n", type=int, default=400, help="target number of examples (train + validation), default 400")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--out", default=str(Path(__file__).resolve().parent / "data"),
                   help="output folder (default: coach/own-the-weights/data, git-ignored)")
    p.add_argument("--mock", action="store_true", help="offline + deterministic: template questions, extractive answers")
    p.add_argument("--root", help="repo root (default: TOKENWARS_ROOT or auto-detect)")
    p.add_argument("--teacher", default="premium", help="models.json key of the teacher (default premium)")
    p.add_argument("--top-k", type=int, default=3, help="keyword-retrieved sections per example (SPEC 4.5), default 3")
    p.add_argument("--leak-threshold", type=float, default=0.6, help="drop if token Jaccard vs a workload question >= this")
    p.add_argument("--near-dup-threshold", type=float, default=0.85, help="drop near-duplicates among generated questions")
    p.add_argument("--val-fraction", type=float, default=0.1)
    p.add_argument("--out-of-scope", type=float, default=0.04, help="share of out-of-scope (human hand-off) examples")
    p.add_argument("--no-adaptation", action="store_true",
                   help='omit "Answer in English. Do not invent policies." from the system prompt')
    p.add_argument("--max-output-tokens", type=int, default=350, help="teacher answer cap (same as solution strategy)")
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--epochs", type=int, default=3, help="only for the training-cost estimate in the manifest")
    p.add_argument("--training-price-per-1m", type=float, default=1.0,
                   help="USD per 1M training tokens for the estimate (Ministral 3B Global, Oct 2026), default 1.0")
    p.add_argument("--no-bom", action="store_true", help="write JSONL without UTF-8 BOM (default: with BOM, see README)")
    args = p.parse_args(argv)

    try:
        root = find_root(args.root)
        kb = KnowledgeBase(root)
        workload = load_workload_questions(root)
        rng = random.Random(args.seed)
        n_scope = max(0, min(len(OUT_OF_SCOPE), round(args.n * args.out_of_scope)))
        n_kb = max(1, args.n - n_scope)
        per_section = max(3, math.ceil(n_kb * 1.6 / len(kb.sections)))
        mode = "mock" if args.mock else "teacher"
        print(f"📚 KB: {len(kb.sections)} sections · workload: {len(workload)} questions · mode: {mode} · target n={args.n}")

        teacher = None
        if args.mock:
            candidates = mock_questions(kb, per_section, rng)
        else:
            teacher = Teacher(root, args.teacher, args.max_output_tokens)
            print(f"🧑‍🏫 Teacher: {args.teacher} ({teacher.model['deployment']}) – generating ~{per_section} questions/section")
            candidates = teacher_questions(kb, teacher, per_section, args.concurrency)

        kept, dropped, counts = filter_candidates(candidates, workload, args.leak_threshold, args.near_dup_threshold)
        rng.shuffle(kept)
        if len(kept) < n_kb:
            print(f"⚠️  Only {len(kept)} KB questions survived filtering (wanted {n_kb}).", file=sys.stderr)
        selected = kept[:n_kb]
        selected.extend({"question": q, "source": None, "out_of_scope": True} for q in OUT_OF_SCOPE[:n_scope])
        for item in selected:
            item["customer_id"] = rng.choice(CUSTOMERS)

        adaptation = not args.no_adaptation
        examples: list[dict[str, Any]] = [build_example(kb, item, args.top_k, adaptation) for item in selected]
        retrieval_misses = sum(1 for it in selected if it.get("source") is not None
                               and it["source"] not in kb.top_k_indices(it["question"], args.top_k))

        if args.mock:
            answers = [(mock_answer(kb, it, args.top_k), "stop") for it in selected]
        else:
            with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
                def answer(ex: dict[str, Any]) -> tuple[str, str]:
                    try:
                        return teacher.chat(ex["messages"], temperature=0.2)
                    except KitError as exc:
                        print(f"   ⚠️  answer failed: {exc}", file=sys.stderr)
                        return "", "error"
                answers = list(pool.map(answer, examples))

        final: list[dict[str, Any]] = []
        bad_answers = 0
        for ex, item, (text, finish) in zip(examples, selected, answers):
            if not text or finish in ("length", "error") or text.upper().startswith("ESCALATE"):
                bad_answers += 1
                dropped.append({"question": item["question"], "source": item.get("source"), "reason": f"bad_answer:{finish}"})
                continue
            ex["messages"].append({"role": "assistant", "content": text})
            final.append(ex)
        counts["dropped_bad_answer"] = bad_answers

        rng.shuffle(final)
        n_val = max(1, round(len(final) * args.val_fraction)) if len(final) >= 2 else 0
        validation, train = final[:n_val], final[n_val:]
        if len(train) < 10:
            print("⚠️  Fewer than 10 training examples – Foundry fine-tuning jobs will refuse to start.", file=sys.stderr)

        out = Path(args.out).expanduser().resolve()
        out.mkdir(parents=True, exist_ok=True)
        bom = not args.no_bom
        write_jsonl(out / "train.jsonl", train, bom)
        write_jsonl(out / "validation.jsonl", validation, bom)
        write_jsonl(out / "dropped.jsonl", dropped, False)

        train_tokens = sum(estimate_tokens("".join(m["content"] for m in ex["messages"])) for ex in train)
        manifest = {
            "mode": mode, "seed": args.seed, "target_n": args.n, "teacher": None if args.mock else args.teacher,
            "top_k": args.top_k, "system_prompt_adaptation": adaptation, "utf8_bom": bom,
            "leak_threshold": args.leak_threshold, "near_dup_threshold": args.near_dup_threshold,
            "counts": {**counts, "selected_out_of_scope": n_scope, "retrieval_misses": retrieval_misses,
                       "train": len(train), "validation": len(validation)},
            "estimates": {"train_tokens_approx": train_tokens, "epochs": args.epochs,
                          "training_cost_usd_approx": round(train_tokens * args.epochs * args.training_price_per_1m / 1e6, 2)},
        }
        if teacher is not None:
            manifest["teacher_usage"] = {**teacher.usage, "cost_usd": round(teacher.cost_usd(), 4)}
        (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    except KitError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 2

    c = manifest["counts"]
    print(f"🧹 Candidates {c['candidates']} → kept {c['kept']} "
          f"(dropped: leakage {c['dropped_leakage']} [Jaccard ≥ {args.leak_threshold}], exact dup {c['dropped_exact_duplicate']}, "
          f"near dup {c['dropped_near_duplicate']}, empty {c['dropped_empty']}, bad answer {c['dropped_bad_answer']})")
    print(f"🎯 Out-of-scope hand-off examples: {n_scope} · retrieval misses (source section not in top-{args.top_k}): {retrieval_misses}")
    print(f"✅ Wrote {len(train)} train + {len(validation)} validation examples to {out}")
    print(f"💶 ≈{train_tokens:,} training tokens × {args.epochs} epochs ≈ ${manifest['estimates']['training_cost_usd_approx']} "
          f"at ${args.training_price_per_1m}/1M (estimate; check the pricing page)")
    if teacher is not None:
        print(f"🧑‍🏫 Teacher usage: {teacher.usage['calls']} calls, ${manifest['teacher_usage']['cost_usd']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
