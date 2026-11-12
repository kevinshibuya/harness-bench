You are labelling the opening stretch of one Claude Code working session with the phase of work it belongs to.

The phases:

- spec: designing the change, answering design questions, writing or amending a spec
- plan: writing or amending an implementation plan
- plan_review: reviewing a spec or plan before implementation
- execution: implementing, running tests, fixing what the implementation broke
- pr_review: reviewing the finished change and fixing what the review found
- unassigned: none of the above (status questions, housekeeping)

Below are the stretch's human turns and the names of the tools called, in order.

Answer with one JSON object and nothing else:

{"phase": "<one of the phase names>", "why": "<one line>"}
