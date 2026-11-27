"""Release checks, run by .github/workflows/release.yml and by tests/test_release.py.

    python3 scripts/release.py check v0.3.0        the tag, pyproject.toml, __version__ and CHANGELOG.md agree
    python3 scripts/release.py notes 0.3.0         the CHANGELOG.md section of a version, for the GitHub Release
    python3 scripts/release.py wheel dist/*.whl    the wheel holds only the tgsweep package and its metadata

Only the standard library: the release job runs it with the runner's own Python (3.11 or newer).
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = "tgsweep"
# A version section of CHANGELOG.md, as Keep a Changelog writes it: ## [0.3.0] - 2026-11-27
SECTION = re.compile(r"^## \[(?P<version>[^\]]+)\] - (?P<date>\d{4}-\d{2}-\d{2})$")
LINK = re.compile(r"^\[[^\]]+\]: ")  # the comparison links at the end of the file


class ReleaseError(Exception):
    pass


def project_version(root: Path = ROOT) -> str:
    return tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def package_version(root: Path = ROOT) -> str:
    text = (root / "src" / PACKAGE / "__init__.py").read_text(encoding="utf-8")
    found = re.findall(r'^__version__ = "([^"]+)"$', text, flags=re.M)
    if len(found) != 1:
        raise ReleaseError(f"src/{PACKAGE}/__init__.py: expected one __version__ line, found {len(found)}")
    return found[0]


def changelog_sections(root: Path = ROOT) -> list[tuple[str, str]]:
    """(version, text) of every section from the top. Any other second-level heading is an error."""
    sections: list[tuple[str, list[str]]] = []
    for line in (root / "CHANGELOG.md").read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            match = SECTION.match(line)
            if not match:
                raise ReleaseError(f"CHANGELOG.md: not a version heading: {line!r}")
            sections.append((match["version"], []))
        elif sections and not LINK.match(line):
            sections[-1][1].append(line)
    return [(version, "\n".join(lines).strip()) for version, lines in sections]


def check(tag: str, root: Path = ROOT) -> str:
    """The version this tag releases, if everything that names a version agrees with it."""
    if not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise ReleaseError(f"the tag {tag!r} is not vX.Y.Z")
    version = tag[1:]
    problems = []
    if (found := project_version(root)) != version:
        problems.append(f"pyproject.toml has version {found}")
    if (found := package_version(root)) != version:
        problems.append(f"src/{PACKAGE}/__init__.py has __version__ {found}")
    sections = changelog_sections(root)
    if not sections or sections[0][0] != version:
        top = sections[0][0] if sections else "none"
        problems.append(f"the top section of CHANGELOG.md is {top}")
    elif not sections[0][1]:
        problems.append(f"the CHANGELOG.md section for {version} is empty")
    if problems:
        raise ReleaseError(f"the tag {tag} releases {version}, but " + "; ".join(problems))
    return version


def notes(version: str, root: Path = ROOT) -> str:
    for found, text in changelog_sections(root):
        if found == version:
            return text + "\n"
    raise ReleaseError(f"CHANGELOG.md has no section for {version}")


def check_wheel(path: Path) -> list[str]:
    """The names in the wheel, if they are the package and its metadata and nothing else."""
    match = re.fullmatch(rf"{PACKAGE}-(\d+\.\d+\.\d+)-py3-none-any\.whl", path.name)
    if not match:
        raise ReleaseError(f"{path.name}: not a {PACKAGE} wheel for any Python")
    meta = f"{PACKAGE}-{match[1]}.dist-info/"
    with zipfile.ZipFile(path) as wheel:
        names = wheel.namelist()
    extra = [name for name in names if not name.startswith((f"{PACKAGE}/", meta))]
    if extra:
        raise ReleaseError(f"{path.name} holds more than the package: {', '.join(extra)}")
    missing = {f"{PACKAGE}/__init__.py", f"{meta}METADATA", f"{meta}entry_points.txt"} - set(names)
    if missing:
        raise ReleaseError(f"{path.name} lacks {', '.join(sorted(missing))}")
    return names


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="release.py", description="tgsweep release checks")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check").add_argument("tag")
    commands.add_parser("notes").add_argument("version")
    commands.add_parser("wheel").add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            print(f"releasing {check(args.tag)}")
        elif args.command == "notes":
            sys.stdout.write(notes(args.version))
        else:
            for path in args.paths:
                print("\n".join(check_wheel(path)))
    except ReleaseError as error:
        print(f"release: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
