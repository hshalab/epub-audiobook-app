import tomllib
from pathlib import Path


def test_pytest_only_collects_the_tests_directory():
    config = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text("utf-8"))

    assert config["tool"]["pytest"]["ini_options"]["testpaths"] == ["tests"]
