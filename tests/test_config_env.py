"""app.config.load_env: the .env file is read with python-dotenv; real environment variables always win."""
from __future__ import annotations

import os
from pathlib import Path

from app import config


def test_values_from_the_file_are_loaded(tmp_path, monkeypatch):
    monkeypatch.delenv("LG_T_A", raising=False)
    f = tmp_path / ".env"
    f.write_text("LG_T_A=hello\n")
    assert config.load_env(f) is True
    assert os.environ["LG_T_A"] == "hello"


def test_a_real_environment_variable_wins_over_the_file(tmp_path, monkeypatch):
    monkeypatch.setenv("LG_T_B", "from-shell")
    f = tmp_path / ".env"
    f.write_text("LG_T_B=from-file\n")
    config.load_env(f)
    assert os.environ["LG_T_B"] == "from-shell"


def test_comments_quotes_export_and_inline_comments(tmp_path, monkeypatch):
    for k in ("LG_T_C", "LG_T_D", "LG_T_E", "LG_T_F"):
        monkeypatch.delenv(k, raising=False)
    f = tmp_path / ".env"
    f.write_text('# a comment\nLG_T_C="quoted value"\nexport LG_T_D=exported\nLG_T_E=plain   # trailing comment\nLG_T_F=\n')
    config.load_env(f)
    assert (os.environ["LG_T_C"], os.environ["LG_T_D"], os.environ["LG_T_E"], os.environ["LG_T_F"]) == ("quoted value", "exported", "plain", "")


def test_a_dollar_brace_in_a_secret_stays_literal(tmp_path, monkeypatch):
    """No ${VAR} interpolation: a password such as 'a${b}c' must reach the app unchanged."""
    monkeypatch.delenv("LG_T_G", raising=False)
    monkeypatch.setenv("b", "BOOM")
    f = tmp_path / ".env"
    f.write_text("LG_T_G=a${b}c\n")
    config.load_env(f)
    assert os.environ["LG_T_G"] == "a${b}c"


def test_a_missing_file_is_not_an_error(tmp_path):
    assert config.load_env(tmp_path / "nope.env") is False


def test_the_default_path_is_the_repo_root(tmp_path, monkeypatch):
    monkeypatch.delenv("LG_T_H", raising=False)
    (tmp_path / ".env").write_text("LG_T_H=root\n")
    monkeypatch.setattr(config, "ROOT", tmp_path)
    config.load_env()
    assert os.environ["LG_T_H"] == "root"


def test_python_dotenv_is_a_declared_runtime_dependency():
    """The Docker image skips the `checker` group, so the loader must not depend on it."""
    import tomllib
    deps = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())["project"]["dependencies"]
    assert any(d.lower().startswith("python-dotenv") for d in deps)
