from . import s7_announcements as base


def scrape(dry_run: bool = False) -> None:
    base.scrape(kind="news")


def normalize(dry_run: bool = False, limit: int | None = None) -> None:
    base.normalize(kind="news", limit=limit)


def push(dry_run: bool = False) -> None:
    base.push(dry_run=dry_run, kind="news")


def run(dry_run: bool = False) -> None:
    base.run(dry_run=dry_run, kind="news")
