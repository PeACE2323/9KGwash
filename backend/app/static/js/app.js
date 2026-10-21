/* =========================================================================
   9KGwash 控制台前端
   结构：左侧导航 + 右侧视图（单页切换，hash 路由可深链）
   视图：overview / hound / dedup / config / logs
   ========================================================================= */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);

  const VIEW_META = {
    overview: { title: "运行概览", desc: "当前网关状态与运行模式" },
    hound: { title: "空间猎犬", desc: "排查并清理本地无 STRM 的空壳目录" },
    dedup: { title: "影片查重", desc: "全库番号比对与一键洗板清理" },
    deliver: { title: "文件投递", desc: "关键字改名 + 按规则投递到指定目录" },
    config: { title: "运行配置", desc: "Webhook、运行模式与路径查看" },
    logs: { title: "活动日志", desc: "洗板全过程实时输出" },
  };

  const state = {
    currentView: "overview",
    logPaused: false,
    lastLogText: "",
    logCategory: "all",
    emptyFolders: [],
    duplicates: [],
    busy: false,
    loaded: { overview: false, hound: false, dedup: false, deliver: false },
    deliver: null, // 当前投递配置
  };

  /* ----------------------------- 通用 ----------------------------- */
  function toast(msg) {
    const el = $("toast");
    $("toast-msg").textContent = msg;
    el.classList.add("show");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => el.classList.remove("show"), 1700);
  }

  function confirmDialog(title, body, okText) {
    return new Promise((resolve) => {
      const modal = $("modal");
      $("modal-title").textContent = title;
      $("modal-body").textContent = body;
      $("modal-ok").textContent = okText || "确定执行";
      modal.hidden = false;

      const cleanup = (val) => {
        modal.hidden = true;
        $("modal-ok").onclick = null;
        $("modal-cancel").onclick = null;
        resolve(val);
      };
      $("modal-ok").onclick = () => cleanup(true);
      $("modal-cancel").onclick = () => cleanup(false);
    });
  }

  async function api(url, options) {
    const res = await fetch(url, options);
    return res.json();
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function copyText(text, flagEl) {
    const done = () => {
      if (flagEl) {
        const original = flagEl.innerHTML;
        flagEl.innerHTML = "✅ 已复制";
        flagEl.classList.add("copied");
        setTimeout(() => {
          flagEl.innerHTML = original;
          flagEl.classList.remove("copied");
        }, 1100);
      }
      toast("已复制: " + text);
    };
    const fallback = () => {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand("copy");
        done();
      } catch (e) {
        toast("复制失败: " + text);
      }
      document.body.removeChild(ta);
    };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(done).catch(fallback);
    } else {
      fallback();
    }
  }

  /* ----------------------------- 导航 ----------------------------- */
  function closeDrawer() {
    $("sidebar").classList.remove("open");
    const bd = document.querySelector(".nav-backdrop");
    if (bd) bd.classList.remove("show");
  }

  function openDrawer() {
    $("sidebar").classList.add("open");
    let bd = document.querySelector(".nav-backdrop");
    if (!bd) {
      bd = document.createElement("div");
      bd.className = "nav-backdrop";
      bd.addEventListener("click", closeDrawer);
      document.body.appendChild(bd);
    }
    // 强制重排后再加 show，保证过渡动画生效
    void bd.offsetWidth;
    bd.classList.add("show");
  }

  function switchView(name) {
    if (!VIEW_META[name]) name = "overview";
    state.currentView = name;

    document.querySelectorAll(".nav-item").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.view === name);
    });
    document.querySelectorAll(".view").forEach((v) => {
      v.classList.toggle("active", v.id === "view-" + name);
    });

    $("view-title").textContent = VIEW_META[name].title;
    $("view-desc").textContent = VIEW_META[name].desc;

    if (location.hash !== "#" + name) {
      history.replaceState(null, "", "#" + name);
    }

    if (window.innerWidth <= 900) closeDrawer();

    // 首次进入某视图时按需加载
    if (name === "overview") loadStatus();
    if (name === "logs") {
      fetchLogs();
      refreshLogCounts();
    }
    if (name === "deliver") loadDeliverConfig();
  }

  /* ----------------------------- 文件投递 ----------------------------- */
  function deliverMsg(text, cls) {
    const el = $("deliver-msg");
    el.textContent = text || "";
    el.className = "status-msg" + (cls ? " " + cls : "");
  }

  /* ---------- 规则组渲染 ---------- */
  const KIND_BADGE = {
    jav: ["JAV", "badge-jav"],
    western: ["欧美", "badge-western"],
    other: ["普通", "badge-other"],
    fallback: ["兜底", "badge-fallback"],
  };

  function splitKeywords(raw) {
    return (raw || "")
      .replace(/，/g, ",")
      .split(/[,\n]/)
      .map((s) => s.trim())
      .filter(Boolean);
  }

  /** 组头徽标 / 状态 / 子规则计数 随配置刷新 */
  function refreshGroupHead(node) {
    const kind = node.querySelector(".group-kind").value;
    const enabled = node.querySelector(".group-enabled-cb").checked;
    const childCount = node.querySelectorAll(".child-item").length;
    const name = node.querySelector(".group-name").value.trim();

    const [badgeText, badgeCls] = KIND_BADGE[kind] || KIND_BADGE.jav;
    const badgeEl = node.querySelector(".rule-kind-badge");
    badgeEl.textContent = badgeText;
    badgeEl.className = "rule-kind-badge " + badgeCls;

    node.querySelector(".group-count").textContent = `${childCount} 条子规则`;

    const stateEl = node.querySelector(".rule-state");
    stateEl.textContent = enabled ? "启用" : "已停用";
    stateEl.className = "rule-state" + (enabled ? " on" : " off");

    node.classList.toggle("is-disabled", !enabled);
    node.classList.toggle("is-fallback", kind === "fallback");
    node.classList.toggle("is-western", kind === "western");
    // 欧美/兜底组不提供标记，隐藏子规则里的标记控件
    node.classList.toggle("no-mark", kind === "western" || kind === "fallback");
    node.querySelector(".group-name").title = name;
  }

  /** 刷新组的序号 */
  function refreshGroupOrder() {
    document.querySelectorAll("#group-list .group-item").forEach((node, idx) => {
      const badge = node.querySelector(".group-order");
      if (badge) badge.textContent = idx + 1;
    });
  }

  /** 刷新组内子规则序号 */
  function refreshChildOrder(groupNode) {
    groupNode.querySelectorAll(".child-item").forEach((node, idx) => {
      const badge = node.querySelector(".child-order");
      if (badge) badge.textContent = idx + 1;
    });
    refreshGroupHead(groupNode);
  }

  /** 渲染单条子规则 */
  function renderChild(groupNode, child) {
    const tpl = $("child-tpl");
    const node = tpl.content.firstElementChild.cloneNode(true);
    node.dataset.id = child.id || "";
    node.querySelector(".child-keywords").value = (child.keywords || []).join(", ");
    node.querySelector(".child-mark-val").value = child.mark || "";
    node.querySelector(".child-mark-pos").value = child.mark_position || "after";
    node.querySelector(".child-enabled-cb").checked = child.enabled !== false;

    if (child.enabled === false) node.classList.add("is-disabled");

    node.querySelector(".child-up").addEventListener("click", () => moveChild(node, -1));
    node.querySelector(".child-down").addEventListener("click", () => moveChild(node, 1));
    node.querySelector(".child-del").addEventListener("click", () => {
      const list = node.parentElement;
      node.remove();
      if (list && list.querySelectorAll(".child-item").length === 0) {
        // 留一条空白子规则，避免组内无可编辑行
        renderChild(groupNode, {});
      }
      refreshChildOrder(groupNode);
      deliverMsg("子规则已移除，记得保存", "busy");
    });
    node.querySelector(".child-enabled-cb").addEventListener("change", (e) => {
      node.classList.toggle("is-disabled", !e.target.checked);
    });
    node.querySelector(".child-keywords").addEventListener("input", () => {
      deliverMsg("", "");
    });

    groupNode.querySelector(".child-list").appendChild(node);
    return node;
  }

  function moveChild(node, dir) {
    const sib = dir < 0 ? node.previousElementSibling : node.nextElementSibling;
    if (!sib) return;
    if (dir < 0) sib.before(node);
    else sib.after(node);
    refreshChildOrder(node.closest(".group-item"));
    deliverMsg("组内顺序已调整，记得保存", "busy");
  }

  /** 渲染一个规则组 */
  function renderGroup(group) {
    const tpl = $("group-tpl");
    const node = tpl.content.firstElementChild.cloneNode(true);
    node.dataset.id = group.id || "";

    node.querySelector(".group-name").value = group.name || "";
    node.querySelector(".group-kind").value = group.kind || "jav";
    node.querySelector(".group-action").value = group.action || "move";
    node.querySelector(".group-enabled-cb").checked = group.enabled !== false;
    node.querySelector(".group-target").value = group.target_dir || "";

    // 子规则
    const children = (group.children && group.children.length) ? group.children : [{}];
    children.forEach((c) => renderChild(node, c));

    // 展开状态：默认折叠；新增组自动展开
    if (group._expanded) node.classList.add("is-expanded");

    const toggleExpand = () => {
      const expanded = node.classList.toggle("is-expanded");
      group._expanded = expanded;
    };
    node.querySelector(".group-toggle").addEventListener("click", (e) => {
      e.stopPropagation();
      toggleExpand();
    });
    node.querySelector(".group-head").addEventListener("click", (e) => {
      if (e.target.closest(".group-grip, .rule-move, .group-del, .group-toggle, .group-name")) return;
      toggleExpand();
    });

    // 组头编辑联动
    const onHeadChange = () => {
      refreshGroupHead(node);
      deliverMsg("已修改，记得保存", "busy");
    };
    node.querySelector(".group-kind").addEventListener("change", onHeadChange);
    node.querySelector(".group-action").addEventListener("change", onHeadChange);
    node.querySelector(".group-enabled-cb").addEventListener("change", onHeadChange);
    node.querySelector(".group-target").addEventListener("input", onHeadChange);
    node.querySelector(".group-name").addEventListener("input", () => refreshGroupHead(node));

    node.querySelector(".group-browse").addEventListener("click", () => {
      browserTargetId = node.querySelector(".group-target");
      $("browser").hidden = false;
      loadBrowser(node.querySelector(".group-target").value || "/");
    });

    node.querySelector(".child-add").addEventListener("click", () => {
      renderChild(node, {});
      refreshChildOrder(node);
      deliverMsg("已添加子规则，记得保存", "busy");
    });

    node.querySelector(".group-up").addEventListener("click", () => moveGroup(node, -1));
    node.querySelector(".group-down").addEventListener("click", () => moveGroup(node, 1));
    node.querySelector(".group-del").addEventListener("click", async () => {
      const name = node.querySelector(".group-name").value.trim() || "未命名组";
      const ok = await confirmDialog("删除规则组", `确定删除规则组「${name}」及其全部子规则吗？`, "删除");
      if (!ok) return;
      node.remove();
      const list = $("group-list");
      if (!list.querySelector(".group-item")) {
        list.innerHTML = '<div class="empty-hint">还没有规则组，点击右上角「添加规则组」</div>';
      } else {
        refreshGroupOrder();
      }
      deliverMsg("规则组已删除，记得保存", "busy");
    });

    refreshGroupHead(node);
    refreshChildOrder(node);
    return node;
  }

  /** 渲染全部规则组 */
  function renderGroups(groups) {
    const list = $("group-list");
    list.innerHTML = "";
    if (!groups || !groups.length) {
      list.innerHTML = '<div class="empty-hint">还没有规则组，点击右上角「添加规则组」</div>';
      return;
    }
    groups.forEach((g) => list.appendChild(renderGroup(g)));
    refreshGroupOrder();
    enableGroupDrag();
    list.querySelectorAll(".group-item").forEach(refreshChildOrder);
  }

  function moveGroup(node, dir) {
    if (!node) return;
    const sib = dir < 0 ? node.previousElementSibling : node.nextElementSibling;
    if (!sib) return;
    if (dir < 0) sib.before(node);
    else sib.after(node);
    refreshGroupOrder();
    deliverMsg("组顺序已调整，记得保存", "busy");
  }

  /* ---------- 组拖动排序（仅 ⠿ 把手可拖） ---------- */
  let dragGroup = null;

  function enableGroupDrag() {
    const list = $("group-list");
    if (list.dataset.dragBound === "1") return;
    list.dataset.dragBound = "1";

    list.addEventListener("dragstart", (e) => {
      const grip = e.target.closest ? e.target.closest(".group-grip") : null;
      if (!grip) {
        e.preventDefault();
        return;
      }
      const node = grip.closest(".group-item");
      if (!node) return;
      dragGroup = node;
      node.classList.add("is-dragging");
      try {
        e.dataTransfer.effectAllowed = "move";
        e.dataTransfer.setData("text/plain", node.dataset.id || "group");
        e.dataTransfer.setDragImage(node, 20, 20);
      } catch (_) {}
    });

    list.addEventListener("dragend", () => {
      if (dragGroup) dragGroup.classList.remove("is-dragging");
      dragGroup = null;
      refreshGroupOrder();
      deliverMsg("组顺序已调整，记得保存", "busy");
    });

    list.addEventListener("dragover", (e) => {
      if (!dragGroup) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      const over = e.target.closest ? e.target.closest(".group-item") : null;
      if (!over || over === dragGroup) return;
      const rect = over.getBoundingClientRect();
      if (e.clientY > rect.top + rect.height / 2) over.after(dragGroup);
      else over.before(dragGroup);
      refreshGroupOrder();
    });

    list.addEventListener("drop", (e) => {
      if (!dragGroup) return;
      e.preventDefault();
    });
  }

  /** 收集全部规则组为配置结构 */
  function collectGroups() {
    const out = [];
    document.querySelectorAll("#group-list .group-item").forEach((node) => {
      const children = [];
      node.querySelectorAll(".child-item").forEach((c) => {
        children.push({
          id: c.dataset.id || "",
          keywords: splitKeywords(c.querySelector(".child-keywords").value),
          mark: c.querySelector(".child-mark-val").value.trim(),
          mark_position: c.querySelector(".child-mark-pos").value,
          enabled: c.querySelector(".child-enabled-cb").checked,
        });
      });
      out.push({
        id: node.dataset.id || "",
        name: node.querySelector(".group-name").value.trim(),
        kind: node.querySelector(".group-kind").value,
        action: node.querySelector(".group-action").value,
        enabled: node.querySelector(".group-enabled-cb").checked,
        target_dir: node.querySelector(".group-target").value.trim(),
        children,
        _expanded: node.classList.contains("is-expanded"),
      });
    });
    return out;
  }

  /** 新增组后滚动到可见位置 */
  function promiseScrollToLastGroup() {
    setTimeout(() => {
      const items = document.querySelectorAll("#group-list .group-item");
      const last = items[items.length - 1];
      if (last && last.scrollIntoView) {
        last.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    }, 50);
  }

  function collectDeliverConfig() {
    return {
      ENABLED: $("deliver-enabled").checked,
      SCAN_DIR: $("deliver-scan-dir").value.trim(),
      MIN_SIZE_MB: parseInt($("deliver-min-size").value, 10) || 0,
      WATCH_INTERVAL: parseInt($("deliver-interval").value, 10) || 60,
      SETTLE_SECONDS: parseInt($("deliver-settle").value, 10) || 0,
      RECURSIVE: $("deliver-recursive").checked,
      REMOVE_EMPTY_DIRS: $("deliver-remove-empty").checked,
      OVERWRITE: $("deliver-overwrite").checked,
      GROUPS: collectGroups(),
    };
  }

  async function loadDeliverConfig() {
    try {
      const data = await api("/api/deliver/config");
      if (!data.success) {
        deliverMsg("配置读取失败", "err");
        return;
      }
      const cfg = data.config || {};
      state.deliver = cfg;

      $("deliver-enabled").checked = !!cfg.ENABLED;
      $("deliver-scan-dir").value = cfg.SCAN_DIR || "";
      $("deliver-min-size").value = cfg.MIN_SIZE_MB != null ? cfg.MIN_SIZE_MB : 100;
      $("deliver-interval").value = cfg.WATCH_INTERVAL || 60;
      $("deliver-settle").value = cfg.SETTLE_SECONDS != null ? cfg.SETTLE_SECONDS : 10;
      $("deliver-recursive").checked = cfg.RECURSIVE !== false;
      $("deliver-remove-empty").checked = cfg.REMOVE_EMPTY_DIRS !== false;
      $("deliver-overwrite").checked = cfg.OVERWRITE !== false;

      renderGroups(cfg.GROUPS || []);
      updateDeliverStatus(data.watcher);
      state.loaded.deliver = true;
    } catch (e) {
      deliverMsg("配置读取异常", "err");
    }
  }

  function updateDeliverStatus(watcher) {
    const dot = $("nav-deliver-dot");
    if (dot) dot.style.display = watcher && watcher.enabled ? "" : "none";

    const el = $("deliver-status-text");
    if (!el) return;
    if (!watcher) {
      el.textContent = "";
      return;
    }
    if (!watcher.enabled) {
      el.textContent = "后台监控：已关闭";
      return;
    }
    const last = watcher.last_run ? new Date(watcher.last_run * 1000).toLocaleString() : "尚未执行";
    el.textContent = `后台监控：运行中（每 ${watcher.interval}s）· 上次执行：${last}`;
  }

  async function saveDeliverConfig(showToast) {
    deliverMsg("正在保存...", "busy");
    try {
      const res = await api("/api/deliver/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(collectDeliverConfig()),
      });
      if (res.success) {
        deliverMsg("✅ 已保存并生效", "ok");
        state.deliver = res.config;
        updateDeliverStatus((await api("/api/deliver/status")).watcher);
        if (showToast) toast("投递配置已保存");
        return true;
      }
      deliverMsg("❌ 保存失败", "err");
      return false;
    } catch (e) {
      deliverMsg("❌ 网络异常", "err");
      return false;
    }
  }

  async function runDeliver(dryRun) {
    const cfg = collectDeliverConfig();
    if (!cfg.SCAN_DIR) {
      toast("请先填写监控目录");
      return;
    }

    if (!dryRun) {
      const ok = await confirmDialog(
        "确认执行投递",
        `即将扫描并处理监控目录：\n${cfg.SCAN_DIR}\n\n` +
          `· 低于 ${cfg.MIN_SIZE_MB}MB 的文件会被删除\n` +
          `· 命中关键字的媒体文件会被改名并移动到目标目录\n\n确定执行吗？`,
        "🚀 确认执行"
      );
      if (!ok) return;
    }

    const btn = dryRun ? $("deliver-preview-btn") : $("deliver-run-btn");
    const label = btn.textContent;
    btn.disabled = true;
    btn.textContent = "⏳ 处理中...";
    deliverMsg(dryRun ? "正在预览..." : "正在执行...", "busy");

    try {
      const stats = await api("/api/deliver/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dry_run: dryRun }),
      });
      // 执行结果统一写入「活动日志 → 文件投递」，这里不再单独渲染结果面板
      if (stats.success) {
        deliverMsg(dryRun ? "✅ 预览完成" : "✅ 执行完成", "ok");
        const handled = stats.moved + stats.copied + stats.deleted;
        toast(
          dryRun
            ? `预览完成：将处理 ${handled} 个文件，详见活动日志`
            : `完成：移动 ${stats.moved} · 复制 ${stats.copied} · 删除 ${stats.deleted}，详见活动日志`
        );
        // 有实际动作时顺带刷新日志计数，便于用户点进去看
        if (handled > 0) refreshLogCounts();
      } else {
        deliverMsg("❌ " + (stats.error || "执行失败"), "err");
        toast(stats.error || "执行失败");
      }
    } catch (e) {
      deliverMsg("❌ 请求异常", "err");
      toast("请求异常，请查看日志");
    } finally {
      btn.disabled = false;
      btn.textContent = label;
    }
  }

  /* ----------------------------- 目录选择器 ----------------------------- */
  let browserTargetId = null;
  let browserCurrent = "/";

  async function loadBrowser(path) {
    const list = $("browser-list");
    list.innerHTML = '<div class="empty-hint">加载中...</div>';
    try {
      const data = await api("/api/deliver/browse?path=" + encodeURIComponent(path));
      if (!data.success) {
        list.innerHTML = `<div class="empty-hint">${escapeHtml(data.error || "无法读取")}</div>`;
        return;
      }
      browserCurrent = data.path;
      $("browser-path").textContent = data.path;

      const parts = [];
      if (data.parent) {
        parts.push('<div class="browser-entry up" data-path="' + escapeHtml(data.parent) + '">⬆️ 上一级</div>');
      }
      (data.dirs || []).forEach((d) => {
        parts.push(
          '<div class="browser-entry" data-path="' + escapeHtml(d.path) + '">📁 ' + escapeHtml(d.name) + "</div>"
        );
      });
      if (!data.dirs || !data.dirs.length) {
        parts.push('<div class="empty-hint">此目录下没有子目录</div>');
      }
      list.innerHTML = parts.join("");

      list.querySelectorAll(".browser-entry[data-path]").forEach((el) => {
        el.addEventListener("click", () => loadBrowser(el.dataset.path));
      });
    } catch (e) {
      list.innerHTML = '<div class="empty-hint">读取失败</div>';
    }
  }

  function openBrowser(initial) {
    browserTargetId = $("deliver-scan-dir");
    $("browser").hidden = false;
    loadBrowser(initial || "/");
  }

  /* ----------------------------- 概览 ----------------------------- */
  function setStat(id, text, cls) {
    const el = $(id);
    el.textContent = text;
    el.className = "stat-value" + (cls ? " " + cls : "");
  }

  async function loadStatus() {
    try {
      const st = await api("/api/status");
      if (!st.success) return;

      setStat("stat-mode", st.dry_run ? "DRY RUN" : "实弹武装", st.dry_run ? "warn" : "bad");

      const watched = st.watched || [];
      setStat("stat-watched", watched.length ? watched.length + " 个" : "未配置",
        watched.length ? "" : "bad");

      $("stat-mount-path").textContent = st.mount_base_path || "-";
      $("stat-meta-path").textContent = st.metadata_base_path || "-";

      const mountOk = watched.some((w) => w.mount_ok);
      const metaOk = watched.some((w) => w.meta_ok);
      setStat("stat-mount", mountOk ? "已挂载" : "未挂载", mountOk ? "ok" : "bad");
      setStat("stat-meta", metaOk ? "可访问" : "不可访问", metaOk ? "ok" : "bad");

      // 路径明细
      const list = $("path-list");
      if (!watched.length) {
        list.innerHTML = '<div class="empty-hint">未配置任何监听目录</div>';
      } else {
        list.innerHTML = watched
          .map(
            (w) => `
          <div class="path-item">
            <div class="path-item-head">
              <span class="path-item-title">${escapeHtml(w.watch_path)}</span>
              <span class="${w.mount_ok && w.meta_ok ? "badge-ok" : "badge-bad"}">
                ${w.mount_ok && w.meta_ok ? "正常" : "异常"}
              </span>
            </div>
            <div class="path-row"><span class="k">网盘</span><span>${escapeHtml(w.mount_path)}</span>
              <span class="${w.mount_ok ? "badge-ok" : "badge-bad"}">${w.mount_ok ? "存在" : "缺失"}</span></div>
            <div class="path-row"><span class="k">元数据</span><span>${escapeHtml(w.meta_path)}</span>
              <span class="${w.meta_ok ? "badge-ok" : "badge-bad"}">${w.meta_ok ? "存在" : "缺失"}</span></div>
          </div>`
          )
          .join("");
      }

      // 配置视图的只读路径
      $("cfg-mount").textContent = st.mount_base_path || "-";
      $("cfg-meta").textContent = st.metadata_base_path || "-";
      $("cfg-watched").textContent = watched.map((w) => w.watch_path).join(", ") || "-";

      // 模式指示
      const pill = $("mode-pill");
      pill.textContent = st.dry_run ? "DRY RUN 模拟" : "实弹武装";
      pill.className = "pill " + (st.dry_run ? "pill-warn" : "pill-danger");

      const foot = $("foot-mode");
      foot.textContent = st.dry_run ? "DRY RUN 模拟" : "实弹武装";
      foot.classList.toggle("danger", !st.dry_run);

      state.loaded.overview = true;
    } catch (e) {
      setStat("stat-mode", "读取失败", "bad");
    }
  }

  /* ----------------------------- 日志 ----------------------------- */
  function renderLogs(lines) {
    const box = $("log-box");
    const atBottom = box.scrollHeight - box.clientHeight <= box.scrollTop + 8;
    const frag = document.createDocumentFragment();
    for (const raw of lines) {
      const line = String(raw).replace(/\r?\n$/, "");
      const div = document.createElement("div");
      div.className = "log-line";
      const m = line.match(/^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (\[[A-Z]+\]) (.*)$/);
      if (m) {
        if (m[2] === "[ERROR]") div.classList.add("lvl-error");
        else if (m[2] === "[WARNING]") div.classList.add("lvl-warning");
        div.innerHTML = '<span class="ts">' + escapeHtml(m[1]) + "</span> " + escapeHtml(m[3]);
      } else {
        div.textContent = line;
      }
      frag.appendChild(div);
    }
    box.replaceChildren(frag);
    if (atBottom) box.scrollTop = box.scrollHeight;
  }

  async function fetchLogs() {
    if (state.logPaused) return;
    try {
      const cat = state.logCategory || "all";
      const data = await api(`/api/logs?category=${encodeURIComponent(cat)}&limit=200`);
      const lines = data.logs || [];
      const text = cat + "|" + lines.join("");
      if (text !== state.lastLogText) {
        state.lastLogText = text;
        renderLogs(lines);
      }
    } catch (e) {
      /* 静默重试 */
    }
  }

  /** 刷新各分类的条数徽标 */
  async function refreshLogCounts() {
    try {
      const data = await api("/api/logs/categories");
      (data.categories || []).forEach((c) => {
        const el = document.querySelector(`.log-tab-count[data-count="${c.key}"]`);
        if (el) el.textContent = c.count ? c.count : "";
      });
    } catch (e) {
      /* 忽略 */
    }
  }

  /** 切换日志分类 */
  function switchLogCategory(cat) {
    state.logCategory = cat;
    state.lastLogText = "";
    document.querySelectorAll(".log-tab").forEach((t) => {
      t.classList.toggle("active", t.dataset.cat === cat);
    });
    fetchLogs();
  }

  function toggleLogPause() {
    state.logPaused = !state.logPaused;
    const btn = $("pause-btn");
    const ind = $("log-indicator");
    const dot = $("nav-log-dot");
    if (state.logPaused) {
      btn.textContent = "▶️ 恢复";
      ind.textContent = "已暂停";
      ind.classList.add("paused");
      if (dot) dot.style.display = "none";
    } else {
      btn.textContent = "⏸️ 暂停";
      ind.textContent = "实时监控中";
      ind.classList.remove("paused");
      if (dot) dot.style.display = "";
      fetchLogs();
    }
  }

  /* ----------------------------- 空间猎犬 ----------------------------- */
  function renderEmptyFolders(folders) {
    state.emptyFolders = folders.slice();
    const box = $("hound-results");
    $("hound-count").textContent = folders.length ? `共 ${folders.length} 个` : "";
    if (!folders.length) {
      box.innerHTML = '<div class="empty-hint">✅ 完美！未发现任何本地无 STRM 的空壳目录。</div>';
      $("hound-actions").hidden = true;
      return;
    }
    box.innerHTML = folders
      .map(
        (f) =>
          '<div class="shell-item"><span class="path" title="' +
          escapeHtml(f) +
          '">' +
          escapeHtml(f) +
          "</span></div>"
      )
      .join("");
    $("hound-actions").hidden = false;
  }

  async function scanEmptyFolders() {
    if (state.busy) return;
    state.busy = true;
    const btn = $("hound-scan-btn");
    const label = btn.textContent;
    btn.textContent = "⏳ 扫描中...";
    btn.disabled = true;
    try {
      const data = await api("/api/scan_empty_folders");
      if (!data.success) {
        toast("扫描失败: " + (data.error || "未知错误"));
        return;
      }
      renderEmptyFolders(data.empty_meta || []);
      toast("扫描完成，详见活动日志");
    } catch (e) {
      toast("扫描请求失败，请检查后端状态");
    } finally {
      btn.textContent = label;
      btn.disabled = false;
      state.busy = false;
    }
  }

  async function deleteEmptyFolders() {
    if (!state.emptyFolders.length) return;
    const ok = await confirmDialog(
      "⚠️ 高风险操作",
      `即将永久物理删除列表中的 ${state.emptyFolders.length} 个本地空壳文件夹。\n操作不可逆，确定执行吗？`,
      "确认删除全部"
    );
    if (!ok) return;

    const btn = $("hound-del-btn");
    btn.disabled = true;
    btn.textContent = "🗑️ 正在清除...";
    try {
      const data = await api("/api/delete_empty_folders", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ folders: state.emptyFolders }),
      });
      if (data.success) {
        toast(`清理完成：删除 ${data.deleted} 个，跳过 ${data.failed} 个`);
        cancelHound();
      } else {
        toast("删除过程异常，请查看日志");
      }
    } catch (e) {
      toast("网络请求失败");
    } finally {
      btn.disabled = false;
      btn.textContent = "🗑️ 确认删除全部";
    }
  }

  function cancelHound() {
    state.emptyFolders = [];
    $("hound-results").innerHTML =
      '<div class="empty-hint">暂无空壳扫描数据<br><small>点击右上角「开始扫描」</small></div>';
    $("hound-actions").hidden = true;
    $("hound-count").textContent = "";
  }

  /* ----------------------------- 影片查重 ----------------------------- */
  function renderDuplicates(groups) {
    state.duplicates = groups.slice();
    const box = $("dedup-results");
    $("dedup-count").textContent = groups.length ? `共 ${groups.length} 组` : "";
    if (!groups.length) {
      box.innerHTML = '<div class="empty-hint">✅ 媒体库干干净净！未发现任何本地重复影片。</div>';
      $("dedup-actions").hidden = true;
      return;
    }
    box.innerHTML = groups
      .map((item, idx) => {
        const raws = (item.raw_fanhaos || [])
          .map(
            (rf) =>
              '<span class="chip" data-copy="' +
              escapeHtml(rf) +
              '" title="点击复制 ' +
              escapeHtml(rf) +
              '">📋 ' +
              escapeHtml(rf) +
              "</span>"
          )
          .join("");
        const files = (item.files || [])
          .map(
            (f) =>
              '<div class="dup-file ' +
              (f.is_winner ? "winner" : "loser") +
              '"><span class="name" title="' +
              escapeHtml(f.path) +
              '">' +
              (f.is_winner ? "👑 " : "🗑️ ") +
              escapeHtml(f.name) +
              '</span><span class="tag">权:' +
              f.weight +
              "</span></div>"
          )
          .join("");
        return (
          '<div class="dup-group"><div class="dup-head">' +
          '<span class="dup-title" data-copy="' +
          escapeHtml(item.canonical_fanhao) +
          '" title="点击复制主番号">#' +
          (idx + 1) +
          " 【" +
          escapeHtml(item.canonical_fanhao) +
          "】</span>" +
          '<div class="dup-raws">' +
          raws +
          "</div></div>" +
          files +
          "</div>"
        );
      })
      .join("");
    $("dedup-actions").hidden = false;
  }

  async function scanDuplicates() {
    if (state.busy) return;
    state.busy = true;
    const btn = $("dedup-scan-btn");
    const label = btn.textContent;
    btn.textContent = "⏳ 扫描中...";
    btn.disabled = true;
    try {
      const data = await api("/api/scan_duplicates");
      if (!data.success) {
        toast("查重失败: " + (data.error || "未知错误"));
        return;
      }
      renderDuplicates(data.duplicates || []);
      toast("查重完成，详见活动日志");
    } catch (e) {
      toast("查重请求异常，请检查后端状态");
    } finally {
      btn.textContent = label;
      btn.disabled = false;
      state.busy = false;
    }
  }

  async function resolveDuplicates() {
    if (!state.duplicates.length) return;
    const ok = await confirmDialog(
      "⚠️ 终极洗板警告",
      `即将清理选定的 ${state.duplicates.length} 组重复影片！\n\n` +
        "【本操作将直接删除被淘汰影片所在的整个本地文件夹以及网盘上对应的整个文件夹】\n" +
        "（若当前为 DRY RUN 模式则仅记录日志，不实施真删）\n\n确定继续执行吗？",
      "⚡ 确认洗板"
    );
    if (!ok) return;

    const btn = $("dedup-wash-btn");
    btn.disabled = true;
    btn.textContent = "🧹 执行中...";
    try {
      const data = await api("/api/resolve_duplicates", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ duplicates: state.duplicates }),
      });
      if (data.success) {
        toast(`查重洗板完成：清退 ${data.deleted} 处`);
        cancelDedup();
      } else {
        toast("执行洗板出现异常，请参阅活动日志");
      }
    } catch (e) {
      toast("网络请求失败");
    } finally {
      btn.disabled = false;
      btn.textContent = "⚡ 一键洗板清理";
    }
  }

  function cancelDedup() {
    state.duplicates = [];
    $("dedup-results").innerHTML =
      '<div class="empty-hint">暂无查重数据<br><small>点击右上角「开始查重」</small></div>';
    $("dedup-actions").hidden = true;
    $("dedup-count").textContent = "";
  }

  /* ----------------------------- 配置 ----------------------------- */
  async function saveConfig(event) {
    event.preventDefault();
    const msg = $("status-msg");
    msg.textContent = "正在保存...";
    msg.className = "status-msg busy";

    const payload = {
      DB_ONLINE_WEBHOOK: $("db_webhook").value.trim(),
      STRM_WEBHOOK: $("strm_webhook").value.trim(),
      DRY_RUN: $("dry_run").value === "true",
      IGNORE_LEADING_DIGITS: $("ignore_digits").value === "true",
    };

    try {
      const res = await api("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (res.success) {
        msg.textContent = "✅ 已保存并实时生效";
        msg.className = "status-msg ok";
        toast("配置已热更新");
        loadStatus();
      } else {
        msg.textContent = "❌ 保存失败";
        msg.className = "status-msg err";
      }
    } catch (e) {
      msg.textContent = "❌ 网络异常";
      msg.className = "status-msg err";
    }
  }

  /* ----------------------------- 事件绑定 ----------------------------- */
  function bind() {
    $("nav").addEventListener("click", (e) => {
      const item = e.target.closest(".nav-item");
      if (item) switchView(item.dataset.view);
    });

    document.querySelectorAll("[data-goto]").forEach((btn) => {
      btn.addEventListener("click", () => switchView(btn.dataset.goto));
    });

    $("menu-btn").addEventListener("click", openDrawer);

    $("hound-scan-btn").addEventListener("click", scanEmptyFolders);
    $("hound-del-btn").addEventListener("click", deleteEmptyFolders);
    $("hound-cancel-btn").addEventListener("click", cancelHound);

    $("dedup-scan-btn").addEventListener("click", scanDuplicates);
    $("dedup-wash-btn").addEventListener("click", resolveDuplicates);
    $("dedup-cancel-btn").addEventListener("click", cancelDedup);

    $("config-form").addEventListener("submit", saveConfig);
    $("pause-btn").addEventListener("click", toggleLogPause);
    $("clear-btn").addEventListener("click", () => {
      $("log-box").replaceChildren();
      state.lastLogText = "\u0000-cleared";
    });

    // 日志分类标签页
    $("log-tabs").addEventListener("click", (e) => {
      const tab = e.target.closest(".log-tab");
      if (tab) switchLogCategory(tab.dataset.cat);
    });

    /* ---------- 文件投递 ---------- */
    $("deliver-save-btn").addEventListener("click", () => saveDeliverConfig(true));
    $("deliver-preview-btn").addEventListener("click", () => runDeliver(true));
    $("deliver-run-btn").addEventListener("click", () => runDeliver(false));

    $("deliver-add-group").addEventListener("click", () => {
      const groups = collectGroups();
      groups.push({
        id: "",
        name: `规则组 ${groups.length + 1}`,
        kind: "jav",
        action: "move",
        enabled: true,
        target_dir: "",
        children: [{ keywords: [], mark: "-U", mark_position: "after", enabled: true }],
        _expanded: true, // 新增组默认展开，方便立即填写
      });
      renderGroups(groups);
      promiseScrollToLastGroup();
      deliverMsg("已新增规则组（排在最后），记得保存", "busy");
    });

    $("deliver-add-fallback").addEventListener("click", () => {
      const groups = collectGroups();
      if (groups.some((g) => g.kind === "fallback")) {
        toast("已存在兜底组，同一时间只生效第一个");
        return;
      }
      groups.push({
        id: "",
        name: "兜底投递",
        kind: "fallback",
        action: "move",
        enabled: true,
        target_dir: "",
        children: [{ keywords: [], mark: "", mark_position: "after", enabled: true }],
        _expanded: true,
      });
      renderGroups(groups);
      promiseScrollToLastGroup();
      deliverMsg("已添加兜底组，请填写目标目录后保存", "busy");
    });

    // 监控目录的浏览按钮
    document.querySelectorAll('[data-browse="deliver-scan-dir"]').forEach((btn) => {
      btn.addEventListener("click", () => {
        browserTargetId = $("deliver-scan-dir");
        $("browser").hidden = false;
        loadBrowser($("deliver-scan-dir").value || "/");
      });
    });

    $("browser-cancel").addEventListener("click", () => {
      $("browser").hidden = true;
    });
    $("browser-ok").addEventListener("click", () => {
      if (browserTargetId) browserTargetId.value = browserCurrent;
      $("browser").hidden = true;
    });

    // 委托：点击带 data-copy 的元素即复制
    document.addEventListener("click", (e) => {
      const el = e.target.closest("[data-copy]");
      if (!el) return;
      e.stopPropagation();
      copyText(el.dataset.copy, el.classList.contains("chip") ? null : el);
    });

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        if (!$("browser").hidden) {
          $("browser").hidden = true;
        } else if (!$("modal").hidden) {
          $("modal-cancel").click();
        } else {
          closeDrawer();
        }
      }
    });

    window.addEventListener("hashchange", () => {
      switchView(location.hash.replace("#", "") || "overview");
    });

    window.addEventListener("resize", () => {
      if (window.innerWidth > 900) closeDrawer();
    });
  }

  /* ----------------------------- 启动 ----------------------------- */
  function start() {
    bind();
    switchView(location.hash.replace("#", "") || "overview");
    fetchLogs();
    // 仅在日志视图可见时轮询，减少无谓请求
    setInterval(() => {
      if (state.currentView === "logs") fetchLogs();
    }, 2000);
    // 分类条数每 10 秒刷新一次
    setInterval(() => {
      if (state.currentView === "logs") refreshLogCounts();
    }, 10000);
    setInterval(() => {
      if (state.currentView === "overview") loadStatus();
    }, 15000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
