"""9KGwash —— 9KG 媒体库自动洗板网关（容器化版本）。"""

__version__ = "9.15.0"


def create_app():
    """延迟导入，避免 ``python -m app`` 与包 ``app`` 同名冲突。"""
    from .factory import create_app as _create_app

    return _create_app()
