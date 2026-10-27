"""Lever hosted application form (jobs.lever.co/<org>/<id>/apply): a plain HTML form."""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import Page

from .base import FillReport, NeedsYou, field_guard, wait_for_outcome

ERRORS = ".error-message:visible, .application-error:visible"


def form_url(job) -> str:
    return job["apply_url"] or f"{job['url']}/apply"


def fill(page: Page, rows: list[dict], resume: Path, upload: bool = True) -> FillReport:
    report = FillReport()
    page.set_default_timeout(12000)
    page.wait_for_selector("input[name='name']", timeout=30000)
    for row in rows:
        value, kind, field = row["value"], row["kind"], row["field"]
        with field_guard(row, report):
            if value in (None, "", []):
                if row["required"]:
                    raise NeedsYou("missing_answer", row["label"])
                report.skipped.append(field)
                continue
            named = page.locator(f"[name='{field}']")
            if kind == "file":
                if upload:
                    page.set_input_files(f"input[name='{field}']", str(resume))
                    page.wait_for_timeout(2500)
                report.filled[field] = resume.name
            elif kind in ("select", "boolean", "multiselect"):
                values = value if isinstance(value, list) else [value]
                values = [("Yes" if v else "No") if isinstance(v, bool) else str(v) for v in values]
                tag = named.first.evaluate("e => e.tagName + ':' + (e.type || '')")
                if tag.startswith("SELECT"):
                    # Stored values are the <option> values (equal to the text for custom cards).
                    named.first.select_option(value=values if kind == "multiselect" else values[0])
                else:  # radio buttons / checkboxes whose value is the option text
                    for v in values:
                        box = page.locator(f"input[name='{field}'][value='{v}']")
                        if not box.count():
                            raise NeedsYou("option_missing", f"'{v}' is no longer an option for {row['label']}")
                        box.check()
                report.filled[field] = values
            elif kind == "location":
                named.first.fill(str(value))
                page.wait_for_timeout(1500)
                suggestion = page.locator(".dropdown-location:visible, .location-dropdown li:visible").first
                if suggestion.count():
                    suggestion.click()
                report.filled[field] = str(value)
            else:
                if not named.count():
                    raise NeedsYou("unknown_page", f"Field '{row['label']}' not found on the form")
                named.first.fill(str(value))
                report.filled[field] = str(value)
    return report


def submit(page: Page) -> str:
    page.locator("#btn-submit, button[type=submit]").first.click()
    return wait_for_outcome(page, ERRORS)


def enter_code(page: Page, code: str) -> str:
    raise NeedsYou("code_unsupported", "Lever asked for a code; finish this one yourself.")
