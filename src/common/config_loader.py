"""
Config loader for YAML, CSV, and JSON config files.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import yaml


CONFIGS_DIR = Path(__file__).resolve().parent.parent.parent / "configs"


def load_yaml(filename: str) -> dict[str, Any]:
    path = CONFIGS_DIR / filename
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_json(filename: str) -> Any:
    path = CONFIGS_DIR / filename
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_csv(filename: str) -> list[dict[str, str]]:
    path = CONFIGS_DIR / filename
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)
