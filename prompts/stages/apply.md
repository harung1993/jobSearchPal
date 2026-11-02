Today's job search, one application: {{JOB_ID}}. Follow AGENTS.md (read CLAUDE.local.md and materials/context/*.md
first). Work only on this role.

1. `autoapply show {{JOB_ID}}` for the posting. If its ats is "feed", find the same role on the company's own careers
   page (ATS URL) and work from that; if it's gone there, print "<Company>: posting gone" and stop.
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
6. `autoapply fill data/applications/<slug>/plan.json --job {{JOB_ID}}`. If the report lists unanswered required
   questions or errors, fix plan.json and fill again (max 2 more tries).
7. Print one line: "<Company> (<title>): ready to review" or "<Company>: apply by hand" or what went wrong.
