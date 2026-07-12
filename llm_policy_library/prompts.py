"""Loads system prompts and instructions from a configuration file.

This allows prompts to be version-controlled and modified without changing Python
code, enabling easier prompt engineering and testing.
"""

import importlib.resources
import json
from typing import Any

_PROMPTS_CACHE: dict[str, str] | None = None


def load_prompts() -> dict[str, str]:
    """Load the prompts from the JSON configuration file."""
    global _PROMPTS_CACHE
    if _PROMPTS_CACHE is None:
        file_path = importlib.resources.files("llm_policy_library").joinpath("prompts.json")
        _PROMPTS_CACHE = json.loads(file_path.read_text(encoding="utf-8"))
    return _PROMPTS_CACHE


def get_prompt(key: str, **kwargs: Any) -> str:
    """Get a formatted prompt by its key.

    Args:
        key: The key of the prompt in the JSON file.
        **kwargs: Format string arguments to interpolate into the prompt.

    Returns:
        The formatted prompt.
    """
    prompts = load_prompts()
    prompt = prompts[key]
    return prompt.format(**kwargs)
