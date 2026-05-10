import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx
import pytest
from asgi_lifespan import LifespanManager
from unittest.mock import AsyncMock, patch

# ---------------------------------------------------------------------------
# Make ASGITransport fire ASGI lifespan events.
#
# httpx 0.27.x's ASGITransport.handle_async_request never sends the
# lifespan scope, so FastAPI's startup/shutdown hooks don't run during tests.
# Subclassing here — at conftest import time, before any test module is
# imported — means "from httpx import ASGITransport" in test files picks up
# this lifespan-aware version automatically.
# ---------------------------------------------------------------------------
_OrigASGITransport = httpx.ASGITransport


class _LifespanASGITransport(_OrigASGITransport):
    async def __aenter__(self):
        self._lifespan_mgr = LifespanManager(self.app)
        await self._lifespan_mgr.__aenter__()
        return self

    async def __aexit__(self, *exc_info):
        await self._lifespan_mgr.__aexit__(*exc_info)
        await self.aclose()


httpx.ASGITransport = _LifespanASGITransport


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
