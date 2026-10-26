"""Stage 3: the LLM judge. Reads your resume and a job, returns a scored, explained recommendation."""

from __future__ import annotations

import json

from .. import llm
from ..config import Paths

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "number", "minimum": 1, "maximum": 5,
                  "description": "Overall fit, 1 (poor) to 5 (excellent)"},
        "dimensions": {
            "type": "object",
            "properties": {k: {"type": "number", "minimum": 1, "maximum": 5} for k in
                           ("skills", "experience_level", "domain", "location_and_authorization", "growth")},
            "required": ["skills", "experience_level", "domain", "location_and_authorization", "growth"],
        },
        "recommendation": {"type": "string", "enum": ["apply", "maybe", "skip"]},
        "reasons": {
            "type": "array", "maxItems": 3,
            "items": {"type": "object", "properties": {
                "point": {"type": "string"},
                "resume_quote": {"type": "string", "description": "Exact words from the resume that support the point"},
            }, "required": ["point", "resume_quote"]},
        },
        "concerns": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
        "sponsorship_clues": {"type": "string", "description": "What the posting says about visas, or ''"},
        "resume_key": {"type": "string", "description": "Key of the best resume variant to send"},
        "keywords": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
    },
    "required": ["score", "dimensions", "recommendation", "reasons", "concerns", "sponsorship_clues", "resume_key", "keywords"],
}

SYSTEM = """You are a careful career advisor screening job postings for one candidate.
Score fit on skills, experience level, domain, location and work authorization, and growth.
Rules:
- Every reason must quote the candidate's resume word for word. Never claim a skill or experience the resume does not show.
- List real gaps as concerns: missing must-have skills, more years required than the candidate has, citizenship or clearance requirements, wrong seniority.
- If the candidate needs visa sponsorship and the posting rules it out, recommend "skip".
- Recommend "apply" only for a genuinely good fit; use "maybe" for a stretch.
- Respect the candidate's stated preferences."""


def judge_job(p: Paths, job: dict, resumes: dict[str, str], candidate: dict, preferences: str) -> dict:
    resume_block = "\n\n".join(f"### Resume variant `{key}`\n{text[:12000]}" for key, text in resumes.items())
    prompt = f"""## Candidate
{json.dumps(candidate, indent=1)}

## Preferences learned from past choices
{preferences or "(none yet)"}

{resume_block}

## Job posting
Company: {job['company']}
Title: {job['title']}
Location: {job['location']}
H-1B record: {job.get('h1b_note') or 'unknown'}

{(job['description'] or '')[:15000]}
"""
    result = llm.ask_json(p, SYSTEM, prompt, JUDGE_SCHEMA)
    if result.get("resume_key") not in resumes:
        result["resume_key"] = next(iter(resumes))
    return result
