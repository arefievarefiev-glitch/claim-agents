#!/usr/bin/env python3
"""Розшифровує скани з блокнота NotebookLM через чат блокнота (`nlm notebook query`).

`nlm source content` для скану без текстового шару повертає лише посилання на зображення
сторінок. Чат NotebookLM читає ці зображення сам, тож розшифровку отримуємо запитом до
одного джерела (--source-ids) у новій розмові.

Для кожного джерела з позначкою image_only в index.json (результат nlm_export.py):
  1. дві незалежні розшифровки (кожна в новій розмові), пакетами по --pages-per-query
     сторінок, щоб відповідь не перевищувала межу розміру;
  2. посторінкове порівняння; технічні розбіжності (пробіли в числах, розділові знаки,
     маркери цитат [N], перестановка рядків) відкидаються;
  3. перша розшифровка стає текстом джерела <id>.txt, змістовні розбіжності — <id>.diff.json
     для гейту G1 («перевірте за оригіналом»).

Тест 22.10.2026 (2 скани, 6 сторінок): збіг по сторінках 95,6–99,98 %; змістовні
розбіжності — назва вулиці, відмінкове закінчення, сполучник.

Використання:
    python nlm_transcribe.py --notebook <id> --texts <тека_справи>/texts [--pages-per-query 8]

Коди виходу: 0 — успіх; 3 — протермінований вхід; 1 — інша помилка.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import subprocess
import sys
from pathlib import Path

AUTH_MARKERS = ("Authentication expired", "Authentication Error", "stale")
PAGE_RE = re.compile(r"=== \S+ (\d+) ===")
CIT_RE = re.compile(r"\[\d+(?:[,\s\-–]+\d+)*\]")
NUM_SPACE_RE = re.compile(r"(?<=\d)[   ](?=\d{3}\b)")
TOKEN_RE = re.compile(r"\w+|[^\w\s]")

PROMPT = (
    "Наведи ПОВНІСТЮ і дослівно текст цього документа{pages}, сторінка за сторінкою, "
    "від першого до останнього рядка, зберігаючи номери, дати і реквізити. "
    "Перед кожною сторінкою пиши рядок «=== Сторінка N ===», де N — номер сторінки документа. "
    "Не переказуй, не скорочуй, не виправляй, не додавай коментарів. "
    "Якщо фрагмент нечитабельний, познач його [нерозбірливо]."
)


class AuthExpired(RuntimeError):
    pass


def query(notebook: str, source_id: str, question: str) -> str:
    proc = subprocess.run(
        ["nlm", "notebook", "query", notebook, question, "--source-ids", source_id,
         "--new-conversation", "--json", "--timeout", "300"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (proc.stdout or "") + (proc.stderr or "")
    if any(m in out for m in AUTH_MARKERS):
        raise AuthExpired("Вхід у NotebookLM протерміновано: виконайте `nlm login`.")
    if proc.returncode != 0:
        raise RuntimeError(f"nlm query: код {proc.returncode}: {out.strip()[:300]}")
    return json.loads(proc.stdout).get("answer", "")


def split_pages(text: str) -> dict[int, str]:
    parts = PAGE_RE.split(text)
    return {int(parts[i]): parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}


def transcribe(notebook: str, source_id: str, n_pages: int, batch: int) -> dict[int, str]:
    pages: dict[int, str] = {}
    if n_pages <= batch:
        return split_pages(query(notebook, source_id, PROMPT.format(pages="")))
    for start in range(1, n_pages + 1, batch):
        end = min(start + batch - 1, n_pages)
        got = split_pages(query(notebook, source_id, PROMPT.format(pages=f" — лише сторінки з {start} по {end}")))
        pages.update({k: v for k, v in got.items() if start <= k <= end})
    return pages


def tokens(text: str) -> list[str]:
    text = NUM_SPACE_RE.sub("", CIT_RE.sub("", text))
    return TOKEN_RE.findall(text)


def substantive_diffs(a: str, b: str) -> list[dict]:
    ta, tb = tokens(a), tokens(b)
    ops = [o for o in difflib.SequenceMatcher(None, ta, tb, autojunk=False).get_opcodes() if o[0] != "equal"]
    deleted = [tuple(ta[i1:i2]) for op, i1, i2, _, _ in ops if op == "delete"]
    inserted = [tuple(tb[j1:j2]) for op, _, _, j1, j2 in ops if op == "insert"]
    moved = set(deleted) & set(inserted)  # той самий фрагмент в іншому місці — перестановка рядків
    result = []
    for op, i1, i2, j1, j2 in ops:
        s1, s2 = ta[i1:i2], tb[j1:j2]
        if not any(re.match(r"\w", t) for t in s1 + s2):
            continue  # лише розділові знаки чи розмітка
        if (op == "delete" and tuple(s1) in moved) or (op == "insert" and tuple(s2) in moved):
            continue
        result.append({
            "context": " ".join(ta[max(0, i1 - 6):i1]),
            "run1": " ".join(s1),
            "run2": " ".join(s2),
            "digits": any(re.search(r"\d", t) for t in s1 + s2),
        })
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--notebook", required=True)
    ap.add_argument("--texts", required=True, type=Path, help="Тека з index.json від nlm_export.py")
    ap.add_argument("--pages-per-query", type=int, default=8)
    args = ap.parse_args()

    index_path = args.texts / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    summary = []
    try:
        for src in index:
            if "image_only" not in src.get("flags", []):
                continue
            n = int(src.get("image_pages") or 0)
            run1 = transcribe(args.notebook, src["id"], n, args.pages_per_query)
            run2 = transcribe(args.notebook, src["id"], n, args.pages_per_query)
            diffs = []
            for p in sorted(set(run1) | set(run2)):
                for d in substantive_diffs(run1.get(p, ""), run2.get(p, "")):
                    diffs.append({"page": p, **d})
            text = "\n\n".join(f"=== Сторінка {p} ===\n{run1[p]}" for p in sorted(run1))
            Path(src["path"]).write_text(text, encoding="utf-8")
            (args.texts / f"{src['id']}.diff.json").write_text(
                json.dumps(diffs, ensure_ascii=False, indent=2), encoding="utf-8")
            missing = [p for p in range(1, n + 1) if p not in run1]
            src.update({"transcribed": True, "chars": len(text), "pages_transcribed": len(run1),
                        "pages_missing": missing, "diffs": len(diffs),
                        "diffs_with_digits": sum(d["digits"] for d in diffs),
                        "illegible": text.count("[нерозбірливо]")})
            summary.append({k: src[k] for k in ("id", "title", "image_pages", "pages_transcribed",
                                                  "pages_missing", "diffs", "diffs_with_digits", "illegible")})
    except AuthExpired as exc:
        print(json.dumps({"error": "auth_expired", "message": str(exc)}, ensure_ascii=False))
        return 3
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"error": "transcribe_failed", "message": str(exc)}, ensure_ascii=False))
        return 1
    finally:
        index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({"transcribed": summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
