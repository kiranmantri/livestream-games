"""When STT is off the page falls back to the typed box. That must never be silent: the log names the cause once."""
from __future__ import annotations

import logging
import uuid

import pytest

from app import sttworker as W


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for k in ("STT_PROVIDER", "GROQ_API_KEY", "XAI_API_KEY", "STT_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(W, "_warned", set())


def test_unset_provider_is_explained():
    assert "STT_PROVIDER is not set" in W.off_reason()


def test_explicit_off_is_explained_as_a_choice(monkeypatch):
    monkeypatch.setenv("STT_PROVIDER", "off")
    assert "off" in W.off_reason()


def test_unknown_provider_names_the_valid_ones(monkeypatch):
    monkeypatch.setenv("STT_PROVIDER", "gorq")
    r = W.off_reason()
    assert "gorq" in r and "groq" in r and "grok" in r and "whispercpp" in r


@pytest.mark.parametrize(("provider", "var"), [("groq", "GROQ_API_KEY"), ("grok", "XAI_API_KEY")])
def test_a_hosted_provider_without_its_key_names_the_variable(monkeypatch, provider, var):
    monkeypatch.setenv("STT_PROVIDER", provider)
    r = W.off_reason()
    assert var in r and "empty" in r


def test_whispercpp_without_a_url_is_explained(monkeypatch):
    monkeypatch.setenv("STT_PROVIDER", "whispercpp")
    assert "STT_URL" in W.off_reason()


def test_a_working_setup_has_no_reason(monkeypatch):
    monkeypatch.setenv("STT_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_FAKE_0123456789")
    assert W.off_reason() is None and W.enabled()


def test_the_reason_never_contains_a_key(monkeypatch):
    monkeypatch.setenv("STT_PROVIDER", "gorq")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_FAKE_0123456789")
    assert "gsk_FAKE" not in W.off_reason()


def test_ensure_logs_the_reason_once_and_returns_none(caplog):
    with caplog.at_level(logging.WARNING, logger=W.log.name):
        assert W.ensure(uuid.uuid4(), "abcdefghijk") is None
        assert W.ensure(uuid.uuid4(), "abcdefghijk") is None
    msgs = [r.getMessage() for r in caplog.records if "stt" in r.getMessage().lower()]
    assert len(msgs) == 1 and "STT_PROVIDER is not set" in msgs[0]


def test_ensure_logs_again_when_the_reason_changes(caplog, monkeypatch):
    with caplog.at_level(logging.WARNING, logger=W.log.name):
        W.ensure(uuid.uuid4(), "abcdefghijk")
        monkeypatch.setenv("STT_PROVIDER", "groq")
        W.ensure(uuid.uuid4(), "abcdefghijk")
    assert len([r for r in caplog.records if "stt" in r.getMessage().lower()]) == 2
