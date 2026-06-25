"""LLM client abstraction for SnowDuckAI.

Supports multiple LLM providers via a common interface:
- Anthropic (Claude)
- OpenAI (GPT)
- Ollama (local models)
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
import os


class LLMClient(ABC):
    """Base class for LLM provider implementations."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.llm_config = config.get("llm", {})
        self.model = self.llm_config.get("model")

    @abstractmethod
    def complete(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = 4096,
        system: Optional[str] = None
    ) -> Dict[str, Any]:
        """Send completion request to LLM.

        Args:
            messages: List of message dicts with 'role' and 'content'
            tools: Optional list of tool definitions
            max_tokens: Maximum tokens to generate
            system: Optional system prompt

        Returns:
            Dict with 'content' (text response) and optional 'tool_calls'
        """
        pass


class AnthropicClient(LLMClient):
    """Anthropic (Claude) LLM client."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)

        try:
            import anthropic
        except ImportError:
            raise ImportError(
                "anthropic package not installed. Install with: pip install anthropic"
            )

        api_key = self.llm_config.get("api_key") or os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("Anthropic API key not found in config or ANTHROPIC_API_KEY env var")

        self.client = anthropic.Anthropic(api_key=api_key)

        if not self.model:
            self.model = "claude-haiku-4-5"

    def complete(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = 4096,
        system: Optional[str] = None
    ) -> Dict[str, Any]:
        """Send completion request to Anthropic API."""

        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages
        }

        if system:
            kwargs["system"] = system

        if tools:
            kwargs["tools"] = tools

        response = self.client.messages.create(**kwargs)

        result = {
            "content": "",
            "tool_calls": []
        }

        for block in response.content:
            if block.type == "text":
                result["content"] += block.text
            elif block.type == "tool_use":
                result["tool_calls"].append({
                    "id": block.id,
                    "name": block.name,
                    "input": block.input
                })

        result["stop_reason"] = response.stop_reason

        return result


class OpenAIClient(LLMClient):
    """OpenAI (GPT) LLM client."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)

        try:
            import openai
        except ImportError:
            raise ImportError(
                "openai package not installed. Install with: pip install openai"
            )

        api_key = self.llm_config.get("api_key") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OpenAI API key not found in config or OPENAI_API_KEY env var")

        self.client = openai.OpenAI(api_key=api_key)

        if not self.model:
            self.model = "gpt-4o-mini"

    def complete(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = 4096,
        system: Optional[str] = None
    ) -> Dict[str, Any]:
        """Send completion request to OpenAI API."""

        # Prepend system message if provided
        final_messages = messages
        if system:
            final_messages = [{"role": "system", "content": system}] + messages

        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": final_messages
        }

        if tools:
            formatted_tools = []
            for tool in tools:
                formatted_tools.append({
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "parameters": tool.get("input_schema", {})
                    }
                })
            kwargs["tools"] = formatted_tools

        response = self.client.chat.completions.create(**kwargs)

        message = response.choices[0].message

        result = {
            "content": message.content or "",
            "tool_calls": []
        }

        if message.tool_calls:
            import json
            for tool_call in message.tool_calls:
                result["tool_calls"].append({
                    "id": tool_call.id,
                    "name": tool_call.function.name,
                    "input": json.loads(tool_call.function.arguments)
                })

        result["stop_reason"] = response.choices[0].finish_reason

        return result


class OllamaClient(LLMClient):
    """Ollama (local models) LLM client using OpenAI-compatible API."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)

        try:
            import openai
        except ImportError:
            raise ImportError(
                "openai package not installed. Install with: pip install openai"
            )

        base_url = self.llm_config.get("base_url", "http://localhost:11434")
        # Ensure base_url ends with /v1 for OpenAI-compatible endpoint
        self.base_url = base_url.rstrip("/") + "/v1"

        # Ollama's OpenAI-compatible endpoint requires a placeholder API key
        self.client = openai.OpenAI(
            base_url=self.base_url,
            api_key="ollama"
        )

        if not self.model:
            self.model = "llama3.1:8b"

    def complete(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = 4096,
        system: Optional[str] = None
    ) -> Dict[str, Any]:
        """Send completion request to Ollama via OpenAI-compatible API."""

        # Prepend system message if provided
        final_messages = messages
        if system:
            final_messages = [{"role": "system", "content": system}] + messages

        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": final_messages
        }

        if tools:
            # Convert Anthropic tool format to OpenAI format
            formatted_tools = []
            for tool in tools:
                formatted_tools.append({
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "parameters": tool.get("input_schema", {})
                    }
                })
            kwargs["tools"] = formatted_tools

        response = self.client.chat.completions.create(**kwargs)

        message = response.choices[0].message

        result = {
            "content": message.content or "",
            "tool_calls": []
        }

        if message.tool_calls:
            import json
            for tool_call in message.tool_calls:
                result["tool_calls"].append({
                    "id": tool_call.id,
                    "name": tool_call.function.name,
                    "input": json.loads(tool_call.function.arguments)
                })

        result["stop_reason"] = response.choices[0].finish_reason

        return result


def get_llm_client(config: Dict[str, Any]) -> LLMClient:
    """Factory function to instantiate the appropriate LLM client.

    Args:
        config: Configuration dict with 'llm' section

    Returns:
        LLMClient instance for the configured provider

    Raises:
        ValueError: If provider is unknown
    """
    provider = config.get("llm", {}).get("provider")

    if provider == "anthropic":
        return AnthropicClient(config)
    elif provider == "openai":
        return OpenAIClient(config)
    elif provider == "ollama":
        return OllamaClient(config)
    else:
        raise ValueError(
            f"Unknown LLM provider: {provider}. "
            f"Supported providers: anthropic, openai, ollama"
        )
