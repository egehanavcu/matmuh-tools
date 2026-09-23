import json
from datetime import datetime

from .config import STATE_FILE

STAGES = {
    0: ("0-prepare", "Hazırlık", []),
    1: ("1-academic-terms", "Akademik dönemler", [0]),
    2: ("2-staff", "Personel", [0]),
    3: ("3-lectures", "Dersler", [0]),
    4: ("4-elective-groups", "Seçmeli grupları", [3]),
    5: ("5-offerings-schedule", "Dönem kayıtları ve ders saatleri", [1, 2, 3]),
    6: ("6-grade-results", "Harf sonuçları ve sınav istatistikleri", [2, 3]),
    7: ("7-announcements", "Duyurular", [0]),
    8: ("8-news", "Haberler", [0]),
    9: ("9-translate", "Duyuru ve haber çevirisi", [7, 8]),
}


def load() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"steps": {}}


def save(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def step_status(state: dict, number: int) -> str | None:
    return state["steps"].get(STAGES[number][0], {}).get("status")


def missing_prerequisites(state: dict, number: int) -> list[int]:
    return [n for n in STAGES[number][2] if step_status(state, n) != "done"]


def finish(number: int, *, created=0, updated=0, skipped=0, failed=0, report: str | None = None, **extra) -> dict:
    state = load()
    state["steps"][STAGES[number][0]] = {
        "status": "done" if failed == 0 else "partial",
        "finishedAt": datetime.now().isoformat(timespec="seconds"),
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "failed": failed,
        "report": report,
        **extra,
    }
    save(state)
    return state
