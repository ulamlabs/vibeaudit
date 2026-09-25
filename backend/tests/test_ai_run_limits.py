from decimal import Decimal
from unittest.mock import patch

import pytest
from django.test import override_settings

from audit.ai.budget import CostBudgetCallback
from audit.ai.model import get_llm
from audit.ai.output import SubmittedReport
from audit.ai.runner import _run_orchestrator
from audit.models import AuditSuite

MODEL = "claude-sonnet-5"


@pytest.mark.django_db
@override_settings(AI_RECURSION_LIMIT=100, AI_MAX_RUN_COST_USD=25.0)
def test_resolve_ai_run_limits_falls_back_to_settings() -> None:
    suite = AuditSuite.objects.create(name="S", model=MODEL)
    assert suite.resolve_ai_run_limits() == (100, 25.0)


@pytest.mark.django_db
@override_settings(AI_RECURSION_LIMIT=100, AI_MAX_RUN_COST_USD=25.0)
def test_resolve_ai_run_limits_prefers_suite_cost_override() -> None:
    suite = AuditSuite.objects.create(
        name="S", model=MODEL, max_run_cost_usd=Decimal("3.50")
    )
    assert suite.resolve_ai_run_limits() == (100, 3.5)


@override_settings(AI_MODEL_PROVIDER="anthropic", AI_ANTHROPIC_API_KEY="test-key")
def test_get_llm_attaches_cost_callback_when_provided() -> None:
    cb = CostBudgetCallback(budget_usd=5.0, model_name=MODEL)
    model = get_llm(MODEL, cost_callback=cb)
    assert model.callbacks == [cb]


@override_settings(AI_MODEL_PROVIDER="anthropic", AI_ANTHROPIC_API_KEY="test-key")
def test_get_llm_no_callback_when_none() -> None:
    model = get_llm(MODEL, cost_callback=None)
    assert not model.callbacks


def test_run_orchestrator_threads_cost_callback_and_recursion_limit(tmp_path) -> None:
    cb = CostBudgetCallback(budget_usd=12.5, model_name=MODEL)
    with (
        patch("audit.ai.runner.get_llm", return_value="fake-model") as get_llm_mock,
        patch("audit.ai.runner.create_deep_agent") as create_agent,
    ):
        agent = create_agent.return_value
        agent.invoke.return_value = {
            "structured_response": SubmittedReport(summary="s", markdown="m"),
            "messages": [],
        }
        _run_orchestrator(
            tmp_path,
            [],
            MODEL,
            None,
            run_id=1,
            repo_name="r",
            recursion_limit=50,
            cost_callback=cb,
        )

    get_llm_mock.assert_called_once_with(MODEL, cost_callback=cb)
    config = agent.invoke.call_args.kwargs["config"]
    assert config["recursion_limit"] == 50
