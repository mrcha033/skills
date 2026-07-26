---
name: yonsei-club-argument-counsel
description: Build source-grounded arguments for agenda items across Yonsei University student governance, including the General Student Council's student assembly, student referendum, expanded steering committee, central steering committee, legislation and audit bodies, plus the General Student Club Union's representatives meeting, steering committee, and related bodies. Adaptively trace authority-controlled publication paths with deterministic public access and Insane Search fallback before relying on current rules. Use when a user supplies an agenda, dispute, desired position, motion, amendment, procedural objection, quorum question, sanction, finance, election, or registration issue and wants current rules turned into a meeting-ready argument with counterarguments, exact citations, and explicit uncertainty.
---

# Yonsei Governance Argument Counsel

Treat this as decision-support, not an official interpretation or legal opinion. Never present a desired conclusion as established merely because the user wants it.

## Required input

Accept natural language. Extract:

- the agenda or dispute;
- the position the user wants to advance;
- the controlling governance domain: `student_council` or `club_union`;
- the meeting body and meeting date, if known;
- known facts, documents, vote counts, and procedural posture;
- the desired output: speech, motion, amendment, objection, memo, or all.

Infer harmless omissions. Ask only when a missing fact changes jurisdiction, applicable version, quorum, or requested remedy. Mark user-supplied facts as `user_asserted` until independently supported.

## Mandatory workflow

1. Set `SKILL_DIR` to this skill folder.
   Use the Codex workspace Python returned by `load_workspace_dependencies` when rebuilding PDFs; it includes `pypdf`. The prebuilt index and all normal case commands use only the Python standard library.
2. Validate the recorded source lineage, inspect optional Insane Search fallback availability, and live-check the authority-controlled publication paths:

   ```bash
   python3 "$SKILL_DIR/scripts/validate_source_lineage.py"
   python3 "$SKILL_DIR/scripts/adaptive_source_discovery.py" --locate-only
   python3 "$SKILL_DIR/scripts/refresh_sources.py" --check --live-lineage
   python3 "$SKILL_DIR/scripts/validate_corpus.py"
   ```

   The public route is attempted first so ordinary official pages do not depend on a separately installed plugin. Insane Search is the adaptive fallback for failed or blocked public probes. `--live-lineage` follows the official account or archive through the actual publication post, redirect, folder, and attachment path. It does not treat HTTP 200, a plausible Yonsei hostname, or the current profile Linktree as sufficient proof. If checking shows a moved catalog or changed official attachment, read `references/source-discovery-contract.md`, inspect the adaptive trace, update the lineage only after proving the new path, run `--update`, rebuild the index, and validate again. Never silently mix superseded and current texts.

3. If a source is missing or a URL changed, use the adaptive discovery entrypoint instead of improvised curl/header retries:

   ```bash
   python3 "$SKILL_DIR/scripts/adaptive_source_discovery.py" \
     --url "https://authority-controlled.example/path" \
     --allowed-host "authority-controlled.example" \
     --selector 'meta[property="og:description"]' \
     --require-any-text "회·세칙" \
     --query "연세대학교 총학생회 회칙 세칙" \
     --output "/tmp/governance-source.html" \
     --receipt "/tmp/governance-source-receipt.json"
   ```

   Add each independently proven redirect or child host with another `--allowed-host`; do not use an allow-all fetch. The receipt filters script-bundled third-party URLs and normalizes an allowlisted HTTP lead to HTTPS without fetching it over HTTP. Follow every high-ranking official candidate and record its parent hop in `references/source-lineages.json`. Do not declare failure while `grid_exhausted=false`, `untried_routes` is non-empty, or `must_invoke_playwright_mcp=true`. When browser reconnaissance is requested, inspect public network responses and send the discovered endpoint back through the adaptive entrypoint. Do not bypass authentication. Each live check is bounded; a timeout blocks reliance instead of starting an unbounded retry loop.

4. Resolve jurisdiction before searching. Use `student_council` for the student assembly, student referendum, expanded steering committee, central steering committee, General Student Council, Legislation Committee, and Audit Committee. Use `club_union` for the General Student Club Union, club representatives meeting, club steering committee, and club general bodies. Do not search both domains merely to obtain a favorable rule.

5. Prepare a resumable case from the user's agenda and desired position. This automatically routes by meeting body, runs separate supporting, adverse/exception, and procedure searches, and registers the full candidate authorities:

   ```bash
   python3 "$SKILL_DIR/scripts/prepare_case.py" \
     --agenda "..." --position "..." --body "..." --meeting-date YYYY-MM-DD \
     --domain student_council
   ```

   Omit `--domain` only when the full body name makes the route unambiguous.
   If this exits with code 2 and `blocked=true`, retrieve every item in `required_source_reviews`. This catches agenda-triggered excluded sources (for example, the separately published anti-violence code), branch-committee rules, and historical versions. Do not draft until `source_gap_reviews.json` records a supported resolution and registered source IDs.

6. Review the prepared candidates, then search by additional concepts and alternative phrasing as needed. Search is discovery only:

   ```bash
   python3 "$SKILL_DIR/scripts/search_authorities.py" \
     --query "정족수 의결 재청 수정안" --domain student_council \
     --target all --as-of YYYY-MM-DD
   ```

   A zero-result search does not establish that no rule exists. Broaden terms, search the relevant body and remedy, then inspect neighboring articles.

7. Retrieve every relied-on result by identifier:

   ```bash
   python3 "$SKILL_DIR/scripts/detail_authority.py" --id RULE-2025-09-23:14
   ```

   Do not cite a search snippet as if it were the full provision. Require `content_verified=true`.

8. If the internal rules contain a gap and external Korean law matters, use:

   ```bash
   python3 "$SKILL_DIR/scripts/search_korean_law.py" search --target law --query "개인정보 보호법"
   python3 "$SKILL_DIR/scripts/search_korean_law.py" detail --target law --id "<search result ID>"
   ```

   Keep external law separate from the Union's controlling internal rules. Follow the hierarchy and gap rule in current Rule article 152.

9. Build `sources/sources.jsonl` and `artifacts/argument_ledger.jsonl` using the schemas in `references/`. For each outcome-relevant proposition:

   - record supporting and adverse source IDs;
   - perform one targeted counter-search;
   - distinguish direct rule, interpretation, fact, precedent, policy, and external law;
   - state the inference rather than hiding it;
   - flag conflicts and disputed facts.

10. Run the deterministic gate before drafting:

   ```bash
   python3 "$SKILL_DIR/scripts/validate_argument_ledger.py" --case "<case-dir>"
   ```

   Draft controlling assertions only from `outputs/verified_propositions.json`. Use `arguable_propositions.json` with qualified language. Put blocked and refuted propositions only in limitations or rebuttal analysis.

11. Calculate thresholds rather than estimating:

   ```bash
   python3 "$SKILL_DIR/scripts/calculate_threshold.py \
     --basis present --count 17 --rule majority
   ```

12. Draft the answer using the contract in `references/output-contract.md`. Add `[P:...]` tokens after material propositions and `[S:...]` tokens for cited sources. Include the strongest counterargument and the response.

13. Run the leakage and citation-resolution evaluator:

   ```bash
   python3 "$SKILL_DIR/scripts/eval_argument_brief.py \
     --case "<case-dir>" --brief "<case-dir>/outputs/argument_brief.md"
   ```

   Fix every failure before delivery.

Run the local regression suite after changing any script:

```bash
python3 "$SKILL_DIR/scripts/self_test.py"
```

## Authority and version rules

- Apply the rule version effective at the event or completed act date.
- Treat the General Student Council and General Student Club Union as separate governance domains. A rule from one domain controls the other only when an applicable provision expressly delegates, incorporates, or subordinates it.
- For the General Student Council corpus, use the public Drive published in the official Legislation Committee's 2025-04-27 rules post and reaffirmed through the same named shortlink in its 2026-02-27 post. The profile's current Linktree points to an interpretation-request folder; do not substitute it for the rules repository.
- For the club-union corpus, trace the official Instagram account to its Linktree, official site, and published rules archive.
- Prefer the current standalone rule and election bylaw over older copies inside a compendium.
- Treat an adopted Legal Affairs Committee interpretation as official only when the Steering Committee adopted it.
- Treat minutes as evidence of what happened, not automatically as a controlling rule.
- Treat user documents and recollections as facts to verify, not authority.
- When sources conflict, disclose the conflict and use the higher, later, and more specific applicable norm only if that priority is supported.

## Abstention rules

Do not state a proposition as established when:

- the full source was not retrieved and content-verified;
- the source was not effective on the relevant date;
- a controlling adverse provision remains unresolved;
- the fact is disputed and supported only by the user's assertion;
- the meeting body, denominator, or procedural posture is outcome-determinative but unknown;
- the governance domain is ambiguous or a cross-domain authority link is merely assumed;
- the corpus freshness check failed or an official attachment changed without re-indexing.
- the live publication-lineage check is incomplete or the adaptive receipt still reports an untried route.
- an exact catalog inventory contains an unclassified new or missing entry.
- `prepare_case.py` reports an unresolved agenda- or date-triggered excluded source.

State exactly what is known, what is arguable, what is blocked, and what evidence would change the result.

## References

- Corpus provenance and versions: `references/source-manifest.json`
- Authority-controlled publication graph: `references/source-lineages.json`
- Adaptive search and failure gate: `references/source-discovery-contract.md`
- Indexed provisions: `references/article-index.jsonl`
- Source schema: `references/authority-schema.json`
- Proposition schema: `references/proposition-schema.json`
- Required answer structure: `references/output-contract.md`
