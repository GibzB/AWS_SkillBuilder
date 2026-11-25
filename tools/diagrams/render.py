#!/usr/bin/env python3
"""Render a campaign day's diagram spec to ``.drawio`` and PNG.

LOCAL tool. Reads ``diagram_json`` from the campaign table (or ``--spec FILE``)
and writes ``day-NN.drawio`` and ``day-NN.png`` under
``~/.aws-builder-publish/diagrams/`` (outside the repo). No browser, no
publishing.

    python tools/diagrams/render.py --campaign aws-news-30 --day 3
    python tools/diagrams/render.py --spec spec.json --name sample
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Work as a script and as an import from tools/builder_publish.
_TOOLS = Path(__file__).resolve().parents[1]
for _path in (_TOOLS, _TOOLS.parent):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from app.diagrams.spec import parse_spec  # noqa: E402
from diagrams.drawio_builder import build_xml  # noqa: E402
from diagrams.export_png import ExportResult, export_png  # noqa: E402

REGION = "eu-west-1"
OUT_DIR = Path.home() / ".aws-builder-publish" / "diagrams"


@dataclass(frozen=True)
class RenderResult:
    drawio_path: Path | None
    png_path: Path | None
    message: str


def render_spec(
    raw_json: str,
    stem: str,
    out_dir: Path | None = None,
    runner: Callable[..., Any] = subprocess.run,
    cli: str | None = None,
    timeout: int = 60,
) -> RenderResult:
    """Write ``<stem>.drawio`` and try to export ``<stem>.png``."""
    spec = parse_spec(raw_json)
    if spec is None:
        return RenderResult(None, None, "The diagram spec is missing or invalid; no diagram written.")
    target = out_dir if out_dir is not None else OUT_DIR
    target.mkdir(parents=True, exist_ok=True)
    drawio = target / f"{stem}.drawio"
    drawio.write_text(build_xml(spec), encoding="utf-8")
    result: ExportResult = export_png(
        drawio, target / f"{stem}.png", runner=runner, timeout=timeout, cli=cli
    )
    return RenderResult(drawio, result.png_path, result.message)


def load_diagram_json(table: str, campaign_id: str, day: int, region: str = REGION) -> str:
    import boto3
    from botocore.config import Config

    ddb = boto3.client(
        "dynamodb", region_name=region,
        config=Config(connect_timeout=10, read_timeout=20, retries={"max_attempts": 2}),
    )
    resp = ddb.get_item(
        TableName=table,
        Key={"pk": {"S": f"CAMPAIGN#{campaign_id}"}, "sk": {"S": f"DAY#{day:04d}"}},
    )
    return str(resp.get("Item", {}).get("diagram_json", {}).get("S", ""))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render a diagram spec to draw.io and PNG")
    parser.add_argument("--campaign", default="aws-news-30")
    parser.add_argument("--table", default="")
    parser.add_argument("--day", type=int)
    parser.add_argument("--spec", type=Path, help="Read the spec JSON from a file instead of DynamoDB.")
    parser.add_argument("--name", default="", help="Output file stem (default day-NN).")
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--region", default=REGION)
    args = parser.parse_args(argv)

    if args.spec is not None:
        raw = args.spec.read_text(encoding="utf-8")
        stem = args.name or args.spec.stem
    elif args.day is not None:
        table = args.table or f"aws-builder-{args.campaign}-campaign"
        raw = load_diagram_json(table, args.campaign, args.day, args.region)
        stem = args.name or f"day-{args.day:02d}"
    else:
        parser.error("give --day N or --spec FILE")
    result = render_spec(raw, stem, args.out_dir)
    print(result.message)
    if result.drawio_path:
        print(f"drawio: {result.drawio_path}")
    return 0 if result.drawio_path else 1


if __name__ == "__main__":
    raise SystemExit(main())
