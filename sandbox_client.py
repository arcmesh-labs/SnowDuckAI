"""Sandbox client abstraction for Project Red.

Supports multiple sandbox runners via a common interface:
- GitHub Actions (open source)
- Self-hosted CI (enterprise)

Phase 2: Tests proposed fixes in isolated DuckDB + dbt environment.
"""

import os
import time
import base64
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

import requests


class SandboxClient(ABC):
    """Base class for sandbox runner implementations."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.sandbox_config = config.get("sandbox", {})
        self.git_config = config.get("git", {})

    @abstractmethod
    def test_fix(
        self,
        fix_data: Dict[str, Any],
        manifest: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Test a proposed fix in the sandbox environment.

        Args:
            fix_data: Dict with file_path, fixed_content, explanation
            manifest: Optional dbt manifest for schema info

        Returns:
            Dict with 'success' (bool), 'output' (str), and optional 'error'
        """
        pass


class GHAClient(SandboxClient):
    """GitHub Actions sandbox runner."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)

        self.token = self.git_config.get("token") or os.getenv("GITHUB_TOKEN")
        if not self.token:
            raise ValueError("GitHub token not found in config or GITHUB_TOKEN env var")

        self.repo = self.git_config.get("repo")
        if not self.repo:
            raise ValueError("GitHub repo not specified in config (git.repo)")

        self.api_base = "https://api.github.com"
        self.workflow_file = "sandbox.yml"
        self.poll_interval = 10
        self.max_wait_time = 600

    def _trigger_workflow(
        self,
        fix_data: Dict[str, Any],
        manifest: Optional[Dict[str, Any]]
    ) -> str:
        """Trigger the GitHub Actions workflow.

        Args:
            fix_data: Fix data to test
            manifest: Optional manifest

        Returns:
            Run ID as string
        """
        url = f"{self.api_base}/repos/{self.repo}/actions/workflows/{self.workflow_file}/dispatches"

        import json
        payload = {
            "ref": "main",
            "inputs": {
                "fix_data": base64.b64encode(json.dumps(fix_data).encode()).decode(),
                "manifest": base64.b64encode(json.dumps(manifest).encode()).decode() if manifest else ""
            }
        }

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json"
        }

        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()

        time.sleep(2)

        runs_url = f"{self.api_base}/repos/{self.repo}/actions/workflows/{self.workflow_file}/runs"
        runs_response = requests.get(
            runs_url,
            headers=headers,
            params={"per_page": 1, "status": "in_progress,queued"}
        )
        runs_response.raise_for_status()

        runs = runs_response.json()
        if runs["workflow_runs"]:
            return str(runs["workflow_runs"][0]["id"])

        raise RuntimeError("Failed to find triggered workflow run")

    def _wait_for_completion(self, run_id: str) -> Dict[str, Any]:
        """Wait for workflow run to complete and return results.

        Args:
            run_id: GitHub Actions run ID

        Returns:
            Dict with status and conclusion
        """
        url = f"{self.api_base}/repos/{self.repo}/actions/runs/{run_id}"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github.v3+json"
        }

        start_time = time.time()

        while time.time() - start_time < self.max_wait_time:
            response = requests.get(url, headers=headers)
            response.raise_for_status()

            data = response.json()
            status = data["status"]

            if status == "completed":
                return {
                    "conclusion": data["conclusion"],
                    "html_url": data["html_url"]
                }

            time.sleep(self.poll_interval)

        raise TimeoutError(f"Workflow run {run_id} did not complete within {self.max_wait_time}s")

    def _get_logs(self, run_id: str) -> str:
        """Fetch logs from completed workflow run.

        Args:
            run_id: GitHub Actions run ID

        Returns:
            Log content as string
        """
        url = f"{self.api_base}/repos/{self.repo}/actions/runs/{run_id}/logs"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github.v3+json"
        }

        response = requests.get(url, headers=headers)
        response.raise_for_status()

        return response.text

    def test_fix(
        self,
        fix_data: Dict[str, Any],
        manifest: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Test fix in GitHub Actions sandbox."""
        print(f"  🔄 Triggering GitHub Actions workflow...")

        try:
            run_id = self._trigger_workflow(fix_data, manifest)
            print(f"  ⏳ Waiting for run {run_id} to complete...")

            result = self._wait_for_completion(run_id)

            logs = self._get_logs(run_id)

            success = result["conclusion"] == "success"
            print(f"  {'✅' if success else '❌'} Run completed: {result['conclusion']}")

            return {
                "success": success,
                "output": logs,
                "url": result["html_url"],
                "run_id": run_id
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "output": f"Failed to run sandbox test: {e}"
            }


class SelfHostedClient(SandboxClient):
    """Self-hosted CI sandbox runner (enterprise stub)."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)

        self.url = self.sandbox_config.get("url")
        if not self.url:
            raise ValueError("Self-hosted CI URL not specified in config (sandbox.url)")

        self.token = self.sandbox_config.get("token") or os.getenv("CI_TOKEN")
        if not self.token:
            raise ValueError("CI token not found in config or CI_TOKEN env var")

    def test_fix(
        self,
        fix_data: Dict[str, Any],
        manifest: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Test fix in self-hosted CI (enterprise implementation).

        This is a stub for the enterprise version. Implementation details
        will depend on the specific CI system (Jenkins, Azure DevOps, etc.).
        """
        raise NotImplementedError(
            "Self-hosted CI integration is available in Project Red Enterprise. "
            "Contact us for enterprise licensing."
        )


def get_sandbox_client(config: Dict[str, Any]) -> SandboxClient:
    """Factory function to instantiate the appropriate sandbox client.

    Args:
        config: Configuration dict with 'sandbox' section

    Returns:
        SandboxClient instance for the configured runner

    Raises:
        ValueError: If runner is unknown
    """
    runner = config.get("sandbox", {}).get("runner")

    if runner == "github-actions":
        return GHAClient(config)
    elif runner == "self-hosted":
        return SelfHostedClient(config)
    else:
        raise ValueError(
            f"Unknown sandbox runner: {runner}. "
            f"Supported runners: github-actions, self-hosted"
        )


def test_fix_with_retry(
    sandbox_client: SandboxClient,
    fix_data: Dict[str, Any],
    manifest: Optional[Dict[str, Any]] = None,
    max_attempts: int = 5
) -> Dict[str, Any]:
    """Test a fix with retry logic (Phase 2 sandbox loop).

    Args:
        sandbox_client: Initialized sandbox client
        fix_data: Fix data to test
        manifest: Optional dbt manifest
        max_attempts: Maximum number of retry attempts

    Returns:
        Dict with final result including:
        - success: bool
        - attempts: int
        - output: str (final output or error)
        - history: list of all attempts
    """
    history = []

    for attempt in range(1, max_attempts + 1):
        print(f"\n🧪 Sandbox Test — Attempt {attempt}/{max_attempts}")
        print("=" * 50)

        result = sandbox_client.test_fix(fix_data, manifest)
        history.append({
            "attempt": attempt,
            "success": result["success"],
            "output": result.get("output", ""),
            "error": result.get("error")
        })

        if result["success"]:
            print(f"\n✅ Sandbox test passed on attempt {attempt}")
            return {
                "success": True,
                "attempts": attempt,
                "output": result.get("output", ""),
                "history": history
            }
        else:
            print(f"\n❌ Sandbox test failed on attempt {attempt}")
            if result.get("error"):
                print(f"   Error: {result['error']}")

            if attempt < max_attempts:
                print(f"\n   Will retry with same fix (attempt {attempt + 1})...")
            else:
                print(f"\n   Max attempts ({max_attempts}) reached. Giving up.")

    return {
        "success": False,
        "attempts": max_attempts,
        "output": history[-1].get("output", ""),
        "error": f"Failed after {max_attempts} attempts",
        "history": history
    }
