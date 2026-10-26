"""Greenhouse hosted application form (job-boards.greenhouse.io)."""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import Page

from .base import FillReport, NeedsYou, field_guard, wait_for_outcome

ERRORS = ".helper-text--error, [id$='-error']:not(:empty)"


def form_url(job) -> str:
    return f"https://job-boards.greenhouse.io/{job['board']}/jobs/{job['ats_job_id']}"


def _dom_id(field: str) -> str:
    return field.removesuffix("[]")


def _open_menu(page: Page, dom_id: str):
    combo = page.locator(f"[id='{dom_id}']")
    control = combo.locator("xpath=ancestor::div[contains(@class,'select__control')][1]")
    for _ in range(4):
        control.locator("button").first.click()
        try:
            page.wait_for_function(
                f"document.getElementById({dom_id!r}).getAttribute('aria-expanded') === 'true'", timeout=1500
            )
            return control
        except Exception:
            page.wait_for_timeout(500)
    raise NeedsYou("unknown_page", f"Could not open the list for {dom_id}")


def _pick(page: Page, dom_id: str, label: str) -> None:
    control = _open_menu(page, dom_id)
    options = page.locator("[role=option]:visible")
    texts = [t.strip() for t in options.all_inner_texts()]
    index = texts.index(label) if label in texts else next(
        (i for i, t in enumerate(texts) if label.lower() in t.lower()), None)
    if index is None:
        page.keyboard.press("Escape")
        raise NeedsYou("option_missing", f"'{label}' is no longer an option")
    options.nth(index).click()
    page.wait_for_timeout(150)
    if label.lower()[:20] not in control.inner_text().lower() and "multi" not in (control.get_attribute("class") or ""):
        raise NeedsYou("unknown_page", f"Choice for {dom_id} did not stick")


def _label_for(row: dict, value) -> str:
    return next((o["label"] for o in row["options"] if str(o["value"]) == str(value)), str(value))


def fill(page: Page, rows: list[dict], resume: Path, country_label: str = "United States", upload: bool = True) -> FillReport:
    report = FillReport()
    page.set_default_timeout(12000)
    page.wait_for_selector("#first_name", timeout=30000)
    page.wait_for_timeout(1500)  # let the React form hydrate
    if page.locator("#country").count():
        try:
            _pick(page, "country", country_label)
        except NeedsYou:
            pass  # optional phone country helper
    for row in rows:
        value, kind, dom_id = row["value"], row["kind"], _dom_id(row["field"])
        with field_guard(row):
            if value in (None, "", []):
                if row["required"]:
                    raise NeedsYou("missing_answer", row["label"])
                report.skipped.append(row["field"])
                continue
            target = page.locator(f"[id='{dom_id}']")
            if kind == "file":
                if upload:
                    page.set_input_files(f"[id='{dom_id}']", str(resume))
                    page.wait_for_timeout(1500)
                report.filled[row["field"]] = resume.name
            elif kind in ("select", "boolean"):
                _pick(page, dom_id, _label_for(row, value))
                report.filled[row["field"]] = _label_for(row, value)
            elif kind == "multiselect":
                labels = [_label_for(row, v) for v in (value if isinstance(value, list) else [value])]
                for label in labels:
                    _pick(page, dom_id, label)
                report.filled[row["field"]] = labels
            elif kind == "location":
                target.fill(str(value))
                page.wait_for_timeout(1200)
                option = page.locator("[role=option]:visible").first
                if option.count():
                    option.click()
                report.filled[row["field"]] = str(value)
            else:
                if not target.count():
                    raise NeedsYou("unknown_page", f"Field '{row['label']}' not found on the form")
                target.fill(str(value))
                report.filled[row["field"]] = str(value)
    return report


def submit(page: Page) -> str:
    page.locator("button[type=submit]", has_text="Submit").first.click()
    return wait_for_outcome(page, ERRORS)


def enter_code(page: Page, code: str) -> str:
    boxes = page.locator("input[id^='security-input'], input[name*='security'], input[autocomplete='one-time-code']")
    if boxes.count() > 1:
        for box, char in zip(boxes.all(), code):
            box.fill(char)
    else:
        boxes.first.fill(code)
    page.locator("button[type=submit]").first.click()
    return wait_for_outcome(page, ERRORS)
