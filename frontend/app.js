(() => {
  "use strict";

  const chat = document.getElementById("chat");
  const input = document.getElementById("input");
  const sendBtn = document.getElementById("send");
  const btnLabel = sendBtn.querySelector(".btn-label");

  let busy = false;
  // 多轮上下文：只保留最近 3 轮（用户/助手各 3 条），刷新清空
  let messages = [];

  // ---------- 打字机参数 ----------
  const TYPE_SPEED_MS = 18;      // 每字 18ms ≈ 55 字/秒（略快于感知阈值，更跟手）
  const PUNCT_PAUSE_MS = 110;    // 句读处停顿，更像真人说话
  const PUNCT = "。！？!?\n；;，,、—…";

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

  // 平滑滚动：新消息出现时无条件到底；打字机推进时仅在用户位于底部附近才跟随，
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

  // ---------- 打字机 ----------
  // 逐字推进，每次用「原始文本前缀」重新走一遍白名单渲染，保证 **加粗** / 换行
  // 在标记闭合前后都显示正确；光标跟随到最后一个字，结束后移除
  function typeInto(el, text, onDone) {
    let i = 0;
    const chunk = text.length > 240 ? 2 : 1; // 长回答每次多走一字，避免等太久
    const cursor = document.createElement("span");
    cursor.className = "type-cursor";
    function step() {
      i += chunk;
      el.innerHTML = renderAnswer(text.slice(0, i));
      el.appendChild(cursor);
      followScroll();
      if (i >= text.length) {
        el.innerHTML = renderAnswer(text); // 最终完整渲染，移除光标
        if (onDone) onDone();
        return;
      }
      const pause = PUNCT.includes(text[i - 1]) ? PUNCT_PAUSE_MS : TYPE_SPEED_MS;
      setTimeout(step, pause);
    }
    step();
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

  // onTyped：打字机播完后的回调（用于解锁输入框）
  function addAssistant(data, onTyped) {
    const { wrap, body } = makeMsg("assistant");
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    if (data.refused || data.clarify) {
      bubble.classList.add(data.refused ? "is-refused" : "is-clarify", "has-flag");
      const flag = document.createElement("span");
      flag.className = "flag " + (data.refused ? "flag-refused" : "flag-clarify");
      flag.textContent = data.refused ? "\u26A0\uFE0F 已拦截" : "\u2753 需要补充信息";
      bubble.appendChild(flag);
    }

    const answerText = document.createElement("div");
    answerText.className = "answer-text";
    bubble.appendChild(answerText);
    body.appendChild(bubble);
    chat.appendChild(wrap);
    scrollToBottom();

    // 记录助手回答（refuse/clarify 也记录，供后续轮次引用）
    messages.push({ role: "assistant", content: data.answer || "" });

    // 打字机输出，播完后追加来源标签并回调
    typeInto(answerText, data.answer || "", () => {
      appendSources(bubble, data.sources);
      if (onTyped) onTyped();
    });
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

  // ---------- 请求 ----------
  function lockInput() {
    busy = true;
    sendBtn.disabled = true;
    sendBtn.classList.add("is-loading");
    input.disabled = true;
  }
  function unlockInput() {
    busy = false;
    sendBtn.disabled = false;
    sendBtn.classList.remove("is-loading");
    input.disabled = false;
    input.focus();
  }

  async function ask(text) {
    lockInput();

    addUser(text);
    const typingWrap = addTyping();

    // 携带最近 3 轮历史（最多 6 条），排除刚加入的当前问题（它在 question 字段里）
    const history = messages.slice(0, -1).slice(-6);

    try {
      const resp = await fetch("/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: text, history }),
      });
      if (!resp.ok) throw new Error("服务错误 (" + resp.status + ")");
      const data = await resp.json();
      typingWrap.remove();
      // 打字机播完后再解锁输入框，避免打字未完就发送下一条
      addAssistant(data, unlockInput);
    } catch (err) {
      typingWrap.remove();
      addError((err && err.message) || "网络异常，请稍后重试");
      unlockInput();
    }
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
  sendBtn.addEventListener("click", sendCurrent);
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
