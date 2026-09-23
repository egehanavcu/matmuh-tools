import re
from urllib.parse import parse_qs, urlparse

import scrapy

BASE = "https://bologna.yildiz.edu.tr"
PROGRAMS = {
    "undergraduate": f"{BASE}/index.php?r=program/view&id=222&aid=24",
    "masters": f"{BASE}/index.php?r=program/view&id=181&aid=86",
}


def text_of(node) -> str:
    html = node.get()
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    html = re.sub(r"</(p|div|li)>", "\n", html, flags=re.I)
    plain = scrapy.Selector(text=f"<div>{html}</div>").xpath("string(//div)").get() or ""
    lines = [" ".join(line.replace("\xa0", " ").split()) for line in plain.split("\n")]
    return "\n".join(line for line in lines if line).strip()


def cells_of(tr) -> list[str]:
    return [text_of(c) for c in tr.xpath("./th|./td")]


def with_lang(url: str, lang: str) -> str:
    url = re.sub(r"&lang=\w+", "", url)
    return f"{url}&lang={lang}"


class BolognaSpider(scrapy.Spider):
    name = "bologna"
    custom_settings = {"DOWNLOAD_DELAY": 1.0}

    async def start(self):
        for program, url in PROGRAMS.items():
            yield scrapy.Request(with_lang(url, "tr"), self.parse_program, cb_kwargs={"program": program, "url": url})

    def parse_program(self, response, program, url):
        section, rows = None, []
        for tr in response.css("#curriculum table tr"):
            if tr.attrib.get("class") == "table_semester_heading":
                section = text_of(tr)
                continue
            if not tr.xpath("./td"):
                continue
            href = tr.css("a::attr(href)").get()
            cells = cells_of(tr)
            if not cells or not cells[0] or cells[0].endswith(":") or "Toplam" in " ".join(cells):
                continue
            course_id = None
            if href:
                course_id = (parse_qs(urlparse(href).query).get("id") or [None])[0] or None
            rows.append({"section": section, "cells": cells, "href": response.urljoin(href) if href else None, "courseId": course_id})
        yield {"kind": "program", "program": program, "url": url, "rows": rows}

        seen = set()
        for row in rows:
            if not row["courseId"] or row["courseId"] in seen:
                continue
            seen.add(row["courseId"])
            for lang in ("tr", "en"):
                yield scrapy.Request(
                    with_lang(row["href"], lang),
                    self.parse_course,
                    cb_kwargs={"program": program, "course_id": row["courseId"], "lang": lang, "href": row["href"]},
                )

    def parse_course(self, response, program, course_id, lang, href):
        content = response.css("#content")
        header, fields, weeks, assessment = None, {}, [], []
        for table in content.css("table"):
            rows = [cells_of(tr) for tr in table.css("tr")]
            if not rows:
                continue
            first = rows[0][0] if rows[0] else ""
            if "detail-view" in (table.attrib.get("class") or ""):
                for tr in table.css("tr"):
                    label = text_of(tr.css("th")[0]) if tr.css("th") else ""
                    value = text_of(tr.css("td")[0]) if tr.css("td") else ""
                    if label:
                        fields[label] = value
            elif header is None and len(rows) >= 2 and len(rows[0]) >= 7:
                header = dict(zip(rows[0], rows[1]))
            elif first in ("Hafta", "Week"):
                weeks = [r for r in rows[1:] if r]
            elif first in ("Etkinlikler", "Activities") and len(rows[0]) == 3:
                assessment = [r for r in rows[1:] if r]
        yield {
            "kind": "course",
            "program": program,
            "courseId": course_id,
            "lang": lang,
            "url": href,
            "header": header,
            "fields": fields,
            "weeks": weeks,
            "assessment": assessment,
        }
