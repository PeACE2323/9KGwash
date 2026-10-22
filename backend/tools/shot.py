"""用无头 Chrome 截图，辅助调试 UI 布局。

用法：
    python tools/shot.py <url> <输出文件> [宽] [高] [--full]

示例：
    python tools/shot.py http://127.0.0.1:5000/ shot.png 1400 900
    python tools/shot.py http://127.0.0.1:5000/#logs shot_logs.png 600 1100 --full
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
]


def find_chrome() -> str:
    for p in CHROME_CANDIDATES:
        if os.path.exists(p):
            return p
    raise SystemExit("未找到 Chrome/Chromium，请安装或修改 CHROME_CANDIDATES")


def shoot(url: str, out: str, width: int = 1400, height: int = 900, full: bool = False) -> str:
    chrome = find_chrome()
    out = os.path.abspath(out)
    if os.path.exists(out):
        os.remove(out)

    # 浏览器 profile 放在「仓库外的兄弟目录」，避免污染仓库工作区。
    # 系统临时目录在部分沙箱/企业环境中不可写，故不优先使用。
    import uuid

    fallback_root = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".shots"
    )
    profile_root = None
    for cand in (os.environ.get("TEMP"), os.environ.get("TMP"), fallback_root):
        if not cand:
            continue
        try:
            os.makedirs(cand, exist_ok=True)
            probe = os.path.join(cand, ".w_" + uuid.uuid4().hex[:6])
            os.makedirs(probe, exist_ok=True)
            os.rmdir(probe)
            profile_root = cand
            break
        except OSError:
            continue
    if not profile_root:
        raise SystemExit("没有可写的临时目录用于浏览器 profile")

    profile = os.path.join(profile_root, "9kgwash-shot-" + uuid.uuid4().hex[:8])
    os.makedirs(profile, exist_ok=True)

    args = [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--hide-scrollbars",
        "--force-device-scale-factor=1",
        f"--user-data-dir={profile}",
        f"--window-size={width},{height}",
        f"--screenshot={out}",
        "--virtual-time-budget=6000",
    ]
    if full:
        # 新版 Chrome 支持整页截图
        args.insert(1, "--screenshot-format=png")
        args.append("--screenshot-full-page")
    args.append(url)

    subprocess.run(args, capture_output=True, timeout=120)
    return out


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        raise SystemExit(2)

    url = sys.argv[1]
    out = sys.argv[2]
    w = int(sys.argv[3]) if len(sys.argv) > 3 else 1400
    h = int(sys.argv[4]) if len(sys.argv) > 4 else 900
    full = "--full" in sys.argv
    p = shoot(url, out, w, h, full)
    print("saved:", p, os.path.getsize(p), "bytes")
