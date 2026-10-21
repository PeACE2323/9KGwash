# legacy / 原始版本留存

本目录保留容器化之前的原始单文件实现，**仅供对照与回滚参考，不参与镜像构建**。

| 文件 | 说明 |
| --- | --- |
| `xiban_server.py` | 原版 9KGwash 网关（v9.15.0，单文件 Flask，1347 行） |
| `config.sample.json` | 原版配置样例（含真实内网 Webhook 地址，已替换为占位符） |

容器化版本在仓库根目录，功能与该原版逐行对齐，详见 [`../README.md`](../README.md)。

如需直接回归比对，可运行：

```bash
cd backend
XIBAN_ORIG_SRC=../legacy/xiban_server.py python tests/compare_original.py
```
