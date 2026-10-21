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
    config: { title: "运行配置", desc: "Webhook、运行模式与路径查看" },
    logs: { title: "活动日志", desc: "洗板全过程实时输出" },
  };

  const state = {
    currentView: "overview",
    logPaused: false,
    lastLogText: "",
    emptyFolders: [],
    duplicates: [],
    busy: false,
    loaded: { overview: false, hound: false, dedup: false },
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
    if (name === "logs") fetchLogs();
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
      const data = await api("/api/logs");
      const lines = data.logs || [];
      const text = lines.join("");
      if (text !== state.lastLogText) {
        state.lastLogText = text;
        renderLogs(lines);
      }
    } catch (e) {
      /* 静默重试 */
    }
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

    // 委托：点击带 data-copy 的元素即复制
    document.addEventListener("click", (e) => {
      const el = e.target.closest("[data-copy]");
      if (!el) return;
      e.stopPropagation();
      copyText(el.dataset.copy, el.classList.contains("chip") ? null : el);
    });

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        if (!$("modal").hidden) $("modal-cancel").click();
        else closeDrawer();
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
    setInterval(fetchLogs, 2000);
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
