from __future__ import annotations

from aegaeon.protocol.schemas import PlanTask, ProjectPlan


class BrokeBoyPlanner:
    """Creates a stable worker-oriented DAG without calling a paid model API."""

    def plan(self, prompt: str) -> ProjectPlan:
        summary = prompt.strip().splitlines()[0][:240]
        return ProjectPlan(
            project_summary=summary,
            tasks=[
                PlanTask(
                    id="plan",
                    title="Shared architecture and contracts",
                    description=(
                        "Freeze the project brief, repository truth, interface contracts, "
                        "file scopes, and acceptance criteria for every worker."
                    ),
                    suggested_agent="planner",
                    expected_outputs=["json"],
                ),
                PlanTask(
                    id="implementation",
                    title="Application implementation",
                    description=(
                        "Implement the requested application against the shared project brief. "
                        "Return complete files and preserve existing repository interfaces."
                    ),
                    dependencies=["plan"],
                    required_capabilities=["model.generate"],
                    suggested_agent="coder",
                    expected_outputs=["source_file"],
                ),
                PlanTask(
                    id="tests",
                    title="Automated tests",
                    description=(
                        "Add repository-native tests for the stated acceptance criteria and the "
                        "interfaces created by the implementation task."
                    ),
                    dependencies=["implementation"],
                    required_capabilities=["model.generate"],
                    suggested_agent="coder",
                    expected_outputs=["source_file"],
                ),
                PlanTask(
                    id="verification",
                    title="Objective verification",
                    description="Run detected tests, builds, lint, and type checks.",
                    dependencies=["tests"],
                    required_capabilities=["python"],
                    suggested_agent="integrator",
                    expected_outputs=["test_report"],
                ),
                PlanTask(
                    id="review",
                    title="Evidence review",
                    description="Review Git changes and objective verification evidence.",
                    dependencies=["verification"],
                    suggested_agent="reviewer",
                    expected_outputs=["json"],
                ),
            ],
        )
