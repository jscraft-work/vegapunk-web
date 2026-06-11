"use strict";
// vegapunk SPA — 빌드 없는 바닐라 JS. fetch + EventSource만 사용.
// FE는 다시쓰기·검색·요약·병합매칭·인덱싱을 호출하지 않는다(서버 내부 처리).

const $ = (sel, el = document) => el.querySelector(sel);
const api = async (path, opts) => {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  return r.json();
};

// ── 순수 함수(단위 테스트 가능) ────────────────────────────────
// 마크다운 렌더 + [[위키링크]]를 존재/미존재로 칠한다.
function renderMarkdown(body, titles) {
  const known = new Set(titles || []);
  const withLinks = (body || "").replace(/\[\[([^\]|]+)(?:\|[^\]]+)?\]\]/g, (_, t) => {
    const cls = known.has(t.trim()) ? "wikilink" : "wikilink missing";
    return `<a class="${cls}" href="#/note/${encodeURIComponent(t.trim())}">${t.trim()}</a>`;
  });
  return window.marked ? window.marked.parse(withLinks) : withLinks;
}
window.renderMarkdown = renderMarkdown;

// ── 라우팅 ────────────────────────────────────────────────────
function route() {
  const hash = location.hash || "#/chat";
  document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
  if (hash.startsWith("#/note/")) {
    $(`.tab[data-tab="notes"]`).classList.add("active");
    renderNote(decodeURIComponent(hash.slice("#/note/".length)));
  } else if (hash.startsWith("#/notes")) {
    $(`.tab[data-tab="notes"]`).classList.add("active");
    renderNotesSpace();
  } else {
    $(`.tab[data-tab="chat"]`).classList.add("active");
    renderChatSpace(hash.startsWith("#/chat/") ? +hash.slice("#/chat/".length) : 0);
  }
}

// ── 채팅 ──────────────────────────────────────────────────────
async function renderChatSpace(convId) {
  const { conversations } = await api("/api/conversations");
  $("#sidebar").innerHTML =
    `<button onclick="location.hash='#/chat/0'">+ 새 대화</button>` +
    conversations.map((c) =>
      `<div class="conv-item" onclick="location.hash='#/chat/${c.id}'">${c.title || "(제목 없음)"}</div>`
    ).join("");

  $("#main").innerHTML =
    `<div style="text-align:right">${convId ? `<button onclick="openDistill(${convId})">💾 지식으로 저장</button>` : ""}</div>
     <div id="thread"></div>
     <div class="composer">
       <input id="q" placeholder="무엇이든 물어보세요" autofocus />
       <button id="send">전송</button>
     </div>`;

  if (convId) {
    const d = await api(`/api/conversations/${convId}`);
    (d.messages || []).forEach((m) => appendMessage(m.role, m.content, m.sources));
  }
  const send = () => {
    const q = $("#q").value.trim();
    if (q) { startChat(q, convId); $("#q").value = ""; }
  };
  $("#send").onclick = send;
  $("#q").onkeydown = (e) => { if (e.key === "Enter") send(); };
}

function appendMessage(role, content, sources) {
  const thread = $("#thread");
  const wrap = document.createElement("div");
  wrap.className = `msg ${role}`;
  if (sources && sources.length) wrap.appendChild(sourcesEl(sources));
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.innerHTML = role === "assistant" ? renderMarkdown(content, []) : content;
  wrap.appendChild(bubble);
  thread.appendChild(wrap);
  thread.scrollTop = thread.scrollHeight;
  return bubble;
}

function sourcesEl(sources) {
  const el = document.createElement("div");
  el.className = "sources";
  el.innerHTML = sources.map((s) =>
    `<span class="chip" onclick="location.hash='#/note/${encodeURIComponent(s.title)}'">📄 ${s.title}</span>`
  ).join("");
  return el;
}

function startChat(q, convId) {
  appendMessage("user", q, null);
  const hint = appendMessage("assistant", "🔍 검색 중…", null);
  let answer = "";
  const es = new EventSource(`/api/chat?q=${encodeURIComponent(q)}&conv=${convId}`);
  es.addEventListener("conversation", (e) => {
    const d = JSON.parse(e.data);
    if (!convId) { convId = d.id; history.replaceState(null, "", `#/chat/${d.id}`); }
  });
  es.addEventListener("sources", (e) => {
    const s = JSON.parse(e.data);
    if (s.length) hint.parentNode.insertBefore(sourcesEl(s), hint);
    hint.textContent = "";
  });
  es.addEventListener("answer", (e) => {
    answer += JSON.parse(e.data).text;
    hint.innerHTML = renderMarkdown(answer, []);
  });
  es.addEventListener("done", () => es.close());
  es.addEventListener("error", (e) => {
    try { hint.textContent += "\n⚠️ " + JSON.parse(e.data).message; } catch { /* 연결 종료 */ }
    es.close();
  });
}

// ── distill 모달 ──────────────────────────────────────────────
async function openDistill(convId) {
  const { candidates } = await api("/api/distill", {
    method: "POST", body: JSON.stringify({ conv_id: convId }),
  });
  if (!candidates.length) return alert("저장할 지식이 없습니다.");
  const cards = candidates.map((c, i) => candidateCard(c, i)).join("");
  showModal(`<h2>지식으로 저장</h2>${cards}
    <div style="margin-top:1rem"><button onclick="saveAllCandidates()">모두 저장</button></div>`);
  window.__cands = candidates;
}

function candidateCard(c, i) {
  const warn = c.merge_target
    ? `<p class="muted">⚠ 기존 「${c.merge_target.title}」와 ${Math.round(c.merge_target.similarity * 100)}% 유사</p>
       <label><input type="radio" name="m${i}" value="merge" checked> 기존 병합</label>
       <label><input type="radio" name="m${i}" value="new"> 새 노트</label>
       <label><input type="radio" name="m${i}" value="skip"> 버림</label>
       <div id="diff${i}"></div>`
    : `<label><input type="radio" name="m${i}" value="new" checked> 새 노트</label>
       <label><input type="radio" name="m${i}" value="skip"> 버림</label>`;
  return `<div class="candidate"><strong>${c.title}</strong>
    <div>${(c.tags || []).map((t) => `<span class="tag">#${t}</span>`).join("")}</div>${warn}</div>`;
}

async function saveAllCandidates() {
  for (let i = 0; i < window.__cands.length; i++) {
    const c = window.__cands[i];
    const choice = (document.querySelector(`input[name="m${i}"]:checked`) || {}).value;
    if (!choice || choice === "skip") continue;
    const merge_into = choice === "merge" && c.merge_target ? c.merge_target.note_id : null;
    await api("/api/ingest", {
      method: "POST",
      body: JSON.stringify({ title: c.title, body: c.body, tags: c.tags, merge_into }),
    });
  }
  closeModal(); location.hash = "#/notes";
}

// ── 지식 화면 ─────────────────────────────────────────────────
async function renderNotesSpace() {
  const [{ pages }, { tags }] = await Promise.all([api("/api/pages"), api("/api/tags")]);
  $("#sidebar").innerHTML =
    `<input id="note-search" placeholder="🔍 검색" />
     <div class="tags">${tags.map((t) => `<span class="tag" onclick="filterTag('${t.tag}')">#${t.tag} ${t.count}</span>`).join("")}</div>
     <div id="page-list">${pages.map(pageItem).join("")}</div>`;
  $("#note-search").onkeydown = async (e) => {
    if (e.key === "Enter") {
      const { results } = await api(`/api/search?q=${encodeURIComponent(e.target.value)}`);
      $("#page-list").innerHTML = results.map((r) =>
        `<div class="conv-item" onclick="location.hash='#/note/${encodeURIComponent(r.title)}'">
           ${r.title}<br><span class="muted">${r.snippet}</span></div>`).join("");
    }
  };
  $("#main").innerHTML = `<p class="muted">노트를 선택하세요.</p>`;
}
const pageItem = (p) =>
  `<div class="conv-item" onclick="location.hash='#/note/${encodeURIComponent(p.title)}'">${p.title}</div>`;

async function filterTag(tag) {
  const { pages } = await api(`/api/pages?tag=${encodeURIComponent(tag)}`);
  $("#page-list").innerHTML = pages.map(pageItem).join("");
}

async function renderNote(title) {
  const d = await api(`/api/page/${encodeURIComponent(title)}`);
  if (d.error) { $("#main").innerHTML = `<p>없는 노트입니다.</p>`; return; }
  const p = d.page;
  $("#main").innerHTML =
    `<h1>${p.title}</h1>
     <div>${p.tags.map((t) => `<span class="tag">#${t}</span>`).join("")}</div>
     <article>${renderMarkdown(p.body, d.titles)}</article>
     <div class="backlinks"><strong>백링크</strong>
       ${d.backlinks.map((b) => `<div><a href="#/note/${encodeURIComponent(b)}">${b}</a></div>`).join("") || "<span class='muted'>없음</span>"}
     </div>
     <div style="margin-top:1rem">
       <button onclick="suggestTags('${encodeURIComponent(title)}')">태그 제안</button>
       <button onclick="openVersions('${encodeURIComponent(title)}')">🕘 이력</button>
       <button onclick="deleteNote('${encodeURIComponent(title)}')">삭제</button>
     </div>`;
}

async function suggestTags(t) {
  const { tags } = await api(`/api/page/${t}/suggest-tags`, { method: "POST" });
  alert("제안 태그: " + (tags || []).join(", "));
}
async function deleteNote(t) {
  if (!confirm("삭제할까요?")) return;
  await api(`/api/page/${t}`, { method: "DELETE" });
  location.hash = "#/notes";
}
async function openVersions(t) {
  const { versions } = await api(`/api/page/${t}/versions`);
  showModal(`<h2>버전 이력</h2>` + versions.map((v) =>
    `<div class="candidate">${v.created_at} · ${v.source}
       <button onclick="restoreVersion('${t}',${v.id})">되돌리기</button></div>`).join(""));
}
async function restoreVersion(t, vid) {
  await api(`/api/page/${t}/restore`, { method: "POST", body: JSON.stringify({ version_id: vid }) });
  closeModal(); route();
}

// ── 모달/드로어 ───────────────────────────────────────────────
function showModal(html) {
  $("#modal-root").innerHTML =
    `<div class="modal" onclick="if(event.target===this)closeModal()">
       <div class="modal-card">${html}
         <div style="margin-top:1rem"><button onclick="closeModal()">닫기</button></div>
       </div></div>`;
}
function closeModal() { $("#modal-root").innerHTML = ""; }

// ── 부트스트랩 ────────────────────────────────────────────────
async function boot() {
  const { user } = await api("/auth/me");
  $("#user-box").innerHTML = user
    ? `${user.name} · <a href="/auth/logout">로그아웃</a>`
    : `<a href="/login">로그인</a>`;
  $("#drawer-toggle").onclick = () => {
    $("#sidebar").classList.toggle("open");
    $("#scrim").hidden = !$("#sidebar").classList.contains("open");
  };
  $("#scrim").onclick = () => { $("#sidebar").classList.remove("open"); $("#scrim").hidden = true; };
  window.addEventListener("hashchange", route);
  route();
}
boot();
