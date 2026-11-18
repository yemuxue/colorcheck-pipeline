#!/usr/bin/env python3
"""生成 colorcheck 的核心图（PIL，无第三方依赖）。

输出到 02_核心图_PNG/：
  F01 实验设计图
  C01 三模型结果矩阵
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / "02_核心图_PNG"

FONT = "/System/Library/Fonts/PingFang.ttc"
NAVY = (31, 42, 68)
INK = (23, 32, 51)
MUTED = (90, 102, 120)
LINE = (222, 229, 240)
BLUE = (47, 85, 151)
TEAL = (20, 121, 109)
AMBER = (138, 92, 31)
SOFT = (246, 249, 255)
GREEN_BG = (234, 245, 230)
AMBER_BG = (253, 243, 208)


def font(size, bold=False):
    try:
        return ImageFont.truetype(FONT, size, index=1 if bold else 0)
    except Exception:
        return ImageFont.load_default()


def header(d, w, num, title, subtitle):
    d.text((100, 64), num, font=font(26, True), fill=BLUE)
    d.text((100, 104), title, font=font(50, True), fill=NAVY)
    d.text((100, 176), subtitle, font=font(24), fill=MUTED)
    d.line([(100, 232), (w - 100, 232)], fill=LINE, width=2)


def box(d, xy, w, h, title, sub, fill="white", border=LINE, fg=INK):
    x, y = xy
    d.rounded_rectangle([x, y, x + w, y + h], radius=12, fill=fill, outline=border, width=2)
    d.text((x + 24, y + h / 2 - 34), title, font=font(26, True), fill=fg)
    if sub:
        d.text((x + 24, y + h / 2 - 2), sub, font=font(20), fill=MUTED)


def arrow(d, x1, y1, x2, y2, color=MUTED, width=3):
    d.line([(x1, y1), (x2, y2)], fill=color, width=width)
    d.polygon([(x2, y2), (x2 - 12, y2 - 7), (x2 - 12, y2 + 7)], fill=color)


STIMULI = [
    ("v_clean.png", "干净", "基准"),
    ("v_mild.png", "轻微偏色", "±8 ΔE"),
    ("v_strong.png", "强偏色", "±25 ΔE"),
    ("v_strong_fixed.png", "校正后", "同一张图"),
    ("v_noise.png", "噪声", "σ = 12"),
    ("v_dark.png", "欠曝", "−2 EV"),
    ("v_jpeg.png", "JPEG", "q = 35"),
]


def thumb(d, path, xy, size):
    im = Image.open(path).convert("RGB")
    im = im.resize(size, Image.LANCZOS)
    x, y = xy
    d.rounded_rectangle([x - 2, y - 2, x + size[0] + 2, y + size[1] + 2],
                        radius=8, fill="white", outline=LINE, width=2)
    d._image.paste(im, (x, y))
    return im


def fig_f01():
    w, h = 1800, 1160
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    d._image = img
    header(d, w, "F01", "实验设计", "一张物理色卡、七个退化版本、两种问法、三个模型")

    # 刺激行
    d.text((100, 274), "刺激", font=font(24, True), fill=NAVY)
    d.text((176, 278), "同一张色卡，只改成像条件", font=font(20), fill=MUTED)

    tw, th = 200, 136
    gap = 18
    x = 100
    for i, (fname, label, sub) in enumerate(STIMULI):
        y = 318
        thumb(d, BASE / "experiments/colorcheck/images" / fname, (x, y), (tw, th))
        cx = x + tw / 2
        d.text((cx, y + th + 22), label, font=font(20, True), fill=INK, anchor="ma")
        d.text((cx, y + th + 46), sub, font=font(16), fill=MUTED, anchor="ma")
        x += tw + gap

    # 刺激 → 问法（一分二）
    d.line([(900, 520), (900, 552)], fill=BLUE, width=3)
    d.line([(480, 552), (1240, 552)], fill=BLUE, width=3)
    for cx in (480, 1240):
        arrow(d, cx, 552, cx, 592, color=BLUE)

    d.text((100, 596), "问法", font=font(24, True), fill=NAVY)

    box(d, (100, 632), 760, 104, "命名", "「这是一块什么颜色？」→ 对 / 错", fill=SOFT, border=BLUE)
    box(d, (940, 632), 760, 104, "色差", "「它的 Lab 与标准值差多少？」→ ΔE00", fill=SOFT, border=BLUE)

    # 问法 → 模型（两张卡汇入，再一分三）
    for cx in (480, 1240):
        d.line([(cx, 736), (cx, 752)], fill=BLUE, width=3)
    d.line([(480, 752), (1240, 752)], fill=BLUE, width=3)
    d.line([(900, 752), (900, 764)], fill=BLUE, width=3)
    d.line([(350, 764), (1430, 764)], fill=BLUE, width=3)
    for cx in (350, 890, 1430):
        arrow(d, cx, 764, cx, 800, color=BLUE)

    d.text((100, 806), "模型", font=font(24, True), fill=NAVY)

    models = ["Kimi K3", "GLM-5.3-flash", "DeepSeek-v4.1-flash"]
    for i, m in enumerate(models):
        bx = 100 + i * 540
        d.rounded_rectangle([bx, 840, bx + 500, 912], radius=10, fill="white", outline=LINE, width=2)
        d.text((bx + 24, 866), m, font=font(22, True), fill=INK)

    # 模型 → 指标（三个模型汇入）
    for cx in (350, 890, 1430):
        d.line([(cx, 912), (cx, 930)], fill=BLUE, width=3)
    d.line([(350, 930), (1430, 930)], fill=BLUE, width=3)
    arrow(d, 900, 930, 900, 980, color=BLUE)

    d.text((100, 988), "指标", font=font(24, True), fill=NAVY)
    d.text((176, 992), "命名准确率 %　·　平均 ΔE00", font=font(22), fill=INK)

    d.text((100, 1074), "注：504 次调用 = 3 个模型 × (144 基准 + 72 干预)；一条记录一次调用，可逐条核对。",
           font=font(20), fill=MUTED)

    img.save(OUT / "01_F01_实验设计.png", optimize=True)
    print("写出 02_核心图_PNG/01_F01_实验设计.png", img.size)


def fig_c01():
    cols = ["模型", "干净", "强偏色", "校正后", "ΔE00（偏色 → 校正）"]
    rows = [
        ["Kimi K3", "95.8%", "62.5%", "87.5%", "10.47 → 3.42"],
        ["GLM-5.3-flash", "83.3%", "41.7%", "75.0%", "10.28 → 4.73"],
        ["DeepSeek-v4.1-flash", "70.8%", "45.8%", "62.5%", "8.71 → 2.91"],
    ]
    widths = [460, 220, 220, 220, 460]
    w = sum(widths) + 200
    rh = 96
    h = 340 + rh * (len(rows) + 1) + 150
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    header(d, w, "C01", "三模型结果矩阵", "同一组刺激下，退化与校正的对照")

    heads = [NAVY, BLUE, AMBER, TEAL, TEAL]
    x0, y0 = 100, 280
    x = x0
    for i, (c, wd) in enumerate(zip(cols, widths)):
        d.rectangle([x, y0, x + wd, y0 + rh], fill=heads[i])
        d.text((x + wd / 2, y0 + rh / 2), c, font=font(25, True), fill="white", anchor="mm")
        x += wd

    fills = ["white", GREEN_BG, AMBER_BG, GREEN_BG, SOFT]
    for r, row in enumerate(rows):
        y = y0 + rh * (r + 1)
        x = x0
        for i, cell in enumerate(row):
            if i:
                d.rectangle([x, y, x + widths[i], y + rh], fill=fills[i])
            d.text((x + 22 if i == 0 else x + widths[i] / 2, y + rh / 2), cell,
                   font=font(26, i == 0), fill=INK if i == 0 else TEAL,
                   anchor="lm" if i == 0 else "mm")
            x += widths[i]
        d.line([(x0, y + rh), (x0 + sum(widths), y + rh)], fill=LINE, width=2)

    d.text((w // 2, y0 + rh * 4 + 56),
           "强偏色平均掉 33pp；只修输入、不换模型，平均回补 25pp，ΔE 下降 62%。",
           font=font(24), fill=INK, anchor="ma")
    img.save(OUT / "02_C01_三模型结果矩阵.png", optimize=True)
    print("写出 02_核心图_PNG/02_C01_三模型结果矩阵.png", img.size)


fig_f01()
fig_c01()
