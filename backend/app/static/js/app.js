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
    logQuery: "", // 日志搜索关键字
    logFollow: true, // 是否自动跟随最新日志
    emptyFolders: [],
    duplicates: [],
    busy: false,
    loaded: { overview: false, hound: false, dedup: false, deliver: false },
    deliver: null, // 当前投递配置
    weightDefault: [], // 权重默认顺序（用于一键恢复）
    weightCustom: [], // 自定义权重档位
    lastStatus: null, // 最近一次 /api/status 结果（路径表单与浏览起点会用到）
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

  /** POST JSON 便捷方法 */
  async function apiPost(url, payload) {
    return api(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload || {}),
    });
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
      // 视图刚从隐藏变为可见，此时容器才有真实高度，需要重新滚到底部
      state.logFollow = true;
      updateLogFollowUI();
      fetchLogs();
      refreshLogCounts();
      requestAnimationFrame(() => scrollLogToBottom(false));
      setTimeout(() => scrollLogToBottom(false), 60);
    }
    if (name === "deliver") loadDeliverConfig();
    if (name === "config") {
      // 路径配置需要 /api/status 的数据来回填，否则表单会是空的
      loadStatus();
      loadWeights();
    }
  }

  /* ----------------------------- 主题切换 ----------------------------- */
  const THEME_KEY = "9kgwash-theme";

  function currentTheme() {
    // 优先读 html 上的属性（head 内联脚本已提前设置），兜底读 localStorage
    const attr = document.documentElement.getAttribute("data-theme");
    if (attr === "light" || attr === "dark") return attr;
    try {
      const saved = localStorage.getItem(THEME_KEY);
      if (saved === "light" || saved === "dark") return saved;
    } catch (e) {}
    return "dark";
  }

  function applyTheme(theme, persist) {
    const t = theme === "light" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", t);
    const icon = $("theme-icon");
    const label = $("theme-label");
    if (icon) icon.textContent = t === "light" ? "☀️" : "🌙";
    if (label) label.textContent = t === "light" ? "浅色" : "深色";
    if (persist) {
      try {
        localStorage.setItem(THEME_KEY, t);
      } catch (e) {}
    }
  }

  function toggleTheme() {
    applyTheme(currentTheme() === "light" ? "dark" : "light", true);
    toast(currentTheme() === "light" ? "已切换到浅色主题" : "已切换到深色主题");
  }

  /* ----------------------------- 洗板权重 ----------------------------- */
  const WEIGHT_BADGE = {
    UC: ["UC", "w-badge-uc"],
    C: ["C", "w-badge-c"],
    U: ["U", "w-badge-u"],
    NORMAL: ["普通", "w-badge-normal"],
  };

  function weightMsg(text, cls) {
    const el = $("weight-msg");
    el.textContent = text || "";
    el.className = "status-msg" + (cls ? " " + cls : "");
  }

  /** 收集当前界面上的档位顺序 */
  function collectWeightOrder() {
    return [...document.querySelectorAll("#weight-list .weight-item")].map(
      (n) => n.dataset.tier
    );
  }

  /** 按顺序重排序号与权重值显示 */
  function refreshWeightRanks() {
    const items = [...document.querySelectorAll("#weight-list .weight-item")];
    const total = items.length;
    items.forEach((node, idx) => {
      node.querySelector(".weight-rank").textContent = idx + 1;
      node.querySelector(".weight-val").textContent = `权重 ${total - 1 - idx}`;
      node.classList.toggle("is-top", idx === 0);
      node.classList.toggle("is-bottom", idx === total - 1);
      node.querySelector(".weight-up").disabled = idx === 0;
      node.querySelector(".weight-down").disabled = idx === total - 1;
    });
  }

  function renderWeights(data) {
    const list = $("weight-list");
    const tpl = $("weight-tpl");
    list.innerHTML = "";

    // 记住最新的自定义档位，供保存时一起提交
    state.weightCustom = (data.custom || []).slice();

    const tiers = data.tiers || [];
    if (!tiers.length) {
      list.innerHTML = '<div class="empty-hint">没有可用的权重档位</div>';
      return;
    }

    tiers.forEach((t) => {
      const node = tpl.content.firstElementChild.cloneNode(true);
      node.dataset.tier = t.key;
      node.dataset.customId = t.custom_id || "";

      const isCustom = !!t.is_custom;
      const [badgeText, badgeCls] = isCustom
        ? ["自定义", "w-badge-custom"]
        : WEIGHT_BADGE[t.key] || [t.key, "w-badge-normal"];
      const badgeEl = node.querySelector(".weight-badge");
      badgeEl.textContent = badgeText;
      badgeEl.className = "weight-badge " + badgeCls;

      node.querySelector(".weight-label").textContent = t.label || t.key;
      node.querySelector(".weight-desc").textContent = t.desc || "";

      // 自定义档位额外展示关键字小标签
      const kwsEl = node.querySelector(".weight-kws");
      if (isCustom && t.keywords && t.keywords.length) {
        kwsEl.replaceChildren();
        t.keywords.forEach((k) => {
          const chip = document.createElement("span");
          chip.className = "kw-mini";
          chip.textContent = k;
          kwsEl.appendChild(chip);
        });
      } else {
        kwsEl.remove();
      }

      node.classList.toggle("is-custom", isCustom);

      // 编辑/删除按钮只对自定义档位显示
      const editBtn = node.querySelector(".weight-edit");
      const delBtn = node.querySelector(".weight-del");
      if (isCustom) {
        editBtn.addEventListener("click", () => openWeightModal(t.custom_id));
        delBtn.addEventListener("click", () => removeCustomTier(node));
      } else {
        editBtn.remove();
        delBtn.remove();
      }

      node.querySelector(".weight-up").addEventListener("click", () => moveWeight(node, -1));
      node.querySelector(".weight-down").addEventListener("click", () => moveWeight(node, 1));

      list.appendChild(node);
    });

    refreshWeightRanks();
    enableWeightDrag();
  }

  /* ------------------- 自定义权重档位 ------------------- */
  let weightModalState = { customId: null, kw: null };

  function openWeightModal(customId) {
    const modal = $("weight-modal");
    const custom = state.weightCustom || [];
    const editing = customId ? custom.find((c) => c.id === customId) : null;

    $("weight-modal-title").textContent = editing ? "编辑自定义档位" : "添加自定义档位";
    $("weight-name").value = editing ? editing.name || "" : "";

    const host = $("weight-keywords");
    weightModalState.customId = editing ? editing.id : null;
    weightModalState.kw = createKeywordInput(host, editing ? editing.keywords || [] : []);

    modal.hidden = false;
    setTimeout(() => $("weight-name").focus(), 50);
  }

  function closeWeightModal() {
    $("weight-modal").hidden = true;
    weightModalState = { customId: null, kw: null };
  }

  function confirmWeightModal() {
    const name = ($("weight-name").value || "").trim();
    const keywords = weightModalState.kw ? weightModalState.kw.get() : [];

    if (!name) {
      toast("请填写档位名称");
      return;
    }
    if (!keywords.length) {
      toast("请至少添加一个关键字");
      return;
    }

    const custom = (state.weightCustom || []).slice();
    if (weightModalState.customId) {
      // 编辑已有档位
      const idx = custom.findIndex((c) => c.id === weightModalState.customId);
      if (idx >= 0) {
        custom[idx] = { ...custom[idx], name, keywords };
      }
      weightMsg("档位已修改，记得保存", "busy");
    } else {
      // 新增档位，id 由后端最终生成；这里先给个临时 id
      const tmpId = "tmp" + Date.now().toString(36);
      custom.push({ id: tmpId, name, keywords });
      weightMsg("已添加档位，记得保存", "busy");
    }

    state.weightCustom = custom;
    closeWeightModal();
    rerenderWeightsWithCustom();
  }

  /** 用本地 state.weightCustom 重绘（无需重新请求后端） */
  function rerenderWeightsWithCustom() {
    const order = collectWeightOrder();
    // 保持已有顺序，新档位追加到末尾
    const custom = state.weightCustom || [];
    const known = new Set(custom.map((c) => "custom:" + c.id));
    const newOrder = order.filter((t) => !t.startsWith("custom:") || known.has(t));
    custom.forEach((c) => {
      const key = "custom:" + c.id;
      if (!newOrder.includes(key)) newOrder.push(key);
    });

    // 构造一份与后端 GET 返回同构的数据
    const total = newOrder.length;
    const tiers = newOrder.map((key, idx) => {
      if (key.startsWith("custom:")) {
        const id = key.slice("custom:".length);
        const c = custom.find((x) => x.id === id) || { name: "自定义", keywords: [] };
        return {
          key,
          label: c.name,
          desc: "命中关键字：" + (c.keywords || []).join("、"),
          weight: total - 1 - idx,
          is_custom: true,
          custom_id: id,
          keywords: c.keywords || [],
        };
      }
      const isBuiltin = WEIGHT_BADGE[key];
      return {
        key,
        label: isBuiltin ? key : key,
        desc: "",
        weight: total - 1 - idx,
        is_custom: false,
      };
    });

    // 内置档位的中文说明从 DOM 已有内容里继承，避免丢文案
    const prevDesc = {};
    document.querySelectorAll("#weight-list .weight-item").forEach((n) => {
      if (!n.dataset.customId) {
        prevDesc[n.dataset.tier] = {
          label: n.querySelector(".weight-label").textContent,
          desc: n.querySelector(".weight-desc").textContent,
        };
      }
    });
    tiers.forEach((t) => {
      if (!t.is_custom && prevDesc[t.key]) {
        t.label = prevDesc[t.key].label;
        t.desc = prevDesc[t.key].desc;
      }
    });

    renderWeights({ tiers, custom });
  }

  function removeCustomTier(node) {
    const id = node.dataset.customId;
    if (!id) return;
    const c = (state.weightCustom || []).find((x) => x.id === id);
    if (!confirm(`确定删除自定义档位「${c ? c.name : id}」吗？`)) return;
    state.weightCustom = (state.weightCustom || []).filter((x) => x.id !== id);
    node.remove();
    refreshWeightRanks();
    weightMsg("档位已删除，记得保存", "busy");
  }

  function moveWeight(node, dir) {
    const sib = dir < 0 ? node.previousElementSibling : node.nextElementSibling;
    if (!sib) return;
    if (dir < 0) sib.before(node);
    else sib.after(node);
    refreshWeightRanks();
    weightMsg("顺序已调整，记得保存", "busy");
  }

  let dragWeight = null;

  function enableWeightDrag() {
    const list = $("weight-list");
    if (list.dataset.dragBound === "1") return;
    list.dataset.dragBound = "1";

    list.addEventListener("dragstart", (e) => {
      const item = e.target.closest ? e.target.closest(".weight-item") : null;
      if (!item) return;
      dragWeight = item;
      item.classList.add("is-dragging");
      try {
        e.dataTransfer.effectAllowed = "move";
        e.dataTransfer.setData("text/plain", item.dataset.tier || "tier");
        e.dataTransfer.setDragImage(item, 30, 20);
      } catch (_) {}
    });

    list.addEventListener("dragend", () => {
      if (dragWeight) dragWeight.classList.remove("is-dragging");
      dragWeight = null;
      refreshWeightRanks();
      weightMsg("顺序已调整，记得保存", "busy");
    });

    list.addEventListener("dragover", (e) => {
      if (!dragWeight) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      const over = e.target.closest ? e.target.closest(".weight-item") : null;
      if (!over || over === dragWeight) return;
      const rect = over.getBoundingClientRect();
      if (e.clientY > rect.top + rect.height / 2) over.after(dragWeight);
      else over.before(dragWeight);
      refreshWeightRanks();
    });

    list.addEventListener("drop", (e) => {
      if (dragWeight) e.preventDefault();
    });
  }

  async function loadWeights() {
    try {
      const data = await api("/api/weight");
      if (!data.success) {
        weightMsg("读取失败", "err");
        return;
      }
      state.weightDefault = data.default_order || [];
      renderWeights(data);
      weightMsg("");
    } catch (e) {
      weightMsg("读取异常", "err");
    }
  }

  async function saveWeights() {
    const order = collectWeightOrder();
    if (!order.length) return;
    weightMsg("正在保存...", "busy");
    try {
      const res = await api("/api/weight", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          order,
          // 自定义档位一并提交（临时 id 由后端重新分配）
          custom: (state.weightCustom || []).map((c) => ({
            id: c.id,
            name: c.name,
            keywords: c.keywords || [],
          })),
        }),
      });
      if (res.success) {
        weightMsg("✅ 已保存并实时生效", "ok");
        const names = res.order.map((k) => {
          if (k.startsWith("custom:")) {
            const id = k.slice("custom:".length);
            const c = (res.custom || []).find((x) => x.id === id);
            return c ? c.name : "自定义";
          }
          return k;
        });
        toast("权重顺序已更新：" + names.join(" > "));
        loadWeights();
      } else {
        weightMsg("❌ " + (res.error || "保存失败"), "err");
      }
    } catch (e) {
      weightMsg("❌ 网络异常", "err");
    }
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
      .replace(/、/g, ",")
      .split(/[,\n]/)
      .map((s) => s.trim())
      .filter(Boolean);
  }

  /* ------------------------- 关键字标签输入组件 ------------------------- */
  /**
   * 把任意「关键字来源」规范成去重后的标签数组。
   * 兼容：字符串（英文/中文逗号、顿号、换行）、数组。
   * 旧数据（逗号分隔字符串）会自动拆分，实现无缝迁移。
   */
  function normalizeKeywords(raw) {
    let list = [];
    if (Array.isArray(raw)) {
      raw.forEach((item) => {
        list = list.concat(splitKeywords(String(item)));
      });
    } else {
      list = splitKeywords(String(raw || ""));
    }
    const seen = new Set();
    const out = [];
    list.forEach((k) => {
      const v = k.trim();
      if (!v) return;
      const key = v.toLowerCase();
      if (seen.has(key)) return; // 不分大小写去重
      seen.add(key);
      out.push(v);
    });
    return out;
  }

  /**
   * 初始化一个关键字标签输入框。
   * @param {HTMLElement} host 容器（.kw-input）
   * @param {Array|string} value 初始值
   * @returns {{get:Function, set:Function, onChange:Function}}
   */
  function createKeywordInput(host, value) {
    let tags = normalizeKeywords(value);

    const input = document.createElement("input");
    input.type = "text";
    input.className = "kw-field";
    input.autocomplete = "off";
    input.spellcheck = false;

    const placeholder = host.dataset.placeholder || "输入关键字后回车添加";

    function notify() {
      if (typeof host._onChange === "function") host._onChange(tags.slice());
    }

    function refreshPlaceholder() {
      // 有标签时输入框变窄，placeholder 只在没有标签时显示
      input.placeholder = tags.length ? "" : placeholder;
    }

    function chip(text) {
      const el = document.createElement("span");
      el.className = "kw-chip";
      const label = document.createElement("span");
      label.className = "kw-chip-text";
      label.textContent = text;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "kw-chip-del";
      btn.title = "移除 " + text;
      btn.textContent = "×";
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        tags = tags.filter((t) => t !== text);
        render();
        notify();
      });
      el.appendChild(label);
      el.appendChild(btn);
      return el;
    }

    function render() {
      host.replaceChildren();
      tags.forEach((t) => host.appendChild(chip(t)));
      host.appendChild(input);
      refreshPlaceholder();
    }

    function add(raw) {
      const items = normalizeKeywords(raw);
      if (!items.length) return false;
      let changed = false;
      items.forEach((k) => {
        const exists = tags.some((t) => t.toLowerCase() === k.toLowerCase());
        if (!exists) {
          tags.push(k);
          changed = true;
        }
      });
      if (changed) {
        render();
        notify();
      }
      return changed;
    }

    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        // 回车：把当前输入变成标签
        if (add(input.value)) input.value = "";
        else input.value = "";
        return;
      }
      if (e.key === "," || e.key === "，" || e.key === "、") {
        // 输入逗号/顿号直接确认当前关键字，不写入框内
        e.preventDefault();
        if (add(input.value)) input.value = "";
        return;
      }
      if (e.key === "Backspace" && !input.value && tags.length) {
        // 退格删除最后一个标签
        tags.pop();
        render();
        notify();
        input.focus();
      }
    });

    // 粘贴：自动按逗号/换行拆分成多个标签
    input.addEventListener("paste", (e) => {
      const text = (e.clipboardData || window.clipboardData).getData("text");
      if (!text) return;
      if (/[,，、\n]/.test(text)) {
        e.preventDefault();
        add(text);
        input.value = "";
      }
    });

    // 失焦时把残留输入也转成标签，避免用户忘了回车
    input.addEventListener("blur", () => {
      if (input.value.trim()) {
        add(input.value);
        input.value = "";
      }
    });

    // 点击容器空白处聚焦到输入框
    host.addEventListener("click", (e) => {
      if (e.target === host) input.focus();
    });

    render();

    return {
      get: () => tags.slice(),
      set: (v) => {
        tags = normalizeKeywords(v);
        render();
      },
      onChange: (fn) => {
        host._onChange = fn;
      },
      input,
    };
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

    // 折叠态的关键字预览：显示前若干个标签，超出折叠为 +N
    refreshGroupKeywordPreview(node, kind);
  }

  /** 汇总组内全部关键字，折叠态用小标签展示 */
  function refreshGroupKeywordPreview(node, kind) {
    let host = node.querySelector(".group-kw-preview");
    if (!host) {
      host = document.createElement("span");
      host.className = "group-kw-preview";
      node.querySelector(".group-head").insertBefore(
        host,
        node.querySelector(".group-head .rule-spacer")
      );
    }
    host.replaceChildren();

    if (kind === "fallback") {
      const t = document.createElement("span");
      t.className = "kw-mini kw-mini-dim";
      t.textContent = "所有未命中的视频";
      host.appendChild(t);
      host.title = "";
      return;
    }

    if (kind === "western") {
      // 欧美组的“关键字”其实是厂牌，标签样式区分一下
      host.classList.add("is-studio");
    } else {
      host.classList.remove("is-studio");
    }

    const all = [];
    node.querySelectorAll(".child-item").forEach((c) => {
      if (!c._kw) return;
      c._kw.get().forEach((k) => {
        if (!all.some((x) => x.toLowerCase() === k.toLowerCase())) all.push(k);
      });
    });

    if (!all.length) {
      const t = document.createElement("span");
      t.className = "kw-mini kw-mini-dim";
      t.textContent = "未设置关键字";
      host.appendChild(t);
      host.title = "";
      return;
    }

    const LIMIT = 3;
    const shown = all.slice(0, LIMIT);
    shown.forEach((k) => {
      const t = document.createElement("span");
      t.className = "kw-mini";
      t.textContent = k;
      host.appendChild(t);
    });
    if (all.length > LIMIT) {
      const more = document.createElement("span");
      more.className = "kw-mini kw-mini-more";
      more.textContent = `+${all.length - LIMIT}`;
      host.appendChild(more);
    }
    host.title = all.join(", ");
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

    const kwHost = node.querySelector(".child-keywords");
    // 关键字用标签输入；旧数据（逗号分隔字符串）由 normalizeKeywords 自动拆分
    const kw = createKeywordInput(kwHost, child.keywords || []);
    node._kw = kw;
    kw.onChange(() => {
      refreshGroupHead(node.closest(".group-item"));
      deliverMsg("", "");
    });

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
          keywords: c._kw ? c._kw.get() : [],
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
  let browserRoots = [];

  /** 路径配置表单：只包含「存储路径」两项（网盘根 / 元数据根） */
  const pathForm = { loaded: false, dirty: false };

  /** 把 /api/status 的数据回填到路径表单（用户正在编辑时不覆盖） */
  function fillPathForm(st) {
    const mountEl = $("cfg-mount");
    const metaEl = $("cfg-meta");
    if (!mountEl || !metaEl) return;

    if (!pathForm.loaded || !pathForm.dirty) {
      mountEl.value = st.mount_base_path || "";
      metaEl.value = st.metadata_base_path || "";
      pathForm.loaded = true;
      updatePathHints(st);
    }
    state.lastStatus = st;
  }

  /** 显示路径的实际解析结果，帮助用户确认映射是否正确 */
  function updatePathHints(st) {
    const s = st || state.lastStatus;
    if (!s) return;
    const watching = s.watched || [];
    const mountHint = $("cfg-mount-hint");
    const metaHint = $("cfg-meta-hint");

    const mountOk = !!s.mount_base_path && watching.some((w) => w.mount_ok);
    const metaOk = !!s.metadata_base_path && watching.some((w) => w.meta_ok);
    const hasWatch = watching.length > 0;

    if (mountHint) {
      if (!s.mount_base_path) {
        mountHint.textContent = "";
      } else if (!hasWatch) {
        mountHint.textContent = "尚未配置监控目录，无法校验";
      } else if (mountOk) {
        mountHint.textContent = `✅ 容器内可访问，示例：${watching[0].mount_path}`;
      } else {
        mountHint.textContent = `⚠️ 容器内找不到该目录，请检查 Docker 映射：${s.mount_base_path}`;
      }
      mountHint.className = "field-hint" + (hasWatch && !mountOk ? " bad" : "");
    }

    if (metaHint) {
      if (!s.metadata_base_path) {
        metaHint.textContent = "";
      } else if (!hasWatch) {
        metaHint.textContent = "尚未配置监控目录，无法校验";
      } else if (metaOk) {
        metaHint.textContent = `✅ 容器内可访问，示例：${watching[0].meta_path}`;
      } else {
        metaHint.textContent = `⚠️ 容器内找不到该目录，请检查 Docker 映射：${s.metadata_base_path}`;
      }
      metaHint.className = "field-hint" + (hasWatch && !metaOk ? " bad" : "");
    }

    // 投递页的监控目录提示
    updateScanDirHint();
  }

  /** 投递页：校验监控目录是否存在，并给出可读提示 */
  function updateScanDirHint() {
    const hint = $("deliver-scan-dir-hint");
    const input = $("deliver-scan-dir");
    if (!hint || !input) return;
    const v = (input.value || "").trim();
    if (!v) {
      hint.textContent = "";
      hint.className = "field-hint";
      return;
    }
    // 前端只能做基本判断，真实存在性靠后端 /api/path_check
    checkScanDir(v);
  }

  let scanDirTimer = null;
  function checkScanDir(path) {
    const hint = $("deliver-scan-dir-hint");
    clearTimeout(scanDirTimer);
    scanDirTimer = setTimeout(async () => {
      try {
        const data = await apiPost("/api/path_check", {
          MOUNT_BASE_PATH: (state.lastStatus && state.lastStatus.mount_base_path) || undefined,
          METADATA_BASE_PATH: (state.lastStatus && state.lastStatus.metadata_base_path) || undefined,
          WATCHED_DIRS: [path],
        });
        if (!hint) return;
        const r = (data.resolved || [])[0];
        if (r && r.mount_ok) {
          hint.textContent = `✅ 容器内可访问：${r.mount_path}`;
          hint.className = "field-hint";
        } else if (r) {
          hint.textContent = `⚠️ 容器内找不到该目录：${r.mount_path}`;
          hint.className = "field-hint bad";
        } else if (data.errors && data.errors.length) {
          hint.textContent = "⚠️ " + data.errors[0];
          hint.className = "field-hint bad";
        } else {
          hint.textContent = "";
          hint.className = "field-hint";
        }
      } catch (e) {
        /* 静默 */
      }
    }, 350);
  }

  /** 收集路径表单（只有两项） */
  function collectPathForm() {
    return {
      MOUNT_BASE_PATH: ($("cfg-mount").value || "").trim(),
      METADATA_BASE_PATH: ($("cfg-meta").value || "").trim(),
    };
  }

  async function savePaths() {
    const payload = collectPathForm();
    if (!payload.MOUNT_BASE_PATH || !payload.METADATA_BASE_PATH) {
      pathMsg("网盘根目录与元数据根目录都不能为空", "bad");
      return;
    }
    // 先做一次后端校验，路径明显有问题时不让保存
    try {
      const chk = await apiPost("/api/path_check", payload);
      if (chk.errors && chk.errors.length) {
        pathMsg(chk.errors[0], "bad");
        return;
      }
    } catch (e) {
      /* 校验失败不阻塞保存 */
    }
    const btn = $("path-save");
    btn.disabled = true;
    try {
      const data = await apiPost("/api/config", payload);
      if (data.success) {
        pathForm.dirty = false;
        pathMsg("已保存并生效", "ok");
        toast("存储路径已更新，立即生效");
        loadStatus();
      } else {
        pathMsg(data.error || "保存失败", "bad");
      }
    } catch (e) {
      pathMsg("保存失败：" + e.message, "bad");
    } finally {
      btn.disabled = false;
    }
  }

  function pathMsg(text, kind) {
    const el = $("path-msg");
    if (!el) return;
    el.textContent = text || "";
    el.className = "status-msg" + (kind ? " " + kind : "");
    if (text) {
      clearTimeout(pathMsg._t);
      pathMsg._t = setTimeout(() => {
        el.textContent = "";
      }, 4000);
    }
  }

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
      if (data.roots) browserRoots = data.roots;
      $("browser-path").textContent = data.path;

      // 快捷跳转：网盘根 / 元数据根，省去从 / 逐层点进去
      const quick = $("browser-quick");
      if (quick) {
        const cur = (data.path || "").replace(/\/+$/, "");
        quick.innerHTML = browserRoots
          .filter((r) => r && r !== "/")
          .map((r) => {
            const on = cur === r || cur.startsWith(r.replace(/\/+$/, "") + "/");
            return `<button type="button" class="browser-quick-btn${on ? " on" : ""}" data-path="${escapeHtml(
              r
            )}">${escapeHtml(r)}</button>`;
          })
          .join("");
      }

      const parts = [];
      if (data.parent) {
        parts.push('<div class="browser-entry up" data-path="' + escapeHtml(data.parent) + '">⬆️ 上一级</div>');
      }
      if (!(data.dirs || []).length) {
        parts.push('<div class="empty-hint">该目录下没有子文件夹</div>');
      }
      (data.dirs || []).forEach((d) => {
        parts.push(
          '<div class="browser-entry" data-path="' + escapeHtml(d.path) + '">📁 ' + escapeHtml(d.name) + "</div>"
        );
      });
      list.innerHTML = parts.join("");

      list.querySelectorAll(".browser-entry[data-path]").forEach((el) => {
        el.addEventListener("click", () => loadBrowser(el.dataset.path));
      });
    } catch (e) {
      list.innerHTML = '<div class="empty-hint">读取失败</div>';
    }
  }

  function openBrowser(initial) {
    if (arguments.length === 0 || initial === undefined) {
      browserTargetId = $("deliver-scan-dir");
    }
    $("browser").hidden = false;
    // 默认打开网盘根目录，而不是从 / 开始逐层点击
    const start = initial || (state.lastStatus && state.lastStatus.mount_base_path) || "/";
    loadBrowser(start);
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

      // 配置视图的路径表单：只在用户没改动时回填，避免轮询覆盖正在编辑的内容
      fillPathForm(st);

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
  /** 判断内容变化时要不要自动滚到底部。
   *  规则：用户没手动往回翻（logFollow=true）就自动跟随；搜索时总是滚到首条结果。 */
  function shouldFollowLog() {
    return state.logFollow !== false;
  }

  /** 滚到底部 */
  function scrollLogToBottom(smooth) {
    const box = $("log-box");
    if (!box) return;
    if (smooth && box.scrollTo) {
      box.scrollTo({ top: box.scrollHeight, behavior: "smooth" });
    } else {
      box.scrollTop = box.scrollHeight;
    }
  }

  /** 高亮搜索关键字 */
  function highlightLog(text, q) {
    const safe = escapeHtml(text);
    if (!q) return safe;
    const needle = q.toLowerCase();
    const low = safe.toLowerCase();
    let out = "";
    let i = 0;
    while (true) {
      const idx = low.indexOf(needle, i);
      if (idx < 0) {
        out += safe.slice(i);
        break;
      }
      out += safe.slice(i, idx) + '<mark class="log-hit">' + safe.slice(idx, idx + q.length) + "</mark>";
      i = idx + q.length;
    }
    return out;
  }

  function renderLogs(lines) {
    const box = $("log-box");
    const q = (state.logQuery || "").trim();
    const follow = shouldFollowLog();
    const frag = document.createDocumentFragment();

    for (const raw of lines) {
      const line = String(raw).replace(/\r?\n$/, "");
      const div = document.createElement("div");
      div.className = "log-line";
      const m = line.match(/^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (\[[A-Z]+\]) (.*)$/);
      if (m) {
        if (m[2] === "[ERROR]") div.classList.add("lvl-error");
        else if (m[2] === "[WARNING]") div.classList.add("lvl-warning");
        div.innerHTML =
          '<span class="ts">' + escapeHtml(m[1]) + "</span> " + highlightLog(m[3], q);
      } else {
        div.innerHTML = highlightLog(line, q);
      }
      frag.appendChild(div);
    }
    box.replaceChildren(frag);

    // 渲染后立即滚动：跟随模式滚到底；搜索模式滚到第一条命中
    if (follow) {
      scrollLogToBottom(false);
      // 布局完成后补一次，兼容图片/字体导致的二次回流
      requestAnimationFrame(() => scrollLogToBottom(false));
    }
  }

  function updateLogFollowUI() {
    const btn = $("follow-btn");
    if (!btn) return;
    const on = shouldFollowLog();
    btn.textContent = on ? "⬇️ 自动跟随" : "⏸️ 已锁定";
    btn.classList.toggle("is-on", on);
    btn.title = on ? "新日志会自动滚动到底部（点击锁定）" : "已锁定当前位置（点击恢复自动跟随）";
  }

  function setLogFollow(on, notify) {
    state.logFollow = !!on;
    updateLogFollowUI();
    if (on) {
      scrollLogToBottom(true);
      if (notify) toast("已恢复自动跟随");
    } else if (notify) {
      toast("已锁定日志位置");
    }
  }

  async function fetchLogs() {
    if (state.logPaused) return;
    try {
      const cat = state.logCategory || "all";
      const q = (state.logQuery || "").trim();
      // 搜索时放宽行数上限，尽量多给出命中结果
      const limit = q ? 500 : 200;
      const url =
        `/api/logs?category=${encodeURIComponent(cat)}&limit=${limit}` +
        (q ? `&q=${encodeURIComponent(q)}` : "");
      const data = await api(url);
      const lines = data.logs || [];
      const text = cat + "|" + q + "|" + lines.join("");
      if (text !== state.lastLogText) {
        state.lastLogText = text;
        renderLogs(lines);
        updateLogSearchInfo(lines.length, q);
      }
    } catch (e) {
      /* 静默重试 */
    }
  }

  /** 搜索状态提示：命中条数 / 无结果 */
  function updateLogSearchInfo(count, q) {
    const el = $("log-search-info");
    if (!el) return;
    if (!q) {
      el.textContent = "";
      el.className = "log-search-info";
      return;
    }
    if (count > 0) {
      el.textContent = `命中 ${count} 条`;
      el.className = "log-search-info ok";
    } else {
      el.textContent = "没有匹配的日志";
      el.className = "log-search-info none";
    }
  }

  /** 执行搜索（防抖调用） */
  function applyLogSearch(q) {
    state.logQuery = q || "";
    state.lastLogText = "";
    // 搜索时强制跟随（滚到首条结果）
    if (state.logQuery.trim()) state.logFollow = true;
    updateLogFollowUI();
    fetchLogs();
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

  /** 切换日志分类（保留当前搜索词，相当于在该分类内搜索） */
  function switchLogCategory(cat) {
    state.logCategory = cat;
    state.lastLogText = "";
    state.logFollow = true;
    updateLogFollowUI();
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
    $("theme-toggle").addEventListener("click", toggleTheme);
    $("weight-save").addEventListener("click", saveWeights);
    $("weight-reset").addEventListener("click", async () => {
      const def = state.weightDefault && state.weightDefault.length
        ? state.weightDefault
        : ["UC", "C", "U", "NORMAL"];
      await api("/api/weight", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ order: def }),
      });
      toast("已恢复默认权重顺序");
      loadWeights();
    });

    // 自定义权重档位
    $("weight-add-custom").addEventListener("click", () => openWeightModal(null));
    $("weight-modal-cancel").addEventListener("click", closeWeightModal);
    $("weight-modal-ok").addEventListener("click", confirmWeightModal);
    $("weight-modal").addEventListener("click", (e) => {
      if (e.target.id === "weight-modal") closeWeightModal();
    });
    $("pause-btn").addEventListener("click", toggleLogPause);
    $("clear-btn").addEventListener("click", () => {
      $("log-box").replaceChildren();
      state.lastLogText = "\u0000-cleared";
    });

    // 日志自动跟随开关
    $("follow-btn").addEventListener("click", () => {
      setLogFollow(!shouldFollowLog(), true);
    });

    // 用户手动往上翻时自动锁定，翻回底部时恢复跟随
    $("log-box").addEventListener("scroll", () => {
      const box = $("log-box");
      const atBottom = box.scrollHeight - box.clientHeight <= box.scrollTop + 8;
      if (!atBottom && state.logFollow) {
        state.logFollow = false;
        updateLogFollowUI();
      } else if (atBottom && !state.logFollow && state.logQuery === "") {
        state.logFollow = true;
        updateLogFollowUI();
      }
    });

    // 日志搜索（输入防抖 300ms，回车立即执行）
    let searchTimer = null;
    const doSearch = (val) => {
      state.logCategory = state.logCategory || "all";
      applyLogSearch(val);
    };
    $("log-search").addEventListener("input", (e) => {
      const val = e.target.value;
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => doSearch(val), 300);
    });
    $("log-search").addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        clearTimeout(searchTimer);
        doSearch(e.target.value);
      } else if (e.key === "Escape") {
        e.target.value = "";
        clearTimeout(searchTimer);
        doSearch("");
      }
    });
    $("log-search-clear").addEventListener("click", () => {
      const input = $("log-search");
      input.value = "";
      clearTimeout(searchTimer);
      doSearch("");
      input.focus();
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
      if (browserTargetId) {
        if (browserTargetId.tagName === "TEXTAREA") {
          // 监听目录是多行文本框：追加一行
          const cur = browserTargetId.value.trim();
          if (!cur.split(/\n/).map((s) => s.trim()).includes(browserCurrent)) {
            browserTargetId.value = (cur ? cur + "\n" : "") + browserCurrent;
          }
        } else {
          browserTargetId.value = browserCurrent;
        }
        browserTargetId.dispatchEvent(new Event("input", { bubbles: true }));
      }
      $("browser").hidden = true;
    });

    // 快捷跳转（网盘根 / 元数据根）
    $("browser-quick").addEventListener("click", (e) => {
      const btn = e.target.closest(".browser-quick-btn");
      if (btn) loadBrowser(btn.dataset.path);
    });

    /* ---------- 存储路径表单（只有网盘根 / 元数据根） ---------- */
    ["cfg-mount", "cfg-meta"].forEach((id) => {
      const el = $(id);
      if (!el) return;
      el.addEventListener("input", () => {
        pathForm.dirty = true;
        pathMsg("");
      });
    });

    /* ---------- 投递页：监控目录实时校验 ---------- */
    const scanDirEl = $("deliver-scan-dir");
    if (scanDirEl) {
      scanDirEl.addEventListener("input", updateScanDirHint);
      scanDirEl.addEventListener("blur", updateScanDirHint);
    }

    document.querySelectorAll(".path-browse").forEach((btn) => {
      btn.addEventListener("click", () => {
        browserTargetId = $(btn.dataset.target);
        $("browser").hidden = false;
        const draft = collectPathForm();
        const start =
          (btn.dataset.target === "cfg-meta" ? draft.METADATA_BASE_PATH : draft.MOUNT_BASE_PATH) ||
          (state.lastStatus && state.lastStatus.mount_base_path) ||
          "/";
        loadBrowser(start);
      });
    });

    $("path-save").addEventListener("click", savePaths);
    $("path-reset").addEventListener("click", () => {
      pathForm.dirty = false;
      pathForm.loaded = false;
      pathMsg("已恢复为当前生效值", "ok");
      loadStatus();
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
    // 同步主题按钮文案（主题本身已由 head 内联脚本提前设置）
    applyTheme(currentTheme(), false);
    updateLogFollowUI();
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
