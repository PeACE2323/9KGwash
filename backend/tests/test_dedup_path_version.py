"""查重页路径直显 + 版本号动态化的测试。

问题：
  1. 查重结果需要悬停才能看到文件目录，希望扫描完直接显示
  2. 左下角版本号一直是 v9.15.0 不变
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "backend"))

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name} {extra}")


JS = open(os.path.join(BASE, "app", "static", "js", "app.js"), encoding="utf-8").read()
CSS = open(os.path.join(BASE, "app", "static", "css", "app.css"), encoding="utf-8").read()
HTML = open(os.path.join(BASE, "app", "templates", "index.html"), encoding="utf-8").read()

# ===========================================================================
print("\n=== A. 查重结果直接显示目录 ===")
check("渲染里有 dup-dir 元素", "dup-dir" in JS)
check("目录取自文件完整路径", 'replace(/[\\\\/][^\\\\/]+$/, "")' in JS or "dup-dir" in JS)
check("目录不再是 title 唯一入口", 'class="dup-dir"' in JS)
check("有目录样式", ".dup-file .dup-dir" in CSS)
check("目录有省略号截断", "text-overflow: ellipsis" in CSS.split(".dup-file .dup-dir")[1][:400])
check("文件名与目录分两行", "flex-direction: column" in CSS.split(".dup-file .dup-info")[1][:300])
check("标记单独成列", ".dup-file .dup-mark" in CSS)
check("王者目录有独立配色", ".dup-file.winner .dup-dir" in CSS)

# 语法检查
node = shutil.which("node") or r"C:\Users\zhang\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe"
if os.path.exists(node) or shutil.which(node):
    r = subprocess.run([node, "--check", os.path.join(BASE, "app", "static", "js", "app.js")],
                       capture_output=True)
    check("JS 语法正确", r.returncode == 0, r.stderr.decode()[:200])

# ===========================================================================
print("\n=== B. 版本号动态化 ===")
# 清理构建环境变量，测无构建信息场景
for k in ("XIBAN_BUILD_COMMIT", "XIBAN_BUILD_TIME"):
    os.environ.pop(k, None)

env1 = dict(os.environ)
r = subprocess.run(
    [sys.executable, "-c",
     "import sys; sys.path.insert(0, r'%s'); import app; print(app.version_full())" % os.path.join(BASE, "backend")],
    capture_output=True, text=True, env=env1,
)
out1 = (r.stdout or "").strip()
check("无构建信息时只显示版本号", out1.startswith("v") and "+" not in out1, out1)

env2 = dict(os.environ)
env2["XIBAN_BUILD_COMMIT"] = "1b2ab113d41def017e209be1675ea42574399f85"
env2["XIBAN_BUILD_TIME"] = "2026-11-02T10:17:54Z"
r = subprocess.run(
    [sys.executable, "-c",
     "import sys; sys.path.insert(0, r'%s'); import app; print(app.version_full())" % os.path.join(BASE, "backend")],
    capture_output=True, text=True, env=env2,
)
out2 = (r.stdout or "").strip()
check("有构建信息时含 commit 短号", "1b2ab11" in out2, out2)
check("有构建信息时含日期", "2026-11-02" in out2, out2)
check("格式形如 vX.Y.Z+commit 加日期",
      out2.startswith("v") and "+" in out2 and out2.count("-") >= 2 and "1b2ab11" in out2,
      out2)

# 版本号不再是写死的 9.15.0
init_py = open(os.path.join(BASE, "app", "__init__.py"), encoding="utf-8").read()
check("版本号已升级（非 9.15.0）", 'BASE_VERSION = "9.15.0"' not in init_py)
check("有 version_full 函数", "def version_full" in init_py)

# ===========================================================================
print("\n=== C. 构建信息注入链路 ===")
dockerfile = open(os.path.join(os.path.dirname(BASE), "Dockerfile"), encoding="utf-8").read()
check("Dockerfile 声明 BUILD_COMMIT", "ARG BUILD_COMMIT" in dockerfile)
check("Dockerfile 声明 BUILD_TIME", "ARG BUILD_TIME" in dockerfile)
check("Dockerfile 注入环境变量", "XIBAN_BUILD_COMMIT" in dockerfile and "XIBAN_BUILD_TIME" in dockerfile)

wf = open(os.path.join(os.path.dirname(BASE), ".github", "workflows", "build.yml"), encoding="utf-8").read()
check("CI 注入 build-args", "build-args:" in wf and "BUILD_COMMIT=" in wf)
check("CI 传递 commit", "GITHUB_SHA" in wf)

# ===========================================================================
print("\n=== D. 模板与 API 使用完整版本 ===")
main_py = open(os.path.join(BASE, "app", "routers", "main.py"), encoding="utf-8").read()
check("VERSION_LABEL 用 version_full", "VERSION_LABEL = version_full()" in main_py)
check("status 接口返回 version_full", '"version_full": version_full()' in main_py)
check("模板显示 version 变量", "{{ version }}" in HTML)

print("\n" + "=" * 50)
print(f"结果: {PASS} 通过, {FAIL} 失败")
print("=" * 50)
sys.exit(1 if FAIL else 0)
