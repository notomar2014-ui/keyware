"""Quiet update check.

Source: the latest GitHub release of notomar2014-ui/keyware.

    https://api.github.com/repos/notomar2014-ui/keyware/releases/latest

That endpoint is the newest non-draft, non-prerelease tag. Keyware reads
``tag_name`` (``v1.4.0`` or ``1.4.0``) and compares it to ``engine.__version__``.
Nothing is downloaded. A failure, including being offline, returns no notice.

The Update button opens one of:

* Store / MSIX package: ``ms-windows-store://pdp/?productid=9ngdp5bl0105``
* Portable exe: ``https://keyware.site/#install``
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

GITHUB_LATEST = "https://api.github.com/repos/notomar2014-ui/keyware/releases/latest"
STORE_PRODUCT = "9ngdp5bl0105"
STORE_URL = f"ms-windows-store://pdp/?productid={STORE_PRODUCT}"
DOWNLOAD_URL = "https://keyware.site/#install"
CHECK_INTERVAL_SECONDS = 24 * 60 * 60
SNOOZE_SECONDS = 24 * 60 * 60

_VERSION = re.compile(r"^\d+\.\d+\.\d+$")


@dataclass(frozen=True)
class UpdateNotice:
    current: str
    latest: str
    url: str
    packaged: bool


def version_tuple(text: str) -> tuple[int, int, int] | None:
    raw = (text or "").strip()
    if raw.lower().startswith("v"):
        raw = raw[1:]
    if not _VERSION.fullmatch(raw):
        return None
    major, minor, patch = raw.split(".")
    return int(major), int(minor), int(patch)


def parse_release_version(payload: object) -> str | None:
    """``tag_name`` from a GitHub release object, or None when it is not x.y.z."""
    if not isinstance(payload, dict):
        return None
    tag = str(payload.get("tag_name") or "").strip()
    if tag.lower().startswith("v"):
        tag = tag[1:]
    return tag if version_tuple(tag) else None


def is_newer(latest: str, current: str) -> bool:
    new = version_tuple(latest)
    old = version_tuple(current)
    if new is None or old is None:
        return False
    return new > old


def check_due(checked_at: float, now: float, interval: float = CHECK_INTERVAL_SECONDS) -> bool:
    try:
        return float(now) - float(checked_at or 0) >= float(interval)
    except (TypeError, ValueError):
        return True


def notice_for(
    current: str,
    latest: str | None,
    *,
    enabled: bool = True,
    skipped: str = "",
    later_until: float = 0,
    now: float = 0,
    packaged: bool = False,
) -> UpdateNotice | None:
    """The banner to show, or None when there is nothing to say."""
    found = version_tuple(latest or "")
    if not enabled or found is None or not is_newer(latest or "", current):
        return None
    latest_text = ".".join(str(part) for part in found)
    if version_tuple(skipped) == found:
        return None
    try:
        if float(later_until or 0) > float(now or 0):
            return None
    except (TypeError, ValueError):
        pass
    return UpdateNotice(
        current=current,
        latest=latest_text,
        url=STORE_URL if packaged else DOWNLOAD_URL,
        packaged=bool(packaged),
    )


def read_latest_body(body: bytes) -> str | None:
    """Parse a GitHub release response body. The HTTP call lives outside engine/."""
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, AttributeError):
        return None
    return parse_release_version(payload)
