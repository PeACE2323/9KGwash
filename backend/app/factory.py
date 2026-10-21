"""Flask 应用工厂。"""

from __future__ import annotations

import os

from flask import Flask

from . import config
from .logging_setup import setup_logging
from .routers.deliver_api import bp as deliver_bp
from .routers.main import VERSION_LABEL, bp


def _print_banner() -> None:
    from .logging_setup import logger

    logger.info("===================================================")
    logger.info(f"🌸 9KGwash 洗板网关启动 ({VERSION_LABEL})")
    logger.info(f"📡 DB online 目标地址: {config.get_db_webhook() or '未配置'}")
    logger.info(f"📡 STRM 触发目标地址: {config.get_strm_webhook() or '未配置'}")
    logger.info(f"🛡️ Dry Run 模式: {'开启 (模拟)' if config.get_dry_run() else '关闭 (危险实弹)'}")
    ignore = config.get_ignore_leading_digits()
    logger.info(f"✂️ 忽略番号前置数字: {'开启 (跨番号通吃+去前缀转发)' if ignore else '关闭 (精确匹配)'}")

    from .services import deliver_store

    dcfg = deliver_store.current_deliver_config()
    logger.info(
        f"📦 文件投递: {'启用' if dcfg.get('ENABLED') else '未启用'} | "
        f"监控目录={dcfg.get('SCAN_DIR') or '未设置'} | 规则数={len(dcfg.get('RULES', []))}"
    )
    logger.info("🌐 WebUI 控制台地址: 见宿主机映射端口")
    logger.info("===================================================")


def _start_deliver_watcher() -> None:
    """启动后台投递监控线程（daemon，不影响进程退出）。"""
    try:
        from .services import deliver_store

        deliver_store.start_watcher()
    except Exception as e:  # noqa: BLE001
        from .logging_setup import logger

        logger.error(f"📦 投递监控线程启动失败: {e}")


def create_app() -> Flask:
    config.init_config()
    setup_logging()

    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.json.ensure_ascii = False

    app.register_blueprint(bp)
    app.register_blueprint(deliver_bp)

    if os.environ.get("XIBAN_PRINT_BANNER", "1") != "0":
        _print_banner()

    if os.environ.get("XIBAN_DISABLE_WATCHER", "0") != "1":
        _start_deliver_watcher()

    return app
