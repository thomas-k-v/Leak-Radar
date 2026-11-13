"""Loads YAML config files. Every threshold/assumption comes from config/, never from code."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def config_dir() -> Path:
    return Path(os.environ.get("LEAKRADAR_CONFIG_DIR", REPO_ROOT / "config"))


def data_dir() -> Path:
    return Path(os.environ.get("LEAKRADAR_DATA_DIR", REPO_ROOT / "data"))


def _load(name: str) -> dict[str, Any]:
    with open(config_dir() / name, encoding="utf-8") as f:
        return yaml.safe_load(f)


@lru_cache(maxsize=None)
def assumptions() -> dict[str, Any]:
    return _load("assumptions.yaml")


@lru_cache(maxsize=None)
def products() -> dict[str, Any]:
    return _load("products.yaml")


@lru_cache(maxsize=None)
def error_codes() -> dict[str, Any]:
    return _load("error_codes.yaml")


@lru_cache(maxsize=None)
def generator() -> dict[str, Any]:
    return _load("generator.yaml")


def code_layer(code: str) -> str:
    return error_codes()["codes"][code]["layer"]


def code_cause(code: str) -> str:
    return error_codes()["codes"][code]["cause"]


def customer_codes() -> list[str]:
    return [c for c, v in error_codes()["codes"].items() if v["cause"] == "customer"]
