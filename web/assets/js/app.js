/* AI-LPR shell: login, module bar, global status */
"use strict";
const NAV = [
  { id: "dashboard", label: "หน้าหลัก", icon: "dashboard" },
  { id: "monitor", label: "Monitoring", icon: "monitor" },
  { id: "events", label: "ค้นหาเหตุการณ์", icon: "search", badge: true },
  { id: "vehicles", label: "ทะเบียนรถ", icon: "car" },
  { id: "groups", label: "สิทธิ์การเข้า-ออก", icon: "shield" },
  { id: "device", label: "กล้อง & I/O", icon: "cpu" },
  { id: "training", label: "AI Training", icon: "brain", role: "operator" },
  { id: "test", label: "ทดสอบระบบ", icon: "flask", role: "operator" },
  { id: "users", label: "ผู้ใช้งาน", icon: "users", role: "admin" },
  { id: "settings", label: "ตั้งค่า", icon: "settings" },
];
let currentCleanup = null;
let unseenDenied = 0;

/* ------------------------------------------------------------------ login */
function renderLogin() {
  Bus.close();
  $("#root").innerHTML = `
  <div class="login">
    <div class="login-top"><div class="brand" style="border:0;padding:0"><div class="brand-mark">${icon("logo")}</div>
      <div><div class="brand-name">AI-LPR</div><div class="brand-sub">Entrance & Exit Control</div></div></div></div>
    <div class="login-body">
      <form class="login-card" id="login-form" autocomplete="on">
        <h2>เข้าสู่ระบบ</h2>
        <div class="sub">ระบบรู้จำป้ายทะเบียนและควบคุมการเข้า-ออก</div>
        <div class="field"><label>ชื่อผู้ใช้</label><div class="input-icon">${icon("user")}<input class="input" name="username" autocomplete="username" required autofocus></div></div>
        <div class="field"><label>รหัสผ่าน</label><div class="input-icon">${icon("key")}<input class="input" type="password" name="password" autocomplete="current-password" required></div></div>
        <div class="login-err" id="login-err"></div>
        <button class="btn primary lg block" type="submit" data-busy="กำลังตรวจสอบ...">เข้าสู่ระบบ</button>
      </form>
    </div>
    <div class="login-foot">AI-LPR v1.2 · บัญชีเริ่มต้น admin / admin1234 — กรุณาเปลี่ยนรหัสผ่านหลังเข้าใช้งานครั้งแรก</div>
  </div>`;
  $("#login-form").onsubmit = async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    const btn = $("button[type=submit]", e.target);
    $("#login-err").textContent = "";
    try {
      const res = await busy(btn, () => api("/api/auth/login", { body: { username: f.get("username"), password: f.get("password") } }));
      App.token = res.token;
      App.user = res.user;
      localStorage.setItem("lpr_token", res.token);
      boot();
    } catch (err) { $("#login-err").textContent = err.message; }
  };
}

function logout(expired = false) {
  if (App.token && !expired) api("/api/auth/logout", { method: "POST" }).catch(() => {});
  App.token = "";
  App.user = null;
  localStorage.removeItem("lpr_token");
  if (currentCleanup) { currentCleanup(); currentCleanup = null; }
  renderLogin();
}

/* ------------------------------------------------------------------ shell */
function renderShell() {
  const mods = NAV.filter((n) => !n.role || can(n.role)).map((n) =>
    `<a href="#/${n.id}" data-nav="${n.id}" title="${n.label}">${icon(n.icon)}<span>${n.label}</span>${n.badge ? `<b class="badge-count hidden" id="nav-badge"></b>` : ""}</a>`).join("");
  $("#root").innerHTML = `
  <div class="shell">
    <header class="topbar">
      <div class="brand"><div class="brand-mark">${icon("logo")}</div><div><div class="brand-name">AI-LPR</div><div class="brand-sub" id="sb-cam">Entrance & Exit</div></div></div>
      <nav class="modules">${mods}</nav>
      <div class="top-right">
        <div class="status-chips">
          <span class="chip" title="สัญญาณวิดีโอ"><span class="dot" id="st-video"></span><span id="st-video-t">Video</span></span>
          <span class="chip" title="AI Engine"><span class="dot" id="st-ai"></span><span id="st-ai-t">AI</span></span>
          <span class="chip" title="Alarm I/O"><span class="dot" id="st-io"></span><span>I/O</span></span>
        </div>
        <div class="clock" id="clock"></div>
        <div style="position:relative">
          <button class="user-btn" id="user-btn"><span class="avatar">${esc((App.user.full_name || App.user.username)[0].toUpperCase())}</span>
            <span style="text-align:left"><div class="u-name">${esc(App.user.username)}</div><div class="u-role">${ROLE_TH[App.user.role]}</div></span></button>
        </div>
      </div>
    </header>
    <main class="main"><section class="content" id="content"></section></main>
  </div>`;
  $("#user-btn").onclick = (e) => {
    e.stopPropagation();
    if ($("#user-menu")) return $("#user-menu").remove();
    const m = document.createElement("div");
    m.className = "menu";
    m.id = "user-menu";
    m.innerHTML = `<button data-a="pw">${icon("key")}เปลี่ยนรหัสผ่าน</button><button data-a="out">${icon("logout")}ออกจากระบบ</button>`;
    $("#user-btn").parentElement.appendChild(m);
    $("[data-a=pw]", m).onclick = () => { m.remove(); changePasswordDialog(); };
    $("[data-a=out]", m).onclick = () => logout();
    setTimeout(() => document.addEventListener("click", () => m.remove(), { once: true }));
  };
  tickClock();
}

function tickClock() {
  const el = $("#clock");
  if (!el) return;
  const d = new Date();
  el.textContent = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  el.title = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}
setInterval(tickClock, 1000);

function setDot(id, state) {
  const el = document.getElementById(id);
  if (el) el.className = `dot ${state}`;
}

function applyStatus(s) {
  App.status = s;
  const cams = (s.cameras || []).filter((c) => c.enabled);
  const online = cams.filter((c) => c.video.connected).length;
  setDot("st-video", !cams.length ? "bad" : online === cams.length ? "ok" : online ? "warn" : "bad");
  const vt = $("#st-video-t");
  if (vt) vt.textContent = cams.length > 1 ? `กล้อง ${online}/${cams.length}` : online ? `${cams[0].video.fps} fps` : "No video";
  const ai = s.engine.status;
  setDot("st-ai", ai === "ready" ? "ok" : ai === "loading" ? "warn pulse" : "bad");
  const at = $("#st-ai-t");
  if (at) at.textContent = s.train?.state === "training" ? `AI เทรน ${Math.round(s.train.progress * 100)}%`
    : ai === "ready" ? `AI ${s.engine.proc_fps} fps` : ai === "loading" ? "AI loading" : "AI error";
  const ioBad = cams.filter((c) => c.host && !c.io.online).length, ioWarn = cams.some((c) => c.io.conflicts);
  setDot("st-io", ioBad ? "bad" : ioWarn ? "warn" : "ok");
  if ($("#sb-cam")) $("#sb-cam").textContent = cams.length > 1 ? `${cams.length} ช่องทาง` : (cams[0]?.name || "Entrance & Exit");
}

function changePasswordDialog() {
  modal({
    title: "เปลี่ยนรหัสผ่าน", icon: "key",
    body: `<div class="form-grid"><div class="field full"><label>รหัสผ่านเดิม</label><input class="input" type="password" id="pw-old"></div>
      <div class="field"><label>รหัสผ่านใหม่</label><input class="input" type="password" id="pw-new"></div>
      <div class="field"><label>ยืนยันรหัสผ่านใหม่</label><input class="input" type="password" id="pw-new2"></div></div>`,
    footer: `<button class="btn" data-close>ยกเลิก</button><button class="btn primary" id="pw-save">บันทึก</button>`,
    onMount: (el, close) => {
      $("#pw-save", el).onclick = async () => {
        if ($("#pw-new", el).value !== $("#pw-new2", el).value) return toast("รหัสผ่านใหม่ไม่ตรงกัน", "bad");
        try {
          await api("/api/auth/password", { body: { old: $("#pw-old", el).value, new: $("#pw-new", el).value } });
          toast("เปลี่ยนรหัสผ่านเรียบร้อย");
          close();
        } catch (e) { toast(e.message, "bad"); }
      };
    },
  });
}

/* ------------------------------------------------------------------ alerts */
function onEventGlobal(ev) {
  if (ev.decision === "denied" && location.hash !== "#/events") {
    unseenDenied++;
    const b = $("#nav-badge");
    if (b) { b.textContent = unseenDenied; b.classList.remove("hidden"); }
  }
  if (ev.list_type === "deny") {
    $(".alert-pop")?.remove();
    const el = document.createElement("div");
    el.className = "alert-pop";
    el.innerHTML = `<span style="color:var(--bad)">${icon("alert")}</span>
      <div><div class="ap-title">ตรวจพบรถในบัญชีดำ (Blocklist)</div><div class="muted" style="font-size:12px">${esc(ev.ts)} · ${esc(ev.camera || "")}</div></div>
      ${plateHTML(ev.plate, ev.province, "sm")}<button class="btn sm ghost icon">${icon("x")}</button>`;
    document.body.appendChild(el);
    el.querySelector("button").onclick = () => el.remove();
    setTimeout(() => el.remove(), 12000);
  }
}

/* ------------------------------------------------------------------ router */
function route() {
  const id = (location.hash.replace("#/", "") || "dashboard").split("?")[0];
  const page = App.pages[id] || App.pages.dashboard;
  const nav = NAV.find((n) => n.id === id) || NAV[0];
  if (nav.role && !can(nav.role)) { location.hash = "#/dashboard"; return; }
  if (currentCleanup) { try { currentCleanup(); } catch (e) { console.error(e); } currentCleanup = null; }
  $$(".modules a").forEach((a) => a.classList.toggle("active", a.dataset.nav === id));
  document.title = `${nav.label} · AI-LPR`;
  if (id === "events") { unseenDenied = 0; $("#nav-badge")?.classList.add("hidden"); }
  const el = $("#content");
  el.innerHTML = `<div class="page"></div>`;
  el.scrollTop = 0;
  try { currentCleanup = page.render(el.firstElementChild) || null; } catch (e) { console.error(e); el.innerHTML = emptyHTML(esc(e.message), "alert"); }
}

async function boot() {
  if (!App.token) return renderLogin();
  try { App.user = await api("/api/auth/me"); } catch { return renderLogin(); }
  renderShell();
  Bus.handlers = {};
  Bus.on("status", applyStatus);
  Bus.on("ws", (ok) => { if (!ok) setDot("st-video", "bad"); });
  Bus.on("event", onEventGlobal);
  Bus.on("io", (d) => (App.io[d.cam] = d));
  Bus.on("train", (t) => {
    if (t.state === "done" && t.result) toast(t.result.saved ? `เทรน AI เสร็จ: ${pct(t.result.base_acc)} → ${pct(t.result.acc)}${t.result.activated ? " (เปิดใช้งานแล้ว)" : ""}` : "เทรนเสร็จ — โมเดลใหม่ไม่ดีกว่าเดิม", "info", 6000);
    if (t.state === "error") toast(`เทรน AI ไม่สำเร็จ: ${t.message}`, "bad", 6000);
  });
  Bus.connect();
  try { App.settings = await api("/api/settings"); } catch {}
  try { await loadCams(); } catch {}
  loadGroups().catch(() => {});
  window.onhashchange = route;
  route();
}

boot();
