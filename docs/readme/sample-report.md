# harness-bench report (synthetic sample)

> Synthetic. Every number, repo name and run id below is invented to show the shape of
> `out/report.md`. It is not output from a real run.

Generated 2026-11-01 06:30 UTC, extractor version 2. Costs are list-price reference numbers. Cells with n below 3 show n only.

## Pool sizes

| view | records |
| --- | --- |
| loosened | 12 |
| strict | 5 |

## Headline

Grouped by arm, source, kind, view and stratum. History and run records never share a row.

| arm | source | kind | view | stratum | n | median cost_usd | median attention_s | median human_turns | escaped-major rate | cost per record with zero escaped majors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A | history | whole | loosened | large | 6 | 70.00 | 3,000.00 | 11.00 | n/a |  |
| Aprime | history | whole | loosened | small | 4 | 6.00 | 550.00 | 2.00 | n/a |  |
| V | run | whole | loosened | small | 2 |  |  |  |  |  |

## Cost per phase, A history

| phase | n records | median cost_usd | median wall_s | median attention_s | share of cost |
| --- | --- | --- | --- | --- | --- |
| spec | 4 | 9.00 | 5,000.00 | 1,400.00 | 8% |
| plan_review | 5 | 14.00 | 4,000.00 | 400.00 | 21% |
| execution | 6 | 40.00 | 15,000.00 | 950.00 | 45% |
| pr_review | 5 | 26.00 | 5,800.00 | 600.00 | 19% |

## Runs

| run | task | arm | outcome | test gate | stand-in rounds | cost_usd | wall_s |
| --- | --- | --- | --- | --- | --- | --- | --- |
| tiny-V-20261101101500 | tiny | V | done | pass | 0 | 0.05 | 9.00 |

## Coverage

| repo | month | sessions seen | unattributed | excluded | attributed |
| --- | --- | --- | --- | --- | --- |
| sample-app | 2026-10 | 40 | 6 | 1 | 33 |
