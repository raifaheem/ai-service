import threading
import time
from collections import deque


class Metrics:
    """Thread-safe in-memory metrics collection for the application."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.requests_total: int = 0
        self.requests_by_status: dict[int, int] = {}
        self.requests_by_intent: dict[str, int] = {}
        self.response_times: deque[float] = deque(maxlen=1000)
        self.openai_tokens_prompt: int = 0
        self.openai_tokens_completion: int = 0
        self.rag_requests: int = 0
        self.rag_hits: int = 0
        self.errors_timestamps: deque[float] = deque(maxlen=10000)

    def record_request(self, status_code: int, duration_ms: float) -> None:
        with self._lock:
            self.requests_total += 1
            self.requests_by_status[status_code] = self.requests_by_status.get(status_code, 0) + 1
            self.response_times.append(duration_ms)
            if status_code >= 400:
                self.errors_timestamps.append(time.time())

    def record_intent(self, category: str) -> None:
        with self._lock:
            self.requests_by_intent[category] = self.requests_by_intent.get(category, 0) + 1

    def record_openai_usage(self, prompt_tokens: int, completion_tokens: int) -> None:
        with self._lock:
            self.openai_tokens_prompt += prompt_tokens
            self.openai_tokens_completion += completion_tokens

    def record_rag_result(self, hit: bool) -> None:
        with self._lock:
            self.rag_requests += 1
            if hit:
                self.rag_hits += 1

    def record_error(self) -> None:
        with self._lock:
            self.errors_timestamps.append(time.time())

    def snapshot(self) -> dict:
        with self._lock:
            now = time.time()
            one_hour_ago = now - 3600

            errors_in_hour = sum(1 for ts in self.errors_timestamps if ts > one_hour_ago)

            avg_response_time = 0.0
            if self.response_times:
                avg_response_time = round(sum(self.response_times) / len(self.response_times), 2)

            rag_hit_rate = 0.0
            if self.rag_requests > 0:
                rag_hit_rate = round(self.rag_hits / self.rag_requests, 4)

            error_rate = 0.0
            if self.requests_total > 0:
                error_rate = round(errors_in_hour / max(self.requests_total, 1), 4)

            return {
                "requests_total": self.requests_total,
                "requests_by_status": dict(self.requests_by_status),
                "requests_by_intent": dict(self.requests_by_intent),
                "avg_response_time_ms": avg_response_time,
                "openai_tokens_prompt": self.openai_tokens_prompt,
                "openai_tokens_completion": self.openai_tokens_completion,
                "openai_tokens_total": self.openai_tokens_prompt + self.openai_tokens_completion,
                "rag_hit_rate": rag_hit_rate,
                "rag_requests": self.rag_requests,
                "rag_hits": self.rag_hits,
                "error_rate_1h": error_rate,
                "errors_in_last_hour": errors_in_hour,
            }


metrics = Metrics()
