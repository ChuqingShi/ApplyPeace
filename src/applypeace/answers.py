"""Draft an answer for every application question, before any browser opens.

Sources, in order: answers you saved during earlier reviews, your profile and
standard answers, then (with consent) an LLM draft for essay questions.
Legal, consent and factual questions we can't know (e.g. "Have you interviewed
here before?") are left empty for you to answer once in review; ApplyPeace
never guesses them.
"""

from __future__ import annotations

import json
import re
import sqlite3

from . import llm, profile
from .companies.names import normalize
from .config import Paths
from .forms import Question
from .sources.base import US_STATES

DECLINE = re.compile(r"decline|don.?t wish|do not wish|not want|prefer not|choose not", re.I)


def norm_label(label: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", label.lower()).split("|")[0].strip()


def _similar(a: str, b: str) -> bool:
    ta, tb = set(norm_label(a).split()), set(norm_label(b).split())
    return bool(ta and tb) and len(ta & tb) / len(ta | tb) >= 0.8


def choose(question: Question, wanted) -> str | list | None:
    """Pick the option value matching a wanted answer (bool, text, or 'decline')."""
    options = question.options
    if question.kind == "boolean" and not options:
        return bool(wanted) if isinstance(wanted, bool) else None
    if wanted is None or not options:
        return wanted if not options else None
    labels = [(o["label"], o["value"]) for o in options]
    if isinstance(wanted, bool):
        prefix = "yes" if wanted else "no"
        hit = next((v for l, v in labels if l.strip().lower().startswith(prefix)), None)
    elif wanted == "decline":
        hit = next((v for l, v in labels if DECLINE.search(l)), None)
    else:
        w = str(wanted).strip().lower()
        hit = next((v for l, v in labels if l.strip().lower() == w), None) \
            or next((v for l, v in labels if w and (w in l.lower() or l.lower() in w)), None)
    if hit is None:
        return None
    return [hit] if question.kind == "multiselect" else hit


def _profile_value(q: Question, prof: dict, ans: dict, company: str):
    """(value, source) from profile/standard answers, or (None, None) if not ours to answer."""
    label, field = q.label.lower(), q.field.lower()
    exp = (prof.get("experience") or [{}])[0] if prof.get("experience") else {}
    edu = (prof.get("education") or [{}])[0] if prof.get("education") else {}
    links, loc = prof.get("links") or {}, prof.get("location") or {}
    full = f"{prof.get('first_name', '')} {prof.get('last_name', '')}".strip()
    city = ", ".join(x for x in (loc.get("city"), loc.get("state")) if x)

    if SIGNATURE.search(label):
        return None, None  # an electronic signature is yours to give, in review
    short = len(label) <= 80
    if q.kind == "file":
        if "resume" in label or "cv" in label or "resume" in field:
            return {"resume": True}, "profile"
        return None, None  # cover letters and other files: only if required, answered in review
    rules = [
        ("preferred" in label and "name" in label, prof.get("preferred_name") or prof.get("first_name")),
        (field == "first_name" or (short and q.kind == "text" and "first name" in label), prof.get("first_name")),
        (field == "last_name" or (short and q.kind == "text" and ("last name" in label or "surname" in label)), prof.get("last_name")),
        (field in ("name", "_systemfield_name") or label in ("full name", "name", "legal name"), full),
        ("email" in field or label.startswith("email"), prof.get("email")),
        ("phone" in field or "phone" in label, prof.get("phone")),
        (q.kind == "location" or label in ("location", "current location", "city") or "where are you located" in label, city),
        ("linkedin" in label or "linkedin" in field, links.get("linkedin")),
        ("github" in label or "github" in field, links.get("github")),
        (("portfolio" in label or "website" in label) and q.kind != "select", links.get("portfolio")),
        (label in ("current company", "current employer", "most recent employer") or field == "org", exp.get("company")),
        (label in ("current title", "current job title", "most recent title"), exp.get("title")),
        (("school" in label or "university" in label or "college" in label) and "graduat" not in label, edu.get("school")),
        ("degree" in label and "year" not in label, edu.get("degree")),
        ("discipline" in label or "field of study" in label or "major" in label, edu.get("field")),
        ("state" in label and ("reside" in label or "live" in label) and "united states" not in label,
         STATE_NAMES.get((loc.get("state") or "").upper(), loc.get("state")) if q.options else loc.get("state")),
    ]
    for matched, value in rules:
        if matched:
            return (choose(q, value) if q.options else value) or None, "profile"

    if re.search(r"authori[sz]ed to work|legally authori[sz]ed|eligible to work|authori[sz]ation to work", label):
        return choose(q, ans.get("work_authorized_us")), "answers"
    if "sponsor" in label or ("visa" in label and ("require" in label or "need" in label)):
        now, future = ans.get("needs_sponsorship_now"), ans.get("needs_sponsorship_future")
        if now is None or future is None:
            return None, None
        wants = (now or future) if "future" in label else now
        return choose(q, wants), "answers"
    if "relocat" in label and ans.get("willing_to_relocate") is not None:
        return choose(q, ans["willing_to_relocate"]), "answers"
    if ("salary" in label or "compensation" in label) and q.kind in ("text", "textarea") and ans.get("salary_expectation"):
        return ans["salary_expectation"], "answers"
    if ("start date" in label or "available to start" in label or "notice period" in label) and ans.get("earliest_start_date"):
        return ans["earliest_start_date"], "answers"
    if "how did you hear" in label and q.options:
        pick = next((o["value"] for o in q.options if re.search(r"company (web)?site|careers? (page|site)", o["label"], re.I)), None)
        return (([pick] if q.kind == "multiselect" else pick), "answers") if pick else (None, None)
    eeo_key = next((k for k, pat in EEO_PATTERNS if re.search(pat, label) or re.search(pat, field)), None)
    if eeo_key and q.options and (q.section == "eeo" or any(DECLINE.search(o["label"]) for o in q.options)):
        eeo = ans.get("eeo") or {}
        wanted = eeo.get(eeo_key, "decline")
        return choose(q, "decline" if wanted == "decline" else wanted), "answers"
    if "language" in label and ans.get("languages"):
        picks = [choose(q, lang) for lang in ans["languages"]]
        picks = [x for p in picks if p for x in (p if isinstance(p, list) else [p])]
        if picks:
            return (picks if q.kind == "multiselect" else picks[0]), "answers"
    if ("18" in label and ("age" in label or "years old" in label)) and ans.get("over_18") is not None:
        return choose(q, ans["over_18"]), "answers"
    brand = normalize(company).split()[0] if company and normalize(company) else ""
    if brand and PREVIOUSLY_EMPLOYED.search(label) and re.search(rf"\b{re.escape(brand)}\b", label):
        worked_there = any(brand in normalize(e.get("company") or "") for e in prof.get("experience") or [])
        return choose(q, worked_there), "profile"
    if ans.get("auto_acknowledge") and q.required and ACKNOWLEDGE.search(label) and q.options:
        affirm = [o for o in q.options if AFFIRM.match(o["label"].strip())]
        if affirm and len(affirm) == len(q.options):  # a single "I agree" style tick box, no real choice
            return ([affirm[0]["value"]] if q.kind == "multiselect" else affirm[0]["value"]), "consent"
    return None, None


EEO_PATTERNS = (("gender", r"\bgender\b"), ("race", r"\brac(e|ial)\b|ethnic(ity)?\b(?!.*hispanic)"),
                ("hispanic_latino", r"hispanic|latin[oax]"), ("veteran", r"\bveteran"), ("disability", r"disabilit"))
PREVIOUSLY_EMPLOYED = re.compile(
    r"(ever|previously|currently or have you ever) (been )?(employed|worked)|(employee|work(ed)?) (at|for) .* before"
    r"|(provided|done|performed) (any )?contract(or)? work", re.I)
ACKNOWLEDGE = re.compile(r"privacy|acknowledg|consent|certif|attest|agree|arbitrat|policy|confirm receipt|have read|terms|i understand", re.I)
SIGNATURE = re.compile(r"signature|sign by typing|type your (full )?(legal )?name to sign", re.I)
STATE_NAMES = {abbr: name.title() for name, abbr in US_STATES.items()}
AFFIRM = re.compile(r"(i |yes,? i )?(agree|acknowledge|understand|have read|certify|confirm|accept|consent)|acknowledged|^yes$", re.I)


def draft_answers(p: Paths, job: dict, questions: list[Question], company: str) -> list[dict]:
    prof, ans = profile.load_profile(p), profile.load_answers(p)
    saved = ans.get("saved") or []
    rows = []
    for i, q in enumerate(questions):
        value, source = None, "none"
        match = next((s for s in saved if norm_label(s["question"]) == norm_label(q.label)), None) \
            or next((s for s in saved if _similar(s["question"], q.label)), None)
        if match:
            value = choose(q, match["answer"]) if q.options else match["answer"]
            source = "saved" if value is not None else "none"
        if value is None:
            value, src = _profile_value(q, prof, ans, company)
            source = src or "none"
        rows.append({**q.to_dict(), "value": value, "source": source if value not in (None, "", []) else "none", "position": i})

    essays = [r for r in rows if r["kind"] == "textarea" and r["source"] == "none"]
    if essays and llm.available(p):
        try:
            drafts = draft_essays(p, job, company, essays, prof)
            for r in essays:
                if drafts.get(r["field"]):
                    r["value"], r["source"] = drafts[r["field"]], "llm"
        except llm.LLMUnavailable:
            pass
    return rows


ESSAY_SCHEMA = {
    "type": "object",
    "properties": {"answers": {"type": "array", "items": {"type": "object", "properties": {
        "field": {"type": "string"}, "answer": {"type": "string"}}, "required": ["field", "answer"]}}},
    "required": ["answers"],
}
ESSAY_SYSTEM = """You draft short job-application answers for one candidate, in their voice (first person).
Use only facts from the candidate's profile and resume; never invent employers, numbers, skills or experiences.
Be specific to the company and role, plain and sincere, 60-150 words unless the question implies a short answer.
If a question cannot be answered truthfully from the material, return an empty answer for it."""


def draft_essays(p: Paths, job: dict, company: str, essays: list[dict], prof: dict) -> dict[str, str]:
    resume_key = job.get("resume_key") or (profile.resumes_index(p) or [{}])[0].get("key")
    resume = profile.resume_text(p, resume_key) if resume_key else ""
    questions = "\n".join(f"- field `{e['field']}`: {e['label']}" for e in essays)
    prompt = f"""Company: {company}
Role: {job['title']}

Job description:
{(job.get('description') or '')[:8000]}

Candidate profile summary: {prof.get('summary', '')}
Candidate resume:
{resume[:10000]}

Questions to answer:
{questions}"""
    out = llm.ask_json(p, ESSAY_SYSTEM, prompt, ESSAY_SCHEMA)
    return {a["field"]: a["answer"].strip() for a in out.get("answers", [])}


def store(conn: sqlite3.Connection, job_id: str, rows: list[dict]) -> None:
    conn.execute("DELETE FROM answers WHERE job_id=?", (job_id,))
    conn.executemany(
        "INSERT INTO answers (job_id, field, label, kind, required, options, value, source, position, section)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        [(job_id, r["field"], r["label"], r["kind"], int(r["required"]), json.dumps(r["options"]),
          json.dumps(r["value"]), r["source"], r["position"], r["section"]) for r in rows],
    )
    conn.commit()


def load(conn: sqlite3.Connection, job_id: str) -> list[dict]:
    rows = conn.execute("SELECT * FROM answers WHERE job_id=? ORDER BY position", (job_id,)).fetchall()
    return [{**dict(r), "options": json.loads(r["options"] or "[]"), "value": json.loads(r["value"]) if r["value"] else None}
            for r in rows]


def missing_required(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r["required"] and r["value"] in (None, "", [])]


def remember(p: Paths, label: str, answer) -> None:
    """Save an answer as your default for this question wording (Save as default)."""
    from .config import save_yaml

    ans = profile.load_answers(p)
    saved = [s for s in ans.get("saved") or [] if norm_label(s["question"]) != norm_label(label)]
    saved.append({"question": label, "answer": answer})
    ans["saved"] = saved
    save_yaml(p.answers, ans)

