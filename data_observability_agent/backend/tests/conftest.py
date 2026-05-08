import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import AsyncMock, patch


@pytest.fixture(autouse=True)
def _mock_embed():
    with patch("rag.embedder.embed", new=AsyncMock(return_value=[0.0] * 1536)):
        yield


@pytest.fixture(autouse=True)
def _mock_classify_topic():
    # Default: on-topic. Per-test patches override this within their own with-block.
    try:
        with patch(
            "guardrails.input_guardrails._classify_topic",
            new=AsyncMock(return_value=True),
        ):
            yield
    except (ModuleNotFoundError, AttributeError):
        yield
