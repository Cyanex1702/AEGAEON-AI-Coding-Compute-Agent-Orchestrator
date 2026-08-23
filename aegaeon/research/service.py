from __future__ import annotations

import json
import re
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy import select

from aegaeon.database.models import ResearchRecord
from aegaeon.database.session import Database
from aegaeon.models.scout import ModelEvidence, ModelRecommendation


class ManualResearchImport(BaseModel):
    advisor: str = Field(min_length=2, max_length=80)
    response: str = Field(min_length=10, max_length=100_000)


class ResearchResult(BaseModel):
    id: str
    project_id: str
    advisor: str
    recommended_model: str | None
    runner_up: str | None
    confidence: str | None
    tradeoffs: list[str]
    warnings: list[str]
    conflicts: list[str]


class ResearchService:
    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def prompt(
        project: str,
        role: str,
        hardware: dict[str, Any],
        recommendations: list[ModelRecommendation],
    ) -> str:
        candidates = []
        for item in recommendations:
            model = item.model
            candidates.append(
                "\n".join(
                    [
                        f"MODEL: {model.id}",
                        f"AEGAEON SCORE: {item.score}",
                        f"SAFE VRAM ESTIMATE: {item.estimated_vram_mb} MB",
                        f"CONTEXT: {model.context_length or 'unknown'}",
                        f"LICENSE: {model.license or 'unknown'}",
                        f"RUNTIME: {item.recommended_quantization} on {', '.join(model.runtimes)}",
                        f"BENCHMARK EVIDENCE: {json.dumps(model.benchmark_scores, sort_keys=True)}",
                    ]
                )
            )
        return f"""PROJECT
{project}

WORKER ROLE
{role}

ENVIRONMENT
{json.dumps(hardware, indent=2, sort_keys=True)}

CANDIDATES
{chr(10).join(candidates)}

TASK
Compare only the supplied candidates. Evaluate coding quality, hardware fit,
runtime stability, context, structured JSON reliability, and license tradeoffs.
Do not invent model facts or benchmark results. If evidence is missing, say unknown.

RETURN EXACT JSON
{{
  "recommended_model": "candidate model ID",
  "runner_up": "candidate model ID or null",
  "tradeoffs": ["..."],
  "warnings": ["..."],
  "confidence": "low|medium|high",
  "vram_claims_mb": {{"candidate model ID": 12345}}
}}
"""

    def import_response(
        self,
        project_id: str,
        request: ManualResearchImport,
        evidence: list[ModelEvidence],
    ) -> ResearchResult:
        parsed = self._parse(request.response)
        known = {item.id: item for item in evidence}
        recommended = parsed.get("recommended_model")
        runner_up = parsed.get("runner_up")
        conflicts: list[str] = []
        if recommended not in known:
            conflicts.append("Recommended model is not in AEGAEON's supplied evidence")
        if runner_up is not None and runner_up not in known:
            conflicts.append("Runner-up model is not in AEGAEON's supplied evidence")
        claims = parsed.get("vram_claims_mb") or {}
        if isinstance(claims, dict):
            for model_id, claim in claims.items():
                model = known.get(model_id)
                estimates = list(model.safe_vram_mb.values()) if model else []
                if estimates and isinstance(claim, (int, float)) and claim < min(estimates) * 0.75:
                    conflicts.append(
                        f"{model_id} VRAM claim ({claim} MB) conflicts with factual estimates"
                    )
        result = ResearchResult(
            id=f"research-{uuid4().hex[:12]}",
            project_id=project_id,
            advisor=request.advisor,
            recommended_model=recommended if isinstance(recommended, str) else None,
            runner_up=runner_up if isinstance(runner_up, str) else None,
            confidence=(
                parsed.get("confidence")
                if parsed.get("confidence") in {"low", "medium", "high"}
                else None
            ),
            tradeoffs=[str(item) for item in parsed.get("tradeoffs", [])][:50],
            warnings=[str(item) for item in parsed.get("warnings", [])][:50],
            conflicts=conflicts,
        )
        with self.database.session() as session:
            session.add(
                ResearchRecord(
                    id=result.id,
                    project_id=project_id,
                    advisor=result.advisor,
                    response=request.response,
                    parsed=result.model_dump(mode="json"),
                    conflicts=conflicts,
                )
            )
        return result

    def list(self, project_id: str) -> list[ResearchResult]:
        with self.database.session() as session:
            rows = session.scalars(
                select(ResearchRecord)
                .where(ResearchRecord.project_id == project_id)
                .order_by(ResearchRecord.created_at.desc())
            ).all()
            return [ResearchResult.model_validate(row.parsed) for row in rows]

    @staticmethod
    def _parse(response: str) -> dict[str, Any]:
        cleaned = response.strip()
        fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", cleaned, re.DOTALL)
        if fence:
            cleaned = fence.group(1)
        try:
            value = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise ValueError("Research response must contain the requested JSON object") from exc
        if not isinstance(value, dict):
            raise ValueError("Research response must be a JSON object")
        return value
