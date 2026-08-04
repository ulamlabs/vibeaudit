import logging

from django.conf import settings

logger = logging.getLogger(__name__)


def _rate_limiter():
    rps = settings.AI_REQUESTS_PER_SECOND
    if not rps:
        return None
    from langchain_core.rate_limiters import InMemoryRateLimiter

    return InMemoryRateLimiter(requests_per_second=rps, check_every_n_seconds=0.1)


def get_llm(model_name: str, cost_callback=None):
    """Return a model for DeepAgents — a ChatAnthropic instance or a provider:model string."""
    provider = settings.AI_MODEL_PROVIDER

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        kwargs = {
            "model": model_name,
            "api_key": settings.AI_ANTHROPIC_API_KEY,
            "rate_limiter": _rate_limiter(),
        }
        max_tokens = settings.AI_MAX_TOKENS
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        model = ChatAnthropic(**kwargs)
        if cost_callback is not None:
            model.callbacks = [*(model.callbacks or []), cost_callback]
        return model

    if cost_callback is not None:
        logger.warning(
            "Cost callback provided but provider '%s' returns a model string; "
            "cost is untracked and any budget is unenforced (only 'anthropic' is "
            "instrumented).",
            provider,
        )
    return f"{provider}:{model_name}"
