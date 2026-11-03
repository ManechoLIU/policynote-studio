from pathlib import Path

import pytest

from skills.shared.scripts import output_paths


def test_discover_root_in_fresh_checkout(tmp_path, monkeypatch):
    script = tmp_path / "skills" / "shared" / "scripts" / "output_paths.py"
    script.parent.mkdir(parents=True)
    script.touch()
    (tmp_path / "pyproject.toml").touch()
    (tmp_path / "easel").mkdir()
    (tmp_path / "easel" / "cli.py").touch()
    monkeypatch.delenv("EASEL_ROOT", raising=False)
    monkeypatch.setattr(output_paths, "__file__", str(script))

    assert output_paths._discover_root() == tmp_path.resolve()
    assert not (tmp_path / "outputs").exists()


def test_discover_root_honors_explicit_configuration(tmp_path, monkeypatch):
    configured = tmp_path / "configured"
    monkeypatch.setenv("EASEL_ROOT", str(configured))

    assert output_paths._discover_root() == configured.resolve()


def test_content_output_requires_project_subdirectory(tmp_path, monkeypatch):
    monkeypatch.setattr(output_paths, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(output_paths, "OUTPUTS_DIR", tmp_path / "outputs")

    valid = output_paths.validate_output_path("outputs/成都文旅/成品.md")

    assert valid == tmp_path / "outputs" / "成都文旅" / "成品.md"
    with pytest.raises(output_paths.OutputPathError):
        output_paths.validate_output_path("outputs/成品.md")


@pytest.mark.parametrize("name", ["xhs", "test", "tmp", "output", "主题名"])
def test_generic_project_names_are_rejected(tmp_path, monkeypatch, name):
    monkeypatch.setattr(output_paths, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(output_paths, "OUTPUTS_DIR", tmp_path / "outputs")

    with pytest.raises(output_paths.OutputPathError):
        output_paths.validate_output_path(f"outputs/{name}/result.md")


def test_path_escape_and_unregistered_system_paths_are_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(output_paths, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(output_paths, "OUTPUTS_DIR", tmp_path / "outputs")

    with pytest.raises(output_paths.OutputPathError):
        output_paths.validate_output_path("outputs/主题/../../outside.md")
    with pytest.raises(output_paths.OutputPathError):
        output_paths.validate_output_path("outputs/_random/state.json", allow_system=True)


def test_registered_system_path_requires_explicit_opt_in(tmp_path, monkeypatch):
    monkeypatch.setattr(output_paths, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(output_paths, "OUTPUTS_DIR", tmp_path / "outputs")

    with pytest.raises(output_paths.OutputPathError):
        output_paths.validate_output_path("outputs/_scratch/check.png")
    valid = output_paths.validate_output_path(
        "outputs/_scratch/check.png", allow_system=True,
    )
    assert valid == tmp_path / "outputs" / "_scratch" / "check.png"
