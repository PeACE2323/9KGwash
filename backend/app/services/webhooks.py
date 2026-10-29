"""Webhook 处理器：CD2(CMS) 通关 + Emby 中转与幽灵删除防御。

``handle_cd2``      -> 对应原 /webhook：等待挂载同步 -> 分盘缓冲 -> 洗板
``handle_emby``     -> 对应原 /emby_webhook：拦截/放行删除通知并转发给 DB online
"""

from __future__ import annotations

import os
import time

import requests

from .. import config
from ..logging_setup import categorized
from ..services import fs, history, washer
from ..services.fanhao import get_weight_and_fanhao, is_jav, parse_fanhao_info

# 本模块日志统一打上分类标签，供前端「活动日志」按标签页筛选
logger = categorized("emby")


# ---------------------------------------------------------------------------
# CD2 / CMS 新增文件通知
# ---------------------------------------------------------------------------
def handle_cd2(payload) -> None:
    if not payload or "data" not in payload:
        return

    mount_base = config.mount_base_path().rstrip("/")
    watched = config.watched_dirs()

    for item in payload["data"]:
        if item.get("action") in ["create", "rename"] and str(item.get("is_dir", "false")).lower() == "false":
            file_rel_path = item.get("destination_file") or item.get("source_file", "")

            if not any(file_rel_path.startswith(d) for d in watched):
                continue

            local_file_path = mount_base + file_rel_path
            basename = os.path.basename(local_file_path)

            logger.info(f"🚀 [通关] 新增目标视频 [{basename}]，开始智能等待挂载同步...")

            waited = 0
            found = False
            while waited < 60:
                if os.path.exists(local_file_path):
                    found = True
                    break
                time.sleep(5)
                waited += 5

            if not found:
                logger.warning(f"⚠️ [失败] 等待超时，本地未刷出该文件: {local_file_path}")
                continue

            _, _, target_is_part = get_weight_and_fanhao(basename)
            if target_is_part:
                logger.info(
                    f"⏳ [分盘缓冲] 发现分盘文件 [{basename}]，延迟挂起 20 秒，等待同阵列其余分盘完全落盘..."
                )
                time.sleep(20)

            washer.process_xiban(local_file_path)


# ---------------------------------------------------------------------------
# Emby 删除通知中转
# ---------------------------------------------------------------------------
def handle_emby(payload, incoming_headers) -> tuple[str, str]:
    """返回 (判定结果, 说明)，判定结果用于 HTTP 响应体。"""
    if not payload:
        return "OK", "空负载"

    event_type = payload.get("Event", "")
    item = payload.get("Item", {}) or {}
    item_type = item.get("Type", "")
    item_path = item.get("Path", "")
    item_name = item.get("Name", "")

    if event_type == "library.deleted":
        if item_type not in ["Movie", "Episode", "Video"]:
            logger.info(f"ℹ️ [忽略] Emby 报告删除了非影视对象 [{item_type}]: {item_name}，直接无视！")
            return "Ignored Non-Media", "非影视对象"

        if item_path:
            time.sleep(2.5)
            if os.path.exists(item_path):
                logger.info(f"👻 [幽灵防御] 拦截虚假删除: {item_name}。物理路径依然存在，直接丢弃！")
                return "Phantom Deletion Ignored", "物理路径仍存在"

        basename = os.path.splitext(os.path.basename(item_path))[0] if item_path else item_name
        canonical_fanhao, raw_fanhao, _ = parse_fanhao_info(basename)

        if not is_jav(canonical_fanhao):
            logger.info(f"⚠️ [放行] 欧美/非 JAV 影片 {basename} 被删，正常放行转发。")
        else:
            hist = history.purge_expired()

            if basename in hist:
                logger.info(f"🛡️ [精准拦截] JAV 影片 {basename} 被删。查阅档案确认由洗板网关物理清理，丢弃不转发。")
                return "Intercepted", "命中洗板档案"

            search_fanhao = canonical_fanhao if config.get_ignore_leading_digits() else raw_fanhao
            logger.info(
                f"🕵️ [补充防御] 档案未记录 [{basename}]，启动【本地极速扫描】确认是否为真实物理删除 "
                f"(搜寻特征: {search_fanhao})..."
            )

            folder_still_exists = False
            for w in config.watched_dirs():
                meta_path = config.meta_path_for(w)
                if fs.fast_check_folder_exists(meta_path, search_fanhao):
                    folder_still_exists = True
                    break

            if folder_still_exists:
                logger.info(
                    f"👻 [幽灵拦截] 发现【本地】依然存活包含 [{search_fanhao}] 的物理文件夹！"
                    f"判定为刮削/转移替换产生的虚假删除，直接丢弃通知！"
                )
                return "Phantom Deletion Ignored", "本地目录仍存在"

            logger.info("✅ [核实真删] 本地关联目录确已消失。确认为真实手动删除事件，予以放行转发。")

            if config.get_ignore_leading_digits() and raw_fanhao != canonical_fanhao:
                if isinstance(payload.get("Item"), dict):
                    if isinstance(payload["Item"].get("Name"), str):
                        payload["Item"]["Name"] = payload["Item"]["Name"].replace(raw_fanhao, canonical_fanhao)
                    if isinstance(payload["Item"].get("Path"), str):
                        payload["Item"]["Path"] = payload["Item"]["Path"].replace(raw_fanhao, canonical_fanhao)
                logger.info(f"✂️ [通知降噪] 转发删除通知前，已剔除数据内数字前缀: {raw_fanhao} -> {canonical_fanhao}")

    db_webhook = config.get_db_webhook()
    if db_webhook:
        fwd_item = payload.get("Item", {}) or {}
        logger.info(
            f"📤 [中转转发] 将 Emby 事件 [{event_type}] ({fwd_item.get('Name', '')}) 转发至 DB online..."
        )
        headers = {k: v for k, v in (incoming_headers or {}).items() if k.lower() != "host"}
        try:
            resp = requests.post(db_webhook, json=payload, headers=headers, timeout=5)
            logger.info(f"   [反馈] DB online 返回状态码: {resp.status_code}")
        except Exception as e:  # noqa: BLE001
            logger.error(f"   [转发失败] {e}")

    return "Forwarded", "已转发"
