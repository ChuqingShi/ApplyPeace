"""Fill a development data home with a fictional persona ("Alex Rivera") for testing.

Usage: APPLYPEACE_HOME=/path/to/dev-home uv run python scripts/seed-sample-persona.py [--llm]
Never point this at your real data home.
"""

import sys
from pathlib import Path

from applypeace import config, history, profile

p = config.paths()
if p.home.name == "ApplyPeace-data":
    sys.exit("Refusing to seed the real data home; set APPLYPEACE_HOME to a dev folder.")
p.home.mkdir(parents=True, exist_ok=True)

profile.add_resume(p, Path(__file__).parent.parent / "tests/fixtures/sample_resume.pdf", "Data analyst")
config.save_yaml(p.profile, {
    **profile.PROFILE_TEMPLATE,
    "first_name": "Alex", "last_name": "Rivera", "email": "alex.rivera@example.com", "phone": "(555) 010-2030",
    "location": {"city": "Austin", "state": "TX", "country": "United States"},
    "links": {"linkedin": "https://linkedin.com/in/alex-rivera-example", "github": "https://github.com/alexrivera-example",
              "portfolio": "", "other": ""},
    "summary": "Data analyst with 3 years of experience in SQL, Python and experimentation.",
    "education": [{"school": "University of Example", "degree": "M.S.", "field": "Business Analytics", "start": "2021", "end": "2023"}],
    "experience": [{"company": "Example Fintech Co.", "title": "Data Analyst", "start": "Jun 2023", "end": "Present",
                    "highlights": ["Built SQL pipelines for weekly revenue reporting", "Ran 20+ A/B tests"]}],
    "skills": ["SQL", "Python", "pandas", "Tableau", "A/B testing", "dbt"],
})
answers = profile.load_answers(p)
answers.update(work_authorized_us=True, needs_sponsorship_now=False, needs_sponsorship_future=True,
               visa_status="F-1 STEM OPT", willing_to_relocate=True, salary_expectation="$110,000",
               earliest_start_date="Two weeks after offer", over_18=True,
               languages=["English", "Spanish"], auto_acknowledge=True)
config.save_yaml(p.answers, answers)
settings = config.load_settings(p)
settings["targets"].update(titles=["Data Analyst", "Analytics Engineer", "Business Analyst", "Product Analyst",
                                   "Data Scientist", "Business Intelligence"],
                           exclude_titles=["Director", "Principal", "Intern", "Manager"])
settings["llm"].update(consent="--llm" in sys.argv, judge_daily_limit=8)
settings["batch_size"] = 5
settings["setup_complete"] = True
config.save_settings(settings, p)
history.commit(p, "Seed sample persona")
print(f"Seeded Alex Rivera in {p.home} (LLM {'on' if '--llm' in sys.argv else 'off'})")
