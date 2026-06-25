"""Sandbox client abstraction for SnowDuckAI.

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
        from datetime import datetime, timezone

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

        # Record the trigger time in UTC
        trigger_time = datetime.now(timezone.utc)
        print(f"  📤 Triggering workflow at {trigger_time.isoformat()}")

        import sys
        print(f"  📦 fix_data size: {len(payload['inputs']['fix_data'])} bytes", file=sys.stderr)
        print(f"  📦 manifest size: {len(payload['inputs']['manifest'])} bytes", file=sys.stderr)

        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()

        # Wait longer for GitHub to register the run
        print(f"  ⏳ Waiting for GitHub to register the workflow run...")
        time.sleep(5)

        # Poll for the run with retries
        runs_url = f"{self.api_base}/repos/{self.repo}/actions/workflows/{self.workflow_file}/runs"
        max_poll_attempts = 6
        poll_wait = 2

        for attempt in range(max_poll_attempts):
            runs_response = requests.get(
                runs_url,
                headers=headers,
                params={
                    "per_page": 10,
                    "event": "workflow_dispatch"
                }
            )
            runs_response.raise_for_status()

            runs_data = runs_response.json()
            workflow_runs = runs_data.get("workflow_runs", [])

            print(f"  🔍 Poll attempt {attempt + 1}/{max_poll_attempts}: Found {len(workflow_runs)} workflow_dispatch runs")

            # Debug: show the runs we found
            for i, run in enumerate(workflow_runs[:3]):
                run_time = datetime.fromisoformat(run["created_at"].replace('Z', '+00:00'))
                time_diff = (run_time - trigger_time).total_seconds()
                print(f"     Run {i + 1}: ID={run['id']}, status={run['status']}, created={run['created_at']} (diff: {time_diff:.1f}s)")

            # Find runs created after we triggered (within 60 second window)
            for run in workflow_runs:
                run_created_at = datetime.fromisoformat(run["created_at"].replace('Z', '+00:00'))
                time_diff = (run_created_at - trigger_time).total_seconds()

                # Run should be created within -5 to +60 seconds of trigger time
                # (allow -5s for clock skew)
                if -5 <= time_diff <= 60:
                    print(f"  ✅ Found matching run: ID={run['id']} (created {time_diff:.1f}s after trigger)")
                    return str(run["id"])

            if attempt < max_poll_attempts - 1:
                print(f"     No matching run found yet, waiting {poll_wait}s before retry...")
                time.sleep(poll_wait)

        # Final debug output
        print(f"  ❌ Failed to find triggered run after {max_poll_attempts} attempts")
        print(f"     Trigger time: {trigger_time.isoformat()}")
        print(f"     Latest runs: {json.dumps([{'id': r['id'], 'created_at': r['created_at'], 'status': r['status']} for r in workflow_runs[:3]], indent=2)}")

        raise RuntimeError("Failed to find triggered workflow run after polling")

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
            "Self-hosted CI integration is available in SnowDuckAI Enterprise. "
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
