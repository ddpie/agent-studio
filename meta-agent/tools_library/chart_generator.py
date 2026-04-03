"""Generate charts as inline SVG."""

TOOL_META = {
    "id": "generate_chart",
    "name": "Chart Generator",
    "description": "Generate bar, line, and pie charts as inline SVG",
    "category": "visualization",
}

TOOL_NAMES = "generate_chart"

TOOL_CODE = '''
@tool
def generate_chart(chart_type: str, data: str, title: str = "", x_label: str = "", y_label: str = "", width: int = 800, height: int = 400) -> str:
    """Generate a chart as inline SVG markup.

    The SVG will be rendered directly in the chat. Do NOT wrap it in a code block.

    Args:
        chart_type: Type of chart: "bar", "line", or "pie".
        data: JSON array of data points.
            - For bar/line: [{"x": "Label", "y": 123}, {"x": "Label2", "y": 456}]
            - For pie: [{"label": "Slice1", "value": 40}, {"label": "Slice2", "value": 60}]
            - Multiple series for line/bar: [{"x": "Jan", "y": 10, "series": "A"}, {"x": "Jan", "y": 20, "series": "B"}]
        title: Chart title.
        x_label: X-axis label (bar/line only).
        y_label: Y-axis label (bar/line only).
        width: Chart width in pixels. Default 800.
        height: Chart height in pixels. Default 400.

    Returns:
        SVG markup string to be displayed inline.

    Example:
        generate_chart("bar", '[{"x":"EKS","y":977829},{"x":"S3","y":290231}]', title="Top Services")
        generate_chart("pie", '[{"label":"EKS","value":977829},{"label":"S3","value":290231}]', title="Cost Distribution")
    """
    import json
    import math

    try:
        items = json.loads(data) if isinstance(data, str) else data
    except json.JSONDecodeError as e:
        return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 60"><text x="10" y="30" fill="red">JSON parse error: {e}</text></svg>'

    if not items:
        return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 60"><text x="10" y="30" fill="red">No data provided</text></svg>'

    colors = ["#3B82F6", "#EF4444", "#10B981", "#F59E0B", "#8B5CF6", "#EC4899", "#14B8A6", "#F97316"]
    pad = {"top": 50, "right": 30, "bottom": 70, "left": 80}
    cw = width - pad["left"] - pad["right"]
    ch = height - pad["top"] - pad["bottom"]

    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" style="max-width:{width}px;background:white;border-radius:8px;border:1px solid #e5e7eb;font-family:system-ui,sans-serif">']

    if title:
        svg.append(f'<text x="{width/2}" y="28" text-anchor="middle" font-size="14" font-weight="bold" fill="#1F2937">{title}</text>')

    def fmt(v):
        if abs(v) >= 1e6: return f"{v/1e6:.1f}M"
        if abs(v) >= 1e3: return f"{v/1e3:.1f}K"
        if isinstance(v, float): return f"{v:.1f}"
        return str(v)

    if chart_type == "pie":
        total = sum(item.get("value", 0) for item in items)
        if total == 0:
            return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 60"><text x="10" y="30" fill="red">All values are zero</text></svg>'
        cx, cy = width / 2, height / 2 + 5
        r = min(cw, ch) / 2 - 20
        angle = -math.pi / 2
        for i, item in enumerate(items):
            val = item.get("value", 0)
            label = item.get("label", f"Item {i+1}")
            pct = val / total
            sweep = pct * 2 * math.pi
            if sweep < 0.001:
                angle += sweep
                continue
            x1 = cx + r * math.cos(angle)
            y1 = cy + r * math.sin(angle)
            x2 = cx + r * math.cos(angle + sweep)
            y2 = cy + r * math.sin(angle + sweep)
            large = 1 if sweep > math.pi else 0
            color = colors[i % len(colors)]
            svg.append(f'<path d="M{cx},{cy} L{x1:.1f},{y1:.1f} A{r},{r} 0 {large} 1 {x2:.1f},{y2:.1f} Z" fill="{color}" stroke="white" stroke-width="1.5"/>')
            # Label at midpoint
            mid = angle + sweep / 2
            lx = cx + (r * 0.65) * math.cos(mid)
            ly = cy + (r * 0.65) * math.sin(mid)
            svg.append(f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="middle" font-size="11" fill="white" font-weight="bold">{pct*100:.0f}%</text>')
            angle += sweep
        # Legend
        lx, ly = 10, height - 20
        for i, item in enumerate(items):
            label = item.get("label", "")
            val = item.get("value", 0)
            color = colors[i % len(colors)]
            svg.append(f'<rect x="{lx}" y="{ly - 9}" width="10" height="10" fill="{color}" rx="2"/>')
            svg.append(f'<text x="{lx + 14}" y="{ly}" font-size="10" fill="#6B7280">{label} ({fmt(val)})</text>')
            lx += max(len(label) * 7 + 60, 80)
            if lx > width - 80:
                lx = 10
                ly += 16

    elif chart_type in ("bar", "line"):
        # Group by series
        series_map = {}
        x_labels = []
        for item in items:
            s = item.get("series", "default")
            if s not in series_map:
                series_map[s] = {}
            xl = str(item.get("x", ""))
            if xl not in x_labels:
                x_labels.append(xl)
            series_map[s][xl] = item.get("y", 0)

        all_vals = [v for s in series_map.values() for v in s.values()]
        if not all_vals:
            return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 60"><text x="10" y="30" fill="red">No numeric data</text></svg>'

        y_min = 0
        y_max = max(all_vals) * 1.15 or 1
        n = len(x_labels)
        series_list = list(series_map.keys())
        ns = len(series_list)

        # Grid lines + Y-axis labels
        for i in range(5):
            yv = y_max * i / 4
            yp = pad["top"] + ch - (i / 4) * ch
            svg.append(f'<line x1="{pad["left"]}" y1="{yp:.1f}" x2="{pad["left"] + cw}" y2="{yp:.1f}" stroke="#F3F4F6" stroke-width="1"/>')
            svg.append(f'<text x="{pad["left"] - 8}" y="{yp + 4:.1f}" text-anchor="end" font-size="10" fill="#9CA3AF">{fmt(yv)}</text>')

        # Axes
        svg.append(f'<line x1="{pad["left"]}" y1="{pad["top"]}" x2="{pad["left"]}" y2="{pad["top"] + ch}" stroke="#D1D5DB" stroke-width="1"/>')
        svg.append(f'<line x1="{pad["left"]}" y1="{pad["top"] + ch}" x2="{pad["left"] + cw}" y2="{pad["top"] + ch}" stroke="#D1D5DB" stroke-width="1"/>')

        gw = cw / max(n, 1)

        if chart_type == "bar":
            bw = gw * 0.7 / max(ns, 1)
            for si, sname in enumerate(series_list):
                color = colors[si % len(colors)]
                for gi, xl in enumerate(x_labels):
                    val = series_map[sname].get(xl, 0)
                    bh = (val / y_max) * ch if y_max else 0
                    bx = pad["left"] + gi * gw + gw * 0.15 + si * bw
                    by = pad["top"] + ch - bh
                    svg.append(f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bw * 0.9:.1f}" height="{bh:.1f}" fill="{color}" rx="2"/>')
                    # Data label on top of bar
                    if bh > 15:
                        svg.append(f'<text x="{bx + bw * 0.45:.1f}" y="{by - 4:.1f}" text-anchor="middle" font-size="9" fill="#6B7280">{fmt(val)}</text>')
        else:
            for si, sname in enumerate(series_list):
                color = colors[si % len(colors)]
                pts = []
                for gi, xl in enumerate(x_labels):
                    val = series_map[sname].get(xl, 0)
                    px = pad["left"] + gi * gw + gw / 2
                    py = pad["top"] + ch - (val / y_max) * ch if y_max else pad["top"] + ch
                    pts.append((px, py, val))
                if pts:
                    svg.append(f'<polyline points="{" ".join(f"{p[0]:.1f},{p[1]:.1f}" for p in pts)}" fill="none" stroke="{color}" stroke-width="2"/>')
                    for px, py, val in pts:
                        svg.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="3.5" fill="{color}"/>')
                        svg.append(f'<text x="{px:.1f}" y="{py - 8:.1f}" text-anchor="middle" font-size="9" fill="#6B7280">{fmt(val)}</text>')

        # X-axis labels (rotated if many)
        rotate = n > 6
        for gi, xl in enumerate(x_labels):
            tx = pad["left"] + gi * gw + gw / 2
            ty = pad["top"] + ch + 18
            if rotate:
                svg.append(f'<text x="{tx:.1f}" y="{ty:.1f}" text-anchor="end" font-size="10" fill="#6B7280" transform="rotate(-35,{tx:.1f},{ty:.1f})">{xl}</text>')
            else:
                svg.append(f'<text x="{tx:.1f}" y="{ty:.1f}" text-anchor="middle" font-size="10" fill="#6B7280">{xl}</text>')

        # Axis labels
        if x_label:
            svg.append(f'<text x="{pad["left"] + cw / 2}" y="{height - 8}" text-anchor="middle" font-size="11" fill="#6B7280">{x_label}</text>')
        if y_label:
            svg.append(f'<text x="14" y="{pad["top"] + ch / 2}" text-anchor="middle" font-size="11" fill="#6B7280" transform="rotate(-90,14,{pad["top"] + ch / 2})">{y_label}</text>')

        # Legend (only if multiple series)
        if ns > 1:
            lx = pad["left"]
            for si, sname in enumerate(series_list):
                color = colors[si % len(colors)]
                svg.append(f'<rect x="{lx}" y="{pad["top"] - 18}" width="10" height="10" fill="{color}" rx="2"/>')
                svg.append(f'<text x="{lx + 14}" y="{pad["top"] - 9}" font-size="10" fill="#6B7280">{sname}</text>')
                lx += len(sname) * 7 + 25
    else:
        svg.append(f'<text x="10" y="30" fill="red">Unsupported chart type: {chart_type}. Use bar, line, or pie.</text>')

    svg.append("</svg>")
    return "\\n".join(svg)
'''
