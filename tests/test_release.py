"""The release to PyPI: release.yml as the trusted publisher expects it, and scripts/release.py.

PyPI trusts a run by the workflow file name and the environment, so these are the brief's literals,
not values read from the workflow itself.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import zipfile
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO / ".github" / "workflows"
RELEASE = WORKFLOWS / "release.yml"
SCRIPT = REPO / "scripts" / "release.py"
PUBLISH_ACTION = "pypa/gh-action-pypi-publish"


def load_script():
    spec = importlib.util.spec_from_file_location("tgsweep_release", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def workflow(path: Path = RELEASE) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def steps_using(job: dict, action: str) -> list[dict]:
    return [step for step in job["steps"] if step.get("uses", "").startswith(action + "@")]


# --- release.yml ----------------------------------------------------------------


def test_the_release_runs_on_a_version_tag_only():
    on = workflow()[True]  # YAML 1.1 reads the key "on" as true
    assert on == {"push": {"tags": ["v*"]}}


def test_only_the_publish_job_publishes_and_from_the_pypi_environment():
    jobs = workflow()["jobs"]
    publishing = {name for name, job in jobs.items() if steps_using(job, PUBLISH_ACTION)}
    assert publishing == {"publish"}
    publish = jobs["publish"]
    assert publish["environment"]["name"] == "pypi"
    assert [s for s in publish["steps"] if "run" in s] == []  # nothing but the download and the upload
    # No password or token: trusted publishing only.
    assert "with" not in steps_using(publish, PUBLISH_ACTION)[0]
    assert "secrets." not in RELEASE.read_text(encoding="utf-8")


def test_each_job_gets_only_the_permissions_it_needs():
    flow = workflow()
    assert flow["permissions"] == {}
    granted = {name: job.get("permissions") for name, job in flow["jobs"].items()}
    assert granted == {
        "check": {"contents": "read"},
        "test": {"contents": "read"},
        "build": {"contents": "read"},
        "publish": {"id-token": "write"},
        "github-release": {"contents": "write"},
    }


def test_publishing_waits_for_the_tests_and_the_build():
    jobs = workflow()["jobs"]
    chain = {name: job.get("needs") for name, job in jobs.items()}
    assert chain == {"check": None, "test": "check", "build": "test", "publish": "build", "github-release": "publish"}


def test_the_build_is_checked_before_it_is_handed_on():
    runs = [step.get("run", "") for step in workflow()["jobs"]["build"]["steps"]]
    assert any(r.startswith("uv build") for r in runs)
    assert any("twine check --strict dist/*" in r for r in runs)
    assert "python3 scripts/release.py wheel dist/*.whl" in runs
    check = [step.get("run", "") for step in workflow()["jobs"]["check"]["steps"]]
    assert 'python3 scripts/release.py check "$GITHUB_REF_NAME"' in check


@pytest.mark.parametrize("path", sorted(WORKFLOWS.glob("*.yml")), ids=lambda p: p.name)
def test_actions_are_pinned_by_commit_with_the_version_in_a_comment(path):
    uses = re.findall(r"^\s*(?:- )?uses: (\S+)(.*)$", path.read_text(encoding="utf-8"), flags=re.M)
    assert uses
    for ref, comment in uses:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", ref), ref
        assert re.fullmatch(r"\s+# v\d+(\.\d+)*", comment), (ref, comment)


def test_no_template_expansion_inside_shell_commands():
    # The tag name goes in through $GITHUB_REF_NAME, never pasted into the script by ${{ }}.
    for job in workflow()["jobs"].values():
        for step in job["steps"]:
            assert "${{" not in step.get("run", ""), step


# --- scripts/release.py ---------------------------------------------------------


@pytest.fixture
def repo(tmp_path):
    """A copy of the files the check reads."""
    for rel in ("pyproject.toml", "CHANGELOG.md", "src/tgsweep/__init__.py"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / rel, tmp_path / rel)
    return tmp_path


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1, old
    path.write_text(text.replace(old, new), encoding="utf-8")


def test_the_check_passes_for_the_tag_of_this_version(repo):
    release = load_script()
    version = release.project_version(repo)
    assert release.check(f"v{version}", repo) == version


@pytest.mark.parametrize("tag", ["v0.3.0.1", "0.3.0", "v0.3", "release-0.3.0", "v0.3.0-rc1"])
def test_the_check_wants_a_vxyz_tag(repo, tag):
    release = load_script()
    with pytest.raises(release.ReleaseError, match="is not vX.Y.Z"):
        release.check(tag, repo)


def test_the_check_fails_on_a_tag_of_another_version(repo):
    release = load_script()
    with pytest.raises(release.ReleaseError, match="pyproject.toml has version"):
        release.check("v9.9.9", repo)


def top_heading(repo: Path) -> str:
    return re.search(r"^## .*$", (repo / "CHANGELOG.md").read_text(encoding="utf-8"), flags=re.M).group()


@pytest.mark.parametrize("rel, line, problem", [
    ("pyproject.toml", 'version = "{v}"', "pyproject.toml has version 9.9.8"),
    ("src/tgsweep/__init__.py", '__version__ = "{v}"', "src/tgsweep/__init__.py has __version__ 9.9.8"),
    ("CHANGELOG.md", "## [{v}] - ", "the top section of CHANGELOG.md is 9.9.8"),
], ids=["pyproject", "package", "changelog"])
def test_the_check_names_the_file_that_disagrees(repo, rel, line, problem):
    release = load_script()
    version = release.project_version(repo)
    assert release.check(f"v{version}", repo) == version
    edit(repo / rel, line.format(v=version), line.format(v="9.9.8"))
    with pytest.raises(release.ReleaseError) as caught:
        release.check(f"v{version}", repo)
    assert problem in str(caught.value)


def test_the_check_fails_on_an_empty_changelog_section(repo):
    release = load_script()
    version = release.project_version(repo)
    changelog = repo / "CHANGELOG.md"
    text = changelog.read_text(encoding="utf-8")
    heading = top_heading(repo)
    start = text.index(heading) + len(heading)
    changelog.write_text(text[:start] + "\n\n" + text[text.index("\n## [", start) + 1:], encoding="utf-8")
    with pytest.raises(release.ReleaseError, match=f"section for {re.escape(version)} is empty"):
        release.check(f"v{version}", repo)


def test_an_unreleased_section_on_top_stops_the_check(repo):
    release = load_script()
    heading = top_heading(repo)
    edit(repo / "CHANGELOG.md", heading, f"## [Unreleased]\n\n- more\n\n{heading}")
    with pytest.raises(release.ReleaseError, match="not a version heading"):
        release.check(f"v{release.project_version(repo)}", repo)


def test_the_release_notes_are_the_section_of_the_version():
    release = load_script()
    text = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    version = release.project_version()
    start = text.index(f"## [{version}]")
    body = text[text.index("\n", start) + 1:text.index("\n## [", start)].strip()
    notes = release.notes(version)
    assert notes == body + "\n"
    assert "### Added" in notes and "## [" not in notes
    # The last section ends before the comparison links.
    oldest = release.notes("0.1.0")
    assert oldest.startswith("### Added") and "]: https://" not in oldest
    with pytest.raises(release.ReleaseError, match="no section for 0.0.1"):
        release.notes("0.0.1")


def make_wheel(folder: Path, names: list[str], version: str = "0.3.0") -> Path:
    path = folder / f"tgsweep-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as wheel:
        for name in names:
            wheel.writestr(name, "")
    return path


GOOD_WHEEL = [
    "tgsweep/__init__.py", "tgsweep/cli.py",
    "tgsweep-0.3.0.dist-info/METADATA", "tgsweep-0.3.0.dist-info/entry_points.txt",
    "tgsweep-0.3.0.dist-info/RECORD", "tgsweep-0.3.0.dist-info/licenses/LICENSE",
]


def test_a_wheel_of_the_package_and_its_metadata_passes(tmp_path):
    release = load_script()
    assert release.check_wheel(make_wheel(tmp_path, GOOD_WHEEL)) == GOOD_WHEEL


@pytest.mark.parametrize("extra", [
    "tests/test_static.py", "site/index.html", "docs/media/demo-en.gif", "scripts/demo.py",
    "tgsweep-0.2.0.dist-info/METADATA", "conftest.py",
])
def test_a_wheel_with_anything_else_fails(tmp_path, extra):
    release = load_script()
    with pytest.raises(release.ReleaseError, match="holds more than the package"):
        release.check_wheel(make_wheel(tmp_path, GOOD_WHEEL + [extra]))


def test_a_wheel_without_the_package_fails(tmp_path):
    release = load_script()
    with pytest.raises(release.ReleaseError, match="lacks tgsweep/__init__.py"):
        release.check_wheel(make_wheel(tmp_path, [n for n in GOOD_WHEEL if n != "tgsweep/__init__.py"]))


def test_the_command_line_exits_1_with_the_reason(capsys):
    release = load_script()
    version = release.project_version()
    assert release.main(["check", f"v{version}"]) == 0
    assert capsys.readouterr().out == f"releasing {version}\n"
    assert release.main(["check", "v9.9.9"]) == 1
    assert capsys.readouterr().err.startswith("release: the tag v9.9.9 releases 9.9.9, but ")
