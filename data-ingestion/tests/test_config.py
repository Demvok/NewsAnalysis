from app.s_scope.config import Settings


def test_settings_default():
    settings = Settings()
    assert settings.database_url.startswith("sqlite:///")
    assert settings.ingestion_concurrency > 0
    assert settings.request_timeout > 0


def test_settings_env_override(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pass@localhost:5432/testdb")
    monkeypatch.setenv("INGESTION_CONCURRENCY", "42")
    settings = Settings()
    assert settings.database_url == "postgresql+psycopg://user:pass@localhost:5432/testdb"
    assert settings.ingestion_concurrency == 42
