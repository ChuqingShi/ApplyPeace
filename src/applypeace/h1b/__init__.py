"""H-1B sponsorship evidence from public DOL data, usable on its own as a library or API.

Kept free of ApplyPeace's job pipeline so it can move into its own
`applypeace-h1b` repository; only company-name normalisation is shared.
"""

from pathlib import Path

from ..config import runtime_dir


def default_db() -> Path:
    return runtime_dir() / "h1b" / "h1b.db"


def raw_dir() -> Path:
    return runtime_dir() / "h1b" / "raw"
