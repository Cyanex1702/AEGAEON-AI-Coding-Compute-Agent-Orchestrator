# AEGAEON Contract Governance, Milestone DAG, and Broke Boy Runtime Pass

Verified: 2026-08-25

## Implemented

- Generated Colab Cell 4 uses structured `BatchEncoding`, embedding-device placement, `inputs["input_ids"]`, and `model.generate(**inputs)`.
- Generated notebook sanitization uses character/ordinal checks and cannot embed literal NUL bytes.
- Every freshly generated code cell compiles as standard Python; the install cell no longer depends on notebook shell magic.
- Colab and local Transformers workers carry changes, contract proposals, risks, blockers, acceptance evidence, tests, and notes through `JobResult`.
- AEGAEON Core creates, version-controls, Git-commits, and deterministically rematerializes `contracts/CanonicalContract.json`.
- Foundation workers receive explicit protected-file instructions and no longer own canonical contract creation.
- Contract v1 includes project-specific services, methods, UI/state, architecture, entities, and protected paths.
- Proposal decisions are tied to exact semantic targets and expected revisions. There is no global approved-proposal bypass.
- Approved proposals increment the contract version, persist the old/new relationship and affected tasks, refresh future task versions, and trigger Core rematerialization.
- Direct worker create/update/delete/rename operations against protected files are rejected even when another proposal was approved.
- Valid worker generation is persisted as `WORKER_RESULT_RECEIVED` before proposal review and integration.
- Contract-governance failures are non-generation failures with `CONTRACT_VALIDATION` and contract-specific classifications/retry actions.
- Broke Boy execution uses tasks generated from persisted milestones; worker count controls available execution capacity rather than DAG shape.
- Small projects receive a bounded two-milestone/six-task plan. Larger projects expand to milestone-specific task graphs with parallelizable scopes.
- The architecture UI shows actual tasks under each milestone, proposal status/details, exact Core decisions, revision history, and high-visibility contract failure evidence.

## Partially implemented

- `LeadEngineerProvider` is now an explicit replaceable boundary, and deterministic Lead output remains the offline/test fallback.
- A real Broke Boy worker-backed Lead provider is not yet scheduled for analysis/blueprint/contract/milestone/review operations. Current Broke Boy architecture preparation still uses the deterministic Lead provider before coding workers run.
- Automatic additive proposal review is implemented. Advanced manual approval of breaking proposal types remains intentionally unavailable; those proposals are rejected for safety.

## Still open

- Launch a freshly generated, unmodified notebook in real Google Colab and verify it reaches `job.completed` after these source changes.
- Run a live real-Qwen calculator project through contract materialization, milestone tasks, integration, verification, and final review.
- Run a live real-Qwen contract-proposal scenario and verify the full remote WebSocket path plus rematerialization.
- Implement and exercise the worker-backed Broke Boy Lead provider and structured Lead job schemas.

## Database migration

Schema migration version 6 adds:

- `worker_results` durable post-generation records.
- proposal `current_value`, `proposed_value`, and `expected_revision` fields.
- revision `previous_version` and `affected_tasks` fields.
- richer blueprint `project`, `ui`, and `state` fields.

The live database reports schema versions 2 through 6 and existing projects load successfully.

## New models and protocol schemas

- `WorkerChange`
- `WorkerResult`
- `WorkerRisk`
- `WorkerBlocker`
- `AcceptanceEvidence`
- extended `ContractProposal`
- `WorkerResultRecord`
- `ProjectContractMaterializer`
- `LeadEngineerProvider`
- contract-specific `FailureCode` values and retry strategies

## Tests

- Full backend: 86 passed.
- Focused notebook/governance/milestone suite: 27 passed.
- Generated notebook: 4 code cells compile; no NUL bytes.
- Frontend time tests: 4 passed.
- Ruff: passed.
- TypeScript: passed.
- ESLint: passed.
- Next.js production build: passed.
- Existing controller/mock-worker Broke Boy E2E completes using the new small-project milestone DAG.

## Manual verification

- Migrated and restarted the real local controller and UI.
- Controller and UI both return HTTP 200.
- Existing projects load after migration.
- Live orchestration responses expose `milestone_tasks`, `contract_proposals`, and `contract_revisions`.
- No real Google Colab GPU session was controlled from this machine, so remote real-Qwen acceptance remains open.