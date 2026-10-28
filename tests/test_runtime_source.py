import importlib.util
import io
from pathlib import Path
from zipfile import ZipFile

import pytest

spec = importlib.util.spec_from_file_location(
    "runtime_source", Path(__file__).parents[1] / "deploy/runtime_source.py"
)
assert spec and spec.loader
runtime_source = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime_source)


def test_source_archive_is_deterministic_and_contains_only_reviewed_inputs(tmp_path):
    for source in runtime_source.FILES.values():
        (tmp_path / source).write_text(source)
    (tmp_path / "credentials.json").write_text("do not package")
    first = runtime_source.build_source(tmp_path)
    assert first == runtime_source.build_source(tmp_path)
    with ZipFile(io.BytesIO(first)) as archive:
        assert set(archive.namelist()) == {"Dockerfile", "runtime_downloads.py", "buildspec.yml"}
        assert archive.read("Dockerfile") == b"Dockerfile.decider-test"
    (tmp_path / "runtime_downloads.py").write_text("changed")
    assert runtime_source.build_source(tmp_path) != first


def test_missing_build_input_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        runtime_source.build_source(tmp_path)
