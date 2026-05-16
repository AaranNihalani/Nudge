from __future__ import annotations

from importlib import import_module


def create_app(*args, **kwargs):
    return import_module(".app", __name__).create_app(*args, **kwargs)

__all__ = ["create_app"]
