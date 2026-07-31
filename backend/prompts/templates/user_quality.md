## Diff to Review

```diff
{diff}
```

## Retrieved Context (grounding)

{context}

## Task

Analyze the diff above for quality issues. Use the retrieved
context to understand the surrounding code. Return your findings as
JSON with this structure:

{{
  "findings": [
    {{
      "severity": "MEDIUM",
      "category": "complexity",
      "summary": "Function has too many branches",
      "file_path": "src/example.py",
      "line_start": 10,
      "line_end": 25,
      "suggestion": "Extract branches into helper functions",
      "confidence": 0.80,
      "rationale": "The function has 7 branches, exceeding the recommended maximum of 5"
    }}
  ]
}}

Return ONLY valid JSON. No prose before or after.