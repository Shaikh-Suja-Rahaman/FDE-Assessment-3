"""Minimal Gemini REST client (no SDK dependency).

- API key is sent in the `x-goog-api-key` header, never in the URL, so it can
  never leak into exception messages, logs or eval CSVs.
- Timeouts on every request; retries honour the server's RetryInfo delay for
  429s and back off exponentially for 5xx / network errors.
- Daily-quota exhaustion fails fast so the caller can degrade gracefully.
- Every call is recorded on the RunContext (latency, tokens, retries).
"""
from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any

import requests

from src.telemetry import LLMCallRecord, RunContext

API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"
log = logging.getLogger("procurement.llm")


class LLMError(RuntimeError):
    """Raised when the model cannot produce a usable response."""


@dataclass
class LLMResponse:
    parts: list[dict]
    content: dict           # the raw model turn, appended back verbatim during tool loops

    @property
    def function_calls(self) -> list[dict]:
        return [p["functionCall"] for p in self.parts if "functionCall" in p]

    @property
    def text(self) -> str:
        return "".join(p.get("text", "") for p in self.parts if not p.get("thought"))


def model_name() -> str:
    return os.getenv("MODEL_NAME") or os.getenv("GEMINI_MODEL") or "gemini-3.1-flash-lite"


def llm_configured() -> bool:
    return bool(os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"))


class GeminiClient:
    def __init__(self, ctx: RunContext, model: str | None = None):
        self.ctx = ctx
        self.model = model or model_name()
        self.api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        self.timeout = float(os.getenv("LLM_TIMEOUT_SECONDS", "60"))
        self.max_attempts = int(os.getenv("LLM_MAX_ATTEMPTS", "6"))
        self.max_wait = float(os.getenv("LLM_MAX_RETRY_WAIT_SECONDS", "65"))

    def generate(
        self,
        stage: str,
        contents: list[dict],
        system_instruction: str | None = None,
        tools: list[dict] | None = None,
        allowed_functions: list[str] | None = None,
        response_schema: dict | None = None,
    ) -> LLMResponse:
        if not self.api_key:
            self._record(stage, False, 0.0, 0, error="GOOGLE_API_KEY not set")
            raise LLMError("GOOGLE_API_KEY is not set (see .env.example)")

        generation: dict[str, Any] = {}
        gemini3 = self.model.startswith("gemini-3")
        # Gemini 3 is tuned for its default temperature (low values can cause looping); 2.x runs at 0.
        temperature = os.getenv("LLM_TEMPERATURE", "" if gemini3 else "0")
        if temperature != "":
            generation["temperature"] = float(temperature)
        if response_schema:
            generation.update(responseMimeType="application/json", responseSchema=response_schema)
        if gemini3:
            generation["thinkingConfig"] = {"thinkingLevel": os.getenv("GEMINI_THINKING_LEVEL", "minimal")}
        else:
            budget = os.getenv("GEMINI_THINKING_BUDGET", "0" if self.model.startswith("gemini-2.5-flash") else "")
            if budget != "":
                generation["thinkingConfig"] = {"thinkingBudget": int(budget)}
        payload: dict[str, Any] = {"contents": contents, "generationConfig": generation}
        if system_instruction:
            payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}
        if tools:
            payload["tools"] = [{"functionDeclarations": tools}]
            fc: dict[str, Any] = {"mode": "ANY"}
            if allowed_functions:
                fc["allowedFunctionNames"] = allowed_functions
            payload["toolConfig"] = {"functionCallingConfig": fc}

        url = f"{API_ROOT}/{self.model}:generateContent"
        headers = {"x-goog-api-key": self.api_key, "Content-Type": "application/json"}
        start = time.perf_counter()
        last_error = "unknown error"
        for attempt in range(self.max_attempts):
            try:
                resp = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = f"network error: {type(exc).__name__}"
                time.sleep(min(2 ** attempt, self.max_wait))
                continue

            if resp.status_code == 429:
                body = _json(resp)
                if _daily_quota_exhausted(body):
                    last_error = "daily quota exhausted (429)"
                    break
                last_error = "rate limited (429)"
                wait = min(_retry_delay(body) or 2 ** (attempt + 1), self.max_wait)
                log.warning("%s: rate limited by Gemini, retrying in %.0fs (attempt %d)", stage, wait, attempt + 1)
                time.sleep(wait)
                continue
            if resp.status_code >= 500:
                last_error = f"server error {resp.status_code}"
                log.warning("%s: Gemini %s, retrying (attempt %d)", stage, resp.status_code, attempt + 1)
                time.sleep(min(2 ** (attempt + 1), self.max_wait))
                continue
            if resp.status_code >= 400:
                message = (_json(resp).get("error") or {}).get("message", resp.text[:200])
                last_error = f"HTTP {resp.status_code}: {message}"
                break

            body = _json(resp)
            usage = body.get("usageMetadata") or {}
            try:
                content = body["candidates"][0]["content"]
                parts = content.get("parts") or []
            except (KeyError, IndexError):
                reason = (body.get("promptFeedback") or {}).get("blockReason") or \
                         ((body.get("candidates") or [{}])[0].get("finishReason"))
                last_error = f"empty response ({reason})"
                continue
            self._record(stage, True, start, attempt, usage)
            return LLMResponse(parts=parts, content={"role": "model", "parts": parts})

        self._record(stage, False, start, self.max_attempts, error=last_error)
        raise LLMError(f"Gemini call failed at stage '{stage}': {last_error}")

    def _record(self, stage: str, ok: bool, start: float, retries: int, usage: dict | None = None, error: str | None = None):
        usage = usage or {}
        self.ctx.llm_calls.append(LLMCallRecord(
            stage=stage, model=self.model, ok=ok,
            latency_ms=round((time.perf_counter() - start) * 1000, 1) if start else 0.0,
            input_tokens=int(usage.get("promptTokenCount") or 0),
            output_tokens=int(usage.get("candidatesTokenCount") or 0) + int(usage.get("thoughtsTokenCount") or 0),
            retries=retries, error=error,
        ))


def _json(resp: requests.Response) -> dict:
    try:
        return resp.json()
    except ValueError:
        return {}


def _retry_delay(body: dict) -> float | None:
    for detail in (body.get("error") or {}).get("details") or []:
        delay = detail.get("retryDelay")
        if delay:
            match = re.match(r"([\d.]+)s", str(delay))
            if match:
                return float(match.group(1)) + 1
    return None


def _daily_quota_exhausted(body: dict) -> bool:
    for detail in (body.get("error") or {}).get("details") or []:
        for violation in detail.get("violations") or []:
            if "PerDay" in str(violation.get("quotaId", "")):
                return True
    return False
