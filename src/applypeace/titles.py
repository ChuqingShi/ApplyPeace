"""AI job-title suggestions: read every resume and propose more titles worth searching for."""

from __future__ import annotations

from datetime import datetime, timezone

from . import llm, profile
from .config import Paths, load_settings, load_yaml, save_yaml

SCHEMA = {
    "type": "object",
    "properties": {
        "titles": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "title": {"type": "string"},
                "why": {"type": "string", "description": "one short sentence tying it to the resumes"},
                "fit": {"type": "string", "enum": ["core", "adjacent", "stretch"]},
            }, "required": ["title", "why", "fit"]},
        },
    },
    "required": ["titles"],
}

SYSTEM = (
    "You are a career advisor for data, AI and quantitative roles. From the candidate's resumes, suggest job "
    "titles they should search for. Use titles exactly as employers write them on job boards, without level "
    "words (no Senior, Junior, Staff, Lead, I/II/III), so one title matches every level. Include common "
    "synonyms and company-specific titles, adjacent fields their skills transfer to, and a few stretch titles. "
    "Do not repeat titles they already search for. Base every suggestion on evidence in the resumes."
)


def path(p: Paths):
    return p.personal / "title_suggestions.yml"


def suggest_titles(p: Paths) -> list[dict]:
    """Ask the AI for 15-25 more titles, save them for the settings page, and return them."""
    settings = load_settings(p)
    resumes = "\n\n".join(f"### Resume `{r['label']}`\n{profile.resume_text(p, r['key'])[:12000]}"
                          for r in profile.resumes_index(p))
    current = settings["targets"]["titles"]
    prompt = (f"Titles already searched: {', '.join(current) or '(none)'}\n"
              f"Level wanted: {settings['targets'].get('seniority', 'any')}\n\n{resumes}\n\n"
              "Suggest 15 to 25 more titles, best fits first.")
    result = llm.ask_json(p, SYSTEM, prompt, SCHEMA)
    have = {t.lower() for t in current}
    titles = [t for t in result["titles"] if t["title"].strip() and t["title"].lower() not in have]
    save_yaml(path(p), {"made": datetime.now(timezone.utc).isoformat(timespec="seconds"), "titles": titles})
    return titles


def saved(p: Paths) -> list[dict]:
    """The last suggestions, minus any you've since added to your titles."""
    have = {t.lower() for t in load_settings(p)["targets"]["titles"]}
    return [t for t in load_yaml(path(p), {"titles": []})["titles"] if t["title"].lower() not in have]
