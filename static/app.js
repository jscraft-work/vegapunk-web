"use strict";

const md = window.markdownit({ html: false, linkify: true, breaks: true });

// 위키링크 [[제목]] → <a class="wikilink" data-title="제목">. 존재 안하면 .broken
let knownTitles = new Set();
let currentPageTitle = null;
const WIKILINK = /\[\[([^\]]+)\]\]/g;
function renderMarkdown(text) {
  // 마크다운 먼저 렌더(html:false 라 내용 속 HTML은 이스케이프됨) → 그 후 위키링크 주입
  const html = md.render(text);
  return html.replace(WIKILINK, (_, t) => {
    const title = t.trim();
    const broken = knownTitles.has(title) ? "" : " broken";
    return `<a class="wikilink${broken}" data-title="${encodeURIComponent(title)}">${escapeHtml(title)}</a>`;
  });
}

// ---- 페이지 목록/패널 ----
const pageList = document.getElementById("page-list");
const panel = document.getElementById("page-panel");
const panelTitle = document.getElementById("panel-title");
const panelBody = document.getElementById("panel-body");

let activeTag = null;  // 지식 사이드바 태그 필터

async function refreshTitles() {
  const r = await fetch("/api/pages");
  knownTitles = new Set((await r.json()).pages.map((p) => p.title));
}

async function loadPages(filter) {
  let pages;
  if (filter && filter.trim()) {
    const r = await fetch(`/api/search?q=${encodeURIComponent(filter)}`);
    pages = (await r.json()).results.map((x) => ({ title: x.title }));
  } else {
    const url = activeTag ? `/api/pages?tag=${encodeURIComponent(activeTag)}` : "/api/pages";
    pages = (await (await fetch(url)).json()).pages;
  }
  pageList.innerHTML = "";
  for (const p of pages) {
    const li = document.createElement("li");
    li.textContent = p.title;
    li.onclick = () => openPage(p.title, "manage");
    pageList.appendChild(li);
  }
}

const tagBar = document.getElementById("tag-bar");
async function loadTags() {
  const tags = (await (await fetch("/api/tags")).json()).tags;
  tagBar.innerHTML = "";
  if (!tags.length) return;
  const mk = (label, val) => {
    const c = document.createElement("span");
    c.className = "tag-chip" + ((activeTag === val) ? " active" : "");
    c.textContent = label;
    c.onclick = () => { activeTag = val; loadTags(); loadPages(); };
    return c;
  };
  tagBar.appendChild(mk("전체", null));
  for (const t of tags) tagBar.appendChild(mk(`${t.tag} ${t.count}`, t.tag));
}

let pageReturnTo = "chat";  // 페이지 닫으면 돌아갈 모드

function showPane(which) {
  document.getElementById("chat-pane").classList.toggle("hidden", which !== "chat");
  document.getElementById("manage-pane").classList.toggle("hidden", which !== "manage");
  panel.classList.toggle("hidden", which !== "page");
}

async function openPage(title, from) {
  const r = await fetch(`/api/page/${encodeURIComponent(title)}`);
  if (!r.ok) return;
  const { page, titles } = await r.json();
  knownTitles = new Set(titles);
  currentPageTitle = page.title;
  panelTitle.textContent = page.title;
  document.getElementById("ptags-input").value = (page.tags || []).join(", ");
  panelBody.innerHTML = renderMarkdown(page.body || "");
  if (from) pageReturnTo = from;  // 명시 호출(사이드바/관리/출처)만 갱신, 위키링크 점프는 유지
  showPane("page");
  document.querySelector(".panel-scroll").scrollTop = 0;
}

document.getElementById("panel-back").onclick = () => showPane(pageReturnTo);

// 페이지 태그 편집
const ptagsInput = document.getElementById("ptags-input");
document.getElementById("ptags-suggest").onclick = async () => {
  if (!currentPageTitle) return;
  const btn = document.getElementById("ptags-suggest");
  const old = btn.textContent; btn.textContent = "제안 중…"; btn.disabled = true;
  try {
    const { tags } = await (await fetch(`/api/page/${encodeURIComponent(currentPageTitle)}/suggest-tags`, { method: "POST" })).json();
    const cur = ptagsInput.value.split(",").map((s) => s.trim()).filter(Boolean);
    ptagsInput.value = [...new Set([...cur, ...(tags || [])])].join(", ");
  } catch { /* ignore */ }
  btn.textContent = old; btn.disabled = false;
};
document.getElementById("ptags-save").onclick = async () => {
  if (!currentPageTitle) return;
  const tags = ptagsInput.value.split(",").map((s) => s.trim()).filter(Boolean);
  await fetch(`/api/page/${encodeURIComponent(currentPageTitle)}/tags`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ tags }),
  });
  loadTags(); loadPages();
  const btn = document.getElementById("ptags-save"); const o = btn.textContent;
  btn.textContent = "저장됨 ✓"; setTimeout(() => { btn.textContent = o; }, 1200);
};

document.getElementById("panel-delete").onclick = async () => {
  if (!currentPageTitle) return;
  const title = currentPageTitle;
  // 역참조 확인
  const r = await fetch(`/api/backlinks/${encodeURIComponent(title)}`);
  const { backlinks } = await r.json();
  let msg = `"${title}" 페이지를 삭제할까요?`;
  if (backlinks.length) {
    msg += `\n\n⚠️ 이 ${backlinks.length}개 문서가 [[${title}]]로 참조 중입니다 (삭제 시 점선 링크로 남음):\n· ` + backlinks.join("\n· ");
  }
  if (!confirm(msg)) return;
  await fetch(`/api/page/${encodeURIComponent(title)}`, { method: "DELETE" });
  currentPageTitle = null;
  await refreshTitles();
  loadPages();
  if (pageReturnTo === "manage") await openManage(); else showPane("chat");
};
document.getElementById("page-search").addEventListener("input", (e) => loadPages(e.target.value));

// 위키링크/패널 클릭 위임
document.body.addEventListener("click", (e) => {
  const a = e.target.closest("a.wikilink");
  if (a) { openPage(decodeURIComponent(a.dataset.title)); return; }
  const tc = e.target.closest(".page-tags .tag-chip");
  if (tc) {  // 페이지 본문의 태그 클릭 → 지식 공간에서 그 태그로 필터
    activeTag = decodeURIComponent(tc.dataset.tag);
    setSpace("knowledge");
  }
});

// ---- 채팅 (SSE) ----
const messages = document.getElementById("messages");
const form = document.getElementById("chat-form");
const input = document.getElementById("chat-input");
const sendBtn = document.getElementById("send-btn");

// ---- 메모 패널 (데스크탑: Milkdown WYSIWYG) ----
// 에디터 인스턴스는 index.html 모듈 스크립트에서 생성해 window.__memo로 노출.
// 저장(localStorage)은 모듈의 markdownUpdated 리스너가 처리.
const memoPanel = document.getElementById("memo-panel");
const getMemo = () => (window.__memo ? window.__memo.get() : "");
const setMemo = (v) => { if (window.__memo) window.__memo.set(v); };
document.getElementById("memo-clear").onclick = () => {
  if (!getMemo().trim() || confirm("메모를 비울까요?")) setMemo("");
};
function appendToMemo(text) {
  const cur = getMemo().replace(/\s+$/, "");
  setMemo((cur ? cur + "\n\n---\n\n" : "") + text);
}

// 채팅↔메모 너비 리사이즈(드래그). 저장된 너비 복원.
const memoResizer = document.getElementById("memo-resizer");
const savedMemoW = localStorage.getItem("vegapunk_memo_w");
if (savedMemoW) memoPanel.style.width = savedMemoW + "px";
let memoResizing = false;
memoResizer.addEventListener("mousedown", (e) => {
  memoResizing = true;
  document.body.classList.add("resizing-col");
  e.preventDefault();
});
document.addEventListener("mousemove", (e) => {
  if (!memoResizing) return;
  const r = document.getElementById("app").getBoundingClientRect();
  const w = Math.max(220, Math.min(r.right - e.clientX, r.width - 500));  // 메모 220~(앱-500)
  memoPanel.style.width = w + "px";
});
document.addEventListener("mouseup", () => {
  if (!memoResizing) return;
  memoResizing = false;
  document.body.classList.remove("resizing-col");
  localStorage.setItem("vegapunk_memo_w", parseInt(memoPanel.style.width, 10));
});

function addMsg(cls, html) {
  const div = document.createElement("div");
  div.className = `msg ${cls}`;
  div.innerHTML = html;
  messages.appendChild(div);
  messages.scrollTop = messages.scrollHeight;
  return div;
}

function chipRow(items) {
  if (!items.length) return "";
  return `<div class="chips">${items.join("")}</div>`;
}

function sourceChips(sources) {
  return (sources || []).map(
    (s) => `<span class="chip" data-title="${encodeURIComponent(s.title)}" onclick="openPage(decodeURIComponent(this.dataset.title), 'chat')">📎 ${s.title}</span>`
  );
}

function renderBotInto(el, text, sources, prompt) {
  const chips = sourceChips(sources);
  if (prompt) chips.push(`<span class="chip debug-chip">🔍 컨텍스트</span>`);
  chips.push(`<span class="chip memo-chip">📝 메모로</span>`);
  el.innerHTML = `<div class="markdown-body">${renderMarkdown(text)}</div>${chipRow(chips)}`;
  const dc = el.querySelector(".debug-chip");
  if (dc) dc.onclick = () => openDebug(prompt);
  const mc = el.querySelector(".memo-chip");
  if (mc) mc.onclick = () => appendToMemo(text);
}

function openDebug(prompt) {
  document.getElementById("debug-pre").textContent = prompt || "(컨텍스트 없음)";
  document.getElementById("debug-overlay").classList.remove("hidden");
}
document.getElementById("debug-close").onclick = () =>
  document.getElementById("debug-overlay").classList.add("hidden");

// ---- 설정 (검색 관련성 임계 등, 즉시 반영) ----
const settingsOverlay = document.getElementById("settings-overlay");
const setVecdist = document.getElementById("set-vecdist");
const setVecdistVal = document.getElementById("set-vecdist-val");
async function openSettings() {
  try {
    const s = await (await fetch("/api/settings")).json();
    setVecdist.value = s.vec_dist_threshold;
    setVecdistVal.textContent = (+s.vec_dist_threshold).toFixed(2);
  } catch { /* ignore */ }
  settingsOverlay.classList.remove("hidden");
}
document.getElementById("settings-btn").onclick = openSettings;
document.getElementById("settings-close").onclick = () => settingsOverlay.classList.add("hidden");
setVecdist.addEventListener("input", () => { setVecdistVal.textContent = (+setVecdist.value).toFixed(2); });
setVecdist.addEventListener("change", async () => {  // 슬라이더 놓는 즉시 서버 반영
  await fetch("/api/settings", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ vec_dist_threshold: +setVecdist.value }),
  });
});

// ---- 버전 이력 / 되돌리기 ----
const versionOverlay = document.getElementById("version-overlay");
const versionList = document.getElementById("version-list");
const versionPreview = document.getElementById("version-preview");
let viewingVersionId = null;

function fmtTs(s) { return (s || "").replace("T", " ").slice(0, 16); }

async function openVersions() {
  if (!currentPageTitle) return;
  versionPreview.classList.add("hidden");
  versionList.innerHTML = `<li class="version-empty">불러오는 중…</li>`;
  versionOverlay.classList.remove("hidden");
  const { versions } = await (await fetch(`/api/page/${encodeURIComponent(currentPageTitle)}/versions`)).json();
  if (!versions || !versions.length) {
    versionList.innerHTML = `<li class="version-empty">수정 이력이 없어요.</li>`;
    return;
  }
  versionList.innerHTML = "";
  for (const v of versions) {
    const li = document.createElement("li");
    li.innerHTML = `<span class="v-meta">${fmtTs(v.created_at)} · ${escapeHtml(v.source || "")}</span>` +
      `<button class="v-view" data-id="${v.id}">미리보기</button>`;
    versionList.appendChild(li);
  }
}

document.getElementById("panel-versions").onclick = openVersions;
document.getElementById("version-close").onclick = () => versionOverlay.classList.add("hidden");

versionList.addEventListener("click", async (e) => {
  const btn = e.target.closest(".v-view");
  if (!btn) return;
  viewingVersionId = +btn.dataset.id;
  const { body } = await (await fetch(`/api/page/${encodeURIComponent(currentPageTitle)}/versions/${viewingVersionId}`)).json();
  document.getElementById("vp-label").textContent = `버전 #${viewingVersionId} 미리보기`;
  document.getElementById("vp-body").textContent = body || "";
  versionPreview.classList.remove("hidden");
});

document.getElementById("version-restore").onclick = async () => {
  if (!viewingVersionId || !currentPageTitle) return;
  if (!confirm("이 버전으로 되돌릴까요?\n(현재 본문은 새 이력으로 백업됩니다)")) return;
  await fetch(`/api/page/${encodeURIComponent(currentPageTitle)}/restore`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ version_id: viewingVersionId }),
  });
  versionOverlay.classList.add("hidden");
  await openPage(currentPageTitle);   // 본문 갱신
  await refreshTitles();
};

let currentConvId = null;
let activeES = null;  // 현재 포그라운드 스트림. 대화 전환 시 null 로 백그라운드화.

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const q = input.value.trim();
  if (!q) return;
  addMsg("user", escapeHtml(q));
  input.value = "";
  input.style.height = "auto";
  sendBtn.disabled = true;

  const bot = addMsg("bot", `<span class="thinking">생각 중…</span>`);
  let sources = [];

  const es = new EventSource(`/api/chat?q=${encodeURIComponent(q)}&conv=${currentConvId || 0}`);
  activeES = es;
  const fg = () => es === activeES;  // 이 스트림이 아직 화면에 떠 있나

  es.addEventListener("conversation", (ev) => {
    const id = JSON.parse(ev.data).id;
    if (fg()) currentConvId = id;
    loadConversations();  // 새 대화 즉시 사이드바에 표시
  });
  es.addEventListener("sources", (ev) => {
    sources = JSON.parse(ev.data);
  });
  es.addEventListener("answer", (ev) => {
    if (!fg()) return;  // 다른 대화로 이동했으면 화면 안 건드림(응답은 서버에 저장됨)
    const d = JSON.parse(ev.data);
    renderBotInto(bot, d.text, sources, d.prompt);
    messages.scrollTop = messages.scrollHeight;
  });
  es.addEventListener("suggest", () => {
    if (!fg()) return;
    bot.innerHTML += chipRow([`<span class="chip save" onclick="window.openDistill()">💾 지식으로 저장</span>`]);
  });
  es.addEventListener("error", (ev) => {
    if (fg()) {
      try { bot.innerHTML = `<div class="markdown-body">⚠️ ${JSON.parse(ev.data).message}</div>`; }
      catch { bot.innerHTML = `<div class="markdown-body">⚠️ 연결 오류</div>`; }
      sendBtn.disabled = false;
    }
    es.close();
  });
  es.addEventListener("done", () => {
    es.close();
    if (fg()) sendBtn.disabled = false;
    loadConversations();  // 제목/순서 갱신
  });
});

// ---- 대화 목록 ----
const convList = document.getElementById("conv-list");

async function loadConversations() {
  const r = await fetch("/api/conversations");
  const convs = (await r.json()).conversations;
  convList.innerHTML = "";
  for (const c of convs) {
    const li = document.createElement("li");
    if (c.id === currentConvId) li.classList.add("active");
    li.innerHTML =
      `<span class="conv-title">${escapeHtml(c.title || "새 대화")}</span>` +
      `<span class="conv-actions">` +
      `<button class="conv-retitle" title="제목 자동생성(✨)">✨</button>` +
      `<button class="conv-del" title="삭제">🗑</button></span>`;
    const titleEl = li.querySelector(".conv-title");
    let clickTimer = null;
    // 단일클릭=열기, 더블클릭=이름변경. 더블클릭이면 대기 중인 '열기'를 취소해
    // openConversation의 목록 재렌더가 수정창을 날리지 않게 한다.
    titleEl.onclick = () => {
      if (clickTimer) return;
      clickTimer = setTimeout(() => { clickTimer = null; openConversation(c.id); }, 220);
    };
    titleEl.ondblclick = (e) => {
      e.stopPropagation();
      if (clickTimer) { clearTimeout(clickTimer); clickTimer = null; }
      renameConversation(c.id, titleEl);
    };
    li.querySelector(".conv-retitle").onclick = (e) => { e.stopPropagation(); retitleConversation(c.id, titleEl); };
    li.querySelector(".conv-del").onclick = (e) => { e.stopPropagation(); deleteConversation(c.id); };
    convList.appendChild(li);
  }
}

function renameConversation(id, titleEl) {
  const old = titleEl.textContent;
  const input = document.createElement("input");
  input.className = "conv-rename-input";
  input.value = old;
  titleEl.replaceWith(input);
  input.focus(); input.select();
  let done = false;
  const save = async () => {
    if (done) return; done = true;
    const v = input.value.trim();
    if (v && v !== old) {
      await fetch(`/api/conversations/${id}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: v }),
      });
    }
    loadConversations();
  };
  input.onkeydown = (e) => {
    if (e.key === "Enter") { e.preventDefault(); save(); }
    else if (e.key === "Escape") { done = true; loadConversations(); }
  };
  input.onblur = save;
}

async function retitleConversation(id, titleEl) {
  titleEl.textContent = "✨ 제목 생성 중…";
  try { await fetch(`/api/conversations/${id}/retitle`, { method: "POST" }); } catch { /* ignore */ }
  loadConversations();
}

async function openConversation(id) {
  const r = await fetch(`/api/conversations/${id}`);
  if (!r.ok) return;
  const conv = await r.json();
  activeES = null;          // 진행 중 스트림은 백그라운드로(서버엔 계속 저장)
  sendBtn.disabled = false;
  currentConvId = id;
  messages.innerHTML = "";
  for (const m of conv.messages) {
    if (m.role === "user") addMsg("user", escapeHtml(m.content));
    else renderBotInto(addMsg("bot", ""), m.content, m.sources, m.prompt);
  }
  showPane("chat");
  loadConversations();
}

function newChat() {
  activeES = null;
  sendBtn.disabled = false;
  currentConvId = null;
  messages.innerHTML = "";
  showPane("chat");
  loadConversations();
  input.focus();
}

async function deleteConversation(id) {
  if (!confirm("이 대화를 삭제할까요?")) return;
  await fetch(`/api/conversations/${id}`, { method: "DELETE" });
  if (id === currentConvId) newChat();
  else loadConversations();
}

document.getElementById("new-chat").onclick = newChat;

// ---- GNB (채팅 / 지식 공간 전환) ----
const gnbItems = document.querySelectorAll(".gnb-item");
const sideChats = document.getElementById("side-chats");
const sidePages = document.getElementById("side-pages");

function setSpace(space) {
  gnbItems.forEach((b) => b.classList.toggle("active", b.dataset.space === space));
  sideChats.classList.toggle("hidden", space !== "chat");
  sidePages.classList.toggle("hidden", space !== "knowledge");
  memoPanel.classList.toggle("hidden", space !== "chat");  // 메모는 채팅 공간에서만(모바일은 CSS로 숨김)
  memoResizer.classList.toggle("hidden", space !== "chat");
  if (space === "chat") {
    showPane("chat");
  } else {
    loadTags();
    loadPages();
    openManage();  // 지식 → 메인은 바로 관리 뷰
  }
}
gnbItems.forEach((b) => (b.onclick = () => setSpace(b.dataset.space)));

// ---- 모바일 사이드바 드로어 ----
const closeSidebar = () => document.body.classList.remove("sidebar-open");
document.getElementById("menu-toggle").onclick = () => document.body.classList.toggle("sidebar-open");
document.getElementById("sidebar-backdrop").onclick = closeSidebar;
document.getElementById("sidebar").addEventListener("click", (e) => {
  if (e.target.closest("#conv-list li, #page-list li, #new-chat, .tag-chip")) closeSidebar();
});

// 입력창 자동 높이 + Enter 전송(Shift+Enter 줄바꿈)
input.addEventListener("input", () => {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 160) + "px";
});
input.addEventListener("keydown", (e) => {
  // e.isComposing: 한글 IME 조합 중이면 Enter는 조합 확정용 → 전송 안 함(끝글자 중복 방지)
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing && e.keyCode !== 229) {
    e.preventDefault();
    form.requestSubmit();
  }
});

// ---- 지식으로 저장 (LLM이 대화를 토픽별 페이지로 정리 → 검토 → 저장) ----
const distillOverlay = document.getElementById("distill-overlay");
const distillBodyEl = document.getElementById("distill-body");
const distillHint = document.getElementById("distill-hint");
const distillSave = document.getElementById("distill-save");

let distillCands = [];  // 현재 distill 후보들(merge_target 포함)

async function openDistill() {
  if (!currentConvId) { alert("저장할 대화가 없어요. 먼저 대화를 시작하세요."); return; }
  distillOverlay.classList.remove("hidden");
  distillBodyEl.innerHTML = `<div class="distill-empty">대화를 토픽별로 정리하는 중…</div>`;
  distillHint.textContent = "";
  distillSave.disabled = true;
  try {
    const r = await fetch("/api/distill", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conv_id: currentConvId }),
    });
    distillCands = (await r.json()).candidates || [];
  } catch { distillCands = []; }
  if (!distillCands.length) {
    distillBodyEl.innerHTML = `<div class="distill-empty">저장할 만한 내용을 못 찾았어요.</div>`;
    return;
  }
  distillBodyEl.innerHTML = "";
  distillCands.forEach((p, i) => {
    const mt = p.merge_target;
    const div = document.createElement("div");
    div.className = "distill-page";
    div.dataset.idx = i;
    // 병합 대상이 있으면 라디오(기본=새 노트, 병합은 선택 시 lazy 미리보기).
    const mergeUI = mt
      ? `<div class="d-merge">` +
        `<label><input type="radio" name="m${i}" value="merge"> 기존 「${escapeHtml(mt.title)}」에 병합 <span class="sim">${Math.round((mt.similarity || 0) * 100)}%</span></label>` +
        `<label><input type="radio" name="m${i}" value="new" checked> 새 노트</label>` +
        `</div>`
      : ``;
    div.innerHTML =
      `<div class="distill-page-head"><input type="checkbox" class="d-inc" checked>` +
      `<input type="text" class="d-title"></div>` +
      mergeUI +
      `<textarea class="d-body" rows="6"></textarea>` +
      `<pre class="d-diff hidden"></pre>` +
      `<input type="text" class="d-tags" placeholder="태그 (쉼표로 구분)">`;
    div.querySelector(".d-title").value = p.title;
    div.querySelector(".d-body").value = p.body;
    div.querySelector(".d-tags").value = (p.tags || []).join(", ");
    distillBodyEl.appendChild(div);
  });
  distillHint.textContent = `${distillCands.length}개 후보 — 병합/새노트 선택 후 저장`;
  distillSave.disabled = false;
}

// 병합 라디오 선택 시 → merge-preview(통합본+diff) lazy 로드
distillBodyEl.addEventListener("change", async (e) => {
  const radio = e.target.closest("input[type=radio]");
  if (!radio) return;
  const row = radio.closest(".distill-page");
  const cand = distillCands[+row.dataset.idx];
  const bodyEl = row.querySelector(".d-body");
  const diffEl = row.querySelector(".d-diff");
  if (radio.value === "merge") {
    diffEl.textContent = "통합 미리보기 생성 중…";
    diffEl.classList.remove("hidden");
    try {
      const r = await fetch("/api/notes/merge-preview", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target_note_id: cand.merge_target.note_id, candidate_body: bodyEl.value }),
      });
      const { merged_body, diff } = await r.json();
      bodyEl.value = merged_body;             // 통합본으로 교체(편집 가능)
      diffEl.textContent = diff || "(변경 없음)";
    } catch { diffEl.textContent = "미리보기 실패"; }
  } else {  // 새 노트 → 원본 본문 복원, diff 숨김
    bodyEl.value = cand.body;
    diffEl.classList.add("hidden");
    diffEl.textContent = "";
  }
});

distillSave.onclick = async () => {
  distillSave.disabled = true;
  for (const row of distillBodyEl.querySelectorAll(".distill-page")) {
    if (!row.querySelector(".d-inc").checked) continue;
    const cand = distillCands[+row.dataset.idx];
    const title = row.querySelector(".d-title").value.trim();
    const body = row.querySelector(".d-body").value;
    const tags = row.querySelector(".d-tags").value.split(",").map((s) => s.trim()).filter(Boolean);
    if (!title) continue;
    const mergeRadio = row.querySelector("input[type=radio][value=merge]");
    const merge_into = (mergeRadio && mergeRadio.checked) ? cand.merge_target.note_id : null;
    await fetch("/api/ingest", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, body, tags, merge_into }),
    });
  }
  distillOverlay.classList.add("hidden");
  await refreshTitles();
  loadTags();
  loadPages();
};

document.getElementById("save-conv").onclick = openDistill;
document.getElementById("distill-close").onclick = () => distillOverlay.classList.add("hidden");
window.openDistill = openDistill;

// ---- 관리 패널 ----
const chatPane = document.getElementById("chat-pane");
const managePane = document.getElementById("manage-pane");
const mRows = document.getElementById("manage-rows");
const mCount = document.getElementById("manage-count");
const mFilter = document.getElementById("manage-filter");
const mSort = document.getElementById("manage-sort");
const mOrphan = document.getElementById("manage-orphan");
const mDelete = document.getElementById("manage-delete");
const mSel = document.getElementById("manage-sel");
const mAll = document.getElementById("manage-all");
let manageData = [];
const selected = new Set();

async function openManage() {
  const r = await fetch("/api/manage");
  manageData = (await r.json()).pages;
  selected.clear();
  mAll.checked = false;
  renderManage();
  showPane("manage");
}

function closeManage() {
  showPane("chat");
}

function renderManage() {
  const f = mFilter.value.trim().toLowerCase();
  let rows = manageData.filter((p) => !f || p.title.toLowerCase().includes(f));
  if (mOrphan.checked) rows = rows.filter((p) => p.orphan);
  const sort = mSort.value;
  rows.sort((a, b) => {
    if (sort === "title") return a.title.localeCompare(b.title);
    if (sort === "len") return b.len - a.len;
    if (sort === "backlinks") return b.backlinks - a.backlinks;
    return (b.updated || "").localeCompare(a.updated || "");
  });
  mCount.textContent = `(${rows.length})`;
  mRows.innerHTML = "";
  for (const p of rows) {
    const tr = document.createElement("tr");
    if (p.orphan) tr.className = "orphan";
    const checked = selected.has(p.title) ? "checked" : "";
    tr.innerHTML =
      `<td><input type="checkbox" data-title="${encodeURIComponent(p.title)}" ${checked}></td>` +
      `<td class="title-cell" data-title="${encodeURIComponent(p.title)}">${escapeHtml(p.title)}</td>` +
      `<td>${p.updated || ""}</td><td class="num">${p.len}</td>` +
      `<td class="num">${p.outlinks}</td><td class="num">${p.backlinks}</td>`;
    mRows.appendChild(tr);
  }
  updateSelCount();
}

function updateSelCount() {
  mSel.textContent = selected.size;
  mDelete.disabled = selected.size === 0;
}

mRows.addEventListener("change", (e) => {
  const cb = e.target.closest("input[type=checkbox]");
  if (!cb) return;
  const t = decodeURIComponent(cb.dataset.title);
  if (cb.checked) selected.add(t); else selected.delete(t);
  updateSelCount();
});
mRows.addEventListener("click", (e) => {
  const cell = e.target.closest("td.title-cell");
  if (cell) openPage(decodeURIComponent(cell.dataset.title), "manage");
});
mAll.addEventListener("change", () => {
  mRows.querySelectorAll("input[type=checkbox]").forEach((cb) => {
    cb.checked = mAll.checked;
    const t = decodeURIComponent(cb.dataset.title);
    if (mAll.checked) selected.add(t); else selected.delete(t);
  });
  updateSelCount();
});
mFilter.addEventListener("input", renderManage);
mSort.addEventListener("change", renderManage);
mOrphan.addEventListener("change", renderManage);

mDelete.onclick = async () => {
  if (!selected.size) return;
  if (!confirm(`선택한 ${selected.size}개 페이지를 삭제할까요?\n(이들을 참조하는 링크는 점선으로 남습니다)`)) return;
  for (const t of selected) {
    await fetch(`/api/page/${encodeURIComponent(t)}`, { method: "DELETE" });
  }
  selected.clear();
  await refreshTitles();
  loadPages();
  await openManage();  // 목록 새로고침
};

function escapeHtml(s) { const d = document.createElement("div"); d.textContent = s; return d.innerHTML; }
function escapeAttr(s) { return s.replace(/'/g, "\\'").replace(/"/g, "&quot;"); }
window.openPage = openPage;

async function loadUser() {
  try {
    const { user } = await (await fetch("/auth/me")).json();
    const el = document.getElementById("gnb-user");
    if (user) {
      el.innerHTML = `<span>${escapeHtml(user.name || user.email)}</span><a href="/auth/logout">로그아웃</a>`;
    }
  } catch { /* 인증 비활성(로컬) 등 — 무시 */ }
}

refreshTitles();
loadPages();
loadConversations();
loadUser();
setSpace("chat");
