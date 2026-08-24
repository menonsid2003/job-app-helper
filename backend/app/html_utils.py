import html
import re

_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(raw: str) -> str:
    text = _TAG_RE.sub(" ", raw or "")
    text = html.unescape(text)  # e.g. "&#39;" -> "'", "&amp;" -> "&"
    return re.sub(r"\s+", " ", text).strip()
