"""色彩感知实验：让 VLM 判断色卡上某一块是什么颜色，再用 ΔE00 打分。

用法：
    python3 experiment.py --model deepseek-v4.1-flash --conditions clean,mild --patches 0-5
    python3 experiment.py --model deepseek-v4.1-flash --conditions all --patches all

输出：
    results/<model>_<timestamp>.jsonl   每条样本一行
    results/<model>_<timestamp>.summary.json  汇总
"""

import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

from names import NAMES, REF_RGB

ROOT = Path(__file__).parent
SESSION = f"colorcheck-exp-{uuid.uuid4().hex[:10]}"
CONDITIONS = ["clean", "mild", "strong", "noise", "dark", "jpeg", "strong_fixed"]


def srgb_to_lab(rgb):
    """sRGB(0-255) -> CIE Lab (D65)。"""
    def f(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (f(x) for x in rgb)
    x = 0.4124564 * r + 0.3575761 * g + 0.1804375 * b
    y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * b
    z = 0.0193339 * r + 0.1191920 * g + 0.9503041 * b
    x, y, z = x / 0.95047, y / 1.0, z / 1.08883

    def g(t):
        return t ** (1 / 3) if t > 0.008856 else (7.787 * t) + 16 / 116

    fx, fy, fz = g(x), g(y), g(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def de00(lab1, lab2):
    """CIEDE2000。"""
    import math

    L1, a1, b1 = lab1
    L2, a2, b2 = lab2
    C1 = math.hypot(a1, b1)
    C2 = math.hypot(a2, b2)
    Cb = (C1 + C2) / 2
    G = 0.5 * (1 - math.sqrt(Cb**7 / (Cb**7 + 25**7)))
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360 if C1p else 0
    h2p = math.degrees(math.atan2(b2, a2p)) % 360 if C2p else 0
    dLp = L2 - L1
    dCp = C2p - C1p
    if C1p * C2p == 0:
        dhp = 0
    elif abs(h2p - h1p) <= 180:
        dhp = h2p - h1p
    elif h2p - h1p > 180:
        dhp = h2p - h1p - 360
    else:
        dhp = h2p - h1p + 360
    dHp = 2 * math.sqrt(C1p * C2p) * math.sin(math.radians(dhp / 2))
    Lbp = (L1 + L2) / 2
    Cbp = (C1p + C2p) / 2
    if C1p * C2p == 0:
        hbp = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        hbp = (h1p + h2p) / 2
    elif h1p + h2p < 360:
        hbp = (h1p + h2p + 360) / 2
    else:
        hbp = (h1p + h2p - 360) / 2
    T = (
        1
        - 0.17 * math.cos(math.radians(hbp - 30))
        + 0.24 * math.cos(math.radians(2 * hbp))
        + 0.32 * math.cos(math.radians(3 * hbp + 6))
        - 0.20 * math.cos(math.radians(4 * hbp - 63))
    )
    dtheta = 30 * math.exp(-(((hbp - 275) / 25) ** 2))
    Rc = 2 * math.sqrt(Cbp**7 / (Cbp**7 + 25**7))
    Sl = 1 + (0.015 * (Lbp - 50) ** 2) / math.sqrt(20 + (Lbp - 50) ** 2)
    Sc = 1 + 0.045 * Cbp
    Sh = 1 + 0.015 * Cbp * T
    Rt = -math.sin(math.radians(2 * dtheta)) * Rc
    return math.sqrt(
        (dLp / Sl) ** 2
        + (dCp / Sc) ** 2
        + (dHp / Sh) ** 2
        + Rt * (dCp / Sc) * (dHp / Sh)
    )


def load_env():
    if os.environ.get("OPENCODE_API_KEY"):
        return
    p = ROOT / ".env"
    if not p.exists():
        sys.exit("缺少 .env")
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def ask(image_path, patch_idx, model, effort=None, retries=3):
    row, col = patch_idx // 6 + 1, patch_idx % 6 + 1
    options = "、".join(NAMES)
    prompt = (
        f"这是一张 6 列 4 行的 24 色卡。请只看第 {row} 行、第 {col} 列的色块。\n"
        f"从下列名称中选择最接近的一个：{options}\n"
        "同时给出该色块大致的十六进制颜色值。\n"
        '只输出 JSON，格式：{"name":"名称","hex":"#RRGGBB"}'
    )
    b64 = base64.b64encode(Path(image_path).read_bytes()).decode()
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                ],
            }
        ],
        "max_tokens": 2000,
        "temperature": 0,
    }
    if effort:
        payload["reasoning_effort"] = effort
    base = os.environ["OPENCODE_BASE_URL"].rstrip("/")
    key = os.environ["OPENCODE_API_KEY"]
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                f"{base}/chat/completions",
                data=json.dumps(payload).encode(),
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                    "User-Agent": "colorcheck-experiment/1.0",
                    "Accept": "application/json",
                    "x-opencode-session": SESSION,
                },
            )
            with urllib.request.urlopen(req, timeout=180) as r:
                data = json.loads(r.read().decode())
            msg = data["choices"][0]["message"]
            return msg.get("content") or "", data.get("usage", {})
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
            time.sleep(1.5 * (attempt + 1))
    return f"__ERROR__ {last}", {}


def parse_answer(text):
    m = re.search(r"\{[^{}]*\}", text, re.S)
    if m:
        try:
            obj = json.loads(m.group(0))
            return obj.get("name", ""), obj.get("hex", "")
        except json.JSONDecodeError:
            pass
    nm = next((n for n in NAMES if n in text), "")
    hm = re.search(r"#?([0-9a-fA-F]{6})", text)
    return nm, ("#" + hm.group(1) if hm else "")


def hex_to_rgb(h):
    h = h.lstrip("#")
    if len(h) != 6:
        return None
    try:
        return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--conditions", default="clean,mild")
    ap.add_argument("--patches", default="0-5", help="0-23 或 all 或 0-5")
    ap.add_argument("--tag", default="")
    ap.add_argument("--effort", default=None, help="reasoning_effort: low/medium/high")
    args = ap.parse_args()

    load_env()
    conds = CONDITIONS if args.conditions == "all" else args.conditions.split(",")
    if args.patches == "all":
        patches = list(range(24))
    elif "," in args.patches:
        patches = [int(x) for x in args.patches.split(",")]
    elif "-" not in args.patches:
        patches = [int(args.patches)]
    else:
        a, b = args.patches.split("-")
        patches = list(range(int(a), int(b) + 1))

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    tag = f"_{args.tag}" if args.tag else ""
    out = ROOT / "results" / f"{args.model}{tag}_{stamp}.jsonl"
    out.parent.mkdir(exist_ok=True)
    fh = out.open("w", encoding="utf-8")
    rows = []

    total = len(conds) * len(patches)
    done = 0
    for cond in conds:
        img = ROOT / "images" / f"v_{cond}.png"
        for idx in patches:
            done += 1
            raw, usage = ask(img, idx, args.model, args.effort)
            name, hexv = parse_answer(raw)
            ref = REF_RGB[idx]
            rgb = hex_to_rgb(hexv)
            d = de00(srgb_to_lab(rgb), srgb_to_lab(ref)) if rgb else None
            hit = name == NAMES[idx]
            row = {
                "model": args.model,
                "condition": cond,
                "patch": idx,
                "row": idx // 6 + 1,
                "col": idx % 6 + 1,
                "ref_name": NAMES[idx],
                "ref_rgb": ref,
                "answer_name": name,
                "answer_hex": hexv,
                "name_hit": hit,
                "dE00": round(d, 2) if d is not None else None,
                "raw": raw[:400],
                "usage": usage,
            }
            rows.append(row)
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
            print(
                f"[{done}/{total}] {cond:<7} #{idx:<2} 期望 {NAMES[idx]:<4} 回答 {name or '—':<4} "
                f"{'✓' if hit else '✗'}  ΔE {row['dE00']}",
                flush=True,
            )

    fh.close()

    ok = [r for r in rows if r["dE00"] is not None]
    summary = {
        "model": args.model,
        "conditions": conds,
        "n": len(rows),
        "name_accuracy": round(sum(r["name_hit"] for r in rows) / len(rows), 3) if rows else 0,
        "mean_dE00": round(sum(r["dE00"] for r in ok) / len(ok), 2) if ok else None,
        "by_condition": {},
    }
    for c in conds:
        sub = [r for r in rows if r["condition"] == c]
        sub_ok = [r for r in sub if r["dE00"] is not None]
        summary["by_condition"][c] = {
            "n": len(sub),
            "name_accuracy": round(sum(r["name_hit"] for r in sub) / len(sub), 3) if sub else 0,
            "mean_dE00": round(sum(r["dE00"] for r in sub_ok) / len(sub_ok), 2) if sub_ok else None,
        }

    (ROOT / "results" / f"{args.model}{tag}_{stamp}.summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\n" + json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
