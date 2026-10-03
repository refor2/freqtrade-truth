import tomllib
from importlib.metadata import version
from pathlib import Path

from freqtrade_truth import __version__


def test_version_is_defined() -> None:
    assert __version__ == "0.0.1"


def test_runtime_version_matches_package_metadata() -> None:
    assert version("freqtrade-truth") == __version__


def test_runtime_version_matches_pyproject() -> None:
    root = Path(__file__).resolve().parents[1]
    with (root / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)["project"]

    assert project["version"] == __version__
