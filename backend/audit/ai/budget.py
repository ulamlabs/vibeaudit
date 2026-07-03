"""Cumulative dollar-cost guard for an audit run.

`CostBudgetCallback` is a LangChain callback that estimates and accumulates the USD
cost of every model call and aborts the run once a budget is exceeded. It is attached
to the *model instance* (`model.callbacks = [handler]`) rather than passed via the
invoke `config`, because deepagents does not reliably forward config-level callbacks to
subagent LLM calls. The orchestrator and all specialist subagents share one model
instance, so a single handler prices the whole run.

The guard is proactive: `on_chat_model_start` estimates the pending call's cost with
`litellm.token_counter` and refuses to start it if that would breach the budget, so in
the common case the run stops *before* the offending call. `on_llm_end` reconciles with
the provider's real token usage (including prompt-cache buckets). The hard overshoot
bound is one call's actual cost, itself bounded by the context window + `AI_MAX_TOKENS`.

Cost math is delegated to litellm's `cost_per_token`, which expects `prompt_tokens` as
the *total* input (cache tokens included) and subtracts the cache buckets internally.
"""

import logging
import threading

import litellm
from django.conf import settings
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import convert_to_openai_messages
from langchain_core.outputs import LLMResult

logger = logging.getLogger(__name__)

# Used when a model's max output length is unknown and AI_MAX_TOKENS is unset.
_FALLBACK_MAX_OUTPUT_TOKENS = 8192


class CostBudgetExceeded(Exception):
    """Raised when an audit run's cumulative USD cost exceeds its budget."""

    def __init__(self, used: float, budget: float) -> None:
        self.used = used
        self.budget = budget
        super().__init__(f"Cost budget exceeded: ${used:.4f} > ${budget:.4f}")


class CostBudgetCallback(BaseCallbackHandler):
    """Accumulate USD cost across model calls and abort when over budget.

    Attach to the model instance so it covers orchestrator *and* subagent calls (see
    module docstring). `raise_error = True` propagates the exception out of the callback
    manager instead of logging and swallowing it.
    """

    raise_error = True

    def __init__(self, budget_usd: float, model_name: str) -> None:
        self.budget = budget_usd
        self.model_name = model_name
        self.total = 0.0
        self._lock = threading.Lock()

    def _cost(
        self,
        prompt_tokens: int,
        completion_tokens: int,
        cache_read: int = 0,
        cache_creation: int = 0,
    ) -> float:
        prompt_cost, completion_cost = litellm.cost_per_token(
            model=self.model_name,
            custom_llm_provider="anthropic",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cache_read_input_tokens=cache_read,
            cache_creation_input_tokens=cache_creation,
        )
        return prompt_cost + completion_cost

    def _max_output_tokens(self) -> int:
        try:
            info = litellm.get_model_info(self.model_name)
            return info.get("max_output_tokens") or _FALLBACK_MAX_OUTPUT_TOKENS
        except Exception:
            return _FALLBACK_MAX_OUTPUT_TOKENS

    def on_chat_model_start(self, serialized, messages, **kwargs) -> None:
        # Estimate the pending call and refuse to start it if it would breach budget.
        # Cache hits are unknown pre-call, so input is priced as uncached (an
        # overestimate — conservative for a cap). Estimation failures skip the gate;
        # on_llm_end still enforces the real cost.
        try:
            flat = [m for batch in messages for m in batch]
            tools = (kwargs.get("invocation_params") or {}).get("tools")
            est_in = litellm.token_counter(
                model=self.model_name,
                messages=convert_to_openai_messages(flat),
                tools=tools,
            )
            assumed_out = settings.AI_MAX_TOKENS or self._max_output_tokens()
            est_cost = self._cost(est_in, assumed_out)
        except Exception:
            logger.warning("Cost pre-estimate failed; skipping gate", exc_info=True)
            return

        with self._lock:
            projected = self.total + est_cost
            if projected > self.budget:
                raise CostBudgetExceeded(projected, self.budget)

    def on_llm_end(self, response: LLMResult, **kwargs) -> None:
        used = 0.0
        for generations in response.generations:
            for generation in generations:
                usage = getattr(
                    getattr(generation, "message", None), "usage_metadata", None
                )
                if not usage:
                    continue
                details = usage.get("input_token_details") or {}
                used += self._cost(
                    usage.get("input_tokens", 0),
                    usage.get("output_tokens", 0),
                    details.get("cache_read", 0),
                    details.get("cache_creation", 0),
                )

        with self._lock:
            self.total += used
            if self.total > self.budget:
                raise CostBudgetExceeded(self.total, self.budget)
