"""Compatibility import path for the renamed Tirzah package."""

from __future__ import annotations

import sys

import tirzah as _tirzah

__all__ = getattr(_tirzah, "__all__", [])
__path__ = _tirzah.__path__

sys.modules[__name__] = _tirzah
