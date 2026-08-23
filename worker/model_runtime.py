from __future__ import annotations

import json
import re
from fnmatch import fnmatch
from typing import Any, Protocol

from aegaeon.models.results import GeneratedChangeSet
from aegaeon.protocol.schemas import ModelGeneratePayload, WorkerModel


class LocalModelRuntime(Protocol):
    model_id: str
    runtime_name: str
    quantization: str
    minimum_vram_mb: int

    def load(self, hardware: dict[str, Any]) -> None: ...

    def generate(self, payload: ModelGeneratePayload) -> GeneratedChangeSet:
        if self.model is None or self.tokenizer is None:
            raise RuntimeError("model runtime is not loaded")
        system = (
            "You are an AEGAEON coding worker. Return only JSON matching "
            '{"summary":"...","files":[{"path":"relative/path","content":"..."}],'
            '"test_commands":[],"notes":[]}. Never return absolute, hidden, .git, or .. paths.'
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": payload.model_dump_json(indent=2)},
        ]
        last_error: ValueError | None = None
        for attempt in range(3):
            text = self._generate_text(messages, payload.constraints.maximum_new_tokens)
            try:
                return self._validate_result(text, payload)
            except ValueError as exc:
                last_error = exc
                if attempt == 2:
                    break
                messages.extend(
                    [
                        {"role": "assistant", "content": text},
                        {
                            "role": "user",
                            "content": (
                                f"Your response failed validation: {exc}. "
                                "Return corrected JSON only, matching the required schema."
                            ),
                        },
                    ]
                )
        raise ValueError(
            f"local model output was still invalid after 3 attempts: {last_error}"
        )

    def _generate_text(self, messages: list[dict[str, str]], maximum_new_tokens: int) -> str:
        inputs = self.tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=True, return_tensors="pt"
        ).to(self.model.device)
        output = self.model.generate(
            inputs,
            max_new_tokens=maximum_new_tokens,
            do_sample=False,
            pad_token_id=self.tokenizer.eos_token_id,
        )
        return self.tokenizer.decode(output[0][inputs.shape[-1] :], skip_special_tokens=True)

    def _validate_result(
        self, text: str, payload: ModelGeneratePayload
    ) -> GeneratedChangeSet:
        result = GeneratedChangeSet.model_validate(self._json_object(text))
        if len(result.files) > payload.constraints.maximum_files:
            raise ValueError("model returned too many files")
        total = sum(len(item.content.encode("utf-8")) for item in result.files)
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

    def registration(self) -> WorkerModel: ...


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

    def load(self, hardware: dict[str, Any]) -> None:
        gpu = hardware.get("gpu", {})
        actual = int(gpu.get("vram_mb", 0))
        if actual < self.minimum_vram_mb:
            raise RuntimeError(
                f"{self.model_id} needs about {self.minimum_vram_mb} MB safe VRAM; "
                f"this worker reports {actual} MB"
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

    def generate(self, payload: ModelGeneratePayload) -> GeneratedChangeSet:
        if self.model is None or self.tokenizer is None:
            raise RuntimeError("model runtime is not loaded")
        system = (
            "You are an AEGAEON coding worker. Return only JSON matching "
            '{"summary":"...","files":[{"path":"relative/path","content":"..."}],'
            '"test_commands":[],"notes":[]}. Never return absolute, hidden, .git, or .. paths.'
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": payload.model_dump_json(indent=2)},
        ]
        last_error: ValueError | None = None
        for attempt in range(3):
            text = self._generate_text(messages, payload.constraints.maximum_new_tokens)
            try:
                return self._validate_result(text, payload)
            except ValueError as exc:
                last_error = exc
                if attempt == 2:
                    break
                messages.extend(
                    [
                        {"role": "assistant", "content": text},
                        {
                            "role": "user",
                            "content": (
                                f"Your response failed validation: {exc}. "
                                "Return corrected JSON only, matching the required schema."
                            ),
                        },
                    ]
                )
        raise ValueError(
            f"local model output was still invalid after 3 attempts: {last_error}"
        )

    def _generate_text(self, messages: list[dict[str, str]], maximum_new_tokens: int) -> str:
        inputs = self.tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=True, return_tensors="pt"
        ).to(self.model.device)
        output = self.model.generate(
            inputs,
            max_new_tokens=maximum_new_tokens,
            do_sample=False,
            pad_token_id=self.tokenizer.eos_token_id,
        )
        return self.tokenizer.decode(output[0][inputs.shape[-1] :], skip_special_tokens=True)

    def _validate_result(
        self, text: str, payload: ModelGeneratePayload
    ) -> GeneratedChangeSet:
        result = GeneratedChangeSet.model_validate(self._json_object(text))
        if len(result.files) > payload.constraints.maximum_files:
            raise ValueError("model returned too many files")
        total = sum(len(item.content.encode("utf-8")) for item in result.files)
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
        )

    @staticmethod
    def _json_object(text: str) -> dict[str, Any]:
        fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text.strip(), re.DOTALL)
        candidate = fence.group(1) if fence else text.strip()
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise ValueError("local model did not return valid structured JSON") from exc
        if not isinstance(value, dict):
            raise ValueError("local model response must be a JSON object")
        return value
