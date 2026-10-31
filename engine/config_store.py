"""JSON config in %APPDATA%\\Keyware\\config.json, with an atomic replace."""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

from engine.models import (
    GAMING_PROFILE_ID,
    GAMING_PROFILE_NAME,
    SCHEMA_VERSION,
    Config,
    default_config,
    sanitize_config,
)

GAMING_MIGRATION = "v133_gaming"
# Where the real config is copied before a one-time migration rewrites it.
DEFAULT_BACKUP_DIR = r"D:\Keyware_backups"


def appdata_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "Keyware"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "keyware"


def default_config_path() -> Path:
    return appdata_dir() / "config.json"


def default_backup_dir(config_path: Path) -> Path:
    """KEYWARE_BACKUP_DIR, else D:\\Keyware_backups for the real config on Windows, else ./backups."""
    explicit = os.environ.get("KEYWARE_BACKUP_DIR")
    if explicit:
        return Path(explicit)
    try:
        is_real = Path(config_path).resolve() == default_config_path().resolve()
    except OSError:
        is_real = False
    if is_real and sys.platform == "win32" and Path("D:/").exists():
        return Path(DEFAULT_BACKUP_DIR)
    return Path(config_path).parent / "backups"


def migrate_gaming_profile(raw: dict) -> bool:
    """Rename the old "Minecraft" profile to "Gaming" once. Bindings and apps are kept.

    Returns True when ``raw`` was changed (the marker alone counts). A profile
    the user names "Minecraft" later is left alone because the marker stays.
    """
    migrations = raw.get("migrations")
    if not isinstance(migrations, list):
        migrations = []
    if GAMING_MIGRATION in migrations:
        return False
    profiles = [item for item in raw.get("profiles", []) or [] if isinstance(item, dict)]

    def name_of(item: dict) -> str:
        return str(item.get("name", "") or "").strip().lower()

    target = next((item for item in profiles if item.get("id") == "minecraft"), None)
    if target is None:
        target = next((item for item in profiles if name_of(item) == "minecraft" and item.get("id") != "global"), None)
    if target is not None:
        others = [item for item in profiles if item is not target]
        if not any(name_of(item) == GAMING_PROFILE_NAME.lower() for item in others):
            target["name"] = GAMING_PROFILE_NAME
        if target.get("id") == "minecraft" and not any(item.get("id") == GAMING_PROFILE_ID for item in others):
            target["id"] = GAMING_PROFILE_ID
    raw["migrations"] = [str(item) for item in migrations if isinstance(item, str)] + [GAMING_MIGRATION]
    return True


def _names(raw: dict) -> list[tuple]:
    return [(p.get("id"), p.get("name")) for p in raw.get("profiles", []) or [] if isinstance(p, dict)]


class ConfigStore:
    def __init__(self, path: Path | None = None, backup_dir: Path | None = None):
        self.path = Path(path) if path is not None else default_config_path()
        self.backup_dir = Path(backup_dir) if backup_dir is not None else default_backup_dir(self.path)

    def backup_before_migration(self, tag: str = "v133") -> Path:
        """Copy config.json to <backup_dir>/config_before_<tag>_<YYYYMMDD>.json. Never overwrites."""
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        stem = f"config_before_{tag}_{time.strftime('%Y%m%d')}"
        target = self.backup_dir / f"{stem}.json"
        counter = 2
        while target.exists():
            target = self.backup_dir / f"{stem}_{counter}.json"
            counter += 1
        shutil.copy2(self.path, target)
        return target

    def _migrate(self, raw: dict, warnings: list[str]) -> bool:
        probe = json.loads(json.dumps(raw))
        if not migrate_gaming_profile(probe):
            return False
        try:
            backup = self.backup_before_migration()
        except Exception as exc:
            warnings.append(f"Could not back up the config before renaming Minecraft to Gaming ({exc}). Will try again next start.")
            return False
        renamed = _names(raw) != _names(probe)
        raw.clear()
        raw.update(probe)
        if renamed:
            warnings.append(f"Renamed the Minecraft profile to Gaming. Backup: {backup}")
        return True

    def load(self) -> tuple[Config, list[str]]:
        warnings: list[str] = []
        if not self.path.exists():
            config = default_config()
            self.save(config)
            return config, warnings
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("Config root must be an object.")
            migrated = self._migrate(raw, warnings)
            config = Config.from_dict(raw)
            before = config.to_dict()
            self._repair(config, warnings)
            if migrated or raw.get("version") != SCHEMA_VERSION or config.to_dict() != before:
                if raw.get("version") != SCHEMA_VERSION:
                    warnings.append("Config version was updated.")
                self.save(config)
            return config, warnings
        except Exception as exc:
            backup = self.path.with_suffix(".json.bak")
            try:
                shutil.copy2(self.path, backup)
                warnings.append(
                    f"Config was unreadable ({exc}). A backup was saved to {backup.name} and defaults were loaded."
                )
            except Exception as copy_exc:
                warnings.append(
                    f"Config was unreadable ({exc}) and could not be backed up ({copy_exc}). Defaults were loaded."
                )
            config = default_config()
            self.save(config)
            return config, warnings

    def read_validated(self) -> tuple[Config, list[str]]:
        """Parse and sanitize the file. Raise on failure so the caller keeps the previous config.

        Unlike ``load``, a corrupt or partial write is not replaced with defaults.
        """
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("Config root must be an object.")
        config = Config.from_dict(raw)
        warnings: list[str] = []
        self._repair(config, warnings)
        return config, warnings

    def save(self, config: Config) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(config.to_dict(), indent=2)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(payload + "\n", encoding="utf-8")
        temporary.replace(self.path)

    def _repair(self, config: Config, warnings: list[str]) -> None:
        if not any(profile.is_global for profile in config.profiles):
            config.profiles.insert(0, default_config().global_profile())
            warnings.append("Global profile was missing and has been restored.")
        warnings.extend(sanitize_config(config))
        from engine.conflicts import dedupe_profile

        for profile in config.profiles:
            warnings.extend(dedupe_profile(profile.bindings, config.leader_key, profile.name))
        config.version = SCHEMA_VERSION
