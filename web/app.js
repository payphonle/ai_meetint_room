/* AI 群聊 M5 前端逻辑 */
"use strict";

const MEMBER_COLORS = {
  "我": "#07c160",
  "豆包": "#4e6ef2",
  "DeepSeek": "#10a37f",
  "千问": "#7c4dff",
  "智谱清言": "#f59e0b",
  "元宝": "#ef4444",
};

const RISK_TEXT = { low: "低", medium: "中", high: "高" };
const STATUS_TEXT = {
  queued: "排队中…", typing: "对方正在输入…",
  streaming: "回复中…", failed: "发送失败", cancelled: "已取消",
};

const state = {
  members: [],
  echoMode: true,
  conv: { type: "group", platform: null },  // group | private | assistant
  unread: {},
  platforms: {},        // name -> {paused, reason, sent_today, daily_cap, queue_len}
  quote: null,          // {id, sender, content}
};

const msgCache = new Map();

/* ---------- DOM ---------- */
const $ = (sel) => document.querySelector(sel);
const memberListEl = $("#memberList");
const messagesEl = $("#messages");
const inputEl = $("#input");
const sendBtn = $("#sendBtn");
const mentionPopupEl = $("#mentionPopup");
const convTitleEl = $("#convTitle");
const convHintEl = $("#convHint");
const convTabsEl = $("#convTabs");
const riskFootEl = $("#riskFoot");
const modeBadgeEl = $("#modeBadge");
const quoteBarEl = $("#quoteBar");
const quoteTextEl = $("#quoteText");

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function avatarColor(name) { return MEMBER_COLORS[name] || "#888"; }

/* 各助手官方 logo（下载失败时回退为字母头像） */
const LOGOS = {
  "豆包": "logos/doubao.png",
  "DeepSeek": "logos/deepseek.png",
  "千问": "logos/qianwen.png",
  "智谱清言": "logos/chatglm.png",
  "元宝": "logos/yuanbao.png",
};

function makeAvatar(name, charOverride) {
  const av = document.createElement("div");
  av.className = "avatar" + (name === "我" ? " me" : "");
  const logo = LOGOS[name];
  if (logo) {
    const img = document.createElement("img");
    img.className = "avatar-img";
    img.src = logo;
    img.alt = name;
    img.addEventListener("error", () => {
      av.style.background = avatarColor(name);
      av.textContent = name === "智谱清言" ? "智" : name.charAt(0);
    });
    av.appendChild(img);
  } else {
    av.style.background = avatarColor(name);
    av.textContent = charOverride
      || (name === "智谱清言" ? "智" : name.charAt(0));
  }
  return av;
}

/* ---------- Markdown 渲染（仅助手消息，经 DOMPurify 消毒） ---------- */
const COLLAPSE_MIN_CHARS = 260;  // 手机端超过该长度默认折叠
const expandedMsgIds = new Set();  // 流式更新会重渲染，记住用户展开过的消息

function isMobileView() {
  return window.matchMedia("(max-width: 768px)").matches;
}

/* 规整历史数据：元宝等平台会把列表符号拆成独立行（"•\n正文"） */
function normalizeText(t) {
  return (t || "")
    .replace(/\r\n?/g, "\n")
    .replace(/^[ \t]*([•·▪◦‣])[ \t]*\n[ \t]*(?=\S)/gm, "$1 ");
}

/* 手机端长文本默认折叠，可手动展开/收起；桌面端始终完整显示 */
function maybeCollapse(node, text, id) {
  if (!text || text.length < COLLAPSE_MIN_CHARS || !isMobileView()) return node;
  const wrap = document.createElement("div");
  const expanded = id !== undefined && expandedMsgIds.has(id);
  wrap.className = expanded ? "collapsible" : "collapsible collapsed";
  const inner = document.createElement("div");
  inner.className = "collapsible-inner";
  inner.appendChild(node);
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "collapse-toggle";
  btn.textContent = expanded ? "收起" : "展开全文";
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    const collapsed = wrap.classList.toggle("collapsed");
    if (id !== undefined) {
      if (collapsed) expandedMsgIds.delete(id);
      else expandedMsgIds.add(id);
    }
    btn.textContent = collapsed ? "展开全文" : "收起";
  });
  wrap.appendChild(inner);
  wrap.appendChild(btn);
  return wrap;
}

function renderContent(m) {
  const c = normalizeText(m.content);
  if (!c) return document.createTextNode("…");
  if (m.role === "assistant" && window.marked && window.DOMPurify) {
    const div = document.createElement("div");
    div.className = "md";
    try {
      // breaks:true 保留单换行（元宝等平台段落之间只有一个 \n）
      div.innerHTML = DOMPurify.sanitize(
        marked.parse(c, { gfm: true, breaks: true }));
      return maybeCollapse(div, c, m.id);
    } catch { /* 渲染失败退回纯文本 */ }
  }
  return maybeCollapse(document.createTextNode(c), c, m.id);
}

/* ---------- 侧栏 ---------- */
function renderMembers() {
  memberListEl.innerHTML = "";
  memberListEl.appendChild(memberItem("我", "group", null, "点击进入群聊"));
  for (const m of state.members) {
    memberListEl.appendChild(
      memberItem(m.name, "private", m.name, "点击进入私聊 / 群聊筛选"));
  }
}

function memberItem(name, convType, platform, sub) {
  const li = document.createElement("li");
  li.className = "member";
  if (name === "我") {
    if (state.conv.type === "group") li.classList.add("active");
  } else if (state.conv.platform === platform) {
    li.classList.add("active");
  }
  const av = makeAvatar(name, name === "我" ? "群" : undefined);

  const info = document.createElement("div");
  info.className = "info";
  const nm = document.createElement("div");
  nm.className = "m-name";
  nm.textContent = name === "我" ? "助手群" : name;
  const sb = document.createElement("div");
  sb.className = "m-sub";
  if (name !== "我") {
    const dot = document.createElement("span");
    dot.className = "risk-dot risk-" + (state.members.find(x => x.name === name)?.risk || "low");
    sb.appendChild(dot);
  }
  const ps = state.platforms[name];
  let subText = sub;
  if (name !== "我") {
    if (ps?.paused) {
      subText = "已熔断 · 点击恢复";
      li.classList.add("offline");
      sb.style.color = "#fa5151";
    } else if (ps) {
      subText = `今日 ${ps.sent_today ?? 0}/${ps.daily_cap ?? "?"}`;
      if (ps.queue_len > 0) subText += ` · 排队 ${ps.queue_len}`;
    }
  }
  sb.appendChild(document.createTextNode(subText));

  info.appendChild(nm);
  info.appendChild(sb);
  li.appendChild(av);
  li.appendChild(info);

  if (state.unread[name]) {
    const dot = document.createElement("span");
    dot.className = "unread";
    li.appendChild(dot);
  }

  li.addEventListener("click", async () => {
    if (name !== "我" && state.platforms[name]?.paused) {
      await fetch(`/api/platform/${encodeURIComponent(name)}/resume`, { method: "POST" });
    }
    switchConv(name === "我" ? "group" : "private", platform);
  });
  return li;
}

function switchConv(type, platform) {
  state.conv = { type, platform };
  if (platform) state.unread[platform] = false;
  renderMembers();
  updateTabs();
  loadMessages();
  closeSidebar(); // 手机端抽屉：切换会话后收起
}

function updateTabs() {
  const isPlatform = state.conv.type === "private" || state.conv.type === "assistant";
  convTabsEl.classList.toggle("hidden", !isPlatform);
  if (!isPlatform) return;
  for (const btn of convTabsEl.querySelectorAll("button")) {
    btn.classList.toggle("on", btn.dataset.t === state.conv.type);
  }
}

/* ---------- 消息 ---------- */
async function loadMessages() {
  const { type, platform } = state.conv;
  let q;
  if (type === "group") q = "conv=group";
  else if (type === "private") q = `conv=private&platform=${encodeURIComponent(platform)}`;
  else q = `conv=assistant&platform=${encodeURIComponent(platform)}`;
  const res = await fetch(`/api/messages?${q}`);
  const data = await res.json();
  msgCache.clear();
  renderAll(data.messages);
}

function convTitle() {
  const { type, platform } = state.conv;
  if (type === "group") {
    convTitleEl.textContent = "助手群（5）";
    convHintEl.textContent = "";
  } else if (type === "private") {
    convTitleEl.textContent = platform;
    convHintEl.textContent = "私聊 · 消息仅发送给该助手";
  } else {
    convTitleEl.textContent = `${platform} · 群聊记录`;
    convHintEl.textContent = "从群聊中筛选：它收到的问题与它的回复";
  }
  updateTabs();
}

function renderAll(messages) {
  convTitle();
  messagesEl.innerHTML = "";
  for (const m of messages) msgCache.set(m.id, m);
  if (!messages.length) {
    const tip = document.createElement("div");
    tip.className = "empty-tip";
    tip.textContent = {
      group: "在群里发一条消息试试，比如：@DeepSeek @豆包 你好",
      private: "向该助手发送第一条私聊消息",
      assistant: "群聊里还没有与该助手的互动记录",
    }[state.conv.type] || "";
    messagesEl.appendChild(tip);
    return;
  }
  for (const m of messages) messagesEl.appendChild(renderMessage(m));
  scrollBottom();
}

function statusChip(m) {
  const show = m.status && m.status !== "done";
  if (!show) return null;
  const st = document.createElement("span");
  st.className = `status-chip st-${m.status}`;
  st.textContent = STATUS_TEXT[m.status] || m.status;
  return st;
}

function renderMessage(m) {
  const isUser = m.role === "user";
  const senderName = isUser ? "我" : (m.sender || m.platform);
  const div = document.createElement("div");
  div.className = `msg ${isUser ? "user" : "assistant"}`;
  div.dataset.id = m.id;

  const av = makeAvatar(senderName, isUser ? "我" : undefined);

  const body = document.createElement("div");
  body.className = "body";

  const meta = document.createElement("div");
  meta.className = "meta";
  const nm = document.createElement("span");
  nm.textContent = senderName;
  const tm = document.createElement("span");
  tm.textContent = (m.created_at || "").slice(5, 16);
  meta.appendChild(nm);
  meta.appendChild(tm);
  const chip = statusChip(m);
  if (chip) meta.appendChild(chip);

  if (m.quote_content) {
    const qp = document.createElement("div");
    qp.className = "quote-prev";
    const preview = (m.quote_content || "").slice(0, 80).replace(/\s+/g, " ");
    qp.textContent = `引用 ${m.quote_sender || "消息"}：${preview}`;
    body.appendChild(qp);
  }

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.appendChild(renderContent(m));

  body.appendChild(meta);
  body.appendChild(bubble);

  // 操作按钮
  const act = document.createElement("div");
  act.className = "msg-act";
  if (isUser) {
    if (m.status === "queued") {
      act.appendChild(actionBtn("取消", () => cancelMsg(m.id)));
    } else if (m.status === "failed" || m.status === "cancelled") {
      act.appendChild(actionBtn("重发", () => resendMsg(m.id)));
    }
  } else {
    act.appendChild(actionBtn("引用", () => quoteMsg(m)));
  }
  body.appendChild(act);

  div.appendChild(av);
  div.appendChild(body);
  return div;
}

function actionBtn(text, onClick) {
  const b = document.createElement("span");
  b.className = "act-btn";
  b.textContent = text;
  b.addEventListener("click", (e) => { e.stopPropagation(); onClick(); });
  return b;
}

function scrollBottom() { messagesEl.scrollTop = messagesEl.scrollHeight; }

function inCurrentConv(m) {
  const { type, platform } = state.conv;
  if (type === "group") return m.conv_type === "group";
  if (type === "private") return m.conv_type === "private" && m.platform === platform;
  return false; // 助手筛选视图为只读快照，新消息不实时插入
}

function upsertMessage(m) {
  msgCache.set(m.id, m);
  if (!inCurrentConv(m)) {
    if (m.conv_type === "private" && m.platform && m.platform !== state.conv.platform) {
      state.unread[m.platform] = true;
      renderMembers();
    }
    return;
  }
  const tip = messagesEl.querySelector(".empty-tip");
  if (tip) tip.remove();
  const old = messagesEl.querySelector(`[data-id="${m.id}"]`);
  if (old) old.replaceWith(renderMessage(m));
  else messagesEl.appendChild(renderMessage(m));
  scrollBottom();
}

function patchMessage(evt) {
  const m = msgCache.get(evt.id);
  if (m) {
    if (evt.content !== undefined) m.content = evt.content;
    if (evt.status !== undefined) m.status = evt.status;
  }
  const el = messagesEl.querySelector(`[data-id="${evt.id}"]`);
  if (!el) return;
  const cached = m || { id: evt.id };
  const merged = { ...cached, ...evt };
  el.replaceWith(renderMessage(merged));
  if (evt.content !== undefined) scrollBottom();
}

/* ---------- 消息操作 ---------- */
async function cancelMsg(id) {
  const r = await fetch(`/api/cancel/${id}`, { method: "POST" });
  if (!r.ok) toast((await r.json().catch(() => ({})))?.error || "取消失败");
}

async function resendMsg(id) {
  const r = await fetch(`/api/resend/${id}`, { method: "POST" });
  if (!r.ok) toast((await r.json().catch(() => ({})))?.error || "重发失败");
}

function quoteMsg(m) {
  state.quote = { id: m.id, sender: m.sender || m.platform, content: m.content || "" };
  quoteTextEl.textContent =
    `引用 ${state.quote.sender}：${state.quote.content.slice(0, 60).replace(/\s+/g, " ")}…`;
  quoteBarEl.classList.remove("hidden");
  inputEl.focus();
}

function clearQuote() {
  state.quote = null;
  quoteBarEl.classList.add("hidden");
}

$("#quoteRemove").addEventListener("click", clearQuote);

/* ---------- @ 提及 ---------- */
let mentionState = null;

function showMentionPopup(query) {
  mentionPopupEl.innerHTML = "";
  const title = document.createElement("div");
  title.className = "mp-title";
  title.textContent = "选择要 @ 的助手（可多选）";
  mentionPopupEl.appendChild(title);

  const q = (query || "").toLowerCase();
  const matched = state.members.map(m => m.name)
    .filter(name => name.toLowerCase().includes(q));

  if (!matched.length) {
    const empty = document.createElement("div");
    empty.className = "mp-item";
    empty.style.color = "#aaa";
    empty.textContent = "无匹配成员";
    mentionPopupEl.appendChild(empty);
    mentionPopupEl.classList.remove("hidden");
    return;
  }

  for (const name of matched) {
    const item = document.createElement("div");
    item.className = "mp-item";
    const av = document.createElement("span");
    av.className = "avatar";
    av.style.cssText = `width:22px;height:22px;font-size:11px;border-radius:4px;background:${avatarColor(name)}`;
    av.textContent = name.charAt(0);
    item.appendChild(av);
    item.appendChild(document.createTextNode(name));
    item.addEventListener("mousedown", (e) => {
      e.preventDefault();
      insertMention(name);
      mentionPopupEl.classList.add("hidden");
    });
    mentionPopupEl.appendChild(item);
  }
  mentionPopupEl.classList.remove("hidden");
}

function insertMention(name) {
  if (!mentionState) return;
  const pos = inputEl.selectionStart;
  const before = inputEl.value.slice(0, mentionState.start) + `@${name} `;
  const after = inputEl.value.slice(pos);
  inputEl.value = before + after;
  const newPos = before.length;
  inputEl.setSelectionRange(newPos, newPos);
  inputEl.focus();
  mentionState = null;
}

function hideMentionPopup() {
  mentionState = null;
  mentionPopupEl.classList.add("hidden");
}

/* ---------- 发送 ---------- */
async function sendMessage() {
  const text = inputEl.value.trim();
  if (!text) return;
  hideMentionPopup();
  sendBtn.disabled = true;
  try {
    await fetch("/api/send", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text,
        conv_type: state.conv.type === "assistant" ? "group" : state.conv.type,
        platform: state.conv.type === "private" ? state.conv.platform : null,
        quote_msg_id: state.quote?.id ?? null,
      }),
    });
    inputEl.value = "";
    clearQuote();
  } finally {
    sendBtn.disabled = false;
    inputEl.focus();
  }
}

/* ---------- WebSocket ---------- */
let ws = null;
let wsTimer = null;

function connectWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => { clearTimeout(wsTimer); };
  ws.onclose = () => { wsTimer = setTimeout(connectWS, 2000); };
  ws.onmessage = (e) => {
    const evt = JSON.parse(e.data);
    if (evt.type === "new_message") upsertMessage(evt.message);
    else if (evt.type === "message_update") patchMessage(evt);
    else if (evt.type === "platform_state") {
      state.platforms[evt.platform] = { ...(state.platforms[evt.platform] || {}), ...evt };
      renderMembers();
      renderQueuePanel();
      if (evt.paused) toast(`${evt.platform} 已熔断：${evt.reason || "未知原因"}`);
    } else if (evt.type === "platform_info") {
      toast(`${evt.platform}：${evt.text}`);
    }
  };
}

/* ---------- 平台状态轮询 ---------- */
async function fetchPlatforms() {
  try {
    const r = await fetch("/api/platforms");
    const d = await r.json();
    let changed = false;
    for (const p of d.platforms) {
      const old = state.platforms[p.name];
      if (!old || old.paused !== p.paused || old.sent_today !== p.sent_today
          || old.queue_len !== p.queue_len) changed = true;
      state.platforms[p.name] = p;
    }
    if (changed) {
      renderMembers();
      renderQueuePanel();
    }
  } catch { /* 服务暂不可达时静默 */ }
}

/* ---------- 队列面板 ---------- */
function renderQueuePanel() {
  const panel = $("#queuePanel");
  if (!panel || panel.classList.contains("hidden")) return;
  panel.innerHTML = "";
  for (const m of state.members) {
    const p = state.platforms[m.name] || {};
    const row = document.createElement("div");
    row.className = "qp-row";
    const dot = document.createElement("span");
    dot.className = "risk-dot risk-" + m.risk;
    const label = document.createElement("span");
    label.className = "qp-name";
    label.textContent = m.name;
    const stat = document.createElement("span");
    stat.className = "qp-stat" + (p.paused ? " qp-paused" : "");
    stat.textContent = p.paused ? "已熔断"
      : `排队 ${p.queue_len ?? 0} · 今日 ${p.sent_today ?? 0}/${p.daily_cap ?? "?"}`;
    row.appendChild(dot);
    row.appendChild(label);
    row.appendChild(stat);
    panel.appendChild(row);
  }
}

function toggleQueuePanel() {
  const panel = $("#queuePanel");
  panel.classList.toggle("hidden");
  $("#queueToggle").textContent =
    panel.classList.contains("hidden") ? "防风控状态 ▸" : "防风控状态 ▾";
  renderQueuePanel();
}

/* ---------- 导出 / 设置 ---------- */
function doExport() {
  const { type, platform } = state.conv;
  let q = "conv=group";
  if (type === "private") q = `conv=private&platform=${encodeURIComponent(platform)}`;
  else if (type === "assistant") q = `conv=assistant&platform=${encodeURIComponent(platform)}`;
  window.open(`/api/export?${q}`);
}

async function openSettings() {
  const r = await fetch("/api/settings");
  const d = await r.json();
  $("#setQuietStart").value = d.quiet_hours?.[0] || "01:00";
  $("#setQuietEnd").value = d.quiet_hours?.[1] || "07:30";
  const tbody = $("#setMemberRows");
  tbody.innerHTML = "";
  for (const m of d.members) {
    const tr = document.createElement("tr");
    const td0 = document.createElement("td");
    td0.textContent = m.name;
    tr.appendChild(td0);
    for (const k of ["min_delay", "max_delay", "daily_cap"]) {
      const td = document.createElement("td");
      const inp = document.createElement("input");
      inp.type = "number";
      inp.min = "1";
      inp.step = k === "daily_cap" ? "1" : "5";
      inp.value = m[k];
      inp.dataset.name = m.name;
      inp.dataset.key = k;
      td.appendChild(inp);
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
  $("#settingsModal").classList.remove("hidden");
}

async function saveSettings() {
  const members = [];
  for (const inp of $("#setMemberRows").querySelectorAll("input")) {
    let row = members.find(x => x.name === inp.dataset.name);
    if (!row) { row = { name: inp.dataset.name }; members.push(row); }
    row[inp.dataset.key] = Number(inp.value);
  }
  const body = {
    quiet_hours: [$("#setQuietStart").value || "01:00",
                  $("#setQuietEnd").value || "07:30"],
    members,
  };
  const r = await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (r.ok) {
    $("#settingsModal").classList.add("hidden");
    toast("设置已保存并即时生效");
    fetchPlatforms();
  } else {
    toast("保存失败");
  }
}

/* ---------- toast ---------- */
let toastTimer = null;
function toast(text) {
  let el = $("#toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    document.body.appendChild(el);
  }
  el.textContent = text;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 4500);
}

/* ---------- 移动端抽屉 ---------- */
function closeSidebar() {
  $("#sidebar").classList.remove("open");
  $("#sidebarMask").classList.add("hidden");
}

/* ---------- 初始化 ---------- */
async function init() {
  const res = await fetch("/api/state");
  const data = await res.json();
  state.members = data.members;
  state.echoMode = data.echo_mode;
  modeBadgeEl.textContent = state.echoMode ? "回声模式" : "在线模式";
  riskFootEl.textContent = state.echoMode
    ? "回声模式 · 未连接任何平台"
    : "浏览器自动化运行中";
  renderMembers();
  connectWS();
  loadMessages();
  fetchPlatforms();
  setInterval(fetchPlatforms, 5000);

  sendBtn.addEventListener("click", sendMessage);
  $("#queueToggle").addEventListener("click", toggleQueuePanel);
  $("#exportBtn").addEventListener("click", doExport);
  $("#settingsBtn").addEventListener("click", openSettings);
  $("#settingsSave").addEventListener("click", saveSettings);
  $("#settingsClose").addEventListener("click",
    () => $("#settingsModal").classList.add("hidden"));
  $("#menuBtn").addEventListener("click", () => {
    $("#sidebar").classList.add("open");
    $("#sidebarMask").classList.remove("hidden");
  });
  $("#sidebarMask").addEventListener("click", closeSidebar);
  convTabsEl.addEventListener("click", (e) => {
    const t = e.target.dataset?.t;
    if (t) switchConv(t, state.conv.platform);
  });

  inputEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    } else if (e.key === "Escape") {
      hideMentionPopup();
    }
  });
  inputEl.addEventListener("input", () => {
    const v = inputEl.value;
    const pos = inputEl.selectionStart;
    const at = v.lastIndexOf("@", Math.max(0, pos - 1));
    if (at >= 0) {
      const query = v.slice(at + 1, pos);
      if (!query.includes(" ") && !query.includes("\n")) {
        mentionState = { start: at, query };
        showMentionPopup(query);
        return;
      }
    }
    hideMentionPopup();
  });
  document.addEventListener("click", (e) => {
    if (!mentionPopupEl.contains(e.target) && e.target !== inputEl) {
      hideMentionPopup();
    }
  });
}

init();
