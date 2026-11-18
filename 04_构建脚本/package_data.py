#!/usr/bin/env python3
"""把实验产物打包成站点可下载的 zip。

输出 03_数据产物/：
  results.zip  504 条原始结果 + summary
  stimuli.zip  七个退化版本的刺激图
"""
import zipfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / "experiments" / "colorcheck"
OUT = BASE / "03_数据产物"
OUT.mkdir(exist_ok=True)


def pack(name, files):
    target = OUT / name
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(f, arcname=f.name)
    print(f"写出 {target.relative_to(BASE)}  {len(files)} 个文件  {target.stat().st_size // 1024} KB")


pack("results.zip", sorted((SRC / "results").glob("*.json*")))
pack("stimuli.zip", sorted((SRC / "images").glob("*.png")))
