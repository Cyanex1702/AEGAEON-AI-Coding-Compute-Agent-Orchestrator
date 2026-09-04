from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from uuid import uuid4

from aegaeon.compute.schemas import (
    ComputePolicy,
    ComputePolicyPreset,
    ContextPlan,
    ContextSection,
    DeviceStrategy,
    GPUState,
    ModelExecutionPlan,
    OOMRisk,
    RecoveryAction,
    WorkerComputeState,
)


class ContextBudgeter:
    """Priority-aware context packing that never drops protected contract facts."""

    @staticmethod
    def estimate_tokens(value: str) -> int:
        if not value:
            return 0
        return max(1, (len(value) + 3) // 4)

    def pack(self, sections: Iterable[ContextSection], token_budget: int) -> ContextPlan:
        prepared = [
            section.model_copy(
                update={
                    "estimated_tokens": self.estimate_tokens(section.content),
                    "included_tokens": 0,
                    "included": False,
                }
            )
            for section in sections
        ]
        ordered = sorted(
            prepared,
            key=lambda item: (not item.protected, -item.priority),
        )
        remaining = max(0, token_budget)
        dropped: list[str] = []
        for section in ordered:
            cost = section.estimated_tokens
            if section.protected:
                section.included = True
                section.included_tokens = cost
                remaining = max(0, remaining - cost)
                continue
            if cost <= remaining:
                section.included = True
                section.included_tokens = cost
                remaining -= cost
                continue
            if remaining >= 128 and section.priority >= 70:
                section.content = section.content[: remaining * 4]
                section.included = True
                section.included_tokens = self.estimate_tokens(section.content)
                remaining = max(0, remaining - section.included_tokens)
            else:
                dropped.append(section.name)
        sections_by_input = {section.name: section for section in ordered}
        result_sections = [sections_by_input[item.name] for item in prepared]
        return ContextPlan(
            token_budget=token_budget,
            estimated_tokens=sum(item.estimated_tokens for item in prepared),
            packed_tokens=sum(item.included_tokens for item in result_sections),
            sections=result_sections,
            dropped_sections=dropped,
        )


class AdaptiveComputePlanner:
    """Turns task, model, live hardware, and observations into an executable plan."""

    def __init__(self) -> None:
        self.context_budgeter = ContextBudgeter()

    @staticmethod
    def policy_from_options(options: dict[str, Any] | None) -> ComputePolicy:
        values = options or {}
        raw = str(values.get("compute_policy") or values.get("compute_strategy") or "balanced")
        preset = {
            "maximum_quality": ComputePolicyPreset.QUALITY_FIRST,
            "quality_first": ComputePolicyPreset.QUALITY_FIRST,
            "QUALITY_FIRST": ComputePolicyPreset.QUALITY_FIRST,
            "cheapest": ComputePolicyPreset.MEMORY_SAFE,
            "memory_safe": ComputePolicyPreset.MEMORY_SAFE,
            "MEMORY_SAFE": ComputePolicyPreset.MEMORY_SAFE,
            "fast": ComputePolicyPreset.FAST,
            "FAST": ComputePolicyPreset.FAST,
        }.get(raw, ComputePolicyPreset.BALANCED)
        defaults: dict[str, Any] = {"preset": preset}
        if preset == ComputePolicyPreset.QUALITY_FIRST:
            defaults.update(
                allow_model_fallback=False,
                allow_runtime_change=True,
                allow_cpu_offload=True,
            )
        elif preset == ComputePolicyPreset.MEMORY_SAFE:
            defaults.update(
                allow_model_fallback=True,
                allow_runtime_change=True,
                minimum_quality_score=55,
            )
        elif preset == ComputePolicyPreset.FAST:
            defaults.update(allow_cpu_offload=False, allow_model_fallback=True)
        else:
            defaults.update(allow_model_fallback=True)
        for field in ComputePolicy.model_fields:
            if field in values:
                defaults[field] = values[field]
        return ComputePolicy.model_validate(defaults)

    @staticmethod
    def worker_state(worker: Any) -> WorkerComputeState:
        item = worker.model_dump(mode="json") if hasattr(worker, "model_dump") else dict(worker)
        hardware = dict(item.get("hardware") or {})
        memory = dict(hardware.get("memory") or {})
        singular_gpu = dict(hardware.get("gpu") or {})
        gpu_items = hardware.get("gpus")
        if not isinstance(gpu_items, list):
            gpu_items = [singular_gpu] if singular_gpu.get("available") else []
        used_total = int(item.get("vram_used_mb") or memory.get("vram_allocated_mb") or 0)
        gpus: list[GPUState] = []
        for index, gpu in enumerate(gpu_items):
            total = int(gpu.get("total_mb") or gpu.get("vram_mb") or 0)
            allocated = int(gpu.get("allocated_mb") or (used_total if index == 0 else 0))
            reserved = int(gpu.get("reserved_mb") or allocated)
            free = int(gpu.get("free_mb") or max(0, total - max(allocated, reserved)))
            gpus.append(
                GPUState(
                    index=int(gpu.get("index", index)),
                    name=str(gpu.get("name") or "Unknown GPU"),
                    total_mb=total,
                    allocated_mb=allocated,
                    reserved_mb=reserved,
                    free_mb=free,
                    utilization_percent=float(gpu.get("utilization_percent") or 0),
                    compute_capability=gpu.get("compute_capability"),
                )
            )
        ready_models = [
            model
            for model in item.get("models", [])
            if model.get("loaded") and model.get("status", "ready") == "ready"
        ]
        resident = ready_models[0] if ready_models else {}
        free_vram = sum(gpu.free_mb for gpu in gpus)
        total_vram = sum(gpu.total_mb for gpu in gpus)
        headroom_ratio = free_vram / total_vram if total_vram else 0
        risk = (
            OOMRisk.LOW.value
            if headroom_ratio >= 0.45
            else OOMRisk.MODERATE.value
            if headroom_ratio >= 0.25
            else OOMRisk.HIGH.value
            if headroom_ratio >= 0.12
            else OOMRisk.VERY_HIGH.value
        )
        return WorkerComputeState(
            worker_id=str(item.get("id") or item.get("worker_id") or "unknown"),
            hostname=str(item.get("hostname") or "unknown"),
            status=str(item.get("status") or "offline"),
            gpus=gpus,
            ram_total_mb=int(hardware.get("ram_mb") or 0),
            ram_available_mb=int(
                memory.get("ram_available_mb")
                or hardware.get("ram_available_mb")
                or (
                    int(hardware.get("ram_mb") or 0)
                    * (100 - float(item.get("ram_percent") or 0))
                    / 100
                )
            ),
            cpu_cores=int(hardware.get("cpu_cores") or 1),
            disk_free_mb=int(hardware.get("storage_mb") or 0),
            current_running_jobs=1 if item.get("current_job_id") or item.get("current_task") else 0,
            resident_model=resident.get("id"),
            runtime=resident.get("runtime"),
            quantization=resident.get("quantization"),
            model_memory_mb=int(resident.get("memory_footprint_mb") or 0),
            max_recommended_prompt_tokens=int(resident.get("max_recommended_prompt_tokens") or 0),
            max_recommended_output_tokens=int(resident.get("max_recommended_output_tokens") or 0),
            memory_risk_profile=str(resident.get("memory_risk_profile") or risk),
            supports_cpu_offload=bool(resident.get("supports_cpu_offload", False)),
            supports_multi_gpu=bool(resident.get("supports_multi_gpu", len(gpus) > 1)),
        )

    def plan(
        self,
        *,
        project_id: str | None,
        task_id: str | None,
        task_description: str,
        model_id: str,
        runtime: str,
        quantization: str,
        workers: Iterable[Any],
        model_catalog: Iterable[Any] = (),
        policy: ComputePolicy | None = None,
        context_sections: Iterable[ContextSection] = (),
        requested_output_tokens: int | None = None,
        attempt: int = 0,
        previous_failure: str | None = None,
        observations: Iterable[Any] = (),
    ) -> ModelExecutionPlan:
        active_policy = policy or ComputePolicy()
        states = [self.worker_state(worker) for worker in workers]
        catalog = [
            item.model_dump(mode="json") if hasattr(item, "model_dump") else dict(item)
            for item in model_catalog
        ]
        metadata = next((item for item in catalog if item.get("id") == model_id), {})
        compatible = [
            worker
            for worker in states
            if worker.status in {"online", "idle", "ready"}
            and (worker.resident_model in {None, model_id})
            and (worker.runtime in {None, runtime})
            and (worker.quantization in {None, quantization})
        ]
        compatible.sort(
            key=lambda worker: (
                worker.current_running_jobs == 0,
                worker.resident_model == model_id,
                worker.free_vram_mb,
                worker.ram_available_mb,
            ),
            reverse=True,
        )
        selected = compatible[0] if compatible else None
        prompt_budget = self._prompt_budget(active_policy.preset, selected)
        context_plan = self.context_budgeter.pack(context_sections, prompt_budget)
        output_tokens = self._output_budget(
            task_description,
            active_policy.preset,
            requested_output_tokens,
            selected,
        )
        memory_failure = (
            str(previous_failure or "").endswith("CUDA_OOM")
            or previous_failure == "CONTEXT_TOO_LARGE"
        )
        if memory_failure:
            if attempt == 1:
                output_tokens = max(
                    active_policy.minimum_viable_output_tokens,
                    int(output_tokens * 0.55),
                )
            elif attempt >= 2:
                output_tokens = max(
                    active_policy.minimum_viable_output_tokens,
                    int(output_tokens * 0.45),
                )
                prompt_budget = max(2048, int(prompt_budget * 0.65))
                context_plan = self.context_budgeter.pack(context_sections, prompt_budget)

        model_vram = self._model_vram(metadata, quantization)
        parameter_count = float(
            metadata.get("parameter_count_b") or self._parameter_count(model_id)
        )
        activation_workspace_mb = int(
            (context_plan.packed_tokens + output_tokens) * max(1.0, parameter_count) * 0.10
        )
        estimated_vram = max(model_vram, 1024) + max(512, activation_workspace_mb)
        estimated_ram = max(4096, int(estimated_vram * 0.45))
        multi_gpu_capacity = bool(
            selected
            and selected.supports_multi_gpu
            and len(selected.gpus) > 1
            and active_policy.allow_multi_gpu
        )
        if selected and multi_gpu_capacity:
            free_vram = sum(gpu.free_mb for gpu in selected.gpus)
            total_vram = sum(gpu.total_mb for gpu in selected.gpus)
        else:
            free_vram = max((gpu.free_mb for gpu in selected.gpus), default=0) if selected else 0
            total_vram = max((gpu.total_mb for gpu in selected.gpus), default=0) if selected else 0
        available_ram = selected.ram_available_mb if selected else 0

        historical = self._history_hint(
            observations,
            model_id,
            quantization,
            selected.gpus[0].name if selected and selected.gpus else None,
            context_plan.packed_tokens,
            output_tokens,
        )
        if historical["peak_vram_mb"]:
            estimated_vram = max(
                estimated_vram,
                int(historical["peak_vram_mb"] * 1.05),
            )
            estimated_ram = max(4096, int(estimated_vram * 0.45))
        safe_capacity = int(total_vram * 0.80) if total_vram else free_vram
        ratio = estimated_vram / max(1, safe_capacity)
        risk_points = 12 if ratio < 0.65 else 35 if ratio < 0.85 else 60 if ratio <= 1 else 82
        risk_points += historical["risk_delta"]
        if selected and selected.current_running_jobs:
            risk_points += 10
        risk_points = min(100, max(0, risk_points))
        risk = (
            OOMRisk.LOW
            if risk_points < 30
            else OOMRisk.MODERATE
            if risk_points < 55
            else OOMRisk.HIGH
            if risk_points < 80
            else OOMRisk.VERY_HIGH
        )

        device = DeviceStrategy.GPU
        offload = "NONE"
        if selected and multi_gpu_capacity:
            largest = max((gpu.free_mb for gpu in selected.gpus), default=0)
            if largest < estimated_vram <= selected.free_vram_mb:
                device = DeviceStrategy.MULTI_GPU
        if estimated_vram > free_vram and selected:
            offload_need = estimated_vram - free_vram
            ram_safe = available_ram - active_policy.ram_safety_buffer_mb
            if (
                active_policy.allow_cpu_offload
                and selected.supports_cpu_offload
                and ram_safe >= offload_need + 1024
            ):
                device = DeviceStrategy.GPU_WITH_CPU_OFFLOAD
                offload = "DEVICE_MAP_AUTO_WITH_RAM_LIMIT"
                risk = OOMRisk.MODERATE if risk == OOMRisk.HIGH else risk
            elif active_policy.allow_cpu_only and available_ram >= estimated_vram + 4096:
                device = DeviceStrategy.CPU_ONLY
                offload = "CPU_ONLY_LAST_RESORT"

        fallbacks = self._fallback_models(
            catalog,
            metadata,
            quantization,
            model_vram,
            active_policy,
        )
        fallback_workers = [
            worker.worker_id
            for worker in compatible[1:]
            if selected and worker.free_vram_mb >= max(selected.free_vram_mb * 1.2, estimated_vram)
        ]
        recovery = [
            RecoveryAction.CLEAN_MEMORY,
            RecoveryAction.RETRY_REDUCED_OUTPUT,
            RecoveryAction.RETRY_REDUCED_CONTEXT,
            RecoveryAction.SPLIT_TASK,
        ]
        if active_policy.allow_cpu_offload:
            recovery.append(RecoveryAction.RETRY_WITH_CPU_OFFLOAD)
        if active_policy.allow_quantization_change:
            recovery.append(RecoveryAction.RETRY_WITH_DIFFERENT_QUANTIZATION)
        if active_policy.allow_runtime_change:
            recovery.append(RecoveryAction.RETRY_DIFFERENT_RUNTIME)
        recovery.append(RecoveryAction.RETRY_DIFFERENT_WORKER)
        if active_policy.allow_model_fallback:
            recovery.append(RecoveryAction.FALLBACK_MODEL)
        recovery.append(RecoveryAction.ESCALATE_USER)
        next_action = self._next_recovery(attempt, previous_failure, active_policy)

        reasoning = [
            (
                f"Selected {model_id} with {quantization} because the explicit model "
                "choice is preserved."
            ),
            (
                f"{selected.hostname} has about {free_vram:,} MB live VRAM headroom."
                if selected
                else "No live compatible worker is currently available; plan is provisional."
            ),
            (
                f"Packed {context_plan.packed_tokens:,} prompt tokens and budgeted "
                f"{output_tokens:,} output tokens."
            ),
            f"Estimated fit is {max(0, 100 - risk_points)}/100 with {risk.value} OOM risk.",
        ]
        if device == DeviceStrategy.GPU_WITH_CPU_OFFLOAD:
            reasoning.append(
                "GPU + CPU offload is allowed and host RAM retains the configured safety buffer."
            )
        if context_plan.dropped_sections:
            reasoning.append(
                "Dropped low-priority context first: " + ", ".join(context_plan.dropped_sections)
            )
        if historical["samples"]:
            reasoning.append(
                "Historical evidence adjusted risk using "
                f"{historical['samples']} comparable observation(s)."
            )

        return ModelExecutionPlan(
            id=f"compute-plan-{uuid4().hex}",
            project_id=project_id,
            task_id=task_id,
            model_id=model_id,
            runtime=runtime,
            quantization=quantization,
            worker_id=selected.worker_id if selected else None,
            device_strategy=device,
            prompt_token_budget=prompt_budget,
            maximum_new_tokens=output_tokens,
            memory_strategy="LIVE_HEADROOM_AND_HISTORY",
            offload_strategy=offload,
            attention_strategy="RUNTIME_DEFAULT",
            estimated_vram_required_mb=estimated_vram,
            estimated_ram_required_mb=estimated_ram,
            estimated_oom_risk=risk,
            fit_score=max(0, 100 - risk_points),
            fallback_models=fallbacks if active_policy.allow_model_fallback else [],
            fallback_workers=fallback_workers,
            task_split_allowed=True,
            recovery_policy=recovery,
            next_recovery_action=next_action,
            reasoning_summary=reasoning,
            evidence={
                "free_vram_mb": free_vram,
                "total_vram_mb": total_vram,
                "ram_available_mb": available_ram,
                "model_resident_mb": model_vram,
                "kv_cache_estimate_mb": activation_workspace_mb,
                "activation_workspace_estimate_mb": activation_workspace_mb,
                "historical_oom_rate": historical["oom_rate"],
                "attempt": attempt + 1,
            },
            context_plan=context_plan,
            policy=active_policy,
        )

    @staticmethod
    def _prompt_budget(
        preset: ComputePolicyPreset,
        worker: WorkerComputeState | None,
    ) -> int:
        budget = {
            ComputePolicyPreset.QUALITY_FIRST: 10_000,
            ComputePolicyPreset.BALANCED: 7_000,
            ComputePolicyPreset.MEMORY_SAFE: 4_500,
            ComputePolicyPreset.FAST: 5_500,
        }[preset]
        if worker and worker.max_recommended_prompt_tokens:
            budget = min(
                budget,
                max(1_024, worker.max_recommended_prompt_tokens - 1_024),
            )
        if worker and worker.free_vram_mb and worker.free_vram_mb < 4096:
            budget = min(budget, 3500)
        return budget

    @staticmethod
    def _output_budget(
        task: str,
        preset: ComputePolicyPreset,
        requested: int | None,
        worker: WorkerComputeState | None,
    ) -> int:
        words = len(task.split())
        # Coding tasks return complete raw file envelopes. Even a short task
        # description therefore needs enough room to close one useful file block.
        complexity = 1536 if words < 120 else 2048 if words < 260 else 3072
        if preset == ComputePolicyPreset.QUALITY_FIRST:
            complexity = min(4096, int(complexity * 1.25))
        elif preset == ComputePolicyPreset.MEMORY_SAFE:
            complexity = max(1024, int(complexity * 0.75))
        elif preset == ComputePolicyPreset.FAST:
            complexity = max(1024, int(complexity * 0.8))
        if requested:
            complexity = min(complexity, requested)
        if worker and worker.max_recommended_output_tokens:
            complexity = min(complexity, worker.max_recommended_output_tokens)
        if worker and worker.free_vram_mb < 3072:
            complexity = min(complexity, 1024)
        return max(768, complexity)

    @staticmethod
    def _model_vram(metadata: dict[str, Any], quantization: str) -> int:
        estimates = metadata.get("safe_vram_mb") or {}
        if estimates.get(quantization):
            return int(estimates[quantization])
        parameters = float(metadata.get("parameter_count_b") or 0)
        multiplier = {"4bit": 0.75, "8bit": 1.2, "bf16": 2.4}.get(quantization, 1.5)
        return int(parameters * 1024 * multiplier) if parameters else 3072

    @staticmethod
    def _parameter_count(model_id: str) -> float:
        lowered = model_id.lower()
        for size in (72, 32, 14, 7, 3, 1.5, 1.3, 0.5):
            if f"{size:g}b" in lowered:
                return size
        return 3.0

    @staticmethod
    def _history_hint(
        observations: Iterable[Any],
        model_id: str,
        quantization: str,
        gpu_name: str | None,
        prompt_tokens: int,
        output_tokens: int,
    ) -> dict[str, Any]:
        comparable = []
        same_environment = []
        for raw in observations:
            item = raw.model_dump(mode="json") if hasattr(raw, "model_dump") else dict(raw)
            if item.get("model_id") != model_id or item.get("quantization") != quantization:
                continue
            if gpu_name and item.get("gpu_name") not in {gpu_name, "Unknown GPU"}:
                continue
            same_environment.append(item)
            observed_prompt = int(item.get("prompt_tokens") or 0)
            observed_output = int(item.get("output_limit") or 0)
            if observed_prompt and not 0.5 <= prompt_tokens / observed_prompt <= 1.6:
                continue
            if observed_output and not 0.5 <= output_tokens / observed_output <= 1.6:
                continue
            comparable.append(item)
        failures = sum(
            (
                str(item.get("failure_classification") or "").endswith("CUDA_OOM")
                or item.get("failure_classification") == "CONTEXT_TOO_LARGE"
            )
            or str(item.get("result", "")).upper() == "OOM"
            for item in comparable
        )
        oom_rate = failures / len(comparable) if comparable else 0
        peak_vram_mb = max(
            (int(item.get("peak_vram_mb") or 0) for item in same_environment),
            default=0,
        )
        return {
            "samples": len(comparable),
            "oom_rate": round(oom_rate, 3),
            "risk_delta": min(25, int(oom_rate * 25)),
            "peak_vram_mb": peak_vram_mb,
        }

    def _fallback_models(
        self,
        catalog: list[dict[str, Any]],
        selected: dict[str, Any],
        quantization: str,
        selected_vram: int,
        policy: ComputePolicy,
    ) -> list[str]:
        family = selected.get("family")
        candidates = []
        for model in catalog:
            estimate = self._model_vram(model, quantization)
            quality = float((model.get("benchmark_scores") or {}).get("coding", 0))
            if (
                model.get("id") != selected.get("id")
                and estimate < selected_vram
                and quality >= policy.minimum_quality_score
                and quantization in (model.get("quantizations") or [])
            ):
                family_bonus = (
                    1 if policy.prefer_same_model_family and model.get("family") == family else 0
                )
                candidates.append((family_bonus, quality, -estimate, str(model.get("id"))))
        candidates.sort(reverse=True)
        return [item[3] for item in candidates[:3]]

    @staticmethod
    def _next_recovery(
        attempt: int,
        previous_failure: str | None,
        policy: ComputePolicy,
    ) -> RecoveryAction | None:
        if previous_failure in {"INVALID_JSON", "INVALID_MODEL_OUTPUT", "OUTPUT_TRUNCATED"}:
            return RecoveryAction.SPLIT_TASK
        memory_failure = (
            str(previous_failure or "").endswith("CUDA_OOM")
            or previous_failure == "CONTEXT_TOO_LARGE"
        )
        if not memory_failure:
            return None
        if attempt <= 1:
            return RecoveryAction.RETRY_REDUCED_CONTEXT
        if attempt == 2:
            return RecoveryAction.SPLIT_TASK
        if policy.allow_model_fallback:
            return RecoveryAction.FALLBACK_MODEL
        if policy.allow_cpu_offload:
            return RecoveryAction.RETRY_WITH_CPU_OFFLOAD
        return RecoveryAction.ESCALATE_USER
