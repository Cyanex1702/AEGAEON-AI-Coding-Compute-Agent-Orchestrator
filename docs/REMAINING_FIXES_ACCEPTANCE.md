# AEGAEON Remaining-Fixes Acceptance Report

Date: 2026-08-24

## Fixed

- Capacity-aware scheduling distinguishes `WORKER_AVAILABLE`, `COMPATIBLE_WORKER_BUSY`, `WORKER_OFFLINE`, and `NO_COMPATIBLE_WORKER_EXISTS`.
- Compatible busy workers cause `WAITING_FOR_WORKER` without consuming an attempt. Capacity changes emit `task.waiting_for_worker`, `task.worker_available`, and `task.dispatched`.
- One-worker Broke Boy projects use the same granular DAG as multi-worker projects. Worker count controls concurrency only.
- Failure evidence uses explicit codes: `INVALID_JSON`, `OUTPUT_TRUNCATED`, `OUTPUT_TOO_LARGE`, `UNSAFE_PATH`, `CUDA_OOM`, `TOKENIZER_ERROR`, `MODEL_RUNTIME_ERROR`, `WORKER_DISCONNECTED`, `WORKER_TIMEOUT`, `DEPENDENCY_FAILURE`, `TEST_FAILURE`, `BUILD_FAILURE`, `TYPECHECK_FAILURE`, `NO_COMPATIBLE_WORKER`, and `WORKER_BUSY`.
- Truncation/oversize/OOM retries reduce context and output limits. Unsafe paths, tokenizer errors, and no-compatible-worker failures stop instead of retrying blindly.
- Verification runs in a fresh disposable source copy with a clean, secret-free environment, bounded output, timeouts, and process-tree cleanup.
- npm, pnpm, yarn, requirements.txt, and installable pyproject dependency bootstrap is detected. npm uses lockfile-clean install and disables lifecycle scripts.
- Verification artifacts contain named dependency/test/typecheck/lint/build checks, status, exit evidence, sandbox metadata, and failure codes.
- Worker WebSocket credentials are header-only. `/ws/worker?token=...` is rejected. The shared development token is disabled by default and requires explicit local opt-in.
- Static-token worker disconnect/revocation no longer requires a pairing row.
- Pairing throttles are database-backed and shared by controller processes using the same database.
- A configured control bearer token protects sensitive routes even on loopback; remote non-public control routes always require it.
- UI and worker WebSocket origin boundaries have allow/deny tests.
- Source bundles support safe create, update, delete, and rename operations with repository containment, collision checks, concurrent-change checks, Git commits, and patches.
- `intelligence` now scales generation token/file limits. The misleading project-level research selector was removed; manual research remains an explicit prompt/import API workflow.
- ngrok launches with a unique owned tunnel name and accepts only the HTTPS tunnel matching that name and controller target. Multiple-tunnel selection is tested.
- Source releases are deterministic and exclude node_modules, virtualenvs, runtime data, caches, databases, secrets, and generated builds.
- Frontend now has reproducible `lint`, `typecheck`, `build`, and `verify` scripts and a lockfile verified with `npm ci`.
- Database migration ledger version 3 records the security schema, including persistent rate-limit buckets.
- UI shows human-readable Queued, Waiting for worker, Assigned, Running, and Retry states plus active run ID and historical run count.

## Partially fixed / explicit limitations

- The production sandbox interface currently selects a hardened local fallback. It uses a disposable copy, clean environment, bounded output/time, and process-tree termination, but it does not yet enforce container-level non-root identity, kernel memory/CPU/PID quotas, or a network namespace. Reports state this limitation instead of claiming full isolation.
- Dependency installs in the hardened local fallback have a clean environment, and npm lifecycle scripts are disabled, but OS-level network allowlisting is not available without a container backend.
- Control-plane bearer authentication is enforced when configured. A browser login/session UI for deployments that set `AEGAEON_CONTROL_TOKEN` is not included; the default local-only UI continues to rely on loopback trust when no token is configured.
- Migrations are versioned, idempotent, and recorded in `schema_migrations`; this is an Alembic-equivalent local migration ledger, not an Alembic package deployment.

## Still open / requires real external infrastructure

- A fresh real Qwen inference was not run in this repair session. Do not mark Qwen fixed until a newly generated notebook produces a fresh `job.completed` event and the returned changes pass verification.
- Container/VM enforcement remains the final security-hardening milestone if untrusted generated repositories will be verified on a shared or production controller.

## Automated evidence

Run from the repository root:

```powershell
.\.venv\Scripts\python.exe -m ruff check aegaeon worker tests scripts
.\.venv\Scripts\python.exe -m compileall -q aegaeon worker scripts
npm.cmd --prefix apps/web run lint
npm.cmd --prefix apps/web run typecheck
npm.cmd --prefix apps/web run build
```

Backend suite is split into bounded groups on Windows; the groups cover all 54 collected tests:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_api.py tests/test_broke_boy.py tests/test_concurrent_integration.py tests/test_dag.py tests/test_end_to_end.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_job_leases.py tests/test_model_runtime.py tests/test_multi_worker_end_to_end.py tests/test_project_and_artifacts.py tests/test_provider.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_remote_connectivity.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_scheduler.py tests/test_source_materialization.py tests/test_strategy.py tests/test_worker_executor.py tests/test_remaining_fixes.py -q
```

## Manual acceptance: real Qwen / Broke Boy

1. Start the controller and frontend. Confirm `/health` returns `status: ok`.
2. Create a Broke Boy project with Standard local-evidence model selection.
3. Set up and verify HTTPS and worker WebSocket connectivity.
4. Generate a fresh notebook. Do not reuse an older notebook after a tunnel URL change.
5. In a fresh Colab GPU runtime, run all cells and enter the one-time pairing code when prompted.
6. Confirm the worker is `online`, its exact model is loaded/ready, and its credential was supplied via `X-AEGAEON-Worker-Token` (never a URL query parameter).
7. Start the project. With one worker, confirm one parallel-ready task runs and the other shows `Waiting for worker` without increasing its attempt count.
8. Wait for a fresh `job.completed` from the Qwen worker. Record job ID, run ID, attempt ID, model, runtime, and generated artifact checksum.
9. Confirm all granular tasks complete, verification dependencies install in the disposable environment, and every named test/typecheck/lint/build check passes.
10. Export the project and confirm no node_modules, runtime database, token, cache, `.env`, or tunnel secret is present.
11. Revoke the worker. Confirm its live socket closes and the credential cannot reconnect.

A Qwen failure is accepted as fixed only after steps 8 and 9 succeed on a fresh run.
