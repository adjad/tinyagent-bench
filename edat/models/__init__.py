"""tinyagent-bench's model adapter. See docs/benchmark/model-adapter.md."""
from edat.models.base import (ADAPTER_SCHEMA, FIXED_SENSITIVITY, ChatResult, ConfigurationError,
                              EngineError, ModelClient, Sampling, Timing, ToolCall, Usage,
                              parse_arguments)
from edat.models.media import audio_part, image_part, image_url_part, message_text, text_part, user_message
from edat.models.omlx import OmlxClient
from edat.models.openrouter import OpenRouterClient
from edat.models.stub import NullClient, ScriptedClient, ScriptExhausted

__all__ = [
    "ADAPTER_SCHEMA", "FIXED_SENSITIVITY", "ChatResult", "ConfigurationError", "EngineError",
    "ModelClient", "NullClient", "OmlxClient", "OpenRouterClient", "Sampling", "ScriptedClient",
    "ScriptExhausted", "Timing", "ToolCall", "Usage", "parse_arguments",
    "audio_part", "image_part", "image_url_part", "message_text", "text_part", "user_message",
]
