"""fastci: run in each case only the checks and tests a change can break, cheapest first."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("fastci")
except PackageNotFoundError:  # running from a source tree that is not installed
    __version__ = "0.0.0"
