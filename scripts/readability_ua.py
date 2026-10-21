#!/usr/bin/env python3
"""Математичний аудит читабельності та процесуальний лінтер українського юридичного тексту."""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

VOWELS = set("аеєиіїоуюяАЕЄИІЇОУЮЯ")
WORD_RE = re.compile(r"[А-ЯІЇЄҐа-яіїєґ][А-ЯІЇЄҐа-яіїєґ'’-]*")

ABBR_RE = re.compile(
    r"(^|[\s(«„])(ст|чч|ч|п|пп|абз|розд|грн|коп|тис|млн|млрд|м|смт|вул|просп|пров|буд|кв|обл|р-н|р|рр|див|напр|зокр|ім|т|с|а\.\s*с)\.",
    re.IGNORECASE,
)

STATUTORY_PARTICIPLE_WHITELIST = (
    "контролюючий орган",
    "контролюючого органу",
    "контролюючому органу",
    "контролюючим органом",
    "контролюючі органи",
    "контролюючих органів",
    "арбітражний керуючий",
    "арбітражного керуючого",
    "арбітражному керуючому",
    "арбітражним керуючим",
    "виконуючий обов'язки",
    "виконуючого обов'язки",
    "забезпечення позову",
    "забезпечення доказів",
)

SPLIT_VERBS = (
    "здійснювати", "здійснити", "здійснює", "здійснив", "здійснила", "здійснили",
    "проводити", "провести", "проводить", "провів", "провела", "провели",
    "виконувати", "виконати", "виконує", "виконав", "виконала", "виконали",
    "надавати", "надати", "надає", "надав", "надала", "надали",
    "вживати", "вжити", "вживає", "вжив", "вжила", "вжили",
    "приймати", "прийняти", "приймає", "прийняв", "прийняла", "прийняли",
)

SPLIT_NOMINALS = (
    "здійснення", "проведення", "виконання", "надання", "вжиття", "прийняття", "внесення",
)

FORBIDDEN_PATTERNS: list[tuple[str, str, str]] = [
    (r"\bданий\b|\bданого\b|\bданому\b|\bданим\b|\bдана\b|\bданої\b|\bданій\b|\bдану\b|\bдані\b|\bданих\b",
     "Канцеляризм «даний»", "Замініть на «цей» або вилучіть"),
    (r"\bвказаний\b|\bвказаного\b|\bвказаному\b|\bвказана\b|\bвказаної\b|\bвказані\b|\bвказаних\b",
     "Канцеляризм «вказаний»", "Замініть на «цей» або точне посилання на пункт"),
    (r"\bвищезазначен\w*|\bвищенаведен\w*|\bнижченаведен\w*|\bзазначен\w+\s+вище",
     "Неозначене відсилання («вищезазначений» тощо)", "Дайте точне посилання «див. п. X.Y.Z» або повторіть факт одним рядком"),
    (r"\bу\s+відповідності\s+з\b|\bу\s+відповідності\s+до\b",
     "Калька «у відповідності з/до»", "Замініть на «відповідно до»"),
    (r"\bз\s+метою\b",
     "Канцеляризм «з метою»", "Замініть на «для»"),
    (r"\bу\s+разі,\s*якщо\b",
     "Надлишкова конструкція «у разі, якщо»", "Замініть на «якщо»"),
    (r"\bна\s+даний\s+час\b",
     "Канцеляризм «на даний час»", "Замініть на «наразі» або вкажіть точну дату"),
    (r"\bбанківськ\w+\s+дн\w*",
     "Невизначений термін «банківські дні»", "Замініть на «робочі дні» або «календарні дні»"),
    (r"як\s+вбачається\s+з\s+матеріалів\s+справи|що\s+підтверджується\s+доданими\s+документами",
     "Безпредметне посилання на матеріали справи", "Поставте точне посилання у форматі «(додаток N, с. M)»"),
    (r"\bтам\s+само\b",
     "Заборонене посилання «там само»", "Вкажіть явний номер додатка та сторінку або коротку назву постанови ВС"),
    (r"на\s+підставі\s+викладеного,?\s+керуючись|керуючись\s+ст\.?\s*ст\.?",
     "Заборонена формула «На підставі викладеного, керуючись ст.ст. …»", "Переходьте одразу до «Прошу суд:»"),
    (r"звертається\s+до\s+суду\s+з\s+огляду\s+на\s+наступне",
     "Слабкий зачин розділу «Суть»", "Почніть перше речення Суті одразу з головного прохання або правового висновку"),
    (r"\bне\s+стверджує,\s+що\b|\bне\s+стверджуємо,\s+що\b",
     "Оборонна конструкція «не стверджує, що…»", "Вилучіть: не підказуйте опоненту заперечення"),
    (r"\bочевидно\b|\bабсолютно\b|\bгрубо\b|\bявно\b|\bбезпідставно\b|\bцинізм\w*|\bманіпуляц\w*|\bсвавілл\w*",
     "Емоційний підсилювач або оцінка опонента", "Вилучіть оцінне слово і замініть його перевірюваним фактом із посиланням на доказ"),
]


def count_syllables(word: str) -> int:
    return sum(1 for ch in word if ch in VOWELS)


def extract_words(text: str) -> list[str]:
    return WORD_RE.findall(text)


def mask_abbreviations(text: str) -> str:
    masked = ABBR_RE.sub(lambda m: m.group(0).replace(".", "·"), text)
    masked = re.sub(r"(^|[\s(«])[А-ЯІЇЄҐA-Z]\.", lambda m: m.group(0).replace(".", "·"), masked)
    masked = re.sub(r"\d+\.\d+(\.\d+)*", lambda m: m.group(0).replace(".", "·"), masked)
    masked = re.sub(r"https?://\S+", "URL", masked)
    return masked


def split_sentences(text: str) -> list[str]:
    masked = mask_abbreviations(text.strip())
    parts = re.split(r"(?<=[.!?…])\s+(?=[«(„\"\[]?[А-ЯІЇЄҐA-Z0-9])", masked)
    out = []
    for p in parts:
        restored = p.replace("·", ".").strip()
        if restored:
            out.append(restored)
    return out


def looks_like_two_sentences(text: str) -> bool:
    return len(split_sentences(text)) >= 2


def is_verbal_noun(word: str) -> bool:
    w = word.lower()
    if len(w) < 6:
        return False
    return w.endswith(("ння", "ття", "ація", "ізація", "ення", "іння"))


def is_active_participle(word: str) -> bool:
    w = word.lower()
    if len(w) < 6:
        return False
    if re.search(r"(уч|юч)(ий|ого|ому|им|ім|а|ої|ій|у|ою|е|і|их|ими)$", w):
        return True
    if re.search(r"(ач|яч)(ий|ого|ому|им|их|ими)$", w):
        return True
    return False


def is_adverbial_participle(word: str) -> bool:
    w = word.lower()
    if len(w) < 5:
        return False
    return w.endswith(("вши", "ши", "учи", "ючи", "ачи", "ячи"))


def is_genitive_candidate(word: str) -> bool:
    w = word.lower()
    if len(w) < 5:
        return False
    return w.endswith(("ння", "ття", "ості", "ства", "ції", "ів", "ень", "и", "у", "а", "я"))


def find_genitive_chains(sentence: str) -> list[str]:
    words_in_sent = extract_words(sentence)
    if len(words_in_sent) < 15:
        return []
    chunks = re.split(r"[,:;()–—«»\"]|\b(?:в|у|на|до|від|за|при|про|під|над|між|для|без|через|після|перед|щодо|згідно|відповідно)\b", sentence, flags=re.IGNORECASE)
    chains: list[str] = []
    for chunk in chunks:
        c_words = extract_words(chunk)
        run: list[str] = []
        for w in c_words:
            if is_genitive_candidate(w):
                run.append(w)
            else:
                if len(run) >= 3 and any(is_verbal_noun(x) or x.lower().endswith(("ості", "ства", "ції")) for x in run):
                    chains.append(" ".join(run))
                run = []
        if len(run) >= 3 and any(is_verbal_noun(x) or x.lower().endswith(("ості", "ства", "ції")) for x in run):
            chains.append(" ".join(run))
    return chains


def plain_runs(runs_spec: object) -> str:
    if runs_spec is None:
        return ""
    if isinstance(runs_spec, str):
        return runs_spec
    if isinstance(runs_spec, list):
        parts = []
        for r in runs_spec:
            if isinstance(r, str):
                parts.append(r)
            elif isinstance(r, dict):
                parts.append(str(r.get("text") or r.get("url") or r.get("cached") or ""))
        return "".join(parts)
    return str(runs_spec)


def extract_units_from_content_json(data: dict) -> tuple[list[tuple[str, str]], list[str]]:
    units: list[tuple[str, str]] = []
    structural_errors: list[str] = []
    flat = bool(data.get("flat"))
    s = 0
    ss = 0
    p = 0
    section_has_sub: dict[int, bool] = {}

    for item in data.get("body") or []:
        if not isinstance(item, dict):
            continue
        itype = item.get("type")
        if itype == "section":
            s += 1
            ss = 0
            p = 0
            section_has_sub[s] = False
            sec_text = str(item.get("text") or "").strip()
            units.append((f"Розділ {s}", sec_text))
            if re.search(r"список\s+скорочень|перелік\s+скорочень|визначення\s+термінів|глосарій", sec_text, re.IGNORECASE):
                structural_errors.append(f"Розділ {s} («{sec_text}»): окремий список скорочень/визначень заборонено (вибір 5А).")
        elif itype == "subsection":
            ss += 1
            p = 0
            if s > 0:
                section_has_sub[s] = True
            sub_text = str(item.get("text") or "").strip()
            units.append((f"Підзаголовок {s}.{ss}", sub_text))
            sub_words = len(extract_words(sub_text))
            if s >= 3 and (sub_words < 8 or sub_words > 40):
                structural_errors.append(
                    f"Підзаголовок {s}.{ss} має {sub_words} слів (орієнтир для тези-підзаголовка розділу доводів: 15–35 слів за формулою «[Висновок], оскільки [причина]»)."
                )
        elif itype == "para":
            p += 1
            num = f"{p}" if flat else f"{s}.{ss}.{p}"
            txt = plain_runs(item.get("runs")).strip()
            units.append((f"п. {num}", txt))
            if looks_like_two_sentences(txt):
                structural_errors.append(
                    f"п. {num}: виявлено більше одного речення в одному нумерованому абзаці (порушення правила «1 абзац = 1 речення», вибір 2А): «{txt[:110]}…»"
                )
        elif itype == "claims":
            for idx, c in enumerate(item.get("items") or [], start=1):
                units.append((f"Вимога {idx}", plain_runs(c).strip()))

    if not flat:
        for sec_num, has_sub in section_has_sub.items():
            if not has_sub:
                structural_errors.append(
                    f"Розділ {sec_num}: відсутній підзаголовок рівня {sec_num}.1 (схема 1. -> 1.1. -> 1.1.1. має бути єдиною в усьому документі)."
                )
    return units, structural_errors


def extract_units_from_markdown(md_text: str) -> tuple[list[tuple[str, str]], list[str]]:
    units: list[tuple[str, str]] = []
    structural_errors: list[str] = []
    for line in md_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("|") or stripped.startswith(">"):
            continue
        if re.match(r"^#{1,3}\s+", stripped):
            heading = re.sub(r"^#{1,3}\s+", "", stripped)
            units.append(("Заголовок", heading))
            if re.search(r"список\s+скорочень|перелік\s+скорочень|визначення\s+термінів|глосарій", heading, re.IGNORECASE):
                structural_errors.append(f"Заголовок «{heading}»: окремий список скорочень/визначень заборонено (вибір 5А).")
            continue
        m_para = re.match(r"^(\d+\.\d+\.\d+)\.\s+(.+)$", stripped)
        if m_para:
            num, body = m_para.group(1), m_para.group(2)
            units.append((f"п. {num}", body))
            if looks_like_two_sentences(body):
                structural_errors.append(
                    f"п. {num}: виявлено більше одного речення в одному нумерованому абзаці (вибір 2А): «{body[:110]}…»"
                )
        else:
            units.append(("Текст", stripped))
    return units, structural_errors


def analyze_Path(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".json":
        data = json.loads(raw)
        units, structural_errors = extract_units_from_content_json(data)
        full_text = "\n".join(t for _, t in units)
    else:
        units, structural_errors = extract_units_from_markdown(raw)
        full_text = raw

    long_dash_hits: list[str] = []
    for label, txt in units:
        if "—" in txt:
            long_dash_hits.append(f"{label}: використано довге тире «—» замість короткого «–»")

    all_sentences: list[tuple[str, str, int]] = []
    for label, txt in units:
        if label.startswith(("Розділ", "Заголовок")):
            continue
        for sent in split_sentences(txt):
            w_count = len(extract_words(sent))
            if w_count > 0:
                all_sentences.append((label, sent, w_count))

    all_words = extract_words(full_text)
    total_words = len(all_words)
    total_sentences = len(all_sentences)
    sent_lengths = [w for _, _, w in all_sentences] or [0]
    avg_len = round(sum(sent_lengths) / max(total_sentences, 1), 1)
    med_len = round(float(statistics.median(sent_lengths)), 1)

    over_20 = [(lbl, s, n) for lbl, s, n in all_sentences if n > 20]
    over_40 = [(lbl, s, n) for lbl, s, n in all_sentences if n > 40]
    over_60 = [(lbl, s, n) for lbl, s, n in all_sentences if n > 60]
    longest = max(all_sentences, key=lambda x: x[2]) if all_sentences else ("", "", 0)

    long_words = [w for w in all_words if len(w) > 6]
    complex_words = [w for w in all_words if count_syllables(w) > 3]
    pct_long = round(len(long_words) * 100.0 / max(total_words, 1), 1)
    pct_complex = round(len(complex_words) * 100.0 / max(total_words, 1), 1)
    lix = round(avg_len + pct_long, 1)

    lowered_text = full_text.lower()
    for phrase in STATUTORY_PARTICIPLE_WHITELIST:
        lowered_text = lowered_text.replace(phrase, " ")
    cleaned_words = extract_words(lowered_text)

    verbal_nouns = [w for w in cleaned_words if is_verbal_noun(w)]
    active_participles = [w for w in cleaned_words if is_active_participle(w)]
    adverbial_participles = [w for w in cleaned_words if is_adverbial_participle(w)]

    scale = 1000.0 / max(total_words, 1)
    vn_per_1k = round(len(verbal_nouns) * scale, 1)
    adv_per_1k = round(len(adverbial_participles) * scale, 1)

    split_predicates: list[str] = []
    genitive_chains: list[str] = []
    forbidden_hits: list[dict] = []

    for label, txt in units:
        low_u = txt.lower()
        for phrase in STATUTORY_PARTICIPLE_WHITELIST:
            low_u = low_u.replace(phrase, " ")
        u_words = extract_words(low_u)
        for i in range(len(u_words) - 1):
            w1, w2 = u_words[i], u_words[i + 1]
            if w1 in SPLIT_VERBS and is_verbal_noun(w2):
                split_predicates.append(f"{label}: «{w1} {w2}»")
            elif w1 in SPLIT_NOMINALS and (is_verbal_noun(w2) or is_genitive_candidate(w2)):
                split_predicates.append(f"{label}: «{w1} {w2}»")

        for ch in find_genitive_chains(txt):
            genitive_chains.append(f"{label}: «{ch}»")

        for pat, name, fix in FORBIDDEN_PATTERNS:
            m = re.search(pat, txt, flags=re.IGNORECASE)
            if m:
                forbidden_hits.append({
                    "location": label,
                    "issue": name,
                    "fragment": m.group(0),
                    "fix": fix,
                })

    blockers: list[str] = []
    blockers.extend(structural_errors)
    blockers.extend(long_dash_hits)
    for lbl, s, n in over_40:
        blockers.append(f"{lbl}: речення має {n} слів (ліміт — 40 слів): «{s[:100]}…»")
    if active_participles:
        blockers.append(f"Активні дієприкметники поза законними винятками ({len(active_participles)}): {', '.join(sorted(set(active_participles))[:10])}")
    for hit in forbidden_hits:
        blockers.append(f"{hit['location']}: {hit['issue']} («{hit['fragment']}») -> {hit['fix']}")

    return {
        "status": "clean" if not blockers else "needs_fixes",
        "metrics": {
            "total_words": total_words,
            "total_sentences": total_sentences,
            "avg_sentence_words": avg_len,
            "target_avg_sentence_words": 18.0,
            "median_sentence_words": med_len,
            "sentences_over_20_pct": round(len(over_20) * 100.0 / max(total_sentences, 1), 1),
            "sentences_over_40_count": len(over_40),
            "sentences_over_60_count": len(over_60),
            "longest_sentence": {
                "location": longest[0],
                "words": longest[2],
                "preview": longest[1][:140],
            },
            "long_words_pct": pct_long,
            "complex_words_pct": pct_complex,
            "lix_index": lix,
            "verbal_nouns_per_1000": vn_per_1k,
            "target_verbal_nouns_per_1000": 60.0,
            "active_participles_count": len(active_participles),
            "adverbial_participles_per_1000": adv_per_1k,
            "split_predicates_count": len(split_predicates),
            "genitive_chains_count": len(genitive_chains),
        },
        "blockers": blockers,
        "warnings": {
            "split_predicates": split_predicates[:15],
            "genitive_chains": genitive_chains[:15],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Аудит читабельності та процесуальний лінтер ClaimForge-UA")
    parser.add_argument("input", type=Path, help="Шлях до content.json або чернетки .md")
    parser.add_argument("--out", type=Path, help="Куди записати JSON-звіт")
    args = parser.parse_args()

    report = analyze_Path(args.input)
    out_text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(out_text + "\n", encoding="utf-8")
    print(out_text)
    return 0 if report["status"] == "clean" else 1


if __name__ == "__main__":
    sys.exit(main())
