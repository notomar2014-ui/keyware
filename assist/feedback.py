"""Suggestions & feedback: one POST to the Keyware site (/api/feedback). Never raises anything but FeedbackError."""

from __future__ import annotations

import json
import re
import socket
import urllib.error
from urllib.parse import urlsplit, urlunsplit

FEEDBACK_TYPES = ("idea", "feedback", "bug")
TYPE_LABELS = {"Idea": "idea", "Feedback": "feedback", "Bug": "bug"}
MAX_MESSAGE = 2000
MIN_MESSAGE = 5
MAX_EMAIL = 254
TIMEOUT = 15
EMAIL_RE = re.compile(r"^[^\s@<>()\[\],;:\"]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9\-]{0,62}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9\-]{0,62}[A-Za-z0-9])?)+$")
FOLLOW_UP_NOTE = "We might email you to ask for more information."
SENT_MESSAGE = "Thanks! Your message was sent."
OFFLINE_MESSAGE = "No internet connection. Your message wasn't sent. Try again when you're online."
LIMIT_MESSAGE = "You've sent a lot today. Try again tomorrow."


class FeedbackError(Exception):
    def __init__(self, message: str, code: str = "error"):
        super().__init__(message)
        self.code = code


def feedback_endpoint(ai_endpoint: str) -> str:
    """Same site as the AI proxy: …/api/bind -> …/api/feedback."""
    parts = urlsplit(ai_endpoint or "")
    return urlunsplit((parts.scheme, parts.netloc, "/api/feedback", "", ""))


def validate(kind: str, message: str, email: str) -> dict:
    kind = TYPE_LABELS.get(kind, kind if kind in FEEDBACK_TYPES else "")
    if not kind:
        raise FeedbackError("Pick Idea, Feedback or Bug.", code="invalid")
    message = (message or "").replace("\r\n", "\n").strip()
    if len(message) < MIN_MESSAGE:
        raise FeedbackError("Write a few words first.", code="invalid")
    if len(message) > MAX_MESSAGE:
        raise FeedbackError(f"Keep it under {MAX_MESSAGE} characters ({len(message)} now).", code="invalid")
    email = (email or "").strip()
    if not email:
        raise FeedbackError("Add your email so we can follow up.", code="invalid")
    if len(email) > MAX_EMAIL or not EMAIL_RE.match(email):
        raise FeedbackError("That email doesn't look right.", code="invalid")
    return {"type": kind, "message": message, "email": email}


def send_feedback(endpoint: str, install_id: str, version: str, kind: str, message: str, email: str,
                  transport=None) -> None:
    """Validates, then POSTs. Raises FeedbackError with a friendly message on any failure."""
    from assist.ai_binder import ProxyReply, urllib_proxy_transport

    payload = validate(kind, message, email)
    payload["app_version"] = version
    if not endpoint.startswith(("https://", "http://127.0.0.1", "http://localhost")):
        raise FeedbackError("Feedback isn't available in this build.", code="invalid")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": f"Keyware/{version}",
        "X-Keyware-Install": install_id,
    }
    send = transport or urllib_proxy_transport
    try:
        data = send(endpoint, headers, json.dumps(payload).encode("utf-8"), TIMEOUT)
    except ProxyReply as exc:
        if exc.status == 429:
            raise FeedbackError(LIMIT_MESSAGE, code="rate_limited") from None
        server = exc.data.get("error") if isinstance(exc.data, dict) else None
        if exc.status in (400, 413, 422) and isinstance(server, str) and server:
            raise FeedbackError(server[:200], code="invalid") from None
        raise FeedbackError("Couldn't send right now. Try again in a minute.", code="busy") from None
    except (socket.timeout, TimeoutError):
        raise FeedbackError("Timed out. Try again.", code="timeout") from None
    except (urllib.error.URLError, OSError, ValueError):
        raise FeedbackError(OFFLINE_MESSAGE, code="offline") from None
    except Exception:
        raise FeedbackError("Couldn't send right now. Try again in a minute.", code="busy") from None
    if not isinstance(data, dict) or not data.get("ok"):
        raise FeedbackError("Couldn't send right now. Try again in a minute.", code="busy")
