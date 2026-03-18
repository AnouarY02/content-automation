"""
Base agent klasse voor alle AI-agents in het systeem.
Elke agent erft van deze klasse.

Ondersteunt OpenAI (primair, via OPENAI_API_KEY) en Anthropic (fallback).
Provider wordt bepaald door model_config.json → per-task "provider" veld.
"""

import json
import os
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

ROOT = Path(__file__).parent.parent
PROMPTS_DIR = ROOT / "prompts"
CONFIGS_DIR = ROOT / "configs"

with open(CONFIGS_DIR / "model_config.json") as f:
    MODEL_CONFIG = json.load(f)


def _build_openai_client():
    """Lazy-init OpenAI client."""
    import openai
    return openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def _build_anthropic_client():
    """Lazy-init Anthropic client (fallback)."""
    import anthropic
    return anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


class BaseAgent(ABC):
    """
    Abstracte basisklasse voor alle marketing agents.
    Beheert: model-selectie, prompt-loading, API-aanroepen, cost-tracking.
    """

    task_name: str = "base"  # Override in subklassen

    def __init__(self):
        self.config = MODEL_CONFIG["models"].get(self.task_name, {})
        self.provider = self.config.get("provider", "openai")
        self.model = self.config.get("model", "gpt-4o-mini")
        self.max_tokens = self.config.get("max_tokens", 1024)
        self.temperature = self.config.get("temperature", 0.7)
        self.total_cost_usd = 0.0

        # Lazy client — wordt pas aangemaakt bij eerste API call
        self._client = None

        self._base_system_prompt = self._load_prompt("system/base_agent.txt")

    @property
    def client(self):
        if self._client is None:
            if self.provider == "openai":
                self._client = _build_openai_client()
            else:
                self._client = _build_anthropic_client()
        return self._client

    def _load_prompt(self, relative_path: str) -> str:
        path = PROMPTS_DIR / relative_path
        if path.exists():
            return path.read_text(encoding="utf-8")
        logger.warning(f"Prompt niet gevonden: {path}")
        return ""

    def _build_system_prompt(self, extra: str = "") -> str:
        parts = [self._base_system_prompt]
        if extra:
            parts.append(extra)
        return "\n\n".join(parts)

    def _fill_template(self, template: str, variables: dict) -> str:
        for key, value in variables.items():
            placeholder = "{" + key + "}"
            template = template.replace(placeholder, str(value))
        return template

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    def _call_api(self, system: str, user_message: str) -> str:
        logger.info(f"[{self.__class__.__name__}] API call → provider={self.provider} model={self.model}")
        start = time.time()

        if self.provider == "openai":
            result = self._call_openai(system, user_message, start)
        else:
            result = self._call_anthropic(system, user_message, start)

        # Guard tegen lege responses — gooi error zodat tenacity opnieuw probeert
        if not result or not result.strip():
            raise ValueError(
                f"[{self.__class__.__name__}] API gaf lege response terug "
                f"(provider={self.provider}, model={self.model}). Retry..."
            )

        return result

    def _call_openai(self, system: str, user_message: str, start: float) -> str:
        kwargs = dict(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_message},
            ],
        )
        if self.config.get("response_format") == "json_object":
            kwargs["response_format"] = {"type": "json_object"}
        response = self.client.chat.completions.create(**kwargs)

        elapsed = time.time() - start
        input_tokens = response.usage.prompt_tokens
        output_tokens = response.usage.completion_tokens
        cost = self._estimate_cost(input_tokens, output_tokens)
        self.total_cost_usd += cost

        logger.info(
            f"[{self.__class__.__name__}] Klaar in {elapsed:.1f}s | "
            f"tokens in={input_tokens} out={output_tokens} | "
            f"kosten=${cost:.5f}"
        )

        return response.choices[0].message.content

    def _call_anthropic(self, system: str, user_message: str, start: float) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            system=system,
            messages=[{"role": "user", "content": user_message}],
        )

        elapsed = time.time() - start
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        cost = self._estimate_cost(input_tokens, output_tokens)
        self.total_cost_usd += cost

        logger.info(
            f"[{self.__class__.__name__}] Klaar in {elapsed:.1f}s | "
            f"tokens in={input_tokens} out={output_tokens} | "
            f"kosten=${cost:.5f}"
        )

        return response.content[0].text

    def _estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        costs = MODEL_CONFIG["cost_tracking"]
        if self.provider == "openai":
            model_lower = self.model.lower()
            if "gpt-4o-mini" in model_lower:
                in_rate = costs.get("gpt4o_mini_input_per_1k", 0.00015)
                out_rate = costs.get("gpt4o_mini_output_per_1k", 0.0006)
            elif "gpt-4o" in model_lower:
                in_rate = costs.get("gpt4o_input_per_1k", 0.0025)
                out_rate = costs.get("gpt4o_output_per_1k", 0.01)
            elif "gpt-4.1-mini" in model_lower:
                in_rate = costs.get("gpt41_mini_input_per_1k", 0.0004)
                out_rate = costs.get("gpt41_mini_output_per_1k", 0.0016)
            elif "gpt-4.1" in model_lower:
                in_rate = costs.get("gpt41_input_per_1k", 0.002)
                out_rate = costs.get("gpt41_output_per_1k", 0.008)
            else:
                in_rate = costs.get("gpt4o_mini_input_per_1k", 0.00015)
                out_rate = costs.get("gpt4o_mini_output_per_1k", 0.0006)
        else:
            is_haiku = "haiku" in self.model
            in_rate = costs["haiku_input_per_1k"] if is_haiku else costs["sonnet_input_per_1k"]
            out_rate = costs["haiku_output_per_1k"] if is_haiku else costs["sonnet_output_per_1k"]
        return (input_tokens / 1000 * in_rate) + (output_tokens / 1000 * out_rate)

    def _parse_json_response(self, raw: str, default: Any = None) -> Any:
        """Haal JSON uit de response, ook als er extra tekst omheen zit.

        Args:
            raw:     Ruwe tekstrespons van de LLM.
            default: Retourwaarde als parsen mislukt (None by default).
                     Stel in op {} of [] als de pipeline niet mag crashen.

        Returns:
            Geparseerde JSON-structuur, of `default` bij parse-fout.
        """
        import re as _re
        raw = raw.strip()

        # Strip markdown code fences (```json ... ``` of ``` ... ```)
        fence_match = _re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
        if fence_match:
            raw = fence_match.group(1).strip()

        # Zoek JSON block (object eerst, dan array)
        for start_char in ["{", "["]:
            start = raw.find(start_char)
            if start != -1:
                end_char = "}" if start_char == "{" else "]"
                end = raw.rfind(end_char)
                if end != -1 and end >= start:
                    try:
                        return json.loads(raw[start : end + 1])
                    except json.JSONDecodeError as exc:
                        logger.debug(
                            f"[{self.__class__.__name__}] JSONDecodeError bij extractie "
                            f"(start_char={start_char!r}): {exc}"
                        )
        # Laatste poging: probeer de hele string
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(
                f"[{self.__class__.__name__}] Kan JSON niet parsen uit API-respons — "
                f"retourneert default={default!r}. "
                f"Ruwe respons (eerste 300 tekens): {raw[:300]!r}"
            )
            return default

    @abstractmethod
    def run(self, *args, **kwargs) -> Any:
        """Voer de agent-taak uit. Override in elke subklasse."""
        pass
