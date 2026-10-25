/* Access groups / permissions */
"use strict";
const GROUP_COLORS = ["#22c55e", "#3b82f6", "#eab308", "#a855f7", "#06b6d4", "#f97316", "#ec4899", "#ef4444", "#64748b"];
const TYPE_META = {
  allow: { label: "Allowlist", th: "อนุญาตเข้า-ออก", icon: "check", cls: "ico-green" },
  visitor: { label: "Visitor", th: "ผู้มาติดต่อ (จำกัดเวลา)", icon: "user", cls: "ico-cyan" },
  deny: { label: "Blocklist", th: "บัญชีดำ ห้ามเข้า", icon: "ban", cls: "ico-red" },
};

App.pages.groups = {
  render(el) {
    el.innerHTML = `
    <div class="section-title"><div><h2>กลุ่ม & สิทธิ์การเข้า-ออก</h2><p>กำหนดประเภทรายการ ช่วงเวลา และช่อง Alarm Output ที่จะสั่งเปิดไม้กั้นของแต่ละกลุ่ม</p></div>
      <div class="right">${can("admin") ? `<button class="btn primary" id="g-add">${icon("plus")}สร้างกลุ่มสิทธิ์</button>` : ""}</div></div>
    <div class="grid g-3" id="g-list"></div>
    <div class="card" style="margin-top:18px">
      <div class="card-h"><h3>ตรวจสอบสิทธิ์ทะเบียน</h3><span class="sub">จำลองการตัดสินสิทธิ์ ณ เวลาปัจจุบันโดยไม่สั่งงานอุปกรณ์</span></div>
      <div class="card-b"><div class="toolbar">
        <input class="input mono" id="chk-plate" placeholder="เช่น 1กข 1234" style="width:220px;font-size:15px">
        <input class="input" id="chk-prov" list="prov-list2" placeholder="จังหวัด (ไม่บังคับ)" style="width:220px"><datalist id="prov-list2">${PROVINCES.map((p) => `<option value="${p}">`).join("")}</datalist>
        <button class="btn primary" id="chk-go">${icon("shield")}ตรวจสอบ</button></div>
        <div id="chk-res" style="margin-top:16px"></div></div>
    </div>`;
    const load = async () => {
      const groups = await loadGroups();
      $("#g-list").innerHTML = groups.map((g) => {
        const m = TYPE_META[g.type];
        const s = g.schedule || {};
        const days = DAYS_TH.map((d, i) => `<span class="${(s.days || []).includes(i) ? "on" : ""}">${d}</span>`).join("");
        const outs = g.outputs?.length ? g.outputs.map((o) => `OUT${o}`).join(", ") : "ช่องไม้กั้นหลัก";
        return `<div class="card group-card"><div class="stripe" style="background:${esc(g.color)}"></div><div class="gc-b">
          <div class="gc-h"><div class="gc-ico ${m.cls}">${icon(m.icon)}</div><div><h4>${esc(g.name)}</h4><div style="margin-top:3px">${listBadge(g.type)}</div></div>
            <div class="gc-count"><b>${g.vehicles}</b><span>คัน</span></div></div>
          <div class="muted" style="font-size:12.5px;min-height:20px;margin-bottom:8px">${esc(g.description || m.th)}</div>
          <div class="gc-row">${icon("calendar")}<div class="mini-days">${days}</div></div>
          <div class="gc-row">${icon("clock")}<span class="mono">${esc(s.start || "00:00")} – ${esc(s.end || "23:59")}</span></div>
          ${App.cams.length > 1 ? `<div class="gc-row">${icon("camera")}<span>${g.cameras?.length ? g.cameras.map((c) => esc(camName(c))).join(", ") : "ทุกช่องทาง"}</span></div>` : ""}
          <div class="gc-row">${icon("gate")}${g.type === "deny" ? `<span style="color:#fca5a5">ปฏิเสธเสมอ + แจ้งเตือน</span>` : g.auto_open ? `เปิดไม้กั้นอัตโนมัติ · <span class="mono">${outs}</span>` : `<span class="muted">ไม่เปิดอัตโนมัติ (บันทึกอย่างเดียว)</span>`}</div>
        </div>${can("admin") ? `<div class="gc-foot"><button class="btn sm" data-edit="${g.id}">${icon("edit")}แก้ไข</button><a class="btn sm ghost" href="#/vehicles">${icon("car")}ดูรถในกลุ่ม</a>
          <span style="flex:1"></span><button class="btn sm ghost icon" data-del="${g.id}">${icon("trash")}</button></div>` : ""}</div>`;
      }).join("");
      $$("[data-edit]").forEach((b) => (b.onclick = () => editGroup(App.groups.find((g) => g.id == b.dataset.edit), load)));
      $$("[data-del]").forEach((b) => (b.onclick = async () => {
        const g = App.groups.find((x) => x.id == b.dataset.del);
        if (!(await confirmBox("ลบกลุ่มสิทธิ์", `ลบกลุ่ม <b>${esc(g.name)}</b>? รถ ${g.vehicles} คันในกลุ่มจะไม่มีสิทธิ์จนกว่าจะกำหนดกลุ่มใหม่`, "ลบกลุ่ม"))) return;
        try { await api(`/api/groups/${g.id}`, { method: "DELETE" }); toast("ลบกลุ่มเรียบร้อย"); load(); } catch (e) { toast(e.message, "bad"); }
      }));
    };
    $("#g-add") && ($("#g-add").onclick = () => editGroup(null, load));
    const check = async () => {
      const plate = $("#chk-plate").value.trim();
      if (!plate) return;
      try {
        const r = await api("/api/test/decide", { body: { plate, province: $("#chk-prov").value } });
        const ok = r.decision === "granted";
        $("#chk-res").innerHTML = `<div style="display:flex;gap:20px;align-items:center;flex-wrap:wrap">
          ${plateHTML(r.plate, $("#chk-prov").value || r.vehicle?.province, "")}
          <div class="decision ${ok ? "granted" : "denied"}" style="margin:0;flex:1;min-width:280px"><div class="d-ico">${icon(ok ? "check" : "x")}</div>
            <div><div class="d-title">${ok ? "อนุญาตให้ผ่าน" : "ไม่อนุญาต"}</div><div class="d-sub">${esc(r.reason)}</div></div></div>
          <dl class="kv" style="min-width:260px"><dt>เจ้าของ</dt><dd>${esc(r.vehicle?.owner || "—")}</dd><dt>กลุ่ม</dt><dd>${groupTag(r.group_name, r.group_color)}</dd>
            <dt>สั่งงาน</dt><dd>${ok ? (r.auto_open === false ? "ไม่เปิดอัตโนมัติ" : r.outputs?.length ? r.outputs.map((o) => "OUT" + o).join(", ") : "ช่องไม้กั้นหลัก") : "—"}</dd></dl></div>`;
      } catch (e) { toast(e.message, "bad"); }
    };
    $("#chk-go").onclick = check;
    $("#chk-plate").onkeydown = (e) => e.key === "Enter" && check();
    load().catch((e) => toast(e.message, "bad"));
  },
};

async function editGroup(g, onSaved) {
  const isNew = !g;
  g = g || { name: "", type: "allow", color: GROUP_COLORS[1], auto_open: 1, outputs: [], cameras: [], schedule: { days: [0, 1, 2, 3, 4, 5, 6], start: "00:00", end: "23:59" }, description: "" };
  g.cameras = g.cameras || [];
  let io = App.io[App.cams[0]?.id];
  if (!io) { try { io = await api("/api/io"); } catch { io = { outputs: [] }; } }
  const outs = io.outputs.length ? io.outputs : [{ id: 1, name: "Alarm Out 1" }];
  const sched = g.schedule || {};
  modal({
    title: isNew ? "สร้างกลุ่มสิทธิ์" : `แก้ไขกลุ่ม: ${esc(g.name)}`, icon: "shield",
    body: `<div class="form-grid">
      <div class="field full"><label>ชื่อกลุ่ม *</label><input class="input" id="g-name" value="${esc(g.name)}" placeholder="เช่น พนักงานกะเช้า"></div>
      <div class="field full"><label>ประเภทรายการ</label><div class="seg" id="g-type">${Object.entries(TYPE_META).map(([k, m]) => `<button type="button" data-v="${k}" class="${g.type === k ? "on" : ""}">${m.label} · ${m.th}</button>`).join("")}</div></div>
      <div class="field full"><label>สีประจำกลุ่ม</label><div class="swatches" id="g-color">${GROUP_COLORS.map((c) => `<button type="button" data-c="${c}" class="${c === g.color ? "on" : ""}" style="background:${c}"></button>`).join("")}</div></div>
      <div class="field full"><label>วันที่อนุญาต</label><div class="days" id="g-days">${DAYS_TH.map((d, i) => `<button type="button" data-d="${i}" class="${(sched.days || []).includes(i) ? "on" : ""}">${d}</button>`).join("")}
        <button type="button" class="btn sm ghost" id="g-alldays">ทุกวัน</button><button type="button" class="btn sm ghost" id="g-weekdays">จ–ศ</button></div></div>
      <div class="field"><label>เวลาเริ่ม</label><input class="input" type="time" id="g-start" value="${esc(sched.start || "00:00")}"></div>
      <div class="field"><label>เวลาสิ้นสุด</label><input class="input" type="time" id="g-end" value="${esc(sched.end || "23:59")}"><span class="hint">หากเวลาสิ้นสุดน้อยกว่าเวลาเริ่ม = ช่วงข้ามคืน</span></div>
      <div class="field full" id="g-auto-wrap">${switchHTML("g-auto", g.auto_open, "สั่งเปิดไม้กั้นอัตโนมัติเมื่ออ่านป้ายได้และมีสิทธิ์")}</div>
      <div class="field full" id="g-outs-wrap"><label>ช่อง Alarm Output ที่สั่งงาน</label><div class="toolbar">${outs.map((o) => `<label class="switch"><input type="checkbox" data-o="${o.id}" ${g.outputs.includes(o.id) ? "checked" : ""}><span class="track"></span><span>OUT${o.id} · ${esc(o.name || "")}</span></label>`).join("")}</div>
        <span class="hint">ไม่เลือก = ใช้ช่องไม้กั้นของช่องทางที่อ่านป้ายได้ (ตั้งค่าในหน้า กล้อง & I/O)</span></div>
      ${App.cams.length > 1 ? `<div class="field full"><label>ช่องทางที่อนุญาต</label><div class="toolbar">${App.cams.map((c) => `<label class="switch"><input type="checkbox" data-cam="${c.id}" ${g.cameras.includes(c.id) ? "checked" : ""}><span class="track"></span><span>${esc(c.name)}</span></label>`).join("")}</div>
        <span class="hint">ไม่เลือก = ผ่านได้ทุกช่องทาง · เลือกเพื่อจำกัด เช่น ผู้มาติดต่อเข้าได้เฉพาะประตูหน้า</span></div>` : ""}
      <div class="field full"><label>คำอธิบาย</label><input class="input" id="g-desc" value="${esc(g.description || "")}"></div></div>`,
    footer: `<button class="btn" data-close>ยกเลิก</button><button class="btn primary" id="g-save">${icon("check")}บันทึก</button>`,
    onMount: (m, close) => {
      let type = g.type, color = g.color;
      const sync = () => { $("#g-auto-wrap", m).classList.toggle("hidden", type === "deny"); $("#g-outs-wrap", m).classList.toggle("hidden", type === "deny"); };
      segBind($("#g-type", m), (v) => { type = v; sync(); });
      sync();
      $$("#g-color button", m).forEach((b) => (b.onclick = () => { color = b.dataset.c; $$("#g-color button", m).forEach((x) => x.classList.toggle("on", x === b)); }));
      $$("#g-days [data-d]", m).forEach((b) => (b.onclick = () => b.classList.toggle("on")));
      $("#g-alldays", m).onclick = () => $$("#g-days [data-d]", m).forEach((b) => b.classList.add("on"));
      $("#g-weekdays", m).onclick = () => $$("#g-days [data-d]", m).forEach((b) => b.classList.toggle("on", +b.dataset.d < 5));
      $("#g-save", m).onclick = async () => {
        const body = {
          name: $("#g-name", m).value, type, color, auto_open: type !== "deny" && $("#g-auto", m).checked,
          outputs: $$("[data-o]", m).filter((c) => c.checked).map((c) => +c.dataset.o),
          cameras: $$("[data-cam]", m).filter((c) => c.checked).map((c) => +c.dataset.cam),
          schedule: { days: $$("#g-days [data-d].on", m).map((b) => +b.dataset.d), start: $("#g-start", m).value, end: $("#g-end", m).value },
          description: $("#g-desc", m).value,
        };
        try {
          await api(isNew ? "/api/groups" : `/api/groups/${g.id}`, { method: isNew ? "POST" : "PUT", body });
          toast("บันทึกกลุ่มสิทธิ์เรียบร้อย");
          close();
          onSaved && onSaved();
        } catch (e) { toast(e.message, "bad"); }
      };
    },
  });
}
