"""Shared helpers for the coach-only "Own the Weights" kit (stdlib only).

Deliberately self-contained: small pieces are copied from python/solution/tokenwars (SPEC 2.1, 3.1, 4.3, 4.5)
so the kit never imports the workshop app. Keep these in sync with SPEC if the prompt text changes.
"""
from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Any

KIT_DIR = Path(__file__).resolve().parent

SCENARIO_DATE = "2026-09-15"

# SPEC 4.3 – compact system prompt (identical to the apps).
COMPACT_SYSTEM_PROMPT = (
    "You are ByteCart's customer support assistant. Answer using ONLY the context provided.\n"
    "Be accurate and concise: at most 5 short sentences or bullet points. "
    "Include exact numbers, fees and deadlines from the context.\n"
    "If the context does not contain the answer, say you will connect the customer with a human agent."
)

# SPEC 4.9 TODO 2.3 / AMENDMENT B3 – the apps append this line for non-OpenAI keys (open, selfhosted, custom, fw, fw_*).
MODEL_ADAPTATION_LINE = "Answer in English. Do not invent policies."

TOKEN_RE = re.compile(r"[a-z0-9]+")
STOPWORDS = frozenset("""
a about after all also am an and any are as at be been before but by can could did do does
for from had has have how i if in into is it its just me my no not of on or our so than that
the their them then there these they this to was we what when where which who why will with
would you your
""".split())
SECTION_SEPARATOR = "\n\n---\n\n"


class KitError(Exception):
    pass


# --------------------------------------------------------------------------- root / env / json


def find_root(explicit: str | None = None) -> Path:
    """--root, else TOKENWARS_ROOT, else walk up from cwd and from this folder to a dir containing shared/config."""
    override = explicit or os.environ.get("TOKENWARS_ROOT")
    if override:
        root = Path(override).expanduser().resolve()
        if not (root / "shared" / "config").is_dir():
            raise KitError(f"{root} does not contain shared/config")
        return root
    for start in (Path.cwd().resolve(), KIT_DIR):
        for folder in (start, *start.parents):
            if (folder / "shared" / "config").is_dir():
                return folder
    raise KitError("Could not find the repo root (folder containing shared/config). Use --root or TOKENWARS_ROOT.")


def parse_env_file(path: Path) -> dict[str, str]:
    """SPEC 2.1: simple KEY=VALUE, '#' comments, optional quotes."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        elif value.startswith("#"):
            value = ""
        values[key] = value
    return values


def load_env(root: Path) -> dict[str, str]:
    env = parse_env_file(root / ".env")
    env.update(os.environ)  # real environment variables override .env
    return env


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise KitError(f"{path} is not valid JSON: {exc}") from exc


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def gateway_credentials(root: Path, registry: dict[str, Any]) -> tuple[str, str]:
    gateway = registry.get("gateway")
    if not isinstance(gateway, dict) or not gateway.get("base_url"):
        raise KitError("APIM is required: deploy the coach gateway and regenerate models.json.")
    key_env = gateway.get("api_key_env") or "APIM_SUBSCRIPTION_KEY"
    key = load_env(root).get(key_env, "")
    if not key:
        raise KitError(f"APIM subscription key {key_env} is empty (.env or environment).")
    return str(gateway["base_url"]).strip().rstrip("/") + "/", key


# --------------------------------------------------------------------------- text / KB (SPEC 3.1, 4.5)


def tokenize(text: str) -> list[str]:
    return [t for t in TOKEN_RE.findall(text.lower()) if len(t) >= 3 and t not in STOPWORDS]


def jaccard(a: set[str], b: set[str]) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 1.0


def normalize_question(question: str) -> str:
    text = question.replace("\u2019", "'").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text.rstrip(" .,!?;:…").strip()


class KnowledgeBase:
    """Sections = '## ' blocks prefixed with the doc title; keyword retrieval exactly like SPEC 4.5."""

    def __init__(self, root: Path):
        kb_dir = root / "shared" / "knowledge-base"
        if not kb_dir.is_dir():
            raise KitError(f"{kb_dir} not found")
        self.sections: list[dict[str, str]] = []
        for path in sorted(kb_dir.glob("*.md"), key=lambda p: p.name):
            content = path.read_text(encoding="utf-8").strip()
            doc_title = path.stem
            for line in content.splitlines():
                if line.startswith("# "):
                    doc_title = line[2:].strip()
                    break
            current, body = None, []
            for line in content.splitlines():
                if line.startswith("## "):
                    if current is not None:
                        self._add(doc_title, current, body)
                    current, body = line[3:].strip(), []
                elif current is not None:
                    body.append(line)
            if current is not None:
                self._add(doc_title, current, body)
        if not self.sections:
            raise KitError(f"No '## ' sections found in {kb_dir}")
        n = len(self.sections)
        self._terms = [set(tokenize(s["text"])) for s in self.sections]
        df: dict[str, int] = {}
        for terms in self._terms:
            for t in terms:
                df[t] = df.get(t, 0) + 1
        self._idf = {t: math.log(1 + n / c) for t, c in df.items()}

    def _add(self, doc_title: str, title: str, body: list[str]) -> None:
        body_text = "\n".join(body).strip()
        self.sections.append({"doc_title": doc_title, "title": title, "body": body_text,
                              "text": f"{doc_title} > {title}\n{body_text}"})

    def top_k_indices(self, question: str, top_k: int) -> list[int]:
        query = set(tokenize(question))
        scored = sorted((-sum(self._idf[t] for t in query if t in terms), i) for i, terms in enumerate(self._terms))
        return [i for _, i in scored[:top_k]]

    def retrieve(self, question: str, top_k: int) -> str:
        return SECTION_SEPARATOR.join(self.sections[i]["text"] for i in self.top_k_indices(question, top_k))


def load_workload_questions(root: Path) -> list[dict[str, str]]:
    path = root / "shared" / "data" / "workload.jsonl"
    if not path.exists():
        raise KitError(f"{path} not found (needed for the leakage filter)")
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            item = json.loads(line)
            items.append({"id": item.get("id", ""), "question": item.get("question", "")})
    return items


# --------------------------------------------------------------------------- prompts (SPEC 4.3 cache-friendly compact)


def system_prompt(adaptation: bool = True) -> str:
    return COMPACT_SYSTEM_PROMPT + ("\n" + MODEL_ADAPTATION_LINE if adaptation else "")


def user_message(customer_id: str, context: str, question: str) -> str:
    return (f"Customer ID: {customer_id}\n"
            f"Today: {SCENARIO_DATE}\n\n"
            f"Context:\n{context or '(no additional context)'}\n\n"
            f"Question: {question}")


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / 4)
