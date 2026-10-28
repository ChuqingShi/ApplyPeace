"""Read every application question for a job before any browser opens.

Each ATS is normalised into the same Question shape, so answers can be drafted,
reviewed on the phone and locked at approval.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import asdict, dataclass, field

import httpx

from .sources.base import html_to_text


@dataclass
class Question:
    field: str
    label: str
    kind: str  # text | textarea | select | multiselect | boolean | file | url | location
    required: bool
    options: list[dict] = field(default_factory=list)  # [{"label", "value"}]
    section: str = "custom"  # standard | custom | eeo

    def to_dict(self) -> dict:
        return asdict(self)


# ------------------------------------------------------------------ Greenhouse

GH_KINDS = {
    "input_text": "text", "textarea": "textarea", "input_file": "file",
    "multi_value_single_select": "select", "multi_value_multi_select": "multiselect",
    "input_hidden": "hidden",
}
GH_STANDARD = {"first_name", "last_name", "preferred_name", "email", "phone", "resume", "resume_text",
               "cover_letter", "cover_letter_text"}


def greenhouse_questions(client: httpx.Client, board: str, job_id: str) -> list[Question]:
    data = client.get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}", params={"questions": "true"})
    data.raise_for_status()
    data = data.json()
    out: list[Question] = []
    for q in data.get("questions", []):
        fields = q.get("fields", [])
        # File questions offer an upload or a paste box; we always upload.
        primary = next((f for f in fields if f["type"] == "input_file"), fields[0] if fields else None)
        if not primary or primary["type"] == "input_hidden":
            continue
        kind = GH_KINDS.get(primary["type"], "text")
        options = [{"label": v["label"], "value": str(v["value"])} for v in primary.get("values", [])]
        if kind == "select" and {o["label"].lower() for o in options} == {"yes", "no"}:
            kind = "boolean"
        out.append(Question(primary["name"], html.unescape(q["label"]).strip(), kind, bool(q.get("required")),
                            options, "standard" if primary["name"] in GH_STANDARD else "custom"))
    for q in data.get("location_questions") or []:
        # The location question comes with hidden latitude/longitude helpers the page fills itself.
        for f in q.get("fields") or []:
            if f.get("type") == "input_hidden" or f.get("name") in ("latitude", "longitude"):
                continue
            if q.get("label", "").lower() in ("latitude", "longitude"):
                continue
            out.append(Question(f.get("name", "location"), q.get("label", "Location"), "location",
                                bool(q.get("required")), section="standard"))
    for block in data.get("compliance") or []:
        for q in block.get("questions", []):
            for f in q.get("fields", []):
                out.append(Question(f["name"], _eeo_label(q.get("label", f["name"])), "select", bool(q.get("required")),
                                    [{"label": v["label"], "value": str(v["value"])} for v in f.get("values", [])], "eeo"))
    # Greenhouse shows the Race question only after "Are you Hispanic or Latino?" is answered "No",
    # and the API omits that gate question, so add it just before Race.
    names = [q.field for q in out]
    if "race" in names and "hispanic_ethnicity" not in names:
        out.insert(names.index("race"), Question(
            "hispanic_ethnicity", "Are you Hispanic or Latino?", "select", False,
            [{"label": l, "value": l} for l in ("Yes", "No", "Decline To Self Identify")], "eeo"))
    demo = data.get("demographic_questions") or {}
    for q in demo.get("questions", []) if isinstance(demo, dict) else []:
        out.append(Question(f"demographic_{q['id']}", html_to_text(q.get("label", "")), "multiselect" if q.get("type") == "multi_value_multi_select" else "select",
                            bool(q.get("required")), [{"label": o["label"], "value": str(o["id"])} for o in q.get("answer_options", [])], "eeo"))
    return out


def _eeo_label(raw: str) -> str:
    return {"VeteranStatus": "Veteran status", "DisabilityStatus": "Disability status", "Gender": "Gender",
            "Race": "Race", "Hispanic/Latino": "Hispanic or Latino"}.get(raw, raw)


# ------------------------------------------------------------------ Ashby

ASHBY_FORM_QUERY = """query ApiJobPosting($organizationHostedJobsPageName: String!, $jobPostingId: String!) {
  jobPosting(organizationHostedJobsPageName: $organizationHostedJobsPageName, jobPostingId: $jobPostingId) {
    id
    applicationForm { sections { fieldEntries { ... on FormFieldEntry { id field isRequired } } } }
    surveyForms { sections { fieldEntries { ... on FormFieldEntry { id field isRequired } } } }
  }
}"""
ASHBY_KINDS = {"String": "text", "Email": "text", "Phone": "text", "LongText": "textarea", "File": "file",
               "Boolean": "boolean", "ValueSelect": "select", "MultiValueSelect": "multiselect",
               "Location": "location", "Url": "url", "Date": "text", "Number": "text", "Score": "select"}


def ashby_questions(client: httpx.Client, board: str, job_id: str) -> list[Question]:
    resp = client.post(
        "https://jobs.ashbyhq.com/api/non-user-graphql", params={"op": "ApiJobPosting"},
        json={"operationName": "ApiJobPosting", "query": ASHBY_FORM_QUERY,
              "variables": {"organizationHostedJobsPageName": board, "jobPostingId": job_id}},
    )
    resp.raise_for_status()
    posting = (resp.json().get("data") or {}).get("jobPosting")
    if not posting:
        raise ValueError("Ashby posting not found (closed?)")
    out = []
    forms = [posting.get("applicationForm")] + list(posting.get("surveyForms") or [])
    for form_index, form in enumerate(f for f in forms if f):
        for section in form.get("sections", []):
            for entry in section.get("fieldEntries", []):
                f = entry.get("field") or {}
                if not f.get("path"):
                    continue
                path = f["path"]
                options = [{"label": v.get("label", ""), "value": v.get("value", v.get("label", ""))}
                           for v in f.get("selectableValues") or []]
                title = (f.get("title") or "").strip()
                if not title or title == path:
                    title = "Consent to data processing (privacy notice)" if "consent" in path else path.replace("_systemfield_", "").replace("_", " ").capitalize()
                out.append(Question(path, title, ASHBY_KINDS.get(f.get("type"), "text"),
                                    bool(entry.get("isRequired")), options,
                                    "eeo" if form_index > 0 else ("standard" if path.startswith("_systemfield_") else "custom")))
    return out


# ------------------------------------------------------------------ Lever

LEVER_STANDARD = [
    ("resume", "Resume/CV", "file", True), ("name", "Full name", "text", True), ("email", "Email", "text", True),
    ("phone", "Phone", "text", False), ("location", "Current location", "location", False),
    ("org", "Current company", "text", False), ("urls[LinkedIn]", "LinkedIn URL", "url", False),
    ("urls[GitHub]", "GitHub URL", "url", False), ("urls[Portfolio]", "Portfolio URL", "url", False),
]
LEVER_KINDS = {"text": "text", "textarea": "textarea", "multiple-choice": "select", "multiple-select": "multiselect",
               "dropdown": "select", "file-upload": "file", "university": "select"}


def lever_questions(client: httpx.Client, apply_url: str) -> list[Question]:
    page = client.get(apply_url)
    page.raise_for_status()
    text = page.text
    present = set(re.findall(r'name="([^"]+)"', text))
    out = [Question(n, label, kind, req or f'name="{n}"' in text and _lever_required(text, n), section="standard")
           for n, label, kind, req in LEVER_STANDARD if n in present]
    for tag in re.findall(r'<input[^>]+name="cards\[[^\]]+\]\[baseTemplate\]"[^>]*>', text):
        card_id = re.search(r'name="cards\[([^\]]+)\]', tag).group(1)
        raw = re.search(r'value="([^"]*)"', tag)
        if not raw:
            continue
        template = json.loads(html.unescape(raw.group(1)))
        for i, f in enumerate(template.get("fields", [])):
            options = [{"label": o.get("text", ""), "value": o.get("text", "")} for o in f.get("options", [])]
            kind = LEVER_KINDS.get(f.get("type"), "text")
            if kind == "select" and {o["label"].lower() for o in options} == {"yes", "no"}:
                kind = "boolean"
            out.append(Question(f"cards[{card_id}][field{i}]", f.get("text", "").strip(), kind, bool(f.get("required")), options))
    for name, label in (("eeo[gender]", "Gender"), ("eeo[race]", "Race"), ("eeo[veteran]", "Veteran status"), ("eeo[disability]", "Disability status")):
        m = re.search(rf'<select[^>]+name="{re.escape(name)}"[^>]*>(.*?)</select>', text, re.S)
        if m:
            options = [{"label": html.unescape(l).strip(), "value": html.unescape(v)}
                       for v, l in re.findall(r'<option value="([^"]*)"[^>]*>([^<]*)</option>', m.group(1)) if v]
            out.append(Question(name, label, "select", False, options, "eeo"))
    return out


def _lever_required(text: str, name: str) -> bool:
    m = re.search(rf'<input[^>]+name="{re.escape(name)}"[^>]*>', text)
    return bool(m and "required" in m.group(0))


def questions_for(client: httpx.Client, job) -> list[Question]:
    if job["ats"] == "greenhouse":
        return greenhouse_questions(client, job["board"], job["ats_job_id"])
    if job["ats"] == "ashby":
        return ashby_questions(client, job["board"], job["ats_job_id"])
    if job["ats"] == "lever":
        return lever_questions(client, job["apply_url"] or job["url"] + "/apply")
    if job["ats"] == "workday":
        return []  # Workday forms sit behind a per-company account: you apply on the site yourself
    raise ValueError(f"unsupported ATS {job['ats']}")
