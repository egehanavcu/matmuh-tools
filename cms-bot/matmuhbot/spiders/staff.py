import re

import scrapy

SITE = "https://mtm.yildiz.edu.tr"
GROUPS = {1: "ACADEMIC", 2: "TEACHING_AND_RESEARCH", 3: "ADMINISTRATIVE"}
MANAGEMENT_URL = f"{SITE}/sayfa/PERSONEL/Y%C3%B6netim/118"


def clean(parts) -> str:
    return " ".join(" ".join(parts).replace("\xa0", " ").split())


def lines_of(selector) -> list[str]:
    out = []
    for block in selector.xpath(".//h5 | .//p"):
        text = clean(block.xpath(".//text()").getall())
        if text:
            out.append(text)
    if not out:
        text = clean(selector.xpath(".//text()").getall())
        if text:
            out.append(text)
    return out


class StaffSpider(scrapy.Spider):
    name = "staff"

    async def start(self):
        for group in GROUPS:
            yield scrapy.Request(f"{SITE}/personel/1/{group}", self.parse_list, cb_kwargs={"group": group, "page": 1})
        yield scrapy.Request(MANAGEMENT_URL, self.parse_management)

    def parse_list(self, response, group, page):
        cards = response.css("div.one-staff")
        for position, card in enumerate(cards):
            link = card.css("strong a")
            href = link.attrib.get("href", "")
            match = re.search(r"/(\d+)/?$", href)
            if not match:
                continue
            source_id = int(match.group(1))
            info = card.xpath("./div/div[2]")
            listing = {
                "group": GROUPS[group],
                "page": page,
                "position": position,
                "name": clean(link.xpath(".//text()").getall()),
                "photo": card.css("img").attrib.get("src"),
                "lines": lines_of(info)[1:] if info else [],
            }
            yield response.follow(
                f"{SITE}/personel/1/x/{source_id}",
                self.parse_detail,
                cb_kwargs={"source_id": source_id, "listing": listing},
                dont_filter=True,
            )
        if cards:
            yield scrapy.Request(
                f"{SITE}/personel/{page + 1}/{group}",
                self.parse_list,
                cb_kwargs={"group": group, "page": page + 1},
            )

    def parse_detail(self, response, source_id, listing):
        content = response.css(".staff-content")
        rows = []
        for tr in content.css("span.content table tr"):
            cells = []
            for td in tr.css("td"):
                parts = [clean(p.xpath(".//text()").getall()) for p in td.css("p")]
                parts = [p for p in parts if p]
                cells.append(parts or [clean(td.xpath(".//text()").getall())])
            rows.append(cells)
        yield {
            "kind": "person",
            "sourceId": source_id,
            "url": response.url,
            "listing": listing,
            "detail": {
                "title": clean(content.css("span.title").xpath(".//text()").getall()),
                "summary": lines_of(content.css("span.summary")) if content.css("span.summary") else [],
                "table": rows,
                "links": content.css("span.content a::attr(href)").getall(),
                "photo": response.css("div.col-lg-1 img").attrib.get("src"),
            },
        }

    def parse_management(self, response):
        content = response.css("div.page-content")
        rows = []
        for tr in content.css("table tr"):
            cells = [lines_of(td) for td in tr.css("td")]
            rows.append({
                "cells": cells,
                "headings": [clean(s.xpath(".//text()").getall()) for s in tr.css("strong")],
                "images": tr.css("img::attr(src)").getall(),
            })
        yield {"kind": "management", "url": response.url, "rows": rows}
