"""The release to PyPI: release.yml as the trusted publisher expects it, and scripts/release.py.

PyPI trusts a run by the workflow file name and the environment, so these are the brief's literals,
not values read from the workflow itself.
"""

from __future__ import annotations

import html
import importlib.util
import re
import shutil
import tomllib
import zipfile
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO / ".github" / "workflows"
RELEASE = WORKFLOWS / "release.yml"
INSTALL_CHECK = WORKFLOWS / "install-check.yml"
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
    return [step for step in job.get("steps", []) if step.get("uses", "").startswith(action + "@")]


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
        "install-check": {"contents": "read"},
        "github-release": {"contents": "write"},
    }


def test_publishing_waits_for_the_tests_and_the_build():
    jobs = workflow()["jobs"]
    chain = {name: job.get("needs") for name, job in jobs.items()}
    assert chain == {
        "check": None, "test": "check", "build": "test", "publish": "build",
        "install-check": "publish", "github-release": "publish",
    }


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
    for ref, comment in uses:
        if ref.startswith("$/"):  # this repository at the same commit (GitHub's self-repository syntax)
            assert (REPO / ref[2:]).is_file(), ref
            continue
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", ref), ref
        assert re.fullmatch(r"\s+# v\d+(\.\d+)*", comment), (ref, comment)


@pytest.mark.parametrize("path", [RELEASE, INSTALL_CHECK], ids=lambda p: p.name)
def test_no_template_expansion_inside_shell_commands(path):
    # The tag name and the version go in through the environment, never pasted into the script by ${{ }}.
    for job in workflow(path)["jobs"].values():
        for step in job.get("steps", []):
            assert "${{" not in step.get("run", ""), step


# --- install-check.yml ----------------------------------------------------------

# The commands of the site's steps 2 and 3, as a newcomer pastes them.
SITE_COMMANDS = {
    "Windows": ['powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"',
                '$env:Path = "$HOME\\.local\\bin;$env:Path"'],
    "other": ["curl -LsSf https://astral.sh/uv/install.sh | sh", "source $HOME/.local/bin/env"],
}


def install_steps() -> list[dict]:
    return workflow(INSTALL_CHECK)["jobs"]["install"]["steps"]


def test_the_install_check_runs_by_hand_and_after_a_release():
    flow = workflow(INSTALL_CHECK)
    on = flow[True]
    assert set(on) == {"workflow_dispatch", "workflow_call"}
    for trigger in on.values():
        assert trigger["inputs"]["version"]["default"] == ""  # empty: the newest on PyPI
    assert flow["permissions"] == {"contents": "read"}
    assert [name for name, job in flow["jobs"].items() if "permissions" in job] == []
    call = workflow()["jobs"]["install-check"]
    assert call["uses"] == "$/.github/workflows/install-check.yml"
    assert call["with"] == {"version": "${{ github.ref_name }}"}


def test_the_install_check_starts_from_a_bare_runner():
    # No action prepares the runner: uv comes from its installer, Python from uv.
    assert "uses:" not in INSTALL_CHECK.read_text(encoding="utf-8")
    install = workflow(INSTALL_CHECK)["jobs"]["install"]
    assert install["strategy"] == {"fail-fast": False,
                                   "matrix": {"os": ["windows-latest", "macos-latest", "ubuntu-latest"]}}
    assert install["env"]["UV_MANAGED_PYTHON"] == "1"


def test_the_install_check_pastes_the_site_commands():
    page = (REPO / "site" / "index.html").read_text(encoding="utf-8")
    site = [html.unescape(re.sub(r"<[^>]+>", "", c)) for c in re.findall(r"<code>(.*?)</code>", page)]
    for system, commands in SITE_COMMANDS.items():
        runs = [s["run"] for s in install_steps() if (s["if"] == "runner.os == 'Windows'") == (system == "Windows")]
        assert len(runs) == (3 if system == "Windows" else 2)
        for command in commands:
            assert command in site, command
        assert runs[0].count(commands[0]) == 1  # the installer, once
        if system == "other":
            # The runner has ~/.local/bin on the PATH, a newcomer does not: without it the installer writes env.
            strip = 'grep -vxF "$HOME/.local/bin"'
            assert strip in runs[0] and runs[0].index(strip) < runs[0].index(commands[0])
        assert all(commands[1] in run for run in runs)  # uv on the PATH in every step
        install = runs[0]
        assert 'uv tool install --refresh-package tgsweep "tgsweep==' in install
        assert "ATTEMPTS" in install and ("sleep 30" in install or "Start-Sleep -Seconds 30" in install)
        for run in runs[1:]:
            for command in ("tgsweep --version", "tgsweep --help", "tgsweep --lang ru --help", "uv python dir"):
                assert command in run, (system, command)


def test_windows_powershell_51_runs_the_same_check():
    steps = [s for s in install_steps() if s["if"] == "runner.os == 'Windows'"]
    assert [s["shell"] for s in steps] == ["pwsh", "pwsh", "powershell"]
    assert steps[2]["run"] == steps[1]["run"]


# --- scripts/release.py ---------------------------------------------------------


UNRELEASED = "## [Unreleased]\n\n### Added\n\n- Something still coming.\n\n"


def set_unreleased(changelog: Path, present: bool) -> None:
    """Puts an [Unreleased] section above the first numbered one, or takes it away."""
    text = changelog.read_text(encoding="utf-8")
    first = re.search(r"^## ", text, flags=re.M).start()
    numbered = re.search(r"^## \[\d", text, flags=re.M).start()
    changelog.write_text(text[:first] + (UNRELEASED if present else "") + text[numbered:], encoding="utf-8")


@pytest.fixture(params=[True, False], ids=["with unreleased", "without unreleased"])
def repo(tmp_path, request):
    """A copy of the files the check reads, with and without an [Unreleased] section on top."""
    for rel in ("pyproject.toml", "CHANGELOG.md", "src/tgsweep/__init__.py"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / rel, tmp_path / rel)
    set_unreleased(tmp_path / "CHANGELOG.md", request.param)
    text = (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8")
    assert ("## [Unreleased]" in text) is request.param
    return tmp_path


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1, old
    path.write_text(text.replace(old, new), encoding="utf-8")


def numbered_heading(repo: Path) -> str:
    return re.search(r"^## \[\d.*$", (repo / "CHANGELOG.md").read_text(encoding="utf-8"), flags=re.M).group()


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


@pytest.mark.parametrize("rel, line, problem", [
    ("pyproject.toml", 'version = "{v}"', "pyproject.toml has version 9.9.8"),
    ("src/tgsweep/__init__.py", '__version__ = "{v}"', "src/tgsweep/__init__.py has __version__ 9.9.8"),
    ("CHANGELOG.md", "## [{v}] - ", "the first numbered section of CHANGELOG.md is 9.9.8"),
], ids=["pyproject", "package", "changelog"])
def test_the_check_names_the_file_that_disagrees(repo, rel, line, problem):
    release = load_script()
    version = release.project_version(repo)
    assert release.check(f"v{version}", repo) == version
    edit(repo / rel, line.format(v=version), line.format(v="9.9.8"))
    with pytest.raises(release.ReleaseError) as caught:
        release.check(f"v{version}", repo)
    assert problem in str(caught.value)


def test_a_release_tagged_before_its_section_is_numbered_stops(repo):
    # The version is raised, but its changes still sit under [Unreleased] (or nowhere): the first
    # numbered section is the previous release.
    release = load_script()
    old = release.project_version(repo)
    edit(repo / "pyproject.toml", f'version = "{old}"', 'version = "9.9.8"')
    edit(repo / "src/tgsweep/__init__.py", f'__version__ = "{old}"', '__version__ = "9.9.8"')
    with pytest.raises(release.ReleaseError, match=f"the first numbered section of CHANGELOG.md is {re.escape(old)}$"):
        release.check("v9.9.8", repo)


def test_the_check_fails_on_an_empty_changelog_section(repo):
    release = load_script()
    version = release.project_version(repo)
    changelog = repo / "CHANGELOG.md"
    text = changelog.read_text(encoding="utf-8")
    heading = numbered_heading(repo)
    start = text.index(heading) + len(heading)
    changelog.write_text(text[:start] + "\n\n" + text[text.index("\n## [", start) + 1:], encoding="utf-8")
    with pytest.raises(release.ReleaseError, match=f"section for {re.escape(version)} is empty"):
        release.check(f"v{version}", repo)


def test_unreleased_further_down_stops_the_check(repo):
    release = load_script()
    text = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    second = re.findall(r"^## \[\d.*$", text, flags=re.M)[1]
    edit(repo / "CHANGELOG.md", second, f"## [Unreleased]\n\n{second}")
    with pytest.raises(release.ReleaseError, match=r"goes only on top"):
        release.check(f"v{release.project_version(repo)}", repo)


def test_another_heading_on_top_stops_the_check(repo):
    release = load_script()
    heading = numbered_heading(repo)
    edit(repo / "CHANGELOG.md", heading, f"## Notes\n\n{heading}")
    with pytest.raises(release.ReleaseError, match="not a version heading: '## Notes'"):
        release.check(f"v{release.project_version(repo)}", repo)


def test_the_release_notes_are_the_section_of_the_version(repo):
    release = load_script()
    text = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    version = release.project_version(repo)
    start = text.index(f"## [{version}]")
    body = text[text.index("\n", start) + 1:text.index("\n## [", start)].strip()
    notes = release.notes(version, repo)
    assert notes == body + "\n"
    assert "### Added" in notes and "## [" not in notes and "Something still coming" not in notes
    # The last section ends before the comparison links.
    oldest = release.notes("0.1.0", repo)
    assert oldest.startswith("### Added") and "]: https://" not in oldest
    with pytest.raises(release.ReleaseError, match="no section for 0.0.1"):
        release.notes("0.0.1", repo)


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


def test_the_newest_python_of_the_package_is_tested():
    # uv gives a newcomer the newest Python, so the newest one the package lists must run in CI and
    # in the release tests. The matrix is the one the brief set: 4 versions on 3 systems, 12 jobs.
    project = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    listed = [c.rsplit(" :: ", 1)[1] for c in project["classifiers"]
              if re.fullmatch(r"Programming Language :: Python :: 3\.\d+", c)]
    assert listed == ["3.11", "3.12", "3.13", "3.14", "3.15"]
    matrix = workflow(WORKFLOWS / "tests.yml")["jobs"]["test"]["strategy"]["matrix"]
    assert matrix == {"os": ["ubuntu-latest", "macos-latest", "windows-latest"], "python": ["3.11", "3.13", "3.14", "3.15"]}
    assert listed[-1] in matrix["python"] and listed[0] in matrix["python"]
    runs = [step.get("run", "") for step in workflow()["jobs"]["test"]["steps"]]
    assert f"uv sync --locked --python {listed[-1]}" in runs
