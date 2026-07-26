---
name: katok-reply-reuse
description: Find and reuse the user's own past KakaoTalk replies for a new incoming message, with provenance and only explicit placeholder substitution. Use when the user asks what to reply, wants their real KakaoTalk tone, dislikes AI-sounding rewrites, or wants a past response to a similar situation retrieved from a macOS KakaoTalk archive.
---

# KakaoTalk Reply Reuse

Retrieve first. Do not generate a polished AI reply and then try to humanize it.

## Workflow

1. Run `katok doctor --json`. Report archive/index readiness without opening message content.
2. Obtain the incoming message and enough relationship/context terms to search.
3. Search the authorized archive with the KakaoTalk archive skill. Retrieve explicit matching chunks only when the user has asked to use their messages for this reply.
4. Keep only records that metadata identifies as authored by the user. If authorship cannot be established, do not use the text as the user's exemplar.
5. Build an authorized JSONL candidate file following `references/reuse-contract.md`.
6. Rank it:

   ```bash
   python3 "$SKILL_DIR/scripts/rank_replies.py" \
     --input candidates.jsonl \
     --query "새로 받은 메시지" \
     --limit 3
   ```

7. Prefer the highest-confidence exact past reply. Substitute only placeholders explicitly provided with `--slot key=value`.
8. Show the source date/chat label, similarity score, and what changed. Never send automatically.

## Abstain

If no strong exemplar exists, say so and ask for either a rough draft or a known similar conversation. Do not silently fall back to generic generation. Use a generated fallback only when the user explicitly requests one, and label it as generated rather than retrieved.

## Privacy

- Read the smallest number of chunks needed.
- Do not expose unrelated participants or messages.
- Do not access the underlying KakaoTalk database directly.
- Do not write private messages into the skill repository, tests, logs, or examples.
- Use only synthetic data in `scripts/self_test.py`.

Run `python3 "$SKILL_DIR/scripts/rank_replies.py" --self-test` after modifying ranking.
