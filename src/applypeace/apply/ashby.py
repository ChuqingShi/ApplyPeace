"""Ashby hosted application form (jobs.ashbyhq.com/<org>/<id>/application)."""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import Page

from .base import FillReport, NeedsYou, field_guard, wait_for_outcome

ERRORS = "[class*='_error']:visible, [role=alert]:visible"


def form_url(job) -> str:
    return job["apply_url"] or f"https://jobs.ashbyhq.com/{job['board']}/{job['ats_job_id']}/application"


def _entry(page: Page, field: str):
    entry = page.locator(f"label[for='{field}']").locator("xpath=ancestor::*[contains(@class,'field-entry')][1]")
    if not entry.count():
        entry = page.locator(".ashby-application-form-field-entry").filter(
            has=page.locator(f"[name='{field}'], [id='{field}'], [id*='{field}']"))
    return entry.first


def _label_for(row: dict, value) -> str:
    return next((o["label"] for o in row["options"] if str(o["value"]) == str(value)), str(value))


def fill(page: Page, rows: list[dict], resume: Path, upload: bool = True) -> FillReport:
    report = FillReport()
    page.set_default_timeout(12000)
    page.wait_for_selector("#_systemfield_name, [name='_systemfield_name']", timeout=30000)
    page.wait_for_timeout(1000)
    for row in rows:
        value, kind, field = row["value"], row["kind"], row["field"]
        with field_guard(row, report):
            if value in (None, "", []):
                if row["required"]:
                    raise NeedsYou("missing_answer", row["label"])
                report.skipped.append(field)
                continue
            entry = _entry(page, field)
            if kind == "file":
                if upload:
                    page.set_input_files(f"[id='{field}']", str(resume))
                    page.wait_for_timeout(2500)
                report.filled[field] = resume.name
            elif kind == "boolean":
                entry.get_by_role("button", name="Yes" if value in (True, "true", "Yes") else "No", exact=True).click()
                report.filled[field] = bool(value in (True, "true", "Yes"))
            elif kind in ("select", "multiselect") and not entry.count():
                # Consent tick boxes live outside the field containers.
                labels = [_label_for(row, v) for v in (value if isinstance(value, list) else [value])]
                boxes = page.locator(f"input[type=checkbox][id*='{field}']")
                for label in labels:
                    named = boxes.and_(page.locator(f"[name='{label}']"))
                    (named if named.count() else boxes).first.check()
                report.filled[field] = labels
            elif kind in ("select", "multiselect"):
                labels = [_label_for(row, v) for v in (value if isinstance(value, list) else [value])]
                combo = entry.get_by_role("combobox")
                for label in labels:
                    if combo.count():
                        combo.first.fill(label)
                        page.wait_for_timeout(600)
                        page.get_by_role("option", name=label).first.click()
                    else:
                        entry.get_by_text(label, exact=True).first.click()
                report.filled[field] = labels
            elif kind == "location":
                combo = entry.get_by_role("combobox").first
                combo.fill(str(value))
                page.wait_for_timeout(1500)
                option = page.get_by_role("option").first
                if not option.count():
                    raise NeedsYou("unknown_page", f"No location suggestion for '{value}'")
                option.click()
                report.filled[field] = str(value)
            else:
                target = page.locator(f"[id='{field}'], [name='{field}']").first
                if not target.count():
                    raise NeedsYou("unknown_page", f"Field '{row['label']}' not found on the form")
                target.fill(str(value))
                report.filled[field] = str(value)
    return report


def submit(page: Page) -> str:
    page.get_by_role("button", name="Submit Application").click()
    return wait_for_outcome(page, ERRORS)


def enter_code(page: Page, code: str) -> str:
    page.locator("input[autocomplete='one-time-code'], input[type=text]:visible").first.fill(code)
    page.get_by_role("button", name="Submit").first.click()
    return wait_for_outcome(page, ERRORS)
