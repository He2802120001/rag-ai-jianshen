(() => {
  "use strict";

  const chat = document.getElementById("chat");
  const input = document.getElementById("input");
  const sendBtn = document.getElementById("send");
  const btnLabel = sendBtn.querySelector(".btn-label");

  let busy = false;
  let currentController = null; // 当前流式请求的 AbortController
  // 多轮上下文：只保留最近 3 轮（用户/助手各 3 条），刷新清空
  let messages = [];

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
  function makeMsg(role) {
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
    time.textContent = nowTime();
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
    messages.push({ role: "user", content: text });
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
      // 去掉 .md 后缀仅剩文档名
      const name = fileName(s.source).replace(/\.md$/i, "");
      if (!name || seen.has(name)) return;
      seen.add(name);
      const tag = document.createElement("span");
      tag.className = "src-tag";
      tag.textContent = name; // 📄 图标由 CSS ::before 追加
      // title = source 的原文内容（取前 100 字），悬停可看参考了什么
      const raw = (s.text || "").trim();
      tag.title = raw ? (raw.length > 100 ? raw.slice(0, 100) + "…" : raw) : s.source || "";
      src.appendChild(tag);
    });
    bubble.appendChild(src);
  }

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
    const typingWrap = addTyping();
    // 携带最近 3 轮历史（最多 6 条），排除刚加入的当前问题（它在 question 字段里）
    const history = messages.slice(0, -1).slice(-6);

    let meta = null;        // 首个事件：分类与标志位
    let buf = "";           // 累积的回答文本
    let sources = [];       // 收尾事件：引用来源
    let bubble = null;      // AI 气泡（收到 meta 时创建）
    let answerText = null;  // 气泡内文本节点
    let cursor = null;      // 流式期间的闪烁光标
    let stopped = false;    // 用户主动停止
    let errored = false;
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
        answerText = document.createElement("div");
        answerText.className = "answer-text";
        bubble.appendChild(answerText);
        m.body.appendChild(bubble);
        chat.appendChild(m.wrap);
        cursor = document.createElement("span");
        cursor.className = "type-cursor";
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
        stopped = true;
      } else {
        errored = true;
        typingWrap.remove();
        addError((err && err.message) || "网络异常，请稍后重试");
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
    // 记录助手回答（含被停止的部分内容），供后续轮次引用
    if (buf) messages.push({ role: "assistant", content: buf });

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

  // ---------- 初始化 ----------
  addWelcome();
  input.focus();
})();
