# AEGAEON Worker Reliability, Diagnostics, Time, and Project Management Acceptance

Verified: 2026-08-24

## Worker tokenizer fix

- Both local Transformers execution and newly generated Colab workers request a structured `BatchEncoding` with `return_dict=True` and `return_tensors="pt"`.
- `input_ids` determines prompt length, `attention_mask` is preserved, generation receives `**inputs`, and only the generated continuation is decoded.
- Prompt, generated, and requested token counts are retained as safe diagnostics telemetry.

## Local runtime fix

- The local runtime now uses the same structured generation contract and exposes measured token telemetry.
- Worker stages are canonical uppercase values and no elapsed-time percentage is invented.

## Notebook generator fix

- Every newly generated notebook contains the corrected tokenization/generation path, portable artifact naming, canonical stages, failure history, and explicit retry guidance.
- No user edit to a generated worker cell is required for these fixes.

## Artifact filename and controller validation

- Portable leaf filenames are validated before filesystem access.
- Path separators, traversal, drive paths, invalid Windows characters, trailing dots/spaces, and reserved Windows device names are rejected or sanitized as appropriate.
- Worker-produced invalid filenames become structured controller-receive failures instead of raw OS errors or stuck jobs.
- Artifact writes have storage-specific retries that reuse the same generated result.

## Retry behavior

- Controller storage retry never re-runs model generation.
- Tokenizer errors do not retry; CUDA OOM recommends reducing context/model; malformed model JSON regenerates with evidence; network interruption reassigns; exhausted controller storage does not regenerate; Git conflicts replan from current head.
- Failures persist stage, classification, error type, retry strategy, attempt, logs, diagnostics, and failure timestamp separately.

## Timestamp fixes

- Database timestamps are normalized to timezone-aware UTC, including legacy SQLite values restored after restart.
- APIs emit absolute UTC timestamps. The browser derives local exact and relative values only from those timestamps.
- Future clock skew is bounded with explicit language instead of negative relative time.

## Diagnostics UI

- Persisted activity events are loaded on refresh and merged with the live stream.
- Errors and warnings have high-visibility cards, exact timestamps, stage timelines, traceback views, and copy controls for error, traceback, and sanitized diagnostics.
- Secret-bearing keys, bearer credentials, and signed query values are recursively redacted before persistence/copying; safe token-count telemetry remains visible.
- Worker progress is shown only when a worker reports real progress; otherwise the current stage is shown.

## Project management

- Projects can be searched, filtered, pinned/unpinned, moved within pinned/unpinned groups, and permanently deleted with confirmation.
- Pin and order are persisted and survive controller restart.
- Project deletion removes project-scoped database records, repository, and artifacts while preserving global workers/models.
- Deletion is blocked with HTTP 409 while a project is active.

## Database migration

- Schema ledger version 5 adds persistent project pin/order fields plus structured job and event failure fields.
- Existing projects receive deterministic default ordering and remain loadable.

## Tests added and passing

- Backend: `82 passed` across the full suite.
- Focused reliability/project diagnostics suite: `40 passed`.
- Frontend time tests: `4 passed`.
- Ruff: passed.
- TypeScript typecheck: passed.
- ESLint: passed.
- Next.js production build: passed.

Coverage includes structured `BatchEncoding`, portable filenames, storage-only retries, the complete failure classification matrix, UTC/restart behavior, heartbeat/failure/completion timestamps, secret redaction, pin/order persistence, deletion cleanup, active deletion blocking, and preservation of global workers.

## Manual tests performed

- Restarted the real local controller against the existing database and verified successful migration/startup.
- Verified UI and controller return HTTP 200 on `127.0.0.1:3000` and `127.0.0.1:8000`.
- Verified six existing projects load with pin/order fields and all project creation timestamps are emitted in UTC.
- Verified pin, move, and delete operations are present in the live OpenAPI schema.
- Verified the Next.js development origin used by the in-app browser is allowed.

## Remaining known issue / external acceptance

- An actual Google Colab GPU run was not launched from this machine. The generated notebook source and mocked Transformers pipeline are verified automatically, but final external acceptance still requires launching a freshly generated notebook in Colab and completing receive, tokenize, generate, decode, parse, validate, upload, controller storage, and Git integration with a real Qwen worker.