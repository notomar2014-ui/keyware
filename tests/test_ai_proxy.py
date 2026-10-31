"""v1.3.4 Text mode: proxy by default, own key optional, no shipped keys. Fully mocked; nothing is sent."""

from __future__ import annotations

import hashlib
import os
import json
import re
import sys
import urllib.error
from pathlib import Path

import pytest

import assist.ai_binder as ai
from assist.ai_binder import (
    BUSY_MESSAGE,
    LIMIT_MESSAGE,
    OFFLINE_MESSAGE,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    AIBinderError,
    DescribeBinder,
    ProxyReply,
    binder_for_config,
    check_own_key,
    load_keys,
    own_key_binder,
    proxy_context,
)
from engine.models import DEFAULT_AI_ENDPOINT, Config, default_config
from tests.test_ai_binder import Transport, good, reply

import base64 as _b64

FAKE_BLOB = _b64.b64encode(b"ABCDEFGHIJKLMNOP").decode()  # placeholder, not a real key

INSTALL = "3f2b8c4e-9d1a-4c6b-8e7f-0a1b2c3d4e5f"
SITE = Path(os.environ.get("KEYWARE_SITE") or Path(__file__).resolve().parents[1] / "site")


class Proxy:
    """Scripted proxy answers. Each item is a dict (200 body) or an exception."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls: list[dict] = []

    def __call__(self, url, headers, body, timeout):
        self.calls.append({"url": url, "headers": headers, "body": json.loads(body), "timeout": timeout})
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


def ok(result=None, model="gemini-3.5-flash-lite"):
    return {"ok": True, "prompt_version": PROMPT_VERSION, "model": model, "result": result or good(), "remaining": 19}


def proxy_binder(proxy, transport=None, endpoint="https://keyware-app.vercel.app/api/bind"):
    return DescribeBinder(endpoint=endpoint, install_id=INSTALL, proxy_transport=proxy, transport=transport or Transport())


# --- endpoint selection ----------------------------------------------------------


def test_default_endpoint_and_config_round_trip():
    config = default_config()
    assert config.ai_endpoint == DEFAULT_AI_ENDPOINT == "https://keyware-app.vercel.app/api/bind"
    assert config.ai_use_own_key is False and config.ai_key_protected == ""
    raw = config.to_dict()
    assert raw["ai_endpoint"] == DEFAULT_AI_ENDPOINT and raw["ai_provider"] == "gemini"
    assert Config.from_dict(raw) == config


def test_endpoint_is_configurable_but_only_https_or_local_http():
    assert Config.from_dict({"ai_endpoint": "https://example.com/api/bind"}).ai_endpoint == "https://example.com/api/bind"
    assert Config.from_dict({"ai_endpoint": "http://127.0.0.1:3000/api/bind"}).ai_endpoint.startswith("http://127.0.0.1")
    for bad in ("http://example.com/api/bind", "file:///C:/x", "javascript:alert(1)", "", None, 5):
        assert Config.from_dict({"ai_endpoint": bad}).ai_endpoint == DEFAULT_AI_ENDPOINT


def test_bad_install_id_or_blob_are_dropped():
    config = Config.from_dict({"install_id": "nope", "ai_key_protected": "not base64!", "ai_provider": "openai"})
    assert config.install_id == "" and config.ai_key_protected == "" and config.ai_provider == "gemini"


def test_no_own_key_uses_the_proxy(monkeypatch):
    monkeypatch.delenv("KEYWARE_DEV_KEYS", raising=False)
    config = Config.from_dict({"install_id": INSTALL, "ai_endpoint": "https://example.com/api/bind"})
    binder = binder_for_config(config)
    assert binder.uses_proxy and binder.endpoint == "https://example.com/api/bind" and binder.install_id == INSTALL


def test_own_key_toggle_without_a_key_still_uses_the_proxy(monkeypatch):
    monkeypatch.delenv("KEYWARE_DEV_KEYS", raising=False)
    binder = binder_for_config(Config.from_dict({"ai_use_own_key": True}))
    assert binder.uses_proxy


def test_own_key_calls_the_provider_directly(monkeypatch):
    import assist.secret_store as store

    monkeypatch.setattr(store, "unprotect", lambda blob: "gsk_OWNKEYOWNKEYOWNKEY" if blob == FAKE_BLOB else "")
    config = Config.from_dict({"ai_use_own_key": True, "ai_provider": "groq", "ai_key_protected": FAKE_BLOB})
    transport = Transport(reply(good()))
    binder = binder_for_config(config, transport=transport)
    assert not binder.uses_proxy
    assert {p.name for p in binder.providers} == {"groq"}
    proposal = binder.describe("F2 opens YouTube")
    assert proposal.binding.keys == ["f2"]
    call = transport.calls[0]
    assert "api.groq.com" in call["url"]
    assert call["headers"]["Authorization"] == "Bearer gsk_OWNKEYOWNKEYOWNKEY"
    assert call["body"]["messages"][0]["content"] == SYSTEM_PROMPT


def test_own_gemini_key_only_uses_gemini_models():
    binder = own_key_binder("gemini", "AIzaOWNOWNOWNOWNOWN")
    assert [p.model for p in binder.providers] == ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]


def test_unreadable_saved_key_is_a_clear_error(monkeypatch):
    import assist.secret_store as store

    def broken(_blob):
        raise store.SecretError("The saved key can't be read on this PC. Add it again.")

    monkeypatch.setattr(store, "unprotect", broken)
    config = Config.from_dict({"ai_use_own_key": True, "ai_key_protected": FAKE_BLOB})
    with pytest.raises(AIBinderError, match="Add it again") as caught:
        binder_for_config(config)
    assert caught.value.code == "key"


def test_check_own_key_reports_the_model_or_a_refusal():
    assert check_own_key("gemini", "AIzaOWNOWNOWNOWNOWN", transport=Transport(reply(good()))) == "gemini-3.5-flash-lite"
    refused = Transport(ai._HTTPFailure(401, True), ai._HTTPFailure(401, True))
    with pytest.raises(AIBinderError, match="refused") as caught:
        check_own_key("gemini", "AIzaBADBADBADBADBAD", transport=refused)
    assert caught.value.code == "key" and "AIza" not in str(caught.value)
    with pytest.raises(AIBinderError, match="Paste"):
        check_own_key("gemini", "  ")


# --- proxy requests ----------------------------------------------------------------


def test_proxy_request_has_no_prompt_and_no_keys(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaSHOULDNEVERBESENT1234")
    proxy = Proxy(ok())
    config = default_config()
    config.leader_key = "f13"
    proposal = proxy_binder(proxy).describe("F2 opens YouTube", config, "Global")
    assert proposal.binding.keys == ["f2"] and proposal.model == "gemini-3.5-flash-lite"
    call = proxy.calls[0]
    assert call["url"] == "https://keyware-app.vercel.app/api/bind"
    assert call["headers"]["X-Keyware-Install"] == INSTALL
    assert call["headers"]["User-Agent"].startswith("Keyware/1.3.5")
    assert "Authorization" not in call["headers"]
    body = call["body"]
    assert set(body) == {"description", "context"}
    assert body["description"] == "F2 opens YouTube"
    ctx = body["context"]
    assert ctx["app_version"] == "1.3.5" and ctx["prompt_version"] == PROMPT_VERSION
    assert ctx["current_profile"] == "Global" and ctx["leader_key"] == "F13"
    assert any(p["name"] == "Gaming" for p in ctx["profiles"])
    assert isinstance(ctx["triggers"], list) and ctx["triggers"]
    text = json.dumps(call)
    assert "You are Keyware" not in text and "AIza" not in text and "gsk_" not in text


def test_proxy_context_never_carries_binding_contents():
    config = default_config()
    ctx = json.dumps(proxy_context(config, "Gaming"))
    for binding in (b for p in config.profiles for b in p.bindings):
        for secret in (binding.action.url, binding.action.text):
            if secret and len(secret) > 6:
                assert secret not in ctx


def test_proxy_batch_reply_is_validated_locally():
    hostile = {"ok": True, "bindings": [good(), good(trigger={"mode": "tap", "keys": ["f3"]},
                                                        action={"type": "open_app", "app": "powershell.exe"})]}
    items = proxy_binder(Proxy(ok(hostile))).describe_batch("F2 opens YouTube and F3 opens powershell")
    assert type(items[0]).__name__ == "Proposal"
    assert type(items[1]).__name__ == "Rejected" and "powershell" in items[1].error


def test_proxy_refusal_is_shown():
    with pytest.raises(AIBinderError, match="shut down"):
        proxy_binder(Proxy(ok({"ok": False, "error": "Keyware won't shut down."}))).describe("F12 shuts down my pc")


def test_429_is_the_friendly_daily_limit():
    proxy = Proxy(ProxyReply(429, {"ok": False, "code": "rate_limited", "error": "x"}))
    transport = Transport()
    with pytest.raises(AIBinderError) as caught:
        proxy_binder(proxy, transport).describe("F2 opens YouTube")
    assert str(caught.value) == LIMIT_MESSAGE
    assert caught.value.code == "rate_limited"
    assert transport.calls == []  # never falls back to anything else on its own


def test_503_is_ai_busy():
    with pytest.raises(AIBinderError) as caught:
        proxy_binder(Proxy(ProxyReply(503, {"error": "AI busy"}))).describe("F2 opens YouTube")
    assert str(caught.value) == BUSY_MESSAGE and caught.value.code == "busy"


def test_offline_is_clear():
    with pytest.raises(AIBinderError) as caught:
        proxy_binder(Proxy(urllib.error.URLError(OSError(11001, "getaddrinfo failed")))).describe("F2 opens YouTube")
    assert str(caught.value) == OFFLINE_MESSAGE and caught.value.code == "offline"
    with pytest.raises(AIBinderError, match="Timed out"):
        proxy_binder(Proxy(TimeoutError("timed out"))).describe("F2 opens YouTube")


def test_other_proxy_errors_and_garbage():
    with pytest.raises(AIBinderError, match="under 500"):
        proxy_binder(Proxy(ProxyReply(413, {"error": "Keep it under 500 characters."}))).describe("F2 opens YouTube")
    with pytest.raises(AIBinderError, match="can't read"):
        proxy_binder(Proxy({"ok": True, "result": "nope"})).describe("F2 opens YouTube")


def test_long_and_off_topic_text_never_reach_the_proxy():
    proxy = Proxy()
    with pytest.raises(AIBinderError, match="under 500"):
        proxy_binder(proxy).describe("F2 " + "a" * 520)
    with pytest.raises(AIBinderError):
        proxy_binder(proxy).describe("write me a poem about the sea")
    assert proxy.calls == []


# --- no shipped keys ---------------------------------------------------------------


def test_env_keys_are_ignored_without_the_dev_flag(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaENVENVENVENVENV")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_ENVENVENVENVENV")
    monkeypatch.delenv("KEYWARE_DEV_KEYS", raising=False)
    assert load_keys() == {}
    assert binder_for_config(default_config()).uses_proxy


def test_dev_flag_never_works_in_the_built_exe(monkeypatch):
    monkeypatch.setenv("KEYWARE_DEV_KEYS", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaENVENVENVENVENV")
    assert load_keys() == {"GEMINI_API_KEY": "AIzaENVENVENVENVENV"}
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert load_keys() == {}
    assert binder_for_config(default_config()).uses_proxy


def test_shipped_code_never_reads_a_dotenv_file():
    for path in list(Path("assist").glob("*.py")) + list(Path("ui").glob("*.py")) + list(Path("engine").glob("*.py")) + [Path("main.py")]:
        text = path.read_text(encoding="utf-8")
        assert ".env\"" not in text and "_read_env_file" not in text, path


def test_spec_bundles_the_secret_store():
    assert '"assist.secret_store"' in Path("keyware.spec").read_text(encoding="utf-8")


# --- prompt shared with the proxy ----------------------------------------------------


def test_prompt_version_format():
    assert re.fullmatch(r"\d{4}\.\d{2}\.\d{2}-\d+", PROMPT_VERSION)


@pytest.mark.skipif(not (SITE / "api" / "_lib" / "prompt.js").exists(), reason="website folder not next to the app")
def test_proxy_prompt_matches_the_app():
    from tools.sync_prompt import render

    assert (SITE / "api" / "_lib" / "prompt.js").read_text(encoding="utf-8") == render(), "run tools/sync_prompt.py"
    digest = hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()
    assert digest in render()


# --- DPAPI (Windows only) ------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows only")
def test_dpapi_round_trip_and_no_plain_text():
    from assist.secret_store import protect, unprotect

    secret = "AIzaTESTONLYnotarealkey0123456789"
    blob = protect(secret)
    assert secret not in blob and "AIza" not in blob
    assert unprotect(blob) == secret
    assert protect(secret) != secret
    config = Config.from_dict({"ai_key_protected": blob})
    assert config.ai_key_protected == blob
    assert secret not in json.dumps(config.to_dict())


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows only")
def test_dpapi_rejects_a_tampered_blob():
    import base64

    from assist.secret_store import SecretError, protect, unprotect

    raw = bytearray(base64.b64decode(protect("AIzaTESTONLYnotarealkey0123456789")))
    raw[-5] ^= 0xFF
    with pytest.raises(SecretError):
        unprotect(base64.b64encode(bytes(raw)).decode())


def test_secret_store_refuses_off_windows(monkeypatch):
    import assist.secret_store as store

    monkeypatch.setattr(store, "available", lambda: False)
    with pytest.raises(store.SecretError):
        store.protect("AIzaTESTONLY")
    assert store.unprotect("") == ""


# --- service ---------------------------------------------------------------------------


def test_install_id_is_made_once_and_saved(tmp_path):
    from tests.fakes import service

    created, *_ = service(tmp_path)
    first = created.ensure_install_id()
    assert re.fullmatch(r"[0-9a-f-]{36}", first)
    assert created.ensure_install_id() == first
    saved = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert saved["install_id"] == first


def test_ai_settings_saved_without_a_plain_key(tmp_path):
    from tests.fakes import service

    created, *_ = service(tmp_path)
    created.set_ai_settings(use_own_key=True, provider="groq", key_protected=FAKE_BLOB)
    saved = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert saved["ai_use_own_key"] is True and saved["ai_provider"] == "groq"
    assert saved["ai_key_protected"] == FAKE_BLOB
    with pytest.raises(ValueError):
        created.set_ai_settings(provider="openai")


def test_ai_test_mode_reports_proxy_errors(monkeypatch, capsys):
    import main

    def refuse(self, *_a, **_k):
        raise AIBinderError(LIMIT_MESSAGE, code="rate_limited")

    monkeypatch.setattr(DescribeBinder, "describe_batch", refuse)
    assert main.main(["--ai-test", "F9 opens YouTube", "--endpoint", "http://127.0.0.1:9/api/bind"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["code"] == "rate_limited" and out["endpoint"].startswith("http://127.0.0.1")
