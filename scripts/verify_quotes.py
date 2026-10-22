#!/usr/bin/env python3
"""Звіряє дослівні цитати з реєстру фактів із текстами джерел.

Кожен рядок evidence.jsonl містить щонайменше: id, source_id, quote.
Цитата вважається підтвердженою, якщо після нормалізації (пробіли, лапки,
апострофи, тире, перенесення слів) вона є підрядком тексту джерела.
Непідтверджені факти позначаються `verified: false` і в документ не йдуть.

Використання:
    python verify_quotes.py <evidence.jsonl> --texts <тека_справи>/texts [--out evidence.verified.jsonl]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

TRANSLATE = str.maketrans({
    "«": '"', "»": '"', "„": '"', "“": '"', "”": '"', "‟": '"',
    "’": "'", "ʼ": "'", "‘": "'", "`": "'",
    "–": "-", "—": "-", "‑": "-", "−": "-",
    " ": " ", " ": " ", " ": " ",
})


def normalize(text: str) -> str:
    text = text.replace("­", "")                 # м'який перенос
    text = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)  # перенесення слова через рядок
    text = text.translate(TRANSLATE)
    text = re.sub(r"\s+", " ", text)
    return text.strip().casefold()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("evidence", type=Path)
    ap.add_argument("--texts", required=True, type=Path)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    cache: dict[str, str] = {}
    rows, failed = [], []
    for n, line in enumerate(args.evidence.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        item = json.loads(line)
        sid, quote = str(item.get("source_id", "")), str(item.get("quote", ""))
        if sid not in cache:
            p = args.texts / f"{sid}.txt"
            cache[sid] = normalize(p.read_text(encoding="utf-8", errors="replace")) if p.exists() else ""
        ok = bool(quote.strip()) and bool(cache[sid]) and normalize(quote) in cache[sid]
        item["verified"] = ok
        if not ok:
            reason = "no_source_text" if not cache[sid] else ("empty_quote" if not quote.strip() else "not_found")
            item["verify_reason"] = reason
            failed.append({"line": n, "id": item.get("id"), "source_id": sid, "reason": reason})
        rows.append(item)

    out = args.out or args.evidence.with_suffix(".verified.jsonl")
    out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    print(json.dumps({
        "total": len(rows),
        "verified": len(rows) - len(failed),
        "failed": failed,
        "out": str(out),
    }, ensure_ascii=False, indent=2))
    return 0 if not failed else 4


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8")  # Windows: вивід у файл інакше йде в кодуванні системи
    sys.exit(main())
