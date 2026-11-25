"""Deterministic draw.io XML builder for a validated :class:`DiagramSpec`.

Layout is a left-to-right layered graph: each node sits in the layer of its
longest path from a source (back edges from cycles are ignored). Icons are the
78 px AWS4 resource icons with the label below. Groups (AWS Cloud, Region, VPC,
subnets) are drawn behind the icons as the bounding box of their members. The
same spec always yields the same XML: no randomness and no clock.
"""

from __future__ import annotations

from xml.etree import ElementTree as ET

from app.diagrams.spec import (
    CATEGORY_COLORS,
    ICON_CATALOG,
    DiagramEdge,
    DiagramGroup,
    DiagramNode,
    DiagramSpec,
)

__all__ = ["build_xml", "ICON_SIZE", "MAX_WIDTH", "layout"]

ICON_SIZE = 78
MAX_WIDTH = 1200
_GAP_X = 120
_GAP_Y = 70  # vertical space between icons, leaves room for the label below
_MARGIN = 20
_GROUP_MARGIN = 60  # outer margin when groups are present
_LABEL_ROOM = 30
_TOP_ROOM = 30  # group title strip

_BASE = (
    "outlineConnect=0;gradientColor=none;html=1;whiteSpace=wrap;fontSize=12;"
    "fontStyle=0;container=1;pointerEvents=0;collapsible=0;recursiveResize=0;"
    "shape=mxgraph.aws4.group;verticalAlign=top;align=left;spacingLeft=30;dashed=0;"
)
# kind -> (style, padding, nesting rank: lower is drawn first, i.e. outermost)
_GROUP_STYLES: dict[str, tuple[str, int, int]] = {
    "aws_cloud": (
        _BASE + "grIcon=mxgraph.aws4.group_aws_cloud;strokeColor=#232F3E;"
        "fillColor=none;fontColor=#232F3E;",
        44,
        0,
    ),
    "region": (
        _BASE + "grIcon=mxgraph.aws4.group_region;strokeColor=#00A4A6;"
        "fillColor=none;fontColor=#147EBA;dashed=1;",
        36,
        1,
    ),
    "vpc": (
        _BASE + "grIcon=mxgraph.aws4.group_vpc2;strokeColor=#8C4FFF;"
        "fillColor=none;fontColor=#232F3E;",
        28,
        2,
    ),
    "subnet_public": (
        _BASE + "grIcon=mxgraph.aws4.group_security_group;grStroke=0;"
        "strokeColor=#7AA116;fillColor=#F2F6E8;fontColor=#248814;",
        18,
        3,
    ),
    "subnet_private": (
        _BASE + "grIcon=mxgraph.aws4.group_security_group;grStroke=0;"
        "strokeColor=#00A4A6;fillColor=#E6F6F7;fontColor=#147EBA;",
        18,
        3,
    ),
}
_EDGE_STYLE = (
    "edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;"
    "html=1;endArrow=block;endFill=1;strokeColor=#232F3E;strokeWidth=2;"
    "fontSize=11;fontColor=#232F3E;"
)
_ICON_COMMON = (
    "sketch=0;outlineConnect=0;fontColor=#232F3E;gradientColor=none;html=1;"
    "fontSize=12;fontStyle=0;aspect=fixed;verticalLabelPosition=bottom;"
    "verticalAlign=top;align=center;"
)


def _icon_style(service: str) -> str:
    shape, category = ICON_CATALOG[service]
    color = CATEGORY_COLORS[category]
    if category == "general":
        return f"{_ICON_COMMON}fillColor={color};strokeColor=none;dashed=0;shape={shape};"
    return (
        f"{_ICON_COMMON}fillColor={color};strokeColor=#ffffff;dashed=0;"
        f"shape=mxgraph.aws4.resourceIcon;resIcon={shape};"
    )


def _layers(spec: DiagramSpec) -> dict[str, int]:
    """Longest-path layer per node id, ignoring edges that close a cycle."""
    order = [n.id for n in spec.nodes]
    out: dict[str, list[str]] = {i: [] for i in order}
    for edge in spec.edges:
        if edge.source != edge.target:
            out[edge.source].append(edge.target)
    # Find back edges with an iterative DFS in node order.
    state: dict[str, int] = {}  # 1 = on stack, 2 = done
    back: set[tuple[str, str]] = set()
    for root in order:
        if root in state:
            continue
        stack: list[tuple[str, int]] = [(root, 0)]
        state[root] = 1
        while stack:
            node, idx = stack.pop()
            if idx < len(out[node]):
                stack.append((node, idx + 1))
                nxt = out[node][idx]
                if state.get(nxt) == 1:
                    back.add((node, nxt))
                elif nxt not in state:
                    state[nxt] = 1
                    stack.append((nxt, 0))
            else:
                state[node] = 2
    forward = {i: [t for t in out[i] if (i, t) not in back] for i in order}
    indegree = {i: 0 for i in order}
    for source in order:
        for target in forward[source]:
            indegree[target] += 1
    layer = {i: 0 for i in order}
    ready = [i for i in order if indegree[i] == 0]
    while ready:
        node = ready.pop(0)
        for target in forward[node]:
            layer[target] = max(layer[target], layer[node] + 1)
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
    return layer


def layout(spec: DiagramSpec) -> tuple[dict[str, tuple[int, int]], int, int]:
    """Return (top-left position per node id, canvas width, canvas height)."""
    layer = _layers(spec)
    group_index = {g.id: i for i, g in enumerate(spec.groups)}
    membership: dict[str, tuple[int, ...]] = {n.id: () for n in spec.nodes}
    for group in spec.groups:
        for member in group.members:
            membership[member] = (*membership[member], group_index[group.id])
    columns: dict[int, list[DiagramNode]] = {}
    for node in spec.nodes:
        columns.setdefault(layer[node.id], []).append(node)
    for nodes in columns.values():
        nodes.sort(key=lambda n: membership[n.id])  # stable: keeps spec order
    count = max(columns) + 1 if columns else 1
    margin = _GROUP_MARGIN if spec.groups else _MARGIN
    usable = MAX_WIDTH - 2 * margin
    gap = _GAP_X
    if count > 1:
        gap = max(0, min(_GAP_X, (usable - count * ICON_SIZE) // (count - 1)))
    tallest = max(len(v) for v in columns.values())
    pitch = ICON_SIZE + _GAP_Y
    top = margin + (_TOP_ROOM if spec.groups else 0)
    positions: dict[str, tuple[int, int]] = {}
    for col, nodes in sorted(columns.items()):
        x = margin + col * (ICON_SIZE + gap)
        # Centre shorter columns against the tallest one.
        offset = (tallest - len(nodes)) * pitch // 2
        for row, node in enumerate(nodes):
            positions[node.id] = (x, top + offset + row * pitch)
    width = margin * 2 + count * ICON_SIZE + (count - 1) * gap
    height = top + tallest * pitch - _GAP_Y + _LABEL_ROOM + margin
    return positions, min(width, MAX_WIDTH), height


def _group_box(
    group: DiagramGroup, positions: dict[str, tuple[int, int]], pad: int
) -> tuple[int, int, int, int]:
    xs = [positions[m][0] for m in group.members]
    ys = [positions[m][1] for m in group.members]
    x0, y0 = min(xs) - pad, min(ys) - pad - _TOP_ROOM // 2
    x1 = max(xs) + ICON_SIZE + pad
    y1 = max(ys) + ICON_SIZE + _LABEL_ROOM + pad // 2
    return max(0, x0), max(0, y0), x1 - max(0, x0), y1 - max(0, y0)


def _add_cell(
    root: ET.Element, cell_id: str, *, style: str, value: str = "",
    vertex: bool = False, edge: bool = False, source: str = "", target: str = "",
    geometry: tuple[int, int, int, int] | None = None,
) -> None:
    attrs = {"id": cell_id, "value": value, "style": style, "parent": "1"}
    if vertex:
        attrs["vertex"] = "1"
    if edge:
        attrs["edge"] = "1"
        attrs["source"] = source
        attrs["target"] = target
    cell = ET.SubElement(root, "mxCell", attrs)
    geo = ET.SubElement(cell, "mxGeometry")
    if geometry is not None:
        x, y, w, h = geometry
        geo.set("x", str(x))
        geo.set("y", str(y))
        geo.set("width", str(w))
        geo.set("height", str(h))
    else:
        geo.set("relative", "1")
    geo.set("as", "geometry")


def build_xml(spec: DiagramSpec) -> str:
    """Return draw.io (mxfile) XML for ``spec``."""
    positions, width, height = layout(spec)
    model = ET.Element(
        "mxGraphModel",
        {
            "dx": str(width), "dy": str(height), "grid": "0", "gridSize": "10",
            "guides": "1", "tooltips": "1", "connect": "1", "arrows": "1",
            "fold": "1", "page": "1", "pageScale": "1",
            "pageWidth": str(width), "pageHeight": str(height),
            "math": "0", "shadow": "0",
        },
    )
    root = ET.SubElement(model, "root")
    ET.SubElement(root, "mxCell", {"id": "0"})
    ET.SubElement(root, "mxCell", {"id": "1", "parent": "0"})

    groups = sorted(
        enumerate(spec.groups), key=lambda p: (_GROUP_STYLES[p[1].kind][2], p[0])
    )
    # Outer groups get more padding so nested boxes stay visibly inside.
    for _, group in groups:
        style, pad, _rank = _GROUP_STYLES[group.kind]
        depth_pad = pad + 6 * sum(
            1
            for other in spec.groups
            if other is not group
            and set(other.members) < set(group.members)
            and _GROUP_STYLES[other.kind][2] > _GROUP_STYLES[group.kind][2]
        )
        _add_cell(
            root, f"g-{group.id}", style=style, value=group.label, vertex=True,
            geometry=_group_box(group, positions, depth_pad),
        )
    for node in spec.nodes:
        x, y = positions[node.id]
        _add_cell(
            root, f"n-{node.id}", style=_icon_style(node.service), value=node.label,
            vertex=True, geometry=(x, y, ICON_SIZE, ICON_SIZE),
        )
    edge: DiagramEdge
    for number, edge in enumerate(spec.edges, start=1):
        _add_cell(
            root, f"e-{number}", style=_EDGE_STYLE, value=edge.label, edge=True,
            source=f"n-{edge.source}", target=f"n-{edge.target}",
        )

    diagram = ET.Element("diagram", {"id": "diagram-1", "name": "Architecture"})
    diagram.append(model)
    mxfile = ET.Element("mxfile", {"host": "app.diagrams.net", "version": "24.0.0"})
    mxfile.append(diagram)
    return ET.tostring(mxfile, encoding="unicode") + "\n"
