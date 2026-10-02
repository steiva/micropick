"""The version, in one place.

`pyproject.toml` reads it from here (hatch's version source), the window
shows it in its title, the log writes it at start and a report carries it,
so "which version was running" has one answer. Semantic versioning: the
first number for a change that needs a profile or a habit to change, the
second for a feature, the third for a fix. Each release is a git tag
`v<version>` and an entry in CHANGELOG.md.

`BUILD` is what a packaged build adds - the commit it was built from - and
is empty when running from the source tree (see `packaging/build.py`).
"""

__version__ = "0.1.0"

try:                                     # written by packaging/build.py
    from ._build import BUILD            # type: ignore[import-not-found]
except ImportError:
    BUILD = ""


def describe() -> str:
    """"0.1.0" from the source tree, "0.1.0 (abc1234)" from a build."""
    return f"{__version__} ({BUILD})" if BUILD else __version__
