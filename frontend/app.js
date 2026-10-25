(() => {
  "use strict";

  const chat = document.getElementById("chat");
  const input = document.getElementById("input");
  const sendBtn = document.getElementById("send");
  const btnLabel = sendBtn.querySelector(".btn-label");

  let busy = false;
  let currentController = null; // 当前流式请求的 AbortController
  // 多轮上下文：当前会话的消息列表（刷新后从 localStorage 恢复）
  let messages = [];
  // 多会话历史：[{ sessionId, title, messages, updatedAt }]，持久化在 localStorage
  let sessions = [];
  let currentSessionId = null;
  const SESSIONS_KEY = "fitness_qa_sessions";

  // ---------- 头像 SVG ----------
  const USER_AVATAR_SVG =
    '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 3.6-6 8-6s8 2 8 6"/></svg>';
  const ASSISTANT_AVATAR = "\u{1F3CB}\uFE0F"; // 举重 emoji 🏋️

  // ---------- 工具 ----------
  function fileName(source) {
    const name = String(source || "").replace(/\\/g, "/").split("/").pop();
    return name || source;
  }
  function nowTime() {
    const d = new Date();
    const pad = (n) => String(n).padStart(2, "0");
    return pad(d.getHours()) + ":" + pad(d.getMinutes());
  }

  // 智能滚动跟随（防拽回）：流式期间用户上滑离开底部（>150px）即暂停自动跟随，
  // 滑回底部附近自动恢复；新消息出现（scrollToBottom）视为回到跟随态
  let autoFollow = true;
  let lastChatTop = 0;
  chat.addEventListener("scroll", () => {
    const goingUp = chat.scrollTop < lastChatTop - 4; // 4px 容差，忽略亚像素抖动
    lastChatTop = chat.scrollTop;
    if (goingUp && !isNearBottom()) autoFollow = false;
    else if (isNearBottom()) autoFollow = true;
  });
  function isNearBottom() {
    return chat.scrollHeight - chat.scrollTop - chat.clientHeight < 150;
  }
  function scrollToBottom() {
    autoFollow = true; // 主动滚动（新消息）无条件回到跟随态
    chat.scrollTo({ top: chat.scrollHeight, behavior: "smooth" });
  }
  function followScroll() {
    if (autoFollow) chat.scrollTo({ top: chat.scrollHeight, behavior: "smooth" });
  }

  // 从非 200 响应体解析 FastAPI 标准报错 {"detail": "..."}（400 拦截 / 429 限流等），
  // 后端给出的中文提示原样透传给用户；解析失败（非 JSON / 无 detail）回退通用提示
  async function extractDetail(resp, fallback) {
    try {
      const data = await resp.json();
      if (data && typeof data.detail === "string" && data.detail) return data.detail;
    } catch (e) {
      /* 响应体不是 JSON（如网关 HTML 错误页）：走回退提示 */
    }
    return fallback;
  }

  // ---------- 轻量富文本渲染（防注入） ----------
  // 先转义全部 HTML，再把受控标记转成白名单标签，最后清理任何残留的非白名单标签
  // 支持无序列表（- / * 开头连续行）与有序列表（1. 数字开头连续行）
  function renderAnswer(text) {
    const src = String(text || "");
    // 1) 先转义危险字符（XSS 防线必须在任何标记替换之前，含列表语法）
    const escaped = src
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
    // 2) 行内加粗 ``**文本**`` → <strong>…</strong>（列表项内同样生效）
    const bold = (s) => s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    // 3) 逐行处理：连续「- / * 」行合并为 <ul>，连续「数字. 」行合并为 <ol>，其余行保留换行
    //    注意 ^\s*[-*]\s+ 不会误吞 **加粗** 行（第二个字符不是空白）
    const lines = escaped.split("\n");
    let html = "";
    let list = null; // 当前列表标签（"ul" | "ol" | null）
    for (const line of lines) {
      const mUl = line.match(/^\s*[-*]\s+(.*)$/);
      const mOl = mUl ? null : line.match(/^\s*\d+\.\s+(.*)$/);
      if (mUl || mOl) {
        const tag = mUl ? "ul" : "ol";
        if (list !== tag) {
          if (list) html += "</" + list + ">";
          html += "<" + tag + ">";
          list = tag;
        }
        html += "<li>" + bold(mUl ? mUl[1] : mOl[1]) + "</li>";
      } else {
        if (list) {
          html += "</" + list + ">";
          list = null;
        }
        html += bold(line) + "<br>";
      }
    }
    if (list) html += "</" + list + ">";
    // 4) 清理白名单之外的残留标签（允许 strong / br / ul / ol / li）
    return html.replace(/<\/?(?!\/?(?:strong|br|ul|ol|li)\b)[a-zA-Z][^>]*>/g, "");
  }

  // ---------- 消息构建 ----------
  function makeMsg(role, timeStr) {
    const wrap = document.createElement("div");
    wrap.className = "msg " + role;

    const avatar = document.createElement("div");
    avatar.className = "avatar";
    if (role === "user") {
      avatar.innerHTML = USER_AVATAR_SVG;
    } else {
      avatar.textContent = ASSISTANT_AVATAR;
    }
    wrap.appendChild(avatar);

    const body = document.createElement("div");
    body.className = "msg-body";
    wrap.appendChild(body);

    const time = document.createElement("div");
    time.className = "msg-time";
    time.textContent = timeStr || nowTime();
    body.appendChild(time);
    return { wrap, body };
  }

  // 构建 AI 回答气泡（拒绝/追问徽章 + 改写提示 + 正文容器）：
  // renderHistory 与流式 meta 共用同一构建逻辑，杜绝两处实现漂移
  function buildAnswerBubble(meta, timeStr) {
    const m = makeMsg("assistant", timeStr);
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    if (meta.refused || meta.clarify) {
      bubble.classList.add(meta.refused ? "is-refused" : "is-clarify", "has-flag");
      const flag = document.createElement("span");
      flag.className = "flag " + (meta.refused ? "flag-refused" : "flag-clarify");
      flag.textContent = meta.refused ? "\u26A0\uFE0F 已拦截" : "\u2753 需要补充信息";
      bubble.appendChild(flag);
    }
    if (meta.rewritten) {
      const rw = document.createElement("div");
      rw.className = "rewrite-hint";
      rw.title = "多轮追问已自动重写为独立完整的检索句";
      rw.textContent = "\uD83D\uDD0E 已结合上下文优化检索：" + meta.rewritten;
      bubble.appendChild(rw);
    }
    const answerText = document.createElement("div");
    answerText.className = "answer-text";
    bubble.appendChild(answerText);
    m.body.appendChild(bubble);
    chat.appendChild(m.wrap);
    return { bubble, answerText };
  }

  function addWelcome() {
    const { wrap, body } = makeMsg("assistant");
    const bubble = document.createElement("div");
    bubble.className = "bubble bubble-welcome";
    bubble.innerHTML = renderAnswer(
      "你好，我是你的 AI 健身教练 💪\n" +
      "动作技术、营养原则、伤病预防，尽管问我——我会给出具体建议，并附上知识库里的依据来源。\n" +
      "一句我的执教原则送给你：**动作质量永远优先于重量**。\n" +
      "安全边界先说清：我不做伤病诊断、不涉及任何用药建议，这类问题我会直接建议你去看医生或康复师，别硬扛。\n" +
      "点击左侧的问题开始，或直接输入你的疑问。"
    );
    body.appendChild(bubble);
    chat.appendChild(wrap);
    scrollToBottom();
  }

  function addUser(text) {
    const { wrap, body } = makeMsg("user");
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.textContent = text;
    body.appendChild(bubble);
    chat.appendChild(wrap);
    scrollToBottom();
    messages.push({ role: "user", content: text, t: nowTime() });
  }

  function appendSources(bubble, sources) {
    if (!sources || !sources.length) return;
    const src = document.createElement("div");
    src.className = "sources";
    // 前置说明文字（浅灰，非标签）
    const label = document.createElement("span");
    label.className = "sources-label";
    label.textContent = "参考来源：";
    src.appendChild(label);
    // 按文档名去重，避免同一来源重复出现
    const seen = new Set();
    sources.forEach((s) => {
      // 去掉 .md 后缀，渲染为带书名号的中文资料名
      const name = fileName(s.source).replace(/\.md$/i, "");
      if (!name || seen.has(name)) return;
      seen.add(name);
      const tag = document.createElement("span");
      tag.className = "src-tag";
      tag.textContent = "《" + name + "》";
      // 悬停提示：参考来源：《xxx》
      tag.title = "参考来源：《" + name + "》";
      src.appendChild(tag);
    });
    bubble.appendChild(src);
  }

  // ---------- 气泡操作区：一键复制 + 重新生成 ----------
  const COPY_SVG =
    '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
  const COPIED_SVG =
    '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="#16a34a" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6L9 17l-5-5"/></svg>';
  const REGEN_SVG =
    '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 3v6h-6"/></svg>';

  // 写入剪贴板：优先 clipboard API（带 1.5s 超时——部分内嵌 WebView 无剪贴板权限时
  // promise 永不落定，不设上限会把复制按钮永久卡在禁用态）；
  // 超时/被拒/非安全上下文（服务器 http://IP 部署）时回退临时 textarea + execCommand
  async function copyToClipboard(text) {
    if (navigator.clipboard && window.isSecureContext) {
      let ok = false;
      try {
        ok = await Promise.race([
          navigator.clipboard.writeText(text).then(
            () => true,
            () => false
          ),
          new Promise((r) => setTimeout(() => r(false), 1500)),
        ]);
      } catch (e) {
        ok = false;
      }
      if (ok) return;
    }
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.cssText = "position:fixed;opacity:0;pointer-events:none;";
    document.body.appendChild(ta);
    ta.select();
    try {
      if (!document.execCommand("copy")) throw new Error("execCommand copy failed");
    } finally {
      ta.remove();
    }
  }

  // 给 AI 气泡挂底部操作区（复制按钮；「重新生成」由 refreshRegenButtons 单独挂最后一条）
  function appendActions(bubble, rawText) {
    const row = document.createElement("div");
    row.className = "msg-actions";

    const copyBtn = document.createElement("button");
    copyBtn.type = "button";
    copyBtn.className = "msg-act act-copy";
    copyBtn.title = "复制回答";
    copyBtn.setAttribute("aria-label", "复制回答");
    copyBtn.innerHTML = COPY_SVG;
    copyBtn.addEventListener("click", async () => {
      if (copyBtn.disabled) return;
      copyBtn.disabled = true;
      try {
        // 纯文本：去掉 Markdown 加粗符号；来源标签不在 rawText 内，天然干净
        await copyToClipboard(String(rawText || "").replace(/\*\*/g, ""));
        copyBtn.innerHTML = COPIED_SVG;
        copyBtn.classList.add("is-copied");
        showToast("已复制", true, 2000);
        clearTimeout(copyBtn._t);
        copyBtn._t = setTimeout(() => {
          copyBtn.innerHTML = COPY_SVG;
          copyBtn.classList.remove("is-copied");
          copyBtn.disabled = false;
        }, 2000);
      } catch (e) {
        copyBtn.disabled = false;
        showToast("复制失败，请手动选择复制", false);
      }
    });
    row.appendChild(copyBtn);
    bubble.appendChild(row);
  }

  function makeRegenButton() {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "msg-act act-regen";
    btn.title = "重新生成";
    btn.setAttribute("aria-label", "重新生成");
    btn.innerHTML = REGEN_SVG;
    btn.addEventListener("click", regenerateAnswer);
    return btn;
  }

  // 「重新生成」只挂在最后一条 AI 回答气泡上；消息每次变化后重挂
  function refreshRegenButtons() {
    chat.querySelectorAll(".act-regen").forEach((b) => b.remove());
    if (busy) return; // 生成期间不提供
    const wraps = chat.querySelectorAll(".msg.assistant");
    for (let i = wraps.length - 1; i >= 0; i--) {
      const bubble = wraps[i].querySelector(".bubble");
      if (!bubble || bubble.classList.contains("bubble-welcome")) continue;
      const row = bubble.querySelector(".msg-actions");
      if (!row) continue;
      row.appendChild(makeRegenButton());
      return;
    }
  }

  // 重新生成：取该回答对应的最近一次用户提问，作废其后所有回答，重发流式请求
  function regenerateAnswer() {
    if (busy) return;
    let qi = -1;
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === "user") {
        qi = i;
        break;
      }
    }
    if (qi < 0) return;
    const question = messages[qi].content;
    messages.length = qi + 1; // 丢弃该提问之后的所有消息（通常是 1 条 AI 回答）
    renderHistory(); // DOM 与 messages 严格对齐（顺带清掉旧气泡/错误气泡）
    ask(question, { regen: true });
  }

  // ---------- 多会话历史管理（localStorage） ----------
  const newChatBtn = document.getElementById("newChatBtn");
  const sessionListEl = document.getElementById("sessionList");

  function genId() {
    return (crypto.randomUUID && crypto.randomUUID()) ||
      Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 8);
  }

  // 会话标题：取该会话第一条用户消息的前 15 个字
  function cutTitle(text) {
    const t = String(text || "").trim();
    if (!t) return "新对话";
    return t.length > 15 ? t.slice(0, 15) + "…" : t;
  }

  function loadSessions() {
    try {
      const raw = localStorage.getItem(SESSIONS_KEY);
      const arr = raw ? JSON.parse(raw) : [];
      return Array.isArray(arr) ? arr : [];
    } catch (e) {
      return [];
    }
  }

  function saveSessions() {
    try {
      localStorage.setItem(SESSIONS_KEY, JSON.stringify(sessions));
    } catch (e) {
      /* 存储满等异常：静默降级为仅内存会话 */
    }
  }

  // 当前会话同步入库：空会话不占列表位；标题实时取首条用户消息
  function persistCurrent() {
    const idx = sessions.findIndex((s) => s.sessionId === currentSessionId);
    if (!messages.length) {
      if (idx >= 0) {
        sessions.splice(idx, 1);
        saveSessions();
        renderSessions();
      }
      return;
    }
    const firstUser = messages.find((m) => m.role === "user");
    const sess = idx >= 0 ? sessions[idx] : { sessionId: currentSessionId };
    sess.title = cutTitle(firstUser ? firstUser.content : "");
    sess.messages = messages;
    sess.updatedAt = Date.now();
    if (idx < 0) sessions.push(sess);
    saveSessions();
    renderSessions();
  }

  // 历史会话列表：按更新时间倒序，最新的在最上面
  function renderSessions() {
    sessionListEl.innerHTML = "";
    const sorted = [...sessions].sort((a, b) => b.updatedAt - a.updatedAt);
    sorted.forEach((s) => {
      const li = document.createElement("li");
      li.className = "session-item" + (s.sessionId === currentSessionId ? " is-active" : "");

      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "session-btn";
      btn.title = s.title || "新对话";
      btn.textContent = s.title || "新对话";
      btn.addEventListener("click", () => switchTo(s.sessionId));

      const del = document.createElement("button");
      del.type = "button";
      del.className = "session-del";
      del.setAttribute("aria-label", "删除会话");
      del.textContent = "×";
      del.addEventListener("click", (e) => {
        e.stopPropagation();
        deleteSession(s.sessionId);
      });

      li.appendChild(btn);
      li.appendChild(del);
      sessionListEl.appendChild(li);
    });
  }

  // 从 messages 数组完整重放当前会话的气泡（含徽章与来源标签）
  function renderHistory() {
    chat.innerHTML = "";
    messages.forEach((m) => {
      if (m.role === "user") {
        const { wrap, body } = makeMsg(m.role, m.t);
        const bubble = document.createElement("div");
        bubble.className = "bubble";
        bubble.textContent = m.content;
        body.appendChild(bubble);
        chat.appendChild(wrap);
      } else {
        const built = buildAnswerBubble({ refused: !!m.refused, clarify: !!m.clarify }, m.t);
        built.answerText.innerHTML = renderAnswer(m.content);
        if (m.sources && m.sources.length) appendSources(built.bubble, m.sources);
        appendActions(built.bubble, m.content);
      }
    });
    if (!messages.length) addWelcome();
    refreshRegenButtons();
  }

  function switchTo(id) {
    if (busy) {
      showToast("回答生成中，请先停止或等待完成", false);
      return;
    }
    const sess = sessions.find((s) => s.sessionId === id);
    if (!sess) return;
    persistCurrent(); // 保存刚离开的会话
    currentSessionId = id;
    messages = sess.messages;
    renderHistory();
    renderSessions();
    chat.scrollTop = chat.scrollHeight; // 恢复时直接定位到底部，不做平滑动画
  }

  function newChat() {
    if (busy) {
      showToast("回答生成中，请先停止或等待完成", false);
      return;
    }
    persistCurrent(); // 进行中的对话先落库
    currentSessionId = genId();
    messages = [];
    chat.innerHTML = "";
    addWelcome();
    renderSessions();
    input.focus();
  }

  function deleteSession(id) {
    const isCurrent = id === currentSessionId;
    if (isCurrent && busy) {
      showToast("回答生成中，请先停止或等待完成", false);
      return;
    }
    sessions = sessions.filter((s) => s.sessionId !== id);
    saveSessions();
    if (isCurrent) {
      currentSessionId = genId();
      messages = [];
      chat.innerHTML = "";
      addWelcome();
    }
    renderSessions();
  }

  newChatBtn.addEventListener("click", newChat);

  function addError(msg) {
    const { wrap, body } = makeMsg("assistant");
    const bubble = document.createElement("div");
    bubble.className = "bubble error-line";
    bubble.textContent = "出错了：" + msg;
    body.appendChild(bubble);
    chat.appendChild(wrap);
    scrollToBottom();
  }

  function addTyping() {
    const { wrap, body } = makeMsg("assistant");
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    const typing = document.createElement("span");
    typing.className = "typing";
    typing.innerHTML = "<i></i><i></i><i></i>";
    bubble.appendChild(typing);
    body.appendChild(bubble);
    chat.appendChild(wrap);
    scrollToBottom();
    return wrap;
  }

  // ---------- 流式问答 ----------
  // 生成状态切换：输入框 / 发送按钮 / 重新生成按钮的可用性与文案统一管理
  function setBusy(on) {
    busy = on;
    input.disabled = on;
    sendBtn.classList.toggle("is-stop", on);
    btnLabel.textContent = on ? "停止" : "发送";
    sendBtn.setAttribute("aria-label", on ? "停止生成" : "发送");
    chat.querySelectorAll(".act-regen").forEach((b) => (b.disabled = on));
    if (!on) {
      refreshRegenButtons(); // 空闲后把「重新生成」挂回最后一条 AI 回答
      input.focus();
    }
  }

  // 读取 SSE 流：按空行切分事件（一条事件内可含多个 data: 行），单条坏事件不中断整个流
  async function consumeSseStream(reader, onEvent) {
    const decoder = new TextDecoder("utf-8");
    let sseBuf = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      sseBuf += decoder.decode(value, { stream: true });
      let sep;
      while ((sep = sseBuf.indexOf("\n\n")) >= 0) {
        const rawEvent = sseBuf.slice(0, sep);
        sseBuf = sseBuf.slice(sep + 2);
        for (const line of rawEvent.split("\n")) {
          if (!line.startsWith("data:")) continue;
          try {
            onEvent(JSON.parse(line.slice(5).trim()));
          } catch (e) {
            /* 单条坏事件不中断整个流 */
          }
        }
      }
    }
  }

  // 收尾 DOM：终帧渲染、来源标签、停止标记、操作区；
  // 首事件未到即停止时补一个空气泡承载「已停止」提示（st.bubble 可变，占位气泡回写）
  function finalizeAnswer(st) {
    if (!st.bubble && st.stopped) {
      st.typingWrap.remove();
      const m = makeMsg("assistant");
      st.bubble = document.createElement("div");
      st.bubble.className = "bubble";
      m.body.appendChild(st.bubble);
      chat.appendChild(m.wrap);
    }
    if (st.bubble) {
      st.finalRender(); // 终帧：完整渲染并移除光标
      if (st.sources && st.sources.length) appendSources(st.bubble, st.sources);
      if (st.stopped) {
        const note = document.createElement("div");
        note.className = "stopped-note";
        note.textContent = "\u23F9 已停止生成";
        st.bubble.appendChild(note);
      }
      appendActions(st.bubble, st.buf); // 底部操作区（复制；重新生成由 setBusy 收尾统一挂载）
    } else if (!st.stopped && !st.errored) {
      // 流正常结束却连 meta 都没收到（如服务端中途断流）
      st.typingWrap.remove();
      addError("未收到任何内容，连接可能中断，请重试");
    }
  }

  async function ask(text, opts) {
    const regen = !!(opts && opts.regen); // 重新生成：用户气泡已存在，不重复添加
    setBusy(true);
    currentController = new AbortController();

    if (!regen) {
      addUser(text);
      persistCurrent(); // 用户消息实时入 localStorage
    }
    const typingWrap = addTyping();
    // 携带最近 3 轮历史（最多 6 条），排除刚加入的当前问题（它在 question 字段里）
    const history = messages
      .slice(0, -1)
      .slice(-6)
      .map((m) => ({ role: m.role, content: m.content }));

    let meta = null;        // 首个事件：分类与标志位
    let buf = "";           // 累积的回答文本
    let sources = [];       // 收尾事件：引用来源
    let bubble = null;      // AI 气泡（收到 meta 时创建）
    let answerText = null;  // 气泡内文本节点
    let cursor = null;      // 流式期间的闪烁光标
    let stopped = false;    // 用户主动停止
    let errored = false;
    let timedOut = false;   // 看门狗超时（区别于用户主动停止）
    let watchdog = null;    // 首事件看门狗：45 秒无任何事件则中止请求
    let lastRender = 0;

    // 流式渲染（30ms 节流）：force 时做终帧渲染并移除光标
    function renderStreamed(force) {
      if (!answerText) return;
      const now = Date.now();
      if (!force && now - lastRender < 30) return;
      lastRender = now;
      answerText.innerHTML = renderAnswer(buf);
      if (!force && cursor) answerText.appendChild(cursor);
      followScroll();
    }

    function onEvent(payload) {
      // 首个事件（meta 或文本）到达：看门狗解除武装
      if (watchdog && (payload.meta || typeof payload.text === "string")) {
        clearTimeout(watchdog);
        watchdog = null;
      }
      if (payload.meta) {
        meta = payload.meta;
        typingWrap.remove();
        const built = buildAnswerBubble(meta);
        bubble = built.bubble;
        answerText = built.answerText;
        cursor = document.createElement("span");
        cursor.className = "type-cursor";
        cursor.textContent = "▌"; // 流式期间的闪烁光标字符
        scrollToBottom();
        return;
      }
      if (typeof payload.text === "string") {
        buf += payload.text;
        renderStreamed(false);
        return;
      }
      if (payload.sources) {
        sources = payload.sources;
      }
    }

    try {
      // 看门狗：45 秒内未收到任何事件（LLM 挂起/网络黑洞）→ 中止并提示超时；
      // 正常流一旦开始输出即解除，长回答的持续流式不受影响
      watchdog = setTimeout(() => {
        timedOut = true;
        if (currentController) currentController.abort();
      }, 45000);
      const resp = await fetch("/ask_stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: text, history }),
        signal: currentController.signal,
      });
      if (!resp.ok) {
        // 透传后端 detail（如「问题太长，请精简后提问」「提问太频繁啦…」），而非干巴巴的状态码
        throw new Error(await extractDetail(resp, "服务错误 (" + resp.status + ")"));
      }

      await consumeSseStream(resp.body.getReader(), onEvent);
    } catch (err) {
      if (err && err.name === "AbortError") {
        if (timedOut) {
          // 看门狗触发的中止：提示超时，而非「已停止生成」
          errored = true;
          typingWrap.remove();
          addError("服务响应超时，请稍后重试");
        } else {
          stopped = true;
        }
      } else {
        errored = true;
        typingWrap.remove();
        addError((err && err.message) || "网络异常，请稍后重试");
      }
    } finally {
      if (watchdog) {
        clearTimeout(watchdog);
        watchdog = null;
      }
    }

    // ---------- 收尾 ----------
    finalizeAnswer({
      bubble,
      sources,
      stopped,
      errored,
      typingWrap,
      buf,
      finalRender: () => renderStreamed(true),
    });
    // 记录助手回答（含被停止的部分内容），供后续轮次引用与会话恢复；
    // 额外保存 refused/clarify 标志与来源（text 截断防 localStorage 膨胀）
    if (buf) {
      messages.push({
        role: "assistant",
        content: buf,
        t: nowTime(),
        refused: !!(meta && meta.refused),
        clarify: !!(meta && meta.clarify),
        sources: sources && sources.length
          ? sources.map((s) => ({ source: s.source, text: String(s.text || "").slice(0, 120) }))
          : undefined,
      });
    }
    persistCurrent(); // 完成后同步 localStorage

    setBusy(false);
    currentController = null;
  }

  function sendCurrent() {
    const text = input.value.trim();
    if (!text || busy) return;
    input.value = "";
    autoGrow();
    ask(text);
  }

  // textarea 自适应高度
  function autoGrow() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 140) + "px";
  }
  input.addEventListener("input", autoGrow);

  // ---------- 事件 ----------
  // 流式期间按钮是「停止」；空闲时是「发送」
  sendBtn.addEventListener("click", () => {
    if (busy) {
      if (currentController) currentController.abort();
      return;
    }
    sendCurrent();
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendCurrent();
    }
  });

  // 快捷问题（事件委托：覆盖侧边栏全部分组）
  document.getElementById("quickList").addEventListener("click", (e) => {
    const btn = e.target.closest(".quick-item");
    if (!btn || busy) return;
    ask(btn.dataset.q);
  });

  // ---------- 知识库管理后台（Admin）----------
  const adminModal = document.getElementById("adminModal");
  const adminLoginView = document.getElementById("adminLogin");
  const adminPanelView = document.getElementById("adminPanel");
  const adminTokenInput = document.getElementById("adminToken");
  const adminLoginBtn = document.getElementById("adminLoginBtn");
  const adminLoginError = document.getElementById("adminLoginError");
  const adminOpenBtn = document.getElementById("adminOpen");
  const adminCloseBtn = document.getElementById("adminClose");
  const dropZone = document.getElementById("dropZone");
  const fileInput = document.getElementById("fileInput");
  const docList = document.getElementById("docList");
  const docCount = document.getElementById("docCount");
  const toastEl = document.getElementById("toast");

  let adminToken = ""; // 会话内保存，刷新即清空
  let toastTimer = null;

  function showToast(msg, ok, duration) {
    toastEl.textContent = msg;
    toastEl.classList.toggle("is-error", !ok);
    toastEl.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      toastEl.hidden = true;
    }, duration || 2800);
  }

  function renderDocList(docs) {
    docCount.textContent = docs.length;
    docList.innerHTML = "";
    docs.forEach((d) => {
      const li = document.createElement("li");
      li.className = "doc-item";
      const name = fileName(d).replace(/\.md$/i, "");
      const isUpload = String(d).replace(/\\/g, "/").includes("/uploads/");

      const icon = document.createElement("span");
      icon.className = "doc-icon";
      icon.innerHTML =
        '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/></svg>';

      const text = document.createElement("span");
      text.className = "doc-name";
      text.textContent = "《" + name + "》";

      const badge = document.createElement("span");
      badge.className = "doc-badge" + (isUpload ? " doc-badge-upload" : "");
      badge.textContent = isUpload ? "上传" : "内置";

      li.appendChild(icon);
      li.appendChild(text);
      li.appendChild(badge);
      docList.appendChild(li);
    });
  }

  async function refreshDocs() {
    try {
      const resp = await fetch("/documents", { headers: { "X-Admin-Token": adminToken } });
      if (!resp.ok) throw new Error();
      const data = await resp.json();
      renderDocList(data.documents || []);
    } catch (e) {
      showToast("文档列表加载失败", false);
    }
  }

  function openAdmin() {
    adminModal.hidden = false;
    document.body.classList.add("modal-open");
    if (adminToken) {
      // 会话内已验证：直接进管理界面
      adminLoginView.hidden = true;
      adminPanelView.hidden = false;
      refreshDocs();
    } else {
      adminLoginView.hidden = false;
      adminPanelView.hidden = true;
      adminLoginError.hidden = true;
      setTimeout(() => adminTokenInput.focus(), 60);
    }
  }

  function closeAdmin() {
    adminModal.hidden = true;
    document.body.classList.remove("modal-open");
  }

  // 登录 = 用 token 调 GET /documents：403 即密码错误，200 即通过并顺带拿到列表
  async function tryLogin() {
    const token = adminTokenInput.value.trim();
    if (!token) return;
    adminLoginBtn.disabled = true;
    try {
      const resp = await fetch("/documents", { headers: { "X-Admin-Token": token } });
      if (resp.status === 403) {
        adminLoginError.hidden = false;
        return;
      }
      if (!resp.ok) throw new Error();
      adminToken = token;
      const data = await resp.json();
      adminLoginView.hidden = true;
      adminPanelView.hidden = false;
      renderDocList(data.documents || []);
    } catch (e) {
      adminLoginError.hidden = false;
    } finally {
      adminLoginBtn.disabled = false;
    }
  }

  // 批量上传：一次选择/拖入多个文件，循环调用 /upload，带进度与汇总提示
  let uploading = false;

  async function uploadFiles(fileList) {
    if (uploading) return; // 批次锁：上一批未完成时忽略新批次
    const files = Array.from(fileList || []).filter((f) => /\.(md|txt)$/i.test(f.name));
    if (!files.length) {
      showToast("仅支持 .md / .txt 文件", false);
      return;
    }
    uploading = true;
    let okCount = 0;
    let failCount = 0;
    let totalChunks = 0;
    let firstFailMsg = "";
    try {
      for (let i = 0; i < files.length; i++) {
        const file = files[i];
        const shortName = file.name.replace(/\.(md|txt)$/i, "");
        showToast("正在上传 " + (i + 1) + "/" + files.length + "：" + shortName, true, 6000);
        try {
          const content = await file.text();
          const resp = await fetch("/upload", {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-Admin-Token": adminToken },
            body: JSON.stringify({ filename: file.name, content }),
          });
          const data = await resp.json().catch(() => ({}));
          if (resp.ok) {
            okCount++;
            totalChunks += data.added || 0;
          } else {
            failCount++;
            if (!firstFailMsg) firstFailMsg = data.detail || "上传失败";
          }
        } catch (e) {
          failCount++;
          if (!firstFailMsg) firstFailMsg = "网络异常";
        }
      }
      if (failCount === 0) {
        showToast("✅ 批量入库成功：" + okCount + " 个文件 / " + totalChunks + " 块", true, 4000);
      } else if (okCount === 0) {
        showToast("❌ 批量上传失败：" + firstFailMsg, false, 4000);
      } else {
        showToast("⚠️ 部分成功：成功 " + okCount + " / 失败 " + failCount + "（" + firstFailMsg + "）", false, 5000);
      }
      if (okCount > 0) refreshDocs();
    } finally {
      uploading = false;
    }
  }

  adminOpenBtn.addEventListener("click", openAdmin);
  adminCloseBtn.addEventListener("click", closeAdmin);
  adminModal.addEventListener("click", (e) => {
    if (e.target === adminModal) closeAdmin();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !adminModal.hidden) closeAdmin();
  });
  adminLoginBtn.addEventListener("click", tryLogin);
  adminTokenInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") tryLogin();
  });

  // 上传区：点击选择 / 拖拽
  dropZone.addEventListener("click", () => fileInput.click());
  dropZone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      fileInput.click();
    }
  });
  fileInput.addEventListener("change", () => {
    uploadFiles(fileInput.files);
    fileInput.value = "";
  });
  ["dragenter", "dragover"].forEach((ev) =>
    dropZone.addEventListener(ev, (e) => {
      e.preventDefault();
      dropZone.classList.add("is-drag");
    })
  );
  ["dragleave", "drop"].forEach((ev) =>
    dropZone.addEventListener(ev, (e) => {
      e.preventDefault();
      dropZone.classList.remove("is-drag");
    })
  );
  dropZone.addEventListener("drop", (e) => {
    const files = e.dataTransfer && e.dataTransfer.files;
    uploadFiles(files);
  });

  // ---------- 初始化：恢复最近会话，或开启新对话 ----------
  sessions = loadSessions();
  sessions.sort((a, b) => b.updatedAt - a.updatedAt);
  if (sessions.length) {
    const latest = sessions[0];
    currentSessionId = latest.sessionId;
    messages = latest.messages;
    renderHistory();
    chat.scrollTop = chat.scrollHeight;
  } else {
    currentSessionId = genId();
    messages = [];
    addWelcome();
  }
  renderSessions();
  input.focus();
})();
