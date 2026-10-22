#!/usr/bin/env python3
"""Детермінований калькулятор ціни позову (борг, 3% річних, інфляційні втрати, пеня) та судового збору."""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


def d2(val: object) -> Decimal:
    return Decimal(str(val)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def fmt_money(val: Decimal) -> str:
    s = f"{val:,.2f}".replace(",", "\u00a0").replace(".", ",")
    return f"{s}\u00a0грн"


def parse_date(s: str) -> datetime.date:
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Невірний формат дати: {s!r} (очікується ДД.ММ.РРРР або РРРР-ММ-ДД)")


def is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


class InputError(ValueError):
    """Помилка вхідних даних: розрахунок не виконується."""


PM_HELP = (
    "Прожитковий мінімум (ПМ) — обов'язковий параметр. Значення за замовчуванням немає, "
    "бо розмір ПМ змінюється щороку законом про Державний бюджет. Для судового збору береться ПМ "
    "для працездатних осіб, встановлений законом на 1 січня року подання заяви (ч. 1 ст. 4 "
    "Закону України «Про судовий збір») [Перевірити чинну редакцію]. Приклад вхідних даних:\n"
    '  "subsistence_minimum": 0000.00,\n'
    '  "subsistence_minimum_source": {\n'
    '    "act": "Закону України «Про Державний бюджет України на РРРР рік» (у родовому відмінку)",\n'
    '    "pinpoint": "ст. N",\n'
    '    "as_of": "01.01.РРРР",\n'
    '    "url": "https://zakon.rada.gov.ua/..."\n'
    "  }"
)


def read_subsistence_minimum(spec: dict) -> tuple[Decimal, dict]:
    """Повертає ПМ і його джерело. Без значення і джерела розрахунок не виконується."""
    raw = spec.get("subsistence_minimum")
    if raw is None or str(raw).strip() == "":
        raise InputError("Не задано subsistence_minimum.\n" + PM_HELP)
    try:
        pm = d2(raw)
    except Exception as exc:
        raise InputError(f"subsistence_minimum не є числом: {raw!r}.\n" + PM_HELP) from exc
    if pm <= 0:
        raise InputError(f"subsistence_minimum має бути більшим за нуль, отримано {pm}.\n" + PM_HELP)

    src = spec.get("subsistence_minimum_source")
    if not isinstance(src, dict):
        raise InputError("Не задано subsistence_minimum_source (об'єкт з полями act, pinpoint, as_of, url).\n" + PM_HELP)
    missing = [k for k in ("act", "pinpoint", "as_of") if not str(src.get(k, "")).strip()]
    if missing:
        raise InputError("У subsistence_minimum_source бракує полів: " + ", ".join(missing) + ".\n" + PM_HELP)
    as_of = parse_date(str(src["as_of"]))
    source = {
        "act": str(src["act"]).strip(),
        "pinpoint": str(src["pinpoint"]).strip(),
        "as_of": as_of.strftime("%d.%m.%Y"),
        "url": str(src.get("url", "")).strip(),
    }
    return pm, source


def pm_reference(pm: Decimal, source: dict) -> str:
    ref = f"ПМ {fmt_money(pm)} станом на {source['as_of']} ({source['pinpoint']} {source['act']}"
    if source["url"]:
        ref += f", {source['url']}"
    return ref + ")"


def calculate_claim(spec: dict) -> dict:
    principal = d2(spec.get("principal", 0))
    jurisdiction = str(spec.get("jurisdiction", "ГПК")).upper()
    party_type = str(spec.get("party_type", "legal")).lower()
    electronic_court = bool(spec.get("electronic_court", True))
    pm, pm_source = read_subsistence_minimum(spec)
    non_pecuniary_claims = int(spec.get("non_pecuniary_claims", 0))

    periods = spec.get("periods") or []
    rows_3pct: list[list[str]] = []
    total_3pct = Decimal("0.00")
    rows_penalty: list[list[str]] = []
    total_penalty = Decimal("0.00")

    for p in periods:
        d_start = parse_date(p["start"])
        d_end = parse_date(p["end"])
        days = (d_end - d_start).days + 1
        if days <= 0:
            raise ValueError(f"Період {p['start']} – {p['end']} має <= 0 днів")
        amount = d2(p.get("amount", principal))
        days_in_yr = int(p.get("days_in_year", 366 if is_leap(d_start.year) else 365))
        period_str = f"{d_start.strftime('%d.%m.%Y')}–{d_end.strftime('%d.%m.%Y')}"

        interest_3 = d2(amount * Decimal("0.03") * Decimal(days) / Decimal(days_in_yr))
        total_3pct += interest_3
        rows_3pct.append([
            period_str,
            fmt_money(amount),
            str(days),
            str(days_in_yr),
            fmt_money(interest_3),
        ])

        if "penalty_rate_daily_pct" in p or "nbu_rate_pct" in p:
            if "penalty_rate_daily_pct" in p:
                daily_pct = Decimal(str(p["penalty_rate_daily_pct"]))
                rate_label = f"{daily_pct}% / день"
                pen_val = d2(amount * (daily_pct / Decimal("100")) * Decimal(days))
            else:
                nbu = Decimal(str(p["nbu_rate_pct"]))
                double_nbu = nbu * Decimal("2")
                rate_label = f"подвійна облікова ставка НБУ ({double_nbu}% річних)"
                pen_val = d2(amount * (double_nbu / Decimal("100")) * Decimal(days) / Decimal(days_in_yr))
            total_penalty += pen_val
            rows_penalty.append([
                period_str,
                fmt_money(amount),
                str(days),
                rate_label,
                fmt_money(pen_val),
            ])

    cpi_list = [Decimal(str(x)) for x in (spec.get("cpi_indices_pct") or [])]
    inflation_losses = Decimal("0.00")
    cumulative_cpi = Decimal("1.0")
    for idx in cpi_list:
        cumulative_cpi *= (idx / Decimal("100"))
    if cpi_list:
        inflation_losses = d2(principal * cumulative_cpi - principal)

    total_claim_price = d2(principal + total_3pct + inflation_losses + total_penalty)

    fee_pecuniary = Decimal("0.00")
    fee_formula_desc = ""
    if total_claim_price > 0:
        if jurisdiction == "ГПК":
            raw_fee = d2(total_claim_price * Decimal("0.015"))
            min_fee = d2(pm * Decimal("1"))
            max_fee = d2(pm * Decimal("350"))
            fee_pecuniary = max(min_fee, min(raw_fee, max_fee))
            fee_formula_desc = f"1,5% ціни позову, але не менше 1 ПМ ({fmt_money(min_fee)}) і не більше 350 ПМ ({fmt_money(max_fee)}) (пп. 1 п. 2 ч. 2 ст. 4 ЗУ «Про судовий збір»)"
        elif jurisdiction == "ЦПК":
            if party_type in ("individual", "fop"):
                raw_fee = d2(total_claim_price * Decimal("0.01"))
                min_fee = d2(pm * Decimal("0.4"))
                max_fee = d2(pm * Decimal("5"))
                fee_pecuniary = max(min_fee, min(raw_fee, max_fee))
                fee_formula_desc = f"1% ціни позову, але не менше 0,4 ПМ ({fmt_money(min_fee)}) і не більше 5 ПМ ({fmt_money(max_fee)}) (пп. 1 п. 1 ч. 2 ст. 4 ЗУ «Про судовий збір»)"
            else:
                raw_fee = d2(total_claim_price * Decimal("0.015"))
                min_fee = d2(pm * Decimal("1"))
                max_fee = d2(pm * Decimal("350"))
                fee_pecuniary = max(min_fee, min(raw_fee, max_fee))
                fee_formula_desc = f"1,5% ціни позову, але не менше 1 ПМ ({fmt_money(min_fee)}) і не більше 350 ПМ ({fmt_money(max_fee)})"
        elif jurisdiction in ("КАСУ", "КАС"):
            if party_type == "individual":
                raw_fee = d2(total_claim_price * Decimal("0.01"))
                min_fee = d2(pm * Decimal("0.4"))
                max_fee = d2(pm * Decimal("5"))
            else:
                raw_fee = d2(total_claim_price * Decimal("0.015"))
                min_fee = d2(pm * Decimal("1"))
                max_fee = d2(pm * Decimal("10"))
            fee_pecuniary = max(min_fee, min(raw_fee, max_fee))
            fee_formula_desc = "За ставкою майнового адміністративного позову (пп. 1 п. 3 ч. 2 ст. 4 ЗУ «Про судовий збір»)"

    fee_non_pecuniary = Decimal("0.00")
    if non_pecuniary_claims > 0:
        if jurisdiction == "ГПК":
            unit_fee = d2(pm * Decimal("1"))
        else:
            unit_fee = d2(pm * (Decimal("0.4") if party_type == "individual" else Decimal("1")))
        fee_non_pecuniary = d2(unit_fee * Decimal(non_pecuniary_claims))

    base_court_fee = d2(fee_pecuniary + fee_non_pecuniary)
    final_court_fee = d2(base_court_fee * Decimal("0.8")) if electronic_court else base_court_fee
    if fee_formula_desc:
        fee_formula_desc += "; " + pm_reference(pm, pm_source)

    summary_rows = []
    if principal > 0:
        summary_rows.append(["Основна заборгованість", "Договір та первинні документи", fmt_money(principal)])
    if total_3pct > 0:
        summary_rows.append(["3% річних (ч. 2 ст. 625 ЦК України)", "Сума × 3% × Днів / Днів у році", fmt_money(total_3pct)])
    if inflation_losses > 0:
        summary_rows.append([
            "Інфляційні втрати (ч. 2 ст. 625 ЦК України)",
            f"Сума × сукупний індекс інфляції ({round(float(cumulative_cpi), 4)}) – Сума",
            fmt_money(inflation_losses),
        ])
    if total_penalty > 0:
        summary_rows.append(["Пеня за прострочення зобов'язання", "Сума × Ставка × Днів прострочення", fmt_money(total_penalty)])
    if summary_rows:
        summary_rows.append(["Разом ціна позову", "Сума всіх грошових вимог", fmt_money(total_claim_price)])

    return {
        "jurisdiction": jurisdiction,
        "subsistence_minimum": str(pm),
        "subsistence_minimum_source": pm_source,
        "subsistence_minimum_reference": pm_reference(pm, pm_source),
        "principal": str(principal),
        "total_3pct": str(total_3pct),
        "inflation_losses": str(inflation_losses),
        "total_penalty": str(total_penalty),
        "total_claim_price": str(total_claim_price),
        "total_claim_price_formatted": fmt_money(total_claim_price),
        "base_court_fee": str(base_court_fee),
        "electronic_court_discount_applied": electronic_court,
        "final_court_fee": str(final_court_fee),
        "final_court_fee_formatted": fmt_money(final_court_fee),
        "fee_formula_description": fee_formula_desc,
        "tables": {
            "summary_table": {
                "type": "table",
                "caption": "Таблиця. Зведений розрахунок ціни позову",
                "columns": ["Складова вимоги", "Формула / підстава нарахування", "Сума до стягнення"],
                "widths": [2800, 4071, 2200],
                "rows": summary_rows,
            },
            "three_percent_table": {
                "type": "table",
                "caption": "Таблиця. Розрахунок 3% річних за ч. 2 ст. 625 ЦК України",
                "columns": [
                    "Період прострочення",
                    "База нарахування",
                    "Днів прострочення",
                    "Днів у році",
                    "Сума 3% річних (База × 3% × Днів / Рік)",
                ],
                "widths": [1900, 1800, 1400, 1300, 2671],
                "rows": rows_3pct,
            } if rows_3pct else None,
            "penalty_table": {
                "type": "table",
                "caption": "Таблиця. Розрахунок пені за прострочення грошового зобов'язання",
                "columns": [
                    "Період прострочення",
                    "База нарахування",
                    "Днів прострочення",
                    "Ставка пені",
                    "Сума пені до стягнення",
                ],
                "widths": [1900, 1800, 1400, 1900, 2071],
                "rows": rows_penalty,
            } if rows_penalty else None,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Калькулятор ціни позову та судового збору ClaimForge-UA")
    parser.add_argument("input", type=Path, help="JSON-файл із вхідними параметрами розрахунку")
    parser.add_argument("--out", type=Path, help="Куди зберегти JSON-результат")
    args = parser.parse_args()

    spec = json.loads(args.input.read_text(encoding="utf-8-sig"))
    try:
        res = calculate_claim(spec)
    except InputError as exc:
        print(json.dumps({"error": "input", "message": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    out_text = json.dumps(res, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(out_text + "\n", encoding="utf-8")
    print(out_text)
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8")  # Windows: вивід у файл інакше йде в кодуванні системи
    sys.exit(main())
