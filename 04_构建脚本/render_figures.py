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


def fig_f01():
    w, h = 1800, 1000
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    header(d, w, "F01", "实验设计", "七个退化版本 × 两种问法 × 三个模型，同图同题做对照")

    stages = [
        ("刺激", "1 张物理色卡\n7 个退化版本", 120),
        ("采集", "两种问法\n命名 / 色差 ΔE00", 520),
        ("调用", "3 个模型 × 24 次\n共 504 次调用", 920),
        ("指标", "命名准确率\n平均 ΔE00", 1320),
    ]
    for name, sub, x in stages:
        box(d, (x, 330), 340, 170, name, sub, fill=SOFT, border=BLUE)
    for i in range(3):
        arrow(d, stages[i][2] + 340, 415, stages[i + 1][2] - 10, 415)

    box(d, (120, 620), 760, 150, "基准：clean 原图", "作为对照组，衡量退化带来的偏移", fill="#FFFFFF")
    box(d, (920, 620), 760, 150, "干预：修正输入", "只改输入不改模型，看回补多少", fill="#FFFFFF")

    d.text((100, 890), "注：504 = 3 个模型 × (144 基准 + 72 干预)；一条记录一次调用，可逐条核对。",
           font=font(22), fill=MUTED)

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
