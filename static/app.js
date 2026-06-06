const state = {
  sessionId: localStorage.getItem("km.sessionId"),
  currentNodeId: localStorage.getItem("km.currentNodeId"),
  nodes: [],
  messages: [],
  sending: false,
  zoom: Number(localStorage.getItem("km.zoom") || 88),
  panX: Number(localStorage.getItem("km.panX") || 0),
  panY: Number(localStorage.getItem("km.panY") || 0),
  hasViewport: localStorage.getItem("km.panX") !== null,
  isPanning: false,
  visited: new Set(JSON.parse(localStorage.getItem("km.visitedNodes") || "[]")),
  hideUnvisited: localStorage.getItem("km.hideUnvisited") === "true",
  nodeComposer: null,
  chainPanel: null,
  generatingNodeId: null,
  generatingTree: false,
  newNodeIds: new Set(),
  newNodeEnterDelay: new Map(),
  sidebarCollapsed: localStorage.getItem("km.sidebarCollapsed") === "true",
  sessions: [],
  sessionSearch: "",
  theme: localStorage.getItem("km.theme") || "dark",
  mode: localStorage.getItem("km.mode") || "Lite",
  // 学习模式现在只有教练模式,前端固定 true,旧的 localStorage 值忽略
  coachMode: true,
  // 速览卡片栈:支持嵌套(在 peek answer 里再划词,生成子 peek 叠在上面)。
  // 每项 = {messageId, peekId}。栈顶 = 当前最深一层。
  // 关闭某张 = 移除它 + 所有后代(后入栈的);点击栈外 = 关全部。
  peekStack: [],
  subdividePopoverNodeId: null,
  pendingQuote: "", // 输入框上方"引用"气泡的内容,发送时拼到消息前面
  openThoughtToolsFor: null,
};

const searchSourceTickerTimers = new Set();
const deepSearchLoading = new Set();
const deepReanswerLoading = new Set();
const hiddenThoughtActionLabels = new Set(["继续深入", "我懂了", "跳过", "画细分地图", "回到上一级"]);
let tooltipTimer = null;
let tooltipTarget = null;
let tooltipEl = null;

const STAGE_WIDTH = 4200;
const STAGE_HEIGHT = 3200;
const CENTER_X = STAGE_WIDTH / 2;
const ROOT_Y = STAGE_HEIGHT - 520;
const CARD_W = 230;
const MAIN_W = 250;
const NODE_H = 148;
const ROW_GAP = 34;
const LEVEL_GAP = 370;
const MAIN_GAP = 96;

const $ = (selector) => document.querySelector(selector);

const starter = $("#starter");
const workspace = $("#workspace");
const messagesEl = $("#messages");
const treeEl = $("#tree");
const treeTitleEl = $("#tree-title");
const currentNodeEl = $("#current-node");
const breadcrumbEl = $("#map-breadcrumb");
const progressEl = $("#progress");
const chatInput = $("#chat-input");
const sendButton = $("#send-button");
const zoomRange = $("#zoom-range");
const appShell = $(".app-shell");
const splitter = $("#splitter");
const toggleVisitedButton = $("#toggle-visited");
const nodeSearchForm = $("#node-search-form");
const nodeSearchInput = $("#node-search-input");
const nodeSearchResults = $("#node-search-results");
const sessionList = $("#session-list");
const sessionSearch = $("#session-search");
const userMenu = $("#user-menu");
const avatarButton = $("#avatar-button");
const themeToggle = $("#theme-toggle");
const sidebarToggle = $("#sidebar-toggle");
const modeButton = $("#mode-button");
const modeLabel = $("#mode-label");
const modePopover = $("#mode-popover");
const modeHelp = $("#mode-help");
const selectionMenu = $("#selection-menu");
const backgroundQuiz = $("#background-quiz");
const topicPreviewEl = $("#topic-preview");

document.body.classList.toggle("light-theme", state.theme === "light");
appShell.classList.toggle("sidebar-collapsed", state.sidebarCollapsed);
setMode(state.mode, false);
setCoachMode(state.coachMode, false);
zoomRange.value = String(state.zoom);
toggleVisitedButton.setAttribute("aria-pressed", String(state.hideUnvisited));
toggleVisitedButton.classList.toggle("active", state.hideUnvisited);
const savedChatWidth = localStorage.getItem("km.chatWidth");
if (savedChatWidth) appShell.style.setProperty("--chat-width", savedChatWidth);

hydrateStaticTooltips();
installTooltipSystem();

function hydrateStaticTooltips() {
  const entries = [
    ["#sidebar-toggle", "收起或打开左侧边栏"],
    ["#new-session", "新建一张学习地图"],
    ["#theme-toggle", "切换深色 / 浅色主题"],
    ["#avatar-button", "打开用户菜单"],
    ["#mode-button", "切换思维深度"],
    [".primary-button[type='submit']", "根据你的目标生成知识地图"],
    ["#chat-quote-clear", "移除当前引用"],
    ["#send-button", "发送问题"],
    [".map-open", "打开当前节点详情"],
    ["#zoom-out", "缩小知识树画布"],
    ["#zoom-in", "放大知识树画布"],
    ["#zoom-reset", "重置画布缩放和位置"],
    ["#toggle-visited", "只显示已经走过的分支"],
    ["[data-user-action='settings']", "打开设置"],
    ["[data-user-action='profile']", "查看账户"],
    ["[data-user-action='logout']", "退出当前账户"],
    ["[data-selection-action='highlight']", "把选中的文字标成高亮"],
    ["[data-selection-action='explain']", "围绕选中的词生成一段轻量解释，并固定成可继续追问的速览卡片"],
    ["[data-selection-action='quote']", "把选中文字带到输入框里，下一次提问会附带这段引用"],
    ["[data-mode='Lite']", "轻量模式：节点少、解释短，适合快速建立框架"],
    ["[data-mode='Medium']", "中等模式：拆分和解释更完整，适合正常学习"],
    ["[data-mode='Zen']", "深度模式：节点更细、回答更充分，适合系统钻研"],
  ];
  entries.forEach(([selector, text]) => {
    document.querySelectorAll(selector).forEach((element) => {
      if (!element.dataset.tooltip) element.dataset.tooltip = text;
    });
  });
}

function installTooltipSystem() {
  document.addEventListener("pointerover", scheduleTooltipFromEvent, true);
  document.addEventListener("pointerout", hideTooltipFromEvent, true);
  document.addEventListener("focusin", scheduleTooltipFromEvent, true);
  document.addEventListener("focusout", hideTooltipFromEvent, true);
  document.addEventListener("pointerdown", hideTooltip);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") hideTooltip();
  });
  window.addEventListener("scroll", hideTooltip, true);
  window.addEventListener("resize", hideTooltip);
}

function tooltipTargetFromEvent(event) {
  const target = event.target?.closest?.(
    "button, [role='button'], input[type='range'], .map-breadcrumb-segment, .recommend-dots"
  );
  if (!target || target.disabled || target.getAttribute("aria-disabled") === "true") return null;
  return target;
}

function scheduleTooltipFromEvent(event) {
  const target = tooltipTargetFromEvent(event);
  if (!target) return;
  if (event.type === "pointerover" && event.relatedTarget && target.contains(event.relatedTarget)) return;
  tooltipTarget = target;
  const nativeTitle = target.getAttribute("title");
  if (nativeTitle) {
    target.dataset.nativeTitle = nativeTitle;
    target.removeAttribute("title");
  }
  clearTimeout(tooltipTimer);
  tooltipTimer = setTimeout(() => showTooltip(target), 500);
}

function hideTooltipFromEvent(event) {
  const target = tooltipTargetFromEvent(event);
  if (target && event.type === "pointerout" && event.relatedTarget && target.contains(event.relatedTarget)) return;
  hideTooltip();
}

function tooltipTextFor(target) {
  const raw = target.dataset.tooltip
    || target.dataset.nativeTitle
    || target.getAttribute("aria-label")
    || target.textContent
    || "";
  return raw.replace(/\s+/g, " ").trim();
}

function showTooltip(target) {
  if (!target || !document.body.contains(target)) return;
  const text = tooltipTextFor(target);
  if (!text) return;
  if (!tooltipEl) {
    tooltipEl = document.createElement("div");
    tooltipEl.className = "ui-tooltip";
    tooltipEl.setAttribute("role", "tooltip");
    document.body.append(tooltipEl);
  }
  tooltipEl.textContent = text;
  tooltipEl.classList.remove("visible");
  positionTooltip(target);
  requestAnimationFrame(() => {
    if (tooltipTarget === target) tooltipEl?.classList.add("visible");
  });
}

function positionTooltip(target) {
  if (!tooltipEl) return;
  const rect = target.getBoundingClientRect();
  const tooltipRect = tooltipEl.getBoundingClientRect();
  const gap = 10;
  const margin = 8;
  const topSpace = rect.top;
  const preferTop = topSpace > tooltipRect.height + gap + margin;
  let top = preferTop ? rect.top - tooltipRect.height - gap : rect.bottom + gap;
  let left = rect.left + rect.width / 2 - tooltipRect.width / 2;
  left = Math.max(margin, Math.min(left, window.innerWidth - tooltipRect.width - margin));
  top = Math.max(margin, Math.min(top, window.innerHeight - tooltipRect.height - margin));
  tooltipEl.style.left = `${Math.round(left)}px`;
  tooltipEl.style.top = `${Math.round(top)}px`;
  tooltipEl.dataset.placement = preferTop ? "top" : "bottom";
}

function hideTooltip() {
  clearTimeout(tooltipTimer);
  tooltipTimer = null;
  if (tooltipTarget?.dataset.nativeTitle) {
    tooltipTarget.setAttribute("title", tooltipTarget.dataset.nativeTitle);
    delete tooltipTarget.dataset.nativeTitle;
  }
  tooltipTarget = null;
  if (tooltipEl) tooltipEl.classList.remove("visible");
}

// 像 iOS 那样:卡片上 pointerdown 也开始 pan 追踪,移动 ≥ 5px 才算拖,
// 否则当成普通点击(进入节点)。这样用户不用刻意躲卡片找空白处拖画布。
//
// 注意:不能在 pointerdown 时立刻 setPointerCapture——那会把后续 click 也吸到
// treeEl 上,卡片自己的 click handler 就收不到了,节点点击直接失效。
// 改用 document 级的 pointermove/pointerup,正常 hit-test,click 自然派到卡片。
const DRAG_THRESHOLD = 5;
treeEl.addEventListener("pointerdown", (event) => {
  if (event.button !== 0) return;
  // 卡片里这些子控件该走原生点击(拆分按钮、推荐点 hover 等),不抢成 pan
  if (event.target.closest(".node-toolbar, .recommend-dots, .node-subdivide-btn")) return;
  state.isPanning = true;
  state.didDrag = false;
  state.dragStartX = event.clientX;
  state.dragStartY = event.clientY;
  state.dragOriginX = state.panX;
  state.dragOriginY = state.panY;
});

document.addEventListener("pointermove", (event) => {
  if (!state.isPanning) return;
  const dx = event.clientX - state.dragStartX;
  const dy = event.clientY - state.dragStartY;
  // 没越过阈值时按"未拖"处理,避免微小抖动把 panX/Y 推位
  if (!state.didDrag) {
    if (Math.hypot(dx, dy) < DRAG_THRESHOLD) return;
    state.didDrag = true;
    treeEl.classList.add("is-panning");
    if (state.chainPanel) closeChainPanel();
  }
  state.panX = state.dragOriginX + dx;
  state.panY = state.dragOriginY + dy;
  scheduleViewportTransform();
});

document.addEventListener("pointerup", endPan);
document.addEventListener("pointercancel", endPan);

// 拖完后浏览器仍会派一次 click(原始 pointerdown 那张卡片上),
// 这里在捕获阶段消掉,否则用户拖完画布会顺带误开一个节点。
treeEl.addEventListener("click", (event) => {
  if (!state.suppressNextClick) return;
  state.suppressNextClick = false;
  event.stopPropagation();
  event.preventDefault();
}, true);

treeEl.addEventListener(
  "wheel",
  (event) => {
    if (!state.nodes.length) return;
    event.preventDefault();
    const before = state.zoom / 100;
    const nextZoom = Math.max(10, Math.min(180, state.zoom - event.deltaY * 0.08));
    const after = nextZoom / 100;
    const rect = treeEl.getBoundingClientRect();
    const pointerX = event.clientX - rect.left;
    const pointerY = event.clientY - rect.top;
    state.panX = pointerX - ((pointerX - state.panX) / before) * after;
    state.panY = pointerY - ((pointerY - state.panY) / before) * after;
    state.zoom = nextZoom;
    persistViewport();
    applyZoomCosmetics();
    scheduleViewportTransform();
    if (state.chainPanel) closeChainPanel();
  },
  { passive: false }
);

splitter.addEventListener("pointerdown", (event) => {
  state.resizingPanels = true;
  splitter.setPointerCapture(event.pointerId);
  appShell.classList.add("resizing");
});

splitter.addEventListener("pointermove", (event) => {
  if (!state.resizingPanels) return;
  const railWidth = state.sidebarCollapsed ? 56 : 300;
  const splitterWidth = 8;
  const minChat = 330;
  const minMap = 360;
  const total = window.innerWidth;
  const next = Math.max(minChat, Math.min(event.clientX - railWidth, total - railWidth - splitterWidth - minMap));
  const value = `${Math.round(next)}px`;
  appShell.style.setProperty("--chat-width", value);
  state.pendingChatWidth = value;
});

splitter.addEventListener("pointerup", endPanelResize);
splitter.addEventListener("pointercancel", endPanelResize);

document.addEventListener("pointerdown", (event) => {
  // 点击任何 peek-popover / peek-anchor / 选区菜单 / 高亮菜单 之外的区域
  // → 关闭整个 peek 栈。
  // 选区菜单和高亮菜单虽然在 document.body 上(不在 popover 里),但它们是 peek
  // 流程的一部分(用户正在选词准备开新 peek),点它们绝对不能关掉父 peek。
  if (
    state.peekStack.length &&
    !event.target.closest(".peek-popover") &&
    !event.target.closest(".peek-anchor") &&
    !event.target.closest("#selection-menu") &&
    !event.target.closest(".highlight-menu") &&
    !event.target.closest(".search-sources-popover")
  ) {
    closeAllPeekPopovers();
  }
  if (!modePopover.classList.contains("hidden") && !event.target.closest("#mode-popover") && !event.target.closest("#mode-button")) {
    closeModePopover();
  }
  if (!userMenu.classList.contains("hidden") && !event.target.closest("#user-menu") && !event.target.closest("#avatar-button")) {
    userMenu.classList.add("hidden");
  }
  if (state.chainPanel && !event.target.closest(".chain-panel") && !event.target.closest("[data-depth-chip]")) {
    closeChainPanel();
  }
  if (!selectionMenu.classList.contains("hidden") && !event.target.closest("#selection-menu")) {
    // 点了菜单外面,关闭(具体动作在 click handler 里)
    // 这里只是预判,真正隐藏交给 selectionchange/mouseup
  }
  if (!state.nodeComposer) return;
  if (event.target.closest(".node-query") || event.target.closest(".map-node")) return;
  closeNodeComposer();
});

// === 滚动性能护栏 ===
// composer 是 absolute 漂在 messages 上,有 backdrop-filter blur(26px)。滚动时
// 内容从它下面溜过去 → GPU 每帧重算高斯模糊。和拖动画布同病,同药方:
//   - 滚动期间给 .workspace 加 .is-scrolling,CSS 顺势把 composer 的 blur 关掉
//   - 停滚 120ms 后撤掉 class,blur 立刻回来
// 用 passive listener,不阻塞滚动主线程。
{
  const workspaceEl = workspace;
  let scrollEndTimer = null;
  let rafPending = false;
  const SCROLL_END_MS = 120;

  const markScrolling = () => {
    if (!workspaceEl.classList.contains("is-scrolling")) {
      workspaceEl.classList.add("is-scrolling");
    }
    clearTimeout(scrollEndTimer);
    scrollEndTimer = setTimeout(() => {
      workspaceEl.classList.remove("is-scrolling");
    }, SCROLL_END_MS);
  };

  messagesEl.addEventListener(
    "scroll",
    () => {
      // RAF coalesce:scroll 事件可能高频触发,合并到下一帧再处理
      if (rafPending) return;
      rafPending = true;
      requestAnimationFrame(() => {
        rafPending = false;
        markScrolling();
      });
    },
    { passive: true },
  );
}

// === 划词菜单 ===
messagesEl.addEventListener("mouseup", () => {
  setTimeout(maybeShowSelectionMenu, 0);
});
messagesEl.addEventListener("keyup", (event) => {
  if (event.shiftKey || event.key.startsWith("Arrow")) maybeShowSelectionMenu();
});
document.addEventListener("selectionchange", () => {
  const sel = window.getSelection();
  if (!sel || !sel.toString().trim()) hideSelectionMenu();
});
window.addEventListener("scroll", hideSelectionMenu, true);
window.addEventListener("resize", hideSelectionMenu);

selectionMenu.addEventListener("mousedown", (event) => {
  // 防止 mousedown 在按钮上时把 selection 清掉
  event.preventDefault();
});

selectionMenu.querySelectorAll("[data-selection-action]").forEach((button) => {
  button.addEventListener("click", async (event) => {
    event.preventDefault();
    const action = button.dataset.selectionAction;
    const ctx = state.selectionContext;
    if (!ctx) return;
    hideSelectionMenu();
    if (action === "highlight") {
      // 高亮锚在消息正文 offset 上,嵌套 peek 里的选区 offset 是相对父 answer 的,
      // 存进 message.highlights 会错位 → 在 peek 卡里禁用 highlight,只保留 explain/quote
      if (ctx.parentPeekId) {
        return;
      }
      await persistHighlight(ctx);
    } else if (action === "explain") {
      await createPeek(ctx);
    } else if (action === "quote") {
      quoteToChatInput(ctx.text);
    } else if (action === "websearch") {
      await runWebSearchForSelection(ctx);
    }
  });
});

async function runWebSearchForSelection(ctx) {
  const query = (ctx?.text || "").trim();
  if (!query) return;
  // 立即弹一个 loading 占位的 popover,让用户知道点了有反应。
  // openSearchSourcesPopover 会复用现有的来源列表样式,我们用一条 status=searching 的占位 source。
  const placeholder = [
    {
      status: "searching",
      query,
      title: "正在联网搜索…",
      link: "",
      media: "",
      publish_date: "",
      content: `「${query}」`,
      refer: "",
    },
  ];
  openSearchSourcesPopover(placeholder);
  try {
    const response = await fetch("/api/web-search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, limit: 12 }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${response.status}`);
    }
    const data = await response.json();
    const sources = Array.isArray(data?.sources) ? data.sources : [];
    closeSearchSourcesPopover();
    if (!sources.length) {
      openSearchSourcesPopover([
        {
          status: "empty",
          query,
          title: "",
          link: "",
          media: "",
          publish_date: "",
          content: "联网搜索没有返回结果,可以换个关键词再试。",
          refer: "",
        },
      ]);
      return;
    }
    openSearchSourcesPopover(sources);
  } catch (error) {
    closeSearchSourcesPopover();
    openSearchSourcesPopover([
      {
        status: "error",
        query,
        title: "",
        link: "",
        media: "",
        publish_date: "",
        content: `联网搜索失败:${error.message}`,
        refer: "",
      },
    ]);
  }
}

function quoteToChatInput(rawText) {
  // 仿微信:不再把 "> 引用文本" 塞进 textarea,而是显示在输入框上方的气泡里。
  // 发消息时再把它拼到消息前面,发完自动清空。
  const text = (rawText || "").trim();
  if (!text) return;
  state.pendingQuote = text;
  renderPendingQuote();
  // 清掉残留选区,把光标交给输入框
  window.getSelection()?.removeAllRanges?.();
  chatInput.focus();
  chatInput.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

function renderPendingQuote() {
  const wrap = document.getElementById("chat-quote");
  if (!wrap) return;
  const text = (state.pendingQuote || "").trim();
  if (!text) {
    wrap.classList.add("hidden");
    return;
  }
  wrap.classList.remove("hidden");
  const textEl = wrap.querySelector(".chat-quote-text");
  if (textEl) {
    // 展示用,做一下省略;实际拼到消息里时还是用 state.pendingQuote 全文
    const single = text.replace(/\s+/g, " ").trim();
    textEl.textContent = single.length > 160 ? `${single.slice(0, 160)}…` : single;
    textEl.title = text;
  }
}

function clearPendingQuote() {
  state.pendingQuote = "";
  renderPendingQuote();
}

function buildMessageWithPendingQuote(message) {
  const quote = (state.pendingQuote || "").trim();
  if (!quote) return message;
  const blockquote = quote.split(/\n/).map((line) => `> ${line}`).join("\n");
  return `${blockquote}\n\n${message}`;
}

function maybeShowSelectionMenu() {
  const sel = window.getSelection();
  if (!sel || sel.rangeCount === 0) return hideSelectionMenu();
  const text = sel.toString().trim();
  if (!text || text.length > 600) return hideSelectionMenu();
  const range = sel.getRangeAt(0);

  // 选区可能在三个地方:
  //   1. 消息 .bubble 里 → 老逻辑,触发 peek anchored on message.content
  //   2. .peek-popover 的 .peek-answer 里 → 嵌套 peek,parent_peek_id = 当前 popover 的 peek id
  //   3. .peek-popover 的 .peek-followup p 里 → 嵌套 peek,锚在某条追问回答上
  //   4. 其它地方 → 不显示菜单
  const startEl = range.startContainer.parentElement || range.startContainer;
  const peekTextEl = startEl.closest?.(".peek-answer, .peek-followup p");
  const peekPopover = peekTextEl?.closest?.(".peek-popover");
  const bubble = startEl.closest?.(".bubble");

  if (peekTextEl && peekPopover && peekTextEl.contains(range.endContainer)) {
    // 嵌套场景:从父 peek 的 answer / followup answer 上划词
    const parentPeekId = peekPopover.dataset.peekId;
    const messageId = peekPopover.dataset.messageId;
    if (!parentPeekId || !messageId) return hideSelectionMenu();
    const offsets = computeSelectionOffsets(peekTextEl, range);
    if (!offsets) return hideSelectionMenu();
    const followupEl = peekTextEl.closest(".peek-followup");
    const sourceFollowupId = followupEl?.dataset.followupId || null;
    // nodeId 沿用父 peek 所在消息的 nodeId
    const messageEl = messagesEl.querySelector(`[data-message-id="${cssEscape(messageId)}"]`);
    const nodeId = messageEl?.dataset.nodeId || state.currentNodeId || "";
    state.selectionContext = {
      messageId,
      nodeId,
      text,
      start: offsets.start,
      end: offsets.end,
      parentPeekId,
      sourceKind: sourceFollowupId ? "followup" : "answer",
      sourceFollowupId,
    };
    positionSelectionMenu(range.getBoundingClientRect());
    return;
  }

  if (!bubble || !messagesEl.contains(bubble)) return hideSelectionMenu();
  const messageEl = bubble.closest(".message");
  if (!messageEl) return hideSelectionMenu();
  const messageId = messageEl.dataset.messageId;
  const nodeId = messageEl.dataset.nodeId;
  if (!messageId || !nodeId) return hideSelectionMenu();
  if (!bubble.contains(range.endContainer)) return hideSelectionMenu();

  const offsets = computeSelectionOffsets(bubble, range);
  if (!offsets) return hideSelectionMenu();

  state.selectionContext = {
    messageId,
    nodeId,
    text,
    start: offsets.start,
    end: offsets.end,
    parentPeekId: null,
  };

  positionSelectionMenu(range.getBoundingClientRect());
}

function computeSelectionOffsets(bubble, range) {
  try {
    const pre = range.cloneRange();
    pre.selectNodeContents(bubble);
    pre.setEnd(range.startContainer, range.startOffset);
    const start = pre.toString().length;
    const text = range.toString();
    return { start, end: start + text.length };
  } catch (error) {
    return null;
  }
}

function positionSelectionMenu(rect) {
  if (!rect || (rect.width === 0 && rect.height === 0)) return hideSelectionMenu();
  const x = rect.left + rect.width / 2;
  const y = rect.top;
  selectionMenu.style.left = `${Math.round(x)}px`;
  selectionMenu.style.top = `${Math.round(y)}px`;
  selectionMenu.classList.remove("hidden");
  // 触发一次 reflow,让进入动画跑起来
  void selectionMenu.offsetWidth;
  selectionMenu.classList.add("visible");
}

function hideSelectionMenu() {
  if (selectionMenu.classList.contains("hidden")) return;
  selectionMenu.classList.remove("visible");
  selectionMenu.classList.add("hidden");
  state.selectionContext = null;
}

async function persistHighlight(ctx) {
  const message = state.messages.find((m) => m.id === ctx.messageId);
  if (!message) return;
  const content = message.content || "";
  const existing = message.highlights || [];
  const overlapping = existing.filter((h) => h.start < ctx.end && h.end > ctx.start);
  let next;
  if (overlapping.length > 0) {
    // 切割：非重叠的高亮原样保留，重叠的高亮切掉选中区间后把左右两段残留保留下来
    const untouched = existing.filter((h) => !(h.start < ctx.end && h.end > ctx.start));
    const trimmed = [];
    for (const h of overlapping) {
      if (h.start < ctx.start) trimmed.push({ start: h.start, end: ctx.start, text: content.slice(h.start, ctx.start) });
      if (h.end > ctx.end)   trimmed.push({ start: ctx.end,   end: h.end,   text: content.slice(ctx.end, h.end) });
    }
    next = [...untouched, ...trimmed].sort((a, b) => a.start - b.start);
  } else {
    next = [...existing, { start: ctx.start, end: ctx.end, text: ctx.text }];
  }
  // 乐观更新:先在前端展示
  message.highlights = next;
  renderMessages({ preserveScroll: true });
  try {
    const response = await fetch(`/api/messages/${ctx.messageId}/highlights`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ highlights: next }),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const saved = await response.json();
    message.highlights = saved.highlights || [];
    renderMessages({ preserveScroll: true });
  } catch (error) {
    console.warn("save highlight failed", error);
  }
}

// 高亮上的小菜单:目前只有"取消高亮"一个动作
function openHighlightMenu(anchor, messageId, start, end) {
  closeHighlightMenu();
  const menu = document.createElement("div");
  menu.className = "highlight-menu";
  menu.setAttribute("role", "menu");
  menu.innerHTML = `
    <button type="button" class="highlight-menu-cancel">
      <span aria-hidden="true">✕</span>取消高亮
    </button>
  `;
  document.body.append(menu);
  const rect = anchor.getBoundingClientRect();
  const menuWidth = 120;
  const left = Math.max(8, Math.min(window.innerWidth - menuWidth - 8, rect.left + rect.width / 2 - menuWidth / 2));
  const top = Math.max(8, rect.top - 42);
  menu.style.left = `${Math.round(left)}px`;
  menu.style.top = `${Math.round(top)}px`;
  requestAnimationFrame(() => menu.classList.add("visible"));

  menu.querySelector(".highlight-menu-cancel").addEventListener("click", (event) => {
    event.stopPropagation();
    closeHighlightMenu();
    removeHighlight(messageId, start, end);
  });
  // 点其它地方关掉
  setTimeout(() => {
    document.addEventListener("pointerdown", onHighlightMenuOutside, { once: true });
  }, 0);
}

function onHighlightMenuOutside(event) {
  const menu = document.querySelector(".highlight-menu");
  if (menu && !menu.contains(event.target)) closeHighlightMenu();
}

function closeHighlightMenu() {
  document.removeEventListener("pointerdown", onHighlightMenuOutside);
  const menu = document.querySelector(".highlight-menu");
  if (!menu) return;
  menu.classList.remove("visible");
  setTimeout(() => menu.remove(), 120);
}

async function removeHighlight(messageId, start, end) {
  const message = state.messages.find((m) => m.id === messageId);
  if (!message) return;
  const next = (message.highlights || []).filter(
    (h) => !(Number(h.start) === start && Number(h.end) === end),
  );
  message.highlights = next;
  renderMessages({ preserveScroll: true });
  try {
    const response = await fetch(`/api/messages/${messageId}/highlights`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ highlights: next }),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const saved = await response.json();
    message.highlights = saved.highlights || [];
    renderMessages({ preserveScroll: true });
  } catch (error) {
    console.warn("remove highlight failed", error);
  }
}

async function createPeek(ctx) {
  const message = state.messages.find((m) => m.id === ctx.messageId);
  if (!message) return;
  // 嵌套 peek 时 ctx.parentPeekId 不为空,start/end 相对父 peek 的 answer
  const parentPeekId = ctx.parentPeekId || null;
  const sourceKind = ctx.sourceKind || "answer";
  const sourceFollowupId = ctx.sourceFollowupId || null;
  const localId = `local_peek_${Date.now()}`;
  const localPeek = {
    id: localId,
    parent_peek_id: parentPeekId,
    source_kind: sourceKind,
    source_followup_id: sourceFollowupId,
    start: ctx.start,
    end: ctx.end,
    text: ctx.text,
    answer: "正在解释…",
    status: "thinking",
    followups: [],
  };
  message.peeks = upsertPeek(message.peeks || [], localPeek);
  // 根 peek 才需要重新 paint bubble(给 message.content 加 peek-anchor 下划线);
  // 嵌套 peek 不动消息正文,但要刷新【父 popover】的答案区,新 anchor 才会出现在那里
  if (!parentPeekId) {
    renderMessages({ preserveScroll: true });
  } else {
    refreshOpenPeekPopover(ctx.messageId, parentPeekId);
  }
  openPeekPopover(ctx.messageId, localPeek.id);
  try {
    const response = await fetch(`/api/messages/${ctx.messageId}/peeks`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        start: ctx.start,
        end: ctx.end,
        text: ctx.text,
        mode: state.mode,
        parent_peek_id: parentPeekId,
        source_kind: sourceKind,
        source_followup_id: sourceFollowupId,
      }),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const saved = await response.json();
    state.messages = state.messages.map((m) => (m.id === saved.id ? saved : m));
    if (!parentPeekId) {
      renderMessages({ preserveScroll: true });
    } else {
      // 父 popover 需要用 server 返回的真实 id 重画一次,确保 anchor 的 data-peek-id 是真 id
      refreshOpenPeekPopover(ctx.messageId, parentPeekId);
    }
    // 找到刚 server 写库的那个 peek:同 parent + 同 start/end 唯一
    const savedPeek = (saved.peeks || []).find(
      (p) =>
        (p.parent_peek_id || null) === parentPeekId &&
        (p.source_kind || "answer") === sourceKind &&
        (p.source_followup_id || null) === sourceFollowupId &&
        p.start === ctx.start &&
        p.end === ctx.end,
    );
    if (savedPeek) {
      // 用 server id 替换刚才的 local id,栈里如果在用 local id 也要跟着换
      const stackEntry = state.peekStack.find((s) => s.peekId === localId);
      if (stackEntry) stackEntry.peekId = savedPeek.id;
      // 找到 loading 中的 popover,把 data-peek-id 升级到真 id,然后逐字 type 答案
      // (替换掉之前显示的 3 点 loading 动画)
      const popover = document.querySelector(
        `.peek-popover[data-peek-id="${cssEscape(localId)}"]`,
      );
      if (popover) {
        popover.dataset.peekId = savedPeek.id;
        const answerEl = popover.querySelector(".peek-answer");
        if (answerEl) {
          await typeIntoElement(answerEl, savedPeek.answer || "");
        }
      } else {
        // popover 被用户关掉了,不强行恢复(避免突然冒出来)
      }
    }
  } catch (error) {
    localPeek.answer = `解释失败：${error.message}`;
    localPeek.status = "error";
    if (!parentPeekId) renderMessages({ preserveScroll: true });
    openPeekPopover(ctx.messageId, localPeek.id, { replaceLocal: localId });
  }
}

function upsertPeek(peeks, nextPeek) {
  const parentId = nextPeek.parent_peek_id || null;
  const rest = (peeks || []).filter(
    (p) =>
      !(
        (p.parent_peek_id || null) === parentId &&
        p.start === nextPeek.start &&
        p.end === nextPeek.end
      ),
  );
  // 排序:先按 parent 分组(空 parent 排前),组内按 start/end
  return [...rest, nextPeek].sort((a, b) => {
    const pa = a.parent_peek_id || "";
    const pb = b.parent_peek_id || "";
    if (pa !== pb) return pa < pb ? -1 : 1;
    return a.start - b.start || a.end - b.end;
  });
}

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    closeNodeComposer();
    closeModePopover();
    closeChainPanel();
    closePeekPopover();
    userMenu.classList.add("hidden");
  }
});

window.addEventListener("resize", () => {
  if (state.chainPanel) closeChainPanel();
});

modeButton.addEventListener("click", () => {
  if (modePopover.classList.contains("hidden")) {
    openModePopover();
  } else {
    closeModePopover();
  }
});

modePopover.querySelectorAll("[data-mode]").forEach((button) => {
  button.addEventListener("click", () => {
    setMode(button.dataset.mode);
    closeModePopover();
  });
});

sidebarToggle.addEventListener("click", () => setSidebarCollapsed(!state.sidebarCollapsed));

sessionSearch.addEventListener("input", () => {
  state.sessionSearch = sessionSearch.value.trim();
  window.clearTimeout(state.searchTimer);
  state.searchTimer = window.setTimeout(loadSessions, 180);
});

themeToggle.addEventListener("click", () => {
  state.theme = state.theme === "light" ? "dark" : "light";
  localStorage.setItem("km.theme", state.theme);
  document.body.classList.toggle("light-theme", state.theme === "light");
});

avatarButton.addEventListener("click", (event) => {
  event.stopPropagation();
  userMenu.classList.toggle("hidden");
});

userMenu.querySelectorAll("button").forEach((button) => {
  button.addEventListener("click", () => {
    const action = button.dataset.userAction;
    userMenu.classList.add("hidden");
    if (action === "logout") {
      logout();
      return;
    }
    if (action === "settings") {
      openSettingsDrawer();
      return;
    }
    if (action === "change-password") {
      openPasswordDrawer();
      return;
    }
  });
});

$("#start-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const field = $("#field").value.trim();
  const current_problem = $("#problem").value.trim();
  if (!field || !current_problem) {
    event.currentTarget.reportValidity();
    return;
  }
  const form = event.currentTarget;
  const submitBtn = form.querySelector('button[type="submit"]');
  const originalLabel = submitBtn.textContent;
  submitBtn.disabled = true;
  submitBtn.classList.add("generating");
  submitBtn.textContent = "正在出题…";

  try {
    const response = await fetch("/api/sessions/background-questions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ field, current_problem, mode: state.mode }),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    renderBackgroundQuiz({
      field,
      current_problem,
      questions: payload.questions || [],
      form,
      submitBtn,
      originalLabel,
    });
  } catch (error) {
    renderBackgroundQuiz({
      field,
      current_problem,
      questions: fallbackBackgroundQuestions(field, current_problem),
      form,
      submitBtn,
      originalLabel,
      error: error.message,
    });
  }
});

function renderBackgroundQuiz({ field, current_problem, questions, form, submitBtn, originalLabel, error = "" }) {
  // 用一个统一的 quizState 管理多轮问答(避免和全局 state 命名冲突)。
  // roundsAnswered 收集每轮的 {question, answer},currentQuestions 是这一轮还要答完的题。
  // 用户答完一轮 -> 调 followup -> 可能加新题,直到 AI 收手或用户点"够了"。
  const quizState = {
    roundsAnswered: [],         // [{question, answer}, ...] 跨所有轮的扁平列表
    currentRound: 0,            // 当前是第几轮(0 = 第一轮)
    currentQuestions: questions.length ? questions : fallbackBackgroundQuestions(field, current_problem),
    answersThisRound: new Map(),
    submitting: false,
  };

  backgroundQuiz.classList.remove("hidden");
  // 问卷出现时,把上方的大标题/副标题/起始表单隐藏,整页交给问卷,内部可滚动
  document.getElementById("starter")?.classList.add("quiz-mode");
  backgroundQuiz.innerHTML = `
    <div class="background-quiz-head">
      <strong>先校准一下讲解方式</strong>
      <span class="background-quiz-sub">${error ? "出题接口暂不可用,先用本地诊断题。" : "回答完当前这些题就会生成地图；也可以跳过问卷直接生成。"}</span>
    </div>
    <div class="background-quiz-rounds"></div>
    <div class="background-quiz-current"></div>
    <div class="background-quiz-foot"></div>
    <div class="background-quiz-actions">
      <button type="button" class="ghost-button" data-quiz-action="back" data-tooltip="回到上一页修改学习领域和学习目的">返回修改</button>
      <button type="button" class="ghost-button" data-quiz-action="skip" data-tooltip="不回答背景题，直接用默认新手方式生成地图">跳过问卷</button>
      <button type="button" class="primary-button" data-quiz-action="create" data-tooltip="答完当前题目后生成知识地图" disabled>开始生成</button>
    </div>
  `;
  form.classList.add("is-answering-background");
  submitBtn.disabled = false;
  submitBtn.classList.remove("generating");
  submitBtn.textContent = originalLabel;

  const createBtn = backgroundQuiz.querySelector("[data-quiz-action='create']");
  const skipBtn = backgroundQuiz.querySelector("[data-quiz-action='skip']");

  renderCurrentRound();

  backgroundQuiz.querySelector("[data-quiz-action='back']").addEventListener("click", () => {
    backgroundQuiz.classList.add("hidden");
    backgroundQuiz.innerHTML = "";
    form.classList.remove("is-answering-background");
    document.getElementById("starter")?.classList.remove("quiz-mode");
  });
  skipBtn.addEventListener("click", () => commitProfile(true));
  createBtn.addEventListener("click", () => commitProfile(true));

  function renderCurrentRound() {
    const list = backgroundQuiz.querySelector(".background-quiz-current");
    list.innerHTML = "";
    list.dataset.round = String(quizState.currentRound);

    if (quizState.currentRound > 0) {
      const eyebrow = document.createElement("div");
      eyebrow.className = "background-quiz-eyebrow";
      eyebrow.textContent = quizState.currentReason || "再问你几个,我才好判断难度";
      list.append(eyebrow);
    }

    for (const [questionIndex, question] of quizState.currentQuestions.entries()) {
      const item = document.createElement("section");
      item.className = "background-question";
      item.style.animationDelay = `${questionIndex * 45}ms`;
      item.innerHTML = `
        <strong>${escapeHtml(question.question || "")}</strong>
        <div class="background-question-hint">可多选</div>
        <div class="background-options"></div>
        <div class="background-custom-answer">
          <button type="button" class="background-custom-toggle" data-tooltip="没有合适选项时，自己写一个更准确的回答">自定义输入</button>
          <div class="background-custom-row hidden">
            <input type="text" maxlength="160" placeholder="写下你的真实情况" aria-label="自定义回答" />
            <button type="button" class="background-custom-confirm" data-tooltip="使用这段自定义回答">使用</button>
          </div>
        </div>
      `;
      const options = item.querySelector(".background-options");
      const customToggle = item.querySelector(".background-custom-toggle");
      const customRow = item.querySelector(".background-custom-row");
      const customInput = item.querySelector(".background-custom-row input");
      const customConfirm = item.querySelector(".background-custom-confirm");

      // 当前题的多选 state:Map<selectionKey, {answer, label, isUnsure}>
      const getSelections = () => {
        const existing = quizState.answersThisRound.get(question.id);
        if (existing && existing.selections instanceof Map) return existing.selections;
        const selections = new Map();
        quizState.answersThisRound.set(question.id, {
          question: question.question,
          selections,
        });
        return selections;
      };
      // 任何变更后:同步视觉 + 触发 actions 状态刷新
      const syncVisual = () => {
        const selections = quizState.answersThisRound.get(question.id)?.selections || new Map();
        for (const btn of options.querySelectorAll("button")) {
          btn.classList.toggle("selected", selections.has(btn.dataset.selectionKey));
        }
        customToggle.classList.toggle("selected", selections.has("custom"));
        // 没有选中任何项时,把空 entry 清掉,避免 updateActionsState 误判
        if (selections.size === 0) quizState.answersThisRound.delete(question.id);
        updateActionsState();
      };
      // 共享的"加/去"逻辑:不清楚跟其他选项互斥
      const toggleSelection = (key, payload, { isUnsure = false } = {}) => {
        if (quizState.submitting) return;
        const selections = getSelections();
        if (selections.has(key)) {
          selections.delete(key);
        } else {
          if (isUnsure) {
            // 选"不清楚"清掉其他所有选项,语义冲突
            selections.clear();
          } else {
            // 选其他时把"不清楚"踢掉
            for (const existingKey of selections.keys()) {
              if (existingKey === "unsure") selections.delete(existingKey);
            }
          }
          selections.set(key, { ...payload, isUnsure });
        }
        syncVisual();
      };

      for (const [optionIndex, option] of (question.options || []).entries()) {
        const button = document.createElement("button");
        button.type = "button";
        const label = option.label || option.value || "选项";
        const selectionKey = `opt:${optionIndex}`;
        button.dataset.selectionKey = selectionKey;
        button.textContent = label;
        button.dataset.tooltip = `选择「${label}」(可多选)`;
        button.addEventListener("click", () => {
          toggleSelection(selectionKey, {
            answer: option.value || option.label || "",
            label,
          });
        });
        options.append(button);
      }
      const unsureButton = document.createElement("button");
      unsureButton.type = "button";
      unsureButton.className = "background-option-unsure";
      unsureButton.dataset.selectionKey = "unsure";
      unsureButton.textContent = "不清楚";
      unsureButton.dataset.tooltip = "不确定这一题,选了会取消其他选项";
      unsureButton.addEventListener("click", () => {
        toggleSelection("unsure", {
          answer: "你不确定这一题怎么选,后续讲解要先给背景铺垫,不要默认你已经理解相关概念。",
          label: "不清楚",
        }, { isUnsure: true });
      });
      options.append(unsureButton);
      customToggle.addEventListener("click", () => {
        if (quizState.submitting) return;
        customRow.classList.toggle("hidden");
        if (!customRow.classList.contains("hidden")) customInput.focus();
      });
      const submitCustom = () => {
        if (quizState.submitting) return;
        const raw = customInput.value.trim();
        if (!raw) {
          customInput.focus();
          return;
        }
        // 自定义回答跟普通选项一样:加入选集,可以与其他并存
        const selections = getSelections();
        // 删掉旧的 custom (用户重写),再加新的
        selections.delete("custom");
        // custom 不和 unsure 共存
        selections.delete("unsure");
        selections.set("custom", {
          answer: `你的自定义回答:${raw}`,
          label: raw,
        });
        customRow.classList.add("hidden");
        syncVisual();
      };
      customConfirm.addEventListener("click", submitCustom);
      customInput.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          submitCustom();
        }
      });
      list.append(item);
    }
    updateActionsState();
  }

  // 把一道题的多个选择压成 backend 期望的 {question, answer} 单条:
  //   "选项A / 选项B / 自定义:xxx" 拼到 question 的 answer 字段
  function flattenAnswer(info) {
    const selections = info?.selections;
    if (!selections || !selections.size) return null;
    const parts = [];
    for (const { answer, label } of selections.values()) {
      parts.push(answer || label || "");
    }
    return parts.filter(Boolean).join(" / ");
  }

  function updateActionsState() {
    const ready = quizState.currentQuestions.every((q) => {
      const info = quizState.answersThisRound.get(q.id);
      return info?.selections?.size > 0;
    });
    const answeredCount = quizState.currentQuestions.filter((q) => {
      const info = quizState.answersThisRound.get(q.id);
      return info?.selections?.size > 0;
    }).length;
    const totalCount = quizState.currentQuestions.length;
    const missingCount = Math.max(0, totalCount - answeredCount);
    createBtn.disabled = !ready;
    createBtn.textContent = ready ? "生成知识地图" : `还差 ${missingCount} 题`;
    createBtn.dataset.tooltip = ready
      ? "用这些背景答案生成更贴合你的知识地图"
      : `还需要回答 ${missingCount} 题，或者点“跳过问卷”直接生成`;
    skipBtn.textContent = quizState.roundsAnswered.length || answeredCount ? "直接生成" : "跳过问卷";
    skipBtn.dataset.tooltip = quizState.roundsAnswered.length || answeredCount
      ? "用已经填写的答案生成地图，不再继续等待"
      : "不回答背景题，直接用默认新手方式生成地图";
    if (quizState.submitting) {
      createBtn.disabled = true;
      skipBtn.disabled = true;
      createBtn.textContent = "正在生成…";
    } else {
      skipBtn.disabled = false;
    }
  }

  function buildAnsweredFlat() {
    // 把已答轮 + 当前轮已答合并成 [{question, answer}],多选用 / 拼接
    const flat = [...quizState.roundsAnswered];
    for (const [, info] of quizState.answersThisRound) {
      const answer = flattenAnswer(info);
      if (answer) flat.push({ question: info.question, answer });
    }
    return flat;
  }

  async function commitProfile(skipFollowup) {
    if (quizState.submitting) return;
    quizState.submitting = true;
    updateActionsState();
    // 把当前轮答案沉到已答轮(多选用 / 拼接)
    for (const [, info] of quizState.answersThisRound) {
      const answer = flattenAnswer(info);
      if (answer) quizState.roundsAnswered.push({ question: info.question, answer });
    }
    quizState.answersThisRound = new Map();

    // 第一/第二轮答完后:先问 AI 还要不要追问;skipFollowup=true 时跳过追问直接生成
    if (!skipFollowup && quizState.currentRound < 2) {
      try {
        const response = await fetch("/api/sessions/background-followup", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            field,
            current_problem,
            mode: state.mode || "Lite",
            answered: quizState.roundsAnswered,
            follow_up_round: quizState.currentRound,
          }),
        });
        if (response.ok) {
          const data = await response.json();
          if (data?.need_more && (data?.questions || []).length) {
            // 显示已答 round 的简短摘要,然后切到新一轮
            stashRoundSummary(quizState.currentRound);
            quizState.currentRound += 1;
            quizState.currentQuestions = data.questions;
            quizState.currentReason = data.reason || "";
            quizState.submitting = false;
            renderCurrentRound();
            return;
          }
        }
      } catch (error) {
        console.warn("background followup failed", error);
        // 失败就走默认进入生成
      }
    }

    // 拼 learning_background → 进入"主卡片预览"阶段(让用户先编辑后再造树)
    const learning_background = quizState.roundsAnswered
      .map((a) => a.answer)
      .filter(Boolean)
      .join("\n");
    stashRoundSummary(quizState.currentRound);
    quizState.submitting = false; // 预览阶段允许用户取消回到 quiz
    await showTopicPreview({ field, current_problem, learning_background, form, submitBtn, originalLabel });
  }

  function stashRoundSummary(roundIndex) {
    // 把上一轮的题简短打个标签放到上方,让用户看到自己答了什么
    const wrap = backgroundQuiz.querySelector(".background-quiz-rounds");
    if (!wrap) return;
    const rounds = quizState.roundsAnswered;
    if (!rounds.length) return;
    wrap.innerHTML = "";
    const round = document.createElement("div");
    round.className = "background-quiz-stash";
    round.innerHTML = `
      <span class="background-quiz-stash-tag">已答 ${rounds.length} 题</span>
      <span class="background-quiz-stash-text">${rounds.map((r) => escapeHtml(r.answer.slice(0, 40))).join(" · ")}</span>
    `;
    wrap.append(round);
  }
}

function fallbackBackgroundQuestions(field, currentProblem) {
  return [
    {
      id: "level",
      question: `你现在对「${field}」的熟悉程度?`,
      options: [
        { label: "零基础", value: "用户是零基础,需要先用白话解释概念,少用专业术语。" },
        { label: "懂一点", value: "用户有少量接触,关键术语需要顺手解释。" },
        { label: "有经验", value: "用户有相关经验,可以多讲机制、指标和判断方法。" },
        { label: "做过项目", value: "用户做过相关项目,可以直接结合案例、指标和决策取舍来讲。" },
      ],
    },
    {
      id: "goal",
      question: `围绕「${currentProblem}」,你更想先做到什么?`,
      options: [
        { label: "先听懂", value: "用户目标是先听懂,回答要短、清楚、少分支。" },
        { label: "能判断", value: "用户目标是能做判断,回答要给判断标准、例子和反例。" },
        { label: "能实操", value: "用户目标是能落地实操,回答要给步骤和行动建议。" },
        { label: "能表达", value: "用户目标是能讲给别人听,回答要给结构化话术和清楚类比。" },
      ],
    },
    {
      id: "terms",
      question: "遇到专业术语时,你希望我怎么处理?",
      options: [
        { label: "先翻译成人话", value: "遇到专业术语时先用一句白话解释,再进入分析。" },
        { label: "术语旁边解释", value: "可以保留术语,但第一次出现时要立刻补一句白话解释。" },
        { label: "可以直接讲", value: "可以直接使用常见术语,但复杂术语要补一句边界。" },
        { label: "多用例子", value: "遇到抽象术语时优先配一个具体例子,再补正式定义。" },
      ],
    },
  ];
}

async function createSessionFromProfile({ field, current_problem, learning_background, form, submitBtn, originalLabel, topics = null }) {
  submitBtn.disabled = true;
  submitBtn.classList.add("generating");
  submitBtn.textContent = "正在生长…";
  chatInput.disabled = true;
  sendButton.disabled = true;

  // 立刻进入工作区,左侧显示"思考"占位,右侧显示生长态
  // 同时把问卷页 / 预览页隐藏掉,清掉 quiz-mode 让 starter 恢复正常布局
  backgroundQuiz?.classList.add("hidden");
  if (backgroundQuiz) backgroundQuiz.innerHTML = "";
  topicPreviewEl?.classList.add("hidden");
  if (topicPreviewEl) topicPreviewEl.innerHTML = "";
  document.getElementById("starter")?.classList.remove("quiz-mode");
  form?.classList.remove("is-answering-background");
  state.generatingTree = true;
  state.sessionId = null;
  state.nodes = [];
  state.messages = [
    {
      id: "stream_init",
      role: "assistant",
      content: "",
      node_id: null,
      thinking: true,
      created_at: new Date().toISOString(),
      next_actions: [],
    },
  ];
  render();

  const body = { field, current_problem, learning_background, mode: state.mode };
  if (Array.isArray(topics) && topics.length) {
    body.topics_override = topics.map((t) => ({
      title: t.title,
      summary: t.summary || "",
      custom: Boolean(t.custom),
    }));
  }

  try {
    const response = await fetch("/api/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    state.sessionId = payload.session_id;
    state.currentNodeId = payload.current_node_id;
    state.nodes = payload.initial_nodes || [];
    state.messages = payload.messages || [];
    chatInput.disabled = false;
    sendButton.disabled = false;
    state.visited = visitedFromMessages(state.messages);
    // 标记所有节点为"刚刚生成",在 renderTree 里挂 --enter-delay 实现 stagger
    state.newNodeIds = new Set(state.nodes.map((n) => n.id));
    state.newNodeEnterDelay = computeEnterDelays(state.nodes);
    persistVisited();
    resetViewport();
    persistSession();
    state.generatingTree = false;
    // 生长分支:在第一次 render() 之前就把镜头无动画定位到主干。
    // 否则 resetViewport 把 pan 归零,render() 会先画一帧"在画布原点"→ 随后被
    // runGrowthChoreography 的 fit 拽到主干 = 你看到的"非常快速地闪走"。
    // 先 renderTree() 建立 state.lastLayout,focusOnNodes 才有坐标可算。
    if (body.topics_override) {
      // reservedTrunkHeight 必须和 runGrowthChoreography 里的值一致,否则这次 fit
      // 与生长开局的 fit 主干间距不同 → 仍有一次可见瞬跳。
      state.reservedTrunkHeight = ({ Lite: 720, Medium: 1080, Zen: 1440 })[state.mode] || 900;
      renderTree();
      const trunkIds0 = state.nodes.filter((n) => n.depth === 1).map((n) => n.id);
      if (trunkIds0.length) focusOnNodes(trunkIds0, { animate: false });
    }
    render();
    loadSessions();  // 让侧栏「最近」立刻看到新会话
    setSidebarCollapsed(true);

    if (body.topics_override) {
      // 预览-确认流程:主干已就位,启动 SSE 流式生长 children + 动画编舞
      await runGrowthChoreography(payload.session_id);
    } else {
      // 老流程(一次出整树):保留原有的 stagger + 聚焦
      const focusIds = state.nodes.filter((n) => n.depth <= 1).map((n) => n.id);
      setTimeout(() => focusOnNodes(focusIds), 480);
      setTimeout(() => {
        state.newNodeIds = new Set();
        state.newNodeEnterDelay = new Map();
        renderTree();
      }, 2800);
    }
  } catch (error) {
    state.generatingTree = false;
    state.messages = state.messages.map((m) =>
      m.id === "stream_init" ? { ...m, thinking: false, content: `生成失败：${error.message}` } : m,
    );
    render();
  } finally {
    chatInput.disabled = false;
    sendButton.disabled = false;
    submitBtn.disabled = false;
    submitBtn.classList.remove("generating");
    submitBtn.textContent = originalLabel;
  }
}

// === 主卡片预览 — 编辑 — 确认 ===
// 答完问卷后调 /preview-topics,左侧渲染编辑面板:可删 / 可加 / 可取消,
// 用户点"确认生成"才真正调 /api/sessions(带 topics_override)。
async function showTopicPreview({ field, current_problem, learning_background, form, submitBtn, originalLabel }) {
  if (!topicPreviewEl) {
    // 没有挂载点(老 HTML),退回到一次出整树的老流程
    await createSessionFromProfile({ field, current_problem, learning_background, form, submitBtn, originalLabel });
    return;
  }
  backgroundQuiz?.classList.add("hidden");
  if (backgroundQuiz) backgroundQuiz.innerHTML = "";
  topicPreviewEl.classList.remove("hidden");
  // 保持 quiz-mode 布局(让 starter 让出空间给预览框)
  document.getElementById("starter")?.classList.add("quiz-mode");

  // state.preview 保存当前编辑中的列表 + 控制位
  const previewState = { topics: [], loading: true, error: "", customDraft: "" };

  const renderPreview = () => {
    if (!topicPreviewEl) return;
    topicPreviewEl.innerHTML = "";
    const head = document.createElement("div");
    head.className = "topic-preview-head";
    head.innerHTML = `
      <strong>AI 准备的主知识卡片</strong>
      <span class="topic-preview-sub">看一下、删掉不想学的、补上想学的,再确认生成。</span>
    `;
    topicPreviewEl.append(head);

    if (previewState.loading) {
      const loading = document.createElement("div");
      loading.className = "topic-preview-loading";
      loading.textContent = "AI 正在挑选主卡片…";
      topicPreviewEl.append(loading);
      return;
    }
    if (previewState.error) {
      const err = document.createElement("div");
      err.className = "topic-preview-error";
      err.innerHTML = `<span>${escapeHtml(previewState.error)}</span>`;
      const retry = document.createElement("button");
      retry.type = "button";
      retry.className = "topic-preview-retry";
      retry.textContent = "重试";
      retry.addEventListener("click", () => fetchPreview());
      err.append(retry);
      topicPreviewEl.append(err);
      return;
    }

    const list = document.createElement("div");
    list.className = "topic-preview-list";
    previewState.topics.forEach((topic, index) => {
      const card = document.createElement("div");
      card.className = `topic-preview-card${topic.custom ? " custom" : ""}`;
      card.innerHTML = `
        <div class="topic-preview-card-body">
          <strong>${escapeHtml(topic.title)}</strong>
          ${topic.summary ? `<span>${escapeHtml(topic.summary)}</span>` : ""}
        </div>
        <button type="button" class="topic-preview-card-remove" aria-label="删掉这张卡片" data-tooltip="不学这块">×</button>
      `;
      card.querySelector(".topic-preview-card-remove").addEventListener("click", () => {
        previewState.topics.splice(index, 1);
        renderPreview();
      });
      list.append(card);
    });
    topicPreviewEl.append(list);

    // 新增框
    const addRow = document.createElement("div");
    addRow.className = "topic-preview-add";
    addRow.innerHTML = `
      <input type="text" maxlength="40" placeholder="想了解的领域(例:供应链管理)" aria-label="新增主卡片" />
      <button type="button" class="topic-preview-add-btn" data-tooltip="把这一项加到主卡片列表">＋ 加一项</button>
    `;
    const addInput = addRow.querySelector("input");
    addInput.value = previewState.customDraft;
    const addBtn = addRow.querySelector(".topic-preview-add-btn");
    addInput.addEventListener("input", () => { previewState.customDraft = addInput.value; });
    const submitCustom = () => {
      const val = addInput.value.trim();
      if (!val) return;
      previewState.topics.push({ title: val.slice(0, 40), summary: "", custom: true });
      previewState.customDraft = "";
      renderPreview();
    };
    addBtn.addEventListener("click", submitCustom);
    addInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        submitCustom();
      }
    });
    topicPreviewEl.append(addRow);

    // 操作按钮
    const actions = document.createElement("div");
    actions.className = "topic-preview-actions";
    actions.innerHTML = `
      <button type="button" class="ghost-button" data-preview-action="cancel" data-tooltip="回去重看问卷的答案">取消</button>
      <button type="button" class="primary-button" data-preview-action="confirm" data-tooltip="按这份主卡片列表开始生成知识树" ${previewState.topics.length ? "" : "disabled"}>确认生成 (${previewState.topics.length})</button>
    `;
    actions.querySelector("[data-preview-action='cancel']").addEventListener("click", () => {
      topicPreviewEl.classList.add("hidden");
      topicPreviewEl.innerHTML = "";
      document.getElementById("starter")?.classList.remove("quiz-mode");
      submitBtn.disabled = false;
      submitBtn.textContent = originalLabel;
    });
    actions.querySelector("[data-preview-action='confirm']").addEventListener("click", async () => {
      if (!previewState.topics.length) return;
      await createSessionFromProfile({
        field,
        current_problem,
        learning_background,
        form,
        submitBtn,
        originalLabel,
        topics: previewState.topics,
      });
    });
    topicPreviewEl.append(actions);
  };

  const fetchPreview = async () => {
    previewState.loading = true;
    previewState.error = "";
    renderPreview();
    try {
      const response = await fetch("/api/sessions/preview-topics", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ field, current_problem, learning_background, mode: state.mode }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${response.status}`);
      }
      const data = await response.json();
      previewState.topics = (data.topics || []).map((t) => ({
        title: t.title,
        summary: t.summary || "",
        custom: Boolean(t.custom),
      }));
      previewState.loading = false;
      renderPreview();
    } catch (error) {
      previewState.loading = false;
      previewState.error = `预览失败:${error.message}`;
      renderPreview();
    }
  };

  await fetchPreview();
}

// === 流式生长动画编舞 ===
// 主干已经在创建 session 时落库,这里订阅 /grow-children SSE,每收到一支 children
// 就播放"聚焦主干 → 子节点扇形入场"的 700ms 动画。
// 关键:abort 只跳过动画延时,不断 SSE——否则用户点一下就把后端正在跑的 LLM
// 全 abort 了,children 永远不会出现。
// 关键时间常数:
//   GROWTH_PER_TRUNK_MS  > .viewport-glide 的 880ms,确保上一段 pan 走完才触发下一段,
//                          否则会"半路改方向" → 视觉 flash
//   GROWTH_TRUNK_ZOOM    生长期保持恒定的 zoom——只 pan、不变焦,体感最自然
const GROWTH_PER_TRUNK_MS = 760;  // > glide(560)+buffer,镜头到位后再走下一段,不互相打断
const GROWTH_CARD_FOCUS_MS = 620;
const GROWTH_TRUNK_ZOOM = 0.68;
const GROWTH_FIT_ALL_MS = 600;
async function runGrowthChoreography(sessionId) {
  state.newNodeIds = new Set();
  state.newNodeEnterDelay = new Map();
  state.growthActive = true;
  state.growthAborted = false;
  state.layoutFrozen = true; // 生长期间冻结卡片位置过渡,不漂移
  // 关键:按 mode 预留每个 trunk 的 band 高度,让所有 trunk 从一开始就在最终位置上,
  // 加 children 不会再 ripple 后面所有 trunk —— 视觉上不再有"边缘晃动"。
  // 数值取的是该 mode 下"典型 children 数量"的高度需求(每条 ≈ NODE_H 148 + ROW_GAP 34)。
  // 这个值生长完不主动清——后续用户拆分继续在同一节奏下,layout 仍稳定。
  const reservedPerMode = { Lite: 720, Medium: 1080, Zen: 1440 };
  state.reservedTrunkHeight = reservedPerMode[state.mode] || 900;
  // 先把 hasViewport 置位,挡掉 renderTree RAF 里的 centerViewportOnCurrent——
  // 否则它会先把镜头居中到根节点(第一下漂移),随即又被下面的 fit 拽走(漂回来)。
  state.hasViewport = true;
  renderTree();
  // 开局把镜头 fit 到所有主干上,定一个【稳定的全局视角】。animate:false 让它【瞬间】
  // 就位,不走 glide → 没有"飘进去"的过程。之后整个生长过程镜头不再每支乱跳,
  // children 在这个固定视野里原地长出来;结束后再 fit 一次看全貌。
  const trunkIds = state.nodes.filter((n) => n.depth === 1).map((n) => n.id);
  if (trunkIds.length) focusOnNodes(trunkIds, { animate: false });

  const onKey = (event) => {
    if (event.key === "Escape") state.growthAborted = true;
  };
  document.addEventListener("keydown", onKey);

  const queue = [];
  let sseDone = false;
  let streamError = null;

  // SSE 在后台跑到底,不被 abort 影响
  const ssePromise = (async () => {
    try {
      const response = await fetch(
        `/api/sessions/${sessionId}/grow-children?mode=${encodeURIComponent(state.mode)}`,
        { method: "POST", headers: { Accept: "text/event-stream" } },
      );
      if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { value, done: streamDone } = await reader.read();
        if (streamDone) break;
        buffer += decoder.decode(value, { stream: true });
        let sep;
        while ((sep = buffer.indexOf("\n\n")) !== -1) {
          const raw = buffer.slice(0, sep);
          buffer = buffer.slice(sep + 2);
          const lines = raw.split("\n");
          let eventType = "message";
          let dataStr = "";
          for (const line of lines) {
            if (line.startsWith("event:")) eventType = line.slice(6).trim();
            else if (line.startsWith("data:")) dataStr += line.slice(5).trim();
          }
          let data = {};
          try { data = dataStr ? JSON.parse(dataStr) : {}; } catch { data = {}; }
          if (eventType === "branch_done") queue.push(data);
          else if (eventType === "all_done") sseDone = true;
          else if (eventType === "error") {
            streamError = data.error || "stream error";
            sseDone = true;
          }
        }
      }
      sseDone = true;
    } catch (error) {
      streamError = error.message;
      sseDone = true;
    }
  })();

  // 编舞循环:无论 abort 与否,都要把 SSE 推过来的所有事件**消化完**,让 children
  // 进 state。abort 只决定要不要"pan + pulse + 等节奏"。
  //
  // 平滑相机的两个关键:
  //   1. 用 panToNode 不变焦距,只平移 → 没 zoom 抖动
  //   2. 每段等 1100ms (> CSS glide 880ms) → 没"半路改方向"的 flash
  while (!sseDone || queue.length) {
    if (queue.length === 0) {
      await new Promise((r) => setTimeout(r, 60));
      continue;
    }
    const event = queue.shift();
    if (Array.isArray(event.children) && event.children.length) {
      state.nodes = [...state.nodes, ...event.children];
      state.newNodeIds = new Set(event.children.map((c) => c.id));
      state.newNodeEnterDelay = computeEnterDelays(event.children);
    }
    persistSession();
    renderTree();
    if (!state.growthAborted) {
      pulseNode(event.parent_id);
      const created = Array.isArray(event.children) ? event.children : [];
      if (created.length) {
        for (const child of created) {
          if (state.growthAborted) break;
          panToNode(child.id, { zoom: GROWTH_TRUNK_ZOOM });
          pulseNode(child.id);
          await new Promise((r) => setTimeout(r, GROWTH_CARD_FOCUS_MS));
        }
      } else {
        await new Promise((r) => setTimeout(r, GROWTH_PER_TRUNK_MS));
      }
    }
  }

  await ssePromise.catch(() => {});
  document.removeEventListener("keydown", onKey);
  state.growthActive = false;
  state.layoutFrozen = false; // 解冻:结束后的 fit 让卡片正常平滑过渡
  state.newNodeIds = new Set();
  state.newNodeEnterDelay = new Map();
  renderTree();

  if (streamError) {
    console.warn("[knowledge_map] growth stream error:", streamError);
  }

  // 缩小看全局
  setTimeout(() => {
    const allIds = state.nodes.map((n) => n.id);
    if (allIds.length) focusOnNodes(allIds);
  }, GROWTH_FIT_ALL_MS);
}

// ============ 第一性原理"拆到底" ============
// 从某个节点起,逐层往下拆出更底层前置知识,流式画出,随时可停。
const FP_PER_LAYER_MS = 720; // > glide(560),让每层镜头/卡片滑动跑完再拆下一层,避免半路改向
let fpStopButton = null;

function showFpStopButton() {
  if (fpStopButton) return;
  fpStopButton = document.createElement("button");
  fpStopButton.type = "button";
  fpStopButton.className = "fp-stop-button";
  fpStopButton.textContent = "■ 停止拆解";
  fpStopButton.dataset.tooltip = "停止第一性原理拆解;已拆出的卡片会保留";
  fpStopButton.addEventListener("click", () => {
    state.fpAborted = true;
  });
  document.body.append(fpStopButton);
  requestAnimationFrame(() => fpStopButton?.classList.add("visible"));
}

function hideFpStopButton() {
  if (!fpStopButton) return;
  const btn = fpStopButton;
  fpStopButton = null;
  btn.classList.remove("visible");
  setTimeout(() => btn.remove(), 200);
}

async function startFirstPrinciples(node) {
  if (state.fpActive) return;
  if (!state.sessionId || !node) return;
  state.fpActive = true;
  state.fpAborted = false;
  // 第一性原理是链式向下长:下一层出现时,上一层会从叶子变成父节点。
  // 这里保留 left/top 过渡,否则重算分支高度时上一张卡会瞬间跳走,看起来像消失。
  state.layoutFrozen = false;
  const expectedSession = state.sessionId;

  showFpStopButton();
  // 置位挡掉 renderTree RAF 的 centerViewportOnCurrent,避免"先居中当前节点再被 pan 拽走"的双段漂移
  state.hasViewport = true;
  panToNode(node.id, { zoom: GROWTH_TRUNK_ZOOM });
  pulseNode(node.id);

  const onKey = (event) => {
    if (event.key === "Escape") state.fpAborted = true;
  };
  document.addEventListener("keydown", onKey);

  let reader = null;
  try {
    const response = await fetch(
      `/api/sessions/${state.sessionId}/nodes/${node.id}/first-principles?max_depth=6`,
      { method: "POST", headers: { Accept: "text/event-stream" } },
    );
    if (!response.ok || !response.body) {
      throw new Error(`first-principles HTTP ${response.status}`);
    }
    reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      if (state.fpAborted) {
        try { await reader.cancel(); } catch (_) {}
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      const frames = buffer.split("\n\n");
      buffer = frames.pop() || "";
      for (const frame of frames) {
        if (!frame.trim()) continue;
        let eventType = "message";
        let dataLine = "";
        for (const line of frame.split("\n")) {
          if (line.startsWith("event:")) eventType = line.slice(6).trim();
          else if (line.startsWith("data:")) dataLine += line.slice(5).trim();
        }
        if (eventType === "fp_layer") {
          let payload;
          try { payload = JSON.parse(dataLine); } catch (_) { continue; }
          if (expectedSession !== state.sessionId) continue;
          const kids = payload.children || [];
          if (kids.length) {
            state.nodes = [...state.nodes, ...kids];
            state.newNodeIds = new Set(kids.map((c) => c.id));
            state.newNodeEnterDelay = computeEnterDelays(kids);
            renderTree();
            if (!state.fpAborted) {
              pulseNode(payload.parent_id);
              for (const child of kids) {
                if (state.fpAborted) break;
                state.currentNodeId = child.id;
                markVisited(child.id);
                persistSession();
                updateNodeVisualState();
                focusOnNodes([payload.parent_id, child.id]);
                pulseNode(child.id);
                await new Promise((r) => setTimeout(r, FP_PER_LAYER_MS));
              }
            }
          } else if (payload.reached_bottom) {
            // 这一支触底了:刷新让触底节点显示"基础"标记
            renderTree();
          }
        } else if (eventType === "error") {
          let payload = {};
          try { payload = JSON.parse(dataLine); } catch (_) {}
          console.warn("[knowledge_map] first-principles error:", payload.error);
        }
      }
    }
  } catch (error) {
    console.warn("[knowledge_map] first-principles stream error:", error);
  } finally {
    document.removeEventListener("keydown", onKey);
    state.fpActive = false;
    state.layoutFrozen = false; // 解冻:结束后正常平滑过渡
    state.newNodeIds = new Set();
    state.newNodeEnterDelay = new Map();
    hideFpStopButton();
    renderTree();
    setTimeout(() => {
      const allIds = state.nodes.map((n) => n.id);
      if (allIds.length) focusOnNodes(allIds);
    }, GROWTH_FIT_ALL_MS);
  }
}

function computeEnterDelays(nodes) {
  // 按 (depth, sort_order) 计算 stagger:根 0ms,一级子节点按 sort_order 排,二级再串。
  // 节奏故意放慢——用户能"看到地图一格一格长出来",而不是一闪就完成。
  const map = new Map();
  const STEP = 120;            // 同层相邻节点的延迟间隔
  const DEPTH_OFFSET = 260;    // 进入下一层之前的停顿
  const MAX_DELAY = 2200;      // 全部节点应该在 ~2.2s 内陆续到位
  const sorted = [...nodes].sort((a, b) => {
    if (a.depth !== b.depth) return a.depth - b.depth;
    return (a.sort_order || 0) - (b.sort_order || 0);
  });
  const byDepth = new Map();
  for (const node of sorted) {
    if (!byDepth.has(node.depth)) byDepth.set(node.depth, []);
    byDepth.get(node.depth).push(node);
  }
  for (const [depth, group] of byDepth) {
    group.forEach((node, idx) => {
      const delay = depth * DEPTH_OFFSET + idx * STEP;
      map.set(node.id, Math.min(delay, MAX_DELAY));
    });
  }
  return map;
}

$("#new-session").addEventListener("click", () => {
  localStorage.removeItem("km.sessionId");
  localStorage.removeItem("km.currentNodeId");
  state.sessionId = null;
  state.currentNodeId = null;
  state.nodes = [];
  state.messages = [];
  state.visited = new Set();
  persistVisited();
  resetViewport();
  // 把上一次会话残留的浮层、问卷、引用、输入框全部清空,避免"新对话还停在旧问卷上"
  backgroundQuiz?.classList.add("hidden");
  if (backgroundQuiz) backgroundQuiz.innerHTML = "";
  document.getElementById("start-form")?.classList.remove("is-answering-background");
  document.getElementById("starter")?.classList.remove("quiz-mode");
  closeAllPeekPopovers();
  closeSubdividePopover();
  closeNodeComposer();
  closeHighlightMenu();
  clearPendingQuote();
  if (chatInput) chatInput.value = "";
  render();
});

$("#chat-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = chatInput.value.trim();
  if (!message || state.sending) return;
  const fullMessage = buildMessageWithPendingQuote(message);
  chatInput.value = "";
  resizeChatInput();
  clearPendingQuote();
  // 发完立刻把光标还给输入框,让用户能直接接着想下一句
  chatInput.focus();
  await sendMessage(fullMessage);
  // 网络异步过程里 sendMessage 中途可能切焦点;done 后再确保一次焦点回来
  chatInput.focus();
});

$("#chat-quote-clear")?.addEventListener("click", () => {
  clearPendingQuote();
  chatInput.focus();
});

// 仿 ChatGPT 输入框自动撑高;CSS 上限 120px,超出出滚动条
function resizeChatInput() {
  if (!chatInput) return;
  chatInput.style.height = "auto";
  chatInput.style.height = Math.min(chatInput.scrollHeight, 120) + "px";
}
chatInput.addEventListener("input", resizeChatInput);
chatInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    $("#chat-form").requestSubmit();
  }
});

$("#zoom-out").addEventListener("click", () => setZoom(state.zoom - 8));
$("#zoom-in").addEventListener("click", () => setZoom(state.zoom + 8));
$("#zoom-reset").addEventListener("click", () => {
  state.zoom = 88;
  state.hasViewport = false;
  renderTree();
});
zoomRange.addEventListener("input", () => setZoom(Number(zoomRange.value)));

toggleVisitedButton.addEventListener("click", () => {
  state.hideUnvisited = !state.hideUnvisited;
  localStorage.setItem("km.hideUnvisited", String(state.hideUnvisited));
  toggleVisitedButton.setAttribute("aria-pressed", String(state.hideUnvisited));
  toggleVisitedButton.classList.toggle("active", state.hideUnvisited);
  closeNodeComposer();
  renderTree();
});

// === AI 节点检索 ===
// 输入框防抖 600ms 自动搜;同时 Enter 立刻搜。结果卡片渲染在输入框上方,
// 用户点卡片 → 聚焦那个节点但不替换 currentNode、不刷新对话。
let nodeSearchAbortController = null;
let nodeSearchDebounceTimer = null;
let nodeSearchLastQuery = "";

function setNodeSearchResultsVisible(visible) {
  if (!nodeSearchResults) return;
  nodeSearchResults.hidden = !visible;
  nodeSearchResults.classList.toggle("visible", visible);
}

function renderNodeSearchState(state) {
  if (!nodeSearchResults) return;
  nodeSearchResults.innerHTML = "";
  if (state.kind === "hidden") {
    setNodeSearchResultsVisible(false);
    return;
  }
  if (state.kind === "loading") {
    const hint = document.createElement("div");
    hint.className = "node-search-hint";
    hint.textContent = "AI 正在挑选最相关的节点…";
    nodeSearchResults.append(hint);
  } else if (state.kind === "empty") {
    const hint = document.createElement("div");
    hint.className = "node-search-hint";
    hint.textContent = "AI 没有从知识树里找到匹配的节点";
    nodeSearchResults.append(hint);
  } else if (state.kind === "error") {
    const hint = document.createElement("div");
    hint.className = "node-search-hint is-error";
    hint.textContent = state.message || "检索失败,请稍后再试";
    nodeSearchResults.append(hint);
  } else if (state.kind === "results") {
    for (const hit of state.results) {
      nodeSearchResults.append(createNodeSearchCard(hit));
    }
  }
  setNodeSearchResultsVisible(true);
}

function createNodeSearchCard(hit) {
  const card = document.createElement("button");
  card.type = "button";
  card.className = `node-search-card node-search-score-${hit.score || 2}`;
  card.dataset.nodeId = hit.node_id;
  card.dataset.tooltip = "聚焦到这个节点,但不打断当前对话";

  const head = document.createElement("div");
  head.className = "node-search-card-head";
  const title = document.createElement("strong");
  title.className = "node-search-card-title";
  title.textContent = hit.title || "未命名节点";
  const score = document.createElement("span");
  score.className = "node-search-card-score";
  // 用 1-3 颗点表达相关度(沿用卡片上的推荐点视觉)
  for (let i = 1; i <= 3; i += 1) {
    const dot = document.createElement("span");
    dot.className = `node-search-dot${i <= (hit.score || 2) ? " filled" : ""}`;
    score.append(dot);
  }
  head.append(title, score);
  card.append(head);

  if (hit.reason) {
    const reason = document.createElement("div");
    reason.className = "node-search-card-reason";
    reason.textContent = hit.reason;
    card.append(reason);
  } else if (hit.summary) {
    const summary = document.createElement("div");
    summary.className = "node-search-card-summary";
    summary.textContent = hit.summary;
    card.append(summary);
  }

  card.addEventListener("click", () => {
    const node = state.nodes.find((n) => n.id === hit.node_id);
    if (!node) return;
    // 只聚焦,不替换 currentNode、不刷新对话——用户挑了再决定是否真去那个节点
    focusOnNodes([node.id]);
    pulseNode(node.id);
    // 选完了清掉结果,让画布显示出来
    setNodeSearchResultsVisible(false);
  });
  return card;
}

async function runNodeSearch(query) {
  if (!state.sessionId) {
    renderNodeSearchState({ kind: "error", message: "还没开始任何学习会话" });
    return;
  }
  if (nodeSearchAbortController) nodeSearchAbortController.abort();
  nodeSearchAbortController = new AbortController();
  nodeSearchLastQuery = query;
  renderNodeSearchState({ kind: "loading" });
  try {
    const response = await fetch(`/api/sessions/${state.sessionId}/nodes/search`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, limit: 5 }),
      signal: nodeSearchAbortController.signal,
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    // 中途又改了 query,丢掉旧结果
    if (query !== nodeSearchLastQuery) return;
    if (!data.results || data.results.length === 0) {
      renderNodeSearchState({ kind: "empty" });
    } else {
      renderNodeSearchState({ kind: "results", results: data.results });
    }
  } catch (error) {
    if (error.name === "AbortError") return;
    renderNodeSearchState({ kind: "error", message: `检索失败：${error.message}` });
  }
}

function scheduleNodeSearch() {
  if (!nodeSearchInput) return;
  const value = nodeSearchInput.value.trim();
  clearTimeout(nodeSearchDebounceTimer);
  if (nodeSearchAbortController) nodeSearchAbortController.abort();
  if (!value) {
    nodeSearchLastQuery = "";
    renderNodeSearchState({ kind: "hidden" });
    return;
  }
  if (value.length < 2) {
    // 1 个字符不调 AI,避免误触
    renderNodeSearchState({ kind: "hidden" });
    return;
  }
  nodeSearchDebounceTimer = setTimeout(() => runNodeSearch(value), 600);
}

if (nodeSearchForm) {
  nodeSearchForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const value = nodeSearchInput.value.trim();
    if (value.length < 2) return;
    clearTimeout(nodeSearchDebounceTimer);
    runNodeSearch(value);
    // 提交后:清空输入 + 让焦点离开 → 胶囊自动收回到 92px,结果列表保留
    nodeSearchInput.value = "";
    nodeSearchInput.blur();
  });
}
if (nodeSearchInput) {
  nodeSearchInput.addEventListener("input", scheduleNodeSearch);
  nodeSearchInput.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      nodeSearchInput.value = "";
      renderNodeSearchState({ kind: "hidden" });
      nodeSearchInput.blur();
    }
  });
}
// 点检索结果外的地方,自动收起结果列表(留出画布视野);点输入框/结果自己不算
document.addEventListener("mousedown", (event) => {
  if (!nodeSearchResults || nodeSearchResults.hidden) return;
  if (event.target.closest("#node-search")) return;
  setNodeSearchResultsVisible(false);
});

async function restore() {
  if (state.sessionId) {
    try {
      const [treeResponse, messagesResponse] = await Promise.all([
        fetch(`/api/sessions/${state.sessionId}/tree`),
        fetch(`/api/sessions/${state.sessionId}/messages`),
      ]);
      state.nodes = (await treeResponse.json()).nodes || [];
      state.messages = (await messagesResponse.json()).messages || [];
      hydrateVisitedFromMessages();
    } catch {
      state.sessionId = null;
      state.currentNodeId = null;
      localStorage.removeItem("km.sessionId");
      localStorage.removeItem("km.currentNodeId");
    }
  }
  render();
  // 不管有没有当前 session 都要拉历史列表,否则冷启动侧栏永远是空的
  loadSessions();
}

function setSidebarCollapsed(collapsed) {
  state.sidebarCollapsed = collapsed;
  localStorage.setItem("km.sidebarCollapsed", String(collapsed));
  appShell.classList.toggle("sidebar-collapsed", collapsed);
  sidebarToggle.title = collapsed ? "打开边栏" : "关闭边栏";
}

function openModePopover() {
  const rect = modeButton.getBoundingClientRect();
  modePopover.style.left = `${Math.round(rect.left)}px`;
  modePopover.style.top = `${Math.round(rect.bottom + 8)}px`;
  modePopover.classList.remove("hidden");
  modeButton.setAttribute("aria-expanded", "true");
  updateModeOptions();
}

function closeModePopover() {
  modePopover.classList.add("hidden");
  modeButton.setAttribute("aria-expanded", "false");
}

function setMode(mode, persist = true) {
  state.mode = ["Lite", "Medium", "Zen"].includes(mode) ? mode : "Lite";
  modeLabel.textContent = state.mode;
  modeHelp.textContent = modeDescription(state.mode);
  if (persist) localStorage.setItem("km.mode", state.mode);
  updateModeOptions();
}

function updateModeOptions() {
  modePopover.querySelectorAll("[data-mode]").forEach((button) => {
    button.classList.toggle("active", button.dataset.mode === state.mode);
  });
}

// 学习模式只剩教练模式;函数留着兼容旧调用点,内部固定 true。
function setCoachMode(enabled = true, persist = false) {
  state.coachMode = true;
  void enabled;
  void persist;
}

function modeDescription(mode) {
  return {
    Lite: "轻量拆解，回答更短。",
    Medium: "中等深度，解释和分支更完整。",
    Zen: "最深拆解，节点更细，回答更充分。",
  }[mode] || "轻量拆解，回答更短。";
}

async function loadSessions() {
  sessionList.innerHTML = '<div class="drawer-empty">正在读取对话…</div>';
  const query = new URLSearchParams();
  if (state.sessionSearch) query.set("search", state.sessionSearch);
  const response = await fetch(`/api/sessions${query.toString() ? `?${query}` : ""}`);
  const payload = await response.json();
  state.sessions = payload.sessions || [];
  renderSessionList();
}

function renderSessionList() {
  sessionList.innerHTML = "";
  if (!state.sessions.length) {
    const empty = document.createElement("div");
    empty.className = "drawer-empty";
    empty.textContent = state.sessionSearch ? "没有匹配的对话" : "还没有历史对话";
    sessionList.append(empty);
    return;
  }
  for (const session of state.sessions) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `session-item ${session.id === state.sessionId ? "active" : ""}`;
    button.innerHTML = `
      <strong>${escapeHtml(session.title || session.field || "未命名地图")}</strong>
      <span>${escapeHtml(session.current_problem || "")}</span>
      <em>${Number(session.message_count || 0)} 条对话 · ${Number(session.node_count || 0)} 个节点</em>
    `;
    button.addEventListener("click", async () => {
      await loadSession(session.id);
      closeSessionDrawer();
    });
    sessionList.append(button);
  }
}

async function loadSession(sessionId) {
  const [treeResponse, messagesResponse] = await Promise.all([
    fetch(`/api/sessions/${sessionId}/tree`),
    fetch(`/api/sessions/${sessionId}/messages`),
  ]);
  state.sessionId = sessionId;
  state.nodes = (await treeResponse.json()).nodes || [];
  state.messages = (await messagesResponse.json()).messages || [];
  const root = state.nodes.find((node) => !node.parent_id) || state.nodes[0];
  state.currentNodeId = root?.id || state.nodes[0]?.id || null;
  hydrateVisitedFromMessages();
  state.hasViewport = false;
  persistSession();
  render();
}

async function sendMessage(message, optionsOrNodeId = {}) {
  // 兼容旧签名:sendMessage(text, nodeId)
  const options = typeof optionsOrNodeId === "string" || optionsOrNodeId === null || optionsOrNodeId === undefined
    ? { nodeId: optionsOrNodeId }
    : optionsOrNodeId;
  const intent = options.intent || "auto";
  const targetNodeId = options.targetNodeId || null;
  const promotedTitle = (options.promotedTitle || "").trim();
  const subdivisionAngle = (options.subdivisionAngle || "").trim();
  let nodeId = options.nodeId || state.currentNodeId;
  const previousNodeId = state.currentNodeId;
  if (targetNodeId) {
    state.currentNodeId = targetNodeId;
    nodeId = targetNodeId;
    persistSession();
  }

  state.sending = true;
  sendButton.disabled = true;
  closeNodeComposer();
  state.generatingNodeId = nodeId;
  markVisited(nodeId);
  // 切节点(点"下一个"、点 next_action 跳别处)时,立刻把右侧视口飘到目标卡片,
  // 让用户在 AI 回复之前就能"看到自己跳到了哪",对话和地图联动起来。
  if (targetNodeId && targetNodeId !== previousNodeId) {
    updateNodeVisualState();
    focusOnNodes([targetNodeId]);
    pulseNode(targetNodeId);
  }
  const userMessage = {
    id: `local_${Date.now()}`,
    role: "user",
    content: message,
    node_id: nodeId,
    created_at: new Date().toISOString(),
  };
  const assistantMessage = {
    id: `stream_${Date.now()}`,
    role: "assistant",
    content: "",
    node_id: nodeId,
    created_at: new Date().toISOString(),
    thinking: true,
    next_actions: [],
  };
  state.messages = [...state.messages, userMessage, assistantMessage];
  renderMessages();
  // 把刚发出的用户消息滚到顶部,这样下面流式生成的 AI 回复一出现就在视口里,
  // 用户可以从生成的第一行开始读,后续 token 不会再把视口往下拖。
  const userEl = messagesEl.querySelector(`[data-message-id="${userMessage.id}"]`);
  userEl?.scrollIntoView({ block: "start", behavior: "smooth" });
  updateNodeVisualState();

  try {
    const response = await fetch(`/api/sessions/${state.sessionId}/messages/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        current_node_id: nodeId,
        mode: state.mode,
        intent,
        promoted_title: promotedTitle || null,
        subdivision_angle: subdivisionAngle || null,
      }),
    });
    await readEventStream(response, {
      token(data) {
        assistantMessage.thinking = false;
        assistantMessage.content += data.text || "";
        renderMessages();
      },
      done(data) {
        assistantMessage.thinking = false;
        state.nodes = data.nodes || state.nodes;
        state.messages = data.messages || state.messages;
        const incomingNodeId = data.current_node_id || state.currentNodeId;
        const nodeChanged = incomingNodeId !== previousNodeId;
        state.currentNodeId = incomingNodeId;
        const createdIds = data.created_node_ids || [];
        state.newNodeIds = new Set(createdIds);
        if (createdIds.length) {
          state.newNodeEnterDelay = computeEnterDelays(
            state.nodes.filter((n) => createdIds.includes(n.id)),
          );
        }
        markVisited(state.currentNodeId);
        persistSession();
        render();
        if (createdIds.length) {
          // 让 stagger 先动起来一小段,再把视口滑过去,避免节点还没就位就跳
          setTimeout(() => {
            focusOnNodes([state.currentNodeId, ...createdIds]);
            pulseNode(state.currentNodeId);
          }, 320);
          setTimeout(() => {
            state.newNodeIds = new Set();
            state.newNodeEnterDelay = new Map();
            renderTree();
          }, 2600);
        } else if (nodeChanged) {
          // 没新建节点但 currentNodeId 改了(e.g. 后端把焦点移到别的节点)
          // 也要把视口飘过去
          setTimeout(() => {
            focusOnNodes([state.currentNodeId]);
            pulseNode(state.currentNodeId);
          }, 160);
        }
      },
    });
  } catch (error) {
    assistantMessage.thinking = false;
    assistantMessage.content = `请求失败：${error.message}`;
    renderMessages();
  } finally {
    state.sending = false;
    state.generatingNodeId = null;
    sendButton.disabled = false;
    updateNodeVisualState();
  }
}

async function readEventStream(response, handlers) {
  if (!response.ok || !response.body) {
    throw new Error(`HTTP ${response.status}`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() || "";
    for (const raw of events) {
      const eventName = (raw.match(/^event: (.+)$/m) || [])[1];
      const dataLine = (raw.match(/^data: (.+)$/m) || [])[1];
      if (!eventName || !dataLine) continue;
      const data = JSON.parse(dataLine);
      if (handlers[eventName]) handlers[eventName](data);
    }
  }
}

function persistSession() {
  if (state.sessionId) localStorage.setItem("km.sessionId", state.sessionId);
  if (state.currentNodeId) localStorage.setItem("km.currentNodeId", state.currentNodeId);
}

function markVisited(nodeId) {
  if (!nodeId) return;
  state.visited.add(nodeId);
  persistVisited();
}

function persistVisited() {
  localStorage.setItem("km.visitedNodes", JSON.stringify([...state.visited]));
}

function hydrateVisitedFromMessages() {
  state.visited = visitedFromMessages(state.messages);
  persistVisited();
}

function visitedFromMessages(messages) {
  const visited = new Set();
  for (const message of messages) {
    if (message.node_id && message.role === "user") visited.add(message.node_id);
  }
  return visited;
}

function endPanelResize(event) {
  if (!state.resizingPanels) return;
  state.resizingPanels = false;
  appShell.classList.remove("resizing");
  if (state.pendingChatWidth) {
    localStorage.setItem("km.chatWidth", state.pendingChatWidth);
    state.pendingChatWidth = null;
  }
  try {
    splitter.releasePointerCapture(event.pointerId);
  } catch {
    // Pointer capture may already be released.
  }
}

function endPan(_event) {
  if (!state.isPanning) return;
  state.isPanning = false;
  treeEl.classList.remove("is-panning");
  // 真正拖动了再 suppress click + 持久化新视口;没拖动就放过让 click 自然跑
  if (state.didDrag) {
    state.suppressNextClick = true;
    // 保险:click 没派出来时不要让 flag 一直留着
    setTimeout(() => { state.suppressNextClick = false; }, 50);
    persistViewport();
  }
  state.didDrag = false;
}

function persistViewport() {
  localStorage.setItem("km.zoom", String(Math.round(state.zoom)));
  localStorage.setItem("km.panX", String(Math.round(state.panX)));
  localStorage.setItem("km.panY", String(Math.round(state.panY)));
}

function resetViewport() {
  state.zoom = 88;
  state.panX = 0;
  state.panY = 0;
  state.hasViewport = false;
  persistViewport();
}

// 性能:pointermove 可能 ~120Hz,但只在每个动画帧应用一次 transform 就够。
// 多次 schedule 合并成一次 RAF,避免在一帧里跑多遍 DOM 写入。
let _vpFrame = null;
function scheduleViewportTransform() {
  if (_vpFrame !== null) return;
  _vpFrame = requestAnimationFrame(() => {
    _vpFrame = null;
    applyViewportTransform();
  });
}

// 给舞台挂上 .viewport-glide 过渡,用【单一】计时器在动画结束后撤掉。
// 关键:重叠的 pan(生长/拆到底每步都 pan)会 clear 并重置这个计时器,
// 否则前一段的 remove 会在后一段 glide 还没跑完时把过渡 class 撤掉 → 卡顿/跳变。
let _glideTimer = 0;
function glideStage(stage) {
  if (!stage) return;
  stage.classList.add("viewport-glide");
  if (_glideTimer) clearTimeout(_glideTimer);
  _glideTimer = setTimeout(() => {
    stage.classList.remove("viewport-glide");
    _glideTimer = 0;
  }, 640); // > CSS glide 的 560ms,留一点 buffer
}

function applyViewportTransform() {
  // map-stage 引用缓存到 state,避免每帧 querySelector
  let stage = state.mapStageEl;
  if (!stage || !stage.isConnected) {
    stage = treeEl.querySelector(".map-stage");
    state.mapStageEl = stage;
  }
  if (!stage) return;
  stage.style.transform = `translate3d(${state.panX}px, ${state.panY}px, 0) scale(${state.zoom / 100})`;
}

// zoom 变化才需要的"副作用"——更新滑块、LOD 数据属性。
// 从 applyViewportTransform 里抽出来,只在 wheel / zoomRange 触发,
// 不在 pan 热路径上重复跑。
function applyZoomCosmetics() {
  zoomRange.value = String(Math.round(state.zoom));
  const zoomLevel = state.zoom < 40 ? "low" : state.zoom < 80 ? "mid" : "high";
  if (treeEl.dataset.zoomLevel !== zoomLevel) treeEl.dataset.zoomLevel = zoomLevel;
}

function centerViewportOnCurrent(layout) {
  const rect = treeEl.getBoundingClientRect();
  const current = layout.items.find((item) => item.node.id === state.currentNodeId) || layout.items[0];
  const zoom = state.zoom / 100;
  state.panX = rect.width / 2 - current.x * zoom;
  state.panY = rect.height / 2 - current.y * zoom;
  state.hasViewport = true;
  persistViewport();
}

/**
 * 把视口居中到指定节点(保留当前 zoom)。
 * 用于"折叠/展开按钮被点 → 那张卡片应该平稳停在视口中心"这种局部布局变化。
 * focusOnNodes 会重新计算 zoom 来 fit-bbox,这里**不动 zoom**,只挪 pan。
 */
function centerViewportOnNode(nodeId, { animate = true } = {}) {
  if (!nodeId) return;
  const layout = state.lastLayout;
  if (!layout) return;
  const item = layout.items.find((it) => it.node.id === nodeId);
  if (!item) return;
  const rect = treeEl.getBoundingClientRect();
  const zoom = state.zoom / 100;
  state.panX = rect.width / 2 - item.x * zoom;
  state.panY = rect.height / 2 - item.y * zoom;
  state.hasViewport = true;
  persistViewport();
  const stage = treeEl.querySelector(".map-stage");
  if (animate && stage) {
    glideStage(stage);
    applyViewportTransform();
  } else {
    applyViewportTransform();
  }
}

// 只 pan、不改 zoom 的"跟随相机":生长动画专用。
// focusOnNodes 每次都会重算 zoom,如果布局正在重排(children 流式涌入),
// zoom 会跟着抖,视觉上看着像 flash。pan-only 没这个问题。
function panToNode(nodeId, { zoom } = {}) {
  if (!nodeId) return;
  const layout = state.lastLayout;
  if (!layout) return;
  const item = layout.items.find((it) => it.node.id === nodeId);
  if (!item) return;
  const rect = treeEl.getBoundingClientRect();
  // 没传 zoom 就保持当前;传了就锁到那个值
  const z = (zoom !== undefined ? zoom : state.zoom / 100);
  state.zoom = Math.round(z * 100);
  state.panX = rect.width / 2 - item.x * z;
  state.panY = rect.height / 2 - item.y * z;
  state.hasViewport = true;
  applyZoomCosmetics();
  const stage = treeEl.querySelector(".map-stage");
  if (stage) {
    glideStage(stage);
    applyViewportTransform();
  } else {
    applyViewportTransform();
  }
  persistViewport();
}

function focusOnNodes(nodeIds, { animate = true } = {}) {
  if (!nodeIds || !nodeIds.length) return;
  const layout = state.lastLayout;
  if (!layout) return;
  const items = layout.items.filter((it) => nodeIds.includes(it.node.id));
  if (!items.length) return;
  const xs = items.map((it) => it.x);
  const ys = items.map((it) => it.y);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const cx = (minX + maxX) / 2;
  const cy = (minY + maxY) / 2;

  const rect = treeEl.getBoundingClientRect();
  const padX = 360;
  const padY = 220;
  const bboxW = Math.max(maxX - minX + padX, 320);
  const bboxH = Math.max(maxY - minY + padY, 220);
  // 让 bbox 占 viewport 的 ~62%,在视觉上保留呼吸空间
  const fitZoom = Math.min((rect.width * 0.62) / bboxW, (rect.height * 0.62) / bboxH);
  const targetZoom = Math.max(0.32, Math.min(1.2, fitZoom));

  state.zoom = Math.round(targetZoom * 100);
  state.panX = rect.width / 2 - cx * targetZoom;
  state.panY = rect.height / 2 - cy * targetZoom;
  state.hasViewport = true;
  applyZoomCosmetics();

  const stage = treeEl.querySelector(".map-stage");
  if (animate && stage) {
    glideStage(stage);
    applyViewportTransform();
  } else {
    applyViewportTransform();
  }
  persistViewport();
}

function setZoom(next) {
  state.zoom = Math.max(10, Math.min(180, next));
  persistViewport();
  applyZoomCosmetics();
  applyViewportTransform();
}

function render() {
  const hasSession = Boolean(state.sessionId) || state.generatingTree;
  starter.classList.toggle("hidden", hasSession);
  workspace.classList.toggle("hidden", !hasSession);
  renderMessages();
  renderTree();
  if (state.chainPanel) renderChainPanel();
}

function renderMessages({ preserveScroll = false } = {}) {
  const savedScrollTop = preserveScroll ? messagesEl.scrollTop : null;
  clearSearchSourceTickers();
  messagesEl.innerHTML = "";
  // 算出"最后一条带 next_actions 的 assistant 消息",只在它下面渲染建议按钮
  let lastActionableIndex = -1;
  for (let i = state.messages.length - 1; i >= 0; i -= 1) {
    const m = state.messages[i];
    if (m.role === "assistant" && Array.isArray(m.next_actions) && m.next_actions.length) {
      lastActionableIndex = i;
      break;
    }
  }

  state.messages.forEach((message, index) => {
    const item = document.createElement("article");
    item.className = `message ${message.role === "user" ? "user" : "assistant"} ${
      message.thinking ? "thinking" : ""
    }`;
    if (message.node_id) item.dataset.nodeId = message.node_id;
    item.dataset.messageId = message.id || "";
    if (message.node_id) {
      item.classList.add("clickable");
      item.tabIndex = 0;
      // 点击导航的触发面:角色标签(用户/AI 教练那一行)。
      // 气泡留给文字选择;next-action 按钮自己处理。
      item.addEventListener("click", (event) => {
        if (event.target.closest(".thought-action-panel, .next-action, .peek-anchor, .search-sources")) return;
        if (event.target.closest(".bubble")) return;
        const selection = window.getSelection();
        if (selection && selection.toString().trim().length) return;
        // 传 item 作为 source,让 resonate 触发消息侧的脉冲动画
        focusOnMessageNode(message.node_id, item);
      });
      item.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          focusOnMessageNode(message.node_id, item);
        }
      });
    }
    const role = document.createElement("div");
    role.className = "role";
    const roleName = document.createElement("span");
    roleName.textContent = message.role === "user" ? "你" : "AI 教练";
    role.append(roleName);
    if (message.node_id) {
      const locate = document.createElement("span");
      locate.className = "message-locate";
      locate.textContent = "定位至对应卡片";
      role.append(locate);
    }
    if (message.created_at && message.id !== "stream_init") {
      const d = new Date(message.created_at);
      const t = document.createElement("time");
      t.className = "message-time";
      t.dateTime = message.created_at;
      t.textContent = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
      role.append(t);
    }
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    if (message.thinking && !message.content) {
      const dot = document.createElement("span");
      dot.className = "thinking-dot";
      const text = document.createElement("span");
      text.textContent = "AI正在思考如何高效学习……";
      bubble.append(dot, text);
    } else {
      paintBubbleContent(bubble, message);
    }
    item.append(role, bubble);
    const searchSources = renderSearchSources(message.search_sources || [], message);
    if (searchSources) item.append(searchSources);
    const thoughtActions = renderThoughtActions(
      message,
      index === lastActionableIndex && state.coachMode ? message.next_actions : [],
    );
    if (thoughtActions) {
      item.append(thoughtActions);
    }
    messagesEl.append(item);
  });
  const sentinel = document.createElement("div");
  sentinel.className = "message-bottom-sentinel";
  sentinel.setAttribute("aria-hidden", "true");
  messagesEl.append(sentinel);
  if (savedScrollTop !== null) {
    messagesEl.scrollTop = savedScrollTop;
  } else if (state.sending) {
    // 流式生成中不要跟着光标走,让用户从生成的第一行读起。
    // sendMessage 在 stream 开始前会显式把用户消息滚到顶部,这里就保持那个位置。
  } else {
    scrollMessagesToBottom();
  }
}

function renderSearchSources(sources, message = null) {
  const clean = (sources || []).filter((source) =>
    (source?.title || source?.link || source?.content || source?.query || source?.status || "").trim(),
  );
  if (!clean.length) return null;
  const shallowSources = clean.filter((source) => ["result", "empty", "error"].includes(source.status || "result"));
  const resultSources = shallowSources.filter((source) => (source.status || "result") === "result");
  const deepSources = clean.filter((source) =>
    ["deep_result", "deep_empty", "deep_error"].includes(source.status || "")
  );
  const deepResultSources = deepSources.filter((source) => source.status === "deep_result");
  const status = shallowSources[0]?.status || clean[0]?.status || "result";
  const block = document.createElement("div");
  block.className = "search-source-block";
  const wrap = document.createElement("div");
  wrap.className = "search-sources";
  wrap.setAttribute("role", "button");
  wrap.setAttribute("tabindex", "0");
  wrap.setAttribute("aria-label", "查看联网搜索结果");

  const summary = document.createElement("span");
  summary.className = "search-sources-summary";
  const query = (clean[0]?.query || "").trim();
  if (status === "error") {
    summary.textContent = "联网搜索结果 · 搜索失败";
  } else if (status === "empty") {
    summary.textContent = "联网搜索结果 · 命中 0 条";
  } else {
    summary.textContent = `联网搜索结果 · 命中 ${resultSources.length} 条`;
  }
  wrap.append(summary);

  const ticker = document.createElement("div");
  ticker.className = "search-sources-ticker";
  const track = document.createElement("div");
  track.className = "search-sources-ticker-track";
  const tickerSources = resultSources.length ? resultSources : clean;
  for (const source of tickerSources) {
    const title = (source.title || source.media || source.link || "网页来源").trim();
    const item = document.createElement("span");
    item.className = `search-source-ticker-item${source.link ? "" : " muted"}`;
    item.textContent = title;
    track.append(item);
  }

  ticker.append(track);
  wrap.append(ticker);
  startSearchSourceTicker(wrap, track, tickerSources.length);
  wrap.addEventListener("click", () => openSearchSourcesPopover(clean, wrap));
  wrap.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    openSearchSourcesPopover(clean, wrap);
  });
  block.append(wrap);

  return block;
}

async function runDeepSearch(messageId) {
  if (!messageId || deepSearchLoading.has(messageId)) return;
  deepSearchLoading.add(messageId);
  renderMessages({ preserveScroll: true });
  try {
    const response = await fetch(`/api/messages/${messageId}/deep-search`, { method: "POST" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const saved = await response.json();
    state.messages = state.messages.map((message) => (message.id === saved.id ? saved : message));
  } catch (error) {
    window.alert(`深度联网搜索失败：${error.message}`);
  } finally {
    deepSearchLoading.delete(messageId);
    renderMessages({ preserveScroll: true });
  }
}

async function reanswerWithDeepSearch(messageId) {
  if (!messageId || deepReanswerLoading.has(messageId)) return;
  deepReanswerLoading.add(messageId);
  renderMessages({ preserveScroll: true });
  let appended = false;
  try {
    const response = await fetch(`/api/messages/${messageId}/deep-reanswer`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: state.mode }),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const saved = await response.json();
    state.messages = [...state.messages, saved];
    appended = true;
  } catch (error) {
    window.alert(`重新回答失败：${error.message}`);
  } finally {
    deepReanswerLoading.delete(messageId);
    renderMessages({ preserveScroll: !appended });
    if (appended) scrollMessagesToBottom();
  }
}

function clearSearchSourceTickers() {
  searchSourceTickerTimers.forEach((timer) => clearInterval(timer));
  searchSourceTickerTimers.clear();
}

function startSearchSourceTicker(host, track, count) {
  if (!host || !track || count <= 1) return;
  let index = 0;
  let paused = false;
  const pause = () => {
    paused = true;
  };
  const resume = () => {
    paused = false;
  };
  host.addEventListener("mouseenter", pause);
  host.addEventListener("mouseleave", resume);
  host.addEventListener("focusin", pause);
  host.addEventListener("focusout", resume);
  const timer = setInterval(() => {
    if (paused || !document.body.contains(host)) return;
    index = (index + 1) % count;
    const itemHeight = track.firstElementChild?.getBoundingClientRect().height || 18;
    track.style.transform = `translateY(-${index * itemHeight}px)`;
  }, 2600);
  searchSourceTickerTimers.add(timer);
}

function openSearchSourcesPopover(sources, anchor = null) {
  const clean = (sources || []).filter((source) =>
    (source?.title || source?.link || source?.content || source?.query || source?.status || "").trim(),
  );
  if (!clean.length) return;
  closeSearchSourcesPopover();

  const resultCount = clean.filter((source) => ["result", "deep_result"].includes(source.status || "result")).length;
  const query = (clean.find((source) => source.query)?.query || "").trim();
  const isDeepList = clean.some((source) => String(source.status || "").startsWith("deep_"));
  const isLoading = clean.length === 1 && clean[0].status === "searching";
  const isError = clean.length === 1 && clean[0].status === "error";
  const isEmpty = clean.length === 1 && clean[0].status === "empty";
  const popover = document.createElement("aside");
  popover.className = "search-sources-popover";
  popover.setAttribute("role", "dialog");
  popover.setAttribute("aria-label", "联网搜索结果");

  const head = document.createElement("div");
  head.className = "search-sources-popover-head";
  const titleWrap = document.createElement("div");
  const eyebrow = document.createElement("span");
  eyebrow.className = "search-sources-popover-eyebrow";
  eyebrow.textContent = isDeepList ? "深度联网搜索" : "划词联网搜索";
  const title = document.createElement("strong");
  title.textContent = query ? `「${query}」` : "搜索结果";
  const meta = document.createElement("span");
  meta.className = "search-sources-popover-meta";
  if (isLoading) {
    meta.textContent = "正在联网检索,稍等几秒…";
  } else if (isError) {
    meta.textContent = "搜索失败,可能是 SEARCH_PROVIDER 没配置或网络不通";
  } else if (isEmpty) {
    meta.textContent = "搜索完成,无可用结果";
  } else if (isDeepList) {
    meta.textContent = `返回 ${resultCount} 篇 · 可交给 AI 重新回答`;
  } else {
    meta.textContent = `命中 ${resultCount} 条 · 列表含未展开展示的来源`;
  }
  titleWrap.append(eyebrow, title, meta);
  const close = document.createElement("button");
  close.type = "button";
  close.className = "search-sources-popover-close";
  close.setAttribute("aria-label", "关闭");
  close.title = "关闭";
  close.dataset.tooltip = "关闭联网搜索结果列表";
  close.textContent = "×";
  close.addEventListener("click", closeSearchSourcesPopover);
  head.append(titleWrap, close);
  popover.append(head);

  const list = document.createElement("div");
  list.className = "search-sources-popover-list";
  clean.forEach((source, index) => {
    const status = source.status || "result";
    const item = document.createElement(source.link ? "a" : "div");
    const isResultLike = ["result", "deep_result"].includes(status);
    item.className = `search-source-detail${isResultLike ? "" : " muted"}`;
    if (source.link) {
      item.href = source.link;
      item.target = "_blank";
      item.rel = "noreferrer";
    }
    const number = document.createElement("span");
    number.className = "search-source-detail-index";
    number.textContent = String(index + 1).padStart(2, "0");
    const body = document.createElement("span");
    body.className = "search-source-detail-body";
    const line = document.createElement("span");
    line.className = "search-source-detail-title";
    line.textContent = (source.title || source.media || source.link || source.query || "搜索记录").trim();
    const desc = document.createElement("span");
    desc.className = "search-source-detail-desc";
    desc.textContent = (
      source.content
      || (status === "empty" || status === "deep_empty" ? "搜索完成，但没有命中可用结果。" : "")
      || ""
    ).trim();
    const foot = document.createElement("span");
    foot.className = "search-source-detail-foot";
    const origin = [source.media, source.publish_date, source.refer].filter(Boolean).join(" · ");
    foot.textContent = origin || (status === "deep_result"
      ? "深度搜索结果，可给 AI 参考"
      : status === "result" ? "已传给 AI 参考" : "仅作为状态展示");
    body.append(line);
    if (desc.textContent) body.append(desc);
    body.append(foot);
    item.append(number, body);
    list.append(item);
  });
  popover.append(list);

  document.body.append(popover);
  positionSearchSourcesPopover(popover, anchor);
  requestAnimationFrame(() => popover.classList.add("visible"));
  document.addEventListener("keydown", onSearchSourcesEscape);
  document.addEventListener("mousedown", onSearchSourcesOutside, true);
}

function positionSearchSourcesPopover(popover, anchor) {
  const viewportGap = 16;
  const width = Math.min(420, Math.max(300, window.innerWidth - viewportGap * 2));
  popover.style.width = `${width}px`;
  // 没有 anchor 时(老调用方式)兜底居中 + 视口高度限制
  if (!anchor) {
    popover.style.maxHeight = `${Math.max(280, window.innerHeight - viewportGap * 2)}px`;
    popover.style.left = `${Math.round((window.innerWidth - width) / 2)}px`;
    popover.style.top = `${Math.round((window.innerHeight - Math.min(popover.scrollHeight, window.innerHeight - viewportGap * 2)) / 2)}px`;
    return;
  }
  const rect = anchor.getBoundingClientRect();
  let left = rect.left + rect.width / 2 - width / 2;
  left = Math.max(viewportGap, Math.min(left, window.innerWidth - width - viewportGap));

  const spaceBelow = window.innerHeight - rect.bottom - viewportGap - 10;
  const spaceAbove = rect.top - viewportGap - 10;
  const preferBelow = spaceBelow >= 320 || spaceBelow >= spaceAbove;
  const availableHeight = Math.max(280, preferBelow ? spaceBelow : spaceAbove);
  popover.style.maxHeight = `${Math.min(availableHeight, window.innerHeight - viewportGap * 2)}px`;

  const measuredHeight = Math.min(popover.scrollHeight, parseFloat(popover.style.maxHeight));
  let top = preferBelow ? rect.bottom + 10 : rect.top - measuredHeight - 10;
  top = Math.max(viewportGap, Math.min(top, window.innerHeight - measuredHeight - viewportGap));

  popover.style.left = `${Math.round(left)}px`;
  popover.style.top = `${Math.round(top)}px`;
}

function closeSearchSourcesPopover() {
  const popover = document.querySelector(".search-sources-popover");
  document.removeEventListener("keydown", onSearchSourcesEscape);
  document.removeEventListener("mousedown", onSearchSourcesOutside, true);
  if (!popover) return;
  popover.classList.remove("visible");
  setTimeout(() => popover.remove(), 120);
}

function onSearchSourcesEscape(event) {
  if (event.key === "Escape") closeSearchSourcesPopover();
}

function onSearchSourcesOutside(event) {
  const popover = document.querySelector(".search-sources-popover");
  if (!popover) return;
  if (popover.contains(event.target)) return;
  // 点 search-sources 触发器再次切换;别在自己的触发器上关掉又被立刻打开
  if (event.target.closest(".search-sources, .search-tool-action")) return;
  closeSearchSourcesPopover();
}

// "AI 正在算"的 3 点跳动占位符 HTML —— 主对话 / peek 卡 / followup 都用同一套
function loadingDotsHTML(label = "正在解释") {
  return `<span class="ai-loading-line">${escapeHtml(label)} <span class="ai-loading-dots"><span></span><span></span><span></span></span></span>`;
}

// 假流式:拿到完整文本后用 setTimeout 一段一段写入元素,模拟"逐字出现"。
// peek 调一次 LLM 拿完整答案,做不了真 SSE;前端假流给到同样的体感(且更快)。
// 用户中途关掉 popover → el.isConnected 变 false,自动停止,不报错。
function typeIntoElement(el, fullText, { intervalMs = 6, chunkSize = 2 } = {}) {
  return new Promise((resolve) => {
    if (!el || !el.isConnected) return resolve();
    el.textContent = "";
    let i = 0;
    const step = () => {
      if (!el.isConnected) return resolve();
      i = Math.min(i + chunkSize, fullText.length);
      el.textContent = fullText.slice(0, i);
      if (el.closest(".peek-popover")) scheduleOpenPeekReposition();
      if (i >= fullText.length) return resolve();
      setTimeout(step, intervalMs);
    };
    step();
  });
}

// 只在 peek 已经在栈里(popover 开着)时重画它的内容 —— 避免意外把没显示的卡 push 进栈。
// 用途:嵌套 peek 创建后,父 popover 的 answer 区需要重画,新加的 anchor 才会出现。
function refreshOpenPeekPopover(messageId, peekId) {
  if (!state.peekStack.some((s) => s.peekId === peekId)) return;
  openPeekPopover(messageId, peekId);
}

// peek-answer 上的"已经被深挖过"标记:把 message.peeks 里所有 parent_peek_id === peek.id
// 的子 peek 的 [start, end) 用 .peek-anchor 套出来,复用 bubble 上一样的视觉。
// 用户点 anchor → 重新打开那张子 peek 卡(stack 自动 trim 到正确层)。
function paintPeekAnswerContent(answerEl, message, peek) {
  answerEl.textContent = peek.answer || "";
  const children = (message.peeks || []).filter(
    (p) => p.parent_peek_id === peek.id && (p.source_kind || "answer") === "answer"
  );
  if (!children.length) return;
  const ranges = children
    .map((cp) => ({
      type: "peek",
      id: cp.id,
      start: Number(cp.start),
      end: Number(cp.end),
    }))
    .filter(
      (r) => Number.isFinite(r.start) && Number.isFinite(r.end) && r.end > r.start,
    )
    .sort((a, b) => a.start - b.start);
  for (const range of ranges) {
    // wrapRangeInDom 内部 wrapTextSegment 给 mark 挂 click → openPeekPopover(message.id, range.id)
    // 复用同一份代码,nested peek 点回去自动走栈管理
    wrapRangeInDom(answerEl, range, message);
  }
}

function paintPeekFollowupContent(answerEl, message, peek, followup) {
  answerEl.textContent = followup.answer || "";
  const children = (message.peeks || []).filter(
    (p) =>
      p.parent_peek_id === peek.id &&
      (p.source_kind || "answer") === "followup" &&
      (p.source_followup_id || null) === (followup.id || null)
  );
  if (!children.length) return;
  const ranges = children
    .map((cp) => ({
      type: "peek",
      id: cp.id,
      start: Number(cp.start),
      end: Number(cp.end),
    }))
    .filter((r) => Number.isFinite(r.start) && Number.isFinite(r.end) && r.end > r.start)
    .sort((a, b) => a.start - b.start);
  for (const range of ranges) {
    wrapRangeInDom(answerEl, range, message);
  }
}

function paintBubbleContent(bubble, message) {
  bubble.textContent = "";
  const content = message.content || "";
  if (!content) return;
  const shouldRenderRichText = message.role === "assistant";
  // 先把 Markdown 渲染出来。这样划词偏移(基于 range.toString())和 DOM
  // 文本节点 textContent 的累积长度是同一个空间，下面套 mark 才不会错位。
  if (shouldRenderRichText) {
    renderRichText(bubble, content);
  } else {
    bubble.textContent = content;
  }

  const ranges = [];
  // 只画根 peek (parent_peek_id 为空):嵌套 peek 的 start/end 是相对父答案的偏移,
  // 放到消息正文里位置全错 —— 而且嵌套 peek 该出现在父 peek 卡的 answer 里,不该在 bubble 里
  for (const peek of (message.peeks || []).filter((p) => !p.parent_peek_id)) {
    ranges.push({
      type: "peek",
      id: peek.id,
      start: Number(peek.start),
      end: Number(peek.end),
    });
  }
  for (const highlight of message.highlights || []) {
    ranges.push({
      type: "highlight",
      id: "",
      start: Number(highlight.start),
      end: Number(highlight.end),
    });
  }
  const cleanRanges = ranges
    .filter((h) => Number.isFinite(h.start) && Number.isFinite(h.end) && h.end > h.start)
    .map((h) => ({ ...h, start: Math.max(0, h.start), end: h.end }))
    .sort((a, b) => a.start - b.start || (a.type === "peek" ? -1 : 1));
  if (!cleanRanges.length) return;

  for (const range of cleanRanges) {
    wrapRangeInDom(bubble, range, message);
  }
}

// 在已渲染的 DOM 里给 [range.start, range.end) 这段渲染后文本套上 <mark>。
// 长度沿用 textContent 累积，和 Range.toString() 的语义一致。
function wrapRangeInDom(root, range, message) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
  let offset = 0;
  const segments = [];
  while (walker.nextNode()) {
    const node = walker.currentNode;
    const len = node.textContent.length;
    // 已经在 mark 里的文本不再重复套(避免重叠区间套两层)，
    // 但长度仍然要计入累积偏移。
    if (node.parentElement?.closest("mark")) {
      offset += len;
      continue;
    }
    const nodeStart = offset;
    const nodeEnd = offset + len;
    if (nodeEnd > range.start && nodeStart < range.end) {
      segments.push({
        node,
        localStart: Math.max(0, range.start - nodeStart),
        localEnd: Math.min(len, range.end - nodeStart),
      });
    }
    offset = nodeEnd;
    if (offset >= range.end) break;
  }

  for (const seg of segments) {
    wrapTextSegment(seg, range, message);
  }
}

function wrapTextSegment({ node, localStart, localEnd }, range, message) {
  if (localStart >= localEnd) return;
  const text = node.textContent;
  const beforeText = text.slice(0, localStart);
  const matchText = text.slice(localStart, localEnd);
  const afterText = text.slice(localEnd);

  const mark = document.createElement("mark");
  if (range.type === "peek") {
    mark.className = "peek-anchor";
    mark.dataset.messageId = message.id || "";
    mark.dataset.peekId = range.id || "";
    mark.tabIndex = 0;
    mark.title = "查看速览解释";
    mark.dataset.tooltip = "打开这个词的速览卡片，可继续追问或展开成分支";
    mark.addEventListener("click", (event) => {
      event.stopPropagation();
      openPeekPopover(message.id, range.id);
    });
    mark.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openPeekPopover(message.id, range.id);
      }
    });
  } else if (range.type === "highlight") {
    // 高亮点击 → 弹出小菜单(取消高亮)
    mark.dataset.messageId = message.id || "";
    mark.dataset.highlightStart = String(range.start);
    mark.dataset.highlightEnd = String(range.end);
    mark.tabIndex = 0;
    mark.title = "点击可取消高亮";
    mark.dataset.tooltip = "打开高亮菜单，可取消这段高亮";
    const trigger = (event) => {
      event.stopPropagation();
      event.preventDefault();
      // 用户正在划新词时不要误触发高亮菜单
      const sel = window.getSelection();
      if (sel && sel.toString().trim() && !mark.contains(sel.anchorNode)) return;
      openHighlightMenu(mark, message.id, range.start, range.end);
    };
    mark.addEventListener("click", trigger);
    mark.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") trigger(event);
    });
  }
  mark.textContent = matchText;

  const fragment = document.createDocumentFragment();
  if (beforeText) fragment.append(document.createTextNode(beforeText));
  fragment.append(mark);
  if (afterText) fragment.append(document.createTextNode(afterText));

  node.replaceWith(fragment);
}

function renderRichText(container, rawText) {
  // 容忍 AI 没在 ### 标题前后留空行的情况：把孤零零一行的 # 标题强制
  // 提升成自己的 block，避免被吞进上一段。
  const text = rawText
    .replace(/\r\n/g, "\n")
    .replace(/([^\n])\n(#{1,6}\s+)/g, "$1\n\n$2")
    .replace(/(#{1,6}\s+[^\n]+)\n(?!\n|#)/g, "$1\n\n")
    .trim();
  if (!text) return;
  const blocks = text.split(/\n{2,}/).map((part) => part.trim()).filter(Boolean);
  for (const block of blocks) {
    renderBlock(container, block);
  }
}

function renderBlock(container, block) {
  const lines = block.split("\n").map((line) => line.trim()).filter(Boolean);
  if (!lines.length) return;

  // 整段都是列表项 → ul / ol
  const isList = lines.every((line) => /^([-*]|[0-9]+[.)])\s+/.test(line));
  if (isList) {
    const ordered = lines.every((line) => /^[0-9]+[.)]\s+/.test(line));
    const list = document.createElement(ordered ? "ol" : "ul");
    for (const line of lines) {
      const item = document.createElement("li");
      appendInlineMarkdown(item, line.replace(/^([-*]|[0-9]+[.)])\s+/, ""));
      list.append(item);
    }
    container.append(list);
    return;
  }

  // 按行扫，遇到 ### 或 **xxx** 单行就刷出当前段落，开一个标题
  let paragraph = null;
  const flush = () => {
    if (paragraph) {
      container.append(paragraph);
      paragraph = null;
    }
  };
  for (const line of lines) {
    const hashHeading = line.match(/^(#{1,6})\s+(.+?)\s*$/);
    const boldHeading = line.match(/^\*\*([^*]{2,80})\*\*[:：]?$/);
    if (hashHeading) {
      flush();
      const heading = document.createElement("h3");
      appendInlineMarkdown(heading, hashHeading[2].trim());
      container.append(heading);
    } else if (boldHeading) {
      flush();
      const heading = document.createElement("h3");
      heading.textContent = boldHeading[1].trim();
      container.append(heading);
    } else {
      if (!paragraph) paragraph = document.createElement("p");
      else paragraph.append(document.createElement("br"));
      appendInlineMarkdown(paragraph, line);
    }
  }
  flush();
}

function appendInlineMarkdown(parent, text) {
  const pattern = /\*\*([^*]+)\*\*/g;
  let cursor = 0;
  let match;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > cursor) parent.append(document.createTextNode(text.slice(cursor, match.index)));
    const strong = document.createElement("strong");
    strong.textContent = match[1];
    parent.append(strong);
    cursor = match.index + match[0].length;
  }
  if (cursor < text.length) parent.append(document.createTextNode(text.slice(cursor)));
}

function findPeek(messageId, peekId) {
  const message = state.messages.find((m) => m.id === messageId);
  const peek = message?.peeks?.find((p) => p.id === peekId);
  return { message, peek };
}

function openPeekPopover(messageId, peekId, { scrollToBottom = false, replaceLocal = null } = {}) {
  const { message, peek } = findPeek(messageId, peekId);
  if (!message || !peek) return;

  // 已经栈里有这个 peek?直接更新内容,不重复 push
  let stackIndex = state.peekStack.findIndex((s) => s.peekId === peekId);
  // 后端写库返回新 id 后,要把 replaceLocal 那个 local id 替换掉
  if (stackIndex < 0 && replaceLocal) {
    stackIndex = state.peekStack.findIndex((s) => s.peekId === replaceLocal);
    if (stackIndex >= 0) state.peekStack[stackIndex].peekId = peekId;
  }
  if (stackIndex < 0) {
    // 打开一个新 peek 前,把栈修剪成"从根到本 peek 的祖先链"——
    // 否则点兄弟 anchor 时栈会乱叠成 [level1, childA, childB],childB 错位到 depth 2
    const parentId = peek.parent_peek_id || null;
    if (parentId === null) {
      // 新根:清空整栈再开
      const old = state.peekStack.splice(0);
      for (const item of old) {
        const el = document.querySelector(
          `.peek-popover[data-peek-id="${cssEscape(item.peekId)}"]`,
        );
        if (el) {
          el.classList.remove("visible");
          setTimeout(() => el.remove(), 120);
        }
      }
    } else {
      // 父在栈里 → 砍掉父之后的所有(它们是兄弟分支,不该和当前 peek 同时显示)
      const parentIdx = state.peekStack.findIndex((s) => s.peekId === parentId);
      if (parentIdx >= 0 && parentIdx < state.peekStack.length - 1) {
        const removed = state.peekStack.splice(parentIdx + 1);
        for (const item of removed) {
          const el = document.querySelector(
            `.peek-popover[data-peek-id="${cssEscape(item.peekId)}"]`,
          );
          if (el) {
            el.classList.remove("visible");
            setTimeout(() => el.remove(), 120);
          }
        }
      }
    }
    state.peekStack.push({ messageId, peekId });
    stackIndex = state.peekStack.length - 1;
  }
  const depth = stackIndex;
  const totalDepth = state.peekStack.length;

  let popover = document.querySelector(`.peek-popover[data-peek-id="${cssEscape(peekId)}"]`);
  // local id 升级为 server id 时找老 element
  if (!popover && replaceLocal) {
    popover = document.querySelector(`.peek-popover[data-peek-id="${cssEscape(replaceLocal)}"]`);
    if (popover) popover.dataset.peekId = peekId;
  }
  if (!popover) {
    popover = document.createElement("aside");
    popover.className = "peek-popover";
    popover.setAttribute("role", "dialog");
    popover.dataset.peekId = peekId;
    popover.dataset.messageId = messageId;
    document.body.append(popover);
    // peek popover 挂在 document.body 下,不在 messagesEl 里,所以 messagesEl 的
    // mouseup/keyup 选区监听冒泡不到这里。在 popover 自己上也挂一份,
    // 让 .peek-answer 里的划词也能触发选区菜单(嵌套 peek 的入口)
    popover.addEventListener("mouseup", () => setTimeout(maybeShowSelectionMenu, 0));
    popover.addEventListener("keyup", (event) => {
      if (event.shiftKey || event.key.startsWith("Arrow")) maybeShowSelectionMenu();
    });
  }
  popover.dataset.depth = String(depth);
  popover.style.setProperty("--peek-depth", String(depth));

  const followups = (peek.followups || [])
    .map((item) => {
      // pending followup(本地 placeholder)用动画 dots,而不是显示"正在解释…"
      const isThinking = item.status === "thinking";
      const answerHTML = isThinking ? loadingDotsHTML("正在解释") : escapeHtml(item.answer || "");
      return `
        <div class="peek-followup"${isThinking ? ' data-loading="1"' : ""} data-followup-id="${escapeHtml(item.id || "")}">
          <strong>${escapeHtml(item.question || "")}</strong>
          <p>${answerHTML}</p>
        </div>
      `;
    })
    .join("");

  // 锚点回显:嵌套 peek 顶部显示"从「父锚点 text」展开",建立溯源
  const parentPeek = peek.parent_peek_id
    ? (message.peeks || []).find((p) => p.id === peek.parent_peek_id)
    : null;
  const anchorEcho = parentPeek
    ? `<div class="peek-anchor-echo">从「${escapeHtml(parentPeek.text || "")}」展开</div>`
    : "";

  // 深于 4 层 → 轻提示用户已经挖很深
  const deepHint = depth >= 4
    ? `<div class="peek-deep-hint">你已经挖到第 ${depth + 1} 层 —— 想清楚再继续,或先回到主对话整理一下。</div>`
    : "";

  // 全部关闭按钮:栈深 ≥ 2 时,在最深的卡上显示
  const isTop = depth === totalDepth - 1;
  const closeAllBtn = totalDepth > 1 && isTop
    ? `<button type="button" class="peek-close-all" data-tooltip="一键关闭所有速览卡片">全部关闭</button>`
    : "";

  popover.innerHTML = `
    <div class="peek-head">
      <span class="peek-depth-chip">层 ${depth + 1}${totalDepth > 1 ? ` / ${totalDepth}` : ""}</span>
      <span class="peek-head-title">速览</span>
      <button type="button" class="peek-close" data-tooltip="关闭这一层(及更深的)" title="关闭">×</button>
    </div>
    ${anchorEcho}
    <div class="peek-scroll">
      <div class="peek-term">${escapeHtml(peek.text || "")}</div>
      <div class="peek-answer"></div>
      ${followups ? `<div class="peek-followups">${followups}</div>` : ""}
    </div>
    ${deepHint}
    <form class="peek-form">
      <input name="question" autocomplete="off" placeholder="这里继续问，不刷走对话" />
      <button type="submit" data-tooltip="只追问这个速览词，不会刷新整段对话">问</button>
    </form>
    <div class="peek-actions">
      <button type="button" data-peek-action="promote" data-tooltip="把这个速览词升级为知识树里的正式分支，AI 会围绕它生成子节点">展开成分支</button>
      <button type="button" data-peek-action="quote" data-tooltip="把速览内容放到输入框上方，作为下一次提问的引用上下文">引用到输入框</button>
      ${closeAllBtn}
    </div>
  `;

  // 给 .peek-answer 套上 mark:子 peek 的 [start, end) 范围会被 .peek-anchor 标记;
  // 但 peek.status === "thinking" 时 answer 还是占位符,这时显示 3 点动画 loading,
  // API 返回后由 createPeek 直接 typeIntoElement 写入。
  const answerEl = popover.querySelector(".peek-answer");
  if (answerEl) {
    if (peek.status === "thinking") {
      answerEl.innerHTML = loadingDotsHTML("正在解释");
    } else {
      paintPeekAnswerContent(answerEl, message, peek);
    }
  }
  popover.querySelectorAll(".peek-followup").forEach((itemEl) => {
    const followupId = itemEl.dataset.followupId || "";
    const followup = (peek.followups || []).find((item) => item.id === followupId);
    const answer = itemEl.querySelector("p");
    if (followup && answer && followup.status !== "thinking") {
      paintPeekFollowupContent(answer, message, peek, followup);
    }
  });

  const currentPopoverPeekId = () => popover.dataset.peekId || peekId;
  popover.querySelector(".peek-close").addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    closePeekPopover(currentPopoverPeekId());
  });
  popover.querySelector(".peek-close-all")?.addEventListener("click", closeAllPeekPopovers);
  const submitPeekQuestion = async () => {
    const form = popover.querySelector(".peek-form");
    if (form?.dataset.submitting === "1") return;
    const input = popover.querySelector("input[name='question']");
    if (!input) return;
    const question = input?.value.trim() || "";
    if (!question) return;
    if (form) form.dataset.submitting = "1";
    input.value = "";
    try {
      await createPeekFollowup(messageId, currentPopoverPeekId(), question);
    } finally {
      if (form?.isConnected) delete form.dataset.submitting;
    }
  };
  popover.querySelector(".peek-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    event.stopPropagation();
    await submitPeekQuestion();
  });
  popover.querySelector("input[name='question']")?.addEventListener("keydown", async (event) => {
    if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
    event.preventDefault();
    event.stopPropagation();
    await submitPeekQuestion();
  });
  popover.querySelector("[data-peek-action='promote']").addEventListener("click", async () => {
    closeAllPeekPopovers();
    await sendMessage(`请把「${peek.text}」展开成一个真正的学习分支。`, {
      intent: "subdivide",
      nodeId: message.node_id || state.currentNodeId,
      promotedTitle: peek.text || "",
    });
  });
  popover.querySelector("[data-peek-action='quote']").addEventListener("click", () => {
    const latestPeek = findPeek(messageId, currentPopoverPeekId()).peek || peek;
    quoteToChatInput(`${latestPeek.text}: ${latestPeek.answer}`);
    closeAllPeekPopovers();
  });

  positionPeekPopover(popover, messageId, peekId, depth);
  requestAnimationFrame(() => {
    positionPeekPopover(popover, messageId, peekId, depth);
    popover.classList.add("visible");
    if (scrollToBottom) scrollPeekPopoverToBottom(popover);
    const input = popover.querySelector("input[name='question']");
    input?.focus();
  });
}

function scrollPeekPopoverToBottom(popover = document.querySelector(".peek-popover")) {
  const scrollArea = popover?.querySelector(".peek-scroll");
  if (!scrollArea) return;
  scrollArea.scrollTop = scrollArea.scrollHeight;
}

function positionPeekPopover(popover, messageId, peekId, depth = 0) {
  const viewportGap = 16;
  const width = Math.min(468, Math.max(320, window.innerWidth - viewportGap * 2));
  popover.style.width = `${width}px`;
  const viewportHeight = window.visualViewport?.height || window.innerHeight;
  const viewportWidth = window.visualViewport?.width || window.innerWidth;
  popover.style.maxHeight = `${Math.max(260, viewportHeight - viewportGap * 2)}px`;

  if (depth === 0) {
    // 根 peek:锚在消息正文里的下划线
    const anchor = messagesEl.querySelector(
      `.peek-anchor[data-message-id="${cssEscape(messageId)}"][data-peek-id="${cssEscape(peekId)}"]`
    );
    if (!anchor) return;
    const rect = anchor.getBoundingClientRect();
    let left = rect.left + rect.width / 2 - width / 2;
    left = Math.max(viewportGap, Math.min(left, viewportWidth - width - viewportGap));
    const spaceBelow = viewportHeight - rect.bottom - viewportGap - 10;
    const spaceAbove = rect.top - viewportGap - 10;
    const preferBelow = spaceBelow >= 300 || spaceBelow >= spaceAbove;
    const availableHeight = Math.max(220, Math.min(
      preferBelow ? spaceBelow : spaceAbove,
      viewportHeight - viewportGap * 2,
    ));
    const measuredHeight = Math.min(popover.scrollHeight || availableHeight, availableHeight);
    let top = preferBelow ? rect.bottom + 10 : rect.top - measuredHeight - 10;
    top = Math.max(viewportGap, Math.min(top, viewportHeight - measuredHeight - viewportGap));
    const maxAllowedH = Math.max(220, viewportHeight - top - viewportGap);
    applyPeekViewportBox(popover, maxAllowedH);
    popover.style.left = `${Math.round(left)}px`;
    popover.style.top = `${Math.round(top)}px`;
    return;
  }

  // 嵌套层:从父 popover 的右下角偏移,逐层往右挪,自然占据右侧画布
  const parentPopover = document.querySelector(
    `.peek-popover[data-depth="${depth - 1}"]`
  );
  if (!parentPopover) return;
  const parentRect = parentPopover.getBoundingClientRect();
  const OFFSET_X = 40;
  const OFFSET_Y = 30;
  let left = parentRect.left + OFFSET_X;
  let top = parentRect.top + OFFSET_Y;

  // 横向:屏幕右侧放不下 → 堆叠覆盖,覆盖父卡片,只露 40px 边
  if (left + width > viewportWidth - viewportGap) {
    left = viewportWidth - width - viewportGap - depth * 2;
  }

  // 纵向防越界(关键修复):
  //   1. min-height 决定卡至少多高(CSS 里是 min(420, viewport-32))
  //   2. top 必须满足 top + minH <= viewport - gap,否则向上推
  //   3. max-height 强制锁到 "viewport - top - gap",这样无论内容多长 popover 都不超出视口
  //      —— 之前只看 scrollHeight 而首次渲染时只有 loading dots,所以 typeIntoElement 把答案写进去
  //      后 popover 突破下边界。max-height 锁死后内容超出由 peek-scroll 内部滚动接管
  const minH = Math.min(420, viewportHeight - viewportGap * 2);
  top = Math.max(viewportGap, Math.min(top, viewportHeight - minH - viewportGap));
  const maxAllowedH = Math.max(220, viewportHeight - top - viewportGap);
  applyPeekViewportBox(popover, maxAllowedH);

  popover.style.left = `${Math.round(left)}px`;
  popover.style.top = `${Math.round(top)}px`;
}

function applyPeekViewportBox(popover, maxAllowedH) {
  const height = Math.max(220, Math.floor(maxAllowedH));
  popover.style.height = `${height}px`;
  popover.style.maxHeight = `${height}px`;
  popover.style.minHeight = `${Math.min(420, height)}px`;
}

let peekFitRaf = 0;
function scheduleOpenPeekReposition() {
  if (peekFitRaf) return;
  peekFitRaf = requestAnimationFrame(() => {
    peekFitRaf = 0;
    document.querySelectorAll(".peek-popover").forEach((popover) => {
      positionPeekPopover(
        popover,
        popover.dataset.messageId || "",
        popover.dataset.peekId || "",
        Number(popover.dataset.depth || 0),
      );
    });
  });
}

window.addEventListener("resize", scheduleOpenPeekReposition);
window.visualViewport?.addEventListener("resize", scheduleOpenPeekReposition);
window.visualViewport?.addEventListener("scroll", scheduleOpenPeekReposition);

// 关闭一张 peek 卡 + 它在栈里之后的所有后代。
// 不传 peekId 兼容老用法,默认关栈顶。
function closePeekPopover(peekId = null) {
  if (!state.peekStack.length) return;
  let cutFrom;
  if (peekId == null) {
    cutFrom = state.peekStack.length - 1;
  } else {
    cutFrom = state.peekStack.findIndex((s) => s.peekId === peekId);
    if (cutFrom < 0) return;
  }
  const removed = state.peekStack.splice(cutFrom);
  for (const item of removed) {
    const el = document.querySelector(
      `.peek-popover[data-peek-id="${cssEscape(item.peekId)}"]`,
    );
    if (!el) continue;
    el.classList.remove("visible");
    setTimeout(() => el.remove(), 120);
  }
  // 关掉之后,栈顶卡需要 re-render(全部关闭按钮可能要从"无"变"有"或反过来)
  const top = state.peekStack[state.peekStack.length - 1];
  if (top) openPeekPopover(top.messageId, top.peekId);
}

function closeAllPeekPopovers() {
  if (!state.peekStack.length) return;
  const removed = state.peekStack.splice(0);
  for (const item of removed) {
    const el = document.querySelector(
      `.peek-popover[data-peek-id="${cssEscape(item.peekId)}"]`,
    );
    if (!el) continue;
    el.classList.remove("visible");
    setTimeout(() => el.remove(), 120);
  }
}

// === 拆分浮层 ===
// 卡片上点"拆分" → 调后端拿 AI 建议的 3 个角度 + 1 个"先别拆" → 用户选一个。
// 选角度 → sendMessage(intent=subdivide, subdivision_angle)
// 选"先别拆" → 调 caution-note,把 AI 的理由作为一条 assistant 消息塞回对话
function openSubdividePopover(nodeId) {
  const node = state.nodes.find((n) => n.id === nodeId);
  if (!node) return;
  closeSubdividePopover();
  state.subdividePopoverNodeId = nodeId;

  const popover = document.createElement("aside");
  popover.className = "subdivide-popover";
  popover.setAttribute("role", "dialog");
  popover.setAttribute("aria-modal", "true");
  popover.innerHTML = `
    <div class="subdivide-head">
      <div>
        <div class="subdivide-eyebrow">想怎么拆开它</div>
        <div class="subdivide-title">「${escapeHtml(node.title)}」</div>
      </div>
      <button type="button" class="subdivide-close" data-tooltip="关闭拆分选择面板" title="关闭" aria-label="关闭">×</button>
    </div>
    <div class="subdivide-body">
      ${splitChoiceHTML()}
    </div>
  `;
  document.body.append(popover);

  // 事件委托:body 内容会被后续步骤重写,统一在 popover 上监听。
  popover.addEventListener("click", (event) => {
    if (event.target === popover || event.target.closest(".subdivide-close")) {
      closeSubdividePopover();
      return;
    }
    if (event.target.closest(".subdivide-firstprinciples")) {
      closeSubdividePopover();
      startFirstPrinciples(node);
      return;
    }
    if (event.target.closest(".subdivide-bytopic")) {
      loadSubdivisionAngles(popover, node, nodeId);
    }
  });

  // 触发动画
  requestAnimationFrame(() => popover.classList.add("visible"));
  document.addEventListener("keydown", onSubdivideEscape);
}

// 第一步:两条岔路(按知识点拆分 / 第一性原理拆到底),都不预先调 AI。
function splitChoiceHTML() {
  return `
    <button type="button" class="subdivide-choice subdivide-bytopic" data-tooltip="让 AI 推荐几个角度,把它拆成一组并列的子知识点">
      <span class="subdivide-choice-title">🧩 按知识点拆分</span>
      <span class="subdivide-choice-desc">AI 挑几个合适的角度,拆成一组并列的子知识点</span>
    </button>
    <button type="button" class="subdivide-choice subdivide-firstprinciples" data-tooltip="用第一性原理一层层往下拆,直到基础学科/公理。随时可停,比较耗时">
      <span class="subdivide-choice-title">⛏ 第一性原理 · 拆到底</span>
      <span class="subdivide-choice-desc">一层层挖出更底层的前置知识,直到触底。随时可停</span>
    </button>
  `;
}

// 第二步:用户选了"按知识点拆分",这才调 AI 拿拆分角度。
async function loadSubdivisionAngles(popover, node, nodeId) {
  popover.classList.add("loading");
  popover.querySelector(".subdivide-body").innerHTML = `
    <div class="subdivide-loading">
      <span class="thinking-dot"></span>
      <span>AI 正在根据对话和你的学习目标，挑几个最合适的拆分角度…</span>
    </div>
  `;
  try {
    const response = await fetch(
      `/api/sessions/${state.sessionId}/nodes/${nodeId}/subdivision-options`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: state.mode }),
      },
    );
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (state.subdividePopoverNodeId !== nodeId) return; // 在等待中用户已经关掉了
    renderSubdivideOptions(popover, node, data);
  } catch (error) {
    if (state.subdividePopoverNodeId !== nodeId) return;
    popover.classList.remove("loading");
    popover.querySelector(".subdivide-body").innerHTML = `
      <div class="subdivide-error">拿建议失败：${escapeHtml(error.message)}<br>你可以关掉重试，或直接在底部输入框里说想怎么拆。</div>
    `;
  }
}

function renderSubdivideOptions(popover, node, data) {
  popover.classList.remove("loading");
  const options = Array.isArray(data?.options) ? data.options : [];
  const caution = data?.caution || null;
  const showMulti = options.length >= 2;
  const body = popover.querySelector(".subdivide-body");
  const angleLabels = options
    .slice(0, 3)
    .map((o) => o.label || o.angle || "")
    .filter(Boolean)
    .join("、");
  body.innerHTML = `
    ${showMulti ? `
      <button type="button" class="subdivide-multi" data-tooltip="一次采用所有推荐角度，让 AI 同时生成多组子节点" aria-label="按 ${escapeHtml(angleLabels)} 一次全部拆开">
        <div class="subdivide-multi-head">
          <span class="subdivide-multi-icon" aria-hidden="true">⚡</span>
          <span class="subdivide-multi-title">按这 ${options.length} 个角度一次全拆</span>
        </div>
        <div class="subdivide-multi-sub">AI 会按 ${escapeHtml(angleLabels)} 各生成一组子节点</div>
      </button>
      <div class="subdivide-divider"><span>或者只挑一个角度</span></div>
    ` : ""}
    <ul class="subdivide-options" role="listbox" aria-label="选择拆分角度"></ul>
    <div class="subdivide-custom">
      <input type="text" maxlength="60" placeholder="或者自己写一个角度，比如「按客群拆」" aria-label="自定义拆分角度" />
      <button type="button" class="subdivide-custom-go" data-tooltip="按你输入的角度拆分当前节点">按这个拆</button>
    </div>
    ${caution ? `
      <div class="subdivide-caution" role="button" tabindex="0" data-tooltip="暂时不拆这个节点，把 AI 的谨慎理由记录到对话里">
        <div class="subdivide-caution-head">
          <span class="subdivide-caution-icon" aria-hidden="true">⚠</span>
          <strong>${escapeHtml(caution.label || "先别拆")}</strong>
        </div>
        <p>${escapeHtml(caution.rationale || "")}</p>
      </div>
    ` : ""}
  `;
  if (showMulti) {
    const multiBtn = body.querySelector(".subdivide-multi");
    multiBtn.addEventListener("click", () => commitMultiAngle(node, options));
  }
  const list = body.querySelector(".subdivide-options");
  for (const option of options) {
    const li = document.createElement("li");
    li.className = "subdivide-option";
    li.setAttribute("role", "option");
    li.tabIndex = 0;
    li.dataset.angle = option.angle || "";
    li.dataset.tooltip = "只按这个角度拆分当前节点，生成一组更细的子节点";
    li.innerHTML = `
      <div class="subdivide-option-label">${escapeHtml(option.label || option.angle || "")}</div>
      <div class="subdivide-option-rationale">${escapeHtml(option.rationale || "")}</div>
    `;
    const trigger = () => commitSubdivide(node, option.label || option.angle, option.angle);
    li.addEventListener("click", trigger);
    li.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        trigger();
      }
    });
    list.append(li);
  }

  const customInput = body.querySelector(".subdivide-custom input");
  const customBtn = body.querySelector(".subdivide-custom-go");
  const submitCustom = () => {
    const raw = customInput.value.trim();
    if (!raw) {
      customInput.focus();
      return;
    }
    commitSubdivide(node, raw, raw);
  };
  customBtn.addEventListener("click", submitCustom);
  customInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      submitCustom();
    }
  });

  if (caution) {
    const cautionEl = body.querySelector(".subdivide-caution");
    const trigger = () => commitCaution(node, caution.rationale || "");
    cautionEl.addEventListener("click", trigger);
    cautionEl.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        trigger();
      }
    });
  }
}

async function commitSubdivide(node, label, angle) {
  closeSubdividePopover();
  // 切到目标节点,这样后续消息和节点状态都对齐
  state.currentNodeId = node.id;
  persistSession();
  updateNodeVisualState();
  const angleText = (angle || label || "").trim();
  const message = angleText
    ? `请围绕「${node.title}」按【${angleText}】这个角度拆开。`
    : `请把「${node.title}」拆开。`;
  await sendMessage(message, {
    intent: "subdivide",
    nodeId: node.id,
    subdivisionAngle: angleText || null,
  });
}

async function commitMultiAngle(node, options) {
  // 浮层先切到 loading,告诉用户后端在生成
  const popover = document.querySelector(".subdivide-popover");
  if (popover) {
    popover.classList.add("loading");
    const body = popover.querySelector(".subdivide-body");
    if (body) {
      body.innerHTML = `
        <div class="subdivide-loading">
          <span class="thinking-dot"></span>
          <span>AI 正在按 ${escapeHtml(String(options.length))} 个角度同时拆开「${escapeHtml(node.title)}」…</span>
        </div>
      `;
    }
  }
  try {
    const response = await fetch(
      `/api/sessions/${state.sessionId}/nodes/${node.id}/multi-angle-subdivide`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          mode: state.mode,
          angles: options.slice(0, 4).map((o) => ({
            angle: o.angle || "",
            label: o.label || o.angle || "",
            rationale: o.rationale || "",
          })),
        }),
      },
    );
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    closeSubdividePopover();
    state.nodes = data.nodes || state.nodes;
    state.messages = data.messages || state.messages;
    const createdIds = data.created_node_ids || [];
    state.newNodeIds = new Set(createdIds);
    if (createdIds.length) {
      state.newNodeEnterDelay = computeEnterDelays(
        state.nodes.filter((n) => createdIds.includes(n.id)),
      );
    }
    state.currentNodeId = data.current_node_id || state.currentNodeId;
    markVisited(state.currentNodeId);
    persistSession();
    render();
    if (createdIds.length) {
      setTimeout(() => focusOnNodes([state.currentNodeId, ...createdIds]), 280);
      setTimeout(() => {
        state.newNodeIds = new Set();
        state.newNodeEnterDelay = new Map();
        renderTree();
      }, 2200);
    }
  } catch (error) {
    if (popover) {
      popover.classList.remove("loading");
      const body = popover.querySelector(".subdivide-body");
      if (body) {
        body.innerHTML = `
          <div class="subdivide-error">一次三角度拆失败：${escapeHtml(error.message)}<br>你可以关掉浮层重试,或挑一个角度单独拆。</div>
        `;
      }
    }
  }
}

async function commitCaution(node, rationale) {
  closeSubdividePopover();
  if (state.sending) return;
  try {
    const response = await fetch(
      `/api/sessions/${state.sessionId}/nodes/${node.id}/caution-note`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rationale, mode: state.mode }),
      },
    );
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (data?.message) {
      state.messages = [...state.messages, data.message];
      state.currentNodeId = node.id;
      persistSession();
      renderMessages();
      scrollMessagesToBottom();
    }
  } catch (error) {
    console.warn("caution note failed", error);
  }
}

function closeSubdividePopover() {
  document.removeEventListener("keydown", onSubdivideEscape);
  state.subdividePopoverNodeId = null;
  const popover = document.querySelector(".subdivide-popover");
  if (!popover) return;
  popover.classList.remove("visible");
  setTimeout(() => popover.remove(), 120);
}

function onSubdivideEscape(event) {
  if (event.key === "Escape") closeSubdividePopover();
}

async function createPeekFollowup(messageId, peekId, question) {
  const { message, peek } = findPeek(messageId, peekId);
  if (!message || !peek) return;
  // status: "thinking" 让 openPeekPopover 渲染 3 点 loading dots,不再写"正在解释…"
  const pending = { id: `local_peekq_${Date.now()}`, question, answer: "", status: "thinking" };
  peek.followups = [...(peek.followups || []), pending];
  renderMessages({ preserveScroll: true });
  openPeekPopover(messageId, peekId, { scrollToBottom: true });
  try {
    const response = await fetch(`/api/messages/${messageId}/peeks/${peekId}/followups`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, mode: state.mode }),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const saved = await response.json();
    state.messages = state.messages.map((m) => (m.id === saved.id ? saved : m));

    // 直接在已渲染的 popover 上找最后一个 followup,逐字 type 进它的 <p>;
    // 不重新 openPeekPopover,避免输入焦点 / scroll 位置抖
    const popover = document.querySelector(
      `.peek-popover[data-peek-id="${cssEscape(peekId)}"]`,
    );
    const savedPeek = (saved.peeks || []).find((p) => p.id === peekId);
    const savedFollowup = savedPeek?.followups?.[savedPeek.followups.length - 1];
    if (popover && savedFollowup) {
      const followupEls = popover.querySelectorAll(".peek-followup");
      const lastEl = followupEls[followupEls.length - 1];
      const p = lastEl?.querySelector("p");
      if (p) {
        lastEl.removeAttribute("data-loading");
        await typeIntoElement(p, savedFollowup.answer || "");
        scrollPeekPopoverToBottom(popover);
      } else {
        // 兜底:popover 结构变了,整个重画
        renderMessages({ preserveScroll: true });
        openPeekPopover(saved.id, peekId, { scrollToBottom: true });
      }
    } else {
      renderMessages({ preserveScroll: true });
      openPeekPopover(saved.id, peekId, { scrollToBottom: true });
    }
  } catch (error) {
    pending.answer = `解释失败：${error.message}`;
    delete pending.status;
    renderMessages({ preserveScroll: true });
    openPeekPopover(messageId, peekId, { scrollToBottom: true });
  }
}

function openChainPanel(nodeId) {
  closeNodeComposer();
  state.chainPanel = { nodeId };
  renderChainPanel();
}

function closeChainPanel() {
  state.chainPanel = null;
  const existing = document.querySelector(".chain-panel");
  if (existing) {
    existing.classList.remove("visible");
    setTimeout(() => existing.remove(), 200);
  }
}

function renderChainPanel() {
  document.querySelector(".chain-panel")?.remove();
  if (!state.chainPanel) return;
  const { nodeId } = state.chainPanel;
  const nodeEl = treeEl.querySelector(`.map-node[data-node-id="${cssEscape(nodeId)}"]`);
  if (!nodeEl) return;
  const node = state.nodes.find((n) => n.id === nodeId);
  if (!node) return;
  const rounds = explainRoundsOf(nodeId);
  if (!rounds.length) {
    closeChainPanel();
    return;
  }

  const panel = document.createElement("div");
  panel.className = "chain-panel";
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-label", `${node.title} 的探索链`);

  const header = document.createElement("div");
  header.className = "chain-panel-head";
  const title = document.createElement("strong");
  title.textContent = node.title;
  const sub = document.createElement("span");
  sub.textContent = `${rounds.length} 轮探索 · 点击节点跳到对应对话`;
  const close = document.createElement("button");
  close.type = "button";
  close.className = "chain-panel-close";
  close.title = "关闭";
  close.dataset.tooltip = "关闭探索链面板";
  close.textContent = "×";
  close.addEventListener("click", closeChainPanel);
  header.append(title, sub, close);
  panel.append(header);

  const chain = document.createElement("ol");
  chain.className = "chain-list";
  rounds.forEach((round, index) => {
    const item = document.createElement("li");
    item.className = "chain-item";
    const dot = document.createElement("span");
    dot.className = "chain-dot";
    dot.textContent = `${index + 1}`;
    const body = document.createElement("button");
    body.type = "button";
    body.className = "chain-body";
    body.dataset.tooltip = "跳回这轮深入对应的对话位置";
    const label = document.createElement("span");
    label.className = "chain-label";
    const userText = (round.user?.content || "").trim();
    label.textContent = userText.length > 28 ? `${userText.slice(0, 28)}…` : userText || "(空)";
    const hint = document.createElement("span");
    hint.className = "chain-hint";
    const summary = (round.assistant?.content || "").replace(/\s+/g, " ").trim();
    hint.textContent = summary.length > 56 ? `${summary.slice(0, 56)}…` : summary;
    body.append(label, hint);
    body.addEventListener("click", () => {
      scrollToMessagePair(round.user?.id, round.assistant?.id);
      panel.querySelectorAll(".chain-body").forEach((b) => b.classList.remove("active"));
      body.classList.add("active");
    });
    item.append(dot, body);
    chain.append(item);
  });
  panel.append(chain);

  document.body.append(panel);
  positionChainPanel(panel, nodeEl);
  // 入场动画
  requestAnimationFrame(() => panel.classList.add("visible"));
}

function positionChainPanel(panel, nodeEl) {
  const rect = nodeEl.getBoundingClientRect();
  const viewport = { width: window.innerWidth, height: window.innerHeight };
  const panelWidth = 320;
  // 默认放节点右侧;放不下就放左侧;再不行放下面
  let left = rect.right + 14;
  let top = rect.top;
  if (left + panelWidth > viewport.width - 20) {
    left = rect.left - panelWidth - 14;
  }
  if (left < 20) {
    left = Math.max(20, rect.left);
    top = rect.bottom + 14;
  }
  // 垂直方向:贴近节点顶部,但别超出底部
  const estimatedHeight = Math.min(viewport.height * 0.6, 64 + 64 * 8);
  if (top + estimatedHeight > viewport.height - 20) {
    top = Math.max(20, viewport.height - estimatedHeight - 20);
  }
  panel.style.left = `${Math.round(left)}px`;
  panel.style.top = `${Math.round(top)}px`;
}

function explainRoundsOf(nodeId) {
  // 一轮 = 这个 node 上的一条 user 消息 + 紧随其后的 assistant 回复
  if (!nodeId) return [];
  const rounds = [];
  let pending = null;
  for (const msg of state.messages) {
    if (msg.node_id !== nodeId) continue;
    if (msg.role === "user") {
      if (pending) rounds.push(pending);
      pending = { user: msg, assistant: null };
    } else if (msg.role === "assistant" && pending) {
      pending.assistant = msg;
    }
  }
  if (pending) rounds.push(pending);
  return rounds;
}

function scrollToMessage(messageId) {
  if (!messageId) return false;
  const target = messagesEl.querySelector(`.message[data-message-id="${cssEscape(messageId)}"]`);
  if (!target) return false;
  target.scrollIntoView({ block: "start", inline: "nearest", behavior: "smooth" });
  target.classList.remove("conversation-focus");
  void target.offsetWidth;
  target.classList.add("conversation-focus");
  window.setTimeout(() => target.classList.remove("conversation-focus"), 1800);
  return true;
}

function scrollToMessagePair(userId, assistantId) {
  const focusEl = (id) => {
    if (!id) return;
    const el = messagesEl.querySelector(`.message[data-message-id="${cssEscape(id)}"]`);
    if (!el) return;
    el.classList.remove("conversation-focus");
    void el.offsetWidth;
    el.classList.add("conversation-focus");
    window.setTimeout(() => el.classList.remove("conversation-focus"), 1800);
  };
  const anchor = messagesEl.querySelector(`.message[data-message-id="${cssEscape(userId || assistantId)}"]`);
  if (anchor) anchor.scrollIntoView({ block: "start", inline: "nearest", behavior: "smooth" });
  focusEl(userId);
  focusEl(assistantId);
}

function focusOnMessageNode(nodeId, sourceEl = null) {
  if (!nodeId) return;
  state.currentNodeId = nodeId;
  persistSession();
  updateNodeVisualState();
  focusOnNodes([nodeId]);
  pulseNode(nodeId);
  // item 3.1: 左→右共振。source = 触发的消息元素,target = 右侧节点
  const targetNodeEl = treeEl.querySelector(`.map-node[data-node-id="${cssEscape(nodeId)}"]`);
  resonate(sourceEl, targetNodeEl);
}

function pulseNode(nodeId) {
  const nodeEl = treeEl.querySelector(`.map-node[data-node-id="${cssEscape(nodeId)}"]`);
  if (!nodeEl) return;
  nodeEl.classList.remove("node-ping");
  void nodeEl.offsetWidth;
  nodeEl.classList.add("node-ping");
  // 节奏放慢:让用户清楚地看见"我跳到的卡片"——之前 1.2s 一闪而过,容易没注意到
  setTimeout(() => nodeEl.classList.remove("node-ping"), 1800);
}

/* item 3.1: 双向共振。源端 220ms 缩放脉冲,目标端 60ms 后 360ms 光晕扩散。
 * map-node 元素的 transform 包含 translate(-50%, -50%),所以 source-pulse 只对
 * 非节点元素(message)生效——节点作为源时由现有的 pulseNode 提供脉冲。*/
function resonate(sourceEl, targetEl) {
  if (sourceEl && !sourceEl.classList.contains("map-node")) {
    sourceEl.classList.remove("resonance-source");
    void sourceEl.offsetWidth;
    sourceEl.classList.add("resonance-source");
    setTimeout(() => sourceEl.classList.remove("resonance-source"), 220);
  }
  if (targetEl) {
    setTimeout(() => {
      targetEl.classList.remove("resonance-target");
      void targetEl.offsetWidth;
      targetEl.classList.add("resonance-target");
      setTimeout(() => targetEl.classList.remove("resonance-target"), 360);
    }, 60);
  }
}

function renderThoughtActions(message, actions = []) {
  const nextActions = normalizeNextActions(actions).filter((action) => !isHiddenThoughtAction(action));
  const searchTools = buildSearchToolDescriptors(message);
  if (!nextActions.length && !searchTools.length) return null;

  // 多个 kind_hint=next_step 是"下一个"的候选;第一条做主按钮,其余进 hover 下拉
  const nextStepCandidates = nextActions.filter((a) => a.kind_hint === "next_step");
  const primaryAction = nextStepCandidates[0]
    || nextActions.find((a) => isPrimaryThoughtAction(a))
    || nextActions[0]
    || null;
  const extraNextSteps = nextStepCandidates.filter((a) => a !== primaryAction);
  const secondaryActions = nextActions.filter((action) =>
    action !== primaryAction && !extraNextSteps.includes(action) && isFeedbackThoughtAction(action),
  );
  const toolActions = nextActions.filter((action) =>
    action !== primaryAction && !extraNextSteps.includes(action) && !secondaryActions.includes(action),
  );
  const wrap = document.createElement("div");
  wrap.className = "thought-action-panel";

  if (primaryAction) {
    if (extraNextSteps.length) {
      wrap.append(createNextStepGroup(primaryAction, extraNextSteps));
    } else {
      wrap.append(createNextActionButton(primaryAction, "primary"));
    }
  }

  if (secondaryActions.length) {
    for (const action of secondaryActions) {
      wrap.append(createNextActionButton(action, "feedback"));
    }
  }

  const allTools = [
    ...toolActions.map((action) => ({ type: "next_action", action })),
    ...searchTools,
  ];
  if (allTools.length) {
    wrap.append(renderThoughtToolbox(message, allTools));
  }
  return wrap;
}

// "下一个"主按钮 + hover 100ms 后弹出的候选下拉
function createNextStepGroup(primaryAction, extraCandidates) {
  const group = document.createElement("div");
  group.className = "next-step-group";

  const primary = createNextActionButton(primaryAction, "primary");
  primary.classList.add("has-candidates");
  // 主按钮右侧加个小▾,暗示还有更多候选
  const hint = document.createElement("span");
  hint.className = "next-step-more-hint";
  hint.setAttribute("aria-hidden", "true");
  hint.textContent = "▾";
  primary.append(hint);
  group.append(primary);

  const menu = document.createElement("div");
  menu.className = "next-step-menu";
  menu.setAttribute("role", "menu");
  menu.hidden = true;

  for (const candidate of extraCandidates) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "next-step-cand";
    item.setAttribute("role", "menuitem");
    item.textContent = (candidate.label || "").replace(/^下一个：/, "") || "继续";
    item.dataset.tooltip = "改去这个节点";
    item.addEventListener("click", async (event) => {
      event.stopPropagation();
      closeNextStepMenu();
      await runNextAction(candidate, item);
    });
    menu.append(item);
  }

  const divider = document.createElement("div");
  divider.className = "next-step-menu-divider";
  divider.setAttribute("aria-hidden", "true");
  menu.append(divider);

  const pickInTree = document.createElement("button");
  pickInTree.type = "button";
  pickInTree.className = "next-step-pick-tree";
  pickInTree.setAttribute("role", "menuitem");
  pickInTree.textContent = "在右侧树里挑";
  pickInTree.dataset.tooltip = "聚焦右下角检索框,自己挑一个节点";
  pickInTree.addEventListener("click", (event) => {
    event.stopPropagation();
    closeNextStepMenu();
    if (typeof nodeSearchInput !== "undefined" && nodeSearchInput) {
      nodeSearchInput.focus();
    }
  });
  menu.append(pickInTree);
  group.append(menu);

  let openTimer = null;
  let closeTimer = null;
  const openMenu = () => {
    if (!menu.hidden) return;
    menu.hidden = false;
    requestAnimationFrame(() => menu.classList.add("visible"));
    group.classList.add("is-open");
  };
  const hideMenu = () => {
    menu.classList.remove("visible");
    group.classList.remove("is-open");
    setTimeout(() => {
      // 防止快速 hover 进出导致提前隐藏
      if (!menu.classList.contains("visible")) menu.hidden = true;
    }, 120);
  };
  const closeNextStepMenu = () => {
    clearTimeout(openTimer);
    clearTimeout(closeTimer);
    hideMenu();
  };
  group.addEventListener("mouseenter", () => {
    clearTimeout(closeTimer);
    openTimer = setTimeout(openMenu, 100);
  });
  group.addEventListener("mouseleave", () => {
    clearTimeout(openTimer);
    closeTimer = setTimeout(hideMenu, 180);
  });
  // 键盘可达:聚焦主按钮也能展开
  primary.addEventListener("focus", () => {
    clearTimeout(closeTimer);
    openTimer = setTimeout(openMenu, 100);
  });
  group.addEventListener("focusout", (event) => {
    if (group.contains(event.relatedTarget)) return;
    clearTimeout(openTimer);
    closeTimer = setTimeout(hideMenu, 180);
  });
  return group;
}

function isHiddenThoughtAction(action) {
  const label = String(action?.label || action?.payload || "").trim();
  return hiddenThoughtActionLabels.has(label);
}

function normalizeNextActions(actions) {
  // 把"下一个知识点"按钮排到最前面,即使后端没排;视觉上也最显眼
  return [...(actions || [])].sort((a, b) => {
    const aNext = a.kind_hint === "next_step" ? 0 : 1;
    const bNext = b.kind_hint === "next_step" ? 0 : 1;
    return aNext - bNext;
  });
}

function isPrimaryThoughtAction(action) {
  const label = String(action?.label || "");
  if (action?.kind_hint === "next_step") return true;
  if (isFeedbackThoughtAction(action)) return false;
  if (action?.kind === "subdivide") return false;
  return /继续|深入|开始|下一个/.test(label);
}

function isFeedbackThoughtAction(action) {
  const label = String(action?.label || "");
  const hint = String(action?.kind_hint || "");
  const kind = String(action?.kind || "");
  return (
    hint === "retry" ||
    kind === "retry" ||
    /我懂了|没听懂|再解释|举个例子|跳过/.test(label)
  );
}

function createNextActionButton(action, variant = "tool") {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "next-action";
  if (variant === "primary") button.classList.add("next-step");
  if (variant === "feedback") button.classList.add("feedback-action");
  if (variant === "tool") button.classList.add("tool-action");
  if (action.kind_hint === "next_step") button.classList.add("next-step");
  if (action.kind_hint === "retry") button.classList.add("retry");
  button.dataset.kind = action.kind || "explain";
  button.dataset.kindHint = action.kind_hint || "";
  button.textContent = action.label || "继续";
  if (!button.dataset.tooltip) {
    if (action.kind_hint === "next_step") {
      button.dataset.tooltip = "跳到知识树里下一个待学节点，保持学习顺序不断线";
    } else if (action.kind_hint === "retry") {
      button.dataset.tooltip = "让 AI 换一种更清楚的说法重新解释当前内容";
    } else if (action.kind === "subdivide") {
      button.dataset.tooltip = "把当前节点拆成更细的学习分支";
    } else {
      button.dataset.tooltip = "围绕当前节点继续追问或深入解释";
    }
  }
  button.addEventListener("click", async (event) => {
    event.stopPropagation();
    await runNextAction(action, button);
  });
  return button;
}

function renderThoughtToolbox(message, tools) {
  const box = document.createElement("div");
  box.className = "thought-toolbox";
  if (state.openThoughtToolsFor === message.id) box.classList.add("open");
  const trigger = document.createElement("button");
  trigger.type = "button";
  trigger.className = "thought-toolbox-trigger";
  trigger.dataset.tooltip = "展开高级学习工具";
  trigger.setAttribute("aria-expanded", state.openThoughtToolsFor === message.id ? "true" : "false");
  trigger.innerHTML = `<span aria-hidden="true">⋯</span><span>思维工具</span>`;
  trigger.addEventListener("click", (event) => {
    event.stopPropagation();
    state.openThoughtToolsFor = state.openThoughtToolsFor === message.id ? null : message.id;
    renderMessages({ preserveScroll: true });
  });
  box.append(trigger);

  const menu = document.createElement("div");
  menu.className = "thought-toolbox-menu";
  menu.setAttribute("role", "menu");
  for (const tool of tools) {
    if (tool.type === "next_action") {
      menu.append(createNextActionButton(tool.action, "tool"));
    } else {
      menu.append(createSearchToolButton(tool));
    }
  }
  box.append(menu);
  return box;
}

function buildSearchToolDescriptors(message) {
  const clean = (message?.search_sources || []).filter((source) =>
    (source?.title || source?.link || source?.content || source?.query || source?.status || "").trim(),
  );
  if (!message?.id || String(message.id).startsWith("stream_") || !clean.length) return [];
  const deepSources = clean.filter((source) =>
    ["deep_result", "deep_empty", "deep_error"].includes(source.status || "")
  );
  const deepResultSources = deepSources.filter((source) => source.status === "deep_result");
  const tools = [{
    type: "search",
    key: "deep-search",
    label: deepSearchLoading.has(message.id) ? "深度搜索中…" : "深度联网搜索",
    tooltip: "搜索 20 篇相关信息，并把结果保存在这条回复下面",
    disabled: deepSearchLoading.has(message.id),
    run: () => runDeepSearch(message.id),
  }];
  if (deepSources.length) {
    tools.push({
      type: "search",
      key: "deep-results",
      label: deepResultSources.length ? `深度搜索结果 · ${deepResultSources.length} 篇` : "深度搜索结果",
      tooltip: "查看这次深度联网搜索返回的全部文章信息",
      run: (anchor) => openSearchSourcesPopover(deepSources, anchor),
    });
  }
  if (deepResultSources.length) {
    tools.push({
      type: "search",
      key: "deep-reanswer",
      label: deepReanswerLoading.has(message.id) ? "正在重答…" : "给 AI 参考并重新回答",
      tooltip: "把 20 篇深度搜索结果交给 AI，生成一条新的参考回答",
      disabled: deepReanswerLoading.has(message.id),
      primary: true,
      run: () => reanswerWithDeepSearch(message.id),
    });
  }
  return tools;
}

function createSearchToolButton(tool) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `next-action tool-action search-tool-action${tool.primary ? " search-tool-primary" : ""}`;
  button.dataset.kind = tool.key || "search";
  button.textContent = tool.label || "联网工具";
  button.disabled = Boolean(tool.disabled);
  button.dataset.tooltip = tool.tooltip || "使用联网搜索工具";
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    tool.run?.(button);
  });
  return button;
}

async function runNextAction(action, button) {
  if (state.sending || button?.disabled) return;
  setActionButtonLoading(button, true);
  try {
    // 所有"拆分"按钮统一走浮层选角度,保持入口一致;
    // 但 next_step 按钮即使 kind=subdivide 也直接走(它来自系统注入的"下一个")
    if (action.kind === "subdivide" && action.kind_hint !== "next_step") {
      const targetId = action.target_node_id || state.currentNodeId;
      if (targetId) openSubdividePopover(targetId);
      return;
    }
    await sendMessage(action.payload || action.label, {
      intent: action.kind || "auto",
      targetNodeId: action.target_node_id || null,
    });
  } finally {
    setActionButtonLoading(button, false);
  }
}

function renderNextActions(actions) {
  const wrap = document.createElement("div");
  wrap.className = "next-actions";
  for (const action of normalizeNextActions(actions).filter((item) => !isHiddenThoughtAction(item))) {
    wrap.append(createNextActionButton(action, action.kind_hint === "next_step" ? "primary" : "feedback"));
  }
  return wrap;
}

// 通用:把按钮切到 loading 态(禁用 + 加 .is-loading 用于 CSS 动画)
function setActionButtonLoading(button, loading) {
  if (!button) return;
  if (loading) {
    button.disabled = true;
    button.classList.add("is-loading");
    button.setAttribute("aria-busy", "true");
  } else {
    button.disabled = false;
    button.classList.remove("is-loading");
    button.removeAttribute("aria-busy");
  }
}

function scrollToNodeConversation(nodeId) {
  if (!nodeId) return false;
  const items = [...messagesEl.querySelectorAll(`.message[data-node-id="${cssEscape(nodeId)}"]`)];
  if (!items.length) return false;
  const target = items[items.length - 1];
  target.scrollIntoView({ block: "start", inline: "nearest", behavior: "smooth" });
  for (const item of items) {
    item.classList.remove("conversation-focus");
    void item.offsetWidth;
    item.classList.add("conversation-focus");
  }
  window.setTimeout(() => {
    for (const item of items) item.classList.remove("conversation-focus");
  }, 1800);
  return true;
}

function scrollMessagesToBottom() {
  const forceBottom = () => {
    const sentinel = messagesEl.querySelector(".message-bottom-sentinel");
    if (sentinel) {
      sentinel.scrollIntoView({ block: "end", inline: "nearest", behavior: "auto" });
    }
    messagesEl.scrollTop = messagesEl.scrollHeight;
  };
  requestAnimationFrame(forceBottom);
  setTimeout(forceBottom, 0);
  setTimeout(forceBottom, 80);
}

function renderTree() {
  if (!state.nodes.length) {
    treeTitleEl.textContent = state.generatingTree ? "正在生长…" : "你好啊";
    renderBreadcrumb(null);
    progressEl.textContent = "0%";
    treeEl.className = `map-canvas tree-empty${state.generatingTree ? " tree-growing" : ""}`;
    treeEl.innerHTML = state.generatingTree
      ? `
        <div class="empty-seed">
          <div class="seed-aura"></div>
          <div class="seed-dot"></div>
          <div class="draft-map" aria-hidden="true">
            <span class="draft-spine"></span>
            <span class="draft-node draft-root"></span>
            <span class="draft-node draft-a"></span>
            <span class="draft-node draft-b"></span>
            <span class="draft-node draft-c"></span>
          </div>
          <p>正在绘制知识树骨架</p>
          <span class="seed-status">AI 正在拆分主干卡片,完成后会逐支长出分支</span>
        </div>
      `
      : `
        <div class="empty-seed">
          <div class="seed-dot"></div>
          <p>生成后，节点会从这里长出来。</p>
        </div>
      `;
    return;
  }

  const root = state.nodes.find((node) => !node.parent_id) || state.nodes[0];
  const current = state.nodes.find((node) => node.id === state.currentNodeId) || root;
  treeTitleEl.textContent = root.title;
  renderBreadcrumb(current);
  progressEl.textContent = `${calculateProgress()}%`;
  // layout-frozen:生长/拆解期间冻结卡片位置过渡(已有卡片瞬间就位,不滑动漂移)。
  // 由 renderTree 统一带上,否则每次重渲染都会把 class 冲掉。
  treeEl.className = state.layoutFrozen ? "map-canvas layout-frozen" : "map-canvas";

  // 方案A:渲染前算一遍"建议学习顺序"(相对关系),写到 node.__prereqTitles / __isStart
  computeSuggestedOrder(state.nodes);

  const layout = buildTreeLayout(root);
  state.lastLayout = layout;
  const stage = ensureStage();
  const svg = ensureBranchLayer(stage);
  renderNodeComposer(stage, layout);

  svg.querySelectorAll("path:not([data-edge-id])").forEach((path) => path.remove());
  const visibleNodeIds = visibleNodeIdSet(root);
  const visibleEdges = layout.edges.filter((edge) => visibleNodeIds.has(edge.fromNodeId) && visibleNodeIds.has(edge.toNodeId));
  const visibleItems = layout.items.filter((item) => visibleNodeIds.has(item.node.id));
  const liveEdgeIds = new Set(visibleEdges.map((edge) => edge.id));
  svg.querySelectorAll("path[data-edge-id]").forEach((path) => {
    if (!liveEdgeIds.has(path.dataset.edgeId)) path.remove();
  });
  for (const edge of visibleEdges) {
    let path = svg.querySelector(`path[data-edge-id="${cssEscape(edge.id)}"]`);
    if (!path) {
      path = createSvg("path");
      path.dataset.edgeId = edge.id;
      svg.append(path);
    }
    path.className.baseVal = `branch-line ${edge.active ? "" : "dim"} ${edge.spine ? "spine" : ""}`.trim();
    path.setAttribute("d", edgePath(edge.from, edge.to));
  }

  const liveNodeIds = new Set(visibleItems.map((item) => item.node.id));
  stage.querySelectorAll(".map-node[data-node-id]").forEach((nodeEl) => {
    if (!liveNodeIds.has(nodeEl.dataset.nodeId)) nodeEl.remove();
  });
  // item 4.2: 给新节点准备"从父节点裂出"动画的 CSS 变量
  // 找出每个 item 的父 item 在 layout 里的坐标,新节点动画起点就是这个偏移。
  // 初次生成整棵树时所有节点都在 newNodeIds 里,这种场景交给 node-rise 入场动画处理,
  // 不走 spawn(否则会出现"所有节点同时从根处飞出"的视觉灾难)
  const itemByNodeId = new Map(visibleItems.map((item) => [item.node.id, item]));
  const isInitialBatch = state.newNodeIds.size > 0 && state.newNodeIds.size === state.nodes.length;
  for (const item of visibleItems) {
    let nodeEl = stage.querySelector(`.map-node[data-node-id="${cssEscape(item.node.id)}"]`);
    const isNew = state.newNodeIds.has(item.node.id);
    if (!nodeEl) {
      nodeEl = renderMapNode(item.node, item.x, item.y, item.depth, item.kind, item.step, item.stepTotal);
      stage.append(nodeEl);
      if (isNew && !isInitialBatch && item.node.parent_id) {
        const parentItem = itemByNodeId.get(item.node.parent_id);
        if (parentItem) {
          // 新节点从父节点位置"裂"出,渐渐回到自己的位置
          const dx = parentItem.x - item.x;
          const dy = parentItem.y - item.y;
          nodeEl.style.setProperty("--spawn-dx", `${dx}px`);
          nodeEl.style.setProperty("--spawn-dy", `${dy}px`);
          nodeEl.classList.add("spawning");
          // 540ms 动画 + 220ms 内层渐显 = 760ms,再多 80ms 余量
          setTimeout(() => nodeEl.classList.remove("spawning"), 840);
        }
      }
    } else {
      updateMapNode(nodeEl, item.node, item.x, item.y, item.depth, item.kind, item.step, item.stepTotal, {
        justCreated: false,
      });
    }
  }
  // item 4.2 续:新出现的连线也走"生长"动画(initial batch 同理跳过)
  if (!isInitialBatch) {
    for (const edge of visibleEdges) {
      const path = svg.querySelector(`path[data-edge-id="${cssEscape(edge.id)}"]`);
      if (!path) continue;
      if (state.newNodeIds.has(edge.toNodeId) && !path.dataset.grew) {
        path.dataset.grew = "1";
        path.classList.add("growing-edge");
        setTimeout(() => path.classList.remove("growing-edge"), 400);
      }
    }
  }

  // 同步写入视口位置,避免刷新/新建 stage 后先按默认 transform 画一帧,
  // 下一帧再跳回当前焦点造成"聚焦点闪走"。
  if (!state.hasViewport) centerViewportOnCurrent(layout);
  applyViewportTransform();
}

function ensureStage() {
  let stage = treeEl.querySelector(".map-stage");
  if (stage && stage.querySelector("path:not([data-edge-id])")) {
    stage.remove();
    stage = null;
  }
  if (!stage) {
    treeEl.innerHTML = "";
    stage = document.createElement("div");
    stage.className = "map-stage";
    treeEl.append(stage);
  }
  return stage;
}

function ensureBranchLayer(stage) {
  let svg = stage.querySelector(".branch-layer");
  if (!svg) {
    svg = createSvg("svg");
    svg.classList.add("branch-layer");
    svg.setAttribute("viewBox", `0 0 ${STAGE_WIDTH} ${STAGE_HEIGHT}`);
    svg.setAttribute("aria-hidden", "true");
    stage.prepend(svg);
  }
  return svg;
}

function buildTreeLayout(root) {
  const byParent = new Map();
  for (const node of state.nodes) {
    const key = node.parent_id || "__root__";
    if (!byParent.has(key)) byParent.set(key, []);
    byParent.get(key).push(node);
  }
  for (const children of byParent.values()) {
    children.sort((a, b) => a.sort_order - b.sort_order || a.created_at.localeCompare(b.created_at));
  }

  const items = [];
  const edges = [];
  const activePath = new Set(pathIds(state.currentNodeId));

  items.push({ node: root, depth: 0, kind: "root", x: CENTER_X, y: ROOT_Y });

  const mainNodes = byParent.get(root.id) || [];
  // 生长期预留 band 高度:trunk 从一开始就在最终位置,加 children 不会 ripple 后面所有 trunk
  // (没生长时回到自然贴紧的布局)
  const reservedFloor = state.reservedTrunkHeight || 170;
  const mainBands = mainNodes.map((node) => ({
    node,
    height: Math.max(reservedFloor, branchHeight(node, byParent)),
  }));
  let cursorY = ROOT_Y - 230;
  let previousMainPoint = { x: CENTER_X, y: ROOT_Y };
  let previousMainNodeId = root.id;

  const mainTotal = mainBands.length;
  mainBands.forEach((band, index) => {
    const y = cursorY - band.height / 2;
    const main = band.node;
    items.push({ node: main, depth: 1, kind: "main", step: index + 1, stepTotal: mainTotal, x: CENTER_X, y });
    edges.push({
      id: `spine-${index}-${main.id}`,
      from: previousMainPoint,
      to: { x: CENTER_X, y },
      fromNodeId: previousMainNodeId || root.id,
      toNodeId: main.id,
      active: activePath.has(main.id),
      spine: true,
    });
    if (!main.collapsed) {
      placeBranchChildren(main, 2, CENTER_X, y, index % 2 === 0 ? 1 : -1, "main");
    }
    previousMainPoint = { x: CENTER_X, y };
    previousMainNodeId = main.id;
    cursorY -= band.height + MAIN_GAP;
  });

  function placeBranchChildren(parent, depth, parentX, parentY, side, parentKind) {
    const children = byParent.get(parent.id) || [];
    if (!children.length) return;
    const childBands = children.map((child) => ({ node: child, height: branchHeight(child, byParent) }));
    const totalHeight =
      childBands.reduce((sum, band) => sum + band.height, 0) + Math.max(0, childBands.length - 1) * ROW_GAP;
    let cursor = parentY - totalHeight / 2;
    children.forEach((child, index) => {
      const band = childBands[index];
      const y = cursor + band.height / 2;
      const x = parentX + side * LEVEL_GAP;
      items.push({ node: child, depth, kind: side > 0 ? "branch-right" : "branch-left", x, y });
      edges.push({
        id: `${parent.id}->${child.id}`,
        from: branchAnchor(parentX, parentY, side, parentKind),
        to: branchTarget(x, y, side),
        fromNodeId: parent.id,
        toNodeId: child.id,
        active: activePath.has(parent.id) && activePath.has(child.id),
      });
      if (!child.collapsed) {
        placeBranchChildren(child, depth + 1, x, y, side, side > 0 ? "branch-right" : "branch-left");
      }
      cursor += band.height + ROW_GAP;
    });
  }

  return { items, edges };
}

function branchHeight(node, byParent) {
  const children = byParent.get(node.id) || [];
  if (!children.length || node.collapsed) return NODE_H;
  return Math.max(
    NODE_H,
    children.reduce((sum, child) => sum + branchHeight(child, byParent), 0) +
      Math.max(0, children.length - 1) * ROW_GAP
  );
}

function descendantCount(nodeId) {
  let count = 0;
  const queue = state.nodes.filter((node) => node.parent_id === nodeId);
  while (queue.length) {
    const next = queue.shift();
    count += 1;
    for (const child of state.nodes) {
      if (child.parent_id === next.id) queue.push(child);
    }
  }
  return count;
}

async function toggleNodeCollapsed(node) {
  const next = !node.collapsed;
  // 乐观更新,先翻转再 PATCH,失败回滚。
  node.collapsed = next;
  closeNodeComposer();
  renderTree();
  // 折叠/展开会重排周边节点位置,把视口锁回这张卡片中心,避免它"被推到屏幕外"
  centerViewportOnNode(node.id);
  try {
    const response = await fetch(`/api/nodes/${node.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ collapsed: next }),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    if (Array.isArray(payload.nodes)) {
      state.nodes = payload.nodes;
      renderTree();
      centerViewportOnNode(node.id, { animate: false });
    }
  } catch (error) {
    node.collapsed = !next;
    renderTree();
    centerViewportOnNode(node.id, { animate: false });
    console.warn("toggle collapsed failed", error);
  }
}

function branchAnchor(x, y, side, kind) {
  const inset = 8;
  if (kind === "main") return { x: x + side * (MAIN_W / 2 - inset), y };
  if (kind === "branch-right") return { x: x + CARD_W / 2 - inset, y };
  if (kind === "branch-left") return { x: x - CARD_W / 2 + inset, y };
  return { x: x + side * 34, y };
}

function branchTarget(x, y, side) {
  const inset = 8;
  return { x: x - side * (CARD_W / 2 - inset), y };
}

// 方案A —— 同组(同父)兄弟之间的"建议学习顺序",用【相对关系】表达,不用全局步数。
// 给每个节点算两样东西(只看【同组兄弟】之间的 prerequisite_ids):
//   __prereqTitles：这张卡依赖的兄弟标题(空 = 没有组内前置)
//   __isStart：被别的兄弟依赖、且自己没有前置的"地基"卡(建议起点)
// 完全并列(整组无依赖边)→ 两者都为空,卡片不打任何标记(诚实)。
function computeSuggestedOrder(nodes) {
  for (const n of nodes) {
    n.__prereqTitles = [];
    n.__isStart = false;
  }
  const groups = new Map();
  for (const n of nodes) {
    const key = n.parent_id || "__root__";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(n);
  }
  for (const siblings of groups.values()) {
    const byId = new Map(siblings.map((s) => [s.id, s]));
    const dependedOn = new Set();
    let hasAnyEdge = false;
    for (const s of siblings) {
      const raw = Array.isArray(s.prerequisite_ids) ? s.prerequisite_ids : [];
      const deps = raw.filter((id) => byId.has(id) && id !== s.id);
      s.__prereqTitles = deps.map((id) => byId.get(id).title);
      for (const id of deps) dependedOn.add(id);
      if (deps.length) hasAnyEdge = true;
    }
    if (!hasAnyEdge) {
      for (const s of siblings) s.__prereqTitles = []; // 整组并列 → 清空,不打标记
      continue;
    }
    // 起点 = 没有组内前置、且被别人依赖的"地基"卡
    for (const s of siblings) {
      if (!s.__prereqTitles.length && dependedOn.has(s.id)) s.__isStart = true;
    }
  }
}

// 建议学习顺序徽标(DOM)。返回 null 表示这张卡不打标记(并列卡片)。
function orderBadgeEl(node) {
  if (node.__isStart) {
    const el = document.createElement("div");
    el.className = "map-node-order is-start";
    el.textContent = "建议从这里开始";
    el.dataset.tooltip = "建议的学习起点;同组其它卡片建立在它之上。顺序只是建议,可无视";
    return el;
  }
  const prereqs = node.__prereqTitles || [];
  if (prereqs.length) {
    const el = document.createElement("div");
    el.className = "map-node-order";
    const shown = prereqs.slice(0, 2).join("、");
    const more = prereqs.length > 2 ? ` 等${prereqs.length}项` : "";
    el.textContent = `需先学：${shown}${more}`;
    el.dataset.tooltip = "建议先学完这些同组卡片,再学这一张。只是建议,可无视";
    return el;
  }
  return null;
}

function renderMapNode(node, x, y, depth, kind, step, stepTotal) {
  const button = document.createElement("button");
  button.type = "button";
  button.dataset.nodeId = node.id;
  button.addEventListener("click", async (event) => {
    // chip 自己抓走点击,打开探索链;别再开 composer
    if (event.target.closest("[data-depth-chip]")) {
      event.stopPropagation();
      state.currentNodeId = node.id;
      persistSession();
      updateNodeVisualState();
      openChainPanel(node.id);
      return;
    }
    // 右侧树现在是导航：点节点就切到那个节点。
    //   - 该节点已有对话 → 仅滚回对话区,不再发新消息
    //   - 没有对话 → 自动发起一条 explain,让 AI 直接围绕这张卡讲
    state.currentNodeId = node.id;
    persistSession();
    updateNodeVisualState();
    focusOnNodes([node.id]);
    pulseNode(node.id);
    // item 3.1: 右→左共振。target = 该节点关联的最后一条消息
    const messageEl = messagesEl.querySelector(
      `.message[data-node-id="${cssEscape(node.id)}"]:last-of-type`
    );
    if (messageEl) resonate(null, messageEl);
    if (depth === 0) return; // 根节点只做"回到地图原点"的视觉效果
    if (state.sending) return;
    if (scrollToNodeConversation(node.id)) return;
    await sendMessage(`请围绕「${node.title}」开始讲解。`, {
      intent: "explain",
      targetNodeId: node.id,
    });
  });
  updateMapNode(button, node, x, y, depth, kind, step, stepTotal, {
    justCreated: state.newNodeIds.has(node.id),
  });
  const stagger = state.newNodeEnterDelay.get(node.id);
  if (typeof stagger === "number") {
    button.style.setProperty("--enter-delay", `${stagger}ms`);
  } else {
    button.style.removeProperty("--enter-delay");
  }
  return button;
}

function updateMapNode(button, node, x, y, depth, kind, step, stepTotal, options = {}) {
  const justCreated = Boolean(options.justCreated);
  const hiddenCount = node.collapsed ? descendantCount(node.id) : 0;
  const hasChildren = state.nodes.some((item) => item.parent_id === node.id);
  const explainCount = explainRoundsOf(node.id).length;
  const signature = JSON.stringify({
    title: node.title,
    summary: node.summary || "",
    status: node.status,
    relevance: Number(node.relevance || 0),
    importance: Number(node.importance || 2),
    relevance_score: Number(node.relevance_score || (node.relevance ? 3 : 2)),
    difficulty: Number(node.difficulty || 2),
    depth,
    kind,
    step: step || 0,
    stepTotal: stepTotal || 0,
    collapsed: Boolean(node.collapsed),
    hiddenCount,
    hasChildren,
    explainCount,
    orderStart: Boolean(node.__isStart),
    orderPrereqs: (node.__prereqTitles || []).join("|"),
    isFundamental: Boolean(node.is_fundamental),
    fpRelation: node.fp_relation || "",
    fpReason: node.fp_reason || "",
  });
  // item 4.1: 检测 status 从非 completed → completed 的瞬间,触发一次环扩散动画
  const previousStatus = button.dataset.lastStatus;
  const justCompleted =
    node.status === "completed" && previousStatus && previousStatus !== "completed";
  button.dataset.lastStatus = node.status || "";
  button.className = `map-node ${kind || ""} ${depth === 0 ? "root" : ""} ${
    node.id === state.currentNodeId ? "active" : ""
  } ${state.visited.has(node.id) ? "visited" : ""} ${node.id === state.generatingNodeId ? "generating" : ""} ${
    justCreated ? "just-created" : ""
  } ${node.collapsed && hasChildren ? "collapsed" : ""} ${depth > 0 ? "has-toolbar" : ""} ${node.is_fundamental ? "is-fundamental" : ""}`;
  if (justCompleted) {
    // 通过移除再加来重启动画(class 已经在上面 className 重写后丢了,所以直接加)
    button.classList.add("just-completed");
    setTimeout(() => button.classList.remove("just-completed"), 580);
  }
  button.style.left = `${x}px`;
  button.style.top = `${y}px`;
  button.title = node.summary || node.title;
  button.dataset.tooltip = depth === 0
    ? "回到知识树根节点"
    : "切换到这个节点；如果还没讲过，会让 AI 围绕它开始讲解";
  if (hiddenCount) {
    button.dataset.collapsedCount = `+${hiddenCount}`;
  } else {
    delete button.dataset.collapsedCount;
  }
  if (button.dataset.signature === signature) return;
  button.dataset.signature = signature;
  button.innerHTML = "";

  const title = document.createElement("div");
  title.className = "map-node-title";
  title.textContent = depth === 0 ? "根" : node.title;
  if (kind === "main" && step) {
    const chip = document.createElement("div");
    chip.className = "step-chip";
    // item 1.2: 显示 "N / Total 步",让用户清楚"还剩几步要走"
    if (stepTotal && stepTotal > 1) {
      chip.innerHTML = `第 ${step} <span class="step-total">/ ${stepTotal}</span> 步`;
    } else {
      chip.textContent = `第 ${step} 步`;
    }
    button.append(chip);
  }
  button.append(title);

  if (depth > 0) {
    // 方案A:建议学习顺序(相对关系)——起点卡/有前置的卡才显示;并列卡不显示
    const order = orderBadgeEl(node);
    if (order) button.append(order);

    // 第一性原理触底:这是基础公理/最小单位,不再往下拆
    if (node.is_fundamental) {
      const fp = document.createElement("div");
      fp.className = "map-node-fundamental";
      fp.textContent = "⊥ 基础";
      fp.dataset.tooltip = "第一性原理拆解到底:这是基础学科/公理,不可再拆";
      button.append(fp);
    }

    if (node.fp_relation || node.fp_reason) {
      const why = document.createElement("div");
      why.className = "map-node-fp-why";
      why.textContent = `为什么：${node.fp_relation || "底层依赖"}`;
      why.dataset.tooltip = node.fp_reason || node.fp_relation || "第一性原理拆解到这里的原因";
      button.append(why);
    }

    const summary = document.createElement("div");
    summary.className = "map-node-summary";
    summary.textContent = node.summary || "点击学习这个节点";
    const meta = document.createElement("div");
    meta.className = "map-node-meta";
    meta.append(
      chip(statusLabel(node.status), `status-chip status-${node.status}`),
      recommendDots(node),
    );
    const code = document.createElement("div");
    code.className = "node-code-rain";
    code.setAttribute("aria-hidden", "true");
    code.textContent = randomDigits(54);
    button.append(summary, meta, code);

    // === 底部统一工具栏(方案 A):左 = 已深入 N 轮,右 = 拆分。永远显示,视觉对等 ===
    const toolbar = document.createElement("div");
    toolbar.className = "node-toolbar";

    const exploreSeg = document.createElement("div");
    exploreSeg.className = `node-toolbar-seg node-toolbar-explore${explainCount > 0 ? " is-active" : " is-empty"}`;
    exploreSeg.dataset.depthChip = "true"; // 触发卡片 click handler 的 chip 分支,打开探索链面板
    if (explainCount > 0) {
      exploreSeg.setAttribute("role", "button");
      exploreSeg.setAttribute("tabindex", "0");
      exploreSeg.setAttribute("aria-label", `查看探索链:已深入 ${explainCount} 轮`);
      exploreSeg.dataset.tooltip = `已深入 ${explainCount} 轮 · 点击查看每一轮并跳回对应对话`;
      // 左侧 1/3 的宽度装不下 "已深入 N 轮 + 锁链 SVG",简成 "深入 ×N",
      // 既不丢失语义,又确保不会触发换行成竖条
      exploreSeg.innerHTML = `
        <svg width="11" height="11" viewBox="0 0 10 18" aria-hidden="true">
          <circle cx="5" cy="3" r="1.7" fill="currentColor"/>
          <line x1="5" y1="4.7" x2="5" y2="7.3" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>
          <circle cx="5" cy="9" r="1.7" fill="currentColor"/>
          <line x1="5" y1="10.7" x2="5" y2="13.3" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>
          <circle cx="5" cy="15" r="1.7" fill="currentColor"/>
        </svg>
        <span>深入 ×${explainCount}</span>
      `;
    } else {
      exploreSeg.setAttribute("aria-label", "还没深入过");
      exploreSeg.dataset.tooltip = "这个节点还没有深入讲解记录";
      exploreSeg.innerHTML = `<span class="node-toolbar-hint">未深入</span>`;
    }

    const divider = document.createElement("div");
    divider.className = "node-toolbar-divider";
    divider.setAttribute("aria-hidden", "true");

    const subdivideSeg = document.createElement("div");
    subdivideSeg.className = "node-toolbar-seg node-toolbar-subdivide";
    subdivideSeg.setAttribute("role", "button");
    subdivideSeg.setAttribute("tabindex", "0");
    subdivideSeg.setAttribute("aria-label", `给「${node.title}」选一个拆分角度`);
    subdivideSeg.title = "选一个角度,让 AI 把这个节点继续拆开";
    subdivideSeg.dataset.tooltip = "选择一个拆分角度，把这个节点扩展成更细的子节点";
    subdivideSeg.innerHTML = `
      <svg width="11" height="11" viewBox="0 0 12 12" aria-hidden="true">
        <path d="M6 1.5v9M1.5 6h9" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>
      </svg>
      <span>拆分</span>
    `;
    const triggerSubdivide = (event) => {
      event.stopPropagation();
      event.preventDefault();
      if (subdivideSeg.classList.contains("is-loading")) return;
      subdivideSeg.classList.add("is-loading");
      subdivideSeg.setAttribute("aria-busy", "true");
      openSubdividePopover(node.id);
    };
    subdivideSeg.addEventListener("click", triggerSubdivide);
    subdivideSeg.addEventListener("pointerdown", (event) => event.stopPropagation());
    subdivideSeg.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") triggerSubdivide(event);
    });

    toolbar.append(exploreSeg, divider, subdivideSeg);
    button.append(toolbar);
  } else {
    const dot = document.createElement("span");
    dot.className = "map-node-dot";
    button.append(dot);
  }

  if (depth > 0 && hasChildren) {
    const toggle = document.createElement("span");
    toggle.className = "collapse-toggle";
    toggle.setAttribute("role", "button");
    toggle.setAttribute("tabindex", "0");
    toggle.setAttribute(
      "aria-label",
      node.collapsed ? `展开 ${node.title} 的 ${hiddenCount} 个子节点` : `折叠 ${node.title} 的子树`
    );
    toggle.dataset.tooltip = node.collapsed
      ? `展开这个节点隐藏的 ${hiddenCount} 个子节点`
      : "折叠这个节点下面的子树，减少画布干扰";
    toggle.textContent = node.collapsed ? "+" : "−";
    const trigger = (event) => {
      event.stopPropagation();
      event.preventDefault();
      toggleNodeCollapsed(node);
    };
    toggle.addEventListener("click", trigger);
    toggle.addEventListener("pointerdown", (event) => event.stopPropagation());
    toggle.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        trigger(event);
      }
    });
    button.append(toggle);
  }

  // 注:"拆分"按钮已经合并到上面的 .node-toolbar 里(方案 A 的右半段)
}

function updateNodeVisualState() {
  document.querySelectorAll(".map-node[data-node-id]").forEach((nodeEl) => {
    const nodeId = nodeEl.dataset.nodeId;
    nodeEl.classList.toggle("active", nodeId === state.currentNodeId);
    nodeEl.classList.toggle("visited", state.visited.has(nodeId));
    nodeEl.classList.toggle("generating", nodeId === state.generatingNodeId);
  });
}

function openNodeComposer(nodeId, x, y, depth) {
  if (state.sending) return;
  const node = state.nodes.find((item) => item.id === nodeId);
  state.nodeComposer = { nodeId, x, y, depth, title: node?.title || "这个节点" };
  renderTree();
}

function closeNodeComposer() {
  if (!state.nodeComposer) return;
  state.nodeComposer = null;
  const existing = treeEl.querySelector(".node-query");
  if (existing) existing.remove();
}

function renderNodeComposer(stage, layout) {
  const existing = stage.querySelector(".node-query");
  if (!state.nodeComposer) {
    if (existing) existing.remove();
    return;
  }
  const item = layout.items.find((entry) => entry.node.id === state.nodeComposer.nodeId);
  if (!item) {
    closeNodeComposer();
    return;
  }

  // 模式切换时整块重建,避免按钮残留
  if (existing && existing.dataset.coachMode !== String(state.coachMode)) {
    existing.remove();
    existing = null;
  }

  let form = existing;
  if (!form) {
    form = document.createElement("form");
    form.className = "node-query";
    form.dataset.coachMode = String(state.coachMode);
    form.classList.toggle("manual", !state.coachMode);
    form.addEventListener("pointerdown", (event) => event.stopPropagation());

    const sendCurrent = async (intent) => {
      const input = form.querySelector("textarea");
      const typed = input.value.trim();
      const fallback = intent === "subdivide"
        ? `请把「${state.nodeComposer.title}」拆开成几个具体子方向。`
        : `请围绕「${state.nodeComposer.title}」开始讲解。`;
      await sendMessage(typed || fallback, {
        nodeId: state.nodeComposer.nodeId,
        intent,
      });
    };

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const input = form.querySelector("textarea");
      const value = input.value.trim();
      if (!value || state.sending) return;
      await sendMessage(value, { nodeId: state.nodeComposer.nodeId, intent: "auto" });
    });

    const label = document.createElement("div");
    label.className = "node-query-title";
    const input = document.createElement("textarea");
    input.rows = 2;
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        form.requestSubmit();
      }
    });
    input.placeholder = state.coachMode
      ? "想问什么，AI 会决定深入还是拆开"
      : "想问什么，或用下方按钮选动作";
    const actions = document.createElement("div");
    actions.className = "node-query-actions";

    if (state.coachMode) {
      const submit = document.createElement("button");
      submit.type = "submit";
      submit.textContent = "发送";
      submit.dataset.primary = "true";
      submit.dataset.tooltip = "把输入的问题发给 AI，AI 会判断是深入还是拆分";
      actions.append(submit);
    } else {
      const explainBtn = document.createElement("button");
      explainBtn.type = "button";
      explainBtn.textContent = "深入";
      explainBtn.dataset.tooltip = "围绕这个节点继续解释，不新增同级分支";
      explainBtn.addEventListener("click", () => {
        if (state.sending) return;
        sendCurrent("explain");
      });
      const subdivideBtn = document.createElement("button");
      subdivideBtn.type = "button";
      subdivideBtn.textContent = "细分";
      subdivideBtn.dataset.tooltip = "把这个节点拆成几个更细的子节点";
      subdivideBtn.addEventListener("click", () => {
        if (state.sending) return;
        sendCurrent("subdivide");
      });
      actions.append(explainBtn, subdivideBtn);
    }
    form.append(label, input, actions);
    stage.append(form);
    requestAnimationFrame(() => input.focus());
  }

  form.querySelector(".node-query-title").textContent = `问：${state.nodeComposer.title}`;
  const side = item.kind === "branch-left" ? -1 : 1;
  const cardWidth = item.kind === "main" ? MAIN_W : item.depth === 0 ? 64 : CARD_W;
  form.style.left = `${item.x + side * (cardWidth / 2 + 22)}px`;
  form.style.top = `${item.y}px`;
  form.classList.toggle("left", side < 0);
}

function visibleNodeIdSet(root) {
  if (!state.hideUnvisited) {
    return new Set(state.nodes.map((node) => node.id));
  }
  const visible = new Set([root.id, ...state.visited]);
  if (state.currentNodeId) visible.add(state.currentNodeId);
  return visible;
}

function chip(text, className = "status-chip") {
  const element = document.createElement("span");
  element.className = className;
  element.textContent = text;
  return element;
}

function metricChip(kind, label, value) {
  // kind ∈ {importance, relevance, difficulty} → CSS 给三种几何前缀(● ■ ▲)
  const score = clampMetric(value);
  const element = chip(`${label}${metricLabel(score)}`, `status-chip metric-chip metric-${score}`);
  element.dataset.metricKind = kind;
  element.title = `${label}程度：${metricLabel(score)}`;
  return element;
}

function recommendDots(node) {
  // 把"重要 + 相关"折叠成 1-3 颗推荐点;难度不进入分数(独立维度),
  // 但悬浮时把 3 个原始指标都通过 tooltip 暴露出来,信息不丢。
  const importance = clampMetric(node.importance);
  const relevance = clampMetric(node.relevance_score || (node.relevance ? 3 : 2));
  const difficulty = clampMetric(node.difficulty);
  const score = Math.max(1, Math.min(3, Math.round((importance + relevance) / 2)));
  const advice = score === 3 ? "强烈建议看" : score === 2 ? "可以看看" : "可以跳过";

  const wrap = document.createElement("span");
  wrap.className = `recommend-dots recommend-${score}`;
  wrap.dataset.recommendScore = String(score);
  wrap.dataset.tooltip = `${advice} · 重要 ${metricLabel(importance)} · 相关 ${metricLabel(relevance)} · 难度 ${metricLabel(difficulty)}`;
  wrap.setAttribute("aria-label", wrap.dataset.tooltip);
  for (let i = 1; i <= 3; i += 1) {
    const dot = document.createElement("span");
    dot.className = `recommend-dot${i <= score ? " filled" : ""}`;
    wrap.append(dot);
  }
  return wrap;
}

function metricLabel(value) {
  return ["低", "中", "高"][clampMetric(value) - 1];
}

function clampMetric(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return 2;
  return Math.max(1, Math.min(3, Math.round(number)));
}

function randomDigits(length) {
  const alphabet = "0123456789";
  let output = "";
  for (let index = 0; index < length; index += 1) {
    output += alphabet[Math.floor(Math.random() * alphabet.length)];
    if (index % 6 === 5) output += " ";
  }
  return output;
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function edgePath(from, to) {
  const dx = to.x - from.x;
  if (Math.abs(dx) < 16) {
    return `M ${from.x} ${from.y} L ${to.x} ${to.y}`;
  }
  const midX = from.x + dx * 0.46;
  return `M ${from.x} ${from.y} C ${midX} ${from.y}, ${midX} ${to.y}, ${to.x} ${to.y}`;
}

function createSvg(tag) {
  return document.createElementNS("http://www.w3.org/2000/svg", tag);
}

function cssEscape(value) {
  if (window.CSS?.escape) return CSS.escape(value);
  return String(value).replace(/"/g, '\\"');
}

function statusLabel(status) {
  return {
    pending: "待学",
    active: "当前",
    completed: "完成",
    skipped: "跳过",
    deepening: "深入",
    paused: "暂缓",
  }[status] || status;
}

function pathFor(node) {
  const byId = new Map(state.nodes.map((item) => [item.id, item]));
  const path = [];
  let cursor = node;
  let guard = 0;
  while (cursor && guard < 20) {
    path.unshift(cursor.title);
    cursor = cursor.parent_id ? byId.get(cursor.parent_id) : null;
    guard += 1;
  }
  return path;
}

/* item 1.3: 把"当前位置"从左下角孤立面包屑挪进标题栏,中段可点跳上级 */
function renderBreadcrumb(currentNode) {
  if (!breadcrumbEl) return;
  breadcrumbEl.innerHTML = "";
  if (!currentNode) {
    breadcrumbEl.textContent = state.generatingTree ? "知识地图正在生成" : "未开始";
    return;
  }
  const byId = new Map(state.nodes.map((item) => [item.id, item]));
  const chain = [];
  let cursor = currentNode;
  let guard = 0;
  while (cursor && guard < 20) {
    chain.unshift(cursor);
    cursor = cursor.parent_id ? byId.get(cursor.parent_id) : null;
    guard += 1;
  }
  // 根节点的"根"标题没有意义,直接用 tree-title 标识;面包屑跳过根
  const visible = chain.filter((node) => node.parent_id);
  visible.forEach((node, index) => {
    if (index > 0) {
      const sep = document.createElement("span");
      sep.className = "map-breadcrumb-separator";
      sep.textContent = "/";
      sep.setAttribute("aria-hidden", "true");
      breadcrumbEl.append(sep);
    }
    const seg = document.createElement("button");
    seg.type = "button";
    seg.className = "map-breadcrumb-segment";
    seg.textContent = node.title;
    seg.title = node.title;
    if (index === visible.length - 1) {
      seg.classList.add("current");
      seg.disabled = true;
      seg.setAttribute("aria-current", "true");
    } else {
      seg.addEventListener("click", () => focusOnMessageNode(node.id, seg));
    }
    breadcrumbEl.append(seg);
  });
}

function pathIds(nodeId) {
  const byId = new Map(state.nodes.map((item) => [item.id, item]));
  const ids = [];
  let cursor = nodeId ? byId.get(nodeId) : null;
  let guard = 0;
  while (cursor && guard < 20) {
    ids.unshift(cursor.id);
    cursor = cursor.parent_id ? byId.get(cursor.parent_id) : null;
    guard += 1;
  }
  return ids;
}

function calculateProgress() {
  const learnable = state.nodes.filter((node) => node.depth > 0);
  if (!learnable.length) return 0;
  const done = learnable.filter((node) => ["completed", "skipped"].includes(node.status)).length;
  return Math.round((done / learnable.length) * 100);
}

// ====================================================================
// Phase 2/3:认证 + 设置 drawer
//
// 设计:
//   - localStorage 存 JWT,wrap window.fetch 自动挂 Authorization 头
//   - 任何 /api/* 收到 401 → 清 token,弹登录覆盖层
//   - 登录成功 → 隐藏覆盖层 → 跑 restore()
//   - 设置 drawer 通过 /api/settings 读写 LLM 配置,敏感字段后端会 mask
// ====================================================================

const AUTH_TOKEN_KEY = "km.token";
const getAuthToken = () => localStorage.getItem(AUTH_TOKEN_KEY) || "";
const setAuthToken = (t) => localStorage.setItem(AUTH_TOKEN_KEY, t);
const clearAuthToken = () => localStorage.removeItem(AUTH_TOKEN_KEY);

state.currentUser = null;

// 原生 fetch 备份,login / refresh-me 等"401-不该触发跳转"的场景用它绕过 wrap
const _origFetch = window.fetch.bind(window);

window.fetch = async (input, init = {}) => {
  const url = typeof input === "string" ? input : input.url;
  const isApi = typeof url === "string" && url.startsWith("/api/");
  if (isApi) {
    const headers = new Headers(init.headers || {});
    const token = getAuthToken();
    if (token && !headers.has("Authorization")) {
      headers.set("Authorization", `Bearer ${token}`);
    }
    init = { ...init, headers };
  }
  const response = await _origFetch(input, init);
  if (isApi && response.status === 401) {
    clearAuthToken();
    state.currentUser = null;
    showAuthOverlay();
  }
  return response;
};

const authOverlay = document.getElementById("auth-overlay");
const authForm = document.getElementById("auth-form");
const authError = document.getElementById("auth-error");
const authSubmit = document.getElementById("auth-submit");
const settingsDrawer = document.getElementById("settings-page");
const settingsBody = document.getElementById("settings-body");
const promptsBody = document.getElementById("prompts-body");
const passwordDrawer = document.getElementById("password-drawer");
const passwordForm = document.getElementById("password-form");
const passwordError = document.getElementById("password-error");

function showAuthOverlay() {
  if (!authOverlay) return;
  authOverlay.classList.remove("hidden");
  document.body.classList.add("auth-locked");
  setTimeout(() => document.getElementById("auth-password")?.focus(), 50);
}

function hideAuthOverlay() {
  authOverlay?.classList.add("hidden");
  document.body.classList.remove("auth-locked");
}

function updateUserChip(user) {
  if (!user || !avatarButton) return;
  const dot = avatarButton.querySelector(".avatar-dot");
  const text = avatarButton.querySelector(".avatar-text");
  if (dot) dot.textContent = (user.username || "?").slice(0, 1).toUpperCase();
  if (text) text.textContent = user.username || "User";
}

async function fetchMe() {
  const token = getAuthToken();
  if (!token) return null;
  try {
    const response = await _origFetch("/api/auth/me", {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!response.ok) return null;
    return await response.json();
  } catch {
    return null;
  }
}

async function bootstrapAuth() {
  const me = await fetchMe();
  if (me) {
    state.currentUser = me;
    updateUserChip(me);
    hideAuthOverlay();
    if (me.must_change_password) {
      openPasswordDrawer("⚠️ 当前还是默认密码,建议立刻修改");
    }
    return true;
  }
  showAuthOverlay();
  return false;
}

authForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  authError.textContent = "";
  authSubmit.disabled = true;
  const originalLabel = authSubmit.textContent;
  authSubmit.textContent = "登录中…";
  try {
    const username = document.getElementById("auth-username").value.trim();
    const password = document.getElementById("auth-password").value;
    const response = await _origFetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${response.status}`);
    }
    const data = await response.json();
    setAuthToken(data.access_token);
    state.currentUser = data.user;
    updateUserChip(data.user);
    hideAuthOverlay();
    document.getElementById("auth-password").value = "";
    if (data.user?.must_change_password) {
      openPasswordDrawer("⚠️ 当前还是默认密码,建议立刻修改");
    }
    await restore();
  } catch (error) {
    authError.textContent = error.message;
  } finally {
    authSubmit.disabled = false;
    authSubmit.textContent = originalLabel;
  }
});

function logout() {
  clearAuthToken();
  state.currentUser = null;
  state.sessionId = null;
  state.currentNodeId = null;
  state.nodes = [];
  state.messages = [];
  state.visited = new Set();
  localStorage.removeItem("km.sessionId");
  localStorage.removeItem("km.currentNodeId");
  persistVisited();
  render();
  showAuthOverlay();
}

// ===== 修改密码 drawer =====
function openPasswordDrawer(hint = "") {
  passwordDrawer?.classList.remove("hidden");
  if (passwordError) passwordError.textContent = hint;
  setTimeout(() => document.getElementById("password-old")?.focus(), 50);
}

function closePasswordDrawer() {
  passwordDrawer?.classList.add("hidden");
  passwordForm?.reset();
  if (passwordError) passwordError.textContent = "";
}

passwordDrawer?.querySelectorAll("[data-password-action='close']").forEach((el) => {
  el.addEventListener("click", closePasswordDrawer);
});

passwordForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  passwordError.textContent = "";
  const oldP = document.getElementById("password-old").value;
  const newP = document.getElementById("password-new").value;
  const confirmP = document.getElementById("password-confirm").value;
  if (newP !== confirmP) {
    passwordError.textContent = "两次输入的新密码不一致";
    return;
  }
  if (newP.length < 6) {
    passwordError.textContent = "新密码至少 6 位";
    return;
  }
  try {
    const response = await fetch("/api/auth/change-password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ old_password: oldP, new_password: newP }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${response.status}`);
    }
    const data = await response.json();
    state.currentUser = data.user;
    closePasswordDrawer();
  } catch (error) {
    passwordError.textContent = error.message;
  }
});

// ===== 设置 drawer =====
// help-icon tooltip:挂在 body 下的全局浮层(position: fixed),
// 这样 .settings-body 这种 overflow:auto 的祖先就切不到它了。
// 用事件委托,任何带 data-tooltip 属性的元素 hover/focus 都触发。
(function setupHelpTooltip() {
  let tooltipEl = null;
  const ensureTooltipEl = () => {
    if (tooltipEl) return tooltipEl;
    tooltipEl = document.createElement("div");
    tooltipEl.id = "help-tooltip";
    tooltipEl.setAttribute("role", "tooltip");
    document.body.appendChild(tooltipEl);
    return tooltipEl;
  };
  const positionFor = (target) => {
    const el = ensureTooltipEl();
    const text = target.getAttribute("data-tooltip") || "";
    if (!text) return;
    el.textContent = text;
    // 重置占位算尺寸
    el.style.left = "0px";
    el.style.top = "0px";
    el.removeAttribute("data-placement");
    el.classList.add("is-visible");
    const tipRect = el.getBoundingClientRect();
    const iconRect = target.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const margin = 8;
    // 默认气泡浮在图标上方;空间不够再翻到下方
    let top = iconRect.top - tipRect.height - 10;
    let placement = "above";
    if (top < margin) {
      top = iconRect.bottom + 10;
      placement = "below";
    }
    // 水平居中对齐图标中心,撞到屏幕边再夹回来
    const iconCenter = iconRect.left + iconRect.width / 2;
    let left = iconCenter - tipRect.width / 2;
    left = Math.max(margin, Math.min(left, vw - tipRect.width - margin));
    // 算出小三角应该落在 tooltip 内部的哪个 x,让箭头始终指着图标中心
    const arrowLeft = Math.max(12, Math.min(tipRect.width - 12, iconCenter - left));
    el.style.left = `${Math.round(left)}px`;
    el.style.top = `${Math.round(Math.min(top, vh - tipRect.height - margin))}px`;
    el.style.setProperty("--tooltip-arrow-left", `${Math.round(arrowLeft)}px`);
    if (placement === "below") el.setAttribute("data-placement", "below");
  };
  const hide = () => {
    if (tooltipEl) tooltipEl.classList.remove("is-visible");
  };
  // 用 mouseover/mouseout 而不是 mouseenter/mouseleave,
  // 这样事件委托一个监听就能覆盖所有 .help-icon
  document.addEventListener("mouseover", (event) => {
    const target = event.target.closest("[data-tooltip]");
    if (!target) return;
    positionFor(target);
  });
  document.addEventListener("mouseout", (event) => {
    const target = event.target.closest("[data-tooltip]");
    if (!target) return;
    // 移出图标本体才隐藏
    const related = event.relatedTarget;
    if (related && target.contains(related)) return;
    hide();
  });
  document.addEventListener("focusin", (event) => {
    const target = event.target.closest("[data-tooltip]");
    if (target) positionFor(target);
  });
  document.addEventListener("focusout", (event) => {
    const target = event.target.closest("[data-tooltip]");
    if (target) hide();
  });
  // 滚 / 缩放时直接收起,避免位置漂移
  window.addEventListener("scroll", hide, true);
  window.addEventListener("resize", hide);
})();

// 静态建议列表:用户能在输入框里看到下拉提示,但也可以自己输任意字符串
// (HTML5 <datalist> combobox 模式)。
// 这里手动维护,上游出新模型时回来加一行即可。
const FIELD_SUGGESTIONS = {
  LLM_MODEL: [
    "deepseek-chat",     // V3 通用对话,默认
    "deepseek-reasoner", // R1 推理模型
    "moonshot-v1-8k",
    "moonshot-v1-32k",
  ],
  LLM_BASE_URL: [
    "https://api.deepseek.com/v1",
    "https://api.moonshot.cn/v1",
    "https://openrouter.ai/api/v1",
  ],
  SEARCH_PROVIDER: ["open", "anysearch", "off"],
};

let settingsState = { items: [], groups: [], dirty: {} };

settingsDrawer?.querySelectorAll("[data-settings-action='close']").forEach((el) => {
  el.addEventListener("click", closeSettingsDrawer);
});
document.getElementById("settings-save")?.addEventListener("click", saveSettings);
document.getElementById("prompts-save")?.addEventListener("click", savePrompts);

// Tab 切换:点击 [data-settings-tab="prompts"] 等切到对应 panel
settingsDrawer?.querySelectorAll("[data-settings-tab]").forEach((el) => {
  el.addEventListener("click", () => switchSettingsTab(el.dataset.settingsTab));
});

function switchSettingsTab(tabName) {
  if (!settingsDrawer) return;
  settingsDrawer.querySelectorAll("[data-settings-tab]").forEach((tab) => {
    const active = tab.dataset.settingsTab === tabName;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", active ? "true" : "false");
  });
  const apiPanel = document.getElementById("settings-tab-api");
  const promptsPanel = document.getElementById("settings-tab-prompts");
  apiPanel?.classList.toggle("hidden", tabName !== "api");
  promptsPanel?.classList.toggle("hidden", tabName !== "prompts");
  if (tabName === "prompts" && !promptsState.loaded) loadPrompts();
}

async function openSettingsDrawer() {
  if (!settingsDrawer) return;
  settingsDrawer.classList.remove("hidden");
  document.body.classList.add("settings-page-open");
  // 默认进 API tab
  switchSettingsTab("api");
  settingsBody.textContent = "正在加载…";
  settingsState = { items: [], groups: [], dirty: {} };
  try {
    const response = await fetch("/api/settings");
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${response.status}`);
    }
    const data = await response.json();
    settingsState.items = data.items || [];
    settingsState.groups = data.groups || [];
    renderSettingsBody();
  } catch (error) {
    settingsBody.textContent = `加载失败:${error.message}`;
  }
}

function closeSettingsDrawer() {
  settingsDrawer?.classList.add("hidden");
  document.body.classList.remove("settings-page-open");
}

// "当前生效配置"状态卡 —— 让用户一眼看到运行时到底跑的什么,
// 而不是"我以为我配了 anysearch,实际还是 open"那种暗坑。
// 同时检测几个常见错配 (key 配了路由没切等) 给 warn。
function renderSettingsStatus() {
  if (!settingsState.items.length) return null;
  const items = settingsState.items;
  const get = (key) => items.find((it) => it.key === key);
  const val = (key) => (get(key)?.value || "").trim();
  const isSet = (key) => Boolean(get(key)?.is_set);

  const model = val("LLM_MODEL");
  const baseUrl = val("LLM_BASE_URL");
  const llmKeySet = isSet("LLM_API_KEY");

  const searchProvider = (val("SEARCH_PROVIDER") || "open").toLowerCase();
  let searchDetail = "";
  if (searchProvider === "open") {
    searchDetail = "本地 open-webSearch daemon (免费)";
  } else if (searchProvider === "anysearch") {
    searchDetail = isSet("ANYSEARCH_API_KEY")
      ? "AnySearch · Bearer 已认证"
      : "AnySearch · 匿名 (按 IP 限免费额度)";
  } else if (searchProvider === "off") {
    searchDetail = "已关闭,所有联网搜索都不会发起";
  } else {
    searchDetail = `未知路由 ${searchProvider} —— 会回退到 open`;
  }

  // 错配检测
  const warnings = [];
  if (isSet("ANYSEARCH_API_KEY") && searchProvider !== "anysearch") {
    warnings.push(
      `AnySearch API Key 配了,但网页搜索路由是 <code>${escapeHtml(searchProvider)}</code> —— key 不会被调用。要用 AnySearch,需把"网页搜索路由"改成 <code>anysearch</code>。`,
    );
  }
  if (!llmKeySet) {
    warnings.push(
      "LLM_API_KEY 未设置 —— 对话会走本地兜底模板(无智能)。",
    );
  }

  const status = document.createElement("section");
  status.className = "settings-status";
  status.innerHTML = `
    <header class="settings-status-head">
      <span class="settings-status-dot" aria-hidden="true"></span>
      <strong>当前生效配置</strong>
      <span class="settings-status-hint">下面的修改保存后立即生效</span>
    </header>
    <div class="settings-status-grid">
      <div class="settings-status-row">
        <span class="settings-status-label">对话模型</span>
        <div class="settings-status-value">
          <code>${escapeHtml(model || "(未设置)")}</code>
          <span class="settings-status-detail">base_url: <code>${escapeHtml(baseUrl || "(默认)")}</code> · API key ${llmKeySet ? "<span class='ok'>✓ 已设置</span>" : "<span class='warn'>未设置</span>"}</span>
        </div>
      </div>
      <div class="settings-status-row">
        <span class="settings-status-label">网页搜索</span>
        <div class="settings-status-value">
          <code>${escapeHtml(searchProvider)}</code>
          <span class="settings-status-detail">${searchDetail}</span>
        </div>
      </div>
    </div>
    ${
      warnings.length
        ? `<ul class="settings-status-warnings">${warnings
            .map((w) => `<li>⚠ ${w}</li>`)
            .join("")}</ul>`
        : ""
    }
  `;
  return status;
}

// 渲染单个字段的 row(label + meta + input + description + datalist)
function renderSettingRow(item) {
  const row = document.createElement("div");
  row.className = "settings-row";

  const labelEl = document.createElement("label");
  labelEl.className = "settings-label";
  labelEl.htmlFor = `setting-${item.key}`;
  // 字段说明改成 ? 图标:鼠标悬停才显示 tooltip,不占视觉空间
  const helpIcon = item.description
    ? `<button type="button" class="help-icon" data-tooltip="${escapeHtml(item.description)}" tabindex="0" aria-label="查看说明">?</button>`
    : "";
  labelEl.innerHTML =
    `<span class="settings-label-name">${escapeHtml(item.label)}${helpIcon}</span>` +
    `<em class="settings-meta">${
      item.source === "db" ? "DB 已设置" : item.source === "env" ? ".env 兜底" : "未设置"
    }</em>`;

  const input = document.createElement("input");
  input.id = `setting-${item.key}`;
  input.type = item.sensitive ? "password" : "text";
  input.placeholder = item.is_set ? (item.sensitive ? `已设置 (${item.value})` : item.value) : "未设置";
  input.dataset.key = item.key;
  if (!item.sensitive && item.value && item.source !== "default") {
    input.value = item.value;
  }
  input.addEventListener("input", () => {
    settingsState.dirty[item.key] = input.value;
  });

  // 字段下拉建议(combobox 模式)
  const suggestions = FIELD_SUGGESTIONS[item.key];
  let datalistEl = null;
  if (suggestions && suggestions.length) {
    const datalistId = `datalist-${item.key}`;
    input.setAttribute("list", datalistId);
    datalistEl = document.createElement("datalist");
    datalistEl.id = datalistId;
    for (const opt of suggestions) {
      const option = document.createElement("option");
      option.value = opt;
      datalistEl.append(option);
    }
  }

  row.append(labelEl, input);
  if (datalistEl) row.append(datalistEl);
  return row;
}

function renderSettingsBody() {
  settingsBody.innerHTML = "";
  const status = renderSettingsStatus();
  if (status) settingsBody.append(status);

  // 按 group 分桶。groups 数组从后端拿,已按 order 排好;items 落到 buckets[group.key]
  const groups = (settingsState.groups || []).length
    ? settingsState.groups
    : [{ key: "general", title: "配置", description: "", order: 0 }];
  const buckets = new Map();
  for (const g of groups) buckets.set(g.key, []);
  for (const item of settingsState.items) {
    const key = buckets.has(item.group) ? item.group : groups[groups.length - 1].key;
    buckets.get(key).push(item);
  }

  for (const group of groups) {
    const items = buckets.get(group.key) || [];
    if (!items.length) continue;
    const section = document.createElement("section");
    section.className = "settings-group";
    section.dataset.groupKey = group.key;
    section.innerHTML = `
      <header class="settings-group-head">
        <h3>${escapeHtml(group.title)}</h3>
        ${group.description ? `<p>${escapeHtml(group.description)}</p>` : ""}
      </header>
    `;
    for (const item of items) section.append(renderSettingRow(item));

    // LLM 分组底部挂"试连一下"按钮
    if (group.key === "llm") {
      const testRow = document.createElement("div");
      testRow.className = "settings-test-row";
      testRow.innerHTML = `
        <button type="button" class="ghost-button" id="settings-test">用当前表单里的新 key 试连一下</button>
        <span id="settings-test-result" class="settings-test-result"></span>
      `;
      section.append(testRow);
    }
    settingsBody.append(section);
  }

  document.getElementById("settings-test")?.addEventListener("click", testLlmConnection);
}

async function testLlmConnection() {
  const resultEl = document.getElementById("settings-test-result");
  // 试连只读 dirty(用户在表单里临时填的),不读已保存值 —— 因为已保存的 key 是 mask 的
  const apiKey = (settingsState.dirty.LLM_API_KEY || "").trim();
  if (!apiKey) {
    resultEl.textContent = "请先在 API Key 一栏填入新 key 再试(不会被保存)";
    resultEl.className = "settings-test-result warn";
    return;
  }
  // model / base_url 优先用 dirty,落空回到当前已保存值(明文,非 mask)
  const modelItem = settingsState.items.find((i) => i.key === "LLM_MODEL");
  const baseItem = settingsState.items.find((i) => i.key === "LLM_BASE_URL");
  const model = (settingsState.dirty.LLM_MODEL || modelItem?.value || "").trim();
  const baseUrl = (settingsState.dirty.LLM_BASE_URL || baseItem?.value || "").trim();
  resultEl.textContent = "测试中…";
  resultEl.className = "settings-test-result pending";
  try {
    const response = await fetch("/api/settings/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        api_key: apiKey,
        model: model || null,
        base_url: baseUrl || null,
      }),
    });
    const data = await response.json();
    if (data.ok) {
      resultEl.textContent = `✓ 通了 (${data.latency_ms}ms) ${data.detail || ""}`.trim();
      resultEl.className = "settings-test-result ok";
    } else {
      resultEl.textContent = `✗ ${data.detail || "未知错误"}`;
      resultEl.className = "settings-test-result fail";
    }
  } catch (error) {
    resultEl.textContent = `请求失败:${error.message}`;
    resultEl.className = "settings-test-result fail";
  }
}

async function saveSettings() {
  const updates = settingsState.dirty;
  if (!Object.keys(updates).length) {
    closeSettingsDrawer();
    return;
  }
  const btn = document.getElementById("settings-save");
  btn.disabled = true;
  const prev = btn.textContent;
  btn.textContent = "保存中…";
  try {
    const response = await fetch("/api/settings", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ updates }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${response.status}`);
    }
    closeSettingsDrawer();
  } catch (error) {
    window.alert(`保存失败:${error.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = prev;
  }
}

// ===== Prompt 模板编辑 =====
// items: [{key, label, description, variables, value, default, source, is_overridden}, ...]
// dirty: {key: 新模板原文}
// expanded: Set<key> 展开中的卡片(默认全部折叠,点标题展开)
let promptsState = { items: [], dirty: {}, expanded: new Set(), loaded: false };

async function loadPrompts() {
  if (!promptsBody) return;
  promptsBody.textContent = "正在加载…";
  promptsState.dirty = {};
  try {
    const response = await fetch("/api/prompts");
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${response.status}`);
    }
    const data = await response.json();
    promptsState.items = data.items || [];
    promptsState.loaded = true;
    renderPromptsBody();
  } catch (error) {
    promptsBody.textContent = `加载失败:${error.message}`;
  }
}

function renderPromptsBody() {
  if (!promptsBody) return;
  promptsBody.innerHTML = "";
  for (const item of promptsState.items) {
    const card = document.createElement("article");
    card.className = "prompt-card";
    if (item.is_overridden) card.classList.add("is-overridden");
    if (promptsState.expanded.has(item.key)) card.classList.add("is-expanded");

    // 头部:可点击切换展开
    const header = document.createElement("header");
    header.className = "prompt-card-head";
    header.innerHTML = `
      <div class="prompt-card-title">
        <strong>${escapeHtml(item.label)}</strong>
        <span class="prompt-card-meta">${item.is_overridden ? "DB 已覆盖" : "默认"}</span>
      </div>
      <button type="button" class="prompt-card-toggle" aria-label="展开/收起">
        ${promptsState.expanded.has(item.key) ? "收起" : "展开"}
      </button>
    `;
    header.addEventListener("click", () => {
      if (promptsState.expanded.has(item.key)) {
        promptsState.expanded.delete(item.key);
      } else {
        promptsState.expanded.add(item.key);
      }
      renderPromptsBody();
    });
    card.append(header);

    const desc = document.createElement("p");
    desc.className = "prompt-card-desc";
    desc.textContent = item.description;
    card.append(desc);

    if (promptsState.expanded.has(item.key)) {
      // 可用变量提示
      if (item.variables && item.variables.length) {
        const vars = document.createElement("div");
        vars.className = "prompt-card-vars";
        vars.innerHTML = `<span class="prompt-vars-label">可用变量:</span> ${item.variables
          .map((v) => `<code>{${escapeHtml(v)}}</code>`)
          .join(" ")}`;
        card.append(vars);
      }

      // 编辑器
      const ta = document.createElement("textarea");
      ta.className = "prompt-card-editor";
      ta.rows = 14;
      ta.spellcheck = false;
      ta.value = promptsState.dirty[item.key] !== undefined
        ? promptsState.dirty[item.key]
        : item.value;
      ta.addEventListener("input", () => {
        promptsState.dirty[item.key] = ta.value;
      });
      card.append(ta);

      // 操作按钮
      const actions = document.createElement("div");
      actions.className = "prompt-card-actions";
      actions.innerHTML = `
        <button type="button" class="ghost-button" data-prompt-action="restore" data-key="${escapeHtml(item.key)}">恢复默认到编辑框</button>
        ${item.is_overridden ? `<button type="button" class="ghost-button danger" data-prompt-action="reset" data-key="${escapeHtml(item.key)}">删除 DB 覆盖,回到默认</button>` : ""}
      `;
      actions.querySelector("[data-prompt-action='restore']")?.addEventListener("click", () => {
        promptsState.dirty[item.key] = item.default;
        renderPromptsBody();
      });
      actions.querySelector("[data-prompt-action='reset']")?.addEventListener("click", async () => {
        if (!window.confirm(`确认把「${item.label}」回退到代码默认值?`)) return;
        try {
          const response = await fetch(`/api/prompts/${encodeURIComponent(item.key)}/reset`, {
            method: "POST",
          });
          if (!response.ok) {
            const body = await response.json().catch(() => ({}));
            throw new Error(body.detail || `HTTP ${response.status}`);
          }
          delete promptsState.dirty[item.key];
          await loadPrompts();
        } catch (error) {
          window.alert(`重置失败:${error.message}`);
        }
      });
      card.append(actions);
    }

    promptsBody.append(card);
  }
}

async function savePrompts() {
  const updates = promptsState.dirty;
  if (!Object.keys(updates).length) {
    closeSettingsDrawer();
    return;
  }
  const btn = document.getElementById("prompts-save");
  btn.disabled = true;
  const prev = btn.textContent;
  btn.textContent = "保存中…";
  try {
    const response = await fetch("/api/prompts", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ updates }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${response.status}`);
    }
    const data = await response.json();
    promptsState.items = data.items || [];
    promptsState.dirty = {};
    renderPromptsBody();
    // 不关闭,让用户看到保存成功后回到 "默认/已覆盖" 标记的更新
    btn.textContent = "已保存 ✓";
    setTimeout(() => { btn.textContent = prev; }, 1500);
  } catch (error) {
    window.alert(`保存失败:${error.message}`);
    btn.textContent = prev;
  } finally {
    btn.disabled = false;
  }
}

// ===== Bootstrap:先验 token,再决定走 restore 还是登录 =====
(async () => {
  const ok = await bootstrapAuth();
  if (ok) await restore();
})();
