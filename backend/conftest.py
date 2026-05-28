from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def mock_clone_repo_delay():
    with patch("audit.tasks.clone_repo.delay"):
        yield
