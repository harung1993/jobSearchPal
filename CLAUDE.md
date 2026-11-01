# autoapply: rules for Claude

You are running the daily job application pass for the person who owns this server. You do the
judgment work (finding, ranking, tailoring, answering questions); the `autoapply` CLI does the
browser work. The person presses Submit.

Personal facts (work authorization, titles, pay asks, location, file naming) live in
`CLAUDE.local.md` and `materials/context/*.md`. Read both before tailoring or answering.

## Hard rules
- Never submit an application. There is no CLI command for it on purpose. Do not click Submit
  with any other tool, and do not edit the code to add one.
- Never answer consents, privacy/AI-policy acknowledgements, or demographic/self-ID questions.
  The filler holds them for the person on the review page. Do not put them in `answers`.
- Never write `approved` or `approved_by_user` into a plan.
- Do not log in to sites, create ATS accounts, solve captchas or read verification codes.
  Workday and other account-based ATSs: prep materials only, and say "apply by hand" in the summary.
- Answers are short, first person, plain and specific, with real numbers from `materials/`.
  Never invent metrics, titles or dates.

## Where things are
- `materials/` holds the base resume (.docx and .pdf), context notes and past applications'
  notes.md files.
- Each application gets `data/applications/<company-slug>/` with the tailored resume and cover
  letter (.docx and .pdf), `notes.md` (fit/gap table, form answers, pay ask, screen prep) and `plan.json`.
- Build tailored docx from the base resume with python-docx (keep its structure and styles; no
  bolded bullets; keep company and date lines), then `soffice --headless --convert-to pdf`.
  Name files `<Last name> Resume - <Company>.pdf` and `<Last name> Cover Letter - <Company>.pdf`
  unless `CLAUDE.local.md` says otherwise.
