#!/usr/bin/env python3
"""Вивантажує тексти всіх джерел блокнота NotebookLM у файли через CLI `nlm`.

Текст іде прямо на диск і не проходить через контекст жодної моделі.
Друкує лише зведення: ідентифікатор, назву, кількість символів і прапорці якості.

Використання:
    python nlm_export.py --notebook <notebook-id> --out <тека_справи>/texts

Коди виходу: 0 — успіх; 3 — протермінований вхід (`nlm login`); 1 — інша помилка.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

AUTH_MARKERS = ("Authentication expired", "Authentication Error", "stale")
LETTER_RE = re.compile(r"[A-Za-zА-Яа-яІіЇїЄєҐґ]")
MIN_CHARS = 200          # менше — джерело фактично порожнє (скан без OCR)
MIN_LETTER_SHARE = 0.60  # менше — ймовірно поганий OCR


class AuthExpired(RuntimeError):
    pass


def run_nlm(args: list[str]) -> str:
    proc = subprocess.run(["nlm", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (proc.stdout or "") + (proc.stderr or "")
    if any(m in out for m in AUTH_MARKERS):
        raise AuthExpired("Вхід у NotebookLM протерміновано: виконайте `nlm login` у терміналі.")
    if proc.returncode != 0:
        raise RuntimeError(f"nlm {' '.join(args)} завершився з кодом {proc.returncode}: {out.strip()[:500]}")
    return proc.stdout


def list_sources(notebook_id: str) -> list[dict]:
    raw = run_nlm(["source", "list", notebook_id, "--json"])
    data = json.loads(raw)
    items = data.get("sources", data) if isinstance(data, dict) else data
    result = []
    for it in items:
        sid = it.get("id") or it.get("source_id")
        title = it.get("title") or it.get("name") or ""
        if sid:
            result.append({"id": str(sid), "title": str(title)})
    return result


IMAGE_URL_RE = re.compile(r"https://lh3\.googleusercontent\.com/\S+")
UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")


def quality(text: str) -> dict:
    """Оцінює якість тексту джерела.

    Скан без текстового шару NotebookLM віддає як перелік посилань на зображення сторінок
    (lh3.googleusercontent.com) з ідентифікаторами. Ці посилання вимагають входу в Google,
    тож такий текст для роботи непридатний: потрібен оригінал PDF (прапорець image_only).
    """
    pages = len(IMAGE_URL_RE.findall(text))
    plain = UUID_RE.sub(" ", IMAGE_URL_RE.sub(" ", text))
    non_space = [c for c in plain if not c.isspace()]
    letters = sum(1 for c in non_space if LETTER_RE.match(c))
    share = round(letters / len(non_space), 3) if non_space else 0.0
    flags = []
    if pages and len(non_space) < MIN_CHARS:
        flags.append("image_only")
    elif len(non_space) < MIN_CHARS:
        flags.append("empty_or_no_ocr")
    elif share < MIN_LETTER_SHARE:
        flags.append("poor_ocr")
    return {"chars": len(plain.strip()), "image_pages": pages, "letter_share": share, "flags": flags}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--notebook", required=True, help="Ідентифікатор блокнота NotebookLM")
    ap.add_argument("--out", required=True, type=Path, help="Тека для текстів джерел")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    try:
        sources = list_sources(args.notebook)
        index = []
        for s in sources:
            path = args.out / f"{s['id']}.txt"
            run_nlm(["source", "content", s["id"], "-o", str(path)])
            text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
            index.append({**s, "path": str(path), **quality(text)})
    except AuthExpired as exc:
        print(json.dumps({"error": "auth_expired", "message": str(exc)}, ensure_ascii=False))
        return 3
    except Exception as exc:  # noqa: BLE001 — повідомлення йде оркестратору
        print(json.dumps({"error": "export_failed", "message": str(exc)}, ensure_ascii=False))
        return 1

    (args.out / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        "notebook": args.notebook,
        "sources": len(index),
        "total_chars": sum(i["chars"] for i in index),
        "flagged": [{"id": i["id"], "title": i["title"], "flags": i["flags"]} for i in index if i["flags"]],
        "index": str(args.out / "index.json"),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8")  # Windows: вивід у файл інакше йде в кодуванні системи
    sys.exit(main())
