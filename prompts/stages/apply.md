Today's job search, one application: {{JOB_ID}}. Follow AGENTS.md (read CLAUDE.local.md and materials/context/*.md
first). Work only on this role.

1. `autoapply show {{JOB_ID}}` for the posting. If its ats is "feed", find the same role on the company's own careers
   page (ATS URL) and work from that; if it's gone there, print "<Company>: posting gone" and stop.
   Read the base resume from its .docx (python-docx via `python3`), not the PDF. Don't run `pdftotext` or `pdfinfo`:
   they aren't allowed and stop this step. To check a PDF's text or page count, use `python3` with pypdf, or the
   checks `autoapply fill` prints.
2. Tailor the resume and write a cover letter into data/applications/<company-slug>/ (docx + pdf), plus notes.md
   (fit/gap table, pay ask, form answers, short screen prep). Keep it to what's true in materials/.
3. If the posting is on Workday or another site that needs an account, stop here: say "apply by hand" in notes.md.
4. `autoapply scan <apply url>` for the form's questions.
5. Write data/applications/<slug>/plan.json:
   {"url": "<apply url>", "ats": "<greenhouse|lever|ashby|...>",
    "files": {"resume": "<abs path to resume pdf>", "cover_letter": "<abs path to cover pdf>"},
    "answers": {"<question label exactly as scanned>": "<answer>", ...}}
   Answer every question the profile doesn't already cover, short and specific. Leave out consents and
   self-ID questions: the filler handles those from the person's own standing answers.
   Build the PDFs last, after the final .docx edit (`soffice --headless --convert-to pdf --outdir <folder> <docx>`).
6. `autoapply fill data/applications/<slug>/plan.json --job {{JOB_ID}}`. Its output ends with "checks_failed": the
   pre-submit checks (stale or over-long PDFs, missing job dates, placeholders, unanswered required questions).
   Fix what you can (rebuild a PDF, restore a date line, answer a question) and fill again (max 2 more tries).
   Consent and self-ID checks are the person's to settle: leave those.
7. Print one line, naming any check still failing: "<Company> (<title>): ready to review" or "<Company>: apply by hand" or what went wrong.
