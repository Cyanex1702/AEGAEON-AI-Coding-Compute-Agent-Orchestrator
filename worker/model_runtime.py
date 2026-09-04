from __future__ import annotations

import gc
import time
from fnmatch import fnmatch
from typing import Any, Protocol

from aegaeon.models.results import GeneratedChangeSet
from aegaeon.models.structured_output import parse_model_response
from aegaeon.protocol.schemas import ModelGeneratePayload, WorkerModel


class LocalModelRuntime(Protocol):
    model_id: str
    runtime_name: str
    quantization: str
    minimum_vram_mb: int

    def load(self, hardware: dict[str, Any]) -> None: ...

    def generate(self, payload: ModelGeneratePayload) -> GeneratedChangeSet: ...

    def registration(self) -> WorkerModel: ...


class GenerationOutput:
    def __init__(self, text: str, prompt_tokens: int, generated_tokens: int) -> None:
        self.text = text
        self.prompt_tokens = prompt_tokens
        self.generated_tokens = generated_tokens


def generate_transformers_text(
    tokenizer: Any,
    model: Any,
    messages: list[dict[str, str]],
    maximum_new_tokens: int,
) -> GenerationOutput:
    """Generate from a structured BatchEncoding and decode only its continuation."""
    inputs = tokenizer.apply_chat_template(
        messages,
        add_generation_prompt=True,
        tokenize=True,
        return_tensors="pt",
        return_dict=True,
    )
    input_embeddings = getattr(model, "get_input_embeddings", lambda: None)()
    input_device = getattr(getattr(input_embeddings, "weight", None), "device", model.device)
    inputs = inputs.to(input_device)
    try:
        prompt_length = int(inputs["input_ids"].shape[-1])
    except (KeyError, TypeError, AttributeError) as exc:
        raise RuntimeError("tokenizer did not return structured input_ids") from exc
    output = model.generate(
        **inputs,
        max_new_tokens=maximum_new_tokens,
        min_new_tokens=min(32, maximum_new_tokens),
        do_sample=False,
        pad_token_id=tokenizer.eos_token_id,
    )
    generated_tokens = int(output.shape[-1] - prompt_length)
    generated = output[0][prompt_length:]
    text = tokenizer.decode(generated, skip_special_tokens=True)
    return GenerationOutput(text, prompt_length, generated_tokens)


def cleanup_temporary_memory() -> None:
    """Release Python and CUDA allocator caches without masking an active exception."""
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except (ImportError, RuntimeError):
        pass


def generate_transformers_text_adaptive(
    tokenizer: Any,
    model: Any,
    messages: list[dict[str, str]],
    maximum_new_tokens: int,
) -> GenerationOutput:
    """Generate while measuring resources and releasing all temporary tensors."""
    inputs: Any = None
    output: Any = None
    generated: Any = None
    torch: Any = None
    started = time.monotonic()
    try:
        try:
            import torch as torch_module

            torch = torch_module
            if torch.cuda.is_available():
                torch.cuda.reset_peak_memory_stats()
        except (ImportError, RuntimeError):
            torch = None
        inputs = tokenizer.apply_chat_template(
            messages,
            add_generation_prompt=True,
            tokenize=True,
            return_tensors="pt",
            return_dict=True,
        )
        input_embeddings = getattr(model, "get_input_embeddings", lambda: None)()
        input_device = getattr(getattr(input_embeddings, "weight", None), "device", model.device)
        inputs = inputs.to(input_device)
        try:
            prompt_length = int(inputs["input_ids"].shape[-1])
        except (KeyError, TypeError, AttributeError) as exc:
            raise RuntimeError("tokenizer did not return structured input_ids") from exc
        output = model.generate(
            **inputs,
            max_new_tokens=maximum_new_tokens,
            min_new_tokens=min(32, maximum_new_tokens),
            do_sample=False,
            pad_token_id=tokenizer.eos_token_id,
        )
        generated_tokens = int(output.shape[-1] - prompt_length)
        generated = output[0][prompt_length:]
        text = tokenizer.decode(generated, skip_special_tokens=True)
        result = GenerationOutput(text, prompt_length, generated_tokens)
        result.peak_vram_mb = (
            int(torch.cuda.max_memory_allocated() / 1_048_576)
            if torch is not None and torch.cuda.is_available()
            else 0
        )
        result.elapsed_seconds = round(time.monotonic() - started, 3)
        return result
    finally:
        del inputs, output, generated
        cleanup_temporary_memory()


class TransformersRuntime:
    """Optional worker-side adapter; heavyweight dependencies stay on the worker."""

    runtime_name = "transformers"

    def __init__(
        self,
        model_id: str,
        quantization: str = "4bit",
        minimum_vram_mb: int = 3_072,
        max_context: int = 32_768,
        hf_token: str = "",
    ) -> None:
        self.model_id = model_id
        self.quantization = quantization
        self.minimum_vram_mb = minimum_vram_mb
        self.max_context = max_context
        self.hf_token = hf_token or None
        self.tokenizer: Any = None
        self.model: Any = None
        self.last_metrics: dict[str, int] = {}
        self.compute_profile: dict[str, Any] = {}
        self.hardware: dict[str, Any] = {}

    def load(self, hardware: dict[str, Any]) -> None:
        self.hardware = hardware
        if self.model is not None and self.tokenizer is not None:
            return
        gpu = hardware.get("gpu", {})
        actual = int(gpu.get("vram_mb", 0))
        available_ram = int(hardware.get("ram_available_mb") or hardware.get("ram_mb", 0))
        if actual < self.minimum_vram_mb and available_ram < self.minimum_vram_mb * 2:
            raise RuntimeError(
                f"{self.model_id} needs about {self.minimum_vram_mb} MB safe VRAM; "
                f"this worker reports {actual} MB VRAM and insufficient RAM for offload"
            )
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
        except ImportError as exc:
            raise RuntimeError(
                "Install transformers, accelerate, torch, and bitsandbytes on this model worker"
            ) from exc
        quantization_config = None
        if self.quantization == "4bit":
            quantization_config = BitsAndBytesConfig(
                load_in_4bit=True, bnb_4bit_compute_dtype=torch.float16
            )
        elif self.quantization == "8bit":
            quantization_config = BitsAndBytesConfig(load_in_8bit=True)
        self.tokenizer = AutoTokenizer.from_pretrained(self.model_id, token=self.hf_token)
        self.model = AutoModelForCausalLM.from_pretrained(
            self.model_id,
            token=self.hf_token,
            device_map="auto",
            torch_dtype="auto",
            quantization_config=quantization_config,
        )
        try:
            self._profile_loaded_model(torch)
        except torch.OutOfMemoryError as exc:
            cleanup_temporary_memory()
            raise RuntimeError(f"MODEL_LOAD_CUDA_OOM: {exc}") from exc

    def _profile_loaded_model(self, torch: Any) -> None:
        """Smoke-test the loaded model and advertise conservative task headroom."""
        allocated = (
            int(torch.cuda.memory_allocated() / 1_048_576) if torch.cuda.is_available() else 0
        )
        reserved = (
            int(torch.cuda.memory_reserved() / 1_048_576)
            if torch.cuda.is_available()
            else allocated
        )
        smoke = generate_transformers_text_adaptive(
            self.tokenizer,
            self.model,
            [{"role": "user", "content": "Return an empty JSON object."}],
            1,
        )
        gpu = self.hardware.get("gpu", {})
        total = int(gpu.get("vram_mb") or gpu.get("total_mb") or 0)
        free = max(0, int(gpu.get("free_mb") or total - reserved))
        max_output = 1024 if free < 3072 else 1536 if free < 5120 else 2048 if free < 8192 else 3072
        max_prompt = 4096 if free < 3072 else 8192 if free < 6144 else 16384
        ratio = free / total if total else 0
        risk = "LOW" if ratio >= 0.4 else "MODERATE" if ratio >= 0.25 else "HIGH"
        self.compute_profile = {
            "memory_footprint_mb": max(allocated, reserved),
            "smoke_test_peak_vram_mb": int(getattr(smoke, "peak_vram_mb", 0)),
            "max_recommended_prompt_tokens": min(self.max_context, max_prompt),
            "max_recommended_output_tokens": max_output,
            "memory_risk_profile": risk,
            "supports_cpu_offload": True,
            "supports_multi_gpu": False,
            "available_task_headroom_mb": free,
        }

    def generate(self, payload: ModelGeneratePayload) -> GeneratedChangeSet:
        if self.model is None or self.tokenizer is None:
            raise RuntimeError("model runtime is not loaded")
        system = (
            "You are an AEGAEON bounded coding worker. Return source files in AEGAEON_RESPONSE_V1 "
            "format, never JSON or Markdown. Begin with AEGAEON_RESPONSE_V1 and a one-line "
            "SUMMARY. Wrap each raw file as <<<FILE:relative/path>>>, then its exact content, "
            "then <<<END_FILE>>>. Finish with <<<END_RESPONSE>>>. Canonical contracts are "
            "Core-owned and read-only. Prefer a small complete result. Never return absolute, "
            "hidden, .git, backslash, or .. paths. Do not add a preface, Markdown fence, or "
            "text after <<<END_RESPONSE>>>; stop immediately after the final marker."
        )
        original_request = payload.model_dump_json(indent=2)
        last_error: ValueError | None = None
        for attempt in range(3):
            correction = (
                "\n\nThe last response was unusable. Generate a fresh response; do not repair "
                f"or repeat it. Validation reason: {last_error}. Return one complete file first."
                if last_error is not None
                else ""
            )
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": original_request + correction},
            ]
            text = self._generate_text(messages, payload.constraints.maximum_new_tokens)
            try:
                return self._validate_result(text, payload)
            except ValueError as exc:
                last_error = exc
                requested_tokens = int(self.last_metrics.get("requested_output_tokens") or 0)
                if (
                    requested_tokens > 0
                    and int(self.last_metrics.get("generated_tokens") or 0) >= requested_tokens
                ):
                    raise ValueError(
                        f"OUTPUT_TRUNCATED: generation reached its {requested_tokens} token limit"
                    ) from exc
                if attempt == 2:
                    break
        raise ValueError(f"local model output was still invalid after 3 attempts: {last_error}")

    def _generate_text(self, messages: list[dict[str, str]], maximum_new_tokens: int) -> str:
        encoded = self.tokenizer.apply_chat_template(
            messages,
            add_generation_prompt=True,
            tokenize=True,
            return_tensors="pt",
        )
        prompt_tokens = int(encoded.shape[-1])
        available_output = self.max_context - prompt_tokens - 128
        if available_output < 128:
            raise ValueError(
                f"CONTEXT_TOO_LARGE: {prompt_tokens} prompt tokens leave no safe output budget"
            )
        requested_output = min(maximum_new_tokens, available_output)
        generated = generate_transformers_text_adaptive(
            self.tokenizer, self.model, messages, requested_output
        )
        self.last_metrics = {
            "prompt_tokens": generated.prompt_tokens,
            "generated_tokens": generated.generated_tokens,
            "requested_output_tokens": requested_output,
            "peak_vram_mb": getattr(generated, "peak_vram_mb", 0),
            "elapsed_seconds": getattr(generated, "elapsed_seconds", 0),
        }
        return generated.text

    def _validate_result(self, text: str, payload: ModelGeneratePayload) -> GeneratedChangeSet:
        result = GeneratedChangeSet.model_validate(self._json_object(text))
        if len(result.files) > payload.constraints.maximum_files:
            raise ValueError("model returned too many files")
        total = len(result.model_dump_json().encode("utf-8"))
        if total > payload.constraints.maximum_output_bytes:
            raise ValueError("model output exceeds the configured byte limit")
        scopes = payload.context.permitted_file_scopes
        if scopes != ["**/*"]:
            for item in result.files:
                if not any(fnmatch(item.path, pattern) for pattern in scopes):
                    raise ValueError(
                        f"model returned a file outside its permitted scope: {item.path}"
                    )
        return result

    def registration(self) -> WorkerModel:
        return WorkerModel(
            id=self.model_id,
            provider="huggingface",
            loaded=self.model is not None,
            runtime=self.runtime_name,
            quantization=self.quantization,
            status="ready" if self.model is not None else "loading",
            context_length=self.max_context,
            task_scores={"general coding": 75, "repair": 70},
            memory_footprint_mb=int(self.compute_profile.get("memory_footprint_mb", 0)),
            max_recommended_prompt_tokens=int(
                self.compute_profile.get("max_recommended_prompt_tokens", 0)
            ),
            max_recommended_output_tokens=int(
                self.compute_profile.get("max_recommended_output_tokens", 0)
            ),
            memory_risk_profile=str(self.compute_profile.get("memory_risk_profile", "UNKNOWN")),
            supports_cpu_offload=bool(self.compute_profile.get("supports_cpu_offload", False)),
            supports_multi_gpu=bool(self.compute_profile.get("supports_multi_gpu", False)),
            smoke_test_peak_vram_mb=int(self.compute_profile.get("smoke_test_peak_vram_mb", 0)),
        )

    @staticmethod
    def _json_object(text: str) -> dict[str, Any]:
        return parse_model_response(text)
