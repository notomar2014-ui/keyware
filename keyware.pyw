"""Windowed entry point for pythonw. Same process as main.py, without a console."""

from __future__ import annotations

from main import main

if __name__ == "__main__":
    raise SystemExit(main())
