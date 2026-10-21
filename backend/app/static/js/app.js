/* =========================================================================
   9KGwash 控制台前端逻辑
   - 日志 2 秒轮询（可暂停；暂停后仅缓存，恢复时补拉）
   - 一键复制番号（Clipboard API + execCommand 双兜底）
   - 空间猎犬 / 影片查重 扫描与清理
   - 配置热更新
   ========================================================================= */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const state = {
    logPaused: false,
    lastLogText: "",
    emptyFolders: [],
    duplicates: [],
    busy: false,
  };

  /* ----------------------------- 通用 UI ----------------------------- */
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
    const data = await res.json();
    return data;
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  /* ----------------------------- 复制 ----------------------------- */
  function fallbackCopy(text, cb) {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    try {
      document.execCommand("copy");
      if (cb) cb();
    } catch (err) {
      toast("复制失败: " + text);
    }
    document.body.removeChild(ta);
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
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(done).catch(() => fallbackCopy(text, done));
    } else {
      fallbackCopy(text, done);
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
      const tsMatch = line.match(/^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (\[[A-Z]+\]) (.*)$/);
      if (tsMatch) {
        const level = tsMatch[2];
        if (level === "[ERROR]") div.classList.add("lvl-error");
        else if (level === "[WARNING]") div.classList.add("lvl-warning");
        div.innerHTML =
          '<span class="ts">' + escapeHtml(tsMatch[1]) + "</span> " + escapeHtml(tsMatch[3]);
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
      /* 网络抖动时静默重试 */
    }
  }

  function toggleLogPause() {
    state.logPaused = !state.logPaused;
    const btn = $("pause-btn");
    const ind = $("log-indicator");
    if (state.logPaused) {
      btn.textContent = "▶️ 恢复";
      ind.textContent = "已暂停";
      ind.classList.add("paused");
    } else {
      btn.textContent = "⏸️ 暂停";
      ind.textContent = "实时监控中";
      ind.classList.remove("paused");
      fetchLogs();
    }
  }

  /* ----------------------------- Tab ----------------------------- */
  function switchTab(name) {
    document.querySelectorAll(".tab").forEach((t) => {
      t.classList.toggle("active", t.dataset.tab === name);
    });
    $("panel-hound").hidden = name !== "hound";
    $("panel-dedup").hidden = name !== "dedup";
  }

  /* ----------------------------- 空间猎犬 ----------------------------- */
  function renderEmptyFolders(folders) {
    state.emptyFolders = folders.slice();
    const box = $("hound-results");
    $("hound-count").textContent = folders.length ? `共 ${folders.length} 个空壳目录` : "";
    if (!folders.length) {
      box.innerHTML =
        '<div class="empty-hint">✅ 完美！未发现任何本地无 STRM 的空壳目录。</div>';
      $("hound-actions").hidden = true;
      return;
    }
    const rows = folders
      .map(
        (f) =>
          '<div class="shell-item"><span class="path" title="' +
          escapeHtml(f) +
          '">' +
          escapeHtml(f) +
          "</span></div>"
      )
      .join("");
    box.innerHTML = rows;
    $("hound-actions").hidden = false;
  }

  async function scanEmptyFolders(silent) {
    if (state.busy) return;
    state.busy = true;
    const btn = silent ? $("hound-scan-btn") : $("btn-scan-empty");
    const label = btn.textContent;
    btn.textContent = "⏳ 扫描中...（看右侧日志）";
    btn.disabled = true;
    try {
      const data = await api("/api/scan_empty_folders");
      if (!data.success) {
        toast("扫描失败: " + (data.error || "未知错误"));
        return;
      }
      renderEmptyFolders(data.empty_meta || []);
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
      '<div class="empty-hint">暂无空壳扫描数据<br><small>点击左侧「查找本地空壳文件夹」排查本地无 STRM 的残留孤儿目录</small></div>';
    $("hound-actions").hidden = true;
    $("hound-count").textContent = "";
  }

  /* ----------------------------- 影片查重 ----------------------------- */
  function renderDuplicates(groups) {
    state.duplicates = groups.slice();
    const box = $("dedup-results");
    $("dedup-count").textContent = groups.length ? `共 ${groups.length} 组重复影片` : "";
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

  async function scanDuplicates(silent) {
    if (state.busy) return;
    state.busy = true;
    const btn = silent ? $("dedup-scan-btn") : $("btn-scan-dup");
    const label = btn.textContent;
    btn.textContent = "⏳ 扫描中...（看右侧日志）";
    btn.disabled = true;
    try {
      const data = await api("/api/scan_duplicates");
      if (!data.success) {
        toast("查重失败: " + (data.error || "未知错误"));
        return;
      }
      renderDuplicates(data.duplicates || []);
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
        loadHistory();
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
      '<div class="empty-hint">暂无查重数据<br><small>支持标准纯番号严格比对及前置数字双向归一化互查</small></div>';
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
        const pill = $("mode-pill");
        if (payload.DRY_RUN) {
          pill.textContent = "DRY RUN 模拟";
          pill.className = "pill pill-warn";
        } else {
          pill.textContent = "实弹武装";
          pill.className = "pill pill-danger";
        }
        toast("配置已热更新");
      } else {
        msg.textContent = "❌ 保存失败";
        msg.className = "status-msg err";
      }
    } catch (e) {
      msg.textContent = "❌ 网络异常";
      msg.className = "status-msg err";
    }
  }

  /* ----------------------------- 洗板档案 ----------------------------- */
  function fmtTime(ts) {
    const d = new Date(ts * 1000);
    const p = (n) => String(n).padStart(2, "0");
    return (
      d.getFullYear() +
      "-" +
      p(d.getMonth() + 1) +
      "-" +
      p(d.getDate()) +
      " " +
      p(d.getHours()) +
      ":" +
      p(d.getMinutes())
    );
  }

  async function loadHistory() {
    try {
      const data = await api("/api/history");
      $("history-count").textContent = data.count;
      const box = $("history-box");
      if (!data.items.length) {
        box.innerHTML = '<div class="empty-hint">档案为空<br><small>洗板网关清理过的特征会记录在此，用于抵御 Emby 延迟删除通知</small></div>';
        return;
      }
      box.innerHTML = data.items
        .map(
          (it) =>
            '<div class="history-item"><span class="history-key" data-copy="' +
            escapeHtml(it.key) +
            '" title="点击复制">' +
            escapeHtml(it.key) +
            '</span><span class="history-time">' +
            fmtTime(it.ts) +
            "</span></div>"
        )
        .join("");
    } catch (e) {
      $("history-box").innerHTML = '<div class="empty-hint">档案读取失败</div>';
    }
  }

  /* ----------------------------- 事件绑定 ----------------------------- */
  function bind() {
    document.querySelectorAll(".tab").forEach((t) => {
      t.addEventListener("click", () => switchTab(t.dataset.tab));
    });

    $("btn-scan-empty").addEventListener("click", () => {
      switchTab("hound");
      scanEmptyFolders(false);
    });
    $("hound-scan-btn").addEventListener("click", () => scanEmptyFolders(true));
    $("hound-del-btn").addEventListener("click", deleteEmptyFolders);
    $("hound-cancel-btn").addEventListener("click", cancelHound);

    $("btn-scan-dup").addEventListener("click", () => {
      switchTab("dedup");
      scanDuplicates(false);
    });
    $("dedup-scan-btn").addEventListener("click", () => scanDuplicates(true));
    $("dedup-wash-btn").addEventListener("click", resolveDuplicates);
    $("dedup-cancel-btn").addEventListener("click", cancelDedup);

    $("config-form").addEventListener("submit", saveConfig);
    $("pause-btn").addEventListener("click", toggleLogPause);
    $("clear-btn").addEventListener("click", () => {
      $("log-box").replaceChildren();
      state.lastLogText = "\u0000-cleared";
    });
    $("btn-refresh-history").addEventListener("click", loadHistory);

    // 事件委托：点击任何带 data-copy 的元素即复制
    document.addEventListener("click", (e) => {
      const el = e.target.closest("[data-copy]");
      if (!el) return;
      e.stopPropagation();
      copyText(el.dataset.copy, el.classList.contains("chip") ? null : el);
    });

    // Esc 关闭弹窗
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !$("modal").hidden) $("modal-cancel").click();
    });
  }

  /* ----------------------------- 启动 ----------------------------- */
  function start() {
    bind();
    fetchLogs();
    loadHistory();
    setInterval(fetchLogs, 2000);
    setInterval(loadHistory, 60000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
