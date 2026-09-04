from __future__ import annotations

import re
from datetime import UTC, datetime
from fnmatch import fnmatch
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from aegaeon.database.models import (
    ArchitectureDecisionRecord,
    ComputePlanRecord,
    ContractProposalRecord,
    ContractRevisionRecord,
    LeadDecisionRecord,
    MilestoneRecord,
    ProjectAnalysisRecord,
    ProjectBlueprintRecord,
    ProjectContractRecord,
    ProjectRecord,
    ProjectRequirementRecord,
    RepairRecord,
    TaskRecord,
    VerificationRecord,
)
from aegaeon.database.session import Database
from aegaeon.failures import ClassifiedFailure, FailureCode
from aegaeon.models.results import ChangeOperation, ContractProposal
from aegaeon.orchestration.contracts import ContractApplicationError, ProjectContractMaterializer
from aegaeon.protocol.schemas import PlanTask, ProjectPlan


class LeadEngineerProvider:
    """Replaceable Lead judgment boundary; accepted outputs are persisted by Core."""

    name: str

    def analyze_project(self, prompt: str) -> dict[str, Any]:
        raise NotImplementedError

    def create_blueprint(self, prompt: str, analysis: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def create_milestones(self, complexity: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    def review_contract_proposal(self, proposal: ContractProposal) -> tuple[bool, str]:
        raise NotImplementedError

    def analyze_failure(self, evidence: dict[str, Any]) -> tuple[str, str]:
        raise NotImplementedError


class DeterministicLeadEngineer:
    """Zero-cost Lead judgment provider; Core remains the source of truth."""

    name = "deterministic-lead-v1"
    signals = {
        "calendar",
        "recurring",
        "alarm",
        "notification",
        "authentication",
        "payment",
        "realtime",
        "multi-user",
        "offline",
        "sync",
        "mobile",
        "deployment",
        "security",
        "dashboard",
        "integration",
        "analytics",
        "roles",
        "permissions",
        "themes",
    }

    def analyze_project(self, prompt: str) -> dict[str, Any]:
        lower = prompt.lower()
        requirements = self.requirements(prompt)
        found = sorted(item for item in self.signals if item in lower)
        score = len(found) + max(0, len(requirements) - 3) + (2 if len(prompt) > 800 else 0)
        complexity = "LOW" if score <= 2 else "MEDIUM" if score <= 6 else "HIGH"
        platforms = (
            ["web"]
            if any(k in lower for k in ("web", "ui", "frontend", "calendar"))
            else ["local application"]
        )
        if "mobile" in lower or "responsive" in lower:
            platforms.append("mobile-responsive")
        return {
            "complexity": complexity,
            "required_features": requirements,
            "optional_features": [],
            "platform_targets": platforms,
            "data_requirements": self.match(
                requirements, ("data", "persist", "database", "store", "offline")
            ),
            "security_requirements": self.match(
                requirements, ("auth", "security", "permission", "secret", "payment")
            ),
            "external_apis": [
                name for name in ("Stripe", "Google", "GitHub", "OpenAI") if name.lower() in lower
            ],
            "dependencies": [],
            "deployment_expectations": [
                "deployable release" if "deploy" in lower else "local-first release"
            ],
            "unknowns": ["External service credentials and deployment target remain user-owned"]
            if any(name.lower() in lower for name in ("Stripe", "Google", "GitHub", "OpenAI"))
            else [],
        }

    def create_blueprint(self, prompt: str, analysis: dict[str, Any]) -> dict[str, Any]:
        lower = prompt.lower()
        is_calculator = any(word in lower for word in ("calculator", "advanced math", "scientific"))
        frontend = (
            "React"
            if any(k in lower for k in ("ui", "web", "frontend", "calendar", "theme"))
            else "None"
        )
        backend = (
            "FastAPI"
            if any(
                k in lower for k in ("api", "backend", "server", "persist", "database", "scheduler")
            )
            else "Application core"
        )
        database = (
            "SQLite"
            if any(
                k in lower for k in ("persist", "database", "store", "offline", "event", "schedule")
            )
            else "None"
        )
        entities = [
            name
            for key, name in (
                ("user", "User"),
                ("schedule", "Schedule"),
                ("event", "Event"),
                ("reminder", "Reminder"),
                ("task", "Task"),
                ("project", "Project"),
            )
            if key in lower
        ]
        domain = self.domain_name(prompt)
        explicit_actions = self.action_names(prompt)
        if is_calculator and not explicit_actions:
            entities = ["Calculation", "HistoryEntry"]
            actions = [
                "evaluateExpression",
                "calculateScientificFunction",
                "manageMemory",
                "manageHistory",
                "convertAngleMode",
            ]
        else:
            actions = explicit_actions or ["execute"]
        service_names = (
            ["CalculatorService"]
            if is_calculator
            else [f"{name}Service" for name in entities] or [f"{domain}Service"]
        )
        services = [{"name": name, "methods": actions} for name in service_names]
        if is_calculator and frontend != "None":
            ui_components = [
                "CalculatorShell",
                "CalculatorDisplay",
                "CalculatorKeypad",
                "ScientificPanel",
                "HistoryPanel",
            ]
        else:
            ui_components = (
                [f"{domain}Shell", f"{domain}View", f"{domain}Controls"]
                if frontend != "None"
                else []
            )
        if is_calculator:
            state = {
                "expression": "string",
                "result": "number|string|null",
                "memory": "number",
                "history": "HistoryEntry[]",
                "angleMode": "degrees|radians",
            }
        else:
            state = {self.field_name(name): "string|null" for name in entities} or {
                "status": "string",
                "result": "unknown",
            }
        return {
            "architecture": {"frontend": frontend, "backend": backend, "database": database},
            "entities": [{"name": name, "identifier": "id"} for name in entities],
            "project": domain,
            "services": services,
            "ui": {"components": ui_components},
            "state": state,
            "constraints": [
                "One canonical Git repository",
                "Contract changes require approval",
                "Repository-relative paths only",
            ],
            "risks": analysis["unknowns"],
        }

    def create_milestones(self, complexity: str) -> list[dict[str, Any]]:
        if complexity == "LOW":
            specs = [
                ("implementation", "Implementation", "Deliver the bounded application"),
                (
                    "verification-release",
                    "Verification & Release",
                    "Verify requirements and release",
                ),
            ]
        elif complexity == "MEDIUM":
            specs = [
                (
                    "architecture-foundation",
                    "Architecture & Foundation",
                    "Freeze architecture and foundation",
                ),
                ("core-implementation", "Core Implementation", "Implement primary domain behavior"),
                (
                    "experience-integration",
                    "Experience & Integration",
                    "Complete interfaces and integration",
                ),
                (
                    "verification-release",
                    "Verification & Release",
                    "Run objective gates and release",
                ),
            ]
        else:
            specs = [
                (
                    "architecture-foundation",
                    "Architecture & Foundation",
                    "Research, blueprint, contracts, and foundation",
                ),
                ("backend-domain", "Backend & Domain", "Implement domain services and APIs"),
                (
                    "persistence-integration",
                    "Persistence & Data",
                    "Implement durable data behavior",
                ),
                (
                    "frontend-experience",
                    "Frontend & Experience",
                    "Implement responsive user workflows",
                ),
                (
                    "advanced-integration",
                    "Advanced Features & Integration",
                    "Complete cross-cutting integration",
                ),
                (
                    "verification-release",
                    "Verification & Release",
                    "Run final gates, repair, and release",
                ),
            ]
        return [
            {
                "key": key,
                "title": title,
                "goal": goal,
                "description": goal,
                "sequence": index,
                "dependencies": [] if index == 0 else [specs[index - 1][0]],
                "deliverables": [title],
                "acceptance_criteria": [
                    "Assigned tasks complete",
                    "Canonical contract remains valid",
                ]
                if key != "verification-release"
                else ["Build and tests pass", "Requirements verified"],
            }
            for index, (key, title, goal) in enumerate(specs)
        ]

    def recommend_compute(
        self, complexity: str, milestones: int, plan: ProjectPlan, strategy: str
    ) -> dict[str, Any]:
        parallel = sum(task.parallelizable for task in plan.tasks)
        base = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}[complexity]
        workers = max(
            1,
            min(
                6,
                base + {"cheapest": -2, "balanced": 0, "fast": 1, "maximum_quality": 1}[strategy],
                parallel + 1,
            ),
        )
        roles = ["Lead Engineer", "Implementation Engineer"]
        if workers >= 2:
            roles += ["Backend Engineer", "Frontend Engineer"]
        if workers >= 3:
            roles += ["Test / Integration Engineer", "Reviewer"]
        return {
            "complexity": complexity,
            "milestone_count": milestones,
            "estimated_task_count": len(plan.tasks),
            "parallelizable_task_count": parallel,
            "recommended_worker_count": workers,
            "recommended_roles": roles,
            "model_requirements": {"capability": "model.generate", "quality": strategy},
        }

    def review_contract_proposal(self, proposal: ContractProposal) -> tuple[bool, str]:
        if proposal.type in {"rename", "remove", "change_type", "other"}:
            return False, "Breaking or unclassified changes require explicit user review"
        return True, "Additive proposal approved by zero-cost Lead policy"

    def analyze_failure(self, evidence: dict[str, Any]) -> tuple[str, str]:
        root = str(evidence.get("error") or evidence.get("failure_code") or "Unknown failure")
        return (
            root,
            f"Repair the smallest affected scope, preserve the contract, and rerun gates: {root}",
        )

    @staticmethod
    def domain_name(prompt: str) -> str:
        cleaned = re.sub(
            r"^(?:(?:i\s+)?want\s+(?:you|u)\s+to\s+|"
            r"i\s+need\s+(?:you|u)\s+to\s+|please\s+)?"
            r"(?:build|create|make)\s+(?:me\s+)?(?:an?\s+)?",
            "",
            prompt.strip(),
            flags=re.I,
        )
        if re.search(r"\bcalculator\b", cleaned, flags=re.I):
            return "Calculator"
        words = re.findall(r"[A-Za-z][A-Za-z0-9]+", cleaned.splitlines()[0])
        ignored = {"advanced", "simple", "modern", "application", "app", "project", "with", "all"}
        meaningful = [word for word in words if word.lower() not in ignored]
        chosen = meaningful[0] if meaningful else "Application"
        return chosen[:1].upper() + chosen[1:]

    @staticmethod
    def action_names(prompt: str) -> list[str]:
        candidates = re.findall(
            r"\b(add|subtract|multiply|divide|create|update|delete|list|search|filter|"
            r"schedule|notify|authenticate|sync|export|import|calculate|track|manage)\w*\b",
            prompt.lower(),
        )
        return list(dict.fromkeys(candidates))[:20]

    @staticmethod
    def field_name(value: str) -> str:
        return value[:1].lower() + value[1:]

    @staticmethod
    def requirements(prompt: str) -> list[str]:
        lines = [re.sub(r"^[-*#\d.\s]+", "", line).strip() for line in prompt.splitlines()]
        clauses = [line for line in lines if len(line) >= 3]
        if len(clauses) <= 1:
            body = re.sub(
                r"^(?:(?:i\s+)?want\s+(?:you|u)\s+to\s+|"
                r"i\s+need\s+(?:you|u)\s+to\s+|please\s+)?"
                r"(?:build|create|make)\s+(?:me\s+)?",
                "",
                prompt.strip(),
                flags=re.I,
            )
            clauses = [
                item.strip(" .")
                for item in re.split(r",|;|\band\b", body)
                if len(item.strip()) >= 3
            ]
        return list(dict.fromkeys(clauses))[:40] or [prompt.strip()[:500]]

    @staticmethod
    def match(values: list[str], words: tuple[str, ...]) -> list[str]:
        return [value for value in values if any(word in value.lower() for word in words)]


class OrchestrationArchitectureService:
    """Durable milestones, contracts, Lead decisions, gates, and coverage."""

    def __init__(self, database: Database, projects: Any | None = None) -> None:
        self.database = database
        self.projects = projects
        self.lead: LeadEngineerProvider = DeterministicLeadEngineer()
        self.materializer = ProjectContractMaterializer()

    def prepare(
        self,
        project_id: str,
        prompt: str,
        plan: ProjectPlan,
        options: dict[str, Any],
        base_commit: str,
    ) -> ProjectPlan:
        with self.database.session() as session:
            if session.scalar(
                select(ProjectContractRecord).where(ProjectContractRecord.project_id == project_id)
            ):
                canonical = self.project_plan(project_id, prompt)
                return self._reconcile_plan(project_id, plan, canonical)
        analysis = self.lead.analyze_project(prompt)
        blueprint = self.lead.create_blueprint(prompt, analysis)
        milestones = self.lead.create_milestones(analysis["complexity"])
        strategy = str(options.get("compute_strategy", "balanced"))
        compute = self.lead.recommend_compute(
            analysis["complexity"], len(milestones), plan, strategy
        )
        selected_workers = int(options.get("worker_count", compute["recommended_worker_count"]))
        contract_content = {
            "project": blueprint["project"],
            "architecture": blueprint["architecture"],
            "entities": {item["name"]: {"id": "string"} for item in blueprint["entities"]},
            "services": {
                item["name"]: {"methods": item.get("methods", [])} for item in blueprint["services"]
            },
            "ui": blueprint["ui"],
            "state": blueprint["state"],
            "api": {},
            "shared_types": {},
            "naming": {"identifier": "id", "style": "framework-native"},
            "protected_paths": ["contracts/**", "schema/**", "shared/**", "openapi.*"],
        }
        milestone_ids = {item["key"]: f"milestone-{uuid4().hex[:12]}" for item in milestones}
        with self.database.session() as session:
            analysis_fields = (
                "complexity",
                "required_features",
                "optional_features",
                "platform_targets",
                "data_requirements",
                "security_requirements",
                "external_apis",
                "dependencies",
                "deployment_expectations",
                "unknowns",
            )
            session.add(
                ProjectAnalysisRecord(
                    id=f"analysis-{uuid4().hex[:12]}",
                    project_id=project_id,
                    **{key: analysis[key] for key in analysis_fields},
                )
            )
            session.add(
                ProjectBlueprintRecord(
                    id=f"blueprint-{uuid4().hex[:12]}", project_id=project_id, **blueprint
                )
            )
            session.add(
                ProjectContractRecord(
                    id=f"contract-{uuid4().hex[:12]}",
                    project_id=project_id,
                    version=1,
                    content=contract_content,
                )
            )
            session.add(
                ContractRevisionRecord(
                    id=f"contract-revision-{uuid4().hex[:12]}",
                    project_id=project_id,
                    version=1,
                    content=contract_content,
                    reason="Initial canonical project contract",
                )
            )
            for item in milestones:
                values = {
                    key: item[key]
                    for key in (
                        "key",
                        "title",
                        "goal",
                        "description",
                        "sequence",
                        "deliverables",
                        "acceptance_criteria",
                    )
                }
                session.add(
                    MilestoneRecord(
                        id=milestone_ids[item["key"]],
                        project_id=project_id,
                        dependencies=[milestone_ids[key] for key in item["dependencies"]],
                        base_commit=base_commit,
                        starting_contract_version=1,
                        **values,
                    )
                )
            usable = [
                item for item in milestones if item["key"] != "verification-release"
            ] or milestones
            for index, text in enumerate(analysis["required_features"]):
                milestone = usable[min(index, len(usable) - 1)]
                session.add(
                    ProjectRequirementRecord(
                        id=f"requirement-{uuid4().hex[:12]}",
                        project_id=project_id,
                        text=text,
                        category=self._requirement_category(text),
                        milestone_id=milestone_ids[milestone["key"]],
                    )
                )
            decisions = [
                (
                    "Canonical repository",
                    "AEGAEON owns main",
                    "Only Core integrates worker results",
                ),
                (
                    "Project contract",
                    "Versioned contract registry",
                    "Workers need one shared interface truth",
                ),
                ("Lead provider", self.lead.name, "Zero-paid-API operation remains available"),
            ]
            for sequence, (topic, decision, reason) in enumerate(decisions, 1):
                session.add(
                    ArchitectureDecisionRecord(
                        id=f"decision-{uuid4().hex[:12]}",
                        project_id=project_id,
                        sequence=sequence,
                        topic=topic,
                        decision=decision,
                        reason=reason,
                        contract_version=1,
                    )
                )
            session.add(
                ComputePlanRecord(
                    id=f"compute-{uuid4().hex[:12]}",
                    project_id=project_id,
                    strategy=strategy,
                    selected_worker_count=selected_workers,
                    **compute,
                )
            )
            session.add(
                LeadDecisionRecord(
                    id=f"lead-{uuid4().hex[:12]}",
                    project_id=project_id,
                    decision_type="project_plan",
                    summary=(
                        f"{analysis['complexity']} project: {len(milestones)} milestones, "
                        f"{len(plan.tasks)} tasks"
                    ),
                    rationale=(
                        f"Recommended {compute['recommended_worker_count']} reusable "
                        f"workers with {strategy} strategy"
                    ),
                    evidence=[
                        f"Parallelizable tasks: {compute['parallelizable_task_count']}",
                        "Contract version: 1",
                    ],
                    provider=self.lead.name,
                )
            )

        if self.projects is not None:
            self.materialize_contract(project_id, "Materialize canonical Contract v1")
        generated_plan = self.project_plan(project_id, prompt)
        generated_compute = self.lead.recommend_compute(
            analysis["complexity"], len(milestones), generated_plan, strategy
        )
        with self.database.session() as session:
            compute_record = session.scalar(
                select(ComputePlanRecord).where(ComputePlanRecord.project_id == project_id)
            )
            if compute_record is not None:
                compute_record.estimated_task_count = generated_compute["estimated_task_count"]
                compute_record.parallelizable_task_count = generated_compute[
                    "parallelizable_task_count"
                ]
                compute_record.recommended_worker_count = generated_compute[
                    "recommended_worker_count"
                ]
                compute_record.recommended_roles = generated_compute["recommended_roles"]
                compute_record.model_requirements = generated_compute["model_requirements"]
        return generated_plan

    def _reconcile_plan(
        self,
        project_id: str,
        source: ProjectPlan,
        canonical: ProjectPlan,
    ) -> ProjectPlan:
        """Preserve planner intelligence while filling uncovered canonical milestones."""
        milestones = self._milestones(project_id)
        if not milestones:
            return source
        tasks = list(source.tasks)
        used_ids = {task.id for task in tasks}
        coverage: dict[str, list[str]] = {milestone.key: [] for milestone in milestones}
        for task in tasks:
            key = task.milestone_key or self._infer_milestone_key(task.id, milestones)
            if task.id != "plan":
                coverage.setdefault(key, []).append(task.id)

        canonical_by_milestone: dict[str, list[PlanTask]] = {}
        for task in canonical.tasks:
            key = task.milestone_key or self._infer_milestone_key(task.id, milestones)
            canonical_by_milestone.setdefault(key, []).append(task)

        previous_ids: list[str] = ["plan"] if "plan" in used_ids else []
        for milestone in milestones:
            existing = coverage.get(milestone.key, [])
            if existing:
                if previous_ids:
                    for task_id in existing:
                        index = next(
                            position for position, item in enumerate(tasks) if item.id == task_id
                        )
                        task = tasks[index]
                        dependencies = (
                            task.dependencies
                            if task.id == "review"
                            else list(dict.fromkeys([*task.dependencies, *previous_ids]))
                        )
                        tasks[index] = task.model_copy(
                            update={
                                "dependencies": dependencies,
                                "milestone_key": milestone.key,
                            }
                        )
                previous_ids = list(existing)
                continue
            if milestone.key == "verification-release":
                continue
            candidates = [
                task
                for task in canonical_by_milestone.get(milestone.key, [])
                if task.id not in {"plan", "verification", "review"}
            ]
            if not candidates:
                continue
            candidate = candidates[0]
            task_id = candidate.id
            suffix = 2
            while task_id in used_ids:
                task_id = f"{candidate.id}-{suffix}"
                suffix += 1
            bridge = candidate.model_copy(
                update={
                    "id": task_id,
                    "dependencies": list(previous_ids),
                    "milestone_key": milestone.key,
                    "parallelizable": False,
                }
            )
            tasks.append(bridge)
            used_ids.add(task_id)
            coverage[milestone.key] = [task_id]
            previous_ids = [task_id]
        ordered: list[PlanTask] = []
        remaining = list(tasks)
        completed: set[str] = set()
        while remaining:
            ready = [task for task in remaining if set(task.dependencies).issubset(completed)]
            if not ready:
                break
            for task in ready:
                ordered.append(task)
                completed.add(task.id)
                remaining.remove(task)
        if remaining:
            ordered.extend(remaining)
        return ProjectPlan(project_summary=source.project_summary, tasks=ordered)

    def project_plan(self, project_id: str, prompt: str) -> ProjectPlan:
        """Decompose persisted milestones into the executable task DAG."""
        milestones = self._milestones(project_id)
        if not milestones:
            raise RuntimeError("project milestones are not prepared")
        analysis = self.summary(project_id)["analysis"] or {"complexity": "LOW"}
        complexity = str(analysis["complexity"])
        first = milestones[0]
        release = milestones[-1]
        tasks: list[PlanTask] = [
            PlanTask(
                id="plan",
                title="Lead analysis and canonical architecture",
                description=(
                    "Use the persisted Lead analysis, blueprint, canonical Contract v1, "
                    "and milestone plan as the source of truth."
                ),
                suggested_agent="planner",
                milestone_key=first.key,
                acceptance_criteria=["Blueprint, Contract v1, and milestones are persisted"],
            )
        ]
        previous: list[str] = ["plan"]
        implementation_milestones = milestones[:-1]
        for index, milestone in enumerate(implementation_milestones):
            if index == 0:
                specs = [
                    (
                        "foundation",
                        "Canonical application foundation",
                        "Build the project scaffold around Core-owned canonical contracts. "
                        "Do not create or edit contract files.",
                        "coder",
                        False,
                    )
                ]
                if complexity == "LOW":
                    specs.append(
                        (
                            "implementation",
                            "Bounded project implementation",
                            "Implement the complete small application against Contract v1.",
                            "coder",
                            False,
                        )
                    )
            else:
                base_key = re.sub(r"[^a-z0-9]+", "-", milestone.key.lower()).strip("-")
                role = (
                    "backend"
                    if any(word in milestone.key for word in ("backend", "domain", "persistence"))
                    else "frontend"
                    if any(word in milestone.key for word in ("frontend", "experience"))
                    else "integrator"
                )
                specs = [
                    (
                        base_key,
                        milestone.title,
                        f"Deliver {milestone.goal.lower()} against the canonical contract.",
                        role,
                        False,
                    )
                ]
                if complexity == "HIGH" and any(
                    word in milestone.key for word in ("frontend", "advanced", "backend")
                ):
                    specs.append(
                        (
                            f"{base_key}-companion",
                            f"{milestone.title} companion scope",
                            "Implement an independent bounded portion of this milestone "
                            "for parallel integration.",
                            role,
                            True,
                        )
                    )
            current_ids: list[str] = []
            for position, (key, title, description, role, parallelizable) in enumerate(specs):
                dependencies = list(previous)
                if complexity == "LOW" and index == 0 and position > 0:
                    dependencies = [current_ids[-1]]
                tasks.append(
                    PlanTask(
                        id=key,
                        title=title,
                        description=description,
                        dependencies=dependencies,
                        required_capabilities=["model.generate"],
                        suggested_agent=role,  # type: ignore[arg-type]
                        milestone_key=milestone.key,
                        parallelizable=parallelizable or len(specs) > 1,
                        allowed_files=["**/*"],
                        acceptance_criteria=list(milestone.acceptance_criteria),
                    )
                )
                current_ids.append(key)
            previous = current_ids
        tests_key = "tests"
        tasks.append(
            PlanTask(
                id=tests_key,
                title="Acceptance and integration tests",
                description="Add focused executable coverage for the accepted requirements.",
                dependencies=previous,
                required_capabilities=["model.generate"],
                suggested_agent="tester",
                milestone_key=(
                    implementation_milestones[-1].key if implementation_milestones else first.key
                ),
                allowed_files=[
                    "tests/**",
                    "generated/**",
                    "test/**",
                    "**/test_*.py",
                    "**/*.test.*",
                    "**/*.spec.*",
                    "pyproject.toml",
                    "package.json",
                ],
                acceptance_criteria=["Accepted requirements have executable coverage"],
            )
        )
        tasks.extend(
            [
                PlanTask(
                    id="verification",
                    title="Milestone verification and release gate",
                    description="Run detected builds, tests, lint, and type checks.",
                    dependencies=[tests_key],
                    required_capabilities=["python"],
                    suggested_agent="integrator",
                    milestone_key=release.key,
                    acceptance_criteria=list(release.acceptance_criteria),
                ),
                PlanTask(
                    id="review",
                    title="Lead final project review",
                    description=(
                        "Review contract consistency, milestone evidence, and requirements."
                    ),
                    dependencies=["verification"],
                    suggested_agent="reviewer",
                    milestone_key=release.key,
                    acceptance_criteria=["All milestones and requirements are verified"],
                ),
            ]
        )
        return ProjectPlan(
            project_summary=prompt.strip().splitlines()[0][:240],
            tasks=tasks,
        )

    def milestone_for_task(self, project_id: str, task: PlanTask) -> MilestoneRecord:
        milestones = self._milestones(project_id)
        if not milestones:
            raise RuntimeError("project milestones are not prepared")
        key = task.milestone_key or self._infer_milestone_key(task.id, milestones)
        return next((item for item in milestones if item.key == key), milestones[-1])

    def task_contract_context(self, project_id: str, task: PlanTask) -> dict[str, Any]:
        milestone = self.milestone_for_task(project_id, task)
        contract = self.contract(project_id)
        return {
            "milestone_id": milestone.id,
            "milestone": milestone.title,
            "contract_version": contract.version,
            "contract": contract.content,
            "allowed_files": task.allowed_files,
            "acceptance_criteria": task.acceptance_criteria
            or [task.description, *milestone.acceptance_criteria],
        }

    def materialize_contract(self, project_id: str, reason: str) -> dict[str, Any]:
        if self.projects is None:
            raise RuntimeError("contract materialization requires the canonical project repository")
        contract = self.contract(project_id)
        rendered = self.materializer.render(contract.version, contract.content)
        base_commit = self.projects.current_revision(project_id)
        try:
            modified, _ = self.projects.integrate_changes(
                project_id,
                f"contract-v{contract.version}",
                reason,
                [
                    ChangeOperation(
                        operation="update",
                        path=rendered.path,
                        content=rendered.content,
                    )
                ],
                base_commit,
            )
        except Exception as exc:
            raise ClassifiedFailure(
                FailureCode.CONTRACT_MATERIALIZATION_FAILURE,
                f"contract materialization failed: {exc}",
                {
                    "failure_stage": "CONTRACT_MATERIALIZATION",
                    "failure_classification": FailureCode.CONTRACT_MATERIALIZATION_FAILURE.value,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "path": rendered.path,
                    "contract_version": contract.version,
                    "retry_strategy": "REPAIR_CONTROLLER_MATERIALIZATION",
                },
            ) from exc
        completion_commit = self.projects.current_revision(project_id)
        with self.database.session() as session:
            milestones = session.scalars(
                select(MilestoneRecord).where(MilestoneRecord.project_id == project_id)
            )
            for milestone in milestones:
                if milestone.status in {"PENDING", "READY"}:
                    milestone.base_commit = completion_commit
        return {
            "path": rendered.path,
            "version": rendered.version,
            "commit": completion_commit,
            "modified": modified,
        }

    def protected_paths(self, project_id: str) -> list[str]:
        return list(self.contract(project_id).content.get("protected_paths", []))

    def attach_existing_tasks(
        self,
        project_id: str,
        plan: ProjectPlan,
        base_commit: str,
    ) -> None:
        """Bind pre-milestone active tasks to durable contract and milestone context."""
        contexts = {task.id: self.task_contract_context(project_id, task) for task in plan.tasks}
        with self.database.session() as session:
            project = session.get(ProjectRecord, project_id)
            if project is None:
                raise KeyError(project_id)
            statement = select(TaskRecord).where(TaskRecord.project_id == project_id)
            if project.active_run_id:
                statement = statement.where(TaskRecord.run_id == project.active_run_id)
            for record in session.scalars(statement):
                context = contexts.get(record.key)
                if context is None:
                    continue
                record.milestone_id = context["milestone_id"]
                record.base_commit = record.base_commit or base_commit
                record.contract_version = context["contract_version"]
                record.allowed_files = context["allowed_files"]
                record.acceptance_criteria = context["acceptance_criteria"]

    def validate_worker_result(
        self,
        task: TaskRecord,
        changes: list[ChangeOperation],
        contract_proposals: list[ContractProposal] | None = None,
    ) -> None:
        contract = self.contract(task.project_id)
        if task.contract_version != contract.version:
            raise ClassifiedFailure(
                FailureCode.STALE_CONTRACT_VERSION,
                "stale contract result: worker used "
                f"v{task.contract_version}; canonical is v{contract.version}",
                {
                    "failure_stage": "CONTRACT_VALIDATION",
                    "failure_classification": FailureCode.STALE_CONTRACT_VERSION.value,
                    "worker_contract_version": task.contract_version,
                    "canonical_contract_version": contract.version,
                    "retry_strategy": "REFRESH_CONTRACT_CONTEXT",
                },
            )
        protected_patterns = list(contract.content.get("protected_paths", []))
        for change in changes:
            unrestricted = task.allowed_files == ["**/*"]
            if not unrestricted and not any(
                fnmatch(change.path, pattern) for pattern in task.allowed_files
            ):
                raise ClassifiedFailure(
                    FailureCode.CONTRACT_VIOLATION,
                    f"worker changed a path outside its task scope: {change.path}",
                    {
                        "failure_stage": "WORKER_OUTPUT_VALIDATION",
                        "failure_classification": FailureCode.CONTRACT_VIOLATION.value,
                        "path": change.path,
                        "retry_strategy": "REPAIR_AGAINST_CANONICAL_CONTRACT",
                    },
                )
            protected = any(fnmatch(change.path, pattern) for pattern in protected_patterns)
            source_protected = bool(
                change.from_path
                and any(fnmatch(change.from_path, pattern) for pattern in protected_patterns)
            )
            if protected or source_protected:
                path = change.from_path if source_protected else change.path
                raise ClassifiedFailure(
                    FailureCode.CONTRACT_PROPOSAL_REQUIRED,
                    f"Core-owned contract path cannot be mutated by a worker: {path}",
                    {
                        "failure_stage": "CONTRACT_VALIDATION",
                        "failure_classification": FailureCode.CONTRACT_PROPOSAL_REQUIRED.value,
                        "error_type": "ProtectedContractMutation",
                        "actual_path": path,
                        "operation": change.operation,
                        "proposal_status": (
                            "RECEIVED_SEPARATELY" if contract_proposals else "MISSING"
                        ),
                        "worker_generation": "SUCCEEDED",
                        "artifact_persistence": "SUCCEEDED",
                        "retry_strategy": "REVIEW_CONTRACT_PROPOSAL",
                    },
                )

    def review_proposals(
        self,
        project_id: str,
        task_id: str,
        worker_id: str | None,
        proposals: list[ContractProposal],
    ) -> list[dict[str, Any]]:
        outcomes: list[dict[str, Any]] = []
        for proposal in proposals:
            approved, review_reason = self.lead.review_contract_proposal(proposal)
            proposal_id = proposal.proposal_id or f"proposal-{uuid4().hex[:12]}"
            now = datetime.now(UTC)
            old_version = self.contract(project_id).version
            new_version: int | None = None
            previous_value: object | None = None
            affected_tasks: list[str] = []
            updated_content: dict[str, Any] | None = None
            if approved:
                try:
                    contract = self.contract(project_id)
                    updated_content, previous_value = self.materializer.apply(
                        contract.content, proposal, contract.version
                    )
                except ContractApplicationError as exc:
                    approved = False
                    review_reason = str(exc)
            with self.database.session() as session:
                session.add(
                    ContractProposalRecord(
                        id=proposal_id,
                        project_id=project_id,
                        task_id=task_id,
                        worker_id=worker_id,
                        proposal_type=proposal.type,
                        target=proposal.target,
                        change=proposal.change,
                        reason=proposal.reason,
                        status="APPROVED" if approved else "REJECTED",
                        review_reason=review_reason,
                        reviewed_by=self.lead.name,
                        reviewed_at=now,
                        current_value=previous_value,
                        proposed_value=proposal.proposed_value,
                        expected_revision=proposal.expected_revision,
                    )
                )
                if approved and updated_content is not None:
                    contract = session.scalar(
                        select(ProjectContractRecord).where(
                            ProjectContractRecord.project_id == project_id
                        )
                    )
                    if contract is None:
                        raise RuntimeError("project contract is missing")
                    if contract.version != old_version:
                        raise ClassifiedFailure(
                            FailureCode.STALE_CONTRACT_VERSION,
                            "contract changed while reviewing proposal",
                            {
                                "failure_stage": "CONTRACT_REVIEW",
                                "failure_classification": FailureCode.STALE_CONTRACT_VERSION.value,
                                "retry_strategy": "REFRESH_CONTRACT_CONTEXT",
                            },
                        )
                    contract.version += 1
                    new_version = contract.version
                    contract.content = updated_content
                    contract.updated_at = now
                    candidates = session.scalars(
                        select(TaskRecord).where(TaskRecord.project_id == project_id)
                    )
                    for candidate in candidates:
                        if candidate.id == task_id or candidate.status in {"PENDING", "READY"}:
                            candidate.contract_version = new_version
                            affected_tasks.append(candidate.id)
                    session.add(
                        ContractRevisionRecord(
                            id=f"contract-revision-{uuid4().hex[:12]}",
                            project_id=project_id,
                            version=new_version,
                            content=updated_content,
                            reason=proposal.reason,
                            proposal_id=proposal_id,
                            previous_version=old_version,
                            affected_tasks=affected_tasks,
                        )
                    )
            materialization: dict[str, Any] | None = None
            if approved and self.projects is not None:
                materialization = self.materialize_contract(
                    project_id, f"Apply approved proposal {proposal_id}"
                )
            outcomes.append(
                {
                    "id": proposal_id,
                    "target": proposal.target,
                    "approved": approved,
                    "status": "APPROVED" if approved else "REJECTED",
                    "reason": review_reason,
                    "old_version": old_version,
                    "new_version": new_version,
                    "affected_tasks": affected_tasks,
                    "materialization": materialization,
                }
            )
        return outcomes

    def start_milestone(self, milestone_id: str) -> None:
        with self.database.session() as session:
            milestone = session.get(MilestoneRecord, milestone_id)
            if milestone is not None and milestone.status in {"PENDING", "READY"}:
                milestone.status = "RUNNING"
                milestone.started_at = milestone.started_at or datetime.now(UTC)

    def gate_ready_milestones(self, project_id: str, commit: str) -> list[str]:
        completed: list[str] = []
        now = datetime.now(UTC)
        contract_version = self.contract(project_id).version
        with self.database.session() as session:
            milestones = list(
                session.scalars(
                    select(MilestoneRecord)
                    .where(MilestoneRecord.project_id == project_id)
                    .order_by(MilestoneRecord.sequence)
                )
            )
            for milestone in milestones:
                if milestone.status == "COMPLETED":
                    continue
                dependencies = [
                    session.get(MilestoneRecord, item) for item in milestone.dependencies
                ]
                if any(item is None or item.status != "COMPLETED" for item in dependencies):
                    continue
                project = session.get(ProjectRecord, project_id)
                statement = select(TaskRecord).where(
                    TaskRecord.project_id == project_id,
                    TaskRecord.milestone_id == milestone.id,
                )
                if project is not None and project.active_run_id:
                    statement = statement.where(TaskRecord.run_id == project.active_run_id)
                tasks = list(session.scalars(statement))
                if not tasks or not all(item.status == "COMPLETED" for item in tasks):
                    continue
                milestone.status = "COMPLETED"
                milestone.started_at = milestone.started_at or now
                milestone.completed_at = now
                milestone.completion_commit = commit
                milestone.ending_contract_version = contract_version
                session.add(
                    VerificationRecord(
                        id=f"verification-{uuid4().hex[:12]}",
                        project_id=project_id,
                        milestone_id=milestone.id,
                        scope="milestone",
                        status="PASSED",
                        checks=[
                            {"name": "task_completion", "passed": True},
                            {"name": "contract_version", "passed": True},
                        ],
                        evidence=[
                            f"All {len(tasks)} milestone tasks completed",
                            f"Canonical commit {commit}",
                        ],
                        commit=commit,
                        contract_version=contract_version,
                    )
                )
                requirements = session.scalars(
                    select(ProjectRequirementRecord).where(
                        ProjectRequirementRecord.milestone_id == milestone.id
                    )
                ).all()
                for requirement in requirements:
                    requirement.status = "VERIFIED"
                    requirement.evidence = [f"Milestone gate passed at {commit}"]
                    requirement.verified_at = now
                completed.append(milestone.id)
        return completed

    def record_repair(self, project_id: str, task: TaskRecord, evidence: dict[str, Any]) -> str:
        root, strategy = self.lead.analyze_failure(evidence)
        record_id = f"repair-{uuid4().hex[:12]}"
        with self.database.session() as session:
            session.add(
                RepairRecord(
                    id=record_id,
                    project_id=project_id,
                    milestone_id=task.milestone_id,
                    task_id=task.id,
                    failure_code=str(evidence.get("failure_code", "UNKNOWN")),
                    evidence=evidence,
                    strategy=strategy,
                )
            )
            session.add(
                LeadDecisionRecord(
                    id=f"lead-{uuid4().hex[:12]}",
                    project_id=project_id,
                    decision_type="repair_plan",
                    summary=root,
                    rationale=strategy,
                    evidence=[str(evidence.get("error", ""))],
                    provider=self.lead.name,
                )
            )
        return record_id

    def final_acceptance(self, project_id: str, commit: str) -> dict[str, Any]:
        summary = self.summary(project_id)
        milestones = summary["milestones"]
        requirements = summary["requirements"]
        milestone_pass = bool(milestones) and all(
            item["status"] == "COMPLETED" for item in milestones
        )
        requirement_pass = all(item["status"] == "VERIFIED" for item in requirements)
        passed = milestone_pass and requirement_pass and summary["contract"] is not None
        milestone_total = sum(item["status"] == "COMPLETED" for item in milestones)
        requirement_total = sum(item["status"] == "VERIFIED" for item in requirements)
        with self.database.session() as session:
            session.add(
                VerificationRecord(
                    id=f"verification-{uuid4().hex[:12]}",
                    project_id=project_id,
                    scope="final",
                    status="PASSED" if passed else "FAILED",
                    checks=[
                        {"name": "milestones", "passed": milestone_pass},
                        {"name": "requirement_coverage", "passed": requirement_pass},
                        {"name": "canonical_contract", "passed": summary["contract"] is not None},
                    ],
                    evidence=[
                        f"Milestones {milestone_total}/{len(milestones)}",
                        f"Requirements {requirement_total}/{len(requirements)}",
                    ],
                    commit=commit,
                    contract_version=summary["contract"]["version"],
                )
            )
        return {
            "passed": passed,
            "milestones": len(milestones),
            "requirements": len(requirements),
            "commit": commit,
            "contract_version": summary["contract"]["version"],
        }

    def summary(self, project_id: str) -> dict[str, Any]:
        with self.database.session() as session:

            def one(model: Any) -> Any:
                return session.scalar(select(model).where(model.project_id == project_id))

            def many(model: Any, order: Any) -> list[Any]:
                return list(
                    session.scalars(
                        select(model).where(model.project_id == project_id).order_by(order)
                    )
                )

            analysis = one(ProjectAnalysisRecord)
            blueprint = one(ProjectBlueprintRecord)
            contract = one(ProjectContractRecord)
            compute = one(ComputePlanRecord)
            milestones = many(MilestoneRecord, MilestoneRecord.sequence)
            requirements = many(ProjectRequirementRecord, ProjectRequirementRecord.created_at)
            decisions = many(ArchitectureDecisionRecord, ArchitectureDecisionRecord.sequence)
            lead = many(LeadDecisionRecord, LeadDecisionRecord.created_at.desc())
            proposals = many(ContractProposalRecord, ContractProposalRecord.created_at.desc())
            revisions = many(ContractRevisionRecord, ContractRevisionRecord.version.desc())
            verifications = many(VerificationRecord, VerificationRecord.created_at.desc())
            project = session.get(ProjectRecord, project_id)
            task_statement = (
                select(TaskRecord)
                .where(TaskRecord.project_id == project_id)
                .order_by(TaskRecord.sequence)
            )
            if project is not None and project.active_run_id:
                task_statement = task_statement.where(TaskRecord.run_id == project.active_run_id)
            tasks = list(session.scalars(task_statement))
        return {
            "analysis": self._serialize(analysis),
            "blueprint": self._serialize(blueprint),
            "contract": self._serialize(contract),
            "compute_plan": self._serialize(compute),
            "milestones": [self._serialize(item) for item in milestones],
            "milestone_tasks": {
                milestone.id: [
                    self._serialize(task) for task in tasks if task.milestone_id == milestone.id
                ]
                for milestone in milestones
            },
            "requirements": [self._serialize(item) for item in requirements],
            "architecture_decisions": [self._serialize(item) for item in decisions],
            "lead_decisions": [self._serialize(item) for item in lead],
            "contract_proposals": [self._serialize(item) for item in proposals],
            "contract_revisions": [self._serialize(item) for item in revisions],
            "verifications": [self._serialize(item) for item in verifications],
        }

    def update_milestone(
        self, project_id: str, milestone_id: str, values: dict[str, Any]
    ) -> dict[str, Any]:
        allowed = {
            "title",
            "goal",
            "description",
            "sequence",
            "dependencies",
            "deliverables",
            "acceptance_criteria",
        }
        if not values or not set(values).issubset(allowed):
            raise ValueError("milestone update contains unsupported fields")
        with self.database.session() as session:
            milestone = session.get(MilestoneRecord, milestone_id)
            if milestone is None or milestone.project_id != project_id:
                raise KeyError(milestone_id)
            if milestone.status not in {"PENDING", "READY"}:
                raise ValueError("started milestones cannot be edited")
            for key, value in values.items():
                setattr(milestone, key, value)
        self._validate_milestone_graph(project_id)
        return self.summary(project_id)

    def select_compute(self, project_id: str, strategy: str, worker_count: int) -> dict[str, Any]:
        if strategy not in {"cheapest", "balanced", "fast", "maximum_quality"}:
            raise ValueError("invalid compute strategy")
        if worker_count < 1 or worker_count > 12:
            raise ValueError("worker count must be between 1 and 12")
        with self.database.session() as session:
            plan = session.scalar(
                select(ComputePlanRecord).where(ComputePlanRecord.project_id == project_id)
            )
            if plan is None:
                raise KeyError(project_id)
            plan.strategy = strategy
            plan.selected_worker_count = worker_count
            plan.updated_at = datetime.now(UTC)
        return self.summary(project_id)

    def contract(self, project_id: str) -> ProjectContractRecord:
        with self.database.session() as session:
            contract = session.scalar(
                select(ProjectContractRecord).where(ProjectContractRecord.project_id == project_id)
            )
            if contract is None:
                raise RuntimeError("project contract is not prepared")
            session.expunge(contract)
            return contract

    def _milestones(self, project_id: str) -> list[MilestoneRecord]:
        with self.database.session() as session:
            items = list(
                session.scalars(
                    select(MilestoneRecord)
                    .where(MilestoneRecord.project_id == project_id)
                    .order_by(MilestoneRecord.sequence)
                )
            )
            for item in items:
                session.expunge(item)
            return items

    @staticmethod
    def _infer_milestone_key(task_id: str, milestones: list[MilestoneRecord]) -> str:
        keys = {item.key for item in milestones}
        if len(milestones) == 2:
            return (
                "verification-release"
                if task_id in {"verification", "review"}
                else "implementation"
            )
        mapping = {
            "plan": "architecture-foundation",
            "foundation": "architecture-foundation",
            "backend": "backend-domain" if "backend-domain" in keys else "core-implementation",
            "database": "persistence-integration",
            "persistence": "persistence-integration",
            "frontend": "frontend-experience"
            if "frontend-experience" in keys
            else "experience-integration",
            "integration": "advanced-integration"
            if "advanced-integration" in keys
            else "experience-integration",
            "tests": "advanced-integration"
            if "advanced-integration" in keys
            else "experience-integration",
            "verification": "verification-release",
            "review": "verification-release",
        }
        return mapping.get(task_id, milestones[min(1, len(milestones) - 1)].key)

    def _validate_milestone_graph(self, project_id: str) -> None:
        milestones = self._milestones(project_id)
        known = {item.id for item in milestones}
        positions = {item.id: item.sequence for item in milestones}
        if len(positions) != len(set(positions.values())):
            raise ValueError("milestone sequences must be unique")
        for item in milestones:
            if item.id in item.dependencies or not set(item.dependencies).issubset(known):
                raise ValueError(f"invalid dependencies for {item.title}")
            if any(positions[dependency] >= item.sequence for dependency in item.dependencies):
                raise ValueError(f"milestone dependencies must precede {item.title}")

    @staticmethod
    def _requirement_category(text: str) -> str:
        lower = text.lower()
        if any(item in lower for item in ("security", "auth", "permission", "secret")):
            return "security"
        if any(item in lower for item in ("deploy", "hosting", "release")):
            return "deployment"
        if any(item in lower for item in ("persist", "database", "store", "offline")):
            return "data"
        return "feature"

    @staticmethod
    def _serialize(record: Any) -> dict[str, Any] | None:
        if record is None:
            return None
        result: dict[str, Any] = {}
        for column in record.__table__.columns:
            value = getattr(record, column.name)
            result[column.name] = value.isoformat() if isinstance(value, datetime) else value
        return result
