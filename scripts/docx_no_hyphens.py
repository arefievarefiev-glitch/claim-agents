"""Забороняє автоматичні переноси в заголовках, назві документа і таблицях DOCX.

Бібліотека docx (npm) не має параметра «Заборонити автоматичне перенесення слів» для стилю,
тому скрипт додає <w:suppressAutoHyphens/> у властивості абзацу потрібних стилів у word/styles.xml.
Змінює файл на місці. Якщо стиль визначено кілька разів (типовий і перевизначений), виправляє всі визначення.

Використання:
    python3 docx_no_hyphens.py document.docx [ДодатковийStyleId ...]
"""
import re
import shutil
import sys
import tempfile
import zipfile

DEFAULT_STYLES = ("Heading1", "Heading2", "Heading3", "Title", "DocTitle", "TableText")


def _add_suppress(match):
    head, body, tail = match.group(1), match.group(2), match.group(3)
    if "suppressAutoHyphens" in body:
        return match.group(0)
    if "<w:pPr>" in body:
        body = body.replace("<w:pPr>", "<w:pPr><w:suppressAutoHyphens/>", 1)
    elif "<w:pPr/>" in body:
        body = body.replace("<w:pPr/>", "<w:pPr><w:suppressAutoHyphens/></w:pPr>", 1)
    elif "<w:rPr>" in body:
        body = body.replace("<w:rPr>", "<w:pPr><w:suppressAutoHyphens/></w:pPr><w:rPr>", 1)
    else:
        body = body + "<w:pPr><w:suppressAutoHyphens/></w:pPr>"
    return head + body + tail


def patch(path, style_ids):
    tmp = tempfile.NamedTemporaryFile(suffix=".docx", delete=False).name
    patched = set()
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "word/styles.xml":
                xml = data.decode("utf-8")
                for sid in style_ids:
                    pattern = r'(<w:style [^>]*w:styleId="%s"[^>]*>)(.*?)(</w:style>)' % re.escape(sid)
                    xml, n = re.subn(pattern, _add_suppress, xml, flags=re.S)
                    if n:
                        patched.add(sid)
                data = xml.encode("utf-8")
            zout.writestr(item, data)
    shutil.move(tmp, path)
    return patched


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    ids = list(DEFAULT_STYLES) + sys.argv[2:]
    done = patch(sys.argv[1], ids)
    print("Переноси заборонено в стилях:", ", ".join(sorted(done)) or "жодного стилю не знайдено")
