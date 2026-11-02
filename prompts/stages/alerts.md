Today's job search, one short step: look up roles from yesterday's job-alert emails. Follow AGENTS.md.
Be brief and economical: a few minutes. A cheap model already read the emails and listed the roles that fit.

1. Read data/daily/alert-roles.json: [{"company", "title", "link", "why"}].
2. For each, find the same posting on the company's own careers page (its ATS: Greenhouse, Lever, Ashby,
   Workday...). The email link usually goes to LinkedIn or Indeed; don't log in there. If the role isn't on the
   company's site any more, skip it.
3. Track each live one: `autoapply add <ats url> --company <name> --title <title>`.
4. Print one line: "Tracked N of M alert roles" (and the companies), or "No alert roles still open".
