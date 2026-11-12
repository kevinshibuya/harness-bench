You are matching one Claude Code session to the pull request it worked on.

Below are the merged pull requests of the repository (number, title, head branch, and the window from the branch's first commit to the merge), then the human turns and skill calls of one session with their timestamps. Spec and plan file names usually name their feature.

Answer with one JSON object and nothing else:

{"pr": <the PR number, or null when the session worked on none of them>, "why": "<one line>"}

Pick a PR only when the session's content clearly belongs to it. When in doubt, answer null.
