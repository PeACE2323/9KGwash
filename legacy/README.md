# legacy / 历史版本留存

本目录保留容器化之前的历史实现，**仅供对照与回滚参考，不参与镜像构建**。

| 路径 | 说明 |
| --- | --- |
| `xiban_server.py` | 原版 9KGwash 网关（v9.15.0，单文件 Flask，1347 行，68914 字节） |
| `config.sample.json` | 原版配置样例（内网地址与 token 已脱敏为占位符） |
| `v0/xiban_server.py` | 仓库最初提交的早期原型（4897 字节，功能尚不完整） |
| `v0/README.md` | 早期原型对应的 README |

容器化版本在仓库根目录，功能与 `xiban_server.py` 逐行对齐，详见 [`../README.md`](../README.md)。

## 回归比对

```bash
cd backend
XIBAN_ORIG_SRC=../legacy/xiban_server.py python tests/compare_original.py
```

该脚本会加载原版并跑相同场景，逐项对照容器版的删除行为是否完全一致。

## 关于旧文件的归档方式

`v0/` 下的文件是用 `git cat-file blob <旧提交>:<路径>` 原样导出的，
与仓库最初提交的字节完全一致（非人工转抄）。
