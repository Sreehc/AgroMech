import json
import os
import stat
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_test_automation_scripts_are_present_and_executable() -> None:
    required_scripts = [
        "scripts/lint.sh",
        "scripts/test-unit.sh",
        "scripts/test-integration.sh",
        "scripts/e2e-smoke.sh",
        "scripts/test-all.sh",
    ]

    for script_name in required_scripts:
        script_path = REPO_ROOT / script_name
        assert script_path.exists(), f"{script_name} is missing"
        assert script_path.read_text().startswith("#!/usr/bin/env bash\n")
        assert script_path.stat().st_mode & stat.S_IXUSR, f"{script_name} is not executable"


def test_unified_test_command_runs_lint_unit_integration_and_e2e() -> None:
    test_all = (REPO_ROOT / "scripts/test-all.sh").read_text()

    assert "scripts/lint.sh" in test_all
    assert "scripts/test-unit.sh" in test_all
    assert "scripts/test-integration.sh" in test_all
    assert "scripts/e2e-smoke.sh" in test_all
    assert "npm run build --prefix frontend" in test_all


def test_integration_script_requires_postgres_url_before_starting_python(tmp_path) -> None:
    fake_python = tmp_path / "python"
    marker = tmp_path / "python-started"
    fake_python.write_text(
        '#!/usr/bin/env bash\n: > "$FAKE_PYTHON_MARKER"\nexit 99\n',
        encoding="utf-8",
    )
    fake_python.chmod(fake_python.stat().st_mode | stat.S_IXUSR)
    env = os.environ.copy()
    env.pop("AGROMECH_TEST_POSTGRES_URL", None)
    env["PYTHON_BIN"] = str(fake_python)
    env["FAKE_PYTHON_MARKER"] = str(marker)

    completed = subprocess.run(
        [str(REPO_ROOT / "scripts/test-integration.sh")],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "AGROMECH_TEST_POSTGRES_URL is required" in completed.stderr
    assert not marker.exists()


def test_ci_uses_the_unified_test_command() -> None:
    workflow = REPO_ROOT / ".github/workflows/ci.yml"

    assert workflow.exists()
    workflow_text = workflow.read_text()
    assert "scripts/test-all.sh" in workflow_text
    assert "actions/setup-python" in workflow_text
    assert "actions/setup-node" in workflow_text


def test_frontend_exposes_lint_test_and_build_commands() -> None:
    package_json = json.loads((REPO_ROOT / "frontend/package.json").read_text())
    scripts = package_json["scripts"]

    assert scripts["lint"] == "eslint"
    assert scripts["test"] == "vitest run"
    assert scripts["build"] == "next build"


def test_e2e_smoke_test_entry_exists() -> None:
    e2e_test = REPO_ROOT / "backend/tests/test_e2e_smoke.py"

    assert e2e_test.exists()
