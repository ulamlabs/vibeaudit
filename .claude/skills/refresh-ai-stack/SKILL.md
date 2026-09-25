---
name: refresh-ai-stack
description: Upgrade deepagents and the LangChain/LiteLLM stack, re-sync the supported model list, and open a PR.
disable-model-invocation: true
---

# Refresh the AI stack

The supported models (`AVAILABLE_AI_MODELS` default in `backend/vibeaudit/settings.py`) are only as good as two upstream registries:

- **deepagents harness profiles**: per-model prompt/tool tuning keyed `provider:model`. A model without a profile still runs, but untuned.
- **litellm `model_cost`**: `audit/ai/budget.py` prices every call through it. A model missing here breaks the cost budget.

The **invariant**: every default model has a `litellm.model_cost` entry. A harness profile is a bonus, not a gate: deepagents lags new Claude releases, and our provider-wide `anthropic` profile applies either way.

Work from `backend/`, on a branch `chore/refresh-ai-stack-<YYYY-MM>`.

## 1. Snapshot before

Record the current versions and registries for the PR body:

```sh
uv pip list | grep -iE 'deepagents|langchain|langgraph|litellm|anthropic'
uv run python -c "
from deepagents.profiles.harness.harness_profiles import _HARNESS_PROFILES, _ensure_harness_profiles_loaded
_ensure_harness_profiles_loaded(); print(sorted(_HARNESS_PROFILES))"
```

Done when both outputs are saved.

## 2. Upgrade

Upgrade only the AI stack; Dependabot owns every other dependency and ignores these (`.github/dependabot.yml`):

```sh
uv lock \
  --upgrade-package anthropic --upgrade-package deepagents --upgrade-package litellm \
  --upgrade-package langsmith --upgrade-package langchain --upgrade-package langchain-core \
  --upgrade-package langchain-anthropic --upgrade-package langchain-google-genai \
  --upgrade-package langchain-protocol --upgrade-package langgraph \
  --upgrade-package langgraph-checkpoint --upgrade-package langgraph-prebuilt \
  --upgrade-package langgraph-sdk
uv sync --group dev
```

The AI stack is `anthropic`, `deepagents`, `litellm`, `langsmith`, and every `langchain*` / `langgraph*` package. `uv lock` takes names, not globs, so first check `uv.lock` for `langchain*` / `langgraph*` packages missing above, and add them to the command and to this list. If `deepagents` crosses a minor version (0.x → 0.y), raise the floor in `pyproject.toml` to the new version. If a non-AI package must move for the AI stack to resolve, let it, and name it in the PR body.

Done when `uv.lock` is updated and sync succeeds.

## 3. Re-read deepagents where we touch internals

`audit/ai/runner.py` imports `HarnessProfile`, `GeneralPurposeSubagentProfile`, `register_harness_profile`, `create_deep_agent`, `SubAgent` and the backends. `register_harness_profile("anthropic", ...)` merges into the built-in per-model profiles, so its semantics matter. Read the changelog between old and new versions (GitHub releases of `langchain-ai/deepagents`) and the source in `.venv/lib/python3.*/site-packages/deepagents/profiles/`. List every change to these symbols, to profile merge order, and to the recursion/subagent defaults referenced in `settings.py` comments.

Done when each symbol above is marked unchanged or changed-with-note.

## 4. Re-sync the model list

Re-run the snapshot command from step 1. List the `claude-*` models in `litellm.model_cost` (bare aliases, not dated or provider-suffixed IDs), and check each current default is still there.

The default holds exactly the newest priced model in each tier (haiku / sonnet / opus), in that order. A newer model replaces the older one in its tier. In the PR body, mark each default with whether it has an `anthropic:<model>` harness profile.

Update the `AVAILABLE_AI_MODELS` default; it is the only place model names live in app code. It is a default, not a rollout: deployments that set the `AVAILABLE_AI_MODELS` env var keep their own list. Existing `AuditSuite` rows point at model names, and a replaced model fails those suites' runs (`audit/tasks.py`) on deployments that use the default. Name every replaced model in the PR body under **Replaced models**, telling self-hosters to switch those suites in the admin or keep the model via the `AVAILABLE_AI_MODELS` env var. This alone doesn't make the PR a draft. Suites are deployment data, so the refresh ships with no migrations (`uv run python manage.py makemigrations --check` stays clean).

Done when the invariant holds for every model in the new default.

## 5. Verify

Run exactly what CI runs:

```sh
uv sync --locked --group dev
uvx ruff check .
uv run mypy audit github_app vibeaudit
uv run pytest
```

Fix breakage caused by the upgrade. Tests hard-code model names (`grep -rn "claude-" tests`); a test still passes on a dropped model if litellm prices it, so update them to the current defaults anyway. If a failure needs a design decision rather than a mechanical fix, stop fixing, and record it in the PR body under **Needs human**.

Done when all four commands are green, or every red one is written up under **Needs human**.

## 6. Hand off

Commit all changes on the branch: write the message to a file with the Write tool and run `git commit -F <file>`. Write the PR body holding:

- version table (before → after) for the AI packages
- model list diff, with the reason for each change and each model's harness-profile status
- **Replaced models**, if any
- deepagents internals notes from step 3
- **Action required** and **Needs human**, if any

If the upgrade changed nothing, there is no PR: skip the commit, and in CI write a one-line note to `$PR_BODY_FILE` saying the stack is current (CI shows it in the run summary; it tells "nothing to do" apart from a stuck run). Locally, just report it and stop.

The PR is a **draft** whenever **Action required** or **Needs human** has content. Then take one branch:

- **`$PR_BODY_FILE` is set** (CI): write the body to that path; if the PR is a draft, also create `$PR_DRAFT_FILE`. Then stop. CI pushes and opens the PR; you have no push access.
- **Otherwise** (local): push, and `gh pr create` with the body, adding `--draft` if the PR is a draft.

Done when the commit exists and the body is in the PR or in `$PR_BODY_FILE`.
