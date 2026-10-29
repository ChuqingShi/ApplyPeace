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
2. **Scans at times you choose.** Jobs come straight from the employers' own Greenhouse, Ashby,
   Lever and Workday sites. They are never scraped from LinkedIn or Indeed. Tap Scan now for an
   extra scan at any time.
3. **Finding your roles.** See [How job matching works](#how-job-matching-works).
4. **H-1B evidence.** Each employer's certified H-1B filings come from public Department of Labor
   LCA data. With the H-1B filter on, employers without a recent record are skipped, and so are
   postings that say they won't sponsor.
5. **AI shortlist.** Free filters and keyword ranking pick the best new jobs, up to your daily
   limit (30 by default). Claude then scores
   each one 1–5, explains why it fits (quoting your resume) and lists its concerns.
6. **Answers prepared in advance.** ApplyPeace reads every application question before any browser
   opens and drafts the answers:
   - from your profile and standard answers;
   - from defaults you saved earlier;
   - essays from AI drafts.

   Anything personal it can't know is left for you.
7. **You approve on your phone.** On the private phone page you can:
   - edit any answer;
   - redraft essays;
   - pick which resume to send;
   - approve, skip or reject with a reason, so the picker learns your taste.

   Approval locks the exact answers and resume you saw; any later change needs a new approval.
8. **It applies.**
   - Applications go out one at a time, with a daily cap and a Hold sending button.
   - Workday jobs are the exception: Workday needs an account with each company, so you apply on
     the site yourself and tap "I applied" to record it.
   - Each one is recorded with screenshots of the filled form and the confirmation page, the exact
     answers, the job description and the date.
   - CAPTCHAs, emailed codes and unfamiliar pages are handed to you; ApplyPeace never tries to get
     around them.
   - For an emailed code, you type it on the phone page and ApplyPeace finishes the application.

## How job matching works

You list a few **roles** you want, such as Data Scientist or Machine Learning Engineer. A short
list is enough. ApplyPeace turns it into a search in three steps, using a little AI:

1. **Search plan, made once per change.** When your roles or level change, one AI request reads
   your resumes and roles. It writes a one-line summary of your background and picks two to six
   broad search phrases, such as "data scientist", "machine learning" and "quantitative". The
   plan is saved in `personal/search_plan.yml`, and the targets page shows its phrases.
2. **Collecting postings.**
   - Greenhouse, Ashby and Lever sites publish every job at once, so ApplyPeace reads them all.
   - Workday sites are too big for that, so ApplyPeace types each search phrase into the site's
     own search box. It keeps titles that loosely match (every word of a phrase, in any order)
     and stops paging once a page has none.
3. **Title screening, cheap and saved.**
   - Before any title is screened, the free filters run: company list, location, level, excluded
     words, posting age and sponsorship. Only jobs that pass them have their titles checked.
   - Titles nobody has screened yet go to Claude's smallest model (Haiku) in batches of 500. It
     answers which ones fit your roles, including other names for the same job, such as
     "Scientist, Machine Learning" or "Quant Researcher".
   - Every answer is saved in the database (the `title_screen` table), so each title is asked
     about only once. A typical scan sends one small request. Changing your roles or level starts
     the screening afresh.

Without AI, both steps fall back to word matching. A title matches a role when the role's words
appear in it in order, with at most one extra word between them. So "AI Engineer" matches
"Applied AI Engineer" but not "Engineer, AI".

The titles that pass then go to the AI scorer, which reads the whole posting against your resumes
(step 5 above). That is the only step that uses a larger model, within your daily limit.

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

Your personal data lives in a separate private folder (`APPLYPEACE_HOME`, default
`ApplyPeace-data` next to this repo) and is never committed here. The folder is split into:
- `personal/`: profile, answers, settings, resumes;
- `applications/`: readable history and per-job records with screenshots;
- `companies/`: the company list;
- `plan/`.

It keeps its own git history. `ap backup --connect you/ApplyPeace-data` links it to a **private**
GitHub repo, which is then pushed automatically after every scan and every application.

ApplyPeace is not legal or immigration advice and is not affiliated with USCIS, DOL, Fortune or
Forbes.

## License

MIT
