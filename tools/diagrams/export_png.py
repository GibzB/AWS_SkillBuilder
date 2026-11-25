"""Export a ``.drawio`` file to PNG with the draw.io desktop CLI.

The CLI is optional. When it is missing, fails or times out, the ``.drawio``
file stays on disk and the result says what to do next. Nothing here raises for
an environment problem, so a missing exporter never blocks drafting.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = ["ExportResult", "find_drawio_cli", "export_png", "INSTALL_GUIDANCE"]

MAC_APP_CLI = "/Applications/draw.io.app/Contents/MacOS/draw.io"
INSTALL_GUIDANCE = (
    "The draw.io desktop CLI was not found, so only the .drawio file was written. "
    "Install it with 'brew install --cask drawio' (ask first), or open the file "
    "in draw.io or through the draw.io MCP in an interactive session and export "
    "a PNG by hand."
)

Runner = Callable[..., Any]


@dataclass(frozen=True)
class ExportResult:
    ok: bool
    drawio_path: Path
    png_path: Path | None
    message: str


def find_drawio_cli(
    which: Callable[[str], str | None] = shutil.which,
    exists: Callable[[str], bool] = os.path.exists,
) -> str | None:
    """Return the draw.io CLI path, or ``None`` when it is not installed."""
    for name in ("drawio", "draw.io"):
        found = which(name)
        if found:
            return found
    if exists(MAC_APP_CLI):
        return MAC_APP_CLI
    return None


def export_png(
    xml_path: Path,
    png_path: Path,
    runner: Runner = subprocess.run,
    timeout: int = 60,
    cli: str | None = None,
) -> ExportResult:
    """Run ``drawio -x -f png -s 2 -b 10 -o <png> <xml>``.

    ``cli`` defaults to :func:`find_drawio_cli`. Failures return an
    ``ExportResult`` with ``ok=False`` and a message; nothing is raised.
    """
    program = cli if cli is not None else find_drawio_cli()
    if program is None:
        return ExportResult(False, xml_path, None, INSTALL_GUIDANCE)
    command = [program, "-x", "-f", "png", "-s", "2", "-b", "10", "-o", str(png_path), str(xml_path)]
    try:
        completed = runner(command, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return ExportResult(False, xml_path, None, f"draw.io export timed out after {timeout}s.")
    except OSError as error:
        return ExportResult(False, xml_path, None, f"draw.io could not be started: {error}")
    if completed.returncode != 0:
        detail = (completed.stderr or "").strip().splitlines()
        tail = detail[-1] if detail else "no error output"
        return ExportResult(False, xml_path, None, f"draw.io export failed ({completed.returncode}): {tail}")
    if not png_path.exists():
        return ExportResult(False, xml_path, None, "draw.io finished but wrote no PNG.")
    return ExportResult(True, xml_path, png_path, f"PNG written to {png_path}")
