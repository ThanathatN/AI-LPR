/* AI-LPR core: API client, realtime bus, UI kit */
"use strict";
const App = { pages: {}, user: null, token: localStorage.getItem("lpr_token") || "", status: null, io: {}, settings: null, groups: [], cams: [] };

/* ------------------------------------------------------------------ helpers */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtNum = (n) => Number(n || 0).toLocaleString("th-TH");
const pct = (v) => `${Math.round((v || 0) * 100)}%`;
const pad = (n) => String(n).padStart(2, "0");
const fmtTime = (ts) => (ts || "").slice(11, 19);
const fmtDate = (ts) => {
  if (!ts) return "-";
  const [d, t] = ts.split(" ");
  const [y, m, dd] = d.split("-");
  return `${dd}/${m}/${Number(y) + 543} ${t ? t.slice(0, 5) : ""}`;
};
const media = (url) => (url ? `${url}${url.includes("?") ? "&" : "?"}token=${encodeURIComponent(App.token)}` : "");
const can = (role) => ({ viewer: 1, operator: 2, admin: 3 }[App.user?.role] || 0) >= ({ viewer: 1, operator: 2, admin: 3 }[role]);
const debounce = (fn, ms = 300) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
const ROLE_TH = { admin: "ผู้ดูแลระบบ", operator: "เจ้าหน้าที่", viewer: "ผู้ชม" };
const LIST_TH = { allow: "Allowlist", visitor: "Visitor", deny: "Blocklist", unknown: "ไม่ลงทะเบียน" };
const SOURCE_TH = { line: "เส้นทริกเกอร์", video: "วิดีโอต่อเนื่อง", input: "Loop Detector", manual: "สั่งด้วยมือ", test: "ทดสอบรูปภาพ", simulate: "จำลอง" };
const MODE_TH = { line: "เส้นเสมือน (Virtual line)", video: "วิดีโอต่อเนื่อง", input: "Loop Detector", mixed: "เส้นเสมือน + Loop" };
const LANE_TH = { entry: "ทางเข้า", exit: "ทางออก", both: "เข้า-ออก" };
const DIR_TH = { in: "เข้า", out: "ออก" };
const dirBadge = (d) => d ? `<span class="badge ${d === "in" ? "b-info" : "b-mute"}">${d === "in" ? "↓" : "↑"} ${DIR_TH[d]}</span>` : `<span class="muted">—</span>`;
const DAYS_TH = ["จ", "อ", "พ", "พฤ", "ศ", "ส", "อา"];
const VTYPE_TH = { car: "รถยนต์", pickup: "รถกระบะ", van: "รถตู้", truck: "รถบรรทุก", motorcycle: "จักรยานยนต์", bus: "รถบัส" };
const PROVINCES = ["กรุงเทพมหานคร","กระบี่","กาญจนบุรี","กาฬสินธุ์","กำแพงเพชร","ขอนแก่น","จันทบุรี","ฉะเชิงเทรา","ชลบุรี","ชัยนาท","ชัยภูมิ","ชุมพร","เชียงราย","เชียงใหม่","ตรัง","ตราด","ตาก","นครนายก","นครปฐม","นครพนม","นครราชสีมา","นครศรีธรรมราช","นครสวรรค์","นนทบุรี","นราธิวาส","น่าน","บึงกาฬ","บุรีรัมย์","ปทุมธานี","ประจวบคีรีขันธ์","ปราจีนบุรี","ปัตตานี","พระนครศรีอยุธยา","พะเยา","พังงา","พัทลุง","พิจิตร","พิษณุโลก","เพชรบุรี","เพชรบูรณ์","แพร่","ภูเก็ต","มหาสารคาม","มุกดาหาร","แม่ฮ่องสอน","ยโสธร","ยะลา","ร้อยเอ็ด","ระนอง","ระยอง","ราชบุรี","ลพบุรี","ลำปาง","ลำพูน","เลย","ศรีสะเกษ","สกลนคร","สงขลา","สตูล","สมุทรปราการ","สมุทรสงคราม","สมุทรสาคร","สระแก้ว","สระบุรี","สิงห์บุรี","สุโขทัย","สุพรรณบุรี","สุราษฎร์ธานี","สุรินทร์","หนองคาย","หนองบัวลำภู","อ่างทอง","อำนาจเจริญ","อุดรธานี","อุตรดิตถ์","อุทัยธานี","อุบลราชธานี","เบตง"];

/* ------------------------------------------------------------------ icons (stroke, 24px grid) */
const ICONS = {
  logo: '<rect x="2.5" y="6.5" width="19" height="11" rx="2.5"/><path d="M6.5 12h3M11.5 12h6"/><path d="M2.5 3.5v2M21.5 3.5v2M2.5 18.5v2M21.5 18.5v2"/>',
  dashboard: '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>',
  monitor: '<path d="M15 10l4.55-2.28A1 1 0 0 1 21 8.62v6.76a1 1 0 0 1-1.45.9L15 14"/><rect x="3" y="6" width="12" height="12" rx="2"/>',
  car: '<path d="M5 17h14M5 17a2 2 0 1 0 4 0M15 17a2 2 0 1 0 4 0"/><path d="M3 17v-4.5l2.2-5A2 2 0 0 1 7 6.3h10a2 2 0 0 1 1.8 1.2l2.2 5V17"/><path d="M3.5 12.5h17"/>',
  shield: '<path d="M12 3l8 3v6c0 4.5-3.4 8.3-8 9-4.6-.7-8-4.5-8-9V6z"/><path d="M9 12l2 2 4-4"/>',
  list: '<path d="M8 6h13M8 12h13M8 18h13"/><circle cx="3.5" cy="6" r="1"/><circle cx="3.5" cy="12" r="1"/><circle cx="3.5" cy="18" r="1"/>',
  cpu: '<rect x="5" y="5" width="14" height="14" rx="2"/><rect x="9" y="9" width="6" height="6" rx="1"/><path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3"/>',
  flask: '<path d="M9 3h6M10 3v6.5L4.6 18.2A1.9 1.9 0 0 0 6.2 21h11.6a1.9 1.9 0 0 0 1.6-2.8L14 9.5V3"/><path d="M7 15h10"/>',
  users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c.8-3.6 3.4-5.5 6.5-5.5s5.7 1.9 6.5 5.5"/><path d="M16 4.6a3.5 3.5 0 0 1 0 6.8M18 14.8c2 .7 3.2 2.4 3.6 5.2"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  gate: '<path d="M4 21V9M4 11h16M8 11l3-2M13 11l3-2M18 11l2-1.5"/><circle cx="4" cy="8" r="1.6"/><path d="M2 21h5"/>',
  scan: '<path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2"/><path d="M7 12h10"/>',
  check: '<path d="M20 6L9 17l-5-5"/>',
  x: '<path d="M18 6L6 18M6 6l12 12"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
  trash: '<path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/>',
  download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/>',
  upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12"/>',
  refresh: '<path d="M21 12a9 9 0 1 1-2.6-6.4L21 8"/><path d="M21 3v5h-5"/>',
  zap: '<path d="M13 2L3 14h9l-1 8 10-12h-9z"/>',
  bell: '<path d="M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9M13.7 21a2 2 0 0 1-3.4 0"/>',
  alert: '<path d="M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
  info: '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  calendar: '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>',
  logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/>',
  key: '<circle cx="7.5" cy="15.5" r="4.5"/><path d="M10.7 12.3L21 2M17 6l3 3M15 8l2 2"/>',
  camera: '<path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/>',
  image: '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/>',
  activity: '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
  target: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
  maximize: '<path d="M8 3H5a2 2 0 0 0-2 2v3M21 8V5a2 2 0 0 0-2-2h-3M3 16v3a2 2 0 0 0 2 2h3M16 21h3a2 2 0 0 0 2-2v-3"/>',
  power: '<path d="M18.4 6.6a9 9 0 1 1-12.8 0M12 2v10"/>',
  radar: '<path d="M19.1 4.9A10 10 0 1 0 22 12"/><path d="M16.2 7.8A6 6 0 1 0 18 12"/><circle cx="12" cy="12" r="2"/><path d="M12 12l7-7"/>',
  loop: '<rect x="3" y="8" width="18" height="8" rx="4"/><path d="M7 12h10"/>',
  file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/>',
  ban: '<circle cx="12" cy="12" r="9"/><path d="M5.6 5.6l12.8 12.8"/>',
  star: '<path d="M12 2l3.1 6.3 6.9 1-5 4.9 1.2 6.8L12 17.8 5.8 21l1.2-6.8-5-4.9 6.9-1z"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21c1-4 4-6 8-6s7 2 8 6"/>',
  link: '<path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/>',
  sliders: '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/>',
  database: '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.7-4 3-9 3s-9-1.3-9-3M3 5v14c0 1.7 4 3 9 3s9-1.3 9-3V5"/>',
  play: '<path d="M6 4l14 8-14 8z"/>',
  layers: '<path d="M12 2l10 5-10 5L2 7z"/><path d="M2 17l10 5 10-5M2 12l10 5 10-5"/>',
  chevron: '<path d="M9 18l6-6-6-6"/>',
  eye: '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8S1 12 1 12z"/><circle cx="12" cy="12" r="3"/>',
  brain: '<path d="M9 3.5a3 3 0 0 0-3 3 3 3 0 0 0-2 5.2A3.2 3.2 0 0 0 6.5 17 3 3 0 0 0 12 18.5V5a2.5 2.5 0 0 0-3-1.5z"/><path d="M15 3.5a3 3 0 0 1 3 3 3 3 0 0 1 2 5.2 3.2 3.2 0 0 1-2.5 5.3A3 3 0 0 1 12 18.5"/><path d="M8.5 9.5h2M13.5 13h2"/>',
  grid: '<rect x="3" y="3" width="8" height="8" rx="1"/><rect x="13" y="3" width="8" height="8" rx="1"/><rect x="3" y="13" width="8" height="8" rx="1"/><rect x="13" y="13" width="8" height="8" rx="1"/>',
  square: '<rect x="3" y="3" width="18" height="18" rx="2"/>',
  polygon: '<path d="M4 7l7-4 9 5-2 11-12 2z"/><circle cx="4" cy="7" r="1.6"/><circle cx="11" cy="3" r="1.6"/><circle cx="20" cy="8" r="1.6"/><circle cx="18" cy="19" r="1.6"/><circle cx="6" cy="21" r="1.6"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
};
const icon = (name, cls = "") => `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${ICONS[name] || ""}</svg>`;

/* ------------------------------------------------------------------ API */
async function api(path, opts = {}) {
  const headers = { Authorization: `Bearer ${App.token}`, ...(opts.headers || {}) };
  let body = opts.body;
  if (body && !(body instanceof FormData) && typeof body !== "string") {
    body = JSON.stringify(body);
    headers["Content-Type"] = "application/json";
  }
  const res = await fetch(path, { method: opts.method || (body ? "POST" : "GET"), headers, body });
  if (res.status === 401 && !path.includes("/auth/login")) { logout(true); throw new Error("หมดเวลาการใช้งาน กรุณาเข้าสู่ระบบใหม่"); }
  const ct = res.headers.get("content-type") || "";
  const data = ct.includes("json") ? await res.json() : await res.text();
  if (!res.ok) throw new Error((data && data.detail) || `HTTP ${res.status}`);
  return data;
}
async function download(path, filename) {
  const res = await fetch(path, { headers: { Authorization: `Bearer ${App.token}` } });
  if (!res.ok) return toast("ดาวน์โหลดไม่สำเร็จ", "bad");
  const blob = await res.blob();
  const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: filename });
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

/* ------------------------------------------------------------------ realtime bus */
const Bus = {
  handlers: {}, ws: null, retry: 1000,
  on(type, fn) { (this.handlers[type] ||= new Set()).add(fn); return () => this.handlers[type].delete(fn); },
  emit(type, data) { (this.handlers[type] || []).forEach((fn) => { try { fn(data); } catch (e) { console.error(e); } }); },
  connect() {
    if (!App.token) return;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(App.token)}`);
    this.ws = ws;
    ws.onopen = () => { this.retry = 1000; this.emit("ws", true); };
    ws.onmessage = (m) => { const msg = JSON.parse(m.data); this.emit(msg.type, msg.data); };
    ws.onclose = (e) => {
      this.emit("ws", false);
      if (e.code === 4401) return logout(true);
      if (App.token) setTimeout(() => this.connect(), this.retry = Math.min(this.retry * 1.6, 10000));
    };
  },
  close() { if (this.ws) { this.ws.onclose = null; this.ws.close(); } },
};

/* ------------------------------------------------------------------ UI kit */
function toast(msg, kind = "ok", ms = 3200) {
  const ic = { ok: "check", bad: "alert", info: "info" }[kind] || "info";
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.innerHTML = `${icon(ic)}<div>${esc(msg)}</div>`;
  $("#toasts").appendChild(el);
  setTimeout(() => { el.style.opacity = "0"; el.style.transition = "opacity .3s"; setTimeout(() => el.remove(), 300); }, ms);
}

function modal({ title, body, footer = "", wide = false, icon: ic = "", onMount }) {
  const ov = document.createElement("div");
  ov.className = "overlay";
  ov.innerHTML = `<div class="modal ${wide ? "wide" : ""}">
    <div class="modal-h">${ic ? `<span class="k-ico">${icon(ic)}</span>` : ""}<h3>${title}</h3>
      <button class="btn ghost icon x" data-close>${icon("x")}</button></div>
    <div class="modal-b">${body}</div>${footer ? `<div class="modal-f">${footer}</div>` : ""}</div>`;
  const close = () => { ov.remove(); document.removeEventListener("keydown", onKey); };
  const onKey = (e) => e.key === "Escape" && close();
  document.addEventListener("keydown", onKey);
  ov.addEventListener("mousedown", (e) => { if (e.target === ov) close(); });
  $$("[data-close]", ov).forEach((b) => b.addEventListener("click", close));
  document.body.appendChild(ov);
  onMount && onMount(ov, close);
  return { el: ov, close };
}

function drawer({ title, body, onMount }) {
  const ov = document.createElement("div");
  ov.className = "overlay drawer-overlay";
  ov.innerHTML = `<div class="drawer"><div class="modal-h"><h3>${title}</h3><button class="btn ghost icon x" data-close>${icon("x")}</button></div>
    <div class="modal-b" style="flex:1">${body}</div></div>`;
  const close = () => ov.remove();
  ov.addEventListener("mousedown", (e) => { if (e.target === ov) close(); });
  $$("[data-close]", ov).forEach((b) => b.addEventListener("click", close));
  document.body.appendChild(ov);
  onMount && onMount(ov, close);
  return { el: ov, close };
}

function confirmBox(title, text, okLabel = "ยืนยัน", danger = true) {
  return new Promise((resolve) => {
    const m = modal({
      title, icon: danger ? "alert" : "info",
      body: `<div class="text-2">${text}</div>`,
      footer: `<button class="btn" data-close>ยกเลิก</button><button class="btn ${danger ? "danger" : "primary"}" id="cb-ok">${okLabel}</button>`,
      onMount: (el, close) => {
        $("#cb-ok", el).onclick = () => { close(); resolve(true); };
        $$("[data-close]", el).forEach((b) => b.addEventListener("click", () => resolve(false)));
      },
    });
    return m;
  });
}

function lightbox(src) {
  const ov = document.createElement("div");
  ov.className = "overlay";
  ov.innerHTML = `<img class="lightbox" src="${src}">`;
  ov.onclick = () => ov.remove();
  document.body.appendChild(ov);
}

async function busy(btn, fn) {
  const html = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = `<span class="spin"></span>${btn.dataset.busy || "กำลังดำเนินการ..."}`;
  try { return await fn(); } finally { btn.disabled = false; btn.innerHTML = html; }
}

const plateHTML = (text, province, size = "") => {
  if (!text) return `<div class="plate empty-plate ${size}"><div class="p-reg">— —</div><div class="p-prov">ไม่มีข้อมูล</div></div>`;
  return `<div class="plate ${size}"><div class="p-reg">${esc(text)}</div><div class="p-prov">${esc(province || " ")}</div></div>`;
};
const decisionBadge = (d) => d === "granted"
  ? `<span class="badge b-ok">${icon("check")}อนุญาต</span>` : `<span class="badge b-bad">${icon("x")}ปฏิเสธ</span>`;
const listBadge = (t) => ({
  allow: `<span class="badge b-ok">Allowlist</span>`, visitor: `<span class="badge b-info">Visitor</span>`,
  deny: `<span class="badge b-bad">Blocklist</span>`,
}[t] || `<span class="badge b-mute">ไม่ลงทะเบียน</span>`);
const groupTag = (name, color) => name ? `<span class="group-tag"><i style="background:${esc(color || "#64748b")}"></i>${esc(name)}</span>` : `<span class="muted">—</span>`;
const switchHTML = (id, checked, label = "") => `<label class="switch"><input type="checkbox" id="${id}" ${checked ? "checked" : ""}><span class="track"></span>${label ? `<span>${label}</span>` : ""}</label>`;
const emptyHTML = (text, ic = "search") => `<div class="empty">${icon(ic)}<div>${text}</div></div>`;
const confBar = (v) => `<div style="display:flex;align-items:center;gap:8px"><div class="conf-bar" style="flex:1;min-width:50px"><i style="width:${Math.round((v || 0) * 100)}%"></i></div><span class="mono" style="font-size:12px">${pct(v)}</span></div>`;

function pager(total, page, size, onGo) {
  const pages = Math.max(1, Math.ceil(total / size));
  const wrap = document.createElement("div");
  wrap.className = "pager";
  wrap.innerHTML = `<span>ทั้งหมด <b class="mono" style="color:var(--text)">${fmtNum(total)}</b> รายการ</span><span class="spacer"></span>
    <button class="btn sm" data-p="${page - 1}" ${page <= 1 ? "disabled" : ""}>ก่อนหน้า</button>
    <span class="mono">${page} / ${pages}</span>
    <button class="btn sm" data-p="${page + 1}" ${page >= pages ? "disabled" : ""}>ถัดไป</button>`;
  $$("[data-p]", wrap).forEach((b) => (b.onclick = () => onGo(Number(b.dataset.p))));
  return wrap;
}

function segBind(root, onChange) {
  $$("button", root).forEach((b) => b.addEventListener("click", () => {
    $$("button", root).forEach((x) => x.classList.toggle("on", x === b));
    onChange && onChange(b.dataset.v);
  }));
}
const segVal = (root) => $("button.on", root)?.dataset.v;

let _tt;
function tooltip(e, html) {
  if (!_tt) { _tt = document.createElement("div"); _tt.className = "tooltip"; document.body.appendChild(_tt); }
  if (!html) { _tt.style.opacity = "0"; return; }
  _tt.innerHTML = html;
  _tt.style.opacity = "1";
  const r = _tt.getBoundingClientRect();
  let x = e.clientX + 14, y = e.clientY - r.height - 10;
  if (x + r.width > innerWidth - 10) x = e.clientX - r.width - 14;
  if (y < 10) y = e.clientY + 16;
  _tt.style.left = `${x}px`;
  _tt.style.top = `${y}px`;
}

async function loadGroups() { App.groups = await api("/api/groups"); return App.groups; }
async function loadCams() { App.cams = await api("/api/cameras"); return App.cams; }
const camName = (id) => (App.cams.find((c) => c.id === id) || {}).name || (id ? `กล้อง #${id}` : "—");
const camStatus = (id) => (App.status?.cameras || []).find((c) => c.id === id);

/* camera picker (select element) — remembers the last camera per page */
function camSelect(id, selected, { all = false, cls = "" } = {}) {
  return `<select class="input ${cls}" id="${id}" style="width:auto;min-width:180px">${all ? `<option value="0">ทุกกล้อง</option>` : ""}
    ${App.cams.map((c) => `<option value="${c.id}" ${c.id === +selected ? "selected" : ""}>${esc(c.name)}${c.enabled ? "" : " (ปิด)"}</option>`).join("")}</select>`;
}
const camPref = {
  get(page) {
    let v = 0;
    try { v = +localStorage.getItem(`lpr_cam_${page}`); } catch {}
    return App.cams.some((c) => c.id === v) ? v : App.cams[0]?.id || 0;
  },
  set(page, id) { try { localStorage.setItem(`lpr_cam_${page}`, id); } catch {} },
};
