"""Base agent class for Excel processing agents."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from core.config import settings
from core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class TokenUsageInfo:
    """Token usage information for an agent result."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: Decimal = field(default_factory=lambda: Decimal("0"))


@dataclass
class AgentResult:
    """Result container for agent operations."""

    success: bool
    data: Any = None
    error: str | None = None
    metadata: dict = field(default_factory=dict)
    usage: TokenUsageInfo | None = None


class BaseAgent(ABC):
    """Abstract base class for all Excel processing agents."""

    def __init__(self, name: str):
        """Initialize base agent.

        Args:
            name: The name identifier for this agent.
        """
        self.name = name
        self.logger = get_logger(f"{__name__}.{name}")

    @abstractmethod
    async def execute(self, *args: Any, **kwargs: Any) -> AgentResult:
        """Execute the agent's main task.

        Returns:
            AgentResult containing success status and data/error.
        """
        raise NotImplementedError

    def _log_start(self, context: dict | None = None) -> None:
        """Log agent execution start."""
        self.logger.info(f"Agent {self.name} starting execution", extra=context or {})

    def _log_complete(self, result: AgentResult) -> None:
        """Log agent execution completion."""
        self.logger.info(
            f"Agent {self.name} completed",
            success=result.success,
            has_error=result.error is not None,
        )

    def _log_error(self, error: Exception) -> None:
        """Log agent execution error."""
        self.logger.error(f"Agent {self.name} failed", error=str(error), exc_info=True)


def get_llm_client():
    """Get the configured LLM client based on settings.

    Returns:
        Configured LangChain LLM instance.

    Raises:
        ValueError: If no API key is configured for the selected provider.
    """
    provider = settings.AGENT_LLM_PROVIDER.lower()
    model = settings.AGENT_LLM_MODEL

    if provider == "gemini":
        if not settings.GOOGLE_GEMINI_API_KEY:
            raise ValueError("GOOGLE_GEMINI_API_KEY is required for Gemini provider")

        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=model,
            google_api_key=settings.GOOGLE_GEMINI_API_KEY,
            temperature=0,
            convert_system_message_to_human=True,
        )

    elif provider == "openai":
        if not settings.OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY is required for OpenAI provider")

        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=model,
            api_key=settings.OPENAI_API_KEY,
            temperature=0,
        )

    else:
        raise ValueError(f"Unsupported LLM provider: {provider}. Use 'gemini' or 'openai'.")
