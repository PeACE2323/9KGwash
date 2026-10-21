"""服务层包。

注意：这里**不要**做子模块的 eager import，避免 ``app.services.x -> app.config``
形成循环导入。各子模块由调用方按需 ``from app.services import washer`` 引入。
"""
