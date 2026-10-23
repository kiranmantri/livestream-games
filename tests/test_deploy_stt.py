"""The Fly files that turn speech-to-text on: ffmpeg in the app image, STT env on the app container, a private whisper app."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def app_env():
    d = json.loads((ROOT / "deploy" / "fly.machine.json").read_text())
    return next(c for c in d["containers"] if c["name"] == "app")["env"]


def test_app_image_has_ffmpeg_from_apt():
    assert re.search(r"apt-get install[^\n]*\bffmpeg\b", (ROOT / "Dockerfile").read_text())


def test_app_container_points_at_the_private_whisper_app():
    env = app_env()
    assert env["STT_PROVIDER"] == "whispercpp" and env["STT_LANGUAGE"] == "pt"
    assert env["STT_URL"] == "http://livebingo-stt.internal:8080"


def test_whisper_app_is_private_small_model_on_a_shared_cpu():
    toml = (ROOT / "deploy" / "stt" / "fly.toml").read_text()
    assert 'app = "livebingo-stt"' in toml and not re.search(r"^\[http_service\]", toml, re.M) and "shared-cpu" in toml
    df = (ROOT / "deploy" / "stt" / "Dockerfile").read_text()
    assert "ggml-small.bin" in df and '"--host", "::"' in df and "USER stt" in df
