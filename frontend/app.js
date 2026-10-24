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

  // 平滑滚动：新消息出现时无条件到底；流式推进时仅在用户位于底部附近才跟随，
  // 避免用户上翻查看历史时被不断拽回底部
  function isNearBottom() {
    return chat.scrollHeight - chat.scrollTop - chat.clientHeight < 120;
  }
  function scrollToBottom() {
    chat.scrollTo({ top: chat.scrollHeight, behavior: "smooth" });
  }
  function followScroll() {
    if (isNearBottom()) scrollToBottom();
  }

  // ---------- 轻量富文本渲染（防注入） ----------
  // 先转义全部 HTML，再把受控标记转成白名单标签，最后清理任何残留的非白名单标签
  function renderAnswer(text) {
    const src = String(text || "");
    // 1) 先转义危险字符
    const escaped = src
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
    // 2) ``**文本**`` → <strong>…</strong>
    let out = escaped.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    // 3) \n → <br>
    out = out.replace(/\n/g, "<br>");
    // 4) 清理白名单之外的残留标签（只允许 <strong> 与 <br>）
    out = out.replace(/<\/?(?!strong|br|\/strong|\/br)[a-zA-Z][^>]*>/g, "");
    return out;
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
      const { wrap, body } = makeMsg(m.role, m.t);
      const bubble = document.createElement("div");
      bubble.className = "bubble";
      if (m.role === "user") {
        bubble.textContent = m.content;
      } else {
        if (m.refused || m.clarify) {
          bubble.classList.add(m.refused ? "is-refused" : "is-clarify", "has-flag");
          const flag = document.createElement("span");
          flag.className = "flag " + (m.refused ? "flag-refused" : "flag-clarify");
          flag.textContent = m.refused ? "\u26A0\uFE0F 已拦截" : "\u2753 需要补充信息";
          bubble.appendChild(flag);
        }
        const answerText = document.createElement("div");
        answerText.className = "answer-text";
        answerText.innerHTML = renderAnswer(m.content);
        bubble.appendChild(answerText);
        if (m.sources && m.sources.length) appendSources(bubble, m.sources);
      }
      body.appendChild(bubble);
      chat.appendChild(wrap);
    });
    if (!messages.length) addWelcome();
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
  async function ask(text) {
    busy = true;
    currentController = new AbortController();
    input.disabled = true;
    // 发送按钮切换为「停止」
    sendBtn.classList.add("is-stop");
    sendBtn.classList.remove("is-loading");
    btnLabel.textContent = "停止";
    sendBtn.setAttribute("aria-label", "停止生成");

    addUser(text);
    persistCurrent(); // 用户消息实时入 localStorage
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
        const m = makeMsg("assistant");
        bubble = document.createElement("div");
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
        answerText = document.createElement("div");
        answerText.className = "answer-text";
        bubble.appendChild(answerText);
        m.body.appendChild(bubble);
        chat.appendChild(m.wrap);
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
      if (!resp.ok) throw new Error("服务错误 (" + resp.status + ")");

      const reader = resp.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let sseBuf = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        sseBuf += decoder.decode(value, { stream: true });
        // SSE 事件以空行分隔；一条事件内可能有多个 data: 行
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
    if (!bubble && stopped) {
      // 首个事件还没到就点了停止：仍给一个气泡承载「已停止」提示
      typingWrap.remove();
      const m = makeMsg("assistant");
      bubble = document.createElement("div");
      bubble.className = "bubble";
      m.body.appendChild(bubble);
      chat.appendChild(m.wrap);
    }
    if (bubble) {
      renderStreamed(true); // 终帧：完整渲染并移除光标
      if (sources && sources.length) appendSources(bubble, sources);
      if (stopped) {
        const note = document.createElement("div");
        note.className = "stopped-note";
        note.textContent = "\u23F9 已停止生成";
        bubble.appendChild(note);
      }
    } else if (!stopped && !errored) {
      // 流正常结束却连 meta 都没收到（如服务端中途断流）
      typingWrap.remove();
      addError("未收到任何内容，连接可能中断，请重试");
    }
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

    busy = false;
    currentController = null;
    input.disabled = false;
    sendBtn.classList.remove("is-stop");
    btnLabel.textContent = "发送";
    sendBtn.setAttribute("aria-label", "发送");
    input.focus();
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

  function showToast(msg, ok) {
    toastEl.textContent = msg;
    toastEl.classList.toggle("is-error", !ok);
    toastEl.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      toastEl.hidden = true;
    }, 2800);
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

  async function uploadFile(file) {
    if (!file) return;
    const lower = file.name.toLowerCase();
    if (!lower.endsWith(".md") && !lower.endsWith(".txt")) {
      showToast("仅支持 .md / .txt 文件", false);
      return;
    }
    let content;
    try {
      content = await file.text();
    } catch (e) {
      showToast("文件读取失败", false);
      return;
    }
    try {
      const resp = await fetch("/upload", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Admin-Token": adminToken },
        body: JSON.stringify({ filename: file.name, content }),
      });
      const data = await resp.json().catch(() => ({}));
      if (resp.ok) {
        showToast("✅ 《" + file.name.replace(/\.md$/i, "") + "》入库成功（" + data.added + " 块）", true);
        refreshDocs();
      } else {
        showToast("❌ " + (data.detail || "上传失败"), false);
      }
    } catch (e) {
      showToast("上传失败：网络异常", false);
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
    uploadFile(fileInput.files[0]);
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
    const file = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    uploadFile(file);
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
