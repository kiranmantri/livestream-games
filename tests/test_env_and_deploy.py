"""Tooling around .env: .env.example, docker compose pass-through, the Fly secret sync and the deploy script (with a stub `fly`)."""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from dotenv import dotenv_values

from deploy import env_secrets as ES

ROOT = Path(__file__).resolve().parents[1]
API_KEYS = ("GROQ_API_KEY", "XAI_API_KEY", "OPENROUTER_API_KEY")
CORE = ("DATABASE_URL", "POSTGRES_PASSWORD", "ADMIN_PASSWORD", "SESSION_SECRET")
FAKE_GROQ = "gsk_FAKE_test_value_0123456789"


# ---------------------------------------------------------------- .env.example
def test_env_example_documents_the_provider_keys_with_empty_values():
    ex = dotenv_values(ROOT / ".env.example")
    for k in API_KEYS:
        assert k in ex and ex[k] in ("", None), f"{k} must be present and EMPTY in the example"
    for k in ("STT_PROVIDER", "STT_LANGUAGE"):
        assert k in ex


def test_env_example_has_no_real_looking_secret():
    text = (ROOT / ".env.example").read_text()
    assert not re.search(r"gsk_[A-Za-z0-9]{10,}|sk-or-[A-Za-z0-9]{10,}|xai-[A-Za-z0-9]{10,}", text)


def test_every_secret_a_fly_container_gets_is_documented_in_the_example():
    ex = dotenv_values(ROOT / ".env.example")
    assert ES.machine_secret_names(ROOT / "deploy" / "fly.machine.json") <= set(ex)


def test_every_variable_compose_interpolates_is_documented_in_the_example():
    ex = set(dotenv_values(ROOT / ".env.example"))
    used = set(re.findall(r"\$\{([A-Z_]+)", (ROOT / "docker-compose.yml").read_text()))
    assert used <= ex, used - ex


# ---------------------------------------------------------------- compose / Dockerfile: secrets only at runtime
def test_compose_passes_the_stt_settings_and_keys_to_the_app_at_runtime():
    text = (ROOT / "docker-compose.yml").read_text()
    for k in ("STT_PROVIDER", "STT_LANGUAGE", "GROQ_API_KEY", "XAI_API_KEY", "CHECKER_MODEL", "OPENROUTER_API_KEY"):
        assert f"{k}: ${{{k}" in text, k
    assert "args:" not in text                                      # nothing from .env becomes a build argument (it would stay in the image layers)


def test_no_secret_is_baked_into_the_image():
    docker = (ROOT / "Dockerfile").read_text()
    assert not re.search(r"^\s*(ARG|ENV)\s+\S*(KEY|SECRET|PASSWORD|TOKEN)", docker, re.MULTILINE | re.IGNORECASE)
    ignore = (ROOT / ".dockerignore").read_text().split()
    assert ".env" in ignore and ".env.*" in ignore and "!.env.example" in ignore


# ---------------------------------------------------------------- deploy/env_secrets.py
def machine(tmp_path, listed=("GROQ_API_KEY", "ADMIN_PASSWORD")):
    f = tmp_path / "machine.json"
    f.write_text(json.dumps({"containers": [{"name": "app", "secrets": [{"env_var": n, "name": n} for n in listed]}, {"name": "db"}]}))
    return f


def test_machine_secret_names_reads_every_container(tmp_path):
    assert ES.machine_secret_names(machine(tmp_path)) == {"GROQ_API_KEY", "ADMIN_PASSWORD"}


def test_only_listed_api_keys_with_a_value_are_selected(tmp_path):
    env = {"GROQ_API_KEY": FAKE_GROQ, "XAI_API_KEY": "xai-x", "OPENROUTER_API_KEY": "", "ADMIN_PASSWORD": "pw", "DATABASE_URL": "postgres://x"}
    chosen, skipped = ES.select(env, {"GROQ_API_KEY", "XAI_API_KEY", "ADMIN_PASSWORD", "OPENROUTER_API_KEY"})
    assert chosen == {"GROQ_API_KEY": FAKE_GROQ, "XAI_API_KEY": "xai-x"}
    assert dict(skipped)["OPENROUTER_API_KEY"] == "empty or missing in .env"


def test_core_secrets_are_never_synced_even_if_listed(tmp_path):
    """A local POSTGRES_PASSWORD/DATABASE_URL would lock the cloud app out of its own database."""
    env = {k: "local-dev-value" for k in CORE} | {"GROQ_API_KEY": FAKE_GROQ}
    chosen, _ = ES.select(env, set(CORE) | {"GROQ_API_KEY"})
    assert set(chosen) == {"GROQ_API_KEY"}


def test_a_key_no_container_receives_is_skipped_with_the_reason():
    chosen, skipped = ES.select({"OPENROUTER_API_KEY": "sk-or-1"}, {"GROQ_API_KEY"})
    assert chosen == {} and dict(skipped)["OPENROUTER_API_KEY"].startswith("no container lists it")


def test_a_value_with_a_newline_is_refused():
    with pytest.raises(ValueError):
        ES.select({"GROQ_API_KEY": "a\nb"}, {"GROQ_API_KEY"})


def run_py(args, tmp_path, env_text):
    envf = tmp_path / ".env"
    envf.write_text(env_text)
    return subprocess.run([sys.executable, "-m", "deploy.env_secrets", "--env", str(envf), "--machine", str(machine(tmp_path)), *args],
                          cwd=ROOT, capture_output=True, text=True, check=False)


def test_names_mode_never_prints_a_value(tmp_path):
    r = run_py(["--names"], tmp_path, f"GROQ_API_KEY={FAKE_GROQ}\n")
    assert r.returncode == 0 and "GROQ_API_KEY" in r.stdout and FAKE_GROQ not in r.stdout + r.stderr


def test_default_mode_emits_name_value_lines_for_fly_import(tmp_path):
    r = run_py([], tmp_path, f"GROQ_API_KEY={FAKE_GROQ}\nADMIN_PASSWORD=local\n")
    assert r.stdout.strip().splitlines() == [f"GROQ_API_KEY={FAKE_GROQ}"]
    assert FAKE_GROQ not in r.stderr and "ADMIN_PASSWORD" in r.stderr and "local" not in r.stderr


def test_nothing_to_sync_is_a_clear_failure(tmp_path):
    r = run_py([], tmp_path, "GROQ_API_KEY=\n")
    assert r.returncode == 1 and "nothing to sync" in r.stderr.lower()


# ---------------------------------------------------------------- scripts (a stub `fly` records its calls; nothing real runs)
@pytest.fixture
def stub(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log, stdin_log = tmp_path / "calls.log", tmp_path / "stdin.log"
    fly = bindir / "fly"
    fly.write_text(f'#!/bin/sh\necho "$*" >> "{log}"\ncase "$*" in\n  "secrets import"*) cat > "{stdin_log}" ;;\n'
                   f'  "ssh console"*) printf "%s" "${{FAKE_ACTIVE:-}}" ;;\nesac\nexit 0\n')
    fly.chmod(fly.stat().st_mode | stat.S_IEXEC)
    envf = tmp_path / ".env"
    envf.write_text(f"GROQ_API_KEY={FAKE_GROQ}\nADMIN_PASSWORD=local-only\nDATABASE_URL=postgresql://dev\n")
    base = os.environ | {"PATH": f"{bindir}:{os.environ['PATH']}", "ENV_FILE": str(envf), "PYTHON": sys.executable, "YES": "1"}
    return {"env": base, "log": log, "stdin": stdin_log}


def sh(script, stub, **extra):
    return subprocess.run(["bash", str(ROOT / "scripts" / script)], cwd=ROOT, env=stub["env"] | extra, capture_output=True, text=True, check=False, input="n\n")


def test_fly_secrets_stages_only_the_api_key_and_prints_no_value(stub):
    r = sh("fly-secrets.sh", stub)
    assert r.returncode == 0, r.stderr
    assert stub["stdin"].read_text().strip() == f"GROQ_API_KEY={FAKE_GROQ}"
    assert "secrets import --stage --app livebingo" in stub["log"].read_text()
    assert FAKE_GROQ not in r.stdout + r.stderr


def test_fly_secrets_dry_run_changes_nothing(stub):
    r = sh("fly-secrets.sh", stub, DRY_RUN="1")
    assert r.returncode == 0 and "GROQ_API_KEY" in r.stdout and FAKE_GROQ not in r.stdout + r.stderr
    assert not stub["stdin"].exists()


def test_fly_secrets_stops_when_there_is_no_env_file(stub, tmp_path):
    r = sh("fly-secrets.sh", stub, ENV_FILE=str(tmp_path / "missing.env"))
    assert r.returncode != 0 and ".env" in r.stdout + r.stderr


def test_deploy_syncs_secrets_before_deploying_with_ha_false(stub):
    r = sh("fly-deploy.sh", stub)
    assert r.returncode == 0, r.stdout + r.stderr
    calls = stub["log"].read_text().splitlines()
    i_sec = next(i for i, c in enumerate(calls) if c.startswith("secrets import"))
    i_dep = next(i for i, c in enumerate(calls) if c.startswith("deploy"))
    assert i_sec < i_dep and "--ha=false" in calls[i_dep] and "--app livebingo" in calls[i_dep]
    assert FAKE_GROQ not in r.stdout + r.stderr


def test_deploy_warns_about_an_active_game_and_cancels_without_consent(stub):
    r = sh("fly-deploy.sh", stub, FAKE_ACTIVE="bingo|1\n", YES="")
    assert "bingo" in r.stdout and r.returncode != 0
    assert not any(c.startswith("deploy") for c in stub["log"].read_text().splitlines())


def test_deploy_dry_run_runs_nothing_real(stub):
    r = sh("fly-deploy.sh", stub, DRY_RUN="1")
    assert r.returncode == 0 and "[dry run] fly deploy --ha=false" in r.stdout
    assert not stub["stdin"].exists()
