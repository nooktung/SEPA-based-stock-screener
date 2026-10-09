"""Minimal HTML helpers on the standard library.

Collection needs three things from a page -- the text inside one element found
by class, the links in it, and a clean text rendering -- which do not justify a
parser dependency. Scripts and styles are dropped; block elements become line
breaks so paragraphs survive.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser

_BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "table", "ul", "ol", "section"}
_VOID = {"br", "img", "hr", "input", "meta", "link", "source", "wbr", "area", "col", "embed", "param", "track"}
_SKIP = {"script", "style", "noscript", "iframe", "svg", "figure"}


class _ElementExtractor(HTMLParser):
    """Collect text and links inside the first element matching a class or an id."""

    def __init__(self, css_class: str | None = None, element_id: str | None = None):
        super().__init__(convert_charrefs=True)
        self.css_class = css_class
        self.element_id = element_id
        self.depth = 0            # >0 while inside the target element
        self.skip = 0
        self.done = False
        self.parts: list[str] = []
        self.links: list[str] = []

    def handle_starttag(self, tag, attrs):
        if self.done:
            return
        a = dict(attrs)
        if self.depth == 0:
            classes = (a.get("class") or "").split()
            hit = (self.css_class is not None and self.css_class in classes) or (
                self.element_id is not None and a.get("id") == self.element_id)
            if hit and tag not in _VOID:
                self.depth = 1
            return
        if tag in _VOID:
            if tag == "br":
                self.parts.append("\n")
            return
        self.depth += 1
        if tag in _SKIP:
            self.skip += 1
        if tag in _BLOCK:
            self.parts.append("\n")
        if tag == "a" and a.get("href"):
            self.links.append(a["href"])

    def handle_endtag(self, tag):
        if self.done or self.depth == 0 or tag in _VOID:
            return
        if tag in _SKIP and self.skip:
            self.skip -= 1
        if tag in _BLOCK:
            self.parts.append("\n")
        self.depth -= 1
        if self.depth == 0:
            self.done = True

    def handle_data(self, data):
        if self.depth and not self.skip:
            self.parts.append(data)


def normalise(text: str) -> str:
    text = html.unescape(text).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()


def extract_element(page: str, css_class: str | None = None, element_id: str | None = None) -> tuple[str, list[str]]:
    """(text, links) of the first element with that class or id; ('', []) if absent."""
    parser = _ElementExtractor(css_class, element_id)
    parser.feed(page)
    return normalise("".join(parser.parts)), parser.links


def extract_by_class(page: str, css_class: str) -> tuple[str, list[str]]:
    return extract_element(page, css_class=css_class)


def strip_tags(fragment: str) -> str:
    return normalise(re.sub(r"<[^>]+>", " ", fragment))
