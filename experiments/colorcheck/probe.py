"""连通性探测：先测文本，再测图像。不会打印 API Key。

用法：
    python3 probe.py                       # 只测文本
    python3 probe.py /path/to/chart.png    # 文本 + 图像
"""

import base64
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).parent
SESSION = os.environ.get("OPENCODE_SESSION") or f"colorcheck-{uuid.uuid4().hex[:12]}"

HINTS = {
    400: "请求被拒，看原文：常见是缺 x-opencode-session 或模型名不对",
    401: "Key 无效或已过期，去 Console 重新创建",
    402: "余额不足：Go 订阅要用 /inference/go/... 前缀",
    403: "被拒：1010 是 UA 被拦；JSON 错误可能是免费模型不对外",
    404: "模型名或协议不对，先查 /v1/models",
}


def load_env():
    """优先用环境变量；没有则读同目录的 .env（该文件由你创建，我不碰）。"""
    if os.environ.get("OPENCODE_API_KEY"):
        return
    p = ROOT / ".env"
    if not p.exists():
        sys.exit("未设置 OPENCODE_API_KEY，也没有 .env 文件")
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def check_config():
    base = os.environ.get("OPENCODE_BASE_URL", "").strip()
    key = os.environ.get("OPENCODE_API_KEY", "").strip()
    model = os.environ.get("OPENCODE_MODEL", "").strip()

    problems = []
    if not base:
        problems.append("OPENCODE_BASE_URL 没填")
    if "你的" in base or "your-endpoint" in base or "example" in base:
        problems.append("OPENCODE_BASE_URL 还是占位符")
    if base and not base.startswith("https://"):
        problems.append("OPENCODE_BASE_URL 必须以 https:// 开头")
    if any(ord(c) > 127 for c in base):
        problems.append("OPENCODE_BASE_URL 含非 ASCII 字符（中文占位符没替换）")
    if not key.startswith("oc_sk_"):
        problems.append("OPENCODE_API_KEY 应以 oc_sk_ 开头")
    if not model:
        problems.append("OPENCODE_MODEL 没填")

    if problems:
        print("配置有问题：")
        for p in problems:
            print("  -", p)
        print("\n当前值（不显示 Key）：")
        print("  BASE_URL =", base or "(空)")
        print("  MODEL    =", model or "(空)")
        sys.exit(1)
    return base, key, model


def post(payload):
    base = os.environ["OPENCODE_BASE_URL"].rstrip("/")
    key = os.environ["OPENCODE_API_KEY"]
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": "colorcheck-probe/1.0",
            "Accept": "application/json",
            # Go 订阅要求每个会话带稳定 ID，否则 400 MissingSessionID
            "x-opencode-session": SESSION,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:800]
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def hint(code, body):
    msg = HINTS.get(code)
    if msg:
        print("  提示:", msg)
    if isinstance(body, str) and "error" in body[:200]:
        print("  原文:", body[:200].replace("\\n", " "))


def text_of(resp):
    try:
        msg = resp["choices"][0]["message"]
        content = msg.get("content")
        if content:
            return content
        # 内容为空时，把整个响应体打出来便于诊断（不含 Key）
        return "【空回答】完整消息: " + json.dumps(msg, ensure_ascii=False)[:800]
    except Exception:
        return json.dumps(resp, ensure_ascii=False)[:800]


def main():
    load_env()
    base, key, model = check_config()
    print(f"模型: {model}")
    print(f"端点: {base}")
    print(f"会话: {SESSION}")

    print("\n[1] 文本连通性")
    code, resp = post(
        {
            "model": model,
            "messages": [{"role": "user", "content": "只回答两个字：收到"}],
            "max_tokens": 20,
        }
    )
    print("HTTP", code)
    print("回答:", text_of(resp)[:200])
    hint(code, resp)

    img = sys.argv[1] if len(sys.argv) > 1 else None
    if not img:
        print("\n未提供图片，跳过图像测试：python probe.py /path/to/chart.png")
        return

    b64 = base64.b64encode(Path(img).read_bytes()).decode()
    print(f"\n[2] 图像连通性（{Path(img).name}）")
    code, resp = post(
        {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "这张图是 6 列 4 行的色卡。只回答：第 3 行第 2 列的色块是什么颜色？",
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"},
                        },
                    ],
                }
            ],
            "max_tokens": 400,
        }
    )
    print("HTTP", code)
    print("回答:", text_of(resp)[:300])
    hint(code, resp)


if __name__ == "__main__":
    main()
