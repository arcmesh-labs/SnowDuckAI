"""Git handler abstraction for SnowDuckAI.

Supports multiple git providers via a common interface:
- GitHub (open source)
- GitHub Enterprise (enterprise)
- GitLab (enterprise)

Phase 3: Creates branch, commits fix, pushes, and opens PR with description.
"""

import os
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import requests


class GitHandler(ABC):
    """Base class for git provider implementations."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.git_config = config.get("git", {})
        self.dbt_config = config.get("dbt", {})
        self.dbt_project_path = Path(self.dbt_config.get("project_path", "./dbt-project"))

    @abstractmethod
    def create_pr(
        self,
        fix_data: Dict[str, Any],
        error_log: str,
        diagnostic_summary: Optional[str] = None,
        sandbox_attempts: int = 1
    ) -> Dict[str, Any]:
        """Create a pull request with the verified fix.

        Args:
            fix_data: Dict with file_path, fixed_content, explanation
            error_log: Original error log that triggered the fix
            diagnostic_summary: Optional summary of diagnostic process
            sandbox_attempts: Number of sandbox attempts before success

        Returns:
            Dict with 'success' (bool), 'pr_url' (str), and optional 'error'
        """
        pass


class GitHubHandler(GitHandler):
    """GitHub git handler."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)

        self.token = self.git_config.get("token") or os.getenv("GITHUB_TOKEN")
        if not self.token:
            raise ValueError("GitHub token not found in config or GITHUB_TOKEN env var")

        self.repo = self.git_config.get("repo")
        if not self.repo:
            raise ValueError("GitHub repo not specified in config (git.repo)")

        self.api_base = "https://api.github.com"
        self.base_branch = self.git_config.get("base_branch", "main")

    def _get_headers(self) -> Dict[str, str]:
        """Get HTTP headers for GitHub API requests."""
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json"
        }

    def _get_default_branch_sha(self) -> str:
        """Get the SHA of the default branch HEAD."""
        url = f"{self.api_base}/repos/{self.repo}/git/refs/heads/{self.base_branch}"
        response = requests.get(url, headers=self._get_headers())
        response.raise_for_status()

        data = response.json()
        return data["object"]["sha"]

    def _create_branch(self, branch_name: str, base_sha: str) -> None:
        """Create a new branch from base SHA.

        Args:
            branch_name: Name of the branch to create
            base_sha: SHA to branch from
        """
        url = f"{self.api_base}/repos/{self.repo}/git/refs"
        payload = {
            "ref": f"refs/heads/{branch_name}",
            "sha": base_sha
        }

        response = requests.post(url, json=payload, headers=self._get_headers())
        response.raise_for_status()

    def _get_file_sha(self, file_path: str, branch: str) -> Optional[str]:
        """Get the SHA of a file on a branch.

        Args:
            file_path: Path to the file in the repo
            branch: Branch name

        Returns:
            File SHA or None if file doesn't exist
        """
        url = f"{self.api_base}/repos/{self.repo}/contents/{file_path}"
        response = requests.get(
            url,
            headers=self._get_headers(),
            params={"ref": branch}
        )

        if response.status_code == 404:
            return None

        response.raise_for_status()
        return response.json()["sha"]

    def _commit_file(
        self,
        file_path: str,
        content: str,
        branch: str,
        commit_message: str
    ) -> None:
        """Commit a file to a branch.

        Args:
            file_path: Path to the file in the repo
            content: New file content
            branch: Branch to commit to
            commit_message: Commit message
        """
        import base64

        url = f"{self.api_base}/repos/{self.repo}/contents/{file_path}"

        file_sha = self._get_file_sha(file_path, branch)

        payload = {
            "message": commit_message,
            "content": base64.b64encode(content.encode()).decode(),
            "branch": branch
        }

        if file_sha:
            payload["sha"] = file_sha

        response = requests.put(url, json=payload, headers=self._get_headers())
        response.raise_for_status()

    def _create_pull_request(
        self,
        title: str,
        body: str,
        head_branch: str,
        base_branch: str
    ) -> str:
        """Create a pull request.

        Args:
            title: PR title
            body: PR description
            head_branch: Source branch
            base_branch: Target branch

        Returns:
            PR URL
        """
        url = f"{self.api_base}/repos/{self.repo}/pulls"
        payload = {
            "title": title,
            "body": body,
            "head": head_branch,
            "base": base_branch
        }

        response = requests.post(url, json=payload, headers=self._get_headers())
        response.raise_for_status()

        data = response.json()
        return data["html_url"]

    def _generate_pr_description(
        self,
        fix_data: Dict[str, Any],
        error_log: str,
        diagnostic_summary: Optional[str],
        sandbox_attempts: int
    ) -> str:
        """Generate PR description from fix data and context.

        Args:
            fix_data: Fix data
            error_log: Original error log
            diagnostic_summary: Diagnostic summary
            sandbox_attempts: Number of sandbox attempts

        Returns:
            PR description markdown
        """
        error_excerpt = error_log[-500:] if len(error_log) > 500 else error_log

        description = f"""## 🤖 Automated dbt Fix

This PR was automatically generated by **SnowDuckAI** to fix a dbt pipeline error.

### What Failed

The dbt pipeline encountered an error:

```
...{error_excerpt}
```

### What Was Investigated

{diagnostic_summary if diagnostic_summary else "The agent analyzed the error log and examined relevant dbt models and configuration files."}

### Fix Applied

**File:** `{fix_data['file_path']}`

**Explanation:** {fix_data['explanation']}

### Verification

✅ This fix was tested in an isolated DuckDB sandbox environment:
- Sandbox attempts: {sandbox_attempts}
- Status: **Passed** (`dbt run` completed successfully)

### Review Checklist

- [ ] Review the code changes
- [ ] Verify the fix addresses the root cause
- [ ] Check for any side effects
- [ ] Run additional tests if needed

---

_Generated by [SnowDuckAI](https://github.com/your-org/snowduckai) — AI-powered dbt error resolution_
"""
        return description

    def create_pr(
        self,
        fix_data: Dict[str, Any],
        error_log: str,
        diagnostic_summary: Optional[str] = None,
        sandbox_attempts: int = 1
    ) -> Dict[str, Any]:
        """Create a pull request with the verified fix."""
        try:
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            branch_name = f"fix/dbt-{timestamp}"

            print(f"📂 Creating branch: {branch_name}")
            base_sha = self._get_default_branch_sha()
            self._create_branch(branch_name, base_sha)

            print(f"💾 Committing fix to {fix_data['file_path']}")
            commit_message = f"Fix dbt error in {fix_data['file_path']}\n\n{fix_data['explanation']}"
            self._commit_file(
                file_path=fix_data["file_path"],
                content=fix_data["fixed_content"],
                branch=branch_name,
                commit_message=commit_message
            )

            print(f"🔀 Creating pull request...")
            pr_title = f"🤖 Fix dbt error in {fix_data['file_path']}"
            pr_body = self._generate_pr_description(
                fix_data=fix_data,
                error_log=error_log,
                diagnostic_summary=diagnostic_summary,
                sandbox_attempts=sandbox_attempts
            )

            pr_url = self._create_pull_request(
                title=pr_title,
                body=pr_body,
                head_branch=branch_name,
                base_branch=self.base_branch
            )

            print(f"✅ Pull request created: {pr_url}")

            return {
                "success": True,
                "pr_url": pr_url,
                "branch": branch_name
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }


class GHEHandler(GitHandler):
    """GitHub Enterprise git handler (enterprise stub)."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)

        self.url = self.git_config.get("url")
        if not self.url:
            raise ValueError("GitHub Enterprise URL not specified in config (git.url)")

        self.token = self.git_config.get("token") or os.getenv("GIT_TOKEN")
        if not self.token:
            raise ValueError("GHE token not found in config or GIT_TOKEN env var")

        self.repo = self.git_config.get("repo")
        if not self.repo:
            raise ValueError("Repo not specified in config (git.repo)")

    def create_pr(
        self,
        fix_data: Dict[str, Any],
        error_log: str,
        diagnostic_summary: Optional[str] = None,
        sandbox_attempts: int = 1
    ) -> Dict[str, Any]:
        """Create PR in GitHub Enterprise (enterprise implementation).

        This is a stub for the enterprise version. The implementation would be
        very similar to GitHubHandler but using the self-hosted API endpoint.
        """
        raise NotImplementedError(
            "GitHub Enterprise integration is available in SnowDuckAI Enterprise. "
            "Contact us for enterprise licensing."
        )


class GitLabHandler(GitHandler):
    """GitLab git handler (enterprise stub)."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)

        self.url = self.git_config.get("url")
        if not self.url:
            raise ValueError("GitLab URL not specified in config (git.url)")

        self.token = self.git_config.get("token") or os.getenv("GITLAB_TOKEN")
        if not self.token:
            raise ValueError("GitLab token not found in config or GITLAB_TOKEN env var")

        self.project = self.git_config.get("project")
        if not self.project:
            raise ValueError("GitLab project not specified in config (git.project)")

    def create_pr(
        self,
        fix_data: Dict[str, Any],
        error_log: str,
        diagnostic_summary: Optional[str] = None,
        sandbox_attempts: int = 1
    ) -> Dict[str, Any]:
        """Create merge request in GitLab (enterprise implementation).

        This is a stub for the enterprise version. Implementation would use
        the GitLab API to create branches and merge requests.
        """
        raise NotImplementedError(
            "GitLab integration is available in SnowDuckAI Enterprise. "
            "Contact us for enterprise licensing."
        )


def get_git_handler(config: Dict[str, Any]) -> GitHandler:
    """Factory function to instantiate the appropriate git handler.

    Args:
        config: Configuration dict with 'git' section

    Returns:
        GitHandler instance for the configured provider

    Raises:
        ValueError: If provider is unknown
    """
    provider = config.get("git", {}).get("provider")

    if provider == "github":
        return GitHubHandler(config)
    elif provider == "github-enterprise":
        return GHEHandler(config)
    elif provider == "gitlab":
        return GitLabHandler(config)
    else:
        raise ValueError(
            f"Unknown git provider: {provider}. "
            f"Supported providers: github, github-enterprise, gitlab"
        )
