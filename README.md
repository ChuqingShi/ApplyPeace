# ApplyPeace

Sponsor-aware job applications on autopilot. ApplyPeace finds jobs at top companies (Fortune 500,
S&P 500, Nasdaq-100, Forbes Global 2000, Forbes AI 50 — you choose) that have a real H-1B record.
An AI shortlists them, you approve from your phone, and it applies.

> Status: working MVP for Greenhouse, Ashby and Lever job boards. Workday is next.
> The command-line tool is `ap`.

## How it works

1. **Company lists you choose.** Switch the lists on or off; a job qualifies if its employer is on
   any list that's on. ApplyPeace matches each company to its own official job board and only
   accepts a board whose owner name really matches the company.
2. **Scan every 8 hours.** Jobs come straight from the employers' Greenhouse, Ashby and Lever
   boards. They are never scraped from LinkedIn or Indeed.
3. **H-1B evidence.** Each employer's certified H-1B filings come from public Department of Labor
   LCA data. With the H-1B filter on, employers without a recent record are skipped, and so are
   postings that say they won't sponsor.
4. **AI shortlist.** Free filters and keyword ranking pick the best ~30 new jobs. Claude then scores
   each one 1–5, explains why it fits (quoting your resume) and lists its concerns.
5. **Answers prepared in advance.** ApplyPeace reads every application question before any browser
   opens and drafts the answers:
   - from your profile and standard answers;
   - from defaults you saved earlier;
   - essays from AI drafts.

   Anything personal it can't know is left for you.
6. **You approve on your phone.** On the private phone page you can:
   - edit any answer;
   - redraft essays;
   - pick which resume to send;
   - approve, skip or reject with a reason, so the picker learns your taste.

   Approval locks the exact answers and resume you saw; any later change needs a new approval.
7. **It applies.**
   - Applications go out one at a time, with a daily cap and a Pause button.
   - Each one is recorded with screenshots of the filled form and the confirmation page, the exact
     answers, the job description and the date.
   - CAPTCHAs, emailed codes and unfamiliar pages are handed to you; ApplyPeace never tries to get
     around them.
   - For an emailed code, you type it on the phone page and ApplyPeace finishes the application.

## Quick start

```bash
uv sync
scripts/install-browser-libs.sh   # headless Chromium libraries, no root needed
PLAYWRIGHT_BROWSERS_PATH=../.applypeace-runtime/ms-playwright uv run playwright install chromium-headless-shell
uv run ap lists update            # S&P 500 and Nasdaq-100 from public sources
uv run ap lists resolve           # match companies to their official job boards
uv run ap h1b update              # load DOL H-1B filings (current and last fiscal year)
uv run ap publish                 # private phone page over Openbase VPN; starts guided setup
uv run ap schedule install        # run the cycle, digest and apply steps automatically
```

Fortune and Forbes rankings are copyrighted, so ApplyPeace doesn't ship them. Save a list as CSV
(it needs a company-name column; country and rank columns are optional) and run
`ap lists import fortune500 file.csv`. The Forbes Global 2000 import splits itself into US and
non-US headquarters.

## Everyday commands

| Command | What it does |
|---|---|
| `ap queue` / `ap show <job>` | jobs waiting for you, and one job's answers |
| `ap set <job> <field> <answer> [--default]` | change an answer (option text is fine) |
| `ap approve <job>` / `ap approve all` | approve one job, or every job with no open questions |
| `ap reject <job> --reason too_senior` / `ap skip <job>` | reject or postpone a job |
| `ap pause` / `ap resume` | stop or restart submitting |
| `ap status` / `ap doctor` | overview, and a setup and health check |

Your personal data (profile, answers, resumes, tracker) lives in a separate private folder
(`APPLYPEACE_HOME`, default `ApplyPeace-data` next to this repo). It is versioned with its own
local git history and never committed here.

ApplyPeace is not legal or immigration advice and is not affiliated with USCIS, DOL, Fortune or
Forbes.

## License

MIT
