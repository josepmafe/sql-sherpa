"""Model factory (agent spec §2): the only place that knows a provider's prefix and its effort field."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import boto3
from botocore.config import Config
from pydantic_ai.models.anthropic import AnthropicModel, AnthropicModelSettings
from pydantic_ai.models.bedrock import BedrockConverseModel, BedrockModelSettings
from pydantic_ai.providers.bedrock import BedrockProvider

from sherpa_agent import AgentError

if TYPE_CHECKING:
    from pydantic_ai.models import Model
    from pydantic_ai.settings import ModelSettings

    from sherpa_agent.settings import AgentSettings, Effort

BEDROCK_PREFIX: Final = "bedrock:"
ANTHROPIC_PREFIX: Final = "anthropic:"
# Botocore retries throttling itself; PydanticAI does not retry model calls on top of it (CLAUDE.md).
BEDROCK_CONFIG: Final = Config(retries={"mode": "adaptive", "max_attempts": 5}, connect_timeout=5)
MAX_TOKENS: Final = 16_000
UNSUPPORTED_MODEL: Final = "Unsupported model {model!r}: use '{bedrock}<id>' or '{anthropic}<id>'."


def build_model(model_string: str, settings: AgentSettings) -> Model:
    match model_string.partition(":"):
        case ("bedrock", ":", model_id) if model_id:
            client = boto3.Session(profile_name=settings.aws_profile).client(
                "bedrock-runtime", region_name=settings.aws_region, config=BEDROCK_CONFIG
            )
            return BedrockConverseModel(model_id, provider=BedrockProvider(bedrock_client=client))
        case ("anthropic", ":", model_id) if model_id:
            return AnthropicModel(model_id)  # the provider reads ANTHROPIC_API_KEY itself
        case _:
            raise _unsupported(model_string)


def model_settings(model_string: str, effort: Effort) -> ModelSettings:
    """Effort and the token ceiling only: never temperature, top_p or tool_choice (the models reject or ignore them)."""
    match model_string.partition(":"):
        case ("bedrock", ":", _):
            return BedrockModelSettings(
                bedrock_additional_model_requests_fields={"output_config": {"effort": effort}},
                max_tokens=MAX_TOKENS,
            )
        case ("anthropic", ":", _):
            return AnthropicModelSettings(anthropic_effort=effort, max_tokens=MAX_TOKENS)
        case _:
            raise _unsupported(model_string)


def _unsupported(model_string: str) -> AgentError:
    return AgentError(UNSUPPORTED_MODEL.format(model=model_string, bedrock=BEDROCK_PREFIX, anthropic=ANTHROPIC_PREFIX))
