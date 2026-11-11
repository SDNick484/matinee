"""CLAUDE.md's Definition of done must name every CI job and the commands that gate them (review F6)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLAUDE = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


def done_section():
    return CLAUDE.split("## Definition of done", 1)[1]


def test_every_ci_job_is_named():
    jobs = re.findall(r"^  ([a-z][a-z0-9-]*):\s*$", CI.split("\njobs:", 1)[1], flags=re.M)
    assert set(jobs) == {"lint", "ui-smoke", "test-python", "test-alpine"}, jobs
    done = done_section()
    for name in ("Lint (shellcheck + ruff)", "UI smoke", "Python 3.10", "3.11", "3.12", "3.13", "Alpine"):
        assert name in done, name
    assert "seven" in done


def test_lint_commands_are_listed():
    done = done_section()
    assert "ruff check" in done
    assert "shellcheck -s sh installer/setup-head.sh installer/build.sh ops/matinee-backup ops/matinee-refresh " \
           "ops/matinee-restore-test" in done
    assert "shellcheck -s sh installer/setup-head.sh installer/build.sh ops/matinee-backup ops/matinee-refresh " \
           "ops/matinee-restore-test" in CI


def test_people_and_privacy_rule():
    assert "tests/test_privacy.py" in CLAUDE
    assert re.search(r"[Pp]eople and attendance are\s+editor-only", CLAUDE)
