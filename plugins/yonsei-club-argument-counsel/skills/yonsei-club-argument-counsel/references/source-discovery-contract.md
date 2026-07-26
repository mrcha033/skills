# Official-source discovery contract

Use this contract whenever the packaged corpus may be stale, a source URL moved, or the user supplies a new governance document.

## Required search order

1. Start from an authority-controlled account or previously proven official anchor.
2. Search the anchor for the publication post, link hub, or archive entry that names the rules.
3. Follow each disclosed redirect and folder hop. Record the complete chain; do not jump from a plausible domain name directly to an attachment.
4. Fetch public pages through `scripts/adaptive_source_discovery.py`. It tries a deterministic unauthenticated public route first, delegates failed or blocked access to an installed Insane Search engine when available, and preserves every attempt plus the failure gate.
5. Require a positive selector or expected governance text. HTTP 200 or a Yonsei-looking hostname alone is not proof.
6. Restrict every fetch and redirect to explicitly allowed HTTPS hosts. The initial host is the default allowlist; add a child host only after the parent provides authority evidence.
7. Compare the complete relevant catalog inventory—not only known-item presence—plus file IDs, titles, dates, and SHA-256 values with `source-manifest.json`. A new or missing entry blocks currentness until it is included or explicitly classified.
8. Run `scripts/validate_source_lineage.py` and `scripts/check_official_paths.py` before accepting a moved or changed source.

For keyword-only discovery, search several Korean formulations before choosing a URL:

- `"연세대학교 총학생회 법제위원회" 회칙 세칙`
- `site:instagram.com/yonsei_legislation 회세칙 공개`
- `"연세총학법제위" 회세칙공개`
- `"연세대학교 총동아리연합회" 회칙 세칙`
- `site:linktr.ee yonsei 동아리 회칙`

Treat search results only as leads. Re-enter this contract at step 1 for every candidate.

## Failure gate

Do not say that a source is unavailable while any of these is true:

- the adaptive fetch reports `grid_exhausted=false`;
- `untried_routes` is non-empty;
- `must_invoke_playwright_mcp=true`;
- a redirect target, public API, rendered page, or archive route has not been tried;
- a response lacks a positive selector or expected governance text.
- a bounded check timed out or the exact catalog contains an unclassified entry.

If browser reconnaissance is required, inspect the page's network requests for public JSON or GraphQL responses, then send the discovered endpoint back through the adaptive entrypoint. Never bypass authentication, use private cookies in a public-source fetch, or treat a login wall as a technical challenge to evade.

## Acceptance rules

Accept a governance source only when all applicable checks pass:

- an authority-controlled anchor names or links the publication;
- every redirect and folder hop is recorded in `source-lineages.json`;
- the response is content-validated, not merely status-validated;
- the document title and applicable date match the intended rule;
- the PDF signature, plausible size, and expected SHA-256 pass;
- a changed attachment is manually inspected and re-indexed before substantive use.

Archive copies and search snippets may locate a missing source, but they do not silently replace the controlling current attachment. Mark them as secondary provenance.
