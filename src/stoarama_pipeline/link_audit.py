from __future__ import annotations

import re
import urllib.parse


PERMANENT_PATTERNS = (
    "video unavailable", "recording is not available", "this video has been removed",
    "private video", "http error 404", "http error 410", "status code 404", "status code 410",
)
AUTH_PATTERNS = ("sign in to confirm", "not a bot", "cookies", "login required")
TEMPORARY_PATTERNS = (
    "timed out", "timeout", "temporary failure", "connection reset", "connection refused",
    "remote end closed", "http error 429", "http error 500", "http error 502",
    "http error 503", "http error 504", "too many requests",
)


def source_review_url(row: dict) -> str:
    return str(row.get("youtube_url") or row.get("source_page_url") or
               row.get("stoarama_url") or row.get("source_url") or "").strip()


def validate_source_link(row: dict) -> tuple[str, str, str]:
    """Return status, normalized URL, and reason without consuming a media stream."""
    url = source_review_url(row)
    if not url:
        return "malformed", "", "source has no reviewable URL"
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return "malformed", url, "source URL must have an HTTP(S) scheme and hostname"
    if row.get("capture_type") == "youtube_watch" and not row.get("video_id"):
        return "malformed", url, "YouTube source has no valid video ID"
    # Reachability is established by the extractor/archive request, avoiding a
    # second request that can trigger rate limits or consume an unbounded stream.
    return "syntax_valid", urllib.parse.urlunsplit(parsed), ""


def classify_link_failure(reason: str) -> tuple[str, str]:
    lowered = re.sub(r"\s+", " ", str(reason or "")).lower()
    if any(pattern in lowered for pattern in PERMANENT_PATTERNS):
        if "private video" in lowered:
            return "restricted", "do_not_retry_without_access_change"
        return "permanently_unavailable", "do_not_retry_unless_source_changes"
    if any(pattern in lowered for pattern in AUTH_PATTERNS):
        return "auth_or_bot_block", "retry_on_mac_with_browser_cookies"
    if any(pattern in lowered for pattern in TEMPORARY_PATTERNS):
        return "temporary_failure", "retry_with_backoff"
    return "extraction_failure", "retry_once_then_manual_review"
