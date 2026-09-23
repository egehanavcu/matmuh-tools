import re

import scrapy

SITE = "https://mtm.yildiz.edu.tr"
KINDS = {"announcement": "duyurular", "news": "haberler"}


class AnnouncementSpider(scrapy.Spider):
    name = "announcements"

    def __init__(self, kind: str = "announcement", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.kind = kind
        self.path = KINDS[kind]

    async def start(self):
        yield scrapy.Request(f"{SITE}/{self.path}/1", self.parse_list, cb_kwargs={"page": 1})

    def parse_list(self, response, page):
        items = response.css("span.news-item")
        for item in items:
            href = item.css("a::attr(href)").get() or ""
            match = re.search(rf"/{self.path}/(\d+)/", href)
            if not match:
                continue
            title = " ".join(item.css("span.news-title::text").getall()).strip()
            yield response.follow(
                f"{SITE}/{self.path}/{match.group(1)}/x",
                self.parse_detail,
                cb_kwargs={"source_id": int(match.group(1)), "list_title": title, "url": response.urljoin(href)},
                dont_filter=True,
            )
        if items:
            yield scrapy.Request(f"{SITE}/{self.path}/{page + 1}", self.parse_list, cb_kwargs={"page": page + 1})

    def parse_detail(self, response, source_id, list_title, url):
        content = response.css("div.page-content")
        title = " ".join(content.css("span.news-content-title::text").getall()).strip()
        quote = " ".join(content.css("blockquote ::text").getall()).strip()
        body_nodes = content.xpath("./node()[not(self::span[@class='news-content-title']) and not(self::blockquote)]")
        body = "".join(body_nodes.getall()).strip()
        yield {
            "kind": self.kind,
            "sourceId": source_id,
            "url": url,
            "listTitle": list_title,
            "title": title or list_title,
            "quote": quote,
            "bodyHtml": body,
            "links": content.css("a::attr(href)").getall(),
            "images": content.css("img::attr(src)").getall(),
        }
