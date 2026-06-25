"""SnowDuckAI CLI — User-facing commands for dbt error resolution.

Commands:
  sd init       - Initialize SnowDuckAI in current dbt project
  sd debug      - Run diagnostic agent on dbt error log
"""

import argparse
import shutil
import sys
import time
from pathlib import Path


def cmd_init():
    """Initialize SnowDuckAI in the current directory.

    Creates:
    - .github/workflows/sandbox.yml (copied from package)
    - snowduckai.yml (config template with placeholders)
    """
    print("🚀 Initializing SnowDuckAI in current directory...")

    current_dir = Path.cwd()

    # Create .github/workflows/ directory
    workflows_dir = current_dir / ".github" / "workflows"
    workflows_dir.mkdir(parents=True, exist_ok=True)

    # Copy sandbox.yml from package
    package_dir = Path(__file__).parent.parent
    source_workflow = package_dir / ".github" / "workflows" / "sandbox.yml"
    dest_workflow = workflows_dir / "sandbox.yml"

    if dest_workflow.exists():
        print(f"⚠️  {dest_workflow} already exists, skipping...")
    else:
        try:
            shutil.copy2(source_workflow, dest_workflow)
            print(f"✅ Created {dest_workflow}")
        except FileNotFoundError:
            print(f"❌ Error: Could not find source workflow at {source_workflow}")
            print(f"   Make sure SnowDuckAI is properly installed.")
            sys.exit(1)

    # Create snowduckai.yml config template
    config_file = current_dir / "snowduckai.yml"

    if config_file.exists():
        print(f"⚠️  {config_file} already exists, skipping...")
    else:
        config_template = """# SnowDuckAI Configuration
# Fill in your API keys and settings below

llm:
  provider: anthropic  # anthropic, openai, or ollama
  api_key: ${ANTHROPIC_API_KEY}  # or ${OPENAI_API_KEY} for OpenAI
  model: claude-haiku-4-5  # or gpt-4o-mini for OpenAI, llama3.1:8b for Ollama

sandbox:
  runner: github-actions

git:
  provider: github
  token: ${GITHUB_TOKEN}
  repo: YOUR_ORG/YOUR_REPO  # e.g., acme/dbt-analytics

dbt:
  project_path: .  # path to dbt_project.yml (current dir by default)
  log_path: logs/dbt.log  # path to dbt.log file

notify:
  channel: email  # email, slack, or teams
  to: dev@example.com  # recipient email or webhook URL
  # For email:
  # smtp_host: smtp.gmail.com
  # smtp_port: 587
  # smtp_user: ${SMTP_USER}
  # smtp_password: ${SMTP_PASSWORD}
  # from: snowduckai@example.com
"""
        config_file.write_text(config_template)
        print(f"✅ Created {config_file}")

    print("\n" + "=" * 70)
    print("✅ Initialization complete!")
    print("\nNext steps:")
    print("1. Edit snowduckai.yml and fill in your API keys and GitHub repo")
    print("2. Set environment variables: ANTHROPIC_API_KEY, GITHUB_TOKEN")
    print("3. Run: sd debug")
    print("=" * 70)


def cmd_debug(args):
    """Run the SnowDuckAI diagnostic agent.

    Args:
        args: Parsed command-line arguments
    """
    from snowduckai.agent import load_config, SnowDuckAIAgent

    config_path = args.config
    diagnose_only = args.diagnose

    # Load config
    try:
        config = load_config(config_path)
    except FileNotFoundError:
        print(f"❌ Error: Config file not found: {config_path}")
        print(f"\nRun 'sd init' first to create snowduckai.yml")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error loading config: {e}")
        sys.exit(1)

    # Read log path from config
    log_path = config.get("dbt", {}).get("log_path")
    if not log_path:
        print(f"❌ Error: dbt.log_path not specified in {config_path}")
        print(f"\nAdd 'log_path: logs/dbt.log' under the 'dbt:' section in your config")
        sys.exit(1)

    # Run the agent
    print("🚀 SnowDuckAI — dbt Error Resolution Agent")
    print("=" * 70)

    try:
        agent = SnowDuckAIAgent(config, diagnose_only=diagnose_only)

        if diagnose_only:
            result = agent.diagnose(log_path=log_path)

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
                print(f"\n   Next step: Run without --diagnose to test in sandbox")
            else:
                print("❌ DIAGNOSTIC FAILED")
                print(f"   Error: {result['error']}")
                print(f"   Iterations: {result['iterations']}")
                sys.exit(1)
        else:
            result = agent.run_full_workflow(log_path=log_path)

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
                sys.exit(1)

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def cmd_watch(args):
    """Watch dbt.log and trigger agent automatically on error.

    Args:
        args: Parsed command-line arguments
    """
    from snowduckai.agent import load_config, SnowDuckAIAgent

    config_path = args.config

    # Load config
    try:
        config = load_config(config_path)
    except FileNotFoundError:
        print(f"❌ Error: Config file not found: {config_path}")
        print(f"\nRun 'sd init' first to create snowduckai.yml")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error loading config: {e}")
        sys.exit(1)

    # Read log path from config
    log_path_str = config.get("dbt", {}).get("log_path")
    if not log_path_str:
        print(f"❌ Error: dbt.log_path not specified in {config_path}")
        print(f"\nAdd 'log_path: logs/dbt.log' under the 'dbt:' section in your config")
        sys.exit(1)

    log_path = Path(log_path_str)

    print("👀 SnowDuckAI — Watching for dbt errors")
    print("=" * 70)
    print(f"   Monitoring: {log_path}")
    print(f"   Config: {config_path}")
    print(f"   Press Ctrl+C to stop")
    print("=" * 70)

    # Wait for log file to exist
    while not log_path.exists():
        print(f"⏳ Waiting for {log_path} to be created...")
        time.sleep(2)

    print(f"✅ Log file found, watching for errors...\n")

    # Track file position and last error time
    last_position = log_path.stat().st_size
    last_error_time = 0
    cooldown_seconds = 60  # Don't trigger again within 60 seconds

    try:
        while True:
            time.sleep(1)

            # Check if file has grown
            current_size = log_path.stat().st_size
            if current_size < last_position:
                # File was truncated/rotated
                last_position = 0

            if current_size > last_position:
                # Read new content
                with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                    f.seek(last_position)
                    new_content = f.read()
                    last_position = current_size

                # Check for dbt error patterns
                if _detect_dbt_error(new_content):
                    current_time = time.time()
                    if current_time - last_error_time < cooldown_seconds:
                        print(f"⏸️  Error detected but in cooldown period ({int(current_time - last_error_time)}s ago)")
                        continue

                    print("\n" + "=" * 70)
                    print("🚨 DBT ERROR DETECTED")
                    print("=" * 70)
                    print(f"   Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}")
                    print(f"   Triggering SnowDuckAI agent...\n")

                    last_error_time = current_time

                    # Trigger the agent workflow
                    try:
                        agent = SnowDuckAIAgent(config, diagnose_only=False)
                        result = agent.run_full_workflow(log_path=log_path_str)

                        print("\n" + "=" * 70)
                        print("WORKFLOW RESULT")
                        print("=" * 70)

                        if result["success"]:
                            print("✅ SUCCESS")
                            print(f"   Pull Request: {result['pr_url']}")
                            print(f"   Branch: {result['branch']}")
                            print(f"   The fix has been verified and is ready for review!")
                        else:
                            print("❌ FAILED")
                            print(f"   Phase: {result['phase']}")
                            print(f"   Error: {result['error']}")

                    except Exception as e:
                        print(f"\n❌ Agent error: {e}")
                        import traceback
                        traceback.print_exc()

                    print("\n" + "=" * 70)
                    print(f"👀 Resuming watch on {log_path}...")
                    print("=" * 70 + "\n")

    except KeyboardInterrupt:
        print("\n\n👋 Stopping watch mode")
        sys.exit(0)


def _detect_dbt_error(content: str) -> bool:
    """Detect if content contains a dbt error.

    Args:
        content: Log content to check

    Returns:
        True if error detected, False otherwise
    """
    error_patterns = [
        "Compilation Error",
        "Runtime Error",
        "Database Error",
        "Unhandled error",
        "ERROR =",
        "Completed with 1 error",
        "Completed with errors",
    ]

    content_lower = content.lower()
    for pattern in error_patterns:
        if pattern.lower() in content_lower:
            return True

    return False


def main():
    """Main CLI entry point for SnowDuckAI."""
    parser = argparse.ArgumentParser(
        prog="sd",
        description="SnowDuckAI — AI-powered dbt error resolution",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  sd init                  Initialize in current dbt project
  sd debug                 Run full diagnostic workflow
  sd debug --diagnose      Diagnose only (no sandbox/PR)
  sd watch                 Watch dbt.log and trigger automatically on error

Environment variables:
  ANTHROPIC_API_KEY    Anthropic API key (if using Claude)
  OPENAI_API_KEY       OpenAI API key (if using GPT)
  GITHUB_TOKEN         GitHub personal access token
  SMTP_USER            SMTP username (if using email notifications)
  SMTP_PASSWORD        SMTP password (if using email notifications)
"""
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # sd init
    init_parser = subparsers.add_parser(
        "init",
        help="Initialize SnowDuckAI in current dbt project"
    )

    # sd debug
    debug_parser = subparsers.add_parser(
        "debug",
        help="Run diagnostic agent on dbt error log"
    )
    debug_parser.add_argument(
        "--config",
        default="snowduckai.yml",
        help="Path to config file (default: snowduckai.yml)"
    )
    debug_parser.add_argument(
        "--diagnose",
        action="store_true",
        help="Run diagnostic only (skip sandbox, PR, and notification)"
    )

    # sd watch
    watch_parser = subparsers.add_parser(
        "watch",
        help="Watch dbt.log and trigger agent automatically on error"
    )
    watch_parser.add_argument(
        "--config",
        default="snowduckai.yml",
        help="Path to config file (default: snowduckai.yml)"
    )

    args = parser.parse_args()

    if args.command == "init":
        cmd_init()
    elif args.command == "debug":
        cmd_debug(args)
    elif args.command == "watch":
        cmd_watch(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
