"""SnowDuckAI Agent — Main coordinator for dbt error diagnosis and fixing.

Full workflow:
- Phase 1: Diagnose error with LLM tool-use loop
- Phase 2: Test fix in sandbox (max 5 attempts)
- Phase 3: Create PR with verified fix
- Phase 4: Notify developer of outcome
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from snowduckai.llm_client import get_llm_client
from snowduckai.sandbox_client import get_sandbox_client, test_fix_with_retry
from snowduckai.git_handler import get_git_handler
from snowduckai.notifier import get_notifier


class SnowDuckAIAgent:
    """Coordinates the dbt error diagnosis and fix workflow."""

    def __init__(self, config: Dict[str, Any], diagnose_only: bool = False):
        """Initialize the agent with configuration.

        Args:
            config: Configuration dict loaded from config.yml
            diagnose_only: If True, only initialize LLM client (skip sandbox/git/notify)
        """
        self.config = config
        self.diagnose_only = diagnose_only
        self.llm_client = get_llm_client(config)

        # Only initialize these if running full workflow
        if not diagnose_only:
            self.sandbox_client = get_sandbox_client(config)
            self.git_handler = get_git_handler(config)
            self.notifier = get_notifier(config)
        else:
            self.sandbox_client = None
            self.git_handler = None
            self.notifier = None

        self.dbt_project_path = Path(config.get("dbt", {}).get("project_path", "./dbt-project"))
        self.conversation_history: List[Dict[str, Any]] = []
        self.max_iterations = 20
        self.error_log: Optional[str] = None

    def _get_tools(self) -> List[Dict[str, Any]]:
        """Define the tools available to the LLM.

        Returns:
            List of tool definitions in Anthropic format
        """
        tools = [
            {
                "name": "read_file",
                "description": "Read the contents of a file from the dbt project. Use this to examine SQL models, schema files, or configuration files.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Path to the file relative to the dbt project root (e.g., 'models/staging/stg_users.sql')"
                        }
                    },
                    "required": ["path"]
                }
            },
            {
                "name": "list_directory",
                "description": "List files and directories in a given path within the dbt project. Use this to explore the project structure.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Path to the directory relative to the dbt project root (e.g., 'models/staging'). Use '.' for the root."
                        }
                    },
                    "required": ["path"]
                }
            }
        ]

        # Add propose_fix tool for providers that support tool calling (anthropic, openai)
        # Ollama uses text-based responses instead
        provider = self.config.get("llm", {}).get("provider", "")
        if provider in ["anthropic", "openai"]:
            tools.append({
                "name": "propose_fix",
                "description": "Propose a fix for the dbt error. This ends the diagnostic loop and submits your proposed solution.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Path to the file to fix, relative to dbt project root"
                        },
                        "original_content": {
                            "type": "string",
                            "description": "The current/original content of the file"
                        },
                        "fixed_content": {
                            "type": "string",
                            "description": "The fixed content of the file"
                        },
                        "explanation": {
                            "type": "string",
                            "description": "Explanation of what was wrong and how the fix addresses it"
                        }
                    },
                    "required": ["file_path", "original_content", "fixed_content", "explanation"]
                }
            })

        return tools

    def _execute_tool(self, tool_name: str, tool_input: Dict[str, Any]) -> str:
        """Execute a tool call and return the result.

        Args:
            tool_name: Name of the tool to execute
            tool_input: Input parameters for the tool

        Returns:
            String result of the tool execution
        """
        if tool_name == "read_file":
            return self._read_file(tool_input["path"])
        elif tool_name == "list_directory":
            return self._list_directory(tool_input["path"])
        elif tool_name == "propose_fix":
            return self._propose_fix(tool_input)
        else:
            return f"Error: Unknown tool '{tool_name}'"

    def _read_file(self, path: str) -> str:
        """Read a file from the dbt project.

        Args:
            path: Relative path from dbt project root

        Returns:
            File contents or error message
        """
        try:
            file_path = self.dbt_project_path / path
            if not file_path.exists():
                return f"Error: File not found: {path}"

            if not file_path.is_relative_to(self.dbt_project_path):
                return f"Error: Path {path} is outside dbt project"

            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            return f"Contents of {path}:\n\n{content}"
        except Exception as e:
            return f"Error reading {path}: {str(e)}"

    def _list_directory(self, path: str) -> str:
        """List contents of a directory in the dbt project.

        Args:
            path: Relative path from dbt project root

        Returns:
            Directory listing or error message
        """
        try:
            dir_path = self.dbt_project_path / path
            if not dir_path.exists():
                return f"Error: Directory not found: {path}"

            if not dir_path.is_relative_to(self.dbt_project_path):
                return f"Error: Path {path} is outside dbt project"

            if not dir_path.is_dir():
                return f"Error: {path} is not a directory"

            items = []
            for item in sorted(dir_path.iterdir()):
                item_type = "DIR" if item.is_dir() else "FILE"
                items.append(f"  {item_type}  {item.name}")

            listing = "\n".join(items) if items else "  (empty directory)"
            return f"Contents of {path}:\n{listing}"
        except Exception as e:
            return f"Error listing {path}: {str(e)}"

    def _propose_fix(self, fix_data: Dict[str, Any]) -> str:
        """Store the proposed fix (doesn't actually modify files yet).

        Args:
            fix_data: Dict with file_path, original_content, fixed_content, explanation

        Returns:
            Confirmation message
        """
        self.proposed_fix = fix_data
        return "Fix proposal recorded. Diagnostic loop complete."

    def _parse_fix_from_text(self, text: str) -> Optional[Dict[str, Any]]:
        """Parse fix data from LLM text response.

        Tries JSON parsing first, then falls back to regex extraction.

        Args:
            text: Text response from LLM

        Returns:
            Dict with file_path, original_content, fixed_content, explanation or None
        """
        import re

        # Try JSON parsing first - try the entire stripped text
        try:
            stripped_text = text.strip()
            fix_data = json.loads(stripped_text)
            if all(k in fix_data for k in ["file_path", "fixed_content"]):
                # Fill in defaults if missing
                if "original_content" not in fix_data:
                    fix_data["original_content"] = ""
                if "explanation" not in fix_data:
                    fix_data["explanation"] = "Fix extracted from LLM response"
                return fix_data
        except json.JSONDecodeError:
            pass

        # Fallback: Try to find JSON object embedded in text with regex
        try:
            json_match = re.search(r'\{[^{}]*"file_path"[^{}]*\}', text, re.DOTALL)
            if json_match:
                fix_data = json.loads(json_match.group(0))
                if all(k in fix_data for k in ["file_path", "fixed_content"]):
                    # Fill in defaults if missing
                    if "original_content" not in fix_data:
                        fix_data["original_content"] = ""
                    if "explanation" not in fix_data:
                        fix_data["explanation"] = "Fix extracted from LLM response"
                    return fix_data
        except json.JSONDecodeError:
            pass

        # Fallback: try to extract from markdown-style response
        # Look for file path
        file_path_match = re.search(r'(?:file_path|File|Path):\s*["`]?([^"`\n]+\.sql)["`]?', text, re.IGNORECASE)
        if not file_path_match:
            # Try to find any .sql file mentioned
            file_path_match = re.search(r'([a-zA-Z0-9_/]+\.sql)', text)

        if file_path_match:
            file_path = file_path_match.group(1)

            # Look for SQL code blocks
            sql_blocks = re.findall(r'```sql\s*\n(.*?)\n```', text, re.DOTALL | re.IGNORECASE)
            if not sql_blocks:
                sql_blocks = re.findall(r'```\s*\n(.*?)\n```', text, re.DOTALL)

            if sql_blocks:
                fixed_content = sql_blocks[-1].strip()  # Take the last code block as the fix

                # Try to extract explanation
                explanation_match = re.search(r'(?:explanation|summary|fix):\s*(.+?)(?:\n\n|\n```|$)', text, re.IGNORECASE | re.DOTALL)
                explanation = explanation_match.group(1).strip() if explanation_match else "Fix extracted from markdown response"

                return {
                    "file_path": file_path,
                    "original_content": "",
                    "fixed_content": fixed_content,
                    "explanation": explanation
                }

        return None

    def _load_dbt_error_log(self, log_path: Optional[str] = None) -> str:
        """Load the dbt error log.

        Args:
            log_path: Optional path to log file. If None, uses logs/dbt.log

        Returns:
            Log content as string
        """
        if log_path is None:
            log_path = self.dbt_project_path / "logs" / "dbt.log"
        else:
            log_path = Path(log_path)

        if not log_path.exists():
            raise FileNotFoundError(f"dbt log file not found: {log_path}")

        with open(log_path, "r", encoding="utf-8") as f:
            return f.read()

    def _load_manifest(self) -> Optional[Dict[str, Any]]:
        """Load dbt manifest.json if available.

        Returns:
            Manifest dict or None if not found
        """
        manifest_path = self.dbt_project_path / "target" / "manifest.json"
        if not manifest_path.exists():
            return None

        with open(manifest_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _create_initial_prompt(self, error_log: str, manifest: Optional[Dict[str, Any]]) -> str:
        """Create the initial diagnostic prompt for the LLM.

        Args:
            error_log: dbt error log content
            manifest: Optional dbt manifest

        Returns:
            Initial prompt text
        """
        prompt = f"""You are a dbt debugging agent. A dbt pipeline has failed and you need to diagnose the error and propose a fix.

Your task:
1. Analyze the error log below
2. Use the available tools to explore the dbt project and understand the issue
3. Once you understand the problem, respond with the fix as JSON

dbt Error Log:
```
{error_log}
```
"""

        if manifest:
            prompt += f"""
dbt Manifest (graph and schema information):
```json
{json.dumps(manifest, indent=2)[:5000]}... (truncated for brevity)
```
"""

        prompt += """
Available tools:
- read_file: Read any file from the dbt project
- list_directory: List contents of any directory

Start by analyzing the error and determining what files you need to examine.

When you have identified the fix, respond with a JSON object in this exact format:
{{
  "file_path": "path/to/file.sql",
  "original_content": "the current file content",
  "fixed_content": "the corrected file content",
  "explanation": "brief explanation of what was wrong and how you fixed it"
}}

IMPORTANT: Return ONLY the JSON object when proposing the fix. Do not add any other text before or after the JSON.
"""
        return prompt

    def diagnose(self, log_path: Optional[str] = None) -> Dict[str, Any]:
        """Run the diagnostic loop to analyze dbt error and propose fix.

        Args:
            log_path: Optional path to dbt log file

        Returns:
            Dict with proposed fix or error information
        """
        print("🔍 Loading dbt error log...")
        self.error_log = self._load_dbt_error_log(log_path)

        print("📋 Loading manifest...")
        manifest = self._load_manifest()

        print("🤖 Starting diagnostic loop...")
        initial_prompt = self._create_initial_prompt(self.error_log, manifest)

        self.conversation_history = [
            {"role": "user", "content": initial_prompt}
        ]

        self.proposed_fix = None
        tools = self._get_tools()

        # System prompt to reinforce JSON response format
        system_prompt = (
            "You are a dbt debugging agent. Your job is to diagnose errors and propose fixes. "
            "When you have identified the fix, respond with a JSON object containing file_path, original_content, fixed_content, and explanation. "
            "Return ONLY the JSON object, with no additional text or markdown formatting."
        )

        for iteration in range(self.max_iterations):
            print(f"  Iteration {iteration + 1}/{self.max_iterations}...")

            response = self.llm_client.complete(
                messages=self.conversation_history,
                tools=tools,
                max_tokens=4096,
                system=system_prompt
            )

            assistant_content = []

            if response.get("content"):
                assistant_content.append({
                    "type": "text",
                    "text": response["content"]
                })
                print(f"  💭 {response['content'][:100]}...")

            if response.get("tool_calls"):
                for tool_call in response["tool_calls"]:
                    tool_name = tool_call["name"]
                    tool_input = tool_call["input"]
                    print(f"  🔧 Calling tool: {tool_name}")

                    assistant_content.append({
                        "type": "tool_use",
                        "id": tool_call["id"],
                        "name": tool_name,
                        "input": tool_input
                    })

                    tool_result = self._execute_tool(tool_name, tool_input)

                    self.conversation_history.append({
                        "role": "assistant",
                        "content": assistant_content
                    })

                    self.conversation_history.append({
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_call["id"],
                                "content": tool_result
                            }
                        ]
                    })

                    if tool_name == "propose_fix":
                        print("✅ Fix proposed!")
                        return {
                            "success": True,
                            "fix": self.proposed_fix,
                            "iterations": iteration + 1,
                            "manifest": manifest
                        }

                    assistant_content = []

            else:
                self.conversation_history.append({
                    "role": "assistant",
                    "content": assistant_content if assistant_content else response["content"]
                })

                if response.get("stop_reason") in ["end_turn", "stop"]:
                    # Try to parse fix from text response
                    text_content = response.get("content", "")
                    if text_content:
                        parsed_fix = self._parse_fix_from_text(text_content)
                        if parsed_fix:
                            print("✅ Fix parsed from text response!")
                            return {
                                "success": True,
                                "fix": parsed_fix,
                                "iterations": iteration + 1,
                                "manifest": manifest
                            }

                    # Check if fix was proposed via tool (for backward compatibility)
                    if self.proposed_fix:
                        return {
                            "success": True,
                            "fix": self.proposed_fix,
                            "iterations": iteration + 1,
                            "manifest": manifest
                        }
                    else:
                        return {
                            "success": False,
                            "error": "LLM stopped without proposing a fix",
                            "iterations": iteration + 1
                        }

        return {
            "success": False,
            "error": f"Max iterations ({self.max_iterations}) reached without fix proposal",
            "iterations": self.max_iterations
        }

    def run_full_workflow(self, log_path: Optional[str] = None) -> Dict[str, Any]:
        """Run the complete Project Red workflow.

        Phase 1: Diagnose error with LLM
        Phase 2: Test fix in sandbox
        Phase 3: Create PR if sandbox passes
        Phase 4: Notify developer

        Args:
            log_path: Optional path to dbt log file

        Returns:
            Dict with workflow outcome
        """
        print("\n" + "=" * 70)
        print("Phase 1 — Diagnostic")
        print("=" * 70)

        diagnostic_result = self.diagnose(log_path)

        if not diagnostic_result["success"]:
            print(f"\n❌ Diagnostic failed: {diagnostic_result['error']}")
            return {
                "success": False,
                "phase": "diagnostic",
                "error": diagnostic_result["error"]
            }

        fix_data = diagnostic_result["fix"]
        manifest = diagnostic_result.get("manifest")

        print("\n" + "=" * 70)
        print("Phase 2 — Sandbox Testing")
        print("=" * 70)

        sandbox_result = test_fix_with_retry(
            sandbox_client=self.sandbox_client,
            fix_data=fix_data,
            manifest=manifest,
            max_attempts=5
        )

        if not sandbox_result["success"]:
            print(f"\n❌ Sandbox testing failed after {sandbox_result['attempts']} attempts")

            if self.notifier:
                print("\n" + "=" * 70)
                print("Phase 4 — Notification (Failure)")
                print("=" * 70)

                notify_result = self.notifier.send_failure(
                    error_log=self.error_log,
                    attempts=sandbox_result["attempts"],
                    last_error=sandbox_result.get("error")
                )

                if notify_result["success"]:
                    print("📧 Failure notification sent")
                else:
                    print(f"⚠️  Failed to send notification: {notify_result.get('error')}")

            return {
                "success": False,
                "phase": "sandbox",
                "error": sandbox_result.get("error"),
                "attempts": sandbox_result["attempts"]
            }

        print("\n" + "=" * 70)
        print("Phase 3 — Create Pull Request")
        print("=" * 70)

        pr_result = self.git_handler.create_pr(
            fix_data=fix_data,
            error_log=self.error_log,
            diagnostic_summary=f"Diagnosed in {diagnostic_result['iterations']} iterations",
            sandbox_attempts=sandbox_result["attempts"]
        )

        if not pr_result["success"]:
            print(f"\n❌ Failed to create PR: {pr_result.get('error')}")
            return {
                "success": False,
                "phase": "git",
                "error": pr_result.get("error")
            }

        print("\n" + "=" * 70)
        print("Phase 4 — Notification (Success)")
        print("=" * 70)

        if self.notifier:
            notify_result = self.notifier.send_success(
                pr_url=pr_result["pr_url"],
                branch=pr_result["branch"],
                fix_data=fix_data
            )

            if notify_result["success"]:
                print("📧 Success notification sent")
            else:
                print(f"⚠️  Failed to send notification: {notify_result.get('error')}")

        return {
            "success": True,
            "pr_url": pr_result["pr_url"],
            "branch": pr_result["branch"],
            "diagnostic_iterations": diagnostic_result["iterations"],
            "sandbox_attempts": sandbox_result["attempts"]
        }


def load_config(config_path: str = "config.yml") -> Dict[str, Any]:
    """Load configuration from YAML file.

    Args:
        config_path: Path to config.yml

    Returns:
        Configuration dict
    """
    try:
        import yaml
    except ImportError:
        raise ImportError("PyYAML not installed. Install with: pip install pyyaml")

    config_path = Path(config_path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    config = _expand_env_vars(config)

    return config


def _expand_env_vars(config: Any) -> Any:
    """Recursively expand environment variables in config values.

    Replaces ${VAR_NAME} with os.getenv('VAR_NAME')

    Args:
        config: Config dict or value

    Returns:
        Config with expanded variables
    """
    if isinstance(config, dict):
        return {k: _expand_env_vars(v) for k, v in config.items()}
    elif isinstance(config, list):
        return [_expand_env_vars(item) for item in config]
    elif isinstance(config, str):
        if config.startswith("${") and config.endswith("}"):
            var_name = config[2:-1]
            return os.getenv(var_name, config)
        return config
    else:
        return config


def main():
    """CLI entry point for SnowDuckAI."""
    import argparse

    parser = argparse.ArgumentParser(
        description="SnowDuckAI — AI agent for dbt error diagnosis and fixing"
    )
    parser.add_argument(
        "--config",
        default="config.yml",
        help="Path to config.yml (default: config.yml)"
    )
    parser.add_argument(
        "--log",
        help="Path to dbt.log file (default: <dbt_project>/logs/dbt.log)"
    )
    parser.add_argument(
        "--diagnose-only",
        action="store_true",
        help="Run Phase 1 only (diagnostic) without sandbox/PR/notify"
    )

    args = parser.parse_args()

    print("🚀 SnowDuckAI — dbt Error Resolution Agent")
    print("=" * 70)

    try:
        config = load_config(args.config)
        agent = SnowDuckAIAgent(config, diagnose_only=args.diagnose_only)

        if args.diagnose_only:
            result = agent.diagnose(log_path=args.log)

            print("\n" + "=" * 70)
            if result["success"]:
                fix = result["fix"]
                print("✅ DIAGNOSTIC COMPLETE")
                print(f"   Iterations: {result['iterations']}")
                print(f"\n📝 Proposed Fix:")
                print(f"   File: {fix['file_path']}")
                print(f"   Explanation: {fix['explanation']}")
                print(f"\n   Original content length: {len(fix['original_content'])} chars")
                print(f"   Fixed content length: {len(fix['fixed_content'])} chars")
                print(f"\n   Next step: Run without --diagnose-only to test in sandbox")
            else:
                print("❌ DIAGNOSTIC FAILED")
                print(f"   Error: {result['error']}")
                print(f"   Iterations: {result['iterations']}")
        else:
            result = agent.run_full_workflow(log_path=args.log)

            print("\n" + "=" * 70)
            print("FINAL RESULT")
            print("=" * 70)

            if result["success"]:
                print("✅ SUCCESS")
                print(f"   Pull Request: {result['pr_url']}")
                print(f"   Branch: {result['branch']}")
                print(f"   Diagnostic iterations: {result['diagnostic_iterations']}")
                print(f"   Sandbox attempts: {result['sandbox_attempts']}")
                print(f"\nThe fix has been verified and is ready for review!")
            else:
                print("❌ FAILED")
                print(f"   Phase: {result['phase']}")
                print(f"   Error: {result['error']}")
                if result.get("attempts"):
                    print(f"   Sandbox attempts: {result['attempts']}")
                exit(1)

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        exit(1)


if __name__ == "__main__":
    main()
