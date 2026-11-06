# poster-agent PR evidence and branch-governance audit — 2026-11-07

This is a point-in-time **metadata / CI / targeted contract review**, not a claim
that every historical change has been independently line-by-line audited.
Authority: GitHub PR state, exact head SHA and associated Actions runs.

## Inventory at audit cutoff (19 PRs before governance PR #21; #14 is an issue)

| PR | Exact head (prefix) | Disposition | Quick CI on that head | Formal review submission |
| --- | --- | --- | --- | --- |
| [#1](https://github.com/avaargsh/poster-agent/pull/1) | `9d34b665` | merged | [success](https://github.com/avaargsh/poster-agent/actions/runs/41941837842) | none |
| [#2](https://github.com/avaargsh/poster-agent/pull/2) | `4a0d6e44` | merged | [success](https://github.com/avaargsh/poster-agent/actions/runs/41947213270) | none |
| [#3](https://github.com/avaargsh/poster-agent/pull/3) | `87ca4ee4` | merged | [success](https://github.com/avaargsh/poster-agent/actions/runs/41949406375) | none |
| [#4](https://github.com/avaargsh/poster-agent/pull/4) | `44360bb7` | merged | [success](https://github.com/avaargsh/poster-agent/actions/runs/41951709303) | none |
| [#5](https://github.com/avaargsh/poster-agent/pull/5) | `63361d06` | merged | [success](https://github.com/avaargsh/poster-agent/actions/runs/41970582647) | none |
| [#6](https://github.com/avaargsh/poster-agent/pull/6) | `38548fdb` | merged | [success](https://github.com/avaargsh/poster-agent/actions/runs/41969790043) | none |
| [#7](https://github.com/avaargsh/poster-agent/pull/7) | `6049ef76` | merged | [success](https://github.com/avaargsh/poster-agent/actions/runs/41971442309) | none |
| [#8](https://github.com/avaargsh/poster-agent/pull/8) | `e7d82f2b` | merged | [success](https://github.com/avaargsh/poster-agent/actions/runs/41974072106) | none |
| [#9](https://github.com/avaargsh/poster-agent/pull/9) | `1399684c` | merged | [success](https://github.com/avaargsh/poster-agent/actions/runs/41981407620) | none |
| [#10](https://github.com/avaargsh/poster-agent/pull/10) | `b8a3e0c7` | merged | [success](https://github.com/avaargsh/poster-agent/actions/runs/41992635297) | none |
| [#11](https://github.com/avaargsh/poster-agent/pull/11) | `ab54c6e5` | merged | [success](https://github.com/avaargsh/poster-agent/actions/runs/41994774721) | none |
| [#12](https://github.com/avaargsh/poster-agent/pull/12) | `18a7810e` | merged | [success](https://github.com/avaargsh/poster-agent/actions/runs/41996121781) | none |
| [#13](https://github.com/avaargsh/poster-agent/pull/13) | `0cae567e` | merged | [success](https://github.com/avaargsh/poster-agent/actions/runs/42139712201) | none |
| [#15](https://github.com/avaargsh/poster-agent/pull/15) | `6cd82715` | merged | [success](https://github.com/avaargsh/poster-agent/actions/runs/42144014692) | none |
| [#16](https://github.com/avaargsh/poster-agent/pull/16) | `a1710e07` | merged | [success](https://github.com/avaargsh/poster-agent/actions/runs/42146552288) | COMMENTED (same owner) |
| [#17](https://github.com/avaargsh/poster-agent/pull/17) | `23708b5d` | merged | [success](https://github.com/avaargsh/poster-agent/actions/runs/42148525293) | COMMENTED (same owner) |
| [#18](https://github.com/avaargsh/poster-agent/pull/18) | `c34b4890` | open Draft | [success](https://github.com/avaargsh/poster-agent/actions/runs/42147167313) | COMMENTED (same owner) |
| [#19](https://github.com/avaargsh/poster-agent/pull/19) | `080aef57` | merged | [success](https://github.com/avaargsh/poster-agent/actions/runs/42152069677) | none |
| [#20](https://github.com/avaargsh/poster-agent/pull/20) | `8d3c348e` | open Draft; stacked on #18 | [new integration test run](https://github.com/avaargsh/poster-agent/actions/runs/42313440950) pending at audit | none |

At this inventory cutoff, seventeen PRs were merged and two remained Draft.\nGovernance PR #21 was opened **after** this inventory snapshot and is deliberately\nnot counted in the historical table. No observed closed-unmerged PR.
A passing individual head Quick CI does **not** prove a passing merged-tree CI.
Formal COMMENTED review also does **not** equal APPROVED independent review.
The audit does not establish a real-production OCR run or release authorization.

## Main baseline, dependencies and known limits

- Latest inspected main: `060bc592c265970264b06fa3d5cde40f6db0ecd0` (merged #19).
- #18 head `c34b48900a192b79fe797541fe6c4cb53c72296c` branches
  from older history and is behind main. The main branch includes OCR
  journaling / UNKNOWN recovery and fail-closed local model pinning from #17/#19.
- #20 is based on #18's feature branch. The preflight must never be presented
  as durable DesignDocument linkage, human review identity, real OCR or release.
- #14 is the M6 OCR evidence issue, **not** an absent PR number.
- The CI workflow intentionally has no push/synchronize trigger. In this
  governance PR we allow **explicit** validation on any stacked target branch
  by Ready or `ci:run`, rather than requiring a temporary base switch to main.
- No independent line-by-line review or branch-protection policy is proven
  by this inventory. Existing already-merged PRs cannot be retrospectively
  treated as independently approved.

## Required forward merge gates

1. Check open/Draft status, exact **40-character head SHA**, base SHA,
   changed-file diff, direct parent dependency and whether either moved.
2. For each head, explicitly trigger locked Python 3.12 Quick CI by Ready
   or `ci:run` label; record run/job completion. Never reuse a green run for
   a previous SHA. Skipped jobs are **not** passing evidence.
3. After parent update/rebase, inspect the **actual merge tree** and run
   relevant integration tests on that exact result before merging a child.
   Avoid force-pushing stale bases without a lease and reconciliation.
4. Resolve blocking review threads and seek an independent reviewer.
   Existing owner COMMENTED reports are evidence notes, not approval.
5. Distinguish **source-contract tests**, **real SQLite and restart**,
   **actual model execution**, **authenticated reviewer evidence**,
   **20 distinct real authorized SKUs**, and **production release**. None
   of these evidence levels substitute for another.
6. Keep Brief approval, campaign-scene proposal, DesignDocument
   revision approval, OCR comparison and release permission separate.
   Unresolved external side-effect outcomes remain UNKNOWN until reconciled.
7. No default paid generation or OCR downloads during ordinary CI;
   expensive real OCR workflows remain manually dispatched.

This document is an operational snapshot, not an automatically refreshed
dashboard. Update it explicitly as PRs change.
