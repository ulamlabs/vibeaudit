from django.conf import settings


def _rate_limiter():
    rps = getattr(settings, "AI_REQUESTS_PER_SECOND", 0)
    if not rps:
        return None
    from langchain_core.rate_limiters import InMemoryRateLimiter

    return InMemoryRateLimiter(requests_per_second=rps, check_every_n_seconds=0.1)


def get_llm():
    """Return a model for DeepAgents — a ChatModel instance for Ollama/Anthropic,
    or a provider:model string for others."""
    provider = settings.AI_MODEL_PROVIDER
    model = settings.AI_MODEL_NAME

    if provider == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=model,
            base_url=settings.AI_OLLAMA_BASE_URL,
            num_ctx=getattr(settings, "AI_OLLAMA_NUM_CTX", 8192),
        )

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        kwargs = dict(
            model=model,
            api_key=settings.AI_ANTHROPIC_API_KEY,
            rate_limiter=_rate_limiter(),
        )
        max_tokens = getattr(settings, "AI_MAX_TOKENS", 0)
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        return ChatAnthropic(**kwargs)

    return f"{provider}:{model}"
