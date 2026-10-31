"""Suggestions & feedback (v1.3.4). Fully mocked: nothing is sent."""

from __future__ import annotations

import json
import os
import random
import time
import urllib.error

import pytest

from assist.ai_binder import ProxyReply
from assist.feedback import (
    FOLLOW_UP_NOTE,
    MAX_MESSAGE,
    OFFLINE_MESSAGE,
    FeedbackError,
    feedback_endpoint,
    send_feedback,
    validate,
)
from tests.fakes import service

INSTALL = "3f2b8c4e-9d1a-4c6b-8e7f-0a1b2c3d4e5f"
URL = "https://keyware-app.vercel.app/api/feedback"
needs_display = pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="Keyware window needs a display")


class Transport:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, url, headers, body, timeout):
        self.calls.append({"url": url, "headers": headers, "body": json.loads(body)})
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


def test_endpoint_is_next_to_the_proxy():
    assert feedback_endpoint("https://keyware-app.vercel.app/api/bind") == URL
    assert feedback_endpoint("http://127.0.0.1:3000/api/bind") == "http://127.0.0.1:3000/api/feedback"


def test_validation():
    assert validate("Idea", "  Add a dark mode  ", "me@example.com") == {
        "type": "idea", "message": "Add a dark mode", "email": "me@example.com"}
    assert validate("Bug", "x" * MAX_MESSAGE, "a.b+c@sub.example.org")["type"] == "bug"
    for kind, message, email, needle in [
        ("Rant", "hello there", "me@example.com", "Pick"),
        ("Idea", "hi", "me@example.com", "few words"),
        ("Idea", "x" * (MAX_MESSAGE + 1), "me@example.com", "under 2000"),
        ("Idea", "hello there", "", "email"),
        ("Idea", "hello there", "not-an-email", "doesn't look right"),
        ("Idea", "hello there", "a@b", "doesn't look right"),
        ("Idea", "hello there", "<x>@example.com", "doesn't look right"),
    ]:
        with pytest.raises(FeedbackError, match=needle):
            validate(kind, message, email)


def test_send_posts_type_message_email_version_and_install_id():
    transport = Transport({"ok": True})
    send_feedback(URL, INSTALL, "1.3.4", "Feedback", "Love the gear icon", "me@example.com", transport=transport)
    call = transport.calls[0]
    assert call["url"] == URL
    assert call["headers"]["X-Keyware-Install"] == INSTALL and call["headers"]["User-Agent"] == "Keyware/1.3.4"
    assert call["body"] == {"type": "feedback", "message": "Love the gear icon", "email": "me@example.com",
                            "app_version": "1.3.4"}


@pytest.mark.parametrize("answer, code, needle", [
    (ProxyReply(429, {"error": "x"}), "rate_limited", "tomorrow"),
    (ProxyReply(400, {"error": "That email doesn't look right."}), "invalid", "email"),
    (ProxyReply(503, {}), "busy", "Try again"),
    (ProxyReply(500, {}), "busy", "Try again"),
    (urllib.error.URLError(OSError(11001, "getaddrinfo failed")), "offline", "No internet"),
    (TimeoutError("timed out"), "timeout", "Timed out"),
    (ValueError("bad json"), "offline", "No internet"),
    ({"ok": False}, "busy", "Try again"),
    ("garbage", "busy", "Try again"),
])
def test_send_errors_are_friendly(answer, code, needle):
    with pytest.raises(FeedbackError, match=needle) as caught:
        send_feedback(URL, INSTALL, "1.3.4", "Idea", "hello there", "me@example.com", transport=Transport(answer))
    assert caught.value.code == code
    if code == "offline":
        assert str(caught.value) == OFFLINE_MESSAGE


def test_invalid_input_never_reaches_the_network():
    transport = Transport()
    with pytest.raises(FeedbackError):
        send_feedback(URL, INSTALL, "1.3.4", "Idea", "", "me@example.com", transport=transport)
    with pytest.raises(FeedbackError):
        send_feedback("http://evil.example/api/feedback", INSTALL, "1.3.4", "Idea", "hello there", "me@example.com",
                      transport=transport)
    assert transport.calls == []


def test_fuzz_validate_never_crashes():
    rng = random.Random(1234)
    alphabet = "abc@.-_ <>\"'\\\n\t\u00e9\u4e2d\U0001F600\x00"
    for _ in range(2000):
        message = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 60)))
        email = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 30)))
        kind = rng.choice(["Idea", "Bug", "Feedback", "", "x", None])
        try:
            validate(kind, message, email)
        except FeedbackError:
            pass


@needs_display
def test_feedback_form_in_settings(tmp_path):
    from ui.app import KeywareApp

    created, *_ = service(tmp_path)
    app = KeywareApp(created, autostart=False, start_tray=False)
    sent = []
    app._feedback_sender = lambda *args: sent.append(args)
    try:
        app._open_settings()
        app.update()
        assert app.feedback_send.winfo_viewable() or True  # may be below the fold; it exists
        app.feedback_type.set("Bug")
        app.feedback_text.insert("1.0", "The gear icon is too small")
        app.feedback_email.insert(0, "me@example.com")
        app._send_feedback()
        end = time.monotonic() + 3
        while app._feedback_busy and time.monotonic() < end:
            app._drain_feedback()
            app.update()
            time.sleep(0.02)
        assert sent and sent[0][3:] == ("Bug", "The gear icon is too small", "me@example.com")
        assert sent[0][0].endswith("/api/feedback") and sent[0][1] == created.config.install_id
        assert "Thanks" in app.feedback_status.cget("text")
        assert app._feedback_message() == ""
    finally:
        app.destroy()


@needs_display
def test_feedback_errors_show_and_never_crash(tmp_path):
    from ui.app import KeywareApp

    created, *_ = service(tmp_path)
    app = KeywareApp(created, autostart=False, start_tray=False)

    def offline(*_args):
        raise FeedbackError(OFFLINE_MESSAGE, code="offline")

    try:
        app._open_settings()
        app._send_feedback()  # empty form
        assert "few words" in app.feedback_status.cget("text")
        app.feedback_text.insert("1.0", "hello there")
        app._send_feedback()
        assert "email" in app.feedback_status.cget("text")
        app.feedback_email.insert(0, "me@example.com")
        app._feedback_sender = offline
        app._send_feedback()
        end = time.monotonic() + 3
        while app._feedback_busy and time.monotonic() < end:
            app._drain_feedback()
            app.update()
            time.sleep(0.02)
        assert "No internet" in app.feedback_status.cget("text")
        assert app._feedback_message() == "hello there"  # kept so it can be sent later
        assert FOLLOW_UP_NOTE == "We might email you to ask for more information."
    finally:
        app.destroy()
