## Diff to Review

```diff
{diff}
```

## Retrieved Context (grounding)

{context}

## Task

Analyze the diff above for documentation issues. Use the retrieved
context to understand the surrounding code. Return your findings as
JSON with this structure:

{{
  "findings": [
    {{
      "severity": "MEDIUM",
      "category": "missing-docstring",
      "summary": "Public function X has no docstring",
      "file_path": "src/example.py",
      "line_start": 10,
      "line_end": 10,
      "suggestion": "Add a docstring describing what X does, its parameters, and return value",
      "confidence": 0.90,
      "rationale": "X is a public API but has no documentation for callers"
    }}
  ]
}}

Return ONLY valid JSON. No prose before or after.