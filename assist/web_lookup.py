"""Tiny HTTPS GET used by the Play action to find a song or video. No keys, no cookies."""

from __future__ import annotations

import urllib.request

ALLOWED_HOSTS = ("https://html.duckduckgo.com/", "https://www.bing.com/", "https://www.youtube.com/")


def fetch_text(url: str, user_agent: str, timeout: float = 6.0, limit: int = 3_000_000) -> str:
    if not url.startswith(ALLOWED_HOSTS):
        return ""
    request = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept-Language": "en"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed https hosts
        return response.read(limit).decode("utf-8", "replace")
