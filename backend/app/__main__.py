"""``python -m app`` 直接启动（等价于容器内 gunicorn 的入口）。"""

from __future__ import annotations

import os

from .factory import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
