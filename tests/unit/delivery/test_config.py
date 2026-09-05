"""Deployment-environment configuration tests."""

from __future__ import annotations

import pytest

from ai_daily_digest.delivery.api.config import DeliverySettings


def test_settings_load_exact_origin_and_optional_runtime_values() -> None:
    secret = "s" * 32

    settings = DeliverySettings.from_environment(
        {
            "FRONTEND_ORIGIN": "https://ai-daily-digest.onrender.com",
            "DOCS_ENABLED": "false",
            "PAGINATION_CURSOR_SECRET": secret,
        }
    )

    assert settings.frontend_origin == "https://ai-daily-digest.onrender.com"
    assert settings.docs_enabled is False
    assert settings.pagination_cursor_secret == secret.encode()
    assert secret not in repr(settings)


def test_cursor_secret_is_optional_until_a_repository_is_configured() -> None:
    settings = DeliverySettings.from_environment({"FRONTEND_ORIGIN": "http://localhost:3000"})

    assert settings.docs_enabled is True
    assert settings.pagination_cursor_secret is None


@pytest.mark.parametrize(
    "origin",
    [
        "",
        "*",
        "https://*.example.com",
        "https://one.example,https://two.example",
        "https://user:password@example.com",
        "https://example.com/path",
        "https://example.com/",
        "https://example.com?query=yes",
        "http://example.com",
    ],
)
def test_invalid_or_non_exact_frontend_origins_fail_closed(origin: str) -> None:
    with pytest.raises(ValueError, match="FRONTEND_ORIGIN"):
        DeliverySettings.from_environment({"FRONTEND_ORIGIN": origin})


def test_invalid_boolean_and_short_cursor_secret_are_rejected() -> None:
    with pytest.raises(ValueError, match="DOCS_ENABLED"):
        DeliverySettings.from_environment(
            {"FRONTEND_ORIGIN": "https://example.com", "DOCS_ENABLED": "sometimes"}
        )

    with pytest.raises(ValueError, match="PAGINATION_CURSOR_SECRET"):
        DeliverySettings.from_environment(
            {
                "FRONTEND_ORIGIN": "https://example.com",
                "PAGINATION_CURSOR_SECRET": "too-short",
            }
        )
