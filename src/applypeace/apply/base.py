"""Shared pieces for the form fillers: outcomes, hand-off signals and page checks."""

from __future__ import annotations

import re
from contextlib import contextmanager
from dataclasses import dataclass, field

from playwright.sync_api import Page


class NeedsYou(Exception):
    """Stop and hand this application to the user (CAPTCHA, unknown page, unanswerable field)."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


class NeedsCode(Exception):
    """The site emailed a verification code; the user types it on the phone page."""


@dataclass
class FillReport:
    filled: dict = field(default_factory=dict)   # field -> value actually entered
    skipped: list = field(default_factory=list)  # optional fields left blank


CONFIRMED = re.compile(
    r"thank(s| you) for (applying|your application|your interest)|application (has been |was )?(submitted|received)"
    r"|we('ve| have) received your application|successfully submitted|you('ve| have) applied",
    re.I,
)
CODE_PROMPT = re.compile(r"(security|verification) code|enter the code (we|that was) sent|check your email for a code", re.I)
CAPTCHA_FRAMES = ("recaptcha/api2/bframe", "recaptcha/enterprise/bframe", "hcaptcha.com", "challenges.cloudflare.com")


def visible_captcha(page: Page) -> bool:
    """True when a CAPTCHA challenge (not just the invisible badge) is showing."""
    for frame in page.frames:
        if any(marker in frame.url for marker in CAPTCHA_FRAMES):
            try:
                element = frame.frame_element()
                box = element.bounding_box()
                if box and box["width"] > 100 and box["height"] > 100 and element.is_visible():
                    return True
            except Exception:  # detached frames during navigation
                continue
    return False


def page_text(page: Page) -> str:
    try:
        return page.inner_text("body", timeout=5000)
    except Exception:
        return ""


def wait_for_outcome(page: Page, error_selector: str, timeout_s: int = 45) -> str:
    """After clicking Submit, return "submitted", "code" or raise NeedsYou."""
    for _ in range(timeout_s * 2):
        page.wait_for_timeout(500)
        text = page_text(page)
        if CONFIRMED.search(text) or re.search(r"/(confirmation|thanks|thank-you)\b", page.url):
            return "submitted"
        if CODE_PROMPT.search(text):
            return "code"
        if visible_captcha(page):
            raise NeedsYou("captcha", "The site showed a CAPTCHA.")
        errors = [e.strip() for e in page.locator(error_selector).all_inner_texts() if e.strip()]
        if errors:
            raise NeedsYou("form_error", "; ".join(errors[:3]))
    raise NeedsYou("no_confirmation", "Submitted, but no confirmation appeared. Check your email.")


@contextmanager
def field_guard(row: dict, report: FillReport | None = None):
    """Handle a failure while filling one field.

    Required field: stop and hand the application to you, naming the field.
    Optional field: leave it blank, note it in the record, and carry on.
    """
    try:
        yield
    except NeedsCode:
        raise
    except NeedsYou as exc:
        if row["required"] or report is None or exc.reason == "missing_answer":
            raise
        report.skipped.append(f"{row['field']} (left blank: {exc.reason})")
    except Exception as exc:
        if row["required"] or report is None:
            raise NeedsYou("unknown_page", f"Could not fill '{row['label'][:80]}' ({type(exc).__name__})") from exc
        report.skipped.append(f"{row['field']} (left blank: could not fill)")
