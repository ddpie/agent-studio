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
def generate_chart(chart_type: str, title: str, labels: str, datasets: str, width: int = 800, height: int = 400) -> str:
    """Generate a chart as inline SVG markup.

    The SVG will be rendered directly in the chat. Do NOT wrap it in a code block.

    Args:
        chart_type: Type of chart: "bar", "line", or "pie".
        title: Chart title.
        labels: JSON array of labels (e.g., \'["Jan", "Feb", "Mar"]\').
        datasets: JSON array of dataset objects (e.g., \'[{"label": "Sales", "data": [10, 20, 30], "color": "#3B82F6"}]\').
        width: Chart width in pixels. Default 800.
        height: Chart height in pixels. Default 400.

    Returns:
        SVG markup string to be displayed inline.
    """
    import json
    import math

    try:
        label_list = json.loads(labels)
        dataset_list = json.loads(datasets)
    except json.JSONDecodeError as e:
        return f"Error parsing JSON: {e}"

    colors = ["#3B82F6", "#EF4444", "#10B981", "#F59E0B", "#8B5CF6", "#EC4899"]
    padding = 60
    chart_w = width - padding * 2
    chart_h = height - padding * 2 - 30

    all_values = [v for ds in dataset_list for v in ds.get("data", [])]
    if not all_values:
        return "Error: No data provided"
    max_val = max(all_values) * 1.1 or 1

    svg_parts = [f\'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" style="max-width:{width}px;background:white;border-radius:8px;border:1px solid #e5e7eb">\']
    svg_parts.append(f\'<text x="{width/2}" y="25" text-anchor="middle" font-size="14" font-weight="bold" fill="#374151">{title}</text>\')

    if chart_type == "bar":
        n_groups = len(label_list)
        n_datasets = len(dataset_list)
        group_w = chart_w / max(n_groups, 1)
        bar_w = group_w * 0.7 / max(n_datasets, 1)

        for gi, label in enumerate(label_list):
            gx = padding + gi * group_w
            svg_parts.append(f\'<text x="{gx + group_w/2}" y="{height - 15}" text-anchor="middle" font-size="10" fill="#6B7280">{label}</text>\')
            for di, ds in enumerate(dataset_list):
                data = ds.get("data", [])
                color = ds.get("color", colors[di % len(colors)])
                if gi < len(data):
                    val = data[gi]
                    bar_h = (val / max_val) * chart_h
                    bx = gx + (group_w * 0.15) + di * bar_w
                    by = padding + 30 + chart_h - bar_h
                    svg_parts.append(f\'<rect x="{bx}" y="{by}" width="{bar_w * 0.9}" height="{bar_h}" fill="{color}" rx="2"/>\')

    elif chart_type == "line":
        for di, ds in enumerate(dataset_list):
            data = ds.get("data", [])
            color = ds.get("color", colors[di % len(colors)])
            points = []
            for i, val in enumerate(data):
                x = padding + (i / max(len(data) - 1, 1)) * chart_w
                y = padding + 30 + chart_h - (val / max_val) * chart_h
                points.append(f"{x},{y}")
            if points:
                svg_parts.append(f\'<polyline points="{" ".join(points)}" fill="none" stroke="{color}" stroke-width="2"/>\')
                for pt in points:
                    x, y = pt.split(",")
                    svg_parts.append(f\'<circle cx="{x}" cy="{y}" r="3" fill="{color}"/>\')

        for i, label in enumerate(label_list):
            x = padding + (i / max(len(label_list) - 1, 1)) * chart_w
            svg_parts.append(f\'<text x="{x}" y="{height - 15}" text-anchor="middle" font-size="10" fill="#6B7280">{label}</text>\')

    elif chart_type == "pie":
        cx, cy = width / 2, height / 2 + 10
        radius = min(chart_w, chart_h) / 2 - 10
        total = sum(all_values) or 1
        start_angle = -90
        for di, ds in enumerate(dataset_list):
            data = ds.get("data", [])
            color = ds.get("color", colors[di % len(colors)])
            label = ds.get("label", "")
            val = data[0] if data else 0
            angle = (val / total) * 360
            end_angle = start_angle + angle
            large = 1 if angle > 180 else 0
            x1 = cx + radius * math.cos(math.radians(start_angle))
            y1 = cy + radius * math.sin(math.radians(start_angle))
            x2 = cx + radius * math.cos(math.radians(end_angle))
            y2 = cy + radius * math.sin(math.radians(end_angle))
            svg_parts.append(f\'<path d="M{cx},{cy} L{x1},{y1} A{radius},{radius} 0 {large} 1 {x2},{y2} Z" fill="{color}"/>\')
            mid = math.radians(start_angle + angle / 2)
            tx = cx + (radius * 0.65) * math.cos(mid)
            ty = cy + (radius * 0.65) * math.sin(mid)
            pct = f"{val/total*100:.0f}%"
            svg_parts.append(f\'<text x="{tx}" y="{ty}" text-anchor="middle" font-size="11" fill="white" font-weight="bold">{pct}</text>\')
            start_angle = end_angle

    # Y-axis labels
    if chart_type in ("bar", "line"):
        for i in range(5):
            val = max_val * i / 4
            y = padding + 30 + chart_h - (i / 4) * chart_h
            svg_parts.append(f\'<text x="{padding - 8}" y="{y + 3}" text-anchor="end" font-size="9" fill="#9CA3AF">{val:.0f}</text>\')
            svg_parts.append(f\'<line x1="{padding}" y1="{y}" x2="{padding + chart_w}" y2="{y}" stroke="#F3F4F6" stroke-width="1"/>\')

    # Legend
    lx = padding
    for di, ds in enumerate(dataset_list):
        color = ds.get("color", colors[di % len(colors)])
        label = ds.get("label", f"Series {di+1}")
        svg_parts.append(f\'<rect x="{lx}" y="{padding + 5}" width="10" height="10" fill="{color}" rx="2"/>\')
        svg_parts.append(f\'<text x="{lx + 14}" y="{padding + 14}" font-size="10" fill="#6B7280">{label}</text>\')
        lx += len(label) * 7 + 25

    svg_parts.append("</svg>")
    return "\\n".join(svg_parts)
\'\'\'
'''
