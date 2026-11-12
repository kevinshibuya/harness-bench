# mini_session fixture

Hand-written. `s1.jsonl` is one main session; `s1/subagents/agent-a1.jsonl` is the reviewer it dispatches.

Expected from `read_session(s1.jsonl)`, main events:

| kind | count | notes |
| --- | --- | --- |
| human | 2 | "Build feature x", "thanks". The `<command-name>` line and the `isMeta` line are not human. |
| assistant | 5 | one per assistant line; lines a1 and a2 share `message.id` m1 |
| tool_use | 3 | Skill (grilling), Bash, Agent (reviewer) |
| tool_result | 3 | the Bash one carries "On branch feat/x" in `text`; the Agent one carries `toolUseResult.agentId` = a1 |
| pr_link | 1 | https://github.com/k/r/pull/7 |

- Events with non-None usage: 4 (m1 once, m2, m3, m4).
- m1's lines carry output 50 then 250, as Claude Code streams a message: the finalised 250 is the one counted, attached to the first event.
- Skipped unparsable lines: 1. The `attachment` line is ignored silently.
- Branches: `ai-staging`, `feat/x`.
- Subagents: key `a1`, 2 events (1 human-shaped prompt, 1 assistant with usage).
