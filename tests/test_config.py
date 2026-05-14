"""Tests for app.config.Settings and its production-safety validation."""

import pytest
from pydantic import ValidationError

from app.config import Settings


def _base_prod_env() -> dict[str, str]:
    """Minimal env vars for a 'production' Settings that would otherwise validate."""
    return {
        "APP_ENV": "production",
        "ENABLE_DEV_ROUTES": "false",
        "OPENAI_API_KEY": "sk-test-key",
        "SERVICE_TOKEN": "a-strong-random-token-xyz",
        "REDIS_URL": "redis://:secret@redis:6379/0",
        "QDRANT_URL": "http://qdrant:6333",
        # ALLOWED_ORIGINS defaults to "*" on Settings, which prod-safety rejects.
        # Tests that want to exercise the "*" path override this explicitly.
        "ALLOWED_ORIGINS": "https://app.example.com",
        # Clear any JWT_PUBLIC_KEY the dev .env might bleed in — the base prod
        # config does not enable JWT auth. Tests that want JWT enabled set the
        # key + audience + issuer explicitly.
        "JWT_PUBLIC_KEY": "",
        "JWT_AUDIENCE": "",
        "JWT_ISSUER": "",
    }


class TestProdSafety:
    def test_valid_prod_config_passes(self, monkeypatch):
        for k, v in _base_prod_env().items():
            monkeypatch.setenv(k, v)
        settings = Settings()
        assert settings.app_env == "production"
        assert settings.enable_dev_routes is False

    def test_enable_dev_routes_in_prod_raises(self, monkeypatch):
        env = _base_prod_env()
        env["ENABLE_DEV_ROUTES"] = "true"
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        with pytest.raises(ValueError, match="ENABLE_DEV_ROUTES"):
            Settings()

    def test_redis_without_password_in_prod_raises(self, monkeypatch):
        env = _base_prod_env()
        env["REDIS_URL"] = "redis://redis:6379/0"  # no password
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        with pytest.raises(ValueError, match="REDIS_URL.*password"):
            Settings()

    def test_redis_with_password_in_prod_passes(self, monkeypatch):
        env = _base_prod_env()
        env["REDIS_URL"] = "redis://:very-secret@redis:6379/0"
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        Settings()  # no raise

    def test_rediss_with_password_in_prod_passes(self, monkeypatch):
        env = _base_prod_env()
        env["REDIS_URL"] = "rediss://user:pwd@managed-redis.example.com:6379/0"
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        Settings()  # no raise

    @pytest.mark.parametrize(
        "placeholder",
        ["change-me-in-prod", "dev", "test", "test-token", "CHANGEME", "placeholder"],
    )
    def test_service_token_placeholder_in_prod_raises(self, monkeypatch, placeholder):
        env = _base_prod_env()
        env["SERVICE_TOKEN"] = placeholder
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        with pytest.raises(ValueError, match="SERVICE_TOKEN"):
            Settings()

    def test_service_token_rotation_with_one_placeholder_in_prod_raises(self, monkeypatch):
        # Rotation is comma-separated. If ANY listed token is a placeholder, fail.
        env = _base_prod_env()
        env["SERVICE_TOKEN"] = "good-token-xyz,change-me-in-prod"
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        with pytest.raises(ValueError, match="SERVICE_TOKEN"):
            Settings()

    def test_allowed_origins_star_in_prod_raises(self, monkeypatch):
        env = _base_prod_env()
        env["ALLOWED_ORIGINS"] = "*"
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        with pytest.raises(ValueError, match="ALLOWED_ORIGINS"):
            Settings()

    def test_allowed_origins_star_with_whitespace_in_prod_raises(self, monkeypatch):
        env = _base_prod_env()
        env["ALLOWED_ORIGINS"] = "  *  "
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        with pytest.raises(ValueError, match="ALLOWED_ORIGINS"):
            Settings()

    def test_explicit_origins_in_prod_pass(self, monkeypatch):
        env = _base_prod_env()
        env["ALLOWED_ORIGINS"] = "https://app.example.com,https://admin.example.com"
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        Settings()  # no raise

    def test_dev_env_skips_prod_validations(self, monkeypatch):
        # In dev, placeholder tokens and unauth'd Redis are fine.
        monkeypatch.setenv("APP_ENV", "dev")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("SERVICE_TOKEN", "test-token")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        monkeypatch.setenv("ENABLE_DEV_ROUTES", "true")
        Settings()  # no raise

    @pytest.mark.parametrize("typo", ["prod", "PRODUCTION", "Production", "stg", "live", "prd"])
    def test_app_env_typos_rejected_at_load(self, monkeypatch, typo):
        # Strict Literal whitelist — any value other than dev/staging/production
        # must fail at Settings construction. Previously these silently bypassed
        # _validate_prod_safety.
        monkeypatch.setenv("APP_ENV", typo)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("SERVICE_TOKEN", "test-token")
        with pytest.raises(ValidationError):
            Settings()

    @pytest.mark.parametrize("env", ["dev", "staging", "production"])
    def test_app_env_canonical_values_accepted(self, monkeypatch, env):
        for k, v in _base_prod_env().items():
            monkeypatch.setenv(k, v)
        monkeypatch.setenv("APP_ENV", env)
        Settings()  # no raise

    @pytest.mark.parametrize("placeholder", ["ci-smoke", "smoke", "ci"])
    def test_ci_smoke_token_rejected_in_prod(self, monkeypatch, placeholder):
        # CI-shaped tokens were leaking through the old blocklist. Now rejected.
        env = _base_prod_env()
        env["SERVICE_TOKEN"] = placeholder
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        with pytest.raises(ValueError, match="SERVICE_TOKEN"):
            Settings()

    @pytest.mark.parametrize("short", ["abc", "fifteenchars123", "a" * 15])
    def test_short_service_token_rejected_in_prod(self, monkeypatch, short):
        env = _base_prod_env()
        env["SERVICE_TOKEN"] = short
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        with pytest.raises(ValueError, match="SERVICE_TOKEN"):
            Settings()

    def test_service_token_at_min_length_accepted(self, monkeypatch):
        env = _base_prod_env()
        env["SERVICE_TOKEN"] = "a" * 16
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        Settings()  # no raise


class TestEnvExampleParity:
    """Every env var in Settings must be documented in .env.example (and vice versa)."""

    def test_env_example_covers_all_settings_fields(self):
        from pathlib import Path

        env_example = Path(__file__).resolve().parent.parent / ".env.example"
        content = env_example.read_text(encoding="utf-8")

        # Collect all aliases declared on Settings fields.
        aliases = {field.alias for field in Settings.model_fields.values() if field.alias}

        missing = [alias for alias in aliases if alias + "=" not in content]
        assert not missing, f".env.example missing Settings fields: {missing}"
