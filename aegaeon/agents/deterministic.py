from __future__ import annotations

from aegaeon.protocol.schemas import PlanTask, ProjectPlan


class BrokeBoyPlanner:
    """Creates a complexity-scaled, contract-aware DAG without a paid API."""

    def plan(self, prompt: str, worker_count: int = 1) -> ProjectPlan:
        summary = prompt.strip().splitlines()[0][:240]
        return ProjectPlan(
            project_summary=summary,
            tasks=[
                PlanTask(
                    id="plan",
                    title="Lead analysis, blueprint, and contracts",
                    description=(
                        "Interpret requirements, freeze the blueprint, project contract, "
                        "milestones, ownership, and acceptance criteria."
                    ),
                    suggested_agent="planner",
                    expected_outputs=["json"],
                    acceptance_criteria=["Blueprint and contract v1 are persisted"],
                ),
                PlanTask(
                    id="foundation",
                    title="Canonical repository foundation",
                    description=(
                        "Create the project skeleton, dependency manifests, shared types, "
                        "and interface foundations defined by the contract."
                    ),
                    dependencies=["plan"],
                    required_capabilities=["model.generate"],
                    suggested_agent="coder",
                    expected_outputs=["source_file"],
                    allowed_files=["**/*"],
                    acceptance_criteria=[
                        "Project installs",
                        "Shared contracts have one canonical definition",
                    ],
                ),
                PlanTask(
                    id="backend",
                    title="Backend and domain implementation",
                    description=(
                        "Implement server, domain, persistence, validation, and API "
                        "behavior without silently changing shared contracts."
                    ),
                    dependencies=["foundation"],
                    required_capabilities=["model.generate"],
                    suggested_agent="backend",
                    parallelizable=True,
                    expected_outputs=["source_file"],
                    allowed_files=[
                        "backend/**",
                        "generated/**",
                        "server/**",
                        "api/**",
                        "app/**",
                        "src/**",
                        "tests/**",
                        "pyproject.toml",
                        "requirements.txt",
                    ],
                    acceptance_criteria=["Backend behavior follows the canonical contract"],
                ),
                PlanTask(
                    id="frontend",
                    title="Frontend experience implementation",
                    description=(
                        "Implement responsive user flows and client integration using "
                        "the canonical API and shared names."
                    ),
                    dependencies=["foundation"],
                    required_capabilities=["model.generate"],
                    suggested_agent="frontend",
                    parallelizable=True,
                    expected_outputs=["source_file"],
                    allowed_files=[
                        "frontend/**",
                        "generated/**",
                        "web/**",
                        "client/**",
                        "src/**",
                        "apps/**",
                        "tests/**",
                        "package.json",
                    ],
                    acceptance_criteria=["Frontend uses canonical contract fields and routes"],
                ),
                PlanTask(
                    id="integration",
                    title="Contract-aware application integration",
                    description=(
                        "Integrate backend and frontend results, repair interface drift, "
                        "and complete cross-cutting behavior."
                    ),
                    dependencies=["backend", "frontend"],
                    required_capabilities=["model.generate"],
                    suggested_agent="integrator",
                    expected_outputs=["source_file", "git_patch"],
                    allowed_files=["**/*"],
                    acceptance_criteria=[
                        "Cross-component flows work",
                        "No unresolved contract drift",
                    ],
                ),
                PlanTask(
                    id="tests",
                    title="Acceptance and integration tests",
                    description=(
                        "Add repository-native tests for requirements, integration paths, "
                        "and important failures."
                    ),
                    dependencies=["integration"],
                    required_capabilities=["model.generate"],
                    suggested_agent="tester",
                    expected_outputs=["source_file"],
                    allowed_files=[
                        "tests/**",
                        "test/**",
                        "generated/**",
                        "**/test_*.py",
                        "**/*.test.*",
                        "**/*.spec.*",
                        "pyproject.toml",
                        "package.json",
                    ],
                    acceptance_criteria=["Original requirements have executable coverage"],
                ),
                PlanTask(
                    id="verification",
                    title="Objective milestone and system verification",
                    description=(
                        "Run detected dependency checks, tests, builds, lint, and type "
                        "checks in the disposable sandbox."
                    ),
                    dependencies=["tests"],
                    required_capabilities=["python"],
                    suggested_agent="integrator",
                    expected_outputs=["test_report"],
                    acceptance_criteria=["Every detected deterministic check passes"],
                ),
                PlanTask(
                    id="review",
                    title="Lead final acceptance review",
                    description=(
                        "Review canonical Git state, milestone gates, contract "
                        "consistency, and requirement coverage."
                    ),
                    dependencies=["verification"],
                    suggested_agent="reviewer",
                    expected_outputs=["json"],
                    acceptance_criteria=["All milestones and requirements are verified"],
                ),
            ],
        )

    @staticmethod
    def _small_plan(summary: str) -> ProjectPlan:
        return ProjectPlan(
            project_summary=summary,
            tasks=[
                PlanTask(
                    id="plan",
                    title="Minimal blueprint and contract",
                    description=(
                        "Freeze the brief, minimal architecture, contract, and acceptance criteria."
                    ),
                    suggested_agent="planner",
                    expected_outputs=["json"],
                ),
                PlanTask(
                    id="implementation",
                    title="Bounded application implementation",
                    description=(
                        "Implement the complete small application against the "
                        "canonical brief and contract."
                    ),
                    dependencies=["plan"],
                    required_capabilities=["model.generate"],
                    suggested_agent="coder",
                    expected_outputs=["source_file"],
                    allowed_files=["**/*"],
                ),
                PlanTask(
                    id="tests",
                    title="Acceptance tests",
                    description="Add focused repository-native tests for the requested behavior.",
                    dependencies=["implementation"],
                    required_capabilities=["model.generate"],
                    suggested_agent="tester",
                    expected_outputs=["source_file"],
                    allowed_files=[
                        "tests/**",
                        "test/**",
                        "generated/**",
                        "**/test_*.py",
                        "**/*.test.*",
                        "**/*.spec.*",
                        "pyproject.toml",
                        "package.json",
                    ],
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
                    title="Final requirement review",
                    description=(
                        "Confirm the implementation, contract, evidence, and original "
                        "request agree."
                    ),
                    dependencies=["verification"],
                    suggested_agent="reviewer",
                    expected_outputs=["json"],
                ),
            ],
        )
