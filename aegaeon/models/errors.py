from __future__ import annotations


class ModelProviderError(RuntimeError):
    """Base exception for provider connectivity and output failures."""


class ProviderRequestError(ModelProviderError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ProviderOutputError(ModelProviderError):
    """Raised when model output cannot be validated after bounded retries."""
