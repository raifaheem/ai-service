import logging
import time

logger = logging.getLogger(__name__)

_FAILURE_THRESHOLD = 3
_RECOVERY_TIMEOUT = 60  # seconds


class CircuitBreaker:
    """Simple circuit breaker for external service calls.

    States:
    - closed: normal operation, requests pass through
    - open: too many failures, requests are rejected immediately
    - half_open: recovery window expired, allow one test request
    """

    def __init__(
        self, name: str, failure_threshold: int = _FAILURE_THRESHOLD, recovery_timeout: int = _RECOVERY_TIMEOUT
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._failure_count = 0
        self._last_failure_time: float = 0.0
        self._state = "closed"

    @property
    def state(self) -> str:
        if self._state == "open" and time.monotonic() - self._last_failure_time >= self.recovery_timeout:
            self._state = "half_open"
        return self._state

    @property
    def is_available(self) -> bool:
        return self.state != "open"

    def record_success(self) -> None:
        if self._state == "half_open":
            logger.info("Circuit breaker '%s' recovered (half_open -> closed)", self.name)
        self._failure_count = 0
        self._state = "closed"

    def record_failure(self) -> None:
        self._failure_count += 1
        self._last_failure_time = time.monotonic()
        if self._state == "half_open" or self._failure_count >= self.failure_threshold:
            if self._state != "open":
                logger.warning(
                    "Circuit breaker '%s' opened after %d failures",
                    self.name,
                    self._failure_count,
                )
            self._state = "open"

    def reset(self) -> None:
        self._failure_count = 0
        self._state = "closed"
        self._last_failure_time = 0.0


# Singleton circuit breakers for external services
openai_breaker = CircuitBreaker("openai")
qdrant_breaker = CircuitBreaker("qdrant")

DEGRADED_MESSAGES = {
    "ru": "Сервис временно перегружен. Пожалуйста, попробуйте позже.",
    "en": "Service is temporarily overloaded. Please try again later.",
    "kk": "Сервис уақытша шамадан тыс жүктелген. Кейінірек қайталап көріңіз.",
}
