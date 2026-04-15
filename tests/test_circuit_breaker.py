import time
from unittest.mock import patch

import pytest

from app.services.circuit_breaker import CircuitBreaker, DEGRADED_MESSAGES


class TestCircuitBreakerStates:
    def test_initial_state_is_closed(self):
        cb = CircuitBreaker("test")
        assert cb.state == "closed"
        assert cb.is_available is True

    def test_stays_closed_under_threshold(self):
        cb = CircuitBreaker("test", failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "closed"
        assert cb.is_available is True

    def test_opens_at_threshold(self):
        cb = CircuitBreaker("test", failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "open"
        assert cb.is_available is False

    def test_success_resets_count(self):
        cb = CircuitBreaker("test", failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert cb.state == "closed"
        assert cb._failure_count == 0

    def test_open_transitions_to_half_open_after_timeout(self):
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=1)
        cb.record_failure()
        assert cb.state == "open"

        # Simulate time passing
        cb._last_failure_time = time.monotonic() - 2
        assert cb.state == "half_open"
        assert cb.is_available is True

    def test_half_open_success_closes(self):
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0)
        cb.record_failure()
        cb._last_failure_time = time.monotonic() - 1  # force half_open
        assert cb.state == "half_open"

        cb.record_success()
        assert cb.state == "closed"
        assert cb._failure_count == 0

    def test_half_open_failure_reopens(self):
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=60)
        cb.record_failure()
        # Force half_open by advancing past recovery timeout
        cb._last_failure_time = time.monotonic() - 61
        assert cb.state == "half_open"

        cb.record_failure()
        # Now last_failure_time is recent, so it stays open
        assert cb.state == "open"

    def test_reset(self):
        cb = CircuitBreaker("test", failure_threshold=1)
        cb.record_failure()
        assert cb.state == "open"

        cb.reset()
        assert cb.state == "closed"
        assert cb._failure_count == 0
        assert cb.is_available is True


class TestCircuitBreakerConfig:
    def test_custom_threshold(self):
        cb = CircuitBreaker("test", failure_threshold=5)
        for _ in range(4):
            cb.record_failure()
        assert cb.state == "closed"
        cb.record_failure()
        assert cb.state == "open"

    def test_custom_recovery_timeout(self):
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=120)
        cb.record_failure()
        assert cb.state == "open"
        # Not enough time passed
        cb._last_failure_time = time.monotonic() - 60
        assert cb.state == "open"
        # Enough time passed
        cb._last_failure_time = time.monotonic() - 121
        assert cb.state == "half_open"


class TestDegradedMessages:
    def test_all_locales_present(self):
        assert "ru" in DEGRADED_MESSAGES
        assert "en" in DEGRADED_MESSAGES
        assert "kk" in DEGRADED_MESSAGES

    def test_messages_not_empty(self):
        for locale, msg in DEGRADED_MESSAGES.items():
            assert len(msg) > 10, f"Degraded message for {locale} too short"
