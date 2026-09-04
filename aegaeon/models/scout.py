from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import BaseModel, Field
from sqlalchemy import select

from aegaeon.database.models import ModelCatalogRecord
from aegaeon.database.session import Database


class ModelEvidence(BaseModel):
    id: str
    publisher: str
    family: str
    parameter_count_b: float | None = None
    architecture: str | None = None
    instruction_tuned: bool
    context_length: int | None = None
    runtimes: list[str]
    quantizations: list[str]
    license: str | None = None
    gated: bool = False
    download_size_gb: float | None = None
    safe_vram_mb: dict[str, int] = Field(default_factory=dict)
    cpu_offload_support: bool = False
    multi_gpu_support: bool = False
    minimum_hardware: dict[str, int] = Field(default_factory=dict)
    recommended_hardware: dict[str, int] = Field(default_factory=dict)
    benchmark_scores: dict[str, float] = Field(default_factory=dict)
    structured_output_score: float | None = None
    languages: list[str] = Field(default_factory=list)
    source_url: str
    source: str = "curated-official-card"
    updated_at: datetime | None = None


class HardwareTarget(BaseModel):
    gpu_name: str = "NVIDIA T4"
    vram_mb: int = Field(default=15_000, ge=0)
    ram_mb: int = Field(default=13_000, ge=1024)
    runtime: str = "transformers"
    quantization: str = "4bit"
    minimum_context: int = Field(default=8_192, ge=1_024)
    require_public: bool = True


class RecommendationRequest(BaseModel):
    project: str = Field(min_length=10, max_length=20_000)
    role: str = "general coding"
    hardware: HardwareTarget = Field(default_factory=HardwareTarget)
    limit: int = Field(default=3, ge=1, le=20)


class ModelRecommendation(BaseModel):
    model: ModelEvidence
    score: float
    confidence: str
    score_breakdown: dict[str, float]
    reasons: list[str]
    warnings: list[str]
    recommended_quantization: str
    estimated_vram_mb: int


CURATED_MODELS = [
    ModelEvidence(
        id="Qwen/Qwen2.5-Coder-1.5B-Instruct",
        publisher="Qwen",
        family="Qwen2.5-Coder",
        parameter_count_b=1.5,
        architecture="qwen2",
        instruction_tuned=True,
        context_length=32_768,
        runtimes=["transformers"],
        quantizations=["4bit", "8bit", "bf16"],
        license="apache-2.0",
        safe_vram_mb={"4bit": 3_072, "8bit": 4_096, "bf16": 6_144},
        benchmark_scores={"coding": 74, "repair": 67},
        structured_output_score=82,
        languages=["English", "Python", "JavaScript", "TypeScript"],
        source_url="https://huggingface.co/Qwen/Qwen2.5-Coder-1.5B-Instruct",
    ),
    ModelEvidence(
        id="deepseek-ai/deepseek-coder-1.3b-instruct",
        publisher="deepseek-ai",
        family="DeepSeek-Coder",
        parameter_count_b=1.3,
        architecture="llama",
        instruction_tuned=True,
        context_length=16_384,
        runtimes=["transformers"],
        quantizations=["4bit", "8bit", "bf16"],
        license="deepseek-model-license",
        safe_vram_mb={"4bit": 3_072, "8bit": 4_096, "bf16": 6_144},
        benchmark_scores={"coding": 69, "repair": 62},
        structured_output_score=72,
        languages=["English", "Python", "JavaScript"],
        source_url="https://huggingface.co/deepseek-ai/deepseek-coder-1.3b-instruct",
    ),
    ModelEvidence(
        id="Qwen/Qwen2.5-Coder-3B-Instruct",
        publisher="Qwen",
        family="Qwen2.5-Coder",
        parameter_count_b=3.09,
        architecture="qwen2",
        instruction_tuned=True,
        context_length=32_768,
        runtimes=["transformers", "vllm"],
        quantizations=["4bit", "8bit", "bf16"],
        license="apache-2.0",
        safe_vram_mb={"4bit": 4_608, "8bit": 6_656, "bf16": 10_240},
        cpu_offload_support=True,
        multi_gpu_support=True,
        minimum_hardware={"ram_mb": 8_192},
        recommended_hardware={"vram_mb": 6_144, "ram_mb": 12_288},
        benchmark_scores={"coding": 84, "repair": 79},
        structured_output_score=87,
        languages=["English", "Python", "JavaScript", "TypeScript", "Java"],
        source_url="https://huggingface.co/Qwen/Qwen2.5-Coder-3B-Instruct",
    ),
    ModelEvidence(
        id="Qwen/Qwen2.5-Coder-7B-Instruct",
        publisher="Qwen",
        family="Qwen2.5-Coder",
        parameter_count_b=7.61,
        architecture="qwen2",
        instruction_tuned=True,
        context_length=131_072,
        runtimes=["transformers", "vllm"],
        quantizations=["4bit", "8bit", "bf16"],
        license="apache-2.0",
        safe_vram_mb={"4bit": 8_704, "8bit": 12_288, "bf16": 20_480},
        benchmark_scores={"coding": 91, "repair": 88},
        structured_output_score=91,
        languages=["English", "Python", "JavaScript", "TypeScript", "Java"],
        source_url="https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct",
    ),
]


class HuggingFaceSource:
    api_url = "https://huggingface.co/api/models"

    async def discover(self, query: str, token: str = "", limit: int = 10) -> list[dict[str, Any]]:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                self.api_url,
                params={"search": query, "limit": limit, "full": "true"},
                headers=headers,
            )
            response.raise_for_status()
            return response.json()


class ModelScout:
    """Deterministic evidence registry, hard filter, and explainable scorer."""

    VRAM_SAFETY_RATIO = 0.80
    PREFILL_WORKSPACE_MB_PER_B_TOKEN = 0.10

    def __init__(self, database: Database) -> None:
        self.database = database
        self.hugging_face = HuggingFaceSource()

    def seed(self) -> None:
        with self.database.session() as session:
            for evidence in CURATED_MODELS:
                if not session.get(ModelCatalogRecord, evidence.id):
                    session.add(
                        ModelCatalogRecord(
                            id=evidence.id,
                            metadata_json=evidence.model_dump(mode="json"),
                            source=evidence.source,
                        )
                    )

    def list(self) -> list[ModelEvidence]:
        with self.database.session() as session:
            rows = session.scalars(select(ModelCatalogRecord).order_by(ModelCatalogRecord.id)).all()
            return [ModelEvidence.model_validate(row.metadata_json) for row in rows]

    @classmethod
    def estimated_peak_vram(
        cls,
        model: ModelEvidence,
        quantization: str,
        *,
        prompt_tokens: int = 8_192,
        output_tokens: int = 1_024,
    ) -> int | None:
        """Conservative full-request estimate, including prefill/attention workspace."""
        base = model.safe_vram_mb.get(quantization)
        if base is None:
            return None
        parameters = float(model.parameter_count_b or 1.0)
        token_window = max(1_024, prompt_tokens + output_tokens)
        workspace = int(parameters * token_window * cls.PREFILL_WORKSPACE_MB_PER_B_TOKEN)
        return base + max(512, workspace)

    def fallback_ladder(
        self,
        model_id: str,
        quantization: str,
        *,
        vram_mb: int,
        prompt_tokens: int = 8_192,
        output_tokens: int = 1_024,
    ) -> list[dict[str, int | str]]:
        """Return trusted same-family models that fit with enforced VRAM headroom."""
        catalog = self.list()
        requested = next((item for item in catalog if item.id == model_id), None)
        if requested is None:
            return []
        requested_parameters = float(requested.parameter_count_b or float("inf"))
        safe_budget = int(vram_mb * self.VRAM_SAFETY_RATIO)
        ladder: list[dict[str, int | str]] = []
        for candidate in catalog:
            parameters = float(candidate.parameter_count_b or float("inf"))
            if (
                candidate.family != requested.family
                or parameters > requested_parameters
                or candidate.gated
                or quantization not in candidate.quantizations
            ):
                continue
            peak = self.estimated_peak_vram(
                candidate,
                quantization,
                prompt_tokens=prompt_tokens,
                output_tokens=output_tokens,
            )
            if peak is None or peak > safe_budget:
                continue
            ladder.append(
                {
                    "id": candidate.id,
                    "base_vram_mb": int(candidate.safe_vram_mb[quantization]),
                    "estimated_peak_vram_mb": peak,
                }
            )
        parameter_counts = {model.id: float(model.parameter_count_b or 0) for model in catalog}
        return sorted(
            ladder,
            key=lambda item: parameter_counts.get(str(item["id"]), 0),
            reverse=True,
        )

    def recommend(self, request: RecommendationRequest) -> list[ModelRecommendation]:
        target = request.hardware
        usable_vram = int(target.vram_mb * self.VRAM_SAFETY_RATIO)
        recommendations: list[ModelRecommendation] = []
        for model in self.list():
            estimate = self.estimated_peak_vram(
                model,
                target.quantization,
                prompt_tokens=target.minimum_context,
            )
            if estimate is None or estimate > usable_vram:
                continue
            if (
                target.runtime not in model.runtimes
                or target.quantization not in model.quantizations
            ):
                continue
            if not model.instruction_tuned or (model.context_length or 0) < target.minimum_context:
                continue
            if target.require_public and model.gated:
                continue
            coding = model.benchmark_scores.get("coding", 50)
            structured = model.structured_output_score or 50
            headroom = max(0, usable_vram - estimate)
            hardware = min(100.0, 70 + headroom / max(128, usable_vram) * 30)
            context = min(100.0, (model.context_length or 0) / target.minimum_context * 55)
            license_score = 100.0 if model.license in {"apache-2.0", "mit"} else 72.0
            breakdown = {
                "coding_quality": round(coding, 1),
                "hardware_fit": round(hardware, 1),
                "structured_output": round(structured, 1),
                "context": round(context, 1),
                "runtime_support": 100.0,
                "license": license_score,
            }
            score = (
                coding * 0.32
                + hardware * 0.25
                + structured * 0.16
                + context * 0.10
                + 100 * 0.10
                + license_score * 0.07
            )
            recommendations.append(
                ModelRecommendation(
                    model=model,
                    score=round(score, 1),
                    confidence="high" if len(model.benchmark_scores) >= 2 else "medium",
                    score_breakdown=breakdown,
                    reasons=[
                        (
                            f"Estimated full-request peak {estimate} MB fits within the "
                            f"{usable_vram} MB safe VRAM budget"
                        ),
                        f"Supports {target.runtime} with {target.quantization}",
                        f"Provides {model.context_length or 0:,} tokens of documented context",
                    ],
                    warnings=(
                        []
                        if model.license in {"apache-2.0", "mit"}
                        else [
                            f"Review the {model.license or 'unknown'} license before distribution"
                        ]
                    ),
                    recommended_quantization=target.quantization,
                    estimated_vram_mb=estimate,
                )
            )
        return sorted(recommendations, key=lambda item: item.score, reverse=True)[: request.limit]

    def import_hugging_face_metadata(self, items: list[dict[str, Any]]) -> int:
        imported = 0
        with self.database.session() as session:
            for item in items:
                model_id = item.get("modelId") or item.get("id")
                if not isinstance(model_id, str) or "/" not in model_id:
                    continue
                if session.get(ModelCatalogRecord, model_id):
                    continue
                card = item.get("cardData") or {}
                tags = item.get("tags") or []
                evidence = ModelEvidence(
                    id=model_id,
                    publisher=model_id.split("/", 1)[0],
                    family=model_id.split("/", 1)[1],
                    instruction_tuned="instruct" in model_id.lower(),
                    runtimes=["transformers"] if "transformers" in tags else [],
                    quantizations=[],
                    license=card.get("license"),
                    gated=bool(item.get("gated", False)),
                    source_url=f"https://huggingface.co/{model_id}",
                    source="hugging-face-api",
                    updated_at=datetime.now(UTC),
                )
                session.add(
                    ModelCatalogRecord(
                        id=model_id,
                        metadata_json=evidence.model_dump(mode="json"),
                        source=evidence.source,
                    )
                )
                imported += 1
        return imported
