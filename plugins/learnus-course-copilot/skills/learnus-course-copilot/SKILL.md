---
name: learnus-course-copilot
description: Use the user's authenticated browser session to inspect Yonsei LearnUs courses, collect authorized course materials, identify assignments and deadlines, and produce study-ready structured output. Use for LearnUs course pages, lecture resources, VOD links, assignment tracking, or downloads when credential-based scripts fail or SSO state must remain in the browser.
---

# LearnUs Course Copilot

Operate through the user's existing authenticated browser. Never request, store, or pass Yonsei credentials on the command line.

## Workflow

1. Use a browser-control skill and open the exact LearnUs URL in the existing browser session.
2. Read the current URL and a bounded DOM snapshot.
3. If the page shows Portal Login, External Login, SSO, or a password field, stop and ask the user to sign in in that browser. Do not automate credentials or copy cookies.
4. On an authenticated course page, save or pass the page HTML to:

   ```bash
   python3 "$SKILL_DIR/scripts/analyze_learnus_snapshot.py" \
     --html snapshot.html \
     --base-url "https://ys.learnus.org/course/view.php?id=..."
   ```

5. Review the structured result. Use page locators from the fresh DOM snapshot to retrieve only the resources the user asked for.
6. Ask before triggering a browser download when the browser surface requires confirmation. Do not bypass DRM, access controls, enrollment restrictions, or expiring authorization.
7. Return the course title, materials, assignments/deadlines, VOD entries, inaccessible items, and the exact pages inspected.

## Boundaries

- Treat public web search as discovery only; it cannot authenticate LearnUs.
- Do not revive or depend on credential-based `learnus-dl.py`.
- Do not infer completion, attendance, or a deadline from a filename alone.
- Do not redistribute course materials or download an entire course unless the user explicitly asks.
- Keep signed URLs and tokens out of logs and delivered artifacts. Report a redacted URL or page locator when needed.
- Mark the result `login_required` when the snapshot is not authenticated.
- Follow `references/snapshot-contract.md` when adding parser support for a new LearnUs page shape.

Run `python3 "$SKILL_DIR/scripts/self_test.py"` after changing the parser.
