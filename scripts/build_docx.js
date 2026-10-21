#!/usr/bin/env node
/*
 * Збирає DOCX процесуального документа за макетом references/layout-spec.md.
 *
 * Використання:
 *   node build_docx.js content.json output.docx
 *
 * content.json описує лише зміст; стилі, нумерацію, колонтитул, нерозривні пробіли
 * і заборону переносів у заголовках скрипт ставить сам. Схема — layout-spec.md,
 * розділ «Складання DOCX».
 *
 * Після збирання скрипт:
 *   - викликає docx_no_hyphens.py з цієї ж теки (заборона переносів у заголовках і таблицях);
 *   - друкує попередження: абзаци, схожі на два речення, і розділи без підзаголовка.
 */
const fs = require("fs");
const path = require("path");
const { execSync, execFileSync } = require("child_process");

function loadDocx() {
  try {
    return require("docx");
  } catch (e) {
    const root = execSync("npm root -g").toString().trim();
    return require(path.join(root, "docx"));
  }
}
const D = loadDocx();
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell, WidthType, BorderStyle,
  AlignmentType, HeadingLevel, LevelFormat, Footer, PageNumber, TableOfContents, Bookmark,
  NumberedItemReference, NumberedItemReferenceFormat, ExternalHyperlink,
} = D;

const NB = " ";
const TNR = "Times New Roman";
const TEXT_W = 9071; // ширина текстового поля A4 при полях 30/20 мм, DXA
const LINK_COLOR = "1F3864";
const NONE = { style: BorderStyle.NONE, size: 0, color: "FFFFFF" };
const LINE = { style: BorderStyle.SINGLE, size: 4, color: "000000" };
const ANNEX_COLUMNS = ["№", "Документ", "Форма", "Сторінок", "Згадано в п.", "Файл"];
const ANNEX_WIDTHS = [450, 2500, 1900, 1300, 1250, 1671];

// ---------- нерозривні пробіли ----------
function nbsp(s) {
  return String(s)
    .replace(/а\. с\. (?=\d)/g, `а.${NB}с.${NB}`)
    .replace(/(^|[\s(«„;])(ст|ч|п|пп|абз|т|с)\. (?=\d)/g, `$1$2.${NB}`)
    .replace(/№ (?=\S)/g, `№${NB}`)
    .replace(/(\d) (?=\d{3}(?!\d))/g, `$1${NB}`)
    .replace(/(\d) (?=(грн|%|р\.|року|днів|дні|дня)(?![а-яіїєґ]))/g, `$1${NB}`)
    .replace(/(^|[\s(«])([А-ЯІЇЄҐ]\.) (?=[А-ЯІЇЄҐ][а-яіїєґ'’])/g, `$1$2${NB}`);
}

// ---------- текстові фрагменти ----------
// Фрагмент: рядок; {text, bold}; {url, text?}; {ref, cached}
function runs(spec, base = {}) {
  if (spec == null) return [];
  const arr = Array.isArray(spec) ? spec : [spec];
  const out = [];
  for (const r of arr) {
    if (typeof r === "string") {
      out.push(new TextRun({ text: nbsp(r), ...base }));
    } else if (r.url) {
      out.push(new ExternalHyperlink({
        link: r.url,
        children: [new TextRun({ text: r.text || r.url, color: LINK_COLOR, ...base })],
      }));
    } else if (r.ref) {
      out.push(new NumberedItemReference(r.ref, r.cached || "", {
        referenceFormat: NumberedItemReferenceFormat.NO_CONTEXT,
      }));
    } else {
      out.push(new TextRun({ text: nbsp(r.text || ""), bold: !!r.bold, ...base }));
    }
  }
  return out;
}

function plainText(spec) {
  if (spec == null) return "";
  const arr = Array.isArray(spec) ? spec : [spec];
  return arr.map((r) => (typeof r === "string" ? r : r.text || r.cached || "")).join("");
}

// ---------- перевірка «абзац — одне речення» ----------
const ABBR = /(^|[\s(«])(ст|ч|п|пп|абз|т|с|м|вул|просп|р|грн|ін|див|зокр|напр|тис|млн|млрд|пров|буд|кв|обл|р-н)\./g;
function looksLikeTwoSentences(text) {
  const t = text
    .replace(ABBR, "$1$2")
    .replace(/(^|[\s(«])[А-ЯІЇЄҐA-Z]\./g, "$1X") // ініціали
    .replace(/\d+\.\d+(\.\d+)*/g, "N"); // дати, пункти
  return /[.!?]»?\s+[«(„]?[А-ЯІЇЄҐA-Z]/.test(t);
}

// ---------- таблиці ----------
function cellPara(spec, bold, keepNext = false) {
  return new Paragraph({ style: "TableText", keepNext, children: runs(spec, bold ? { bold: true } : {}) });
}

// keepLast: останній рядок не відривається від наступного абзацу (дата і підпис після додатків)
function dataTable(columns, rows, widths, keepLast = false) {
  const n = columns.length;
  const w = widths && widths.length === n ? widths : Array(n).fill(Math.floor(TEXT_W / n));
  const borders = { top: LINE, bottom: LINE, left: LINE, right: LINE };
  const mkRow = (cells, head, keep = false) => new TableRow({
    tableHeader: head,
    cantSplit: true,
    children: cells.map((c, i) => new TableCell({
      width: { size: w[i], type: WidthType.DXA },
      borders,
      margins: { top: 40, bottom: 40, left: 80, right: 80 },
      children: [cellPara(c, head, keep)],
    })),
  });
  return new Table({
    width: { size: w.reduce((a, b) => a + b, 0), type: WidthType.DXA },
    columnWidths: w,
    rows: [mkRow(columns, true), ...rows.map((r, i) => mkRow(r, false, keepLast && i === rows.length - 1))],
  });
}

function headerTable(rows) {
  const w = [2400, TEXT_W - 2400];
  const nb = { top: NONE, bottom: NONE, left: NONE, right: NONE };
  return new Table({
    width: { size: TEXT_W, type: WidthType.DXA },
    columnWidths: w,
    borders: { ...nb, insideHorizontal: NONE, insideVertical: NONE },
    rows: rows.map((r) => new TableRow({
      children: [
        new TableCell({ width: { size: w[0], type: WidthType.DXA }, borders: nb, children: [cellPara(r.role, true)] }),
        new TableCell({
          width: { size: w[1], type: WidthType.DXA },
          borders: nb,
          children: (r.lines || []).map((l, i) => cellPara(l, i === 0)),
        }),
      ],
    })),
  });
}

// ---------- збирання ----------
function build(doc) {
  const flat = !!doc.flat; // документ без розділів: абзаци 1., 2., 3.
  const warnings = [];
  let usesFields = false;

  // Перший прохід: номери для попереджень і ширини висячого відступу.
  let s = 0, ss = 0, p = 0, maxDigits = 0;
  const sectionHasSub = [];
  for (const it of doc.body || []) {
    if (it.type === "section") { s++; ss = 0; p = 0; sectionHasSub[s] = false; }
    else if (it.type === "subsection") { ss++; p = 0; sectionHasSub[s] = true; }
    else if (it.type === "para") {
      p++;
      const num = flat ? `${p}` : `${s}.${ss}.${p}`;
      maxDigits = Math.max(maxDigits, num.replace(/\./g, "").length);
      it._num = num;
      if (looksLikeTwoSentences(plainText(it.runs))) warnings.push(`п. ${num}: схоже на два речення`);
    }
  }
  if (!flat) sectionHasSub.forEach((has, i) => { if (i > 0 && !has) warnings.push(`розділ ${i}: немає підзаголовка рівня ${i}.1`); });
  const HANG = maxDigits > 4 ? 992 : 851; // 17,5 мм або 15 мм

  const children = [];
  if (doc.header && doc.header.length) {
    children.push(headerTable(doc.header));
    children.push(new Paragraph({
      indent: { firstLine: 0 },
      border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: "000000", space: 1 } },
      children: [],
    }));
  }
  if (doc.title) children.push(new Paragraph({ style: "DocTitle", children: [new TextRun(String(doc.title).toUpperCase())] }));
  for (const line of [].concat(doc.subject || [])) {
    children.push(new Paragraph({ alignment: AlignmentType.CENTER, indent: { firstLine: 0 }, children: runs(line) }));
  }

  for (const it of doc.body || []) {
    switch (it.type) {
      case "section":
        children.push(new Paragraph({ heading: HeadingLevel.HEADING_1, numbering: { reference: "doc", level: 0 }, children: runs(it.text) }));
        break;
      case "subsection":
        children.push(new Paragraph({ heading: HeadingLevel.HEADING_2, numbering: { reference: "doc", level: 1 }, children: runs(it.text) }));
        break;
      case "heading":
        children.push(new Paragraph({ heading: HeadingLevel.HEADING_1, children: runs(it.text) }));
        break;
      case "para": {
        const content = runs(it.runs);
        if (JSON.stringify(it.runs || "").includes('"ref"')) usesFields = true;
        children.push(new Paragraph({
          numbering: { reference: flat ? "flat" : "doc", level: flat ? 0 : 2 },
          keepNext: !!it.keepNext,
          children: it.id ? [new Bookmark({ id: it.id, children: content })] : content,
        }));
        break;
      }
      case "plain":
        children.push(new Paragraph({ keepNext: !!it.keepNext, children: runs(it.runs) }));
        break;
      case "quote":
        children.push(new Paragraph({ style: "BlockQuote", children: runs(it.runs) }));
        break;
      case "table":
        if (it.caption) {
          children.push(new Paragraph({ style: "TableText", keepNext: true, spacing: { before: 120 }, children: runs(it.caption, { bold: true }) }));
        }
        children.push(dataTable(it.columns, it.rows, it.widths));
        children.push(new Paragraph({ spacing: { after: 0 }, children: [] }));
        break;
      case "toc":
        usesFields = true;
        children.push(new Paragraph({ indent: { firstLine: 0 }, keepNext: true, spacing: { before: 240, after: 120 }, children: [new TextRun({ text: "Зміст", bold: true })] }));
        children.push(new TableOfContents("Зміст", { hyperlink: true, headingStyleRange: "1-2" }));
        break;
      case "claims":
        children.push(new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun("Прохальна частина")] }));
        children.push(new Paragraph({ indent: { firstLine: 0 }, keepNext: true, children: runs(it.intro || "Прошу суд:") }));
        for (const c of it.items || []) {
          children.push(new Paragraph({ numbering: { reference: "claims", level: 0 }, children: runs(c) }));
        }
        break;
      case "annexes":
        children.push(new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun("Додатки")] }));
        children.push(dataTable(ANNEX_COLUMNS, it.rows || [], ANNEX_WIDTHS, true));
        break;
      default:
        throw new Error(`Невідомий тип елемента: ${it.type}`);
    }
  }

  if (doc.date) children.push(new Paragraph({ indent: { firstLine: 0 }, spacing: { before: 360 }, keepNext: true, children: [new TextRun({ text: doc.date, bold: true })] }));
  if (doc.signature) children.push(new Paragraph({ indent: { firstLine: 0 }, children: runs(doc.signature) }));

  const lvl = (level, text, extra = {}) => ({
    level, format: LevelFormat.DECIMAL, text, alignment: AlignmentType.LEFT,
    style: { paragraph: { indent: { left: HANG, hanging: HANG }, ...extra } },
  });

  const document = new Document({
    features: usesFields ? { updateFields: true } : undefined,
    hyphenation: { autoHyphenation: true, consecutiveHyphenLimit: 2 },
    styles: {
      default: {
        document: {
          run: { font: TNR, size: 28, language: { value: "uk-UA" } },
          paragraph: {
            alignment: AlignmentType.JUSTIFIED,
            spacing: { line: 288, lineRule: "auto", before: 0, after: 0 },
            indent: { firstLine: 567 },
            widowControl: true,
          },
        },
      },
      paragraphStyles: [
        { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
          run: { font: TNR, size: 28, bold: true, color: "000000" },
          paragraph: { alignment: AlignmentType.LEFT, spacing: { before: 360, after: 120, line: 240 }, indent: { firstLine: 0 }, keepNext: true, keepLines: true, outlineLevel: 0 } },
        { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
          run: { font: TNR, size: 28, bold: true, color: "000000" },
          paragraph: { alignment: AlignmentType.LEFT, spacing: { before: 240, after: 120, line: 240 }, indent: { firstLine: 0 }, keepNext: true, keepLines: true, outlineLevel: 1 } },
        { id: "DocTitle", name: "Назва документа", basedOn: "Normal", run: { bold: true, characterSpacing: 20 },
          paragraph: { alignment: AlignmentType.CENTER, spacing: { before: 240, after: 240, line: 240 }, indent: { firstLine: 0 }, keepNext: true } },
        { id: "BlockQuote", name: "Цитата відокремлена", basedOn: "Normal", run: { size: 24 },
          paragraph: { spacing: { before: 120, after: 120, line: 240 }, indent: { left: 567, right: 567, firstLine: 0 }, keepLines: true } },
        { id: "TableText", name: "Текст таблиці", basedOn: "Normal", run: { size: 24 },
          paragraph: { alignment: AlignmentType.LEFT, spacing: { before: 0, after: 0, line: 240 }, indent: { firstLine: 0 } } },
        { id: "TOC1", name: "toc 1", basedOn: "Normal", run: { size: 28, bold: false }, paragraph: { indent: { firstLine: 0 } } },
        { id: "TOC2", name: "toc 2", basedOn: "Normal", run: { size: 28, bold: false }, paragraph: { indent: { left: 567, firstLine: 0 } } },
      ],
    },
    numbering: {
      config: [
        { reference: "doc", levels: [lvl(0, "%1."), lvl(1, "%1.%2."), lvl(2, "%1.%2.%3.", { spacing: { after: 120 } })] },
        { reference: "flat", levels: [lvl(0, "%1.", { spacing: { after: 120 } })] },
        { reference: "claims", levels: [lvl(0, "%1.", { spacing: { after: 120 } })] },
      ],
    },
    sections: [{
      properties: {
        titlePage: true,
        page: {
          size: { width: 11906, height: 16838 },
          margin: { top: 1134, bottom: 1134, left: 1701, right: 1134, footer: 709 },
        },
      },
      footers: {
        default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.RIGHT, indent: { firstLine: 0 }, children: [new TextRun({ children: [PageNumber.CURRENT] })] })] }),
        first: new Footer({ children: [new Paragraph({ children: [] })] }),
      },
      children,
    }],
  });
  return { document, warnings };
}

async function main() {
  const [, , input, output] = process.argv;
  if (!input || !output) {
    console.error("Використання: node build_docx.js content.json output.docx");
    process.exit(1);
  }
  const content = JSON.parse(fs.readFileSync(input, "utf8"));
  const { document, warnings } = build(content);
  fs.writeFileSync(output, await Packer.toBuffer(document));
  try {
    execFileSync("python3", [path.join(__dirname, "docx_no_hyphens.py"), output], { stdio: "ignore" });
  } catch (e) {
    warnings.push("не вдалося заборонити переноси в заголовках: запусти docx_no_hyphens.py вручну");
  }
  console.log(`Готово: ${output}`);
  if (warnings.length) console.log("Попередження:\n- " + warnings.join("\n- "));
}

main().catch((e) => { console.error(e.message); process.exit(1); });
