"""whiteboard_md_renderer.py — 共享画板 markdown 渲染逻辑 (PR22 + PR24).

输入: raw whiteboard nodes (来自 /board/v1/whiteboards/{id}/nodes API)
输出: markdown 文本 (供 docx 嵌入或 client chunk 使用)

3 个 renderer:
  - mindmap_tree:  mind_map 节点占比 ≥ 50% → 嵌套 bullet (利用 parent_id/children)
  - flowchart:     真实 connector 边 ≥ 3 → mermaid + 节点 bullet 兜底
  - 2d_grid:       兜底, 按 x/y 聚类成 2D 表 (适合 Gantt/timeline)
"""
from __future__ import annotations

from typing import List, Dict


# ── 节点文本提取 ────────────────────────────────────────────────────────────

def extract_node_text(node: dict) -> str:
    """从画板节点提取纯文本 (支持 text 和 rich_text)."""
    parts: list[str] = []
    t_obj = node.get("text")
    if t_obj and isinstance(t_obj, dict):
        t = (t_obj.get("text", "") or "").strip()
        if t:
            parts.append(t)
    rt = node.get("rich_text")
    if rt and isinstance(rt, dict):
        for para in rt.get("paragraphs", []) or []:
            for el in para.get("elements", []) or []:
                tel = el.get("text_element")
                if tel and isinstance(tel, dict):
                    t = (tel.get("text", "") or "").strip()
                    if t:
                        parts.append(t)
                lel = el.get("link_element")
                if lel and isinstance(lel, dict):
                    t = (lel.get("text", "") or "").strip()
                    if t:
                        parts.append(t)
    return " ".join(" ".join(p.split()) for p in parts if p).strip()


# ── 真实边计数 ──────────────────────────────────────────────────────────────

def count_real_edges(connectors: list[dict]) -> int:
    """统计真实有 src/dst 节点 id 的 connector (排除 specified_coordinate 虚线分隔)."""
    n = 0
    for c in connectors:
        conn = c.get("connector", {})
        s = conn.get("start_object") or conn.get("start", {})
        e = conn.get("end_object") or conn.get("end", {})
        if isinstance(s, dict) and s.get("id") and isinstance(e, dict) and e.get("id"):
            n += 1
    return n


# ── Renderer A: mindmap tree ────────────────────────────────────────────────

def render_mindmap_tree(text_nodes: list[dict]) -> str:
    """mind_map 节点按 parent_id/children 渲染嵌套 bullet 树."""
    by_id = {n["id"]: n for n in text_nodes if n.get("id")}
    parent_map: dict[str, str] = {}
    for n in text_nodes:
        mm = n.get("mind_map") or n.get("mind_map_node") or {}
        pid = mm.get("parent_id") if isinstance(mm, dict) else None
        if not pid:
            pid = n.get("parent_id")
        parent_map[n["id"]] = pid or ""

    # 找根: parent_id 为空 或 parent 不在 by_id
    roots = [n for n in text_nodes
             if not parent_map.get(n["id"]) or parent_map[n["id"]] not in by_id]
    if not roots:
        roots = [text_nodes[0]] if text_nodes else []

    # 子节点索引
    children_map: dict[str, list[str]] = {}
    for nid, pid in parent_map.items():
        if pid and pid in by_id:
            children_map.setdefault(pid, []).append(nid)

    lines: list[str] = []
    visited: set = set()

    def dfs(nid: str, depth: int):
        if nid in visited or depth > 20:
            return
        visited.add(nid)
        node = by_id.get(nid)
        if not node:
            return
        text = extract_node_text(node)
        if text:
            lines.append("  " * depth + f"- {text}")
        for child_id in children_map.get(nid, []):
            dfs(child_id, depth + 1)

    for root in roots:
        dfs(root["id"], 0)

    # 孤立节点 (没被 DFS 到的) 单独列出
    leftover = [n for n in text_nodes
                if n.get("id") and n["id"] not in visited]
    for n in leftover:
        t = extract_node_text(n)
        if t:
            lines.append(f"- {t}")

    return "\n".join(lines)


# ── Renderer B: 2D grid (Gantt) ────────────────────────────────────────────

def _cluster_1d(values: list[float], tol: float = 80.0) -> dict[float, int]:
    """1D 聚类: 把 values 按 tol 容差合并成簇, 返回 {value: cluster_idx}."""
    if not values:
        return {}
    pairs = sorted(set(values))
    clusters: list[float] = [pairs[0]]
    for v in pairs[1:]:
        if v - clusters[-1] > tol:
            clusters.append(v)
    result = {}
    for v in pairs:
        best = min(range(len(clusters)), key=lambda i: abs(clusters[i] - v))
        result[v] = best
    return result


def render_2d_grid(text_nodes: list[dict]) -> str:
    """按 x/y 坐标聚类成 2D 表格. 适合 Gantt / timeline matrix."""
    valid = [n for n in text_nodes if isinstance(n.get("x"), (int, float))
             and isinstance(n.get("y"), (int, float))]
    if not valid:
        # 没坐标兜底: 平铺
        lines = []
        for n in text_nodes:
            t = extract_node_text(n)
            if t:
                lines.append(f"- {t}")
        return "\n".join(lines)

    xs = [n["x"] for n in valid]
    ys = [n["y"] for n in valid]
    col_map = _cluster_1d(xs, tol=80.0)
    row_map = _cluster_1d(ys, tol=60.0)

    n_cols = max(col_map.values()) + 1 if col_map else 0
    n_rows = max(row_map.values()) + 1 if row_map else 0
    if n_cols == 0 or n_rows == 0:
        return ""

    grid: list[list[list[str]]] = [
        [[] for _ in range(n_cols)] for _ in range(n_rows)
    ]
    for n in valid:
        t = extract_node_text(n)
        if not t:
            continue
        r = row_map[n["y"]]
        c = col_map[n["x"]]
        grid[r][c].append(t)

    md = []
    md.append("| " + " | ".join(f"col {c+1}" for c in range(n_cols)) + " |")
    md.append("|" + "|".join(["---"] * n_cols) + "|")
    for r in range(n_rows):
        cells = []
        for c in range(n_cols):
            t = " / ".join(grid[r][c]) if grid[r][c] else " "
            cells.append(t.replace("|", "\\|").replace("\n", " "))
        md.append("| " + " | ".join(cells) + " |")
    return "\n".join(md)


# ── Renderer C: flowchart mermaid ──────────────────────────────────────────

def render_flowchart_mermaid(text_nodes: list[dict],
                             connectors: list[dict]) -> str:
    """真实有边的 flowchart → mermaid + 节点 bullet 双重."""
    id_to_text: dict[str, str] = {}
    for n in text_nodes:
        nid = n.get("id", "")
        t = extract_node_text(n)
        if nid and t:
            id_to_text[nid] = t

    edges: list[tuple[str, str]] = []
    for c in connectors:
        conn = c.get("connector", {})
        s = conn.get("start_object") or conn.get("start", {})
        e = conn.get("end_object") or conn.get("end", {})
        src = s.get("id") if isinstance(s, dict) else None
        dst = e.get("id") if isinstance(e, dict) else None
        if src in id_to_text and dst in id_to_text:
            edges.append((src, dst))

    def safe_id(x: str) -> str:
        return x.replace(":", "_").replace(" ", "_").replace("-", "_")

    def safe_label(t: str) -> str:
        return t.replace('"', "'").replace("\n", " ").replace("[", "(").replace("]", ")")

    lines = ["```mermaid", "flowchart LR"]
    for nid, txt in id_to_text.items():
        lines.append(f'    {safe_id(nid)}["{safe_label(txt)}"]')
    for src, dst in edges:
        lines.append(f"    {safe_id(src)} --> {safe_id(dst)}")
    lines.append("```")

    text_bullets = ["", "节点列表:"]
    for nid, t in id_to_text.items():
        text_bullets.append(f"- {t}")

    return "\n".join(lines + text_bullets)


# ── 主 dispatcher ─────────────────────────────────────────────────────────

def render_whiteboard_md(nodes: List[Dict]) -> str:
    """画板原始 nodes → markdown. 自动选 3 个 renderer 之一.

    Args:
        nodes: /board/v1/whiteboards/{id}/nodes API 返回的原始节点列表
               (含 connector 节点)

    Returns:
        markdown 字符串 (不含 [whiteboard] 头部, 调用方自行拼).
        无可用节点时返回空字符串.
    """
    if not nodes:
        return ""
    text_nodes = [n for n in nodes if n.get("type") != "connector"]
    connectors = [n for n in nodes if n.get("type") == "connector"]
    if not text_nodes:
        return ""

    n_mindmap = sum(1 for n in text_nodes if n.get("type") == "mind_map")
    real_edges = count_real_edges(connectors)

    if n_mindmap >= 1 and n_mindmap / len(text_nodes) >= 0.5:
        return render_mindmap_tree(text_nodes)
    if real_edges >= 3:
        return render_flowchart_mermaid(text_nodes, connectors)
    return render_2d_grid(text_nodes)
