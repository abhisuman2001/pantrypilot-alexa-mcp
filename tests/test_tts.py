"""Tests for the optional ElevenLabs /tts proxy. No real network calls."""

import urllib.error
from unittest.mock import MagicMock, patch

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

import tts


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.delenv("ELEVENLABS_CHAR_BUDGET", raising=False)
    tts.reset()
    yield
    tts.reset()


@pytest.fixture
def client():
    app = Starlette(routes=[Route("/tts", tts.tts_endpoint, methods=["POST"]),
                            Route("/tts/status", tts.status_endpoint)])
    return TestClient(app)


def fake_urlopen(audio=b"ID3audio"):
    resp = MagicMock()
    resp.read.return_value = audio
    resp.__enter__.return_value = resp
    return MagicMock(return_value=resp)


def test_status_reports_enabled(client):
    assert client.get("/tts/status").json() == {"enabled": True, "provider": "elevenlabs"}


def test_disabled_without_key(client, monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY")
    assert client.get("/tts/status").json()["enabled"] is False
    assert client.post("/tts", json={"text": "hi"}).status_code == 503


def test_returns_audio_and_sends_key_server_side(client):
    with patch("urllib.request.urlopen", fake_urlopen()) as op:
        r = client.post("/tts", json={"text": "Spinach expires tomorrow."})
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/mpeg"
    assert r.content == b"ID3audio"
    req = op.call_args.args[0]
    assert req.get_header("Xi-api-key") == "test-key"
    assert "test-key" not in r.text


def test_rejects_empty_and_long_text(client):
    assert client.post("/tts", json={"text": ""}).status_code == 400
    assert client.post("/tts", json={"text": "a" * 401}).status_code == 400
    assert client.post("/tts", content=b"not json").status_code == 400


def test_rate_limited_per_client(client):
    with patch("urllib.request.urlopen", fake_urlopen()):
        codes = [client.post("/tts", json={"text": f"hello {i}"},
                             headers={"x-forwarded-for": "1.2.3.4"}).status_code
                 for i in range(tts.RATE_LIMIT + 1)]
        other = client.post("/tts", json={"text": "hi"}, headers={"x-forwarded-for": "5.6.7.8"})
    assert codes[:-1] == [200] * tts.RATE_LIMIT
    assert codes[-1] == 429
    assert other.status_code == 200


def test_character_budget_stops_calls(client, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_CHAR_BUDGET", "10")
    with patch("urllib.request.urlopen", fake_urlopen()) as op:
        assert client.post("/tts", json={"text": "short"}).status_code == 200
        assert client.post("/tts", json={"text": "too long now"}).status_code == 503
    assert op.call_count == 1


def test_repeated_text_is_cached(client):
    with patch("urllib.request.urlopen", fake_urlopen()) as op:
        for _ in range(3):
            assert client.post("/tts", json={"text": "Yes?"}).status_code == 200
    assert op.call_count == 1


def test_upstream_error_gives_503_not_crash(client):
    err = urllib.error.HTTPError("u", 401, "unauthorized", {}, None)
    err.read = lambda: b"bad key"
    with patch("urllib.request.urlopen", MagicMock(side_effect=err)):
        assert client.post("/tts", json={"text": "hi"}).status_code == 503
    with patch("urllib.request.urlopen", MagicMock(side_effect=TimeoutError())):
        assert client.post("/tts", json={"text": "hello"}).status_code == 503
