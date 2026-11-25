"""Diagram spec: the small JSON the model returns after ``---DIAGRAM---``.

The Lambda only validates the spec and stores it. Turning it into draw.io XML
and a PNG happens locally in ``tools/diagrams`` (this module has no draw.io
code). A bad spec is never an error for the article: :func:`parse_spec` returns
``None`` and the caller drops the diagram.

Shape names in :data:`ICON_CATALOG` were read from the draw.io AWS4 sidebar
palette (``Sidebar-AWS4.js`` in jgraph/drawio). Services that draw.io lists
under a different name are mapped explicitly (OpenSearch Service is
``elasticsearch_service``, CloudWatch is ``cloudwatch_2``, Budgets is
``budgets_2``, Certificate Manager is ``certificate_manager_3``, Amazon Quick
is ``quick_suite``). Terraform has no AWS4 shape and is not in the catalog.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

__all__ = [
    "MAX_NODES",
    "MAX_EDGES",
    "MAX_GROUPS",
    "MAX_LABEL",
    "GROUP_KINDS",
    "ICON_CATALOG",
    "CATEGORY_COLORS",
    "DiagramNode",
    "DiagramEdge",
    "DiagramGroup",
    "DiagramSpec",
    "parse_spec",
]

MAX_NODES = 12
MAX_EDGES = 16
MAX_GROUPS = 6
MAX_LABEL = 24

GROUP_KINDS = frozenset(
    {"aws_cloud", "region", "vpc", "subnet_public", "subnet_private"}
)

# Fill colour of each AWS4 category (the ``n2`` fill of its sidebar palette).
CATEGORY_COLORS: dict[str, str] = {
    "compute": "#ED7100",
    "containers": "#ED7100",
    "storage": "#7AA116",
    "database": "#C925D1",
    "networking": "#8C4FFF",
    "analytics": "#8C4FFF",
    "security": "#DD344C",
    "management": "#E7157B",
    "app_integration": "#E7157B",
    "ai_ml": "#01A88D",
    "developer_tools": "#C925D1",
    "cost": "#7AA116",
    "business_apps": "#DD344C",
    "general": "#232F3E",
}


def _c(shape: str, category: str) -> tuple[str, str]:
    return (f"mxgraph.aws4.{shape}", category)


# service key -> (draw.io shape name, category). Category "general" shapes are
# plain shapes (``shape=...``); every other category is a ``resourceIcon``.
ICON_CATALOG: dict[str, tuple[str, str]] = {
    # The 26 fundamentals.
    "iam": _c("identity_and_access_management", "security"),
    "s3": _c("s3", "storage"),
    "ec2": _c("ec2", "compute"),
    "vpc": _c("vpc", "networking"),
    "lambda": _c("lambda", "compute"),
    "api_gateway": _c("api_gateway", "networking"),
    "eventbridge": _c("eventbridge", "app_integration"),
    "cloudwatch": _c("cloudwatch_2", "management"),
    "systems_manager": _c("systems_manager", "management"),
    "dynamodb": _c("dynamodb", "database"),
    "rds": _c("rds", "database"),
    "aurora": _c("aurora", "database"),
    "sns": _c("sns", "app_integration"),
    "sqs": _c("sqs", "app_integration"),
    "step_functions": _c("step_functions", "app_integration"),
    "cloudfront": _c("cloudfront", "networking"),
    "kms": _c("key_management_service", "security"),
    "route53": _c("route_53", "networking"),
    "cloudformation": _c("cloudformation", "management"),
    "guardduty": _c("guardduty", "security"),
    "config": _c("config", "management"),
    "security_hub": _c("security_hub", "security"),
    "cost_explorer": _c("cost_explorer", "cost"),
    "budgets": _c("budgets_2", "cost"),
    # AI and data.
    "bedrock": _c("bedrock", "ai_ml"),
    "q": _c("q", "ai_ml"),
    "sagemaker": _c("sagemaker", "ai_ml"),
    "quick": _c("quick_suite", "business_apps"),
    "glue": _c("glue", "analytics"),
    "athena": _c("athena", "analytics"),
    "kinesis": _c("kinesis", "analytics"),
    "msk": _c("managed_streaming_for_kafka", "analytics"),
    "opensearch": _c("elasticsearch_service", "analytics"),
    "redshift": _c("redshift", "analytics"),
    # Containers and compute.
    "eks": _c("eks", "containers"),
    "ecs": _c("ecs", "containers"),
    "fargate": _c("fargate", "containers"),
    "ecr": _c("ecr", "containers"),
    "elb": _c("elastic_load_balancing", "networking"),
    # Storage, database, networking.
    "ebs": _c("elastic_block_store", "storage"),
    "efs": _c("elastic_file_system", "storage"),
    "backup": _c("backup", "storage"),
    "elasticache": _c("elasticache", "database"),
    "neptune": _c("neptune", "database"),
    "transit_gateway": _c("transit_gateway", "networking"),
    "direct_connect": _c("direct_connect", "networking"),
    "global_accelerator": _c("global_accelerator", "networking"),
    # Security and management.
    "cognito": _c("cognito", "security"),
    "waf": _c("waf", "security"),
    "shield": _c("shield", "security"),
    "inspector": _c("inspector", "security"),
    "cloudtrail": _c("cloudtrail", "management"),
    "secrets_manager": _c("secrets_manager", "security"),
    "certificate_manager": _c("certificate_manager_3", "security"),
    "organizations": _c("organizations", "management"),
    "xray": _c("xray", "developer_tools"),
    "codepipeline": _c("codepipeline", "developer_tools"),
    "codebuild": _c("codebuild", "developer_tools"),
    "codedeploy": _c("codedeploy", "developer_tools"),
    "cdk": _c("cloud_development_kit", "developer_tools"),
    # Generic shapes.
    "user": _c("user", "general"),
    "client": _c("client", "general"),
    "internet": _c("internet", "general"),
    "on_prem": _c("corporate_data_center", "general"),
}

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,24}$")
_FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*\n(.*?)\n?```\s*$", re.DOTALL)


@dataclass(frozen=True)
class DiagramNode:
    id: str
    service: str
    label: str


@dataclass(frozen=True)
class DiagramEdge:
    source: str
    target: str
    label: str = ""


@dataclass(frozen=True)
class DiagramGroup:
    id: str
    kind: str
    label: str
    members: tuple[str, ...]


@dataclass(frozen=True)
class DiagramSpec:
    nodes: tuple[DiagramNode, ...]
    edges: tuple[DiagramEdge, ...]
    groups: tuple[DiagramGroup, ...] = ()


def _text(value: object, limit: int, *, required: bool) -> str | None:
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if required and not cleaned:
        return None
    if len(cleaned) > limit or "<" in cleaned or ">" in cleaned or "\u2014" in cleaned:
        return None
    return cleaned


def parse_spec(raw: str) -> DiagramSpec | None:
    """Parse and validate a diagram spec, returning ``None`` for anything wrong.

    Never raises. Invalid JSON, an unknown service, a dangling edge or group
    member, duplicate ids or a size breach all give ``None``.
    """
    text = (raw or "").strip()
    if not text:
        return None
    fenced = _FENCE_RE.match(text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):
        return None
    if not isinstance(data, dict):
        return None
    raw_nodes = data.get("nodes")
    raw_edges = data.get("edges", [])
    raw_groups = data.get("groups", [])
    if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
        return None
    if not isinstance(raw_groups, list):
        return None
    if not 1 <= len(raw_nodes) <= MAX_NODES:
        return None
    if len(raw_edges) > MAX_EDGES or len(raw_groups) > MAX_GROUPS:
        return None

    nodes: list[DiagramNode] = []
    for entry in raw_nodes:
        if not isinstance(entry, dict):
            return None
        node_id, service = entry.get("id"), entry.get("service")
        label = _text(entry.get("label"), MAX_LABEL, required=True)
        if (
            not isinstance(node_id, str)
            or not _ID_RE.match(node_id)
            or not isinstance(service, str)
            or service not in ICON_CATALOG
            or label is None
        ):
            return None
        nodes.append(DiagramNode(node_id, service, label))
    node_ids = [n.id for n in nodes]
    if len(set(node_ids)) != len(node_ids):
        return None

    edges: list[DiagramEdge] = []
    for entry in raw_edges:
        if not isinstance(entry, dict):
            return None
        source, target = entry.get("from"), entry.get("to")
        label = _text(entry.get("label"), MAX_LABEL, required=False)
        if (
            not isinstance(source, str)
            or not isinstance(target, str)
            or source not in node_ids
            or target not in node_ids
            or label is None
        ):
            return None
        edges.append(DiagramEdge(source, target, label))

    groups: list[DiagramGroup] = []
    for entry in raw_groups:
        if not isinstance(entry, dict):
            return None
        group_id, kind, members = entry.get("id"), entry.get("kind"), entry.get("members")
        label = _text(entry.get("label"), MAX_LABEL, required=True)
        if (
            not isinstance(group_id, str)
            or not _ID_RE.match(group_id)
            or group_id in node_ids
            or kind not in GROUP_KINDS
            or label is None
            or not isinstance(members, list)
            or not members
            or not all(isinstance(m, str) and m in node_ids for m in members)
        ):
            return None
        groups.append(DiagramGroup(group_id, str(kind), label, tuple(members)))
    if len({g.id for g in groups}) != len(groups):
        return None

    return DiagramSpec(tuple(nodes), tuple(edges), tuple(groups))
