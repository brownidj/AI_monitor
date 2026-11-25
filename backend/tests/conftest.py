import pytest

from app.collectors.openai_costs import CollectorError


@pytest.fixture(autouse=True)
def no_real_codex_account_queries(monkeypatch):
    async def unavailable(*args, **kwargs):
        raise CollectorError("Codex is unavailable in this test.")

    monkeypatch.setattr("app.overview.collect_codex", unavailable)
