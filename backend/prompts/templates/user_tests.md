## Diff to Review

```diff
{diff}
```

## Retrieved Context (grounding)

{context}

## Task

Analyze the diff above for test coverage issues. Use the retrieved
context to understand the surrounding code. Return your findings as
JSON with this structure:

{{
  "findings": [
    {{
      "severity": "HIGH",
      "category": "missing-test",
      "summary": "No test for new function X",
      "file_path": "src/example.py",
      "line_start": 10,
      "line_end": 15,
      "suggestion": "Add a unit test for function X covering normal and edge cases",
      "confidence": 0.85,
      "rationale": "The new function has 3 branches but no test coverage"
    }}
  ]
}}

Return ONLY valid JSON. No prose before or after.