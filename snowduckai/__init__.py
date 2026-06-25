"""SnowDuckAI — AI-powered dbt error resolution.

Automatically diagnose, test, and fix dbt pipeline errors.
"""

__version__ = "0.1.0"

from snowduckai.agent import SnowDuckAIAgent, load_config
from snowduckai.llm_client import get_llm_client
from snowduckai.sandbox_client import get_sandbox_client
from snowduckai.git_handler import get_git_handler
from snowduckai.notifier import get_notifier

__all__ = [
    "SnowDuckAIAgent",
    "load_config",
    "get_llm_client",
    "get_sandbox_client",
    "get_git_handler",
    "get_notifier",
]
