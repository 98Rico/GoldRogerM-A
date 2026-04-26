import time
from typing import Any, Callable, Optional
from concurrent.futures import ThreadPoolExecutor, TimeoutError


class LLMTimeoutError(Exception):
    pass


class AgentRuntime:
    """
    Production-grade wrapper for all LLM agent calls.

    Features:
    - hard timeout (no infinite hang)
    - retry limit
    - latency logging
    - safe fallback
    - optional parallel execution support
    """

    def __init__(
        self,
        timeout: int = 8,
        max_retries: int = 2,
        verbose: bool = True,
    ):
        self.timeout = timeout
        self.max_retries = max_retries
        self.verbose = verbose
        self.executor = ThreadPoolExecutor(max_workers=10)

    # ─────────────────────────────────────────────
    # CORE EXECUTION WRAPPER
    # ─────────────────────────────────────────────
    def run(self, fn: Callable, *args, fallback=None, name: str = "agent", **kwargs) -> Any:
        """
        Runs any LLM agent safely with timeout + retry.
        """

        last_error = None

        for attempt in range(self.max_retries + 1):
            start = time.time()

            try:
                future = self.executor.submit(fn, *args, **kwargs)
                result = future.result(timeout=self.timeout)

                if self.verbose:
                    print(f"[{name}] ✓ success in {time.time() - start:.2f}s")

                return result

            except TimeoutError:
                last_error = "timeout"
                if self.verbose:
                    print(f"[{name}] ⏱ timeout ({self.timeout}s), attempt {attempt+1}")

            except Exception as e:
                last_error = e
                if self.verbose:
                    print(f"[{name}] ❌ error: {str(e)}")

            time.sleep(0.5 * (attempt + 1))  # exponential backoff

        if self.verbose:
            print(f"[{name}] ⚠ fallback used after failure: {last_error}")

        return fallback

    # ─────────────────────────────────────────────
    # PARALLEL EXECUTION (OPTIONAL BUT POWERFUL)
    # ─────────────────────────────────────────────
    def run_parallel(self, tasks: dict[str, Callable]) -> dict:
        """
        Run multiple agents in parallel.

        Example:
        {
            "fundamentals": lambda: agent.run(...),
            "market": lambda: agent.run(...)
        }
        """

        results = {}

        futures = {
            name: self.executor.submit(fn)
            for name, fn in tasks.items()
        }

        for name, future in futures.items():
            try:
                results[name] = future.result(timeout=self.timeout)
            except Exception:
                results[name] = None

        return results