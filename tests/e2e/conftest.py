"""E2E test configuration — fixtures and skip logic."""
from __future__ import annotations

import os

import pytest

from tests.e2e.harness import E2EHarness


def pytest_addoption(parser):
    parser.addoption(
        "--use-mock-llm",
        action="store_true",
        default=False,
        help="Run E2E with deterministic MockLLMClient (no network/cost).",
    )


def pytest_collection_modifyitems(config, items):
    """Skip all E2E tests if OPENROUTER_API_KEY is not set."""
    if bool(config.getoption("--use-mock-llm")):
        return
    if os.environ.get("OPENROUTER_API_KEY"):
        return
    skip = pytest.mark.skip(reason="OPENROUTER_API_KEY not set — skipping E2E tests")
    for item in items:
        if "e2e" in str(item.fspath):
            item.add_marker(skip)


@pytest.fixture
def e2e_harness(tmp_path, request):
    """Create an E2EHarness with a fresh temp directory."""
    use_mock_llm = bool(request.config.getoption("--use-mock-llm"))
    harness = E2EHarness(work_dir=tmp_path, use_mock_llm=use_mock_llm)
    yield harness
