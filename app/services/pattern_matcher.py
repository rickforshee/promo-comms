import re

# ─── Patterns ─────────────────────────────────────────────────────────────────
# Job numbers: exactly 6 digits
# PO numbers:  exactly 5 digits
# Both patterns use word boundaries to avoid matching numbers that are part
# of longer strings (e.g. a phone number or invoice number)

_JOB_PATTERN = re.compile(r'\b(\d{6})\b')
_PO_PATTERN  = re.compile(r'\b(\d{5})\b')

# Common false-positive fragments to ignore (zip codes, etc.)
# Extend this list as patterns emerge in real data
_JOB_EXCLUSIONS = set()
_PO_EXCLUSIONS  = set()

from html.parser import HTMLParser

class _HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts = []
    def handle_data(self, data):
        self._parts.append(data)
    def get_text(self):
        return ' '.join(self._parts)

def strip_html(html: str) -> str:
    if not html:
        return ''
    s = _HTMLStripper()
    s.feed(html)
    return s.get_text()


def extract_job_numbers(text: str) -> list[str]:
    """Return deduplicated list of 6-digit job numbers found in text."""
    if not text:
        return []
    matches = _JOB_PATTERN.findall(text)
    return list(dict.fromkeys(
        m for m in matches if m not in _JOB_EXCLUSIONS
    ))


def extract_po_numbers(text: str) -> list[str]:
    """Return deduplicated list of 5-digit PO numbers found in text."""
    if not text:
        return []
    matches = _PO_PATTERN.findall(text)
    return list(dict.fromkeys(
        m for m in matches if m not in _PO_EXCLUSIONS
    ))


def extract_all(subject: str, body: str = None, body_html: str = None) -> dict:
    text_body = body or strip_html(body_html)
    combined = f"{subject or ''}\n{text_body or ''}"
    return {
        "job_numbers": extract_job_numbers(combined),
        "po_numbers":  extract_po_numbers(combined),
    }
