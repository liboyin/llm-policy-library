"""Unit tests for `llm_policy_library.prompts`."""

import json
from unittest.mock import patch, MagicMock

import pytest

import llm_policy_library.prompts as testee

def test_load_prompts_caches_results() -> None:
    """The JSON file should only be read once."""
    # Reset cache
    testee._PROMPTS_CACHE = None
    
    with patch("json.loads") as mock_loads:
        mock_loads.return_value = {"key": "value"}
        assert testee.load_prompts() == {"key": "value"}
        assert testee.load_prompts() == {"key": "value"}
        
        mock_loads.assert_called_once()
        
    testee._PROMPTS_CACHE = None

def test_get_prompt_formats_kwargs() -> None:
    """Keyword arguments should be interpolated."""
    testee._PROMPTS_CACHE = {"test_prompt": "Hello {name}!"}
    
    assert testee.get_prompt("test_prompt", name="World") == "Hello World!"
    
    testee._PROMPTS_CACHE = None

    testee._PROMPTS_CACHE = None
