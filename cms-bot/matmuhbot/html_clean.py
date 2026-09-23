import re
from html import escape, unescape
from html.parser import HTMLParser

ALLOWED = {"p", "br", "hr", "strong", "b", "em", "i", "u", "s", "del", "strike", "a",
           "ul", "ol", "li", "h2", "h3", "h4", "blockquote", "pre", "code"}
BLOCK_LIKE = {"div", "section", "article", "td", "th", "tr", "table", "tbody", "center", "h1", "h5", "h6"}
DROP_CONTENT = {"script", "style", "img", "iframe", "figure", "figcaption"}
VOID = {"br", "hr"}
HEADING_MAP = {"h1": "h2", "h5": "h4", "h6": "h4"}


def safe_href(href: str | None) -> str | None:
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith(("http://", "https://", "mailto:", "/")):
        return href
    return None


class Cleaner(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.stack: list[str] = []
        self.skip_depth = 0
        self.links: list[str] = []

    def handle_starttag(self, tag, attrs):
        if self.skip_depth:
            if tag not in VOID and tag in DROP_CONTENT:
                self.skip_depth += 1
            return
        if tag in DROP_CONTENT:
            if tag not in ("img", "br", "hr"):
                self.skip_depth = 1
            return
        tag = HEADING_MAP.get(tag, tag)
        if tag == "a":
            href = safe_href(dict(attrs).get("href"))
            if not href:
                self.stack.append("")
                return
            self.links.append(href)
            self.out.append(f'<a href="{escape(href, quote=True)}">')
            self.stack.append("a")
            return
        if tag in VOID:
            self.out.append(f"<{tag}>")
            return
        if tag in ALLOWED:
            self.out.append(f"<{tag}>")
            self.stack.append(tag)
            return
        if tag in BLOCK_LIKE:
            self.out.append(" ")
            self.stack.append("")
            return
        self.stack.append("")

    def handle_endtag(self, tag):
        if self.skip_depth:
            if tag in DROP_CONTENT:
                self.skip_depth -= 1
            return
        if tag in VOID:
            return
        if not self.stack:
            return
        opened = self.stack.pop()
        if opened:
            self.out.append(f"</{opened}>")

    def handle_data(self, data):
        if self.skip_depth:
            return
        text = data.replace("\xa0", " ")
        if not text.strip():
            if self.out and not self.out[-1].endswith(" "):
                self.out.append(" ")
            return
        self.out.append(escape(re.sub(r"\s+", " ", text), quote=False))


def tidy(html: str) -> str:
    html = re.sub(r"</?(span|font)[^>]*>", "", html, flags=re.I)
    html = re.sub(r"<p>\s*(&nbsp;|\s)*</p>", "", html, flags=re.I)
    html = re.sub(r"\s+", " ", html)
    html = re.sub(r"<p>\s*</p>", "", html)
    html = re.sub(r"(<br>\s*){3,}", "<br><br>", html)
    html = re.sub(r"<p>\s*<br>\s*", "<p>", html)
    html = re.sub(r"\s*<br>\s*</p>", "</p>", html)
    html = re.sub(r"\s+</(p|li|h2|h3|h4|blockquote)>", r"</\1>", html)
    html = re.sub(r"<(p|li|h2|h3|h4|blockquote)>\s+", r"<\1>", html)
    return html.strip()


def clean_html(html: str) -> tuple[str, list[str]]:
    cleaner = Cleaner()
    cleaner.feed(html or "")
    cleaner.close()
    while cleaner.stack:
        opened = cleaner.stack.pop()
        if opened:
            cleaner.out.append(f"</{opened}>")
    return tidy("".join(cleaner.out)), cleaner.links


def text_of(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html or "")
    return re.sub(r"\s+", " ", unescape(text)).strip()
