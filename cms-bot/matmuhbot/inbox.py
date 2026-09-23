import hashlib
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .config import ROOT

INBOX = ROOT / "temp"

KINDS = {
    "academic-calendar": "YTÜ akademik takvimi (Excel)",
    "weekly-schedule": "Bölüm haftalık ders programı (PDF)",
}


@dataclass
class InboxFile:
    path: Path
    kind: str | None
    sha256: str
    text: str

    @property
    def name(self) -> str:
        return self.path.name


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cell_text(value) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == datetime.min.time() else value.isoformat(timespec="minutes")
    if isinstance(value, date):
        return value.isoformat()
    return " ".join(str(value).split())


def xlsx_text(path: Path) -> str:
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True, read_only=False)
    out = []
    for ws in wb.worksheets:
        if ws.sheet_state != "visible":
            continue
        out.append(f"## Sayfa: {ws.title}")
        for row in ws.iter_rows(values_only=True):
            cells = [cell_text(v) for v in row if v is not None and str(v).strip()]
            if cells:
                out.append(" | ".join(cells))
    return "\n".join(out)


def pdf_text(path: Path) -> str:
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def classify(text: str, suffix: str) -> str | None:
    upper = text.upper()
    if suffix in (".xlsx", ".xlsm") and "AKADEMİK TAKVİM" in upper:
        return "academic-calendar"
    if suffix == ".pdf" and "SAAT" in upper and "SINIF" in upper:
        return "weekly-schedule"
    return None


def scan() -> list[InboxFile]:
    files = []
    if not INBOX.exists():
        return files
    for path in sorted(INBOX.iterdir()):
        suffix = path.suffix.lower()
        if not path.is_file() or path.name.startswith(("~$", ".")):
            continue
        if suffix in (".xlsx", ".xlsm"):
            text = xlsx_text(path)
        elif suffix == ".pdf":
            text = pdf_text(path)
        else:
            continue
        files.append(InboxFile(path, classify(text, suffix), sha256_of(path), text))
    return files


def pick(kind: str) -> InboxFile:
    matches = [f for f in scan() if f.kind == kind]
    if not matches:
        raise FileNotFoundError(f"temp/ içinde {KINDS[kind]} bulunamadı")
    if len(matches) > 1:
        names = ", ".join(f.name for f in matches)
        raise RuntimeError(f"temp/ içinde birden fazla {KINDS[kind]} var: {names}. Birini kaldırın.")
    return matches[0]
