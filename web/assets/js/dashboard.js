/* Dashboard */
"use strict";
App.pages.dashboard = {
  render(el) {
    el.innerHTML = `
    <div class="grid g-5" id="kpis"></div>
    <div class="grid" style="grid-template-columns:minmax(0,1.6fr) minmax(0,1fr);margin-top:18px">
      <div class="card"><div class="card-h"><h3>ปริมาณรถผ่านรายชั่วโมง</h3><span class="sub">วันนี้</span>
        <div class="right legend"><span><i style="background:var(--ok)"></i>อนุญาต</span><span><i style="background:var(--bad)"></i>ปฏิเสธ</span></div></div>
        <div class="card-b"><div class="chart" id="hourly"></div></div></div>
      <div class="card video-card"><div class="card-h"><h3>ภาพสดจากกล้อง</h3><div class="right"><a class="btn sm" href="#/monitor">${icon("maximize")}เปิด Live Monitor</a></div></div>
        <div class="card-b"><div class="video-wrap" style="border-radius:10px"><img class="live" id="dash-live" alt="">
          <div class="hud"><span class="tag"><span class="rec">● LIVE</span></span><span class="tag mono" id="dash-cam"></span></div></div></div></div>
    </div>
    <div class="grid" style="grid-template-columns:minmax(0,1.6fr) minmax(0,1fr);margin-top:18px">
      <div class="card"><div class="card-h"><h3>เหตุการณ์ล่าสุด</h3><span class="sub">อัปเดตอัตโนมัติแบบเรียลไทม์</span>
        <div class="right"><a class="btn sm" href="#/events">ดูทั้งหมด ${icon("chevron")}</a></div></div>
        <div class="card-b flush table-wrap"><table class="table"><thead><tr><th>ภาพป้าย</th><th>ทะเบียน</th><th>เจ้าของ / กลุ่ม</th><th>ผลลัพธ์</th><th>เวลา</th></tr></thead><tbody id="recent"></tbody></table></div></div>
      <div class="grid" style="align-content:start">
        <div class="card"><div class="card-h"><h3>สุขภาพระบบ</h3><span class="sub" id="uptime"></span></div><div class="card-b"><div class="health" id="health"></div></div></div>
        <div class="card"><div class="card-h"><h3>ทะเบียนรถตามกลุ่มสิทธิ์</h3><span class="sub" id="veh-total"></span></div><div class="card-b bars-h" id="groups"></div></div>
        <div class="card"><div class="card-h"><h3>แนวโน้ม 7 วัน</h3></div><div class="card-b"><div class="chart" id="week"></div></div></div>
      </div>
    </div>`;
    $("#dash-live").src = media("/api/live.mjpg?width=960&fps=10");
    let data = null;
    const load = async () => {
      try { data = await api("/api/dashboard"); paint(); } catch (e) { toast(e.message, "bad"); }
    };
    const paint = () => {
      const t = data.today;
      const rate = t.total ? t.granted / t.total : 0;
      const kpi = (ic, cls, kc, label, val, foot) => `<div class="card kpi" style="--kc:${kc}"><div class="k-top"><div class="k-ico ${cls}">${icon(ic)}</div>${label}</div>
        <div class="k-val">${val}</div><div class="k-foot">${foot}</div></div>`;
      $("#kpis").innerHTML =
        kpi("car", "ico-blue", "var(--info)", "รถผ่านวันนี้", fmtNum(t.total), `ทะเบียนในระบบ ${fmtNum(data.vehicles)} คัน`) +
        kpi("check", "ico-green", "var(--ok)", "อนุญาตเข้า-ออก", fmtNum(t.granted), `อัตราผ่าน ${pct(rate)}`) +
        kpi("ban", "ico-red", "var(--bad)", "ปฏิเสธ", fmtNum(t.denied), `บัญชีดำ ${fmtNum(t.blocked)} ครั้ง`) +
        kpi("alert", "ico-amber", "var(--warn)", "รถไม่ลงทะเบียน", fmtNum(t.unknown), "ไม่พบในฐานข้อมูล") +
        kpi("target", "ico-cyan", "#5cb8d6", "ความแม่นยำเฉลี่ย", pct(data.avg_conf), `ประมวลผลเฉลี่ย ${fmtNum(data.avg_latency)} ms`);
      drawHourly($("#hourly"), data.hourly);
      drawWeek($("#week"), data.days);
      $("#recent").innerHTML = data.recent.length ? data.recent.map(rowHTML).join("") : `<tr><td colspan="5">${emptyHTML("ยังไม่มีเหตุการณ์", "car")}</td></tr>`;
      bindRows();
      const max = Math.max(1, ...data.groups.map((g) => g.n));
      $("#groups").innerHTML = data.groups.map((g) => `<div class="bh"><span class="group-tag"><i style="background:${esc(g.color)}"></i>${esc(g.name)}</span>
        <div class="track"><i style="width:${(g.n / max) * 100}%;background:${esc(g.color)}"></i></div><b class="mono" style="text-align:right">${g.n}</b></div>`).join("");
      $("#veh-total").textContent = `รวม ${fmtNum(data.vehicles)} คัน`;
    };
    const rowHTML = (e) => `<tr class="clickable" data-id="${e.id}"><td>${e.plate_url ? `<img class="thumb" src="${media(e.plate_url)}" loading="lazy">` : `<div class="thumb"></div>`}</td>
      <td>${plateHTML(e.plate, e.province, "xs")}</td>
      <td><div>${esc(e.owner || "—")}</div><div style="margin-top:2px">${groupTag(e.group_name, e.group_color)}</div></td>
      <td>${decisionBadge(e.decision)}<div class="muted" style="font-size:11.5px;margin-top:3px;max-width:220px">${esc(e.reason)}</div></td>
      <td class="mono" style="font-size:12.5px">${fmtTime(e.ts)}<div class="muted" style="font-size:11px">${SOURCE_TH[e.source] || e.source}</div></td></tr>`;
    const bindRows = () => $$("#recent tr[data-id]").forEach((tr) => (tr.onclick = () => {
      const ev = data.recent.find((x) => String(x.id) === tr.dataset.id);
      if (ev) App.showEvent(ev);
    }));
    const healthPaint = (s) => {
      if (!s || !$("#health")) return;
      const h = (ic, label, val, dot) => `<div class="h-item"><div class="h-label">${dot ? `<span class="dot ${dot}"></span>` : icon(ic)}${label}</div><div class="h-val">${val}</div></div>`;
      $("#health").innerHTML =
        h("", "สัญญาณวิดีโอ", s.video.connected ? `${s.video.width}×${s.video.height}` : "ขาดหาย", s.video.connected ? "ok" : "bad") +
        h("", "AI Engine", s.engine.status === "ready" ? `${s.engine.proc_fps} fps` : s.engine.status, s.engine.status === "ready" ? "ok" : "warn") +
        h("", "ตรวจจับป้าย", `${s.engine.det_ms} ms`, "ok") +
        h("", "อ่านตัวอักษร (OCR)", `${s.engine.ocr_ms} ms`, "ok") +
        h("", "Alarm I/O", s.io.online ? "ออนไลน์" : "ออฟไลน์", s.io.online ? "ok" : "bad") +
        h("", "โหมดทริกเกอร์", { line: "เส้นเสมือน", video: "วิดีโอ", input: "Loop" }[s.recognition.mode] || "-", s.recognition.armed ? "ok" : "warn");
      const u = s.uptime;
      $("#uptime").textContent = `Uptime ${Math.floor(u / 3600)}h ${Math.floor((u % 3600) / 60)}m`;
      $("#dash-cam").textContent = `${s.device.name} · ${s.video.fps} fps`;
    };
    healthPaint(App.status);
    load();
    const offs = [
      Bus.on("status", healthPaint),
      Bus.on("event", debounce(load, 800)),
    ];
    const timer = setInterval(load, 60000);
    return () => { offs.forEach((f) => f()); clearInterval(timer); $("#dash-live") && ($("#dash-live").src = ""); };
  },
};

/* ------------------------------------------------------------------ charts */
function drawHourly(el, hours) {
  const W = 760, H = 230, L = 34, B = 24, T = 10;
  const max = Math.max(4, ...hours.map((h) => h.n));
  const nice = Math.ceil(max / 4) * 4;
  const bw = (W - L) / 24;
  const y = (v) => T + (H - T - B) * (1 - v / nice);
  let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="กราฟจำนวนรถรายชั่วโมง">`;
  for (let i = 0; i <= 4; i++) {
    const v = (nice / 4) * i;
    s += `<line class="gridline" x1="${L}" x2="${W}" y1="${y(v)}" y2="${y(v)}"/><text class="axis" x="${L - 8}" y="${y(v) + 3}" text-anchor="end">${v}</text>`;
  }
  const nowH = new Date().getHours();
  hours.forEach((h, i) => {
    const x = L + i * bw + 3, w = bw - 6;
    const gH = (H - T - B) * (h.g / nice), dH = (H - T - B) * (h.d / nice);
    const base = H - B;
    if (h.g) s += `<path d="${roundTop(x, base - gH, w, gH, h.d ? 0 : 4)}" fill="var(--ok)" opacity="${i === nowH ? 1 : .85}"/>`;
    if (h.d) s += `<path d="${roundTop(x, base - gH - dH - (h.g ? 2 : 0), w, dH, 4)}" fill="var(--bad)" opacity="${i === nowH ? 1 : .85}"/>`;
    if (i % 3 === 0 || i === 23) s += `<text class="axis" x="${x + w / 2}" y="${H - 6}" text-anchor="middle">${pad(i)}</text>`;
    s += `<rect class="hit" data-i="${i}" x="${L + i * bw}" y="${T}" width="${bw}" height="${H - T - B}"/>`;
  });
  s += `<line x1="${L}" x2="${W}" y1="${H - B}" y2="${H - B}" stroke="rgba(148,163,184,.25)"/></svg>`;
  el.innerHTML = s;
  $$(".hit", el).forEach((r) => {
    r.addEventListener("mousemove", (e) => {
      const h = hours[r.dataset.i];
      tooltip(e, `<div class="tt-h">${pad(h.h)}:00 – ${pad(h.h)}:59</div><div class="tt-r">ทั้งหมด <b>${h.n}</b></div>
        <div class="tt-r"><span><i style="display:inline-block;width:8px;height:8px;border-radius:2px;background:var(--ok);margin-right:6px"></i>อนุญาต</span><b>${h.g}</b></div>
        <div class="tt-r"><span><i style="display:inline-block;width:8px;height:8px;border-radius:2px;background:var(--bad);margin-right:6px"></i>ปฏิเสธ</span><b>${h.d}</b></div>`);
    });
    r.addEventListener("mouseleave", () => tooltip(null, null));
  });
}

function roundTop(x, y, w, h, r) {
  if (h <= 0) return "";
  r = Math.min(r, w / 2, h);
  return `M${x},${y + h} L${x},${y + r} Q${x},${y} ${x + r},${y} L${x + w - r},${y} Q${x + w},${y} ${x + w},${y + r} L${x + w},${y + h} Z`;
}

function drawWeek(el, days) {
  const W = 360, H = 120, B = 20, T = 14;
  const max = Math.max(1, ...days.map((d) => d.n));
  const bw = W / days.length;
  let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="แนวโน้ม 7 วัน">`;
  days.forEach((d, i) => {
    const h = (H - B - T) * (d.n / max);
    const x = i * bw + 8, w = bw - 16;
    const label = d.date.slice(8, 10) + "/" + d.date.slice(5, 7);
    s += `<path d="${roundTop(x, H - B - h, w, h, 2)}" fill="var(--series)" opacity="${i === days.length - 1 ? 1 : .6}"/>`;
    if (d.n) s += `<text class="axis" x="${x + w / 2}" y="${H - B - h - 4}" text-anchor="middle" style="fill:var(--text-2)">${d.n}</text>`;
    s += `<text class="axis" x="${x + w / 2}" y="${H - 5}" text-anchor="middle">${label}</text>`;
    s += `<rect class="hit" data-i="${i}" x="${i * bw}" y="0" width="${bw}" height="${H - B}"/>`;
  });
  s += `</svg>`;
  el.innerHTML = s;
  $$(".hit", el).forEach((r) => {
    r.addEventListener("mousemove", (e) => { const d = days[r.dataset.i]; tooltip(e, `<div class="tt-h">${fmtDate(d.date).slice(0, 10)}</div><div class="tt-r">รถผ่าน <b>${d.n}</b></div><div class="tt-r">อนุญาต <b>${d.g}</b></div>`); });
    r.addEventListener("mouseleave", () => tooltip(null, null));
  });
}
