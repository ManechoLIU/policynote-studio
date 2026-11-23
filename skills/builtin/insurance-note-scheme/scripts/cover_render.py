#!/usr/bin/env python3
"""cover_render.py — 保险图文封面模板的确定性渲染（PIL，不依赖浏览器）。

输入 outputs/<主题>/assets/covers/cover.json，输出 方案X/封面.png。
四种构图由 assets/templates/cover.json 定义，配色/字号锁定，不允许代理自写渲染。

用法：
    python .../cover_render.py --project outputs/重疾险-等待期
    python .../cover_render.py --project outputs/重疾险-等待期 --scale 2
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1440
PAD = 80
PAPER = "#F2F4F5"
INK = "#0A1F3D"
MUTED = "#5F6D78"
ACCENT = "#315D93"
RULE = "#D5DBE0"
CARD = "#EEF1F3"

FONT_SERIF = r"C:\Windows\Fonts\NotoSerifSC-VF.ttf"
FONT_SANS = r"C:\Windows\Fonts\NotoSansSC-VF.ttf"
FONT_MONO = r"C:\Windows\Fonts\consola.ttf"
FALLBACK_SERIF = r"C:\Windows\Fonts\simsun.ttc"
FALLBACK_SANS = r"C:\Windows\Fonts\msyh.ttc"


def _font(size: int, kind: str = "serif", scale: int = 1):
    size = int(size * scale)
    if kind == "mono":
        candidates = [FONT_MONO]
    elif kind == "sans":
        candidates = [FONT_SANS, FALLBACK_SANS]
    else:
        candidates = [FONT_SERIF, FALLBACK_SERIF]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _width(draw: ImageDraw.ImageDraw, text: str, font) -> float:
    return draw.textlength(text, font=font)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: float) -> list[str]:
    lines, line = [], ""
    for char in text:
        candidate = line + char
        if line and _width(draw, candidate, font) > max_width:
            lines.append(line)
            line = char
        else:
            line = candidate
    if line:
        lines.append(line)
    return lines


def _draw_wrapped(draw, text, x, y, font, fill, max_width, leading=1.55):
    step = int(font.size * leading)
    for line in _wrap(draw, text, font, max_width):
        draw.text((x, y), line, font=font, fill=fill)
        y += step
    return y


def _tracked(draw, text, x, y, font, fill, tracking=2):
    cursor = x
    for char in text:
        draw.text((cursor, y), char, font=font, fill=fill)
        cursor += _width(draw, char, font) + tracking
    return cursor


def _hline(draw, y, x0=PAD, x1=W - PAD):
    draw.line((x0, y, x1, y), fill=RULE, width=2)


def _header(draw, spec, letter, scale):
    draw.rectangle((0, 0, W * scale, 6 * scale), fill=ACCENT)
    _tracked(draw, spec.get("kicker", ""), PAD * scale, 76 * scale,
             _font(21, "sans", scale), MUTED, 2 * scale)
    draw.text(((W - PAD - 18) * scale, 74 * scale), letter,
              font=_font(20, "mono", scale), fill=MUTED)
    _hline(draw, 120 * scale)


def _footer(draw, spec, scale):
    left, right = (spec.get("footer") or ["", "内容为个人理解，具体以条款为准"])[:2]
    _hline(draw, 1338 * scale)
    draw.text((PAD * scale, 1360 * scale), left,
              font=_font(22, "sans", scale), fill=MUTED)
    if right:
        width = _width(draw, right, _font(22, "sans", scale))
        draw.text(((W - PAD) * scale - width, 1360 * scale), right,
                  font=_font(22, "sans", scale), fill=MUTED)


def _band(draw, spec, y, scale):
    label, text = (spec.get("band") or ["本篇回答", ""])[:2]
    draw.rectangle((PAD * scale, y * scale, (PAD + 6) * scale, (y + 118) * scale), fill=ACCENT)
    draw.text(((PAD + 26) * scale, (y + 6) * scale), label,
              font=_font(19, "sans", scale), fill=MUTED)
    _draw_wrapped(draw, text, (PAD + 26) * scale, (y + 34) * scale,
                  _font(40, "serif", scale), INK, 820 * scale, 1.35)


def _panel(draw, spec, y, scale, keys=("适用", "依据", "不适用")):
    rows = (spec.get("rows") or [])[:3]
    _hline(draw, y * scale)
    for index, row in enumerate(rows):
        label = row[0] if len(row) > 0 else ""
        value = row[1] if len(row) > 1 else ""
        draw.text((PAD * scale, (y + 24) * scale), label,
                  font=_font(19, "sans", scale), fill=MUTED)
        _draw_wrapped(draw, value, (PAD + 150) * scale, (y + 20) * scale,
                      _font(28, "sans", scale), "#22374F", 760 * scale, 1.45)
        y += 84
        _hline(draw, y * scale)
    return y


def _layout_a(draw, spec, scale):
    _header(draw, spec, spec.get("letter", "A"), scale)
    title = spec.get("title") or []
    y = 224 if len(title) > 1 else 286
    for line in title:
        draw.text((PAD * scale, y * scale), line, font=_font(124, "serif", scale), fill=INK)
        y += 144
    y += 26
    y = _draw_wrapped(draw, spec.get("lede", ""), PAD * scale, y * scale,
                      _font(36, "sans", scale), "#2B3F57", 900 * scale, 1.55) // scale
    y = max(y + 70, 770)
    _panel(draw, spec, y, scale)
    _band(draw, spec, 1108, scale)
    _footer(draw, spec, scale)


def _layout_b(draw, spec, scale):
    _header(draw, spec, spec.get("letter", "B"), scale)
    y = 200
    for line in spec.get("title") or []:
        draw.text((PAD * scale, y * scale), line, font=_font(86, "serif", scale), fill=INK)
        y += 106
    table = spec.get("table") or []
    top = max(y + 48, 372)
    left_w = 450
    x0, x1, xm = PAD, W - PAD, PAD + left_w
    row_h = 108
    draw.rectangle((x0 * scale, top * scale, x1 * scale, (top + 64 + row_h * max(len(table) - 1, 0)) * scale),
                   outline=RULE, width=2)
    for row_index, row in enumerate(table):
        cell_y = top + 64 + (row_index - 1) * row_h if row_index else top
        height = 64 if row_index == 0 else row_h
        if row_index:
            _hline(draw, cell_y * scale, x0, x1)
        draw.line((xm * scale, cell_y * scale, xm * scale, (cell_y + height) * scale),
                  fill=RULE, width=2)
        for col, text in enumerate(row[:2]):
            cell_x = x0 if col == 0 else xm
            if row_index == 0:
                font = _font(19, "sans", scale)
                fill = MUTED if col == 0 else ACCENT
                draw.rectangle((cell_x * scale, cell_y * scale, (cell_x + (left_w if col == 0 else left_w)) * scale,
                                (cell_y + height) * scale), fill=CARD)
            else:
                font = _font(29, "sans", scale)
                fill = "#1E334B"
            _draw_wrapped(draw, text, (cell_x + 26) * scale, (cell_y + 18) * scale,
                          font, fill, (left_w - 52) * scale, 1.4)
    _band(draw, spec, 1092, scale)
    _footer(draw, spec, scale)


def _layout_c(draw, spec, scale):
    _header(draw, spec, spec.get("letter", "C"), scale)
    y = 200
    for line in spec.get("title") or []:
        draw.text((PAD * scale, y * scale), line, font=_font(86, "serif", scale), fill=INK)
        y += 106
    y = max(y + 40, 360)
    for index, item in enumerate((spec.get("items") or [])[:3]):
        no = item[0] if len(item) > 0 else f"{index + 1:02d}"
        title = item[1] if len(item) > 1 else ""
        body = item[2] if len(item) > 2 else ""
        draw.text((PAD * scale, (y + 4) * scale), no,
                  font=_font(28, "mono", scale), fill=ACCENT)
        draw.text(((PAD + 92) * scale, y * scale), title,
                  font=_font(44, "serif", scale), fill=INK)
        _draw_wrapped(draw, body, (PAD + 92) * scale, (y + 58) * scale,
                      _font(27, "sans", scale), "#2B3F57", 760 * scale, 1.55)
        y += 182
        _hline(draw, y * scale)
        y += 22
    _band(draw, spec, 1078, scale)
    _footer(draw, spec, scale)


def _layout_d(draw, spec, scale):
    _header(draw, spec, spec.get("letter", "D"), scale)
    title = spec.get("title") or {}
    line1 = title.get("line1", "")
    pre = title.get("pre", "")
    keyword = title.get("keyword", "")
    post = title.get("post", "")
    draw.text((PAD * scale, 210 * scale), line1, font=_font(104, "serif", scale), fill=INK)
    y = 350
    cursor = PAD
    draw.text((cursor * scale, y * scale), pre, font=_font(104, "serif", scale), fill=INK)
    cursor += _width(draw, pre, _font(104, "serif", scale))
    draw.text((cursor * scale, y * scale), keyword, font=_font(104, "serif", scale), fill=INK)
    key_w = _width(draw, keyword, _font(104, "serif", scale))
    draw.ellipse(((cursor - 14) * scale, (y + 6) * scale,
                  (cursor + key_w + 14) * scale, (y + 138) * scale),
                 outline=ACCENT, width=3)
    cursor += key_w
    draw.text((cursor * scale, y * scale), post, font=_font(104, "serif", scale), fill=INK)

    note = spec.get("note") or {}
    card_top, card_left, card_w = 520, 330, 670
    draw.rectangle((card_left * scale, card_top * scale,
                    (card_left + card_w) * scale, (card_top + 236) * scale),
                   fill=CARD, outline=RULE, width=2)
    draw.text(((card_left + 30) * scale, (card_top + 24) * scale),
              note.get("label", "划重点"), font=_font(19, "sans", scale), fill=ACCENT)
    _draw_wrapped(draw, note.get("judgment", ""), (card_left + 30) * scale, (card_top + 60) * scale,
                  _font(28, "sans", scale), "#22374F", (card_w - 60) * scale, 1.5)
    draw.line(((card_left + 30) * scale, (card_top + 146) * scale,
               (card_left + card_w - 30) * scale, (card_top + 146) * scale), fill=RULE, width=2)
    _draw_wrapped(draw, note.get("boundary", ""), (card_left + 30) * scale, (card_top + 166) * scale,
                  _font(28, "sans", scale), "#22374F", (card_w - 60) * scale, 1.5)

    y = 812
    for row in (spec.get("rows") or [])[:2]:
        label = row[0] if len(row) > 0 else ""
        value = row[1] if len(row) > 1 else ""
        draw.text((PAD * scale, (y + 4) * scale), label,
                  font=_font(19, "sans", scale), fill=MUTED)
        _draw_wrapped(draw, value, (PAD + 110) * scale, y * scale,
                      _font(28, "sans", scale), "#22374F", 790 * scale, 1.45)
        y += 78
        _hline(draw, y * scale)
    _band(draw, spec, 1092, scale)
    _footer(draw, spec, scale)


LAYOUTS = {"a": _layout_a, "b": _layout_b, "c": _layout_c, "d": _layout_d}


def render_one(spec: dict, out_path: Path, scale: int = 1) -> Path:
    # 只按 1080×1440 出图：字体与坐标共用 1x，避免混用缩放导致错位
    scale = 1
    layout = str(spec.get("layout", "")).lower()
    if layout not in LAYOUTS:
        raise ValueError(f"未知封面构图：{layout!r}（只允许 a/b/c/d）")
    image = Image.new("RGB", (W * scale, H * scale), PAPER)
    draw = ImageDraw.Draw(image)
    LAYOUTS[layout](draw, spec, scale)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path, optimize=True)
    return out_path


def render_project(project: Path, scale: int = 1) -> list[Path]:
    spec_path = project / "assets" / "covers" / "cover.json"
    if not spec_path.is_file():
        raise SystemExit(f"[cover] 缺少封面配置：{spec_path}")
    data = json.loads(spec_path.read_text(encoding="utf-8"))
    written = []
    for key, spec in data.items():
        variant = str(spec.get("variant") or key).upper()
        if not str(variant).startswith("方案"):
            variant = f"方案{variant}"
        if not (project / variant).is_dir():
            continue
        out = project / variant / "封面.png"
        render_one(spec, out, scale=scale)
        written.append(out)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="按 cover.json 渲染四套封面（PIL，无需浏览器）")
    parser.add_argument("--project", required=True, help="outputs/<主题> 目录")
    parser.add_argument("--scale", type=int, default=1, help="固定 1（1080×1440）")
    args = parser.parse_args()
    project = Path(args.project)
    if not project.is_dir():
        raise SystemExit(f"[cover] 项目不存在：{project}")
    for path in render_project(project, scale=1):
        print(f"[cover] {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
