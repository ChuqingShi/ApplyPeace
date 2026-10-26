"""Turn a resume PDF's text into a draft profile the user then confirms.

With LLM consent, Claude reads the resume into the profile schema. Without it,
a simple parser picks out the obvious contact details and leaves the rest for
the user to fill in.
"""

from __future__ import annotations

import re

from . import llm
from .config import Paths

PROFILE_SCHEMA = {
    "type": "object",
    "properties": {
        "first_name": {"type": "string"},
        "last_name": {"type": "string"},
        "email": {"type": "string"},
        "phone": {"type": "string"},
        "location": {
            "type": "object",
            "properties": {"city": {"type": "string"}, "state": {"type": "string"}, "country": {"type": "string"}},
        },
        "links": {
            "type": "object",
            "properties": {
                "linkedin": {"type": "string"}, "github": {"type": "string"},
                "portfolio": {"type": "string"}, "other": {"type": "string"},
            },
        },
        "summary": {"type": "string"},
        "education": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "school": {"type": "string"}, "degree": {"type": "string"}, "field": {"type": "string"},
                "start": {"type": "string"}, "end": {"type": "string"}}},
        },
        "experience": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "company": {"type": "string"}, "title": {"type": "string"}, "start": {"type": "string"},
                "end": {"type": "string"}, "highlights": {"type": "array", "items": {"type": "string"}}}},
        },
        "skills": {"type": "array", "items": {"type": "string"}},
        "suggested_titles": {"type": "array", "items": {"type": "string"},
                             "description": "5-8 job titles this person is a strong fit for"},
    },
    "required": ["first_name", "last_name", "email", "experience", "skills", "suggested_titles"],
}

SYSTEM = (
    "You read resumes into structured data. Copy facts exactly as written; never invent, "
    "embellish or infer details that are not in the text. Leave a field empty if it is absent. "
    "Dates stay in the resume's own format."
)


def read_with_llm(p: Paths, text: str) -> dict:
    return llm.ask_json(p, SYSTEM, f"Resume text:\n\n{text[:30000]}", PROFILE_SCHEMA)


_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(\+?\d[\d\s().-]{8,}\d)")
_URL = re.compile(r"(https?://)?(www\.)?(linkedin\.com/in/[\w-]+|github\.com/[\w-]+)", re.I)


def read_basic(text: str) -> dict:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    first_line = lines[0] if lines else ""
    name_parts = first_line.split() if len(first_line.split()) <= 4 and not _EMAIL.search(first_line) else []
    links = {}
    for m in _URL.finditer(text):
        url = m.group(3)
        links["linkedin" if "linkedin" in url.lower() else "github"] = "https://" + url
    email, phone = _EMAIL.search(text), _PHONE.search(text)
    return {
        "first_name": name_parts[0] if name_parts else "",
        "last_name": " ".join(name_parts[1:]) if len(name_parts) > 1 else "",
        "email": email.group(0) if email else "",
        "phone": phone.group(1).strip() if phone else "",
        "links": links,
        "experience": [],
        "skills": [],
        "suggested_titles": [],
    }


def read_resume(p: Paths, text: str) -> tuple[dict, str]:
    """Return (draft profile, how it was read: "llm" or "basic")."""
    if llm.available(p):
        try:
            return read_with_llm(p, text), "llm"
        except llm.LLMUnavailable:
            pass
    return read_basic(text), "basic"
