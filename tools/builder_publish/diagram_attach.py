"""Attach a diagram PNG in the Builder Center editor through its Image button.

All browser code for the image lives here. The only click is the editor's Image
button, and the only upload is the PNG into that button's file input. It never
touches Publish, like, comment, reply or vote. Any failure prints the PNG path
so you can add the picture by hand; drafting is never blocked.

The selectors below are best guesses: the real editor DOM needs your login, so
confirm them on the first run (see ``inspect_builder_center.py``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

__all__ = ["MARKER", "split_body_at_marker", "strip_marker", "attach_png"]

MARKER = "![Architecture diagram](diagram.png)"

# Confirm on first run: the real editor markup could not be inspected here.
IMAGE_BUTTON_SELECTORS = (
    'button[aria-label="Image"]',
    'button[title="Image"]',
    '[data-testid="image-button"]',
)
FILE_INPUT_SELECTOR = 'input[type="file"]'
_SETTLE_MS = 2000


def split_body_at_marker(body: str) -> tuple[str, str] | None:
    """Split ``body`` at the single marker line, or return ``None``."""
    lines = body.splitlines()
    hits = [i for i, line in enumerate(lines) if line.strip() == MARKER]
    if len(hits) != 1:
        return None
    index = hits[0]
    before = "\n".join(lines[:index]).strip()
    after = "\n".join(lines[index + 1 :]).strip()
    return before, after


def strip_marker(body: str) -> str:
    """Remove marker lines so no broken ``diagram.png`` reference is pasted."""
    kept = [line for line in body.splitlines() if line.strip() != MARKER]
    return "\n".join(kept).strip() + "\n"


def attach_png(page: Any, png_path: Path) -> bool:
    """Click the Image button and upload ``png_path``. Never raises.

    Returns True when the file was handed to the editor. On any failure it
    prints the path and returns False.
    """
    try:
        button = None
        for selector in IMAGE_BUTTON_SELECTORS:
            candidate = page.locator(selector).first
            if candidate.count() > 0:
                button = candidate
                break
        if button is None:
            print(f"  diagram: Image button not found; add the picture by hand: {png_path}")
            return False
        button.click()
        page.locator(FILE_INPUT_SELECTOR).last.set_input_files(str(png_path))
        page.wait_for_timeout(_SETTLE_MS)
        print(f"  diagram: uploaded {png_path.name}")
        return True
    except Exception as error:  # noqa: BLE001 - fallback must never block drafting
        print(f"  diagram: upload failed ({error}); add the picture by hand: {png_path}")
        return False
