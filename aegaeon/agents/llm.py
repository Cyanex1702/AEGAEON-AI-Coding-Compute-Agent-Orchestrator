from __future__ import annotations

import json

from aegaeon.models.provider import ModelProvider
from aegaeon.models.results import FinalReviewReport, GeneratedChangeSet, ReviewReport
from aegaeon.protocol.schemas import PlanTask, ProjectPlan

PLANNER_INSTRUCTIONS = """You are AEGAEON's planner agent. Convert the software brief into a
small, dependency-correct implementation DAG. Return only the requested structured result.

Rules:
- Include a first task with id `plan`, no dependencies, and suggested_agent `planner`.
- Add focused coder or integrator tasks with lowercase kebab-case ids.
- Add exactly one `verification` task after every implementation and test-authoring task.
- Add exactly one final `review` task that depends only on `verification`.
- Every non-plan task must have at least one dependency.
- Keep tasks focused; do not create placeholder tasks or duplicate work.
- required_capabilities must use concrete runtime names such as python, node, or git.
- expected_outputs should use source_file, git_patch, test_report, or json.
"""

CODER_INSTRUCTIONS = """You are AEGAEON's coder agent. Implement exactly one focused task.
Return complete UTF-8 contents for every file you create or change, not a prose diff.

Rules:
- Preserve unrelated repository files and existing architecture.
- Never return .git paths, secrets, .env files, binaries, dependency caches, or build output.
- Use the supplied repository context as the source of truth.
- Produce runnable code rather than placeholders or TODO-only files.
- Add or update tests when this task changes behavior.
- Prefer simple, maintained dependencies and strong typing.
"""

REVIEWER_INSTRUCTIONS = """You are AEGAEON's reviewer agent. Diagnose the supplied test or
build failure from concrete logs and repository context. Return a precise structured review,
including the root cause and the smallest safe repair. Do not invent failures absent from logs.
"""

FINAL_REVIEW_INSTRUCTIONS = """You are AEGAEON's final reviewer. Review the repository tree,
task history, and passing verification evidence. Report only material correctness, security,
integration, or maintainability findings. Approve when no blocking issue remains.
"""


class LLMAgentRuntime:
    """Logical planner/coder/reviewer agents powered by one provider instance."""

    def __init__(self, provider: ModelProvider, output_retries: int = 2) -> None:
        self.provider = provider
        self.output_retries = output_retries

    @property
    def name(self) -> str:
        return f"model:{self.provider.model_id}"

    async def plan(self, prompt: str, repository_context: str) -> ProjectPlan:
        return await self.provider.complete_structured(
            [
                {"role": "developer", "content": PLANNER_INSTRUCTIONS},
                {
                    "role": "user",
                    "content": (
                        f"SOFTWARE BRIEF\n{prompt}\n\nCURRENT REPOSITORY\n{repository_context}"
                    ),
                },
            ],
            ProjectPlan,
            schema_name="aegaeon_project_plan",
            temperature=0.1,
            retries=self.output_retries,
        )

    async def generate(
        self,
        prompt: str,
        task: PlanTask,
        repository_context: str,
    ) -> GeneratedChangeSet:
        task_json = json.dumps(task.model_dump(mode="json"), indent=2)
        return await self.provider.complete_structured(
            [
                {"role": "developer", "content": CODER_INSTRUCTIONS},
                {
                    "role": "user",
                    "content": (
                        f"ORIGINAL BRIEF\n{prompt}\n\n"
                        f"FOCUSED TASK\n{task_json}\n\n"
                        f"REPOSITORY CONTEXT\n{repository_context}"
                    ),
                },
            ],
            GeneratedChangeSet,
            schema_name="aegaeon_change_set",
            temperature=0.1,
            retries=self.output_retries,
        )

    async def review_failure(
        self,
        prompt: str,
        execution: dict[str, object],
        repository_context: str,
    ) -> ReviewReport:
        return await self.provider.complete_structured(
            [
                {"role": "developer", "content": REVIEWER_INSTRUCTIONS},
                {
                    "role": "user",
                    "content": (
                        f"ORIGINAL BRIEF\n{prompt}\n\n"
                        f"FAILURE EVIDENCE\n{json.dumps(execution, indent=2)}\n\n"
                        f"REPOSITORY CONTEXT\n{repository_context}"
                    ),
                },
            ],
            ReviewReport,
            schema_name="aegaeon_failure_review",
            temperature=0.0,
            retries=self.output_retries,
        )

    async def repair(
        self,
        prompt: str,
        review: ReviewReport,
        repository_context: str,
    ) -> GeneratedChangeSet:
        repair_task = PlanTask(
            id="repair",
            title="Repair verification failure",
            description=(
                f"{review.summary}\nRoot cause: {review.root_cause}\n"
                + "\n".join(review.recommended_changes)
            ),
            dependencies=["verification"],
            required_capabilities=[],
            suggested_agent="coder",
            parallelizable=False,
            expected_outputs=["source_file", "git_patch"],
        )
        return await self.generate(prompt, repair_task, repository_context)

    async def final_review(
        self,
        prompt: str,
        repository_context: str,
        task_summary: list[dict[str, object]],
    ) -> FinalReviewReport:
        return await self.provider.complete_structured(
            [
                {"role": "developer", "content": FINAL_REVIEW_INSTRUCTIONS},
                {
                    "role": "user",
                    "content": (
                        f"ORIGINAL BRIEF\n{prompt}\n\n"
                        f"TASK HISTORY\n{json.dumps(task_summary, indent=2)}\n\n"
                        f"REPOSITORY CONTEXT\n{repository_context}"
                    ),
                },
            ],
            FinalReviewReport,
            schema_name="aegaeon_final_review",
            temperature=0.0,
            retries=self.output_retries,
        )
