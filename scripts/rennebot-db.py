#!/usr/bin/env python3
"""Delegate SQLite recovery commands to the RenneBot plugin project."""

from __future__ import annotations

import runpy
from pathlib import Path

runpy.run_path(
    Path(__file__).resolve().parents[1]
    / "rennebot_plugin"
    / "tools"
    / "rennebot-db.py",
    run_name="__main__",
)
