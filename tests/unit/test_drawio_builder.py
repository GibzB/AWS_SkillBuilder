"""Tests for the deterministic draw.io XML builder."""

from __future__ import annotations

import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from app.diagrams.spec import ICON_CATALOG, DiagramSpec, parse_spec

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

from diagrams.drawio_builder import ICON_SIZE, MAX_WIDTH, build_xml  # noqa: E402

pytestmark = pytest.mark.unit

SPEC_JSON = """{
  "nodes": [
    {"id": "user", "service": "user", "label": "User"},
    {"id": "cf", "service": "cloudfront", "label": "CloudFront"},
    {"id": "api", "service": "api_gateway", "label": "API Gateway"},
    {"id": "fn", "service": "lambda", "label": "Lambda"},
    {"id": "db", "service": "dynamodb", "label": "DynamoDB"},
    {"id": "q", "service": "sqs", "label": "SQS"}
  ],
  "edges": [
    {"from": "user", "to": "cf"}, {"from": "cf", "to": "api", "label": "HTTPS & TLS"},
    {"from": "api", "to": "fn"}, {"from": "fn", "to": "db"}, {"from": "fn", "to": "q"},
    {"from": "q", "to": "fn"}
  ],
  "groups": [
    {"id": "cloud", "kind": "aws_cloud", "label": "AWS Cloud", "members": ["cf", "api", "fn", "db", "q"]},
    {"id": "sub", "kind": "subnet_private", "label": "Private", "members": ["fn", "db"]}
  ]
}"""


def _spec(raw: str = SPEC_JSON) -> DiagramSpec:
    spec = parse_spec(raw)
    assert spec is not None
    return spec


def _cells(xml: str) -> list[ET.Element]:
    return list(ET.fromstring(xml).iter("mxCell"))


def test_output_parses_with_unique_ids() -> None:
    cells = _cells(build_xml(_spec()))
    ids = [c.get("id") for c in cells]
    assert len(ids) == len(set(ids))
    assert {"0", "1", "n-user", "g-cloud", "e-1"} <= set(ids)


def test_every_edge_endpoint_exists() -> None:
    cells = _cells(build_xml(_spec()))
    ids = {c.get("id") for c in cells}
    edges = [c for c in cells if c.get("edge") == "1"]
    assert len(edges) == 6
    for edge in edges:
        assert edge.get("source") in ids
        assert edge.get("target") in ids
        assert "edgeStyle=orthogonalEdgeStyle" in (edge.get("style") or "")
        assert "endArrow=block" in (edge.get("style") or "")


def test_every_shape_uses_aws4() -> None:
    cells = _cells(build_xml(_spec()))
    shaped = [c for c in cells if c.get("vertex") == "1"]
    assert len(shaped) == 8
    for cell in shaped:
        style = cell.get("style") or ""
        assert "mxgraph.aws4" in style
    icons = [c for c in shaped if (c.get("id") or "").startswith("n-")]
    assert any("resIcon=mxgraph.aws4.lambda" in (c.get("style") or "") for c in icons)


def test_icons_do_not_overlap_and_fit_width() -> None:
    cells = _cells(build_xml(_spec()))
    boxes = []
    for cell in cells:
        if (cell.get("id") or "").startswith("n-"):
            geo = cell.find("mxGeometry")
            assert geo is not None
            boxes.append((int(geo.get("x", "0")), int(geo.get("y", "0"))))
    for i, (x1, y1) in enumerate(boxes):
        assert x1 + ICON_SIZE <= MAX_WIDTH
        for x2, y2 in boxes[i + 1 :]:
            separate = x1 + ICON_SIZE <= x2 or x2 + ICON_SIZE <= x1 or y1 + ICON_SIZE <= y2 or y2 + ICON_SIZE <= y1
            assert separate


def test_deterministic() -> None:
    assert build_xml(_spec()) == build_xml(_spec())


def test_label_is_escaped() -> None:
    xml = build_xml(_spec())
    assert "HTTPS &amp; TLS" in xml
    labels = {c.get("value") for c in _cells(xml)}
    assert "HTTPS & TLS" in labels


def test_cycle_does_not_break_layout() -> None:
    xml = build_xml(_spec())  # the spec has q -> fn closing a cycle
    assert xml.startswith("<mxfile")


def test_long_chain_stays_within_width_without_overlap() -> None:
    nodes = ",".join(f'{{"id":"n{i}","service":"lambda","label":"L{i}"}}' for i in range(12))
    edges = ",".join(f'{{"from":"n{i}","to":"n{i + 1}"}}' for i in range(11))
    spec = _spec(f'{{"nodes":[{nodes}],"edges":[{edges}],"groups":[]}}')
    boxes = [
        c.find("mxGeometry") for c in _cells(build_xml(spec)) if (c.get("id") or "").startswith("n-")
    ]
    xs = sorted(int(g.get("x", "0")) for g in boxes if g is not None)
    assert xs[-1] + ICON_SIZE <= MAX_WIDTH
    assert all(b - a >= ICON_SIZE for a, b in zip(xs, xs[1:]))


@pytest.mark.parametrize("service", sorted(ICON_CATALOG))
def test_every_catalog_service_builds(service: str) -> None:
    spec = _spec(f'{{"nodes":[{{"id":"a","service":"{service}","label":"A"}}]}}')
    xml = build_xml(spec)
    assert "mxgraph.aws4." in xml
