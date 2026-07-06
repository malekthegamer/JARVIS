"""OpenRouter brain — many open-weight models (Llama/Mistral/Qwen/DeepSeek...)
through one key, via OpenRouter's OpenAI-compatible API."""
from __future__ import annotations

from providers.brain.openai_provider import OpenAICompatibleProvider
from providers.registry import register


@register("brain", "openrouter")
class OpenRouterProvider(OpenAICompatibleProvider):
    provider_label = "OpenRouter"
    key_name = "openrouter"
    base_url = "https://openrouter.ai/api/v1"
    model_setting = "brain.models.openrouter"
    default_model = "meta-llama/llama-3.3-70b-instruct:free"
