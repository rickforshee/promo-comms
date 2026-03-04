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


def extract_all(subject: str, body: str) -> dict:
    """
    Run both patterns across subject and body combined.
    Returns dict with keys 'job_numbers' and 'po_numbers'.
    Subject is searched first so subject-line matches appear first.
    """
    combined = f"{subject or ''}\n{body or ''}"
    return {
        "job_numbers": extract_job_numbers(combined),
        "po_numbers":  extract_po_numbers(combined),
    }
