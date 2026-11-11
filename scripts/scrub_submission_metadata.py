"""INTERNAL (not published): remove locale and software traces from the submission files after the Word export.

    python scripts/scrub_submission_metadata.py            → rewrites docs/v3/submission/*.pdf and *.docx in place

PDFs exported by Word for Mac carry the exporting OS and its UI language in /Producer ("macOS 버전 … Quartz PDFContext");
the KIEE template's docProps/app.xml carries the Korean UI label "제목" in HeadingPairs. Neither names a person, but a
double-blind reviewer should not learn the authors' locale from file metadata. Page content is untouched (pypdf clones
the pages; the docx is re-zipped with one XML member replaced).
"""
from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path

from pypdf import PdfReader, PdfWriter

SUB = Path(__file__).resolve().parents[1] / "docs" / "v3" / "submission"


def scrub_pdf(p: Path) -> None:
    reader = PdfReader(p)
    n_pages, text = len(reader.pages), [pg.extract_text() for pg in reader.pages]
    w = PdfWriter(clone_from=reader)
    w.add_metadata({"/Producer": "", "/Creator": ""})
    tmp = p.with_suffix(".tmp.pdf")
    with tmp.open("wb") as f:
        w.write(f)
    r2 = PdfReader(tmp)
    assert len(r2.pages) == n_pages and [pg.extract_text() for pg in r2.pages] == text, p
    tmp.replace(p)
    print(f"{p.name}: Producer/Creator cleared, {n_pages} pages, text identical")


def scrub_docx(p: Path) -> None:
    with zipfile.ZipFile(p) as z:
        names = z.namelist()
        if "docProps/app.xml" not in names:
            return
        app = z.read("docProps/app.xml").decode("utf-8")
        new = app.replace("<vt:lpstr>제목</vt:lpstr>", "<vt:lpstr>Title</vt:lpstr>")
        if new == app:
            print(f"{p.name}: app.xml already clean")
            return
        tmp = Path(tempfile.mkstemp(suffix=".docx", dir=p.parent)[1])
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
            for item in z.infolist():
                data = new.encode("utf-8") if item.filename == "docProps/app.xml" else z.read(item.filename)
                out.writestr(item, data)
    shutil.move(tmp, p)
    print(f"{p.name}: app.xml heading label localised → 'Title'")


if __name__ == "__main__":
    for p in sorted(SUB.glob("*.pdf")):
        scrub_pdf(p)
    for p in sorted(SUB.glob("*.docx")):
        scrub_docx(p)
