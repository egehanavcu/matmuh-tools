import os
from pathlib import Path

from .config import ROOT


def crawl(spider_cls, out_path: Path, **kwargs) -> None:
    from scrapy.crawler import CrawlerProcess
    from scrapy.utils.project import get_project_settings

    os.chdir(ROOT)
    os.environ.setdefault("SCRAPY_SETTINGS_MODULE", "matmuhbot.settings")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    settings = get_project_settings()
    settings.set("FEEDS", {str(out_path): {"format": "json", "overwrite": True, "encoding": "utf8", "indent": 1}})
    process = CrawlerProcess(settings)
    process.crawl(spider_cls, **kwargs)
    process.start()
