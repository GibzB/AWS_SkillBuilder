"""Unit tests for the diagram spec parser and icon catalog."""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.diagrams.spec import (
    CATEGORY_COLORS,
    GROUP_KINDS,
    ICON_CATALOG,
    MAX_EDGES,
    MAX_NODES,
    parse_spec,
)

pytestmark = pytest.mark.unit


def _spec(**override: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "nodes": [
            {"id": "u", "service": "user", "label": "Reader"},
            {"id": "fn", "service": "lambda", "label": "Handler"},
            {"id": "q", "service": "sqs", "label": "Queue"},
        ],
        "edges": [{"from": "u", "to": "fn", "label": "invoke"}, {"from": "fn", "to": "q"}],
        "groups": [{"id": "g1", "kind": "region", "label": "Region", "members": ["fn", "q"]}],
    }
    base.update(override)
    return base


def _parse(data: Any) -> Any:
    return parse_spec(json.dumps(data))


def test_valid_spec_parses() -> None:
    spec = _parse(_spec())
    assert spec is not None
    assert [n.id for n in spec.nodes] == ["u", "fn", "q"]
    assert spec.edges[0].label == "invoke" and spec.edges[1].label == ""
    assert spec.groups[0].members == ("fn", "q")


def test_edges_and_groups_are_optional() -> None:
    spec = _parse({"nodes": [{"id": "a", "service": "s3", "label": "Bucket"}]})
    assert spec is not None and spec.edges == () and spec.groups == ()


def test_fenced_json_is_accepted() -> None:
    assert parse_spec("```json\n" + json.dumps(_spec()) + "\n```") is not None


@pytest.mark.parametrize("raw", ["", "   ", "not json", "[1, 2]", "null", '{"nodes": "x"}'])
def test_bad_json_or_shape_returns_none(raw: str) -> None:
    assert parse_spec(raw) is None


def test_unknown_service_rejected() -> None:
    data = _spec()
    data["nodes"][1]["service"] = "quantum_toaster"
    assert _parse(data) is None


def test_dangling_edge_rejected() -> None:
    assert _parse(_spec(edges=[{"from": "fn", "to": "ghost"}])) is None


def test_dangling_group_member_rejected() -> None:
    bad = [{"id": "g1", "kind": "vpc", "label": "VPC", "members": ["ghost"]}]
    assert _parse(_spec(groups=bad)) is None


def test_unknown_group_kind_rejected() -> None:
    bad = [{"id": "g1", "kind": "datacenter", "label": "X", "members": ["fn"]}]
    assert _parse(_spec(groups=bad)) is None


def test_duplicate_node_ids_rejected() -> None:
    data = _spec()
    data["nodes"][1]["id"] = "u"
    assert _parse(data) is None


def test_label_too_long_rejected() -> None:
    data = _spec()
    data["nodes"][0]["label"] = "x" * 25
    assert _parse(data) is None


def test_label_with_markup_or_em_dash_rejected() -> None:
    for label in ("<b>x</b>", "a \u2014 b"):
        data = _spec()
        data["nodes"][0]["label"] = label
        assert _parse(data) is None


def test_size_limits() -> None:
    many = [{"id": f"n{i}", "service": "s3", "label": "B"} for i in range(MAX_NODES + 1)]
    assert _parse({"nodes": many}) is None
    ok = many[:MAX_NODES]
    edges = [{"from": "n0", "to": "n1"}] * (MAX_EDGES + 1)
    assert _parse({"nodes": ok, "edges": edges}) is None
    assert _parse({"nodes": ok, "edges": edges[:MAX_EDGES]}) is not None
    assert _parse({"nodes": []}) is None


def test_non_string_fields_rejected() -> None:
    data = _spec()
    data["nodes"][0]["label"] = 5
    assert _parse(data) is None


def test_every_catalog_entry_is_an_aws4_shape_with_a_known_category() -> None:
    for key, (shape, category) in ICON_CATALOG.items():
        assert shape.startswith("mxgraph.aws4."), key
        assert category in CATEGORY_COLORS, key
    assert len({shape for shape, _ in ICON_CATALOG.values()}) == len(ICON_CATALOG)


def test_catalog_covers_the_fundamentals_and_generics() -> None:
    required = {
        "iam", "s3", "ec2", "vpc", "lambda", "api_gateway", "eventbridge", "cloudwatch",
        "systems_manager", "dynamodb", "rds", "sns", "sqs", "step_functions", "cloudfront",
        "kms", "route53", "cloudformation", "bedrock", "q", "sagemaker", "glue", "athena",
        "eks", "ecs", "fargate", "ecr", "kinesis", "opensearch", "cognito", "waf", "shield",
        "guardduty", "inspector", "config", "cloudtrail", "secrets_manager", "user",
        "client", "internet", "on_prem",
    }
    assert required <= set(ICON_CATALOG)
    assert GROUP_KINDS == {"aws_cloud", "region", "vpc", "subnet_public", "subnet_private"}
