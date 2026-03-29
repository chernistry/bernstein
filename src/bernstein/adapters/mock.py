"""Mock CLI adapter for zero-API-key demos and testing."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from typing import TYPE_CHECKING, Any

from bernstein.adapters.base import CLIAdapter, SpawnResult

if TYPE_CHECKING:
    from pathlib import Path

    from bernstein.core.models import ModelConfig


class MockAgentAdapter(CLIAdapter):
    """Simulates an agent without making real API calls.

    Used for demos and testing. Spawns a subprocess that applies
    pre-scripted changes to the project and exits successfully.
    """

    def spawn(
        self,
        *,
        prompt: str,
        workdir: Path,
        model_config: ModelConfig,
        session_id: str,
        mcp_config: dict[str, Any] | None = None,
    ) -> SpawnResult:
        """Spawn a mock agent subprocess that applies demo changes.

        Args:
            prompt: Agent task description (analyzed to determine action).
            workdir: Project root directory.
            model_config: Model configuration (unused for mock).
            session_id: Unique session identifier.
            mcp_config: MCP configuration (unused for mock).

        Returns:
            SpawnResult with mock process PID and log path.
        """
        # Create log file
        log_path = workdir / ".sdd" / "runtime" / f"agent-{session_id}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)

        # Determine which task this is based on the prompt content
        task_name = self._identify_task(prompt)

        # Create a temporary Python script that will simulate the agent work
        script_content = self._build_mock_script()
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".py",
            delete=False,
            dir=workdir / ".sdd" / "runtime",
        ) as tmp:
            tmp.write(script_content)
            tmp.flush()
            script_path = tmp.name

        # Pass task info as JSON to avoid shell quoting issues
        task_info = json.dumps(
            {
                "workdir": str(workdir),
                "task_name": task_name,
                "log_path": str(log_path),
            }
        )

        cmd = [
            sys.executable,
            script_path,
            task_info,
        ]

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=str(workdir),
        )

        return SpawnResult(pid=proc.pid, log_path=log_path, proc=proc)

    def name(self) -> str:
        """Return adapter name."""
        return "mock"

    @staticmethod
    def _identify_task(prompt: str) -> str:
        """Identify which task this is from the prompt text.

        Args:
            prompt: Agent task description.

        Returns:
            Task identifier: "health_check", "tests", "error_handling".
        """
        prompt_lower = prompt.lower()
        if "health" in prompt_lower or "/health" in prompt_lower:
            return "health_check"
        if "test" in prompt_lower:
            return "tests"
        if "error" in prompt_lower or "handler" in prompt_lower:
            return "error_handling"
        return "unknown"

    @staticmethod
    def _build_mock_script() -> str:
        """Build a Python script that simulates agent work.

        Returns:
            Python script source code.
        """
        return '''#!/usr/bin/env python3
"""Mock agent worker that simulates task completion."""
import json
import sys
import time
from pathlib import Path


def write_log(path: Path, message: str) -> None:
    """Append message to log file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(f"{time.time()} {message}\\n")


def add_health_endpoint(workdir: Path, log_path: Path) -> None:
    """Add /health endpoint to app.py."""
    app_file = workdir / "app.py"
    content = app_file.read_text()

    if "@app.route(\\"/health\\")" not in content:
        # Find where to insert the new endpoint (before if __name__)
        insert_point = content.find("if __name__ == ")
        if insert_point > 0:
            new_endpoint = (
                '\\n\\n@app.route(\\"/health\\")\\n'
                'def health() -> object:\\n'
                '    """Health check endpoint."""\\n'
                '    return jsonify({"status": "healthy", "version": "1.0.0"})\\n'
            )
            new_content = content[:insert_point] + new_endpoint + content[insert_point:]
            app_file.write_text(new_content)
            write_log(log_path, "✓ Added /health endpoint to app.py")
        else:
            write_log(log_path, "⚠ Could not find insertion point for health endpoint")


def add_tests(workdir: Path, log_path: Path) -> None:
    """Add comprehensive tests to test_app.py."""
    test_file = workdir / "tests" / "test_app.py"
    test_content = \'\'\'"Comprehensive tests for app.py."\\nimport pytest\\nfrom app import app\\n\\n\\n@pytest.fixture\\ndef client():\\n    app.config["TESTING"] = True\\n    with app.test_client() as c:\\n        yield c\\n\\n\\ndef test_hello(client):\\n    """Test the hello endpoint."""\\n    resp = client.get("/")\\n    assert resp.status_code == 200\\n    assert "message" in resp.json\\n\\n\\ndef test_health(client):\\n    """Test the health check endpoint."""\\n    resp = client.get("/health")\\n    assert resp.status_code == 200\\n    assert resp.json["status"] == "healthy"\\n\'\'\'
    test_file.write_text(test_content)
    write_log(log_path, "✓ Added comprehensive tests to test_app.py")


def add_error_handlers(workdir: Path, log_path: Path) -> None:
    """Add error handling middleware to app.py."""
    app_file = workdir / "app.py"
    content = app_file.read_text()

    if "@app.errorhandler" not in content:
        insert_point = content.find("if __name__ == ")
        if insert_point > 0:
            error_handlers = (
                '\\n\\n@app.errorhandler(404)\\n'
                'def not_found(e):  # type: ignore[misc]\\n'
                '    """Handle 404 errors."""\\n'
                '    return jsonify({"error": "Not found", "status": 404}), 404\\n'
                '\\n'
                '@app.errorhandler(500)\\n'
                'def server_error(e):  # type: ignore[misc]\\n'
                '    """Handle 500 errors."""\\n'
                '    return jsonify({"error": "Internal server error", "status": 500}), 500\\n'
            )
            new_content = content[:insert_point] + error_handlers + content[insert_point:]
            app_file.write_text(new_content)
            write_log(log_path, "✓ Added error handlers to app.py")
        else:
            write_log(log_path, "⚠ Could not find insertion point for error handlers")


def main():
    """Main entry point."""
    task_info = json.loads(sys.argv[1])
    workdir = Path(task_info["workdir"])
    task_name = task_info["task_name"]
    log_path = Path(task_info["log_path"])

    write_log(log_path, f"Mock agent started for task: {task_name}")

    # Simulate realistic work time
    time.sleep(1.5)

    # Apply task-specific changes
    if task_name == "health_check":
        add_health_endpoint(workdir, log_path)
    elif task_name == "tests":
        add_tests(workdir, log_path)
    elif task_name == "error_handling":
        add_error_handlers(workdir, log_path)
    else:
        write_log(log_path, f"Unknown task type: {task_name}")

    # Simulate remaining work
    time.sleep(0.5)
    write_log(log_path, "Mock agent completed successfully")


if __name__ == "__main__":
    main()
'''
