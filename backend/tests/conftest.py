import os

# Price litellm from its bundled snapshot (no startup GitHub fetch) so cost
# assertions are deterministic. Must be set before litellm is first imported.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "true")

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def no_remote_installation_delete():
    """Suppress the pre_delete signal that calls GitHub when an Installation is deleted."""
    with patch("github_app.signals.delete_installation"):
        yield
