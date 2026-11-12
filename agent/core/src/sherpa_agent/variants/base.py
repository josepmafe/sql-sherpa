"""What every variant shares (agent spec §5.4): the context it runs in, its protocol and the run limits (§5.3)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Final, Protocol

from pydantic import BaseModel, ConfigDict
from pydantic_ai import NativeOutput
from pydantic_ai.models import Model
from pydantic_ai.toolsets import AbstractToolset
from pydantic_ai.usage import RunUsage, UsageLimits

from sherpa_agent.outcomes import AnswerOutcome, Correction, Draft, PlanOutcome, Step
from sherpa_agent.settings import AgentSettings

# The one switch for how structured output is requested; the mode is verified against Bedrock in agent-design-3b.
OUTPUT_MODE: Final[type[NativeOutput[Any]]] = NativeOutput
OUTPUT_RETRIES: Final = 2  # §5.2: validator retries before the run ends
TOOL_CALLS_PER_AGENT: Final = 15  # §5.3
REQUEST_LIMIT: Final = 40  # §5.3: model requests per question
TOTAL_TOKENS_LIMIT: Final = 400_000  # §5.3: tokens per question, cumulative


class VariantContext(BaseModel):
    """One question's run environment. `usage` is shared by every agent run of the question: for `answer` it must
    already include `draft.usage`."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    settings: AgentSettings
    toolset: AbstractToolset[Any]  # the MCP toolset, unwrapped
    model_for: Callable[[str], Model]
    usage: RunUsage
    on_progress: Callable[[Step], Awaitable[None]] | None = None

    def usage_limits(self) -> UsageLimits:
        """The limits of one agent run: the tool calls count from where the shared usage stands (§5.3)."""
        return UsageLimits(
            request_limit=REQUEST_LIMIT,
            total_tokens_limit=TOTAL_TOKENS_LIMIT,
            tool_calls_limit=self.usage.tool_calls + TOOL_CALLS_PER_AGENT,
        )


class Variant(Protocol):
    """A variant leaves `stats` unset on its outcomes; `AgentService` fills it in."""

    name: str

    async def plan(self, question: str, corrections: Sequence[Correction], ctx: VariantContext) -> PlanOutcome: ...

    async def answer(self, draft: Draft, ctx: VariantContext) -> AnswerOutcome: ...
