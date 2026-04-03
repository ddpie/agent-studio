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
        data: JSON array of data points. Flexible format — accepts multiple key names:
            - Bar/Line: [{"x": "Label", "y": 123}] or [{"name": "Label", "value": 123}]
            - Pie: [{"label": "Slice", "value": 40}] or [{"name": "Slice", "value": 40}]
            - Multi-series: [{"x": "Jan", "y": 10, "series": "A"}, {"x": "Jan", "y": 20, "series": "B"}]
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
        generate_chart("line", '[{"x":"Jan","y":100},{"x":"Feb","y":150}]', title="Trend", x_label="Month", y_label="Revenue")
    """
    import json
    import math

    # ── Parse input ──────────────────────────────────────────────────────
    try:
        items = json.loads(data) if isinstance(data, str) else data
    except json.JSONDecodeError as e:
        return _svg_error(f"JSON parse error: {e}", width)

    if not items or not isinstance(items, list):
        return _svg_error("No data provided. Expected a JSON array.", width)

    # ── Normalize data keys ──────────────────────────────────────────────
    # Accept: x/y, name/value, label/value — normalize to x/y for bar/line, label/value for pie
    def _norm(item):
        out = dict(item)
        # x-axis key: x > name > label
        if "x" not in out:
            out["x"] = out.get("name", out.get("label", ""))
        # y-axis key: y > value > count
        if "y" not in out:
            v = out.get("value", out.get("count", 0))
            out["y"] = v
        # pie keys
        if "label" not in out:
            out["label"] = out.get("name", out.get("x", ""))
        if "value" not in out:
            out["value"] = out.get("y", out.get("count", 0))
        # Ensure numeric
        for k in ("y", "value"):
            try:
                out[k] = float(out[k]) if out[k] is not None else 0
            except (ValueError, TypeError):
                out[k] = 0
        return out

    items = [_norm(item) for item in items]

    # ── Shared helpers ───────────────────────────────────────────────────
    COLORS = ["#3B82F6", "#EF4444", "#10B981", "#F59E0B", "#8B5CF6", "#EC4899", "#14B8A6", "#F97316", "#6366F1", "#84CC16"]

    def _esc(s):
        """Escape XML special chars."""
        return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

    def _fmt(v):
        """Format number for display."""
        v = float(v)
        av = abs(v)
        sign = "-" if v < 0 else ""
        if av >= 1e9: return f"{sign}{av/1e9:.1f}B"
        if av >= 1e6: return f"{sign}{av/1e6:.1f}M"
        if av >= 1e3: return f"{sign}{av/1e3:.1f}K"
        if av == int(av): return f"{sign}{int(av)}"
        return f"{sign}{av:.1f}"

    def _nice_ticks(lo, hi, target_ticks=5):
        """Calculate nice Y-axis tick values."""
        if hi <= lo:
            hi = lo + 1
        raw_step = (hi - lo) / target_ticks
        mag = 10 ** math.floor(math.log10(raw_step)) if raw_step > 0 else 1
        nice_steps = [1, 2, 2.5, 5, 10]
        step = mag
        for ns in nice_steps:
            if ns * mag >= raw_step:
                step = ns * mag
                break
        tick_min = math.floor(lo / step) * step
        tick_max = math.ceil(hi / step) * step
        ticks = []
        v = tick_min
        while v <= tick_max + step * 0.01:
            ticks.append(round(v, 10))
            v += step
        return ticks

    def _text_width(s, font_size=10):
        """Estimate text width. CJK chars are ~1.5x wider."""
        w = 0
        for ch in str(s):
            if ord(ch) > 0x2E80:
                w += font_size * 0.85
            else:
                w += font_size * 0.55
        return w

    # ── SVG start ────────────────────────────────────────────────────────
    pad = {"top": 55 if title else 30, "right": 30, "bottom": 75, "left": 85}
    cw = width - pad["left"] - pad["right"]
    ch = height - pad["top"] - pad["bottom"]

    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" style="max-width:{width}px;background:white;border-radius:8px;border:1px solid #e5e7eb;font-family:system-ui,-apple-system,sans-serif">']

    # Title
    if title:
        svg.append(f'<text x="{width/2}" y="28" text-anchor="middle" font-size="14" font-weight="600" fill="#1F2937">{_esc(title)}</text>')

    # ── PIE CHART ────────────────────────────────────────────────────────
    if chart_type == "pie":
        total = sum(item["value"] for item in items)
        if total == 0:
            return _svg_error("All values are zero", width)

        cx, cy = width * 0.45, pad["top"] + ch / 2
        r = min(cw * 0.4, ch / 2) - 10
        angle = -math.pi / 2

        for i, item in enumerate(items):
            val = item["value"]
            label = _esc(item["label"])
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
            color = COLORS[i % len(COLORS)]

            svg.append(f'<path d="M{cx},{cy} L{x1:.1f},{y1:.1f} A{r},{r} 0 {large} 1 {x2:.1f},{y2:.1f} Z" fill="{color}" stroke="white" stroke-width="1.5"><title>{label}: {_fmt(val)} ({pct*100:.1f}%)</title></path>')

            # Percentage label inside slice (only if big enough)
            mid = angle + sweep / 2
            if pct >= 0.05:
                lx = cx + r * 0.6 * math.cos(mid)
                ly = cy + r * 0.6 * math.sin(mid)
                svg.append(f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="middle" dominant-baseline="central" font-size="11" fill="white" font-weight="bold">{pct*100:.0f}%</text>')

            angle += sweep

        # Legend on the right side
        legend_x = cx + r + 40
        legend_y = pad["top"] + 10
        for i, item in enumerate(items):
            label = _esc(item["label"])
            val = item["value"]
            pct = val / total * 100
            color = COLORS[i % len(COLORS)]
            y = legend_y + i * 22
            if y > height - 20:
                break
            svg.append(f'<rect x="{legend_x}" y="{y - 5}" width="12" height="12" fill="{color}" rx="2"/>')
            svg.append(f'<text x="{legend_x + 18}" y="{y + 5}" font-size="11" fill="#374151">{label}</text>')
            svg.append(f'<text x="{legend_x + 18}" y="{y + 18}" font-size="9" fill="#9CA3AF">{_fmt(val)} ({pct:.1f}%)</text>')
            legend_y += 4  # extra spacing for the sub-line

    # ── BAR / LINE CHART ─────────────────────────────────────────────────
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
            series_map[s][xl] = item["y"]

        all_vals = [v for s in series_map.values() for v in s.values()]
        if not all_vals:
            return _svg_error("No numeric data found", width)

        series_list = list(series_map.keys())
        ns = len(series_list)
        n = len(x_labels)

        # Calculate nice Y-axis ticks
        data_min = min(0, min(all_vals))
        data_max = max(all_vals)
        if data_max <= data_min:
            data_max = data_min + 1
        ticks = _nice_ticks(data_min, data_max)
        y_lo = ticks[0]
        y_hi = ticks[-1]
        y_range = y_hi - y_lo if y_hi != y_lo else 1

        def _y(val):
            return pad["top"] + ch - (val - y_lo) / y_range * ch

        # Grid lines + Y-axis labels
        for tv in ticks:
            yp = _y(tv)
            svg.append(f'<line x1="{pad["left"]}" y1="{yp:.1f}" x2="{pad["left"] + cw}" y2="{yp:.1f}" stroke="#F3F4F6" stroke-width="1"/>')
            svg.append(f'<text x="{pad["left"] - 8}" y="{yp + 4:.1f}" text-anchor="end" font-size="10" fill="#9CA3AF">{_fmt(tv)}</text>')

        # Axes
        base_y = _y(0) if y_lo <= 0 <= y_hi else _y(y_lo)
        svg.append(f'<line x1="{pad["left"]}" y1="{pad["top"]}" x2="{pad["left"]}" y2="{pad["top"] + ch}" stroke="#D1D5DB" stroke-width="1"/>')
        svg.append(f'<line x1="{pad["left"]}" y1="{base_y:.1f}" x2="{pad["left"] + cw}" y2="{base_y:.1f}" stroke="#D1D5DB" stroke-width="1"/>')

        gw = cw / max(n, 1)

        if chart_type == "bar":
            bw = gw * 0.7 / max(ns, 1)
            for si, sname in enumerate(series_list):
                color = COLORS[si % len(COLORS)]
                for gi, xl in enumerate(x_labels):
                    val = series_map[sname].get(xl, 0)
                    bar_top = _y(val)
                    bar_bottom = _y(0) if y_lo <= 0 else _y(y_lo)
                    bh = abs(bar_bottom - bar_top)
                    by = min(bar_top, bar_bottom)
                    bx = pad["left"] + gi * gw + gw * 0.15 + si * bw

                    svg.append(f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bw * 0.9:.1f}" height="{max(bh, 0.5):.1f}" fill="{color}" rx="2"><title>{_esc(xl)}: {_fmt(val)}</title></rect>')

                    # Data label
                    if bh > 18:
                        label_y = bar_top - 4 if val >= 0 else bar_top + bh + 12
                        svg.append(f'<text x="{bx + bw * 0.45:.1f}" y="{label_y:.1f}" text-anchor="middle" font-size="9" fill="#6B7280">{_fmt(val)}</text>')

        elif chart_type == "line":
            for si, sname in enumerate(series_list):
                color = COLORS[si % len(COLORS)]
                pts = []
                for gi, xl in enumerate(x_labels):
                    val = series_map[sname].get(xl)
                    if val is None:
                        continue
                    px = pad["left"] + gi * gw + gw / 2
                    py = _y(val)
                    pts.append((px, py, val, xl))

                if len(pts) > 1:
                    # Line
                    svg.append(f'<polyline points="{" ".join(f"{p[0]:.1f},{p[1]:.1f}" for p in pts)}" fill="none" stroke="{color}" stroke-width="2.5" stroke-linejoin="round"/>')
                    # Area fill (subtle)
                    area_base = _y(0) if y_lo <= 0 else _y(y_lo)
                    area_pts = f"{pts[0][0]:.1f},{area_base:.1f} " + " ".join(f"{p[0]:.1f},{p[1]:.1f}" for p in pts) + f" {pts[-1][0]:.1f},{area_base:.1f}"
                    svg.append(f'<polygon points="{area_pts}" fill="{color}" opacity="0.08"/>')

                for px, py, val, xl in pts:
                    svg.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="3.5" fill="white" stroke="{color}" stroke-width="2"><title>{_esc(xl)}: {_fmt(val)}</title></circle>')
                    # Data label (skip if too crowded)
                    if n <= 12:
                        svg.append(f'<text x="{px:.1f}" y="{py - 10:.1f}" text-anchor="middle" font-size="9" fill="#6B7280">{_fmt(val)}</text>')

        # X-axis labels
        rotate = n > 8 or any(_text_width(xl) > gw * 0.9 for xl in x_labels)
        for gi, xl in enumerate(x_labels):
            tx = pad["left"] + gi * gw + gw / 2
            ty = pad["top"] + ch + 18
            if rotate:
                svg.append(f'<text x="{tx:.1f}" y="{ty:.1f}" text-anchor="end" font-size="10" fill="#6B7280" transform="rotate(-40,{tx:.1f},{ty:.1f})">{_esc(xl)}</text>')
            else:
                svg.append(f'<text x="{tx:.1f}" y="{ty:.1f}" text-anchor="middle" font-size="10" fill="#6B7280">{_esc(xl)}</text>')

        # Axis labels
        if x_label:
            svg.append(f'<text x="{pad["left"] + cw / 2}" y="{height - 6}" text-anchor="middle" font-size="11" fill="#6B7280">{_esc(x_label)}</text>')
        if y_label:
            svg.append(f'<text x="14" y="{pad["top"] + ch / 2}" text-anchor="middle" font-size="11" fill="#6B7280" transform="rotate(-90,14,{pad["top"] + ch / 2})">{_esc(y_label)}</text>')

        # Legend (only if multiple series)
        if ns > 1:
            lx = pad["left"]
            for si, sname in enumerate(series_list):
                color = COLORS[si % len(COLORS)]
                svg.append(f'<rect x="{lx}" y="{pad["top"] - 20}" width="12" height="12" fill="{color}" rx="2"/>')
                svg.append(f'<text x="{lx + 16}" y="{pad["top"] - 10}" font-size="10" fill="#6B7280">{_esc(sname)}</text>')
                lx += _text_width(sname, 10) + 30

    else:
        return _svg_error(f"Unsupported chart type: {chart_type}. Use bar, line, or pie.", width)

    svg.append("</svg>")
    return "\\n".join(svg)


def _svg_error(msg, width=400):
    """Return an SVG error message."""
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} 50" style="max-width:{width}px"><rect width="{width}" height="50" fill="#FEF2F2" rx="6"/><text x="15" y="30" font-size="12" fill="#DC2626" font-family="system-ui,sans-serif">{msg}</text></svg>'
'''
