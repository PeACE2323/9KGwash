"""主题截图：深色 + 浅色各截一张，便于并排对比。"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
OUT_DIR = r"E:\workspace\9kgwash\.dev"


class WS:
    def __init__(self, url):
        import socket
        from urllib.parse import urlparse

        u = urlparse(url)
        self.sock = socket.create_connection((u.hostname, u.port or 80), timeout=30)
        import base64

        key = base64.b64encode(os.urandom(16)).decode()
        path = u.path + (("?" + u.query) if u.query else "")
        self.sock.sendall(
            (
                f"GET {path} HTTP/1.1\r\nHost: {u.hostname}\r\nUpgrade: websocket\r\n"
                f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                f"Sec-WebSocket-Version: 13\r\n\r\n"
            ).encode()
        )
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.sock.recv(4096)

    def send(self, data):
        p = data.encode()
        h = bytearray([0x81])
        n = len(p)
        if n < 126:
            h.append(0x80 | n)
        elif n < 65536:
            h.append(0x80 | 126)
            h += n.to_bytes(2, "big")
        else:
            h.append(0x80 | 127)
            h += n.to_bytes(8, "big")
        mask = os.urandom(4)
        h += mask
        self.sock.sendall(bytes(h) + bytes(b ^ mask[i % 4] for i, b in enumerate(p)))

    def recv(self):
        def rd(n):
            b = b""
            while len(b) < n:
                c = self.sock.recv(n - len(b))
                if not c:
                    raise ConnectionError
                b += c
            return b

        _, b2 = rd(2)
        n = b2 & 0x7F
        if n == 126:
            n = int.from_bytes(rd(2), "big")
        elif n == 127:
            n = int.from_bytes(rd(8), "big")
        return rd(n).decode("utf-8", "replace")


def main():
    import base64

    profile_root = os.path.join(os.path.dirname(OUT_DIR), ".shots")
    os.makedirs(profile_root, exist_ok=True)
    prof = tempfile.mkdtemp(prefix="cdp-", dir=profile_root)
    proc = subprocess.Popen(
        [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
         f"--user-data-dir={prof}", "--remote-debugging-port=9601", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        tabs = []
        for _ in range(40):
            time.sleep(0.5)
            try:
                tabs = json.loads(urllib.request.urlopen("http://127.0.0.1:9601/json").read())
                if tabs:
                    break
            except Exception:
                pass
        page = next((t for t in tabs if t.get("type") == "page"), tabs[0])
        ws = WS(page["webSocketDebuggerUrl"])
        mid = [0]

        def call(method, params=None):
            mid[0] += 1
            ws.send(json.dumps({"id": mid[0], "method": method, "params": params or {}}))
            while True:
                m = json.loads(ws.recv())
                if m.get("id") == mid[0]:
                    return m

        def ev(expr):
            r = call("Runtime.evaluate", {"expression": expr, "returnByValue": True})
            return r.get("result", {}).get("result", {}).get("value")

        call("Page.enable")
        call("Emulation.setDeviceMetricsOverride",
             {"width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False})

        # 深色：概览页
        call("Page.navigate", {"url": "http://127.0.0.1:5000/#overview"})
        time.sleep(3)
        ev("localStorage.setItem('9kgwash-theme','dark'); document.documentElement.setAttribute('data-theme','dark');")
        time.sleep(0.6)
        r = call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True})
        open(os.path.join(OUT_DIR, "theme_dark.png"), "wb").write(base64.b64decode(r["result"]["data"]))
        print("dark ok")

        # 浅色：配置页（内容最密集）
        call("Page.navigate", {"url": "http://127.0.0.1:5000/#config"})
        time.sleep(3)
        ev("localStorage.setItem('9kgwash-theme','light'); document.documentElement.setAttribute('data-theme','light');")
        time.sleep(0.8)
        r = call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True})
        open(os.path.join(OUT_DIR, "theme_light.png"), "wb").write(base64.b64decode(r["result"]["data"]))
        print("light ok")

        # 浅色：投递页（卡片最多）
        call("Page.navigate", {"url": "http://127.0.0.1:5000/#deliver"})
        time.sleep(3)
        ev("document.documentElement.setAttribute('data-theme','light');")
        time.sleep(0.6)
        r = call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True})
        open(os.path.join(OUT_DIR, "theme_light_deliver.png"), "wb").write(
            base64.b64decode(r["result"]["data"])
        )
        print("light deliver ok")
    finally:
        proc.terminate()


if __name__ == "__main__":
    main()
