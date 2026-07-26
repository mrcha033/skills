# Reply reuse contract

Each authorized JSONL record must contain:

```json
{
  "id": "opaque-local-id",
  "context": "the message or situation this was replying to",
  "reply": "the user's exact past reply",
  "author_is_user": true,
  "timestamp": "2026-01-02T03:04:05+09:00",
  "chat_label": "non-sensitive label",
  "intent": "optional intent terms"
}
```

Do not include a candidate unless `author_is_user` is explicitly true. Prefer opaque identifiers and non-sensitive chat labels.

## Output modes

- `exact`: byte-for-byte reply reuse.
- `slot_fill`: replace only `{{placeholder}}` tokens using user-supplied slot values.
- `none`: no candidate cleared the score threshold.

Never infer a person's name, date, commitment, amount, or sentiment as a slot value.
