"""The one place the update check touches the network.

engine/updates.py decides what a reply means. This module asks GitHub.
The hook never imports it. Offline and any other error return None.
"""

from __future__ import annotations

import urllib.error
import urllib.request

from engine.updates import GITHUB_LATEST, read_latest_body


def fetch_latest_version(url: str = GITHUB_LATEST, timeout: float = 5.0, opener=None) -> str | None:
    from engine import __version__

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": f"Keyware/{__version__}",
            "Accept": "application/vnd.github+json",
        },
    )
    open_url = opener or urllib.request.urlopen
    try:
        with open_url(request, timeout=timeout) as response:
            body = response.read()
    except (OSError, urllib.error.URLError, TimeoutError, ValueError):
        return None
    return read_latest_body(body)
