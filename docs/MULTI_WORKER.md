# Multiple-worker execution

Phase 10 adds durable, concurrent worker execution while keeping the controller's project repository authoritative.

## Runtime behavior

- Every generation attempt is persisted as a job before scheduling.
- Worker reservation is atomic, so the same online worker cannot receive two simultaneous jobs.
- All ready, independent, parallelizable DAG nodes are dispatched together. Verification and review wait for every generation ancestor.
- Heartbeats renew active leases. A disconnected worker interrupts its current job and makes the task eligible for bounded retry on another compatible worker.
- On controller restart, assigned or running jobs are marked interrupted and their active tasks are recovered as retryable work from the persisted plan.
- Each worker returns a source bundle based on a recorded Git revision. Non-overlapping changes rebase cleanly; overlapping edits use a three-way merge. Unresolved conflicts leave the canonical repository unchanged and enter the normal bounded retry path.

## Configuration

```dotenv
AEGAEON_HEARTBEAT_TIMEOUT_SECONDS=20
AEGAEON_JOB_LEASE_SECONDS=30
AEGAEON_JOB_ASSIGNMENT_TIMEOUT_SECONDS=180
AEGAEON_MAX_RETRIES=3
```

Use a lease longer than the expected heartbeat interval. The assignment timeout bounds how long the controller waits for a worker result before interrupting and retrying the job.

## Observability

Use `GET /projects/{project_id}/jobs` for a project's complete attempt history or `GET /jobs` for recent jobs across projects. The project dashboard shows the same worker, attempt, status, timing, and error data under **Dispatch history** for each task.

The activity stream also records parallel dispatch, reassignment, lease interruption, integration conflicts, and task requeue events.
