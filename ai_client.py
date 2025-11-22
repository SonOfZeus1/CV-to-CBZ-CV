import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

from groq import Groq

logger = logging.getLogger(__name__)


class AIClientUnavailable(RuntimeError):
    """Raised when the Groq client cannot be initialised."""


class GroqStructuredClient:
    """
    Thin wrapper around Groq chat completions that guarantees JSON output,
    retries on transient errors and centralises configuration.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        max_retries: int = 3,
        timeout: int = 60,
    ):
        api_key = api_key or os.getenv("GROQ_API_KEY")
        if not api_key:
            raise AIClientUnavailable(
                "GROQ_API_KEY is required to run the AI parsing pipeline."
            )

        self.model = model or os.getenv("GROQ_MODEL", "llama-3.1-70b-versatile")
        self.max_retries = max_retries
        self.timeout = timeout
        self._client = Groq(api_key=api_key)

    def _extract_json(self, content: str) -> Dict[str, Any]:
        snippet = content.strip()
        if snippet.startswith("```"):
            parts = snippet.split("```")
            # Format: ```json { ... } ```
            if len(parts) >= 2:
                snippet = parts[1]
        return json.loads(snippet)

    def structured_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.0,
        response_format: Optional[Dict[str, Any]] = None,
        max_tokens: int = 1200,
    ) -> Dict[str, Any]:
        """
        Calls Groq with retries and returns a parsed JSON payload.
        """
        if not messages:
            raise ValueError("messages cannot be empty")

        response_format = response_format or {"type": "json_object"}
        last_error: Optional[Exception] = None

        for attempt in range(1, self.max_retries + 1):
            try:
                logger.info("Calling Groq (attempt %s/%s) with model %s...", attempt, self.max_retries, self.model)
                start_time = time.time()
                response = self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format=response_format,
                    timeout=self.timeout,
                )
                duration = time.time() - start_time
                content = response.choices[0].message.content
                if not content:
                    raise ValueError("Empty response content from Groq")
                
                logger.info("Groq call successful in %.2fs. Response length: %d chars", duration, len(content))
                return self._extract_json(content)
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Groq call failed (attempt %s/%s): %s",
                    attempt,
                    self.max_retries,
                    exc,
                )
                sleep_seconds = min(2 ** (attempt - 1), 10)
                time.sleep(sleep_seconds)

        logger.error("All Groq attempts failed. Last error: %s", last_error)
        raise RuntimeError(f"Groq call failed after retries: {last_error}") from last_error


_CLIENT_CACHE: Optional[GroqStructuredClient] = None


def get_ai_client() -> GroqStructuredClient:
    global _CLIENT_CACHE
    if _CLIENT_CACHE is None:
        _CLIENT_CACHE = GroqStructuredClient()
    return _CLIENT_CACHE

