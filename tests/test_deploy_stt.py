"""The Fly files that turn speech-to-text on: ffmpeg in the app image, STT env + the Groq secret on the app container."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_app_image_has_ffmpeg_from_apt():
    assert re.search(r"apt-get install[^\n]*\bffmpeg\b", (ROOT / "Dockerfile").read_text())


def app_container():
    d = json.loads((ROOT / "deploy" / "fly.machine.json").read_text())
    return next(c for c in d["containers"] if c["name"] == "app")


def test_app_container_uses_groq_with_its_key_injected_as_a_secret():
    c = app_container()
    assert c["env"]["STT_PROVIDER"] == "groq" and c["env"]["STT_LANGUAGE"] == "pt"
    assert "STT_URL" not in c["env"], "STT_URL would override Groq's URL"
    assert {"env_var": "GROQ_API_KEY", "name": "GROQ_API_KEY"} in c["secrets"], "Fly injects only the secrets a container lists"


def test_no_key_value_is_written_in_the_deploy_files():
    text = (ROOT / "deploy" / "fly.machine.json").read_text() + (ROOT / "fly.toml").read_text()
    assert not re.search(r"gsk_[A-Za-z0-9]{10,}|xai-[A-Za-z0-9]{10,}", text)


def test_the_self_hosted_whisper_app_is_gone():
    """It was too slow on a shared CPU (13 s of audio took > 60 s); Groq replaced it."""
    assert not (ROOT / "deploy" / "stt").exists()
    assert not list((ROOT / "scripts").glob("stt-*.sh")) and not list((ROOT / "scripts").glob("all-*.sh"))
    assert "livebingo-stt" not in (ROOT / "deploy" / "fly.machine.json").read_text()


def test_a_machine_stopped_on_purpose_stays_stopped():
    toml = (ROOT / "fly.toml").read_text()
    assert re.search(r'^\s*auto_start_machines\s*=\s*false', toml, re.MULTILINE)
    assert re.search(r"^\s*auto_stop_machines\s*=\s*true", toml, re.MULTILINE)
    assert re.search(r"^\s*min_machines_running\s*=\s*1", toml, re.MULTILINE), "the only Machine must never be stopped by the proxy"
