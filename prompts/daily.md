Run today's job application pass. Follow AGENTS.md. Today's summary goes in data/daily/<YYYY-MM-DD>.md.

0. `autoapply sync-email --days 3` to file new replies, recruiter emails and work-search proof. It sorts them
   with the configured email classifier; emails it couldn't sort show on the dashboard's Emails page.

0b. Interview prep: `autoapply list --prep` lists applications with an interview email and no prep pack yet
   (or `"update": true` when a newer interview email means a new stage). For each, write the pack in Markdown
   to its `prep_path` (create the folder). Use the interview emails to tell the stage (recruiter screen,
   hiring manager, technical, panel/onsite) and focus on that stage. Sections:
   - **The stage**: who you're meeting, format and length if the email says, what this stage usually tests.
   - **What candidates report**: the interview process and questions others have reported for this company
     (and this role or team if found), from public pages only: Glassdoor/Indeed interview pages you can read
     without signing in, Reddit, Blind, levels.fyi, LeetCode discuss, the company's careers or engineering
     blog. Link every source next to what it says. If little is public, say so; never make questions up
     and call them reported.
   - **Likely questions for this role**: technical and case questions drawn from the posting's requirements
     (label these "from the posting", not "reported").
   - **Your stories**: 5-6 behavioral questions this company stresses, each with a short STAR answer built
     only from real experience in `materials/` and `CLAUDE.local.md`, with real numbers.
   - **About them**: what they do, recent news (with links and dates), the team, and how the role fits.
   - **Questions to ask** them (5-8, specific to this company and stage).
   - **Pay**: the ask from the application's notes.md if there is one, and the posted range.
   Mention new or updated packs in the summary.

1. Find roles. Prefer postings on the company's own ATS: aggregator listings are often stale.
   - `autoapply find`: new roles from every watched board, plus the RemoteOK, Remotive and
     HN Who's Hiring feeds. Feed hits (ats "feed") aren't apply-ready.
   - `autoapply alerts`: yesterday's LinkedIn/Built In/Indeed alert emails as text. Pull out the
     roles that fit.
   - For each promising feed or alert role, find the same posting on the company's careers page
     and its real ATS URL. If it's gone there, it's closed, so skip it. Track live ones with
     `autoapply add <ats url> --company <name> --title <title>` and `autoapply skip` the feed entry.
   - When a company with a good role runs Greenhouse, Lever, Ashby, Workable or SmartRecruiters,
     `autoapply board add <ats> <slug>` so future runs watch it directly.
   - Mondays only: search the web for about 10 companies you don't watch yet that post analytics or data
     engineering roles on those ATSs (e.g. site:job-boards.greenhouse.io "analytics engineer" remote)
     and `autoapply board add` them.
   - Skip anything already in `autoapply list` or `materials/applications/`.
2. Rank. Read each candidate (`autoapply show <id>` or the posting) and pick the best 1-2 by fit,
   then freshness, then pay. `autoapply skip <id> --reason "..."` the clear misses.
3. For each pick:
   a. Tailor the resume and write a cover letter into data/applications/<slug>/ (docx + pdf), plus notes.md.
   b. `autoapply scan <apply url>` to see the form's questions.
   c. Write data/applications/<slug>/plan.json:
      {"url": "<apply url>", "ats": "<greenhouse|lever|ashby>",
       "files": {"resume": "<abs path to resume pdf>", "cover_letter": "<abs path to cover pdf>"},
       "answers": {"<question label exactly as scanned>": "<answer>", ...}}
      Answer every non-held question that the profile doesn't already cover. Leave out consents
      and self-ID questions.
   d. `autoapply fill data/applications/<slug>/plan.json --job <id>`. If the report lists
      unanswered required questions or errors, fix plan.json and fill again (max 3 tries).
4. Write the summary. Start with this week's work-search activity count against the weekly minimum, any new
   replies or recruiter emails since yesterday, and interview prep packs written today, then for each pick, company, title, pay, one line on fit, what you still have
   to decide (held questions), and the review link http://<review host>:8787/job/<id>.
   List roles that need a by-hand apply (Workday etc.) with their materials folder.
5. Print one short line to stdout for the notification, e.g.
   "Ready to review: Acme (Sr Analytics Eng), Globex (Data Eng Mgr)". Print "No good matches today" if none.
