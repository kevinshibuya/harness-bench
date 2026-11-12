You are reviewing a code change for defects. You get the task the change was written for, then the change as a unified diff.

Find real defects in the change: behaviour that is wrong for the task, bugs, crashes, data loss, security holes, broken or missing error handling that the task needed, and regressions to existing behaviour visible in the diff. Judge the change only against the task and the code. Do not report style preferences, naming, missing comments, or documentation.

Answer with a JSON list and nothing else. One object per defect:

[{"title": "<one line>", "where": "<file:line or file>", "why": "<the failure: inputs or state, and what goes wrong>", "severity_guess": "blocker | major | minor"}]

Answer [] when you find no defect.
