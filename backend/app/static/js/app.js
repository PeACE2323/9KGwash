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

  /** 根据规则类型切换字段显示与提示文案 */
  function applyRuleKind(node) {
    const kind = node.querySelector(".rule-kind").value;
    const kwInput = node.querySelector(".rule-keywords");
    const kwLabel = node.querySelector(".rule-kw-label");
    const tip = node.querySelector(".rule-fallback-tip");

    node.classList.toggle("is-other", kind === "other");
    node.classList.toggle("is-fallback", kind === "fallback");

    if (kind === "western") {
      kwLabel.textContent = "厂牌列表（逗号分隔，不分大小写）";
      kwInput.placeholder = "blackedraw, tushy, vixen";
    } else if (kind === "fallback") {
      kwLabel.textContent = "关键字（兜底规则不使用，可留空）";
      kwInput.placeholder = "—";
    } else {
      kwLabel.textContent = "关键字（逗号分隔，不分大小写）";
      kwInput.placeholder = "restored, uncensored";
    }

    if (tip) tip.style.display = kind === "fallback" ? "" : "none";
  }

  /** 刷新每条规则的序号显示 */
  function refreshRuleOrder() {
    document.querySelectorAll("#rule-list .rule-item").forEach((node, idx) => {
      const badge = node.querySelector(".rule-order");
      if (badge) badge.textContent = idx + 1;
    });
  }

  /**
   * 让 textarea 高度随内容自动增长（关键字换行用）。
   * 先归零再按 scrollHeight 撑开，内容少时保持单行高度。
   */
  function autoGrow(el) {
    if (!el || el.tagName !== "TEXTAREA") return;
    el.style.height = "auto";
    // +2 补偿边框，避免出现内部滚动条
    el.style.height = Math.max(el.scrollHeight + 2, 38) + "px";
  }

  /** 批量重算所有关键字输入框高度（渲染后/窗口缩放时） */
  function autoGrowAll() {
    document.querySelectorAll("#rule-list .rule-keywords").forEach(autoGrow);
  }

  function renderRules(rules) {
    const list = $("rule-list");
    const tpl = $("rule-tpl");
    list.innerHTML = "";

    if (!rules || !rules.length) {
      list.innerHTML = '<div class="empty-hint">还没有规则，点击右上角「添加规则」</div>';
      return;
    }

    rules.forEach((rule, idx) => {
      const node = tpl.content.firstElementChild.cloneNode(true);
      node.dataset.idx = String(idx);
      node.dataset.id = rule.id || "";
      // 注意：卡片本身不可拖动，只有 ⠿ 把手可拖，
      // 否则在输入框内按住鼠标会变成拖动整条规则，无法选中文本。

      node.querySelector(".rule-name").value = rule.name || "";
      node.querySelector(".rule-kind").value = rule.kind || "jav";
      node.querySelector(".rule-action").value = rule.action || "move";
      node.querySelector(".rule-enabled-cb").checked = rule.enabled !== false;
      node.querySelector(".rule-keywords").value = (rule.keywords || []).join(", ");
      node.querySelector(".rule-mark").value = rule.mark || "";
      node.querySelector(".rule-target").value = rule.target_dir || "";

      // 关键字过多时自动换行撑高（替代单行 input）
      const kwEl = node.querySelector(".rule-keywords");
      kwEl.addEventListener("input", () => autoGrow(kwEl));

      if (rule.enabled === false) node.classList.add("is-disabled");
      applyRuleKind(node);

      node.querySelector(".rule-kind").addEventListener("change", () => {
        applyRuleKind(node);
        deliverMsg("规则类型已修改，记得保存", "busy");
      });
      node.querySelector(".rule-enabled-cb").addEventListener("change", (e) => {
        node.classList.toggle("is-disabled", !e.target.checked);
      });
      node.querySelector(".rule-del").addEventListener("click", async () => {
        const ok = await confirmDialog("删除规则", `确定删除规则「${rule.name || "未命名"}」吗？`, "删除");
        if (!ok) return;
        node.remove();
        if (!$("rule-list").children.length) {
          $("rule-list").innerHTML = '<div class="empty-hint">还没有规则，点击右上角「添加规则」</div>';
        } else {
          refreshRuleOrder();
        }
        deliverMsg("已移除规则，记得保存", "busy");
      });
      node.querySelector(".rule-browse").addEventListener("click", () => {
        browserTargetId = node.querySelector(".rule-target");
        $("browser").hidden = false;
        loadBrowser(node.querySelector(".rule-target").value || "/");
      });

      // 触屏/键盘备选：上下移动
      node.querySelector(".rule-up").addEventListener("click", () => moveRule(node, -1));
      node.querySelector(".rule-down").addEventListener("click", () => moveRule(node, 1));

      list.appendChild(node);
    });

    refreshRuleOrder();
    enableDragSort();
    // 渲染后（含从配置回填的长关键字列表）立即计算一次高度
    requestAnimationFrame(autoGrowAll);
  }

  /* ---------- 拖动排序（仅 ⠿ 把手可拖，输入框内可正常选字） ---------- */
  let dragNode = null;

  function enableDragSort() {
    const list = $("rule-list");
    if (list.dataset.dragBound === "1") return;
    list.dataset.dragBound = "1";

    list.addEventListener("dragstart", (e) => {
      // 只接受从 ⠿ 把手发起的拖动
      const grip = e.target.closest ? e.target.closest(".rule-grip") : null;
      if (!grip) {
        e.preventDefault();
        return;
      }
      const node = grip.closest(".rule-item");
      if (!node) return;

      dragNode = node;
      node.classList.add("is-dragging");
      // 拖动时让浏览器显示整条规则的半透明快照
      try {
        e.dataTransfer.effectAllowed = "move";
        e.dataTransfer.setData("text/plain", node.dataset.id || "rule");
        e.dataTransfer.setDragImage(node, 20, 20);
      } catch (_) {
        /* 某些浏览器不支持 setDragImage，忽略 */
      }
    });

    list.addEventListener("dragend", () => {
      if (dragNode) dragNode.classList.remove("is-dragging");
      dragNode = null;
      refreshRuleOrder();
      deliverMsg("顺序已调整，记得保存", "busy");
    });

    list.addEventListener("dragover", (e) => {
      if (!dragNode) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";

      const over = e.target.closest ? e.target.closest(".rule-item") : null;
      if (!over || over === dragNode) return;

      const rect = over.getBoundingClientRect();
      const after = e.clientY > rect.top + rect.height / 2;
      if (after) {
        over.after(dragNode);
      } else {
        over.before(dragNode);
      }
      refreshRuleOrder();
    });

    list.addEventListener("drop", (e) => {
      if (!dragNode) return;
      e.preventDefault();
    });
  }

  /** 上移/下移按钮（拖动的键盘/触屏备选方案） */
  function moveRule(node, dir) {
    if (!node) return;
    const sibling = dir < 0 ? node.previousElementSibling : node.nextElementSibling;
    if (!sibling) return;
    if (dir < 0) sibling.before(node);
    else sibling.after(node);
    refreshRuleOrder();
    deliverMsg("顺序已调整，记得保存", "busy");
  }

  /** 新增规则后滚动到可见位置 */
  function promiseScrollToLastRule() {
    setTimeout(() => {
      const items = document.querySelectorAll("#rule-list .rule-item");
      const last = items[items.length - 1];
      if (last && last.scrollIntoView) {
        last.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    }, 50);
  }

  function collectRules() {
    const out = [];
    document.querySelectorAll("#rule-list .rule-item").forEach((node) => {
      const keywordsRaw = node.querySelector(".rule-keywords").value || "";
      const keywords = keywordsRaw
        .replace(/，/g, ",")
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      out.push({
        id: node.dataset.id || "",
        name: node.querySelector(".rule-name").value.trim(),
        kind: node.querySelector(".rule-kind").value,
        action: node.querySelector(".rule-action").value,
        enabled: node.querySelector(".rule-enabled-cb").checked,
        keywords,
        mark: node.querySelector(".rule-mark").value.trim(),
        target_dir: node.querySelector(".rule-target").value.trim(),
      });
    });
    return out;
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
      RULES: collectRules(),
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

      renderRules(cfg.RULES || []);
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

    $("deliver-add-rule").addEventListener("click", () => {
      const rules = collectRules();
      rules.push({
        id: "",
        name: "新规则",
        kind: "jav",
        action: "move",
        enabled: true,
        keywords: [],
        mark: "-U",
        target_dir: "",
      });
      renderRules(rules);
      promiseScrollToLastRule();
      deliverMsg("已新增规则（排在最后），记得保存", "busy");
    });

    $("deliver-add-fallback").addEventListener("click", () => {
      const rules = collectRules();
      if (rules.some((r) => r.kind === "fallback")) {
        toast("已存在兜底规则，同一时间只生效第一条");
        return;
      }
      rules.push({
        id: "",
        name: "兜底投递",
        kind: "fallback",
        action: "move",
        enabled: true,
        keywords: [],
        mark: "",
        target_dir: "",
      });
      renderRules(rules);
      promiseScrollToLastRule();
      deliverMsg("已添加兜底规则，请填写目标目录后保存", "busy");
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
      // 宽度变化会改变换行位置，需重算关键字输入框高度
      if (state.currentView === "deliver") autoGrowAll();
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
