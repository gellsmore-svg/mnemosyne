"""Tirzah package."""

__all__ = ["__version__"]

# Single source of truth is the package metadata (pyproject `version`), so the
# installed wheel and `tirzah.__version__` can never disagree.
from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    __version__ = _pkg_version("tirzah")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0+unknown"
