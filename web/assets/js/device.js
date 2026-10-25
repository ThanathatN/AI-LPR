/* Cameras (lanes), connection, Alarm I/O binding per camera, image tuning for LPR */
"use strict";
App.pages.device = {
  render(el) {
    const admin = can("admin");
    let sel = camPref.get("device");
    el.innerHTML = `
    <div class="section-title"><div><h2>กล้อง & Alarm I/O</h2><p>จัดการกล้องแต่ละช่องทาง การเชื่อมต่อ การผูกไม้กั้น / Loop / Radar และปรับภาพสำหรับอ่านป้าย</p></div>
      <div class="right"><button class="btn" id="d-refresh">${icon("refresh")}รีเฟรช</button>${admin ? `<button class="btn primary" id="d-add">${icon("plus")}เพิ่มกล้อง</button>` : ""}</div></div>
    <div class="grid" style="grid-template-columns:260px minmax(0,1fr);align-items:start">
      <div class="card"><div class="card-h"><h3>กล้องทั้งหมด</h3><span class="sub" id="cam-count"></span></div><div class="cam-list" id="cam-list"></div></div>
      <div class="grid" id="cam-detail" style="align-content:start"></div>
    </div>`;

    let io = null, cam = null;
    const sel_ = (id, val, opts, dis = !admin) => `<select class="input" id="${id}" ${dis ? "disabled" : ""}>${opts.map(([v, t]) => `<option value="${v}" ${String(v) === String(val) ? "selected" : ""}>${t}</option>`).join("")}</select>`;
    const row = (ok, title, detail, extra = "") => `<div class="check-row"><div class="ci ${ok ? "ico-green" : "ico-red"}">${icon(ok ? "check" : "x")}</div>
      <div style="flex:1"><div style="font-weight:600">${title}</div><div class="muted" style="font-size:12px">${detail}</div></div>${extra}</div>`;

    /* ---------------- camera list */
    const paintList = () => {
      $("#cam-count").textContent = `${App.cams.length} ตัว`;
      $("#cam-list").innerHTML = App.cams.map((c) => {
        const st = camStatus(c.id);
        return `<div class="cam-row ${c.id === sel ? "sel" : ""}" data-id="${c.id}"><span class="dot ${!c.enabled ? "" : st?.video.connected ? "ok" : "bad"}"></span>
          <div style="min-width:0;flex:1"><div class="cr-name">${esc(c.name)}</div><div class="cr-sub">${LANE_TH[c.lane] || ""} · ${esc(c.host || "วิดีโอทดแทน")}${c.enabled ? "" : " · ปิดใช้งาน"}</div></div></div>`;
      }).join("") || `<div class="card-b muted">ยังไม่มีกล้อง</div>`;
      $$("#cam-list .cam-row").forEach((r) => (r.onclick = () => { sel = +r.dataset.id; camPref.set("device", sel); paintList(); load(); }));
    };

    /* ---------------- connection form (shared by add + edit) */
    const connForm = (c = {}) => `<div class="form-grid">
      <div class="field"><label>ชื่อช่องทาง / กล้อง</label><input class="input" id="c-name" value="${esc(c.name || "")}" placeholder="เช่น ประตู 1 - ขาเข้า"></div>
      <div class="field"><label>ประเภทช่องทาง</label>${sel_("c-lane", c.lane || "entry", [["entry", "ทางเข้า"], ["exit", "ทางออก"], ["both", "เข้า-ออก (แยกทิศจากเส้น)"]])}</div>
      <div class="field"><label>IP Address</label><input class="input mono" id="c-host" value="${esc(c.host || "")}" placeholder="192.168.1.124"></div>
      <div class="field"><label>สตรีมวิดีโอ</label>${sel_("c-stream", c.stream || "101", [["101", "Main Stream (101) — แนะนำ"], ["102", "Sub Stream (102)"]])}</div>
      <div class="field"><label>Channel</label><input class="input mono" id="c-ch" type="number" min="1" value="${c.channel || 1}"></div>
      <div class="field"><label>SDK Port (ภาพ · HCNetSDK)</label><input class="input mono" id="c-sdk" type="number" value="${c.sdk_port || 8000}"></div>
      <div class="field"><label>HTTP Port (ข้อมูลอุปกรณ์ / Alarm I/O)</label><input class="input mono" id="c-http" type="number" value="${c.http_port || 80}"></div>
      <input type="hidden" id="c-rtsp" value="${c.rtsp_port || 554}">
      <div class="field"><label>Username</label><input class="input" id="c-user" autocomplete="off" value="${esc(c.username || "admin")}"></div>
      <div class="field"><label>Password</label><input class="input" id="c-pass" type="password" autocomplete="new-password" placeholder="${c.has_password ? "•••••• (เว้นว่าง = ใช้รหัสเดิม)" : ""}"></div>
      <div class="field full"><label>แหล่งวิดีโอทดแทน (สำหรับทดสอบ)</label><input class="input mono" id="c-src" value="${esc(c.source_override || "")}" placeholder="เว้นว่าง = ใช้ภาพจากกล้องผ่าน HCNetSDK · หรือ path ไฟล์วิดีโอในเครื่อง เช่น C:\\test\\gate.mp4"></div>
      <div class="field full">${switchHTML("c-en", c.enabled !== false, "เปิดใช้งานกล้องนี้")}</div>
    </div>`;
    const connBody = (root) => ({ name: $("#c-name", root).value.trim(), lane: $("#c-lane", root).value, host: $("#c-host", root).value.trim(),
      capture: "sdk", channel: +$("#c-ch", root).value || 1, sdk_port: +$("#c-sdk", root).value || 8000,
      http_port: +$("#c-http", root).value, rtsp_port: +$("#c-rtsp", root).value, username: $("#c-user", root).value,
      password: $("#c-pass", root).value, stream: $("#c-stream", root).value, source_override: $("#c-src", root).value.trim(), enabled: $("#c-en", root).checked });
    const testConn = async (btn, body, out) => {
      try {
        const r = await busy(btn, () => api("/api/cameras/test", { body }));
        out.innerHTML = row(r.isapi.ok, "ISAPI / HTTP", esc(r.isapi.ok ? `${r.isapi.model} · FW ${r.isapi.firmware}` : r.isapi.error), r.isapi.ms ? `<span class="meta">${r.isapi.ms} ms</span>` : "") +
          row(r.video.ok, esc(r.video.label || "HCNetSDK"), esc(r.video.ok ? `ได้รับภาพ ${r.video.resolution || ""}` : r.video.error), r.video.ms ? `<span class="meta">${r.video.ms} ms</span>` : "") +
          row(r.io.ok, "Alarm I/O", esc(r.io.ok ? `Output ${r.io.outputs} ช่อง · Input ${r.io.inputs} ช่อง` : r.io.error));
      } catch (e) { toast(e.message, "bad"); }
    };

    $("#d-add") && ($("#d-add").onclick = () => modal({
      title: "เพิ่มกล้อง / ช่องทาง", icon: "camera", wide: true,
      body: `${connForm({ username: "admin" })}<div class="checklist" id="add-checks" style="margin-top:12px"></div>`,
      footer: `<button class="btn" id="add-test" data-busy="กำลังทดสอบ...">${icon("activity")}ทดสอบการเชื่อมต่อ</button><span class="spacer"></span><button class="btn" data-close>ยกเลิก</button><button class="btn primary" id="add-save">${icon("check")}เพิ่มกล้อง</button>`,
      onMount: (m, close) => {
        $("#add-test", m).onclick = (e) => testConn(e.currentTarget, connBody(m), $("#add-checks", m));
        $("#add-save", m).onclick = async () => {
          try {
            const c = await api("/api/cameras", { body: connBody(m) });
            await loadCams();
            sel = c.id; camPref.set("device", sel);
            close(); paintList(); load();
            toast(`เพิ่มกล้อง ${c.name} แล้ว — ตั้งเส้นทริกเกอร์และพื้นที่ตรวจจับต่อได้ที่หน้า ตั้งค่า`);
          } catch (e) { toast(e.message, "bad"); }
        };
      },
    }));

    /* ---------------- detail of the selected camera */
    const EV_TH = { VMD: "Motion Detection", tamperdetection: "Video Tampering", defocus: "Defocus", IO: "Alarm Input", linedetection: "Line Crossing", fielddetection: "Intrusion", scenechangedetection: "Scene Change" };
    const paintDetail = (dev) => {
      const c = cam, i = dev.info || {}, v = dev.video || {};
      $("#cam-detail").innerHTML = `
      <div class="grid" style="grid-template-columns:minmax(0,1.2fr) minmax(0,1fr)">
        <div class="card"><div class="card-b"><div class="dev-hero">
          <div class="dev-img"><svg viewBox="0 0 80 60" fill="none"><rect x="8" y="16" width="52" height="24" rx="6" fill="#d9dadc" stroke="#9a9ba0"/><rect x="54" y="20" width="18" height="16" rx="3" fill="#2a2b2f" stroke="#55565c"/>
            <circle cx="63" cy="28" r="5" fill="#0c0c0d" stroke="#6b6c72" stroke-width="1.5"/><circle cx="63" cy="28" r="2" fill="#3a3b40"/><path d="M24 40v8h-8v4h24v-4h-8v-8" stroke="#9a9ba0" stroke-width="2"/><circle cx="20" cy="28" r="2" fill="#e0313a"/></svg></div>
          <div style="flex:1;min-width:0"><div class="muted" style="font-size:12px">${esc(c.name)} · ${LANE_TH[c.lane] || ""}</div>
            <div style="font-size:22px;font-weight:700">${esc(i.model || (c.host ? "ไม่สามารถเชื่อมต่อได้" : "แหล่งวิดีโอทดแทน"))}</div>
            <div class="toolbar" style="margin-top:8px">${v.connected ? `<span class="badge b-ok"><span class="dot ok"></span>ออนไลน์</span>` : `<span class="badge b-bad">ออฟไลน์</span>`}
              <span class="badge b-mute">${esc(c.host || "—")}</span>${dev.stream?.codec ? `<span class="badge b-info">${esc(dev.stream.codec)}</span>` : ""}${c.enabled ? "" : `<span class="badge b-warn">ปิดใช้งาน</span>`}</div></div></div>
          <dl class="kv" style="margin-top:16px;grid-template-columns:140px 1fr">
            ${[["Serial No.", i.serialNumber], ["Firmware", `${i.firmwareVersion || ""} ${i.firmwareReleasedDate || ""}`], ["MAC Address", i.macAddress],
               ["ความละเอียด", dev.stream?.width ? `${dev.stream.width}×${dev.stream.height} @ ${dev.stream.fps} fps` : v.width ? `${v.width}×${v.height}` : ""], ["เวลาในกล้อง", dev.time]]
              .map(([k, x]) => `<dt>${k}</dt><dd class="mono" style="font-size:12.5px">${esc((x || "").trim() || "—")}</dd>`).join("")}
            ${dev.error ? `<dt>ข้อผิดพลาด</dt><dd style="color:#fca5a5">${esc(dev.error)}</dd>` : ""}</dl></div></div>
        <div class="card"><div class="card-h"><h3>การเชื่อมต่อ</h3><span class="sub">ภาพ: HCNetSDK · อุปกรณ์/I/O: ISAPI</span></div><div class="card-b">${connForm(c)}
          <div class="checklist" id="d-checks" style="margin-top:10px"></div>
          ${admin ? `<div class="toolbar" style="margin-top:12px"><button class="btn" id="c-test" data-busy="กำลังทดสอบ...">${icon("activity")}ทดสอบ</button><span class="spacer"></span>
            <button class="btn danger" id="c-del">${icon("trash")}ลบกล้อง</button><button class="btn primary" id="c-save">${icon("check")}บันทึก</button></div>` : ""}</div></div>
      </div>

      <div class="card"><div class="card-h"><h3>ความพร้อมของ Alarm I/O</h3><span class="sub">${esc(c.name)}</span>
        <div class="right"><button class="btn sm" id="lk-check">${icon("refresh")}ตรวจสอบอีกครั้ง</button></div></div><div class="card-b" id="d-health"></div></div>

      <div class="card"><div class="card-h"><h3>การผูก I/O ของช่องทางนี้</h3><span class="sub">ไม้กั้นอาจต่ออยู่ที่รีเลย์ของกล้องตัวอื่นได้</span></div><div class="card-b" id="d-bind"></div></div>

      <div class="grid g-2">
        <div class="card"><div class="card-h"><h3>Alarm Output</h3><span class="sub">พอร์ตรีเลย์ของกล้องนี้</span></div><div class="card-b grid" id="d-outs"></div></div>
        <div class="card"><div class="card-h"><h3>Alarm Input</h3><span class="sub">Loop Detector / Radar กันไม้ตี</span></div><div class="card-b grid" id="d-ins"></div></div>
      </div>

      <div class="card"><div class="card-h"><h3>${icon("sun")}ปรับภาพสำหรับอ่านป้ายทะเบียน</h3><span class="sub">เทียบเท่า “Entrance & Exit exposure” ของกล้อง ANPR</span></div><div class="card-b" id="d-image">
        <div class="muted">กำลังอ่านค่าจากกล้อง...</div></div></div>

      <div class="card"><div class="card-h"><h3>แผนผังการต่อสาย</h3><span class="sub">ขั้วต่อ I/O ของกล้อง</span></div><div class="card-b wiring">${wiringSVG(i.model || "IP Camera")}
        <div class="notice" style="margin-top:12px">${icon("info")}<div>Alarm Output ของกล้องเป็นหน้าสัมผัสกำลังต่ำ ต่อเข้ากับขา <b>OPEN / COM</b> ของชุดควบคุมไม้กั้น (หรือผ่านรีเลย์ช่วย)
          สำหรับ Loop Detector หรือ Radar ให้ต่อหน้าสัมผัสรีเลย์ (NO) เข้ากับขา <b>ALARM IN</b> และ <b>GND</b> ของกล้อง</div></div></div></div>`;

      if (!admin) $$("#cam-detail .form-grid input, #cam-detail .form-grid select").forEach((x) => (x.disabled = true));
      $("#c-test") && ($("#c-test").onclick = (e) => testConn(e.currentTarget, { ...connBody($("#cam-detail")), id: c.id }, $("#d-checks")));
      $("#c-save") && ($("#c-save").onclick = async () => {
        try { await api(`/api/cameras/${c.id}`, { method: "PUT", body: connBody($("#cam-detail")) }); await loadCams(); paintList(); toast("บันทึกแล้ว กำลังเชื่อมต่อใหม่..."); setTimeout(load, 2500); } catch (e) { toast(e.message, "bad"); }
      });
      $("#c-del") && ($("#c-del").onclick = async () => {
        if (!(await confirmBox("ลบกล้อง", `ลบ “${esc(c.name)}” ออกจากระบบ? ประวัติเหตุการณ์เดิมยังคงอยู่`, "ลบกล้อง"))) return;
        try { await api(`/api/cameras/${c.id}`, { method: "DELETE" }); await loadCams(); sel = App.cams[0]?.id; paintList(); load(); toast("ลบกล้องแล้ว"); } catch (e) { toast(e.message, "bad"); }
      });
      $("#lk-check").onclick = async () => { try { const r = await api(`/api/io/linkage?cam=${c.id}`); io.conflicts = r.conflicts; paintHealth(); } catch (e) { toast(e.message, "bad"); } };
      loadImage();
    };

    const paintHealth = () => {
      if (!io || !$("#d-health")) return;
      const c = io.conflicts || [];
      $("#d-health").innerHTML = `<div class="checklist">
        ${row(io.online, "การสั่งงาน Alarm Output ผ่าน ISAPI", io.online ? `พร้อมใช้งาน · Output ${io.outputs.length} ช่อง / Input ${io.inputs.length} ช่อง` : esc(io.error || "เชื่อมต่อไม่ได้"))}
        ${row(!c.length, "ไม่มี Linkage ในกล้องที่สั่ง Alarm Output เอง",
          c.length ? `พบ ${c.length} เหตุการณ์ในกล้องที่สั่งรีเลย์โดยตรง: <b>${c.map((t) => `${EV_TH[t.type] || t.type} → OUT${t.outputs.join(",")}`).join(", ")}</b> — ทำให้ไม้กั้นเปิดเองเมื่อมีการเคลื่อนไหว`
            : "Alarm Output ถูกควบคุมโดยซอฟต์แวร์นี้เท่านั้น",
          c.length && admin ? `<button class="btn primary sm" id="lk-fix">${icon("check")}ยกเลิก Linkage ในกล้อง</button>` : "")}
        ${row(io.stream, "รับเหตุการณ์ Alarm Input แบบทันที (Event Stream)", io.stream ? "เชื่อมต่อแล้ว — Loop Detector / Radar ตอบสนองทันที" : "ยังไม่เชื่อมต่อ — ใช้การอ่านสถานะแบบวนรอบแทน")}
      </div>`;
      $("#lk-fix") && ($("#lk-fix").onclick = async () => {
        if (!(await confirmBox("ยกเลิก Linkage ในกล้อง", "ระบบจะเอา “Alarm Output” ออกจาก Linkage ของ Motion / Tamper / Defocus ในกล้อง (การบันทึกและการแจ้งเตือนอื่นยังคงอยู่)", "ยืนยัน", false))) return;
        try { const r = await api(`/api/io/linkage/fix?cam=${cam.id}`, { body: {} }); io.conflicts = r.conflicts; paintHealth(); toast("ยกเลิก Linkage ในกล้องเรียบร้อย"); } catch (err) { toast(err.message, "bad"); }
      });
    };

    /* ---------------- lane binding (barrier camera/output, alarm, pulse) */
    const paintBind = async () => {
      const cfg = cam.config.io;
      const bcam = +cfg.barrier_camera || cam.id;
      let bOuts = io.outputs;
      if (bcam !== cam.id) { try { bOuts = (await api(`/api/io?cam=${bcam}`)).outputs; } catch { bOuts = []; } }
      const outOpts = (list) => [[0, "— ไม่ใช้ —"], ...list.map((p) => [p.id, `OUT${p.id} ${p.name || ""}`])];
      $("#d-bind").innerHTML = `<div class="form-grid">
        <div class="field"><label>ไม้กั้นต่ออยู่ที่รีเลย์ของกล้อง</label>${sel_("b-cam", bcam, App.cams.map((c) => [c.id, c.id === cam.id ? `${c.name} (กล้องนี้)` : c.name]))}</div>
        <div class="field"><label>ช่อง Alarm Output ไม้กั้น</label>${sel_("b-out", cfg.barrier_output || 0, outOpts(bOuts))}</div>
        <div class="field"><label>ระยะเวลา Pulse เปิดไม้กั้น (ms)</label><input class="input mono" type="number" min="100" step="100" id="b-ms" value="${cfg.pulse_ms || 1000}" ${admin ? "" : "disabled"}></div>
        <div class="field"><label>ไซเรนแจ้งเตือนบัญชีดำ (รีเลย์ของกล้องนี้)</label>${sel_("b-alarm", cfg.alarm_output || 0, outOpts(io.outputs))}</div>
      </div>
      ${admin ? `<div class="toolbar" style="margin-top:12px"><span class="muted" style="font-size:12px">กลุ่มสิทธิ์สามารถกำหนด Output เพิ่มเติมได้ที่หน้า “สิทธิ์การเข้า-ออก”</span><span class="spacer"></span><button class="btn primary" id="b-save">${icon("check")}บันทึกการผูก I/O</button></div>` : ""}`;
      $("#b-cam").onchange = async () => { cam.config.io.barrier_camera = +$("#b-cam").value; cam.config.io.barrier_output = 0; paintBind(); };
      $("#b-save") && ($("#b-save").onclick = async () => {
        const bc = +$("#b-cam").value;
        try {
          const r = await api(`/api/cameras/${cam.id}`, { method: "PUT", body: { config: { io: {
            barrier_camera: bc === cam.id ? 0 : bc, barrier_output: +$("#b-out").value, pulse_ms: +$("#b-ms").value, alarm_output: +$("#b-alarm").value } } } });
          cam = r; await loadCams(); io = await api(`/api/io?cam=${cam.id}`); paintPorts(); toast("บันทึกการผูก I/O แล้ว");
        } catch (e) { toast(e.message, "bad"); }
      });
    };

    /* ---------------- ports */
    const roleTH = { barrier: "ไม้กั้นของช่องทางนี้", alarm: "ไซเรน / แจ้งเตือน", none: "ไม่ได้ผูก" };
    const paintPorts = () => {
      paintHealth();
      const users = App.cams.filter((c) => c.id !== cam.id && +c.config?.io?.barrier_camera === cam.id);
      $("#d-outs").innerHTML = io.outputs.length ? io.outputs.map((p) => {
        const extra = users.filter((c) => +c.config.io.barrier_output === p.id).map((c) => `ไม้กั้นของ ${esc(c.name)}`);
        return `<div class="port-card"><div class="port-head"><span class="pid">OUT${p.id}</span><span class="pname">${esc(p.name || "Alarm Output")}</span>
          <div class="right"><span class="led ${p.active ? "on-green" : ""}" id="led-o${p.id}"></span><span class="muted" style="font-size:12px">${p.active ? "ทำงาน" : "ปกติ"}</span></div></div>
          <div class="muted" style="font-size:12px">บทบาท: <b style="color:var(--text)">${[p.role !== "none" ? roleTH[p.role] : "", ...extra].filter(Boolean).join(", ") || roleTH.none}</b></div>
          <div class="form-grid">
            <div class="field"><label>ชื่อพอร์ต (บันทึกลงกล้อง)</label><input class="input" id="o-name-${p.id}" value="${esc(p.name)}" ${admin ? "" : "disabled"}></div>
            <div class="field"><label>สถานะเริ่มต้น</label>${sel_(`o-def-${p.id}`, p.default_state, [["low", "Low (NO — ปกติเปิด)"], ["high", "High (NC — ปกติปิด)"]])}</div>
          </div>
          <div class="toolbar">
            ${can("operator") ? `<button class="btn success" data-out="${p.id}" data-act="pulse">${icon("zap")}ทดสอบ Pulse</button>
            <button class="btn" data-out="${p.id}" data-act="high">${icon("power")}ON ค้าง</button><button class="btn" data-out="${p.id}" data-act="low">OFF</button>` : ""}
            <span style="flex:1"></span>${admin ? `<button class="btn primary" data-save-out="${p.id}">${icon("check")}บันทึก</button>` : ""}</div></div>`;
      }).join("") : `<div class="notice warn">${icon("alert")}<div>ไม่พบ Alarm Output จากกล้อง ${esc(io.error || "")}</div></div>`;
      const inCfg = (id) => cam.config.io.inputs?.[id] || {};
      $("#d-ins").innerHTML = io.inputs.length ? io.inputs.map((p) => `
        <div class="port-card"><div class="port-head"><span class="pid">IN${p.id}</span><span class="pname">${esc(p.name || "Alarm Input")}</span>
          <div class="right"><span class="led ${p.active ? "on-amber" : ""}"></span><span class="muted" style="font-size:12px">${p.active ? "ACTIVE" : "ปกติ"}</span></div></div>
          <div class="form-grid">
            <div class="field full"><label>บทบาทในระบบ</label>${sel_(`i-role-${p.id}`, inCfg(p.id).role || "none", [["none", "ไม่ใช้งาน (บันทึกสถานะอย่างเดียว)"], ["trigger", "Loop Detector — สั่งให้ AI อ่านป้าย"], ["safety", "Radar / Anti-fall — ค้างไม้กั้นเมื่อมีวัตถุ"]])}</div>
            <div class="field full ${inCfg(p.id).role === "trigger" ? "" : "hidden"}" id="i-tgt-wrap-${p.id}"><label>สั่งอ่านป้ายที่กล้อง</label>${sel_(`i-tgt-${p.id}`, inCfg(p.id).target || cam.id, App.cams.map((c) => [c.id, c.id === cam.id ? `${c.name} (กล้องนี้)` : c.name]))}</div>
            <div class="field"><label>ชื่อพอร์ต</label><input class="input" id="i-name-${p.id}" value="${esc(p.name)}" ${admin ? "" : "disabled"}></div>
            <div class="field"><label>ชนิดหน้าสัมผัส</label>${sel_(`i-trig-${p.id}`, p.triggering, [["high", "N.O. (ปกติเปิด)"], ["low", "N.C. (ปกติปิด)"]])}</div>
            <div class="field full">${switchHTML(`i-en-${p.id}`, p.enabled, "เปิดใช้งาน Alarm Input นี้ในกล้อง")}</div>
          </div>
          ${admin ? `<div class="toolbar"><span style="flex:1"></span><button class="btn primary" data-save-in="${p.id}">${icon("check")}บันทึก</button></div>` : ""}</div>`).join("")
        : `<div class="notice warn">${icon("alert")}<div>ไม่พบ Alarm Input จากกล้อง</div></div>`;
      io.inputs.forEach((p) => { const s = $(`#i-role-${p.id}`); if (s) s.onchange = () => $(`#i-tgt-wrap-${p.id}`).classList.toggle("hidden", s.value !== "trigger"); });
      $$("[data-out]").forEach((b) => (b.onclick = async () => {
        try {
          io = { ...io, ...(await api(`/api/io/output/${b.dataset.out}?cam=${cam.id}`, { body: { action: b.dataset.act } })) };
          toast(`OUT${b.dataset.out}: ${b.dataset.act.toUpperCase()} สำเร็จ`);
        } catch (e) { toast(e.message, "bad"); }
      }));
      $$("[data-save-out]").forEach((b) => (b.onclick = async () => {
        const id = +b.dataset.saveOut, p = io.outputs.find((x) => x.id === id);
        try {
          io = { ...io, ...(await api(`/api/io/output/${id}/config?cam=${cam.id}`, { method: "PUT", body: { name: $(`#o-name-${id}`).value, default_state: $(`#o-def-${id}`).value, output_state: p.output_state || "pulse", pulse_ms: cam.config.io.pulse_ms || 1000 } })) };
          toast(`บันทึกการตั้งค่า OUT${id} เรียบร้อย`); paintPorts();
        } catch (e) { toast(e.message, "bad"); }
      }));
      $$("[data-save-in]").forEach((b) => (b.onclick = async () => {
        const id = +b.dataset.saveIn;
        try {
          io = { ...io, ...(await api(`/api/io/input/${id}/config?cam=${cam.id}`, { method: "PUT", body: { name: $(`#i-name-${id}`).value, triggering: $(`#i-trig-${id}`).value, enabled: $(`#i-en-${id}`).checked, role: $(`#i-role-${id}`).value, target: +$(`#i-tgt-${id}`).value === cam.id ? 0 : +$(`#i-tgt-${id}`).value } })) };
          await loadCams(); cam = App.cams.find((c) => c.id === sel) || cam;
          toast(`บันทึกการตั้งค่า IN${id} เรียบร้อย`); paintPorts();
        } catch (e) { toast(e.message, "bad"); }
      }));
      paintBind();
    };

    /* ---------------- image tuning */
    const IMG_TH = { shutter: "Shutter (ความเร็วชัตเตอร์)", wdr: "WDR", wdr_level: "ระดับ WDR", hlc: "HLC (กดแสงไฟหน้ารถ)", hlc_level: "ระดับ HLC",
      blc: "BLC", dnr: "ลดสัญญาณรบกวน (DNR)", dnr_level: "ระดับ DNR", sharpness: "ความคมชัด", gain: "Gain", brightness: "ความสว่าง", contrast: "คอนทราสต์", ircut: "Day/Night" };
    const loadImage = async () => {
      const box = $("#d-image");
      if (!cam.host) { box.innerHTML = `<div class="muted">กล้องนี้ไม่มี ISAPI</div>`; return; }
      let d;
      try { d = await api(`/api/cameras/${cam.id}/image`); } catch (e) { box.innerHTML = `<div class="notice warn">${icon("alert")}<div>${esc(e.message)}</div></div>`; return; }
      const v = d.values;
      const sh = v.shutter?.value || "";
      const den = +(sh.split("/")[1] || 0);
      const warn = den && den < 200;
      const field = (k) => {
        const x = v[k];
        if (!x) return "";
        if (x.opt) return `<div class="field"><label>${IMG_TH[k]}</label>${sel_(`im-${k}`, x.value, x.opt.map((o) => [o, o]))}</div>`;
        if (x.min !== undefined) return `<div class="field"><label>${IMG_TH[k]}</label><div class="range-row"><input type="range" id="im-${k}" min="${x.min}" max="${x.max}" value="${x.value}" ${admin ? "" : "disabled"}><span class="val" id="im-${k}-v">${x.value}</span></div></div>`;
        return `<div class="field"><label>${IMG_TH[k]}</label><input class="input" id="im-${k}" value="${esc(x.value)}" ${admin ? "" : "disabled"}></div>`;
      };
      box.innerHTML = `
        ${warn ? `<div class="notice bad" style="margin-bottom:12px">${icon("alert")}<div><b>Shutter ปัจจุบัน ${esc(sh)} วินาที — ช้าเกินไปสำหรับอ่านป้าย</b> รถที่เคลื่อนที่จะเบลอ ทำให้ตัวอักษรที่คล้ายกัน (ห/ท, ธ/ร, ข/ช) อ่านผิด
          กล้อง ANPR เฉพาะทางอย่าง DS-TCG406-E ใช้ชัตเตอร์เร็ว + ไฟส่องป้าย แนะนำ <b>1/250</b> สำหรับไม้กั้น (รถ ≤ 20 กม./ชม.) หรือ <b>1/500</b> ถ้ารถวิ่งเร็วกว่า</div></div>` : ""}
        <div class="toolbar" style="margin-bottom:12px">
          ${admin ? `<button class="btn primary" data-preset="lpr_gate">${icon("zap")}โหมดไม้กั้น (แนะนำ) · 1/250</button><button class="btn" data-preset="lpr_fast">รถวิ่งเร็ว · 1/500</button>
          ${d.backup ? `<button class="btn ghost" id="im-restore">${icon("refresh")}คืนค่าเดิม (${esc(d.backup.shutter || "")})</button>` : ""}` : ""}
        </div>
        <div class="form-grid" style="grid-template-columns:repeat(3,minmax(0,1fr))">${["shutter", "wdr", "wdr_level", "hlc", "hlc_level", "dnr", "dnr_level", "sharpness", "gain"].map(field).join("")}</div>
        <div class="notice" style="margin-top:12px">${icon("info")}<div>ชัตเตอร์เร็ว = ภาพมืดลง กล้องจะชดเชยด้วย Gain และไฟ IR · กลางคืน HLC ช่วยกดแสงไฟหน้ารถไม่ให้ป้ายขาวโพลน · DNR สูงทำให้ขอบตัวอักษรเลือน แนะนำ 30-40</div></div>
        ${admin ? `<div class="toolbar" style="margin-top:12px"><span class="spacer"></span><button class="btn primary" id="im-save">${icon("check")}บันทึกค่าภาพลงกล้อง</button></div>` : ""}`;
      $$("#d-image input[type=range]").forEach((r) => (r.oninput = () => ($(`#${r.id}-v`).textContent = r.value)));
      const apply = async (body, msg) => {
        try { await api(`/api/cameras/${cam.id}/image`, { method: "PUT", body }); toast(msg); loadImage(); } catch (e) { toast(e.message, "bad"); }
      };
      $$("[data-preset]", box).forEach((b) => (b.onclick = () => apply({ preset: b.dataset.preset }, "ตั้งค่าภาพสำหรับอ่านป้ายแล้ว — ดูภาพสดว่าความสว่างพอหรือไม่")));
      $("#im-restore") && ($("#im-restore").onclick = () => apply({ restore: true }, "คืนค่าภาพเดิมแล้ว"));
      $("#im-save") && ($("#im-save").onclick = () => {
        const values = {};
        Object.keys(v).forEach((k) => { const x = $(`#im-${k}`); if (x && String(x.value) !== String(v[k].value)) values[k] = x.value; });
        if (!Object.keys(values).length) return toast("ไม่มีค่าที่เปลี่ยน", "info");
        apply({ values }, "บันทึกค่าภาพลงกล้องแล้ว");
      });
    };

    /* ---------------- load */
    const load = async () => {
      if (!sel) { $("#cam-detail").innerHTML = emptyHTML("ยังไม่มีกล้อง — กด “เพิ่มกล้อง”", "camera"); return; }
      cam = App.cams.find((c) => c.id === sel);
      try {
        const [dev, ioRes] = await Promise.all([api(`/api/device?cam=${sel}`), api(`/api/io?cam=${sel}`)]);
        io = ioRes;
        paintDetail(dev);
        paintPorts();
      } catch (e) { toast(e.message, "bad"); }
    };
    $("#d-refresh").onclick = async () => { await api(`/api/io/refresh?cam=${sel}`, { method: "POST" }).catch(() => {}); await loadCams(); paintList(); load(); };
    const offs = [
      Bus.on("io", (d) => {
        if (!io || d.cam !== sel) return;
        d.outputs.forEach((p) => { const led = $(`#led-o${p.id}`); if (led) led.className = `led ${p.active ? "on-green" : ""}`; });
        const changedIn = d.inputs.some((p) => (io.inputs.find((x) => x.id === p.id) || {}).active !== p.active);
        io = { ...io, ...d };
        if (changedIn) paintPorts();
      }),
      Bus.on("status", () => paintList()),
    ];
    paintList();
    load();
    return () => offs.forEach((f) => f());
  },
};

function wiringSVG(model = "IP Camera") {
  const box = "#202124", edge = "#4a4b51", txt = "#e4e4e6", sub = "#8a8b91", wire = "#e0313a", gnd = "#7c7d83", inw = "#eba22c";
  return `<svg viewBox="0 0 640 250" font-family="Segoe UI, Leelawadee UI, sans-serif" font-size="12">
    <rect x="230" y="40" width="180" height="170" rx="3" fill="${box}" stroke="${edge}"/>
    <text x="320" y="66" text-anchor="middle" fill="${txt}" font-weight="700">${esc(model)}</text>
    <text x="320" y="84" text-anchor="middle" fill="${sub}" font-size="11">I/O Terminal</text>
    ${[["ALARM OUT 1", 112], ["COM", 136], ["ALARM IN 1", 168], ["GND", 192]].map(([t, y]) => `<circle cx="${t.includes("IN") || t === "GND" ? 230 : 410}" cy="${y}" r="5" fill="${box}" stroke="${txt}" stroke-width="1.5"/>
      <text x="${t.includes("IN") || t === "GND" ? 244 : 396}" y="${y + 4}" text-anchor="${t.includes("IN") || t === "GND" ? "start" : "end"}" fill="${sub}">${t}</text>`).join("")}
    <rect x="490" y="86" width="140" height="76" rx="3" fill="${box}" stroke="${edge}"/>
    <text x="560" y="114" text-anchor="middle" fill="${txt}" font-weight="600">ชุดควบคุมไม้กั้น</text>
    <text x="560" y="134" text-anchor="middle" fill="${sub}" font-size="11">OPEN</text><text x="560" y="150" text-anchor="middle" fill="${sub}" font-size="11">COM</text>
    <path d="M415 112 H470 V130 H490" stroke="${wire}" stroke-width="2" fill="none"/><path d="M415 136 H460 V146 H490" stroke="${gnd}" stroke-width="2" fill="none"/>
    <rect x="10" y="130" width="150" height="82" rx="3" fill="${box}" stroke="${edge}"/>
    <text x="85" y="158" text-anchor="middle" fill="${txt}" font-weight="600">Loop Detector</text>
    <text x="85" y="176" text-anchor="middle" fill="${txt}" font-weight="600">หรือ Radar</text>
    <text x="85" y="197" text-anchor="middle" fill="${sub}" font-size="11">Relay NO / COM</text>
    <path d="M160 160 H200 V168 H225" stroke="${inw}" stroke-width="2" fill="none"/><path d="M160 186 H200 V192 H225" stroke="${gnd}" stroke-width="2" fill="none"/>
    <text x="452" y="70" text-anchor="middle" fill="${sub}" font-size="11">Dry contact</text>
    <text x="190" y="120" text-anchor="middle" fill="${sub}" font-size="11">สัญญาณเข้า</text>
  </svg>`;
}
