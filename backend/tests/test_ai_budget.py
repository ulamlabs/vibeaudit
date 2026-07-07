from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from litellm import cost_per_token

from audit.ai.budget import CostBudgetCallback, CostBudgetExceeded

MODEL = "claude-sonnet-4-6"


def _llm_result(input_tokens, output_tokens, cache_read=0, cache_creation=0):
    msg = AIMessage(
        content="done",
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "input_token_details": {
                "cache_read": cache_read,
                "cache_creation": cache_creation,
            },
        },
    )
    return LLMResult(generations=[[ChatGeneration(message=msg)]])


def test_on_llm_end_prices_cache_reads_cheaply() -> None:
    # 95k of the 100k input tokens are cache reads (~0.1x price). The accumulated
    # cost must match litellm pricing with the cache bucket applied, NOT the full
    # input rate — this guards the prompt_tokens/cache accounting convention.
    cb = CostBudgetCallback(budget_usd=1000.0, model_name=MODEL)
    cb.on_llm_end(_llm_result(100_000, 2_000, cache_read=95_000))

    p, c = cost_per_token(
        model=MODEL,
        custom_llm_provider="anthropic",
        prompt_tokens=100_000,
        completion_tokens=2_000,
        cache_read_input_tokens=95_000,
    )
    expected = p + c
    full_p, full_c = cost_per_token(
        model=MODEL,
        custom_llm_provider="anthropic",
        prompt_tokens=100_000,
        completion_tokens=2_000,
    )
    assert cb.total == pytest.approx(expected)
    # Cache discount is dramatic; without it the extraction would land near full price.
    assert cb.total < (full_p + full_c) * 0.5


def test_on_llm_end_over_budget_raises() -> None:
    cb = CostBudgetCallback(budget_usd=0.01, model_name=MODEL)
    with pytest.raises(CostBudgetExceeded):
        cb.on_llm_end(_llm_result(1_000_000, 100_000))


def test_on_llm_end_missing_usage_is_zero() -> None:
    cb = CostBudgetCallback(budget_usd=1.0, model_name=MODEL)
    msg = AIMessage(content="no usage")
    cb.on_llm_end(LLMResult(generations=[[ChatGeneration(message=msg)]]))
    assert cb.total == 0.0


def test_track_only_accumulates_without_raising_when_no_budget() -> None:
    # budget 0 => enforce disabled: the callback is a pure accumulator.
    cb = CostBudgetCallback(budget_usd=0.0, model_name=MODEL)
    assert cb.enforce is False
    cb.on_llm_end(_llm_result(1_000_000, 100_000))  # far over any real budget; no raise
    assert cb.total > 0.0
    assert cb.tracked is True


def test_tracked_stays_false_until_a_result_is_seen() -> None:
    cb = CostBudgetCallback(budget_usd=0.0, model_name=MODEL)
    assert cb.tracked is False


def test_pre_call_gate_refuses_when_estimate_breaches_budget() -> None:
    # Tiny prompt, but assumed worst-case output alone (~64k tokens) blows a $0.01 cap.
    cb = CostBudgetCallback(budget_usd=0.01, model_name=MODEL)
    with pytest.raises(CostBudgetExceeded):
        cb.on_chat_model_start({}, [[HumanMessage("audit this repo")]])


def test_pre_call_gate_allows_when_estimate_fits() -> None:
    cb = CostBudgetCallback(budget_usd=1000.0, model_name=MODEL)
    cb.on_chat_model_start({}, [[HumanMessage("audit this repo")]])  # no raise
    assert cb.total == 0.0  # pre-call gate never mutates accumulated spend


def test_pre_call_gate_converts_messages_to_openai_format() -> None:
    cb = CostBudgetCallback(budget_usd=1000.0, model_name=MODEL)
    with patch("audit.ai.budget.litellm.token_counter", return_value=1) as tc:
        cb.on_chat_model_start({}, [[HumanMessage("hi")]])
    passed = tc.call_args.kwargs["messages"]
    assert passed == [{"role": "user", "content": "hi"}]


def test_pre_call_estimation_failure_is_non_fatal() -> None:
    cb = CostBudgetCallback(budget_usd=0.0, model_name=MODEL)
    with patch(
        "audit.ai.budget.litellm.token_counter", side_effect=RuntimeError("boom")
    ):
        cb.on_chat_model_start({}, [[HumanMessage("hi")]])  # must not raise
    assert cb.total == 0.0
