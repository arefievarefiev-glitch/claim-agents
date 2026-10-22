#!/usr/bin/env python3
"""Міст до Gemini 4 Argon через Antigravity CLI (`agy`) з входом Google AI Pro.

Один виклик = одне завдання: текст завдання з файлу (UTF-8) + JSON-схема відповіді.
Результат — JSON за схемою у файлі --out; короткий підсумок у stdout.
Повний текст завдання не повертається в stdout, тож не потрапляє в контекст Claude.

Перевірено на Windows 11, agy 1.4.4 (Ф0, 22.10.2026):
  * текст іде через stdin у форматі stream-json: {"event":"user","message":{"content":"..."}};
    прапорець -p приймає запит як значення і обмежений довжиною командного рядка;
  * повідомлення серіалізується з ensure_ascii=True (екранування \\uXXXX), тож
    кодування консолі Windows кирилицю не псує;
  * --json-schema обов'язковий: без нього agy-агент схильний до «дослідження» робочої теки;
  * виклик іде в порожній тимчасовій теці, а преамбула забороняє інструменти;
    якщо модель усе ж викликала інструмент, це фіксується в підсумку (tools_used);
  * повідомлення понад ~150 тис. символів agy виносить у файл, і модель читає його
    частинами з пропусками (тест: 2 з 3 фактів при 228 тис., 0 з 3 при 304 тис.).
    Тому досьє (--context) ділиться на частини до CHUNK_CHARS по межах абзаців і йде
    послідовними повідомленнями однієї розмови, а завдання — останнім повідомленням
    (тест: 6 з 6 фактів у 360 тис. символів).

Використання:
    python argon.py --task task.md --schema schema.json --out result.json \\
        [--context dossier.md ...] [--effort low|medium|high] [--log events.jsonl] [--role extract]

Порядок контекстних файлів зберігайте однаковим між викликами: неявне кешування
спрацьовує на однаковому префіксі розмови.

Коди виходу: 0 — успіх; 2 — помилка вхідних даних; 3 — agy не знайдено або вхід
протерміновано; 4 — модель не повернула структурованої відповіді; 5 — тайм-аут.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PREAMBLE = (
    "This is a pure text-analysis task, not a coding task. All input is inside this message. "
    "Do not call any tools, do not read or inspect files, do not run commands. "
    "Answer immediately with the structured output that matches the provided JSON schema. "
    "Write all natural-language fields in Ukrainian unless the task says otherwise.\n\n"
)
EFFORTS = ("low", "medium", "high")
CHUNK_CHARS = 120_000
PART_NOTE = ("DOSSIER PART {i} of {n}. Read and retain it in full; the task comes in the last message. "
             "Reply only with the word OK.\n\n")
AUTH_HINTS = ("sign in", "login", "log in", "authenticat", "unauthor", "credential")


def find_agy(explicit: str | None) -> str:
    candidates = [explicit, shutil.which("agy"),
                  os.path.join(os.environ.get("LOCALAPPDATA", ""), "agy", "bin", "agy.exe")]
    for c in candidates:
        if c and Path(c).exists():
            return c
    raise FileNotFoundError("Не знайдено agy. Встановіть Antigravity CLI з власного вікна PowerShell.")


def split_chunks(text: str, limit: int = CHUNK_CHARS) -> list[str]:
    """Ділить текст на частини до limit символів по межах абзаців (рядків)."""
    chunks, cur, size = [], [], 0
    for para in text.splitlines(keepends=True):
        while len(para) > limit:  # надто довгий абзац ріжеться по пробілу
            cut = para.rfind(" ", 0, limit)
            if cut <= 0:
                cut = limit
            if cur:
                chunks.append("".join(cur)); cur, size = [], 0
            chunks.append(para[:cut]); para = para[cut:]
        if size + len(para) > limit and cur:
            chunks.append("".join(cur)); cur, size = [], 0
        cur.append(para); size += len(para)
    if cur:
        chunks.append("".join(cur))
    return [c for c in chunks if c.strip()]


def build_messages(task: str, context: str) -> list[str]:
    if not context.strip():
        return [PREAMBLE + task]
    parts = split_chunks(context)
    msgs = [PREAMBLE + PART_NOTE.format(i=i, n=len(parts)) + c for i, c in enumerate(parts, 1)]
    msgs.append(PREAMBLE + "The dossier is complete. Using ALL dossier parts above, perform the task.\n\n" + task)
    return msgs


def run(task: str, context: str, schema_path: Path, effort: str, agy: str, timeout: int | None) -> dict:
    model = f"gemini-4-argon-{effort}"
    cmd = [agy, "--model", model, "--input-format", "stream-json", "--output-format", "stream-json",
           "--disable-slash-commands", "--json-schema", str(schema_path.resolve())]
    msgs = build_messages(task, context)
    message = "".join(json.dumps({"event": "user", "message": {"content": m}}, ensure_ascii=True) + "\n" for m in msgs)
    work = tempfile.mkdtemp(prefix="argon_")
    started = time.time()
    try:
        proc = subprocess.run(cmd, cwd=work, input=message, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    tools, result, turns = [], {}, []
    for line in proc.stdout.splitlines():
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("event") == "step_update":
            st = ev["step_update"]
            if st.get("step_type") == "tool" and st.get("state") == "ACTIVE":
                tools.append(st.get("tool_name"))
        elif ev.get("event") == "result":
            result = ev["result"]
            turns.append(result.get("usage") or {})
    return {
        "model": model,
        "seconds": round(time.time() - started, 1),
        "returncode": proc.returncode,
        "status": result.get("status"),
        "structured": result.get("structured_output"),
        "error": result.get("error") or proc.stderr.strip()[:1000],
        "usage": result.get("usage"),
        "messages": len(msgs),
        "input_tokens_all_turns": sum(u.get("input_tokens", 0) for u in turns),
        "conversation_id": result.get("conversation_id"),
        "tools_used": tools,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--task", required=True, type=Path, help="Файл із текстом завдання (UTF-8)")
    ap.add_argument("--context", type=Path, action="append", default=[],
                    help="Файл досьє (UTF-8), можна кілька; йде перед завданням частинами")
    ap.add_argument("--schema", required=True, type=Path, help="JSON-схема відповіді")
    ap.add_argument("--out", required=True, type=Path, help="Куди записати JSON-відповідь")
    ap.add_argument("--effort", default="medium", choices=EFFORTS)
    ap.add_argument("--role", default="", help="Роль у конвеєрі для журналу (extract, strategy, practice, review)")
    ap.add_argument("--log", type=Path, help="Журнал викликів events.jsonl")
    ap.add_argument("--agy", help="Шлях до agy.exe, якщо не в PATH")
    ap.add_argument("--timeout", type=int, default=0, help="Секунди; 0 — без обмеження")
    args = ap.parse_args()

    try:
        task = args.task.read_text(encoding="utf-8-sig")
        context = "\n\n".join(c.read_text(encoding="utf-8-sig") for c in args.context)
        json.loads(args.schema.read_text(encoding="utf-8-sig"))
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"error": "input", "message": str(exc)}, ensure_ascii=False))
        return 2
    if args.schema.read_bytes().startswith(b"\xef\xbb\xbf"):
        clean = Path(tempfile.mkdtemp(prefix="argon_schema_")) / "schema.json"
        clean.write_text(args.schema.read_text(encoding="utf-8-sig"), encoding="utf-8")
        args.schema = clean  # agy не приймає схему з міткою BOM

    try:
        agy = find_agy(args.agy)
        res = run(task, context, args.schema, args.effort, agy, args.timeout or None)
    except FileNotFoundError as exc:
        print(json.dumps({"error": "agy_missing", "message": str(exc)}, ensure_ascii=False))
        return 3
    except subprocess.TimeoutExpired:
        print(json.dumps({"error": "timeout", "seconds": args.timeout}, ensure_ascii=False))
        return 5

    summary = {k: v for k, v in res.items() if k != "structured"}
    summary.update({"role": args.role, "task_chars": len(task), "context_chars": len(context), "out": str(args.out),
                    "at": datetime.datetime.now().isoformat(timespec="seconds")})
    if args.log:
        args.log.parent.mkdir(parents=True, exist_ok=True)
        with args.log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"event": "argon_call", **summary}, ensure_ascii=False) + "\n")

    if res["structured"] is None:
        err = (res["error"] or "").lower()
        summary["error"] = "auth" if any(h in err for h in AUTH_HINTS) else "no_structured_output"
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 3 if summary["error"] == "auth" else 4

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(res["structured"], ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
