"""Notifier abstraction for SnowDuckAI.

Supports multiple notification channels:
- Email
- Slack
- Microsoft Teams

Phase 4: Notifies developers of PR creation (success) or failure (max retries).
"""

import os
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

import requests


class Notifier(ABC):
    """Base class for notification channel implementations."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.notify_config = config.get("notify", {})

    @abstractmethod
    def send_success(
        self,
        pr_url: str,
        branch: str,
        fix_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Send success notification when PR is created.

        Args:
            pr_url: URL of the created pull request
            branch: Branch name
            fix_data: Fix data with file_path and explanation

        Returns:
            Dict with 'success' (bool) and optional 'error'
        """
        pass

    @abstractmethod
    def send_failure(
        self,
        error_log: str,
        attempts: int,
        last_error: Optional[str] = None
    ) -> Dict[str, Any]:
        """Send failure notification when max retries reached.

        Args:
            error_log: Original dbt error log
            attempts: Number of attempts made
            last_error: Last error message from sandbox

        Returns:
            Dict with 'success' (bool) and optional 'error'
        """
        pass


class EmailNotifier(Notifier):
    """Email notification channel."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)

        self.to = self.notify_config.get("to")
        if not self.to:
            raise ValueError("Email recipient not specified in config (notify.to)")

        self.smtp_host = self.notify_config.get("smtp_host", "localhost")
        self.smtp_port = self.notify_config.get("smtp_port", 587)
        self.smtp_user = self.notify_config.get("smtp_user") or os.getenv("SMTP_USER")
        self.smtp_password = self.notify_config.get("smtp_password") or os.getenv("SMTP_PASSWORD")
        self.from_address = self.notify_config.get("from", "project-red@localhost")

    def send_success(
        self,
        pr_url: str,
        branch: str,
        fix_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Send success email notification."""
        try:
            import smtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart

            subject = f"✅ SnowDuckAI: PR opened — {branch}"
            body = f"""SnowDuckAI has successfully fixed a dbt error and opened a pull request.

PR URL: {pr_url}
Branch: {branch}

File: {fix_data['file_path']}
Explanation: {fix_data['explanation']}

Please review the pull request and merge when ready.

---
SnowDuckAI — AI-powered dbt error resolution
"""

            msg = MIMEMultipart()
            msg['From'] = self.from_address
            msg['To'] = self.to
            msg['Subject'] = subject
            msg.attach(MIMEText(body, 'plain'))

            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                if self.smtp_user and self.smtp_password:
                    server.starttls()
                    server.login(self.smtp_user, self.smtp_password)
                server.send_message(msg)

            return {"success": True}

        except Exception as e:
            return {"success": False, "error": str(e)}

    def send_failure(
        self,
        error_log: str,
        attempts: int,
        last_error: Optional[str] = None
    ) -> Dict[str, Any]:
        """Send failure email notification."""
        try:
            import smtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart

            subject = f"❌ SnowDuckAI: Failed to fix dbt error after {attempts} attempts"

            error_excerpt = error_log[-500:] if len(error_log) > 500 else error_log

            body = f"""SnowDuckAI attempted to fix a dbt error but was unable to resolve it after {attempts} attempts.

Manual intervention is required.

Original Error:
{error_excerpt}

Last Sandbox Error:
{last_error if last_error else "See sandbox logs for details"}

Please investigate and fix the issue manually.

---
SnowDuckAI — AI-powered dbt error resolution
"""

            msg = MIMEMultipart()
            msg['From'] = self.from_address
            msg['To'] = self.to
            msg['Subject'] = subject
            msg.attach(MIMEText(body, 'plain'))

            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                if self.smtp_user and self.smtp_password:
                    server.starttls()
                    server.login(self.smtp_user, self.smtp_password)
                server.send_message(msg)

            return {"success": True}

        except Exception as e:
            return {"success": False, "error": str(e)}


class SlackNotifier(Notifier):
    """Slack notification channel."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)

        self.webhook_url = self.notify_config.get("webhook_url") or os.getenv("SLACK_WEBHOOK_URL")
        if not self.webhook_url:
            raise ValueError("Slack webhook URL not found in config or SLACK_WEBHOOK_URL env var")

    def send_success(
        self,
        pr_url: str,
        branch: str,
        fix_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Send success Slack notification."""
        try:
            payload = {
                "text": f"✅ *SnowDuckAI: PR Opened*",
                "blocks": [
                    {
                        "type": "header",
                        "text": {
                            "type": "plain_text",
                            "text": "✅ dbt Error Fixed"
                        }
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"SnowDuckAI has successfully fixed a dbt error and opened a pull request."
                        }
                    },
                    {
                        "type": "section",
                        "fields": [
                            {
                                "type": "mrkdwn",
                                "text": f"*File:*\n`{fix_data['file_path']}`"
                            },
                            {
                                "type": "mrkdwn",
                                "text": f"*Branch:*\n`{branch}`"
                            }
                        ]
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*Explanation:*\n{fix_data['explanation']}"
                        }
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {
                                    "type": "plain_text",
                                    "text": "Review PR"
                                },
                                "url": pr_url,
                                "style": "primary"
                            }
                        ]
                    }
                ]
            }

            response = requests.post(self.webhook_url, json=payload)
            response.raise_for_status()

            return {"success": True}

        except Exception as e:
            return {"success": False, "error": str(e)}

    def send_failure(
        self,
        error_log: str,
        attempts: int,
        last_error: Optional[str] = None
    ) -> Dict[str, Any]:
        """Send failure Slack notification."""
        try:
            error_excerpt = error_log[-300:] if len(error_log) > 300 else error_log

            payload = {
                "text": f"❌ *SnowDuckAI: Failed to Fix Error*",
                "blocks": [
                    {
                        "type": "header",
                        "text": {
                            "type": "plain_text",
                            "text": "❌ dbt Error Requires Manual Fix"
                        }
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"SnowDuckAI attempted to fix a dbt error but failed after *{attempts} attempts*. Manual intervention is required."
                        }
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*Error:*\n```\n{error_excerpt}\n```"
                        }
                    }
                ]
            }

            if last_error:
                payload["blocks"].append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Last sandbox error:*\n```\n{last_error[:300]}\n```"
                    }
                })

            response = requests.post(self.webhook_url, json=payload)
            response.raise_for_status()

            return {"success": True}

        except Exception as e:
            return {"success": False, "error": str(e)}


class TeamsNotifier(Notifier):
    """Microsoft Teams notification channel."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)

        self.webhook_url = self.notify_config.get("webhook_url") or os.getenv("TEAMS_WEBHOOK_URL")
        if not self.webhook_url:
            raise ValueError("Teams webhook URL not found in config or TEAMS_WEBHOOK_URL env var")

    def send_success(
        self,
        pr_url: str,
        branch: str,
        fix_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Send success Teams notification."""
        try:
            payload = {
                "@type": "MessageCard",
                "@context": "https://schema.org/extensions",
                "summary": "SnowDuckAI: PR Opened",
                "themeColor": "28a745",
                "title": "✅ dbt Error Fixed",
                "sections": [
                    {
                        "activityTitle": "SnowDuckAI has successfully fixed a dbt error and opened a pull request.",
                        "facts": [
                            {
                                "name": "File",
                                "value": fix_data['file_path']
                            },
                            {
                                "name": "Branch",
                                "value": branch
                            },
                            {
                                "name": "Explanation",
                                "value": fix_data['explanation']
                            }
                        ]
                    }
                ],
                "potentialAction": [
                    {
                        "@type": "OpenUri",
                        "name": "Review PR",
                        "targets": [
                            {
                                "os": "default",
                                "uri": pr_url
                            }
                        ]
                    }
                ]
            }

            response = requests.post(self.webhook_url, json=payload)
            response.raise_for_status()

            return {"success": True}

        except Exception as e:
            return {"success": False, "error": str(e)}

    def send_failure(
        self,
        error_log: str,
        attempts: int,
        last_error: Optional[str] = None
    ) -> Dict[str, Any]:
        """Send failure Teams notification."""
        try:
            error_excerpt = error_log[-300:] if len(error_log) > 300 else error_log

            payload = {
                "@type": "MessageCard",
                "@context": "https://schema.org/extensions",
                "summary": "SnowDuckAI: Failed to Fix Error",
                "themeColor": "dc3545",
                "title": "❌ dbt Error Requires Manual Fix",
                "sections": [
                    {
                        "activityTitle": f"SnowDuckAI failed to fix a dbt error after {attempts} attempts. Manual intervention is required.",
                        "facts": [
                            {
                                "name": "Attempts",
                                "value": str(attempts)
                            },
                            {
                                "name": "Error",
                                "value": error_excerpt
                            }
                        ]
                    }
                ]
            }

            if last_error:
                payload["sections"][0]["facts"].append({
                    "name": "Last sandbox error",
                    "value": last_error[:300]
                })

            response = requests.post(self.webhook_url, json=payload)
            response.raise_for_status()

            return {"success": True}

        except Exception as e:
            return {"success": False, "error": str(e)}


def get_notifier(config: Dict[str, Any]) -> Optional[Notifier]:
    """Factory function to instantiate the appropriate notifier.

    Args:
        config: Configuration dict with 'notify' section

    Returns:
        Notifier instance for the configured channel, or None if not configured

    Raises:
        ValueError: If channel is unknown
    """
    notify_config = config.get("notify", {})
    if not notify_config:
        return None

    channel = notify_config.get("channel")

    if not channel:
        return None

    if channel == "email":
        return EmailNotifier(config)
    elif channel == "slack":
        return SlackNotifier(config)
    elif channel == "teams":
        return TeamsNotifier(config)
    else:
        raise ValueError(
            f"Unknown notification channel: {channel}. "
            f"Supported channels: email, slack, teams"
        )
