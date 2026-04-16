"""Token tracking and cost calculation utilities for LLM calls."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from core.config import settings
from core.logging import get_logger
from core.models.conversation import LLM_PRICING, LLMCallType, LLMUsage

logger = get_logger(__name__)


@dataclass
class TokenUsage:
    """Container for token usage from an LLM call."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    input_cost_usd: Decimal = field(default_factory=lambda: Decimal("0"))
    output_cost_usd: Decimal = field(default_factory=lambda: Decimal("0"))
    total_cost_usd: Decimal = field(default_factory=lambda: Decimal("0"))


@dataclass
class LLMCallResult:
    """Result from an LLM call with usage tracking."""

    content: str
    usage: TokenUsage
    provider: str
    model: str
    latency_ms: int
    success: bool = True
    error: str | None = None
    raw_response: Any = None


def estimate_tokens(text: str) -> int:
    """Estimate token count for text.

    Uses a simple heuristic: ~4 characters per token for English text.
    This is a rough estimate; for accurate counts, use tiktoken for OpenAI
    or the model's tokenizer.

    Args:
        text: The text to estimate tokens for.

    Returns:
        Estimated token count.
    """
    if not text:
        return 0
    # Rough estimate: 1 token ≈ 4 characters for English
    return max(1, len(text) // 4)


def calculate_cost(
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> TokenUsage:
    """Calculate token usage and costs.

    Args:
        provider: LLM provider ('openai' or 'gemini').
        model: Model name.
        input_tokens: Number of input tokens.
        output_tokens: Number of output tokens.

    Returns:
        TokenUsage with calculated costs.
    """
    provider_pricing = LLM_PRICING.get(provider.lower(), {})
    model_pricing = provider_pricing.get(
        model.lower(),
        {"input": Decimal("0"), "output": Decimal("0")}
    )

    # Cost per 1M tokens
    input_cost = (Decimal(input_tokens) / Decimal("1000000")) * model_pricing["input"]
    output_cost = (Decimal(output_tokens) / Decimal("1000000")) * model_pricing["output"]

    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        input_cost_usd=input_cost,
        output_cost_usd=output_cost,
        total_cost_usd=input_cost + output_cost,
    )


def extract_usage_from_response(response: Any, provider: str, model: str) -> TokenUsage:
    """Extract token usage from LLM response.

    Args:
        response: The LLM response object.
        provider: LLM provider name.
        model: Model name.

    Returns:
        TokenUsage with actual or estimated counts.
    """
    input_tokens = 0
    output_tokens = 0

    # Try to extract from LangChain response metadata
    if hasattr(response, "response_metadata"):
        metadata = response.response_metadata

        # OpenAI format
        if "token_usage" in metadata:
            usage = metadata["token_usage"]
            input_tokens = usage.get("prompt_tokens", 0)
            output_tokens = usage.get("completion_tokens", 0)

        # Gemini format
        elif "usage_metadata" in metadata:
            usage = metadata["usage_metadata"]
            input_tokens = usage.get("prompt_token_count", 0)
            output_tokens = usage.get("candidates_token_count", 0)

    # Try usage_metadata directly (newer LangChain versions)
    if hasattr(response, "usage_metadata") and response.usage_metadata:
        usage = response.usage_metadata
        if hasattr(usage, "input_tokens"):
            input_tokens = usage.input_tokens or 0
        if hasattr(usage, "output_tokens"):
            output_tokens = usage.output_tokens or 0

    # If we couldn't extract, estimate from content
    if input_tokens == 0 and output_tokens == 0:
        content = response.content if hasattr(response, "content") else str(response)
        output_tokens = estimate_tokens(content)
        logger.debug(f"Estimated output tokens: {output_tokens}")

    return calculate_cost(provider, model, input_tokens, output_tokens)


class LLMCallTracker:
    """Context manager for tracking LLM calls."""

    def __init__(
        self,
        call_type: LLMCallType | str,
        user_id: str | None = None,
        data_source_id: str | None = None,
        conversation_id: str | None = None,
        message_id: str | None = None,
        excel_schema_id: str | None = None,
    ):
        """Initialize tracker.

        Args:
            call_type: Type of LLM call being made.
            user_id: ID of the user making the call.
            data_source_id: ID of the data source (if applicable).
            conversation_id: ID of the conversation (if applicable).
            message_id: ID of the message (if applicable).
            excel_schema_id: ID of the excel schema (if applicable).
        """
        self.call_type = call_type if isinstance(call_type, str) else call_type.value
        self.user_id = user_id
        self.data_source_id = data_source_id
        self.conversation_id = conversation_id
        self.message_id = message_id
        self.excel_schema_id = excel_schema_id

        self.provider = settings.AGENT_LLM_PROVIDER
        self.model = settings.AGENT_LLM_MODEL

        self.start_time: float | None = None
        self.end_time: float | None = None
        self.usage: TokenUsage | None = None
        self.success = True
        self.error: str | None = None
        self.request_metadata: dict = {}
        self.response_metadata: dict = {}

    def start(self) -> "LLMCallTracker":
        """Start tracking the call."""
        self.start_time = time.time()
        return self

    def end(
        self,
        response: Any = None,
        input_text: str | None = None,
        success: bool = True,
        error: str | None = None,
    ) -> TokenUsage:
        """End tracking and calculate usage.

        Args:
            response: The LLM response object.
            input_text: The input text (for estimation if needed).
            success: Whether the call was successful.
            error: Error message if failed.

        Returns:
            TokenUsage with calculated costs.
        """
        self.end_time = time.time()
        self.success = success
        self.error = error

        if response:
            self.usage = extract_usage_from_response(response, self.provider, self.model)

            # If input tokens still 0 and we have input_text, estimate
            if self.usage.input_tokens == 0 and input_text:
                estimated_input = estimate_tokens(input_text)
                self.usage = calculate_cost(
                    self.provider,
                    self.model,
                    estimated_input,
                    self.usage.output_tokens,
                )
        elif input_text:
            # No response, estimate from input
            self.usage = calculate_cost(
                self.provider,
                self.model,
                estimate_tokens(input_text),
                0,
            )
        else:
            self.usage = TokenUsage()

        return self.usage

    @property
    def latency_ms(self) -> int:
        """Get latency in milliseconds."""
        if self.start_time and self.end_time:
            return int((self.end_time - self.start_time) * 1000)
        return 0

    def to_llm_usage(self) -> LLMUsage:
        """Convert to LLMUsage model for database storage.

        Returns:
            LLMUsage instance ready for database insertion.
        """
        usage = self.usage or TokenUsage()

        return LLMUsage(
            user_id=self.user_id,
            call_type=self.call_type,
            data_source_id=self.data_source_id,
            conversation_id=self.conversation_id,
            message_id=self.message_id,
            excel_schema_id=self.excel_schema_id,
            provider=self.provider,
            model=self.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens,
            input_cost_usd=usage.input_cost_usd,
            output_cost_usd=usage.output_cost_usd,
            total_cost_usd=usage.total_cost_usd,
            request_metadata=self.request_metadata,
            response_metadata=self.response_metadata,
            latency_ms=self.latency_ms,
            success=self.success,
            error_message=self.error,
        )

    def __enter__(self) -> "LLMCallTracker":
        """Enter context manager."""
        return self.start()

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit context manager."""
        if exc_type:
            self.end(success=False, error=str(exc_val))
        elif not self.end_time:
            self.end()
