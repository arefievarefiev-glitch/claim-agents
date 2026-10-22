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
    якщо модель усе ж викликала інструмент, це фіксується в підсумку (tools_used).

Використання:
    python argon.py --task task.md --schema schema.json --out result.json \\
        [--effort low|medium|high] [--log events.jsonl] [--role extract]

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
AUTH_HINTS = ("sign in", "login", "log in", "authenticat", "unauthor", "credential")


def find_agy(explicit: str | None) -> str:
    candidates = [explicit, shutil.which("agy"),
                  os.path.join(os.environ.get("LOCALAPPDATA", ""), "agy", "bin", "agy.exe")]
    for c in candidates:
        if c and Path(c).exists():
            return c
    raise FileNotFoundError("Не знайдено agy. Встановіть Antigravity CLI з власного вікна PowerShell.")


def run(task: str, schema_path: Path, effort: str, agy: str, timeout: int | None) -> dict:
    model = f"gemini-4-argon-{effort}"
    cmd = [agy, "--model", model, "--input-format", "stream-json", "--output-format", "stream-json",
           "--disable-slash-commands", "--json-schema", str(schema_path.resolve())]
    message = json.dumps({"event": "user", "message": {"content": PREAMBLE + task}}, ensure_ascii=True) + "\n"
    work = tempfile.mkdtemp(prefix="argon_")
    started = time.time()
    try:
        proc = subprocess.run(cmd, cwd=work, input=message, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    tools, result = [], {}
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
    return {
        "model": model,
        "seconds": round(time.time() - started, 1),
        "returncode": proc.returncode,
        "status": result.get("status"),
        "structured": result.get("structured_output"),
        "error": result.get("error") or proc.stderr.strip()[:1000],
        "usage": result.get("usage"),
        "conversation_id": result.get("conversation_id"),
        "tools_used": tools,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--task", required=True, type=Path, help="Файл із текстом завдання (UTF-8)")
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
        res = run(task, args.schema, args.effort, agy, args.timeout or None)
    except FileNotFoundError as exc:
        print(json.dumps({"error": "agy_missing", "message": str(exc)}, ensure_ascii=False))
        return 3
    except subprocess.TimeoutExpired:
        print(json.dumps({"error": "timeout", "seconds": args.timeout}, ensure_ascii=False))
        return 5

    summary = {k: v for k, v in res.items() if k != "structured"}
    summary.update({"role": args.role, "task_chars": len(task), "out": str(args.out),
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
