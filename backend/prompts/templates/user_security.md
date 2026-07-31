## Diff to Review

```diff
{diff}
```

## Retrieved Context (grounding)

{context}

## Task

Analyze the diff above for security vulnerabilities. Use the retrieved
context to understand the surrounding code. Return your findings as
JSON with this structure:

{{
  "findings": [
    {{
      "severity": "CRITICAL",
      "category": "sql-injection",
      "summary": "Unsanitized user input in SQL query",
      "file_path": "src/db.py",
      "line_start": 10,
      "line_end": 12,
      "suggestion": "Use parameterized queries instead of string formatting",
      "confidence": 0.95,
      "rationale": "The query uses f-string formatting to insert user_id directly into SQL"
    }}
  ]
}}

Return ONLY valid JSON. No prose before or after.