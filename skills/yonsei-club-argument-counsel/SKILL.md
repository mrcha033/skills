---
name: yonsei-club-argument-counsel
description: Build source-grounded arguments for agenda items across Yonsei University student governance, including the General Student Council's student assembly, student referendum, expanded steering committee, central steering committee, legislation and audit bodies, plus the General Student Club Union's representatives meeting, steering committee, and related bodies. Use when a user supplies an agenda, dispute, desired position, motion, amendment, procedural objection, quorum question, sanction, finance, election, or registration issue and wants current rules turned into a meeting-ready argument with counterarguments, exact citations, and explicit uncertainty.
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
2. Refresh or check the official corpus:

   ```bash
   python3 "$SKILL_DIR/scripts/refresh_sources.py" --check
   python3 "$SKILL_DIR/scripts/validate_corpus.py"
   ```

   If checking shows a changed official attachment, run `--update`, rebuild the index, and validate again. Never silently mix superseded and current texts.

3. Resolve jurisdiction before searching. Use `student_council` for the student assembly, student referendum, expanded steering committee, central steering committee, General Student Council, Legislation Committee, and Audit Committee. Use `club_union` for the General Student Club Union, club representatives meeting, club steering committee, and club general bodies. Do not search both domains merely to obtain a favorable rule.

4. Prepare a resumable case from the user's agenda and desired position. This automatically routes by meeting body, runs separate supporting, adverse/exception, and procedure searches, and registers the full candidate authorities:

   ```bash
   python3 "$SKILL_DIR/scripts/prepare_case.py" \
     --agenda "..." --position "..." --body "..." --meeting-date YYYY-MM-DD \
     --domain student_council
   ```

   Omit `--domain` only when the full body name makes the route unambiguous.

5. Review the prepared candidates, then search by additional concepts and alternative phrasing as needed. Search is discovery only:

   ```bash
   python3 "$SKILL_DIR/scripts/search_authorities.py" \
     --query "정족수 의결 재청 수정안" --domain student_council \
     --target all --as-of YYYY-MM-DD
   ```

   A zero-result search does not establish that no rule exists. Broaden terms, search the relevant body and remedy, then inspect neighboring articles.

6. Retrieve every relied-on result by identifier:

   ```bash
   python3 "$SKILL_DIR/scripts/detail_authority.py" --id RULE-2025-09-23:14
   ```

   Do not cite a search snippet as if it were the full provision. Require `content_verified=true`.

7. If the internal rules contain a gap and external Korean law matters, use:

   ```bash
   python3 "$SKILL_DIR/scripts/search_korean_law.py" search --target law --query "개인정보 보호법"
   python3 "$SKILL_DIR/scripts/search_korean_law.py" detail --target law --id "<search result ID>"
   ```

   Keep external law separate from the Union's controlling internal rules. Follow the hierarchy and gap rule in current Rule article 152.

8. Build `sources/sources.jsonl` and `artifacts/argument_ledger.jsonl` using the schemas in `references/`. For each outcome-relevant proposition:

   - record supporting and adverse source IDs;
   - perform one targeted counter-search;
   - distinguish direct rule, interpretation, fact, precedent, policy, and external law;
   - state the inference rather than hiding it;
   - flag conflicts and disputed facts.

9. Run the deterministic gate before drafting:

   ```bash
   python3 "$SKILL_DIR/scripts/validate_argument_ledger.py" --case "<case-dir>"
   ```

   Draft controlling assertions only from `outputs/verified_propositions.json`. Use `arguable_propositions.json` with qualified language. Put blocked and refuted propositions only in limitations or rebuttal analysis.

10. Calculate thresholds rather than estimating:

   ```bash
   python3 "$SKILL_DIR/scripts/calculate_threshold.py \
     --basis present --count 17 --rule majority
   ```

11. Draft the answer using the contract in `references/output-contract.md`. Add `[P:...]` tokens after material propositions and `[S:...]` tokens for cited sources. Include the strongest counterargument and the response.

12. Run the leakage and citation-resolution evaluator:

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
- For the General Student Council corpus, use the public Drive reached through the official Legislation Committee Instagram profile and Linktree. For the club-union corpus, use the General Student Club Union's official archive.
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

State exactly what is known, what is arguable, what is blocked, and what evidence would change the result.

## References

- Corpus provenance and versions: `references/source-manifest.json`
- Indexed provisions: `references/article-index.jsonl`
- Source schema: `references/authority-schema.json`
- Proposition schema: `references/proposition-schema.json`
- Required answer structure: `references/output-contract.md`
