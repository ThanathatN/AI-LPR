/* System settings: per-lane trigger & geometry, global recognition, access policy, system */
"use strict";
App.pages.settings = {
  render(el) {
    const admin = can("admin");
    const q = new URLSearchParams(location.hash.split("?")[1] || "");
    const startTab = q.get("tab") || "rec";
    let cam = +q.get("cam") || camPref.get("settings");
    el.innerHTML = `
    <div class="section-title"><div><h2>ตั้งค่าระบบ</h2><p>การทริกเกอร์และพื้นที่ตรวจจับของแต่ละช่องทาง การรู้จำป้าย นโยบายการเข้า-ออก และระบบ</p></div></div>
    ${admin ? "" : `<div class="notice warn" style="margin-bottom:12px">${icon("alert")}<div>เฉพาะผู้ดูแลระบบเท่านั้นที่แก้ไขการตั้งค่าได้</div></div>`}
    <div class="tabs" id="s-tabs">
      <button data-t="rec">${icon("scan")}การรู้จำ & ทริกเกอร์</button><button data-t="roi">${icon("target")}เส้นทริกเกอร์ & พื้นที่ตรวจจับ</button>
      <button data-t="access">${icon("shield")}นโยบายการเข้า-ออก</button><button data-t="system">${icon("database")}ระบบ</button>
    </div><div id="s-body"></div>`;
    let s = null;
    let roObs = null;
    const range =(id, label, val, min, max, step, unit = "", hint = "") => `<div class="field"><label>${label}</label>
      <div class="range-row"><input type="range" id="${id}" min="${min}" max="${max}" step="${step}" value="${val}" ${admin ? "" : "disabled"}><span class="val" id="${id}-v">${val}${unit}</span></div>${hint ? `<span class="hint">${hint}</span>` : ""}</div>`;
    const bindRanges = (root, unitMap = {}) => $$("input[type=range]", root).forEach((r) => (r.oninput = () => ($(`#${r.id}-v`).textContent = r.value + (unitMap[r.id] || ""))));
    const saveBtn = (id, label = "บันทึก") => admin ? `<div class="toolbar" style="margin-top:12px"><button class="btn primary" id="${id}">${icon("check")}${label}</button></div>` : "";
    const save = async (section, body, quiet = false) => {
      try { s[section] = await api(`/api/settings/${section}`, { method: "PUT", body }); App.settings = s; quiet || toast("บันทึกการตั้งค่าเรียบร้อย"); } catch (e) { toast(e.message, "bad"); }
    };
    const camObj = () => App.cams.find((c) => c.id === cam) || App.cams[0];
    const saveLane = async (config, msg = "บันทึกการตั้งค่าช่องทางเรียบร้อย") => {
      try { await api(`/api/cameras/${cam}`, { method: "PUT", body: { config } }); await loadCams(); toast(msg); } catch (e) { toast(e.message, "bad"); }
    };
    const camBar = () => `<div class="card" style="margin-bottom:12px"><div class="card-b toolbar"><b>ช่องทาง / กล้อง</b>${camSelect("s-cam", cam)}
      <span class="muted" id="s-cam-info"></span></div></div>`;
    const bindCam = (rerender) => {
      const c = camObj();
      $("#s-cam-info").textContent = c ? `${LANE_TH[c.lane] || ""} · ${c.host || "วิดีโอทดแทน"}` : "";
      $("#s-cam").onchange = () => { cam = +$("#s-cam").value; camPref.set("settings", cam); rerender(); };
    };

    /* ---------------------------------------------------------------- recognition & trigger */
    const tabRec = () => {
      const r = s.recognition, c = camObj();
      if (!c) { $("#s-body").innerHTML = emptyHTML("ยังไม่มีกล้อง", "camera"); return; }
      const L = c.config.recognition;
      $("#s-body").innerHTML = camBar() + `<div class="grid g-2">
        <div class="card"><div class="card-h"><h3>การทริกเกอร์ของช่องทางนี้</h3><span class="sub">${esc(c.name)}</span></div><div class="card-b grid">
          ${switchHTML("r-en", L.enabled, "เปิดใช้งานการอ่านป้ายทะเบียนของกล้องนี้")}
          <div class="field"><label>วิธีการทริกเกอร์</label><div class="seg" id="r-mode">
            ${[["line", "activity", "เส้นเสมือน"], ["video", "monitor", "วิดีโอต่อเนื่อง"], ["input", "loop", "Loop Detector"], ["mixed", "layers", "เส้น + Loop"]]
              .map(([v, ic, t]) => `<button data-v="${v}" class="${L.trigger_mode === v ? "on" : ""}">${icon(ic)} ${t}</button>`).join("")}</div>
            <span class="hint" id="r-mode-hint"></span></div>
          <div class="field" id="r-motion-wrap"><label>ทิศทางรถที่จะอ่าน (โหมดวิดีโอ / Loop)</label><div class="seg" id="r-motion">
            ${[["any", "ทั้งสองทิศ"], ["approach", "วิ่งเข้าหากล้อง (ป้ายหน้า)"], ["leave", "วิ่งออกจากกล้อง (ป้ายหลัง)"]].map(([v, t]) => `<button data-v="${v}" class="${(L.motion || "any") === v ? "on" : ""}">${t}</button>`).join("")}</div></div>
          ${range("r-wait", "เวลารอเก็บผลอ่านเพิ่มหลังข้ามเส้น", L.post_cross_wait ?? 0.8, 0, 3, 0.1, " s", "ถ้าอ่านตรงกันครบจำนวนโหวตแล้ว ระบบจะสั่งงานทันทีโดยไม่ต้องรอ")}
          ${range("r-arm", "ระยะเวลาอ่านหลัง Loop Detector ทำงาน", L.arm_seconds, 2, 20, 1, " s")}
          ${range("r-minw", "ความกว้างป้ายขั้นต่ำที่จะอ่าน", L.min_plate_width, 30, 300, 5, " px", "ป้ายที่เล็กกว่านี้จะติดตามอย่างเดียว ยังไม่อ่าน (รอให้รถเข้าใกล้)")}
          ${range("r-maxw", "ความกว้างป้ายสูงสุด", L.max_plate_width ?? 900, 200, 2000, 20, " px", "กรองสิ่งที่ใหญ่ผิดปกติ (ป้ายโฆษณา / วัตถุใกล้กล้อง)")}
          ${range("r-fps", "อัตราการตรวจจับ/ติดตาม", L.process_fps, 2, 15, 1, " fps", "หลายกล้องบน CPU เดียวกัน แนะนำ 5-8 fps ต่อกล้อง")}
          ${saveBtn("r-lane-save", "บันทึกการตั้งค่าช่องทาง")}
        </div></div>
        <div class="card"><div class="card-h"><h3>การรู้จำ (ทุกช่องทาง)</h3></div><div class="card-b grid">
          <div class="field"><label>รูปแบบป้ายทะเบียน</label><select class="input" id="r-fmt" ${admin ? "" : "disabled"}><option value="TH" ${r.plate_format === "TH" ? "selected" : ""}>ป้ายทะเบียนไทย (ทะเบียน + จังหวัด)</option><option value="ANY" ${r.plate_format === "ANY" ? "selected" : ""}>สากล (A-Z, 0-9)</option></select></div>
          <div class="field"><label>โมเดลตรวจจับป้าย</label><select class="input" id="r-model" ${admin ? "" : "disabled"}>
            ${[["yolo-v9-t-640-license-plate-end2end", "YOLOv9-T 640 — แนะนำ"], ["yolo-v9-s-608-license-plate-end2end", "YOLOv9-S 608 — แม่นยำสูงสุด (ใช้ CPU มาก)"], ["yolo-v9-t-512-license-plate-end2end", "YOLOv9-T 512 — สมดุล"], ["yolo-v9-t-384-license-plate-end2end", "YOLOv9-T 384 — เร็ว"]]
              .map(([v, t]) => `<option value="${v}" ${v === r.detector_model ? "selected" : ""}>${t}</option>`).join("")}</select></div>
          ${range("r-votes", "จำนวนผลอ่านที่ต้องตรงกัน (Multi-frame)", r.min_votes, 1, 5, 1, " ครั้ง", "รวมความน่าจะเป็นของทุกเฟรมจากรถคันเดียวกัน ค่า 2-3 ลดการอ่านผิดได้มาก")}
          ${range("r-conf", "ความมั่นใจขั้นต่ำในการบันทึก", r.min_conf, 0.3, 0.95, 0.05)}
          ${range("r-det", "ความมั่นใจขั้นต่ำของการตรวจจับป้าย", r.min_det_conf, 0.2, 0.9, 0.05)}
          ${range("r-dedupe", "ไม่บันทึกทะเบียนเดิมซ้ำภายใน (ต่อช่องทาง)", r.dedupe_seconds, 5, 300, 5, " s")}
          ${range("r-bias", "ช่วยอ่านทะเบียนที่ลงทะเบียนไว้", r.list_bias ?? 1, 0, 3, 0.25, "", "เมื่อภาพกำกวม (เช่น ห/ท) ให้น้ำหนักกับทะเบียนที่มีในระบบ 0 = ปิด · 1 = แนะนำ")}
          ${switchHTML("r-overlay", r.overlay !== false, "พิมพ์ข้อมูล (กล้อง เวลา ทะเบียน ทิศทาง ผลลัพธ์) ลงบนภาพหลักฐาน")}
          ${saveBtn("r-save", "บันทึกการตั้งค่าการรู้จำ")}
        </div></div></div>`;
      bindCam(tabRec);
      bindRanges($("#s-body"), { "r-wait": " s", "r-arm": " s", "r-fps": " fps", "r-votes": " ครั้ง", "r-dedupe": " s", "r-minw": " px", "r-maxw": " px" });
      const hints = {
        line: "รายงานเมื่อป้ายทะเบียนเคลื่อนที่ข้ามเส้นที่กำหนด (ตั้งเส้นได้ที่แท็บ “เส้นทริกเกอร์ & พื้นที่ตรวจจับ”) — แนะนำสำหรับไม้กั้น",
        video: "รายงานทันทีที่อ่านป้ายได้มั่นใจ ไม่ต้องข้ามเส้น (เลือกทิศทางรถได้)",
        input: "เริ่มอ่านเมื่อ Alarm Input ที่ตั้งบทบาทเป็น Loop Detector ทำงาน",
        mixed: "รายงานเมื่อข้ามเส้น หรือเมื่อ Loop Detector ทำงาน แล้วแต่อย่างใดเกิดก่อน — เหมือนโหมด Mixed ของกล้อง ANPR",
      };
      const syncHint = (v) => { $("#r-mode-hint").textContent = hints[v]; $("#r-motion-wrap").classList.toggle("hidden", v === "line" || v === "mixed"); };
      segBind($("#r-mode"), syncHint);
      segBind($("#r-motion"));
      syncHint(L.trigger_mode);
      $("#r-lane-save") && ($("#r-lane-save").onclick = () => saveLane({ recognition: {
        enabled: $("#r-en").checked, trigger_mode: segVal($("#r-mode")), motion: segVal($("#r-motion")), post_cross_wait: +$("#r-wait").value,
        arm_seconds: +$("#r-arm").value, process_fps: +$("#r-fps").value, min_plate_width: +$("#r-minw").value, max_plate_width: +$("#r-maxw").value } }));
      $("#r-save") && ($("#r-save").onclick = () => save("recognition", {
        plate_format: $("#r-fmt").value, detector_model: $("#r-model").value, min_det_conf: +$("#r-det").value, min_conf: +$("#r-conf").value,
        min_votes: +$("#r-votes").value, dedupe_seconds: +$("#r-dedupe").value, list_bias: +$("#r-bias").value, overlay: $("#r-overlay").checked,
      }));
    };

    /* ---------------------------------------------------------------- line + detection area editor */
    const tabRoi = () => {
      const c = camObj();
      if (!c) { $("#s-body").innerHTML = emptyHTML("ยังไม่มีกล้อง", "camera"); return; }
      const L = c.config.recognition;
      const line = { x1: 0.15, y1: 0.62, x2: 0.85, y2: 0.62, direction: "any", ...(L.trigger_line || {}) };
      let area = (L.area && L.area.length >= 3 ? L.area : [[0, 0], [1, 0], [1, 1], [0, 1]]).map((p) => [...p]);
      const liveSrc = () => media(`/api/live.jpg?cam=${cam}&width=1600&r=${Date.now()}`);
      $("#s-body").innerHTML = camBar() + `<div class="grid" style="grid-template-columns:minmax(0,1fr) 310px">
        <div class="card"><div class="card-h"><h3>ภาพจากกล้อง</h3><span class="sub">ลากจุด A / B วางเส้น · ลากจุดสีเหลืองปรับพื้นที่ตรวจจับ · ลากจุดกลางขอบเพื่อเพิ่มมุม · ดับเบิลคลิกมุมเพื่อลบ</span>
          <div class="right"><button class="btn sm" id="roi-reload">${icon("refresh")}ภาพใหม่</button></div></div>
          <div class="card-b"><div class="roi-box" id="roi-box"><img id="roi-img" src="${liveSrc()}">
            <svg class="overlay-svg" id="area-svg" viewBox="0 0 1000 1000" preserveAspectRatio="none"></svg>
            <svg class="overlay-svg" id="line-svg" viewBox="0 0 1000 1000" preserveAspectRatio="none"></svg>
            <div id="poly-h"></div>
            <div class="line-handle" id="hA"><span>A</span></div><div class="line-handle" id="hB"><span>B</span></div></div>
            <div class="muted mono" style="font-size:11.5px;margin-top:6px" id="roi-txt"></div></div></div>
        <div class="grid" style="align-content:start">
          <div class="card"><div class="card-h"><h3>เส้นทริกเกอร์</h3></div><div class="card-b grid">
            <div class="field"><label>ทิศทางที่ให้ทริกเกอร์</label><div class="seg" id="l-dir">
              <button data-v="any" class="${line.direction === "any" ? "on" : ""}">ทั้งสองทาง</button>
              <button data-v="forward" class="${line.direction === "forward" ? "on" : ""}">ตามลูกศร A→B</button>
              <button data-v="reverse" class="${line.direction === "reverse" ? "on" : ""}">สวนลูกศร</button></div>
              <span class="hint">ลูกศรแสดงทิศที่รถต้องวิ่งผ่าน${c.lane === "both" ? " · ช่องทางเข้า-ออก: วิ่งตามลูกศร = <b>เข้า</b>, สวนลูกศร = <b>ออก</b>" : ""}</span></div>
            <div class="toolbar"><button class="btn sm" id="l-h">เส้นแนวนอน</button><button class="btn sm" id="l-swap">สลับ A/B</button></div>
            <div class="notice">${icon("info")}<div>วางเส้น <b>ก่อนถึงไม้กั้น</b> ในตำแหน่งที่ป้ายทะเบียนชัดที่สุด ระบบสั่งเปิดทันทีเมื่อป้ายข้ามเส้นและอ่านได้มั่นใจ</div></div>
          </div></div>
          <div class="card"><div class="card-h"><h3>${icon("polygon")}พื้นที่ตรวจจับ</h3></div><div class="card-b grid">
            <div class="muted" style="font-size:12px">รูปหลายเหลี่ยมครอบเฉพาะช่องจราจร AI จะสนใจเฉพาะป้ายที่อยู่ในพื้นที่นี้ — กันการอ่านรถที่จอดอยู่ หรือรถในช่องทางข้างเคียง และช่วยให้เร็วขึ้น</div>
            <div class="toolbar"><button class="btn sm" id="a-band">${icon("target")}ครอบรอบเส้น</button><button class="btn sm" id="a-full">ทั้งภาพ</button></div>
            <div class="muted" style="font-size:11.5px" id="a-info"></div></div></div>
          ${saveBtn("roi-save", "บันทึกเส้นและพื้นที่")}
        </div></div>`;
      bindCam(tabRoi);
      const box = $("#roi-box");
      const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
      const toNorm = (ev, b) => [clamp((ev.clientX - b.left) / b.width, 0, 1), clamp((ev.clientY - b.top) / b.height, 0, 1)];
      const drag = (elx, onMove, onDown) => {
        elx.onpointerdown = (e) => {
          if (!admin) return;
          e.preventDefault(); e.stopPropagation();
          const b = box.getBoundingClientRect();
          onDown && onDown(e, b);
          elx.setPointerCapture(e.pointerId);
          elx.onpointermove = (ev) => { onMove(ev, b); paint(false); };
          elx.onpointerup = () => { elx.onpointermove = null; paint(true); };
        };
      };
      const paintHandles = () => {
        const wrap = $("#poly-h");
        wrap.innerHTML = area.map((p, i) => `<div class="poly-handle" data-i="${i}" style="left:${p[0] * 100}%;top:${p[1] * 100}%" title="ลากเพื่อย้าย · ดับเบิลคลิกเพื่อลบ"></div>`).join("") +
          area.map((p, i) => { const q = area[(i + 1) % area.length]; return `<div class="poly-handle mid" data-m="${i}" style="left:${(p[0] + q[0]) * 50}%;top:${(p[1] + q[1]) * 50}%" title="ลากเพื่อเพิ่มมุม"></div>`; }).join("");
        $$(".poly-handle[data-i]", wrap).forEach((h) => {
          const i = +h.dataset.i;
          drag(h, (ev, b) => { area[i] = toNorm(ev, b); });
          h.ondblclick = () => { if (area.length > 3) { area.splice(i, 1); paint(true); } else toast("พื้นที่ต้องมีอย่างน้อย 3 มุม", "info"); };
        });
        $$(".poly-handle.mid", wrap).forEach((h) => {
          let idx = -1;
          drag(h, (ev, b) => { area[idx] = toNorm(ev, b); }, (e, b) => { idx = +h.dataset.m + 1; area.splice(idx, 0, toNorm(e, b)); });
        });
      };
      const paint = (handles = true) => {
        if (!$("#hA")) return;
        Object.assign($("#hA").style, { left: `${line.x1 * 100}%`, top: `${line.y1 * 100}%` });
        Object.assign($("#hB").style, { left: `${line.x2 * 100}%`, top: `${line.y2 * 100}%` });
        if (handles) paintHandles();
        else area.forEach((p, i) => { const h = $(`.poly-handle[data-i="${i}"]`); if (h) Object.assign(h.style, { left: `${p[0] * 100}%`, top: `${p[1] * 100}%` }); });
        const pts = area.map((p) => `${p[0] * 1000},${p[1] * 1000}`).join(" ");
        $("#area-svg").innerHTML = `<path d="M0,0 H1000 V1000 H0 Z M${area.map((p) => `${p[0] * 1000},${p[1] * 1000}`).join(" L")} Z" fill="rgba(0,0,0,.5)" fill-rule="evenodd"/>
          <polygon points="${pts}" fill="rgba(230,198,60,.06)" stroke="#e6c63c" stroke-width="2" stroke-dasharray="8 5" vector-effect="non-scaling-stroke"/>`;
        const b = box.getBoundingClientRect();
        const ax = line.x1 * 1000, ay = line.y1 * 1000, bx = line.x2 * 1000, by = line.y2 * 1000;
        const mx = (ax + bx) / 2, my = (ay + by) / 2;
        const dxp = (line.x2 - line.x1) * b.width, dyp = (line.y2 - line.y1) * b.height;
        const Lp = Math.hypot(dxp, dyp) || 1;
        const nx = (-dyp / Lp) * 40 * (1000 / (b.width || 1000)), ny = (dxp / Lp) * 40 * (1000 / (b.height || 1000));
        const arrow = (sgn) => `<line x1="${mx}" y1="${my}" x2="${mx + nx * sgn}" y2="${my + ny * sgn}" stroke="#e0313a" stroke-width="4" vector-effect="non-scaling-stroke" marker-end="url(#ah)"/>`;
        const d = segVal($("#l-dir")) || line.direction;
        $("#line-svg").innerHTML = `<defs><marker id="ah" markerWidth="8" markerHeight="8" refX="4" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="#e0313a"/></marker></defs>
          <line x1="${ax}" y1="${ay}" x2="${bx}" y2="${by}" stroke="#000" stroke-width="7" vector-effect="non-scaling-stroke" opacity=".6"/>
          <line x1="${ax}" y1="${ay}" x2="${bx}" y2="${by}" stroke="#e0313a" stroke-width="4" vector-effect="non-scaling-stroke"/>
          ${d !== "reverse" ? arrow(1) : ""}${d !== "forward" ? arrow(-1) : ""}`;
        const xs = area.map((p) => p[0]), ys = area.map((p) => p[1]);
        const cover = (Math.max(...xs) - Math.min(...xs)) * (Math.max(...ys) - Math.min(...ys));
        $("#a-info").textContent = `${area.length} มุม · AI ประมวลผล ${Math.round(cover * 100)}% ของภาพ`;
        $("#roi-txt").textContent = `เส้น A(${line.x1.toFixed(2)}, ${line.y1.toFixed(2)}) → B(${line.x2.toFixed(2)}, ${line.y2.toFixed(2)})`;
      };
      drag($("#hA"), (ev, b) => { [line.x1, line.y1] = toNorm(ev, b); });
      drag($("#hB"), (ev, b) => { [line.x2, line.y2] = toNorm(ev, b); });
      segBind($("#l-dir"), () => paint(false));
      $("#l-h").onclick = () => { const y = (line.y1 + line.y2) / 2; line.y1 = line.y2 = y; paint(); };
      $("#l-swap").onclick = () => { [line.x1, line.y1, line.x2, line.y2] = [line.x2, line.y2, line.x1, line.y1]; paint(); };
      $("#a-full").onclick = () => { area = [[0, 0], [1, 0], [1, 1], [0, 1]]; paint(); };
      $("#a-band").onclick = () => {   // lane band: from 35% above the line to 25% below, slightly wider than the line
        const x1 = clamp(Math.min(line.x1, line.x2) - 0.06, 0, 1), x2 = clamp(Math.max(line.x1, line.x2) + 0.06, 0, 1);
        const y = (line.y1 + line.y2) / 2;
        area = [[x1, clamp(y - 0.35, 0, 1)], [x2, clamp(y - 0.35, 0, 1)], [x2, clamp(y + 0.25, 0, 1)], [x1, clamp(y + 0.25, 0, 1)]];
        paint();
      };
      $("#roi-reload").onclick = () => ($("#roi-img").src = liveSrc());
      $("#roi-img").onload = () => paint();
      $("#roi-img").onerror = () => { $("#roi-txt").textContent = "ยังไม่มีภาพจากกล้องนี้"; paint(); };
      roObs?.disconnect();
      roObs = new ResizeObserver(() => paint(false));
      roObs.observe(box);
      paint();
      $("#roi-save") && ($("#roi-save").onclick = () => saveLane({ recognition: {
        trigger_line: { ...line, direction: segVal($("#l-dir")) },
        area: area.map((p) => [+p[0].toFixed(4), +p[1].toFixed(4)]) } }, "บันทึกเส้นและพื้นที่ตรวจจับแล้ว"));
    };

    /* ---------------------------------------------------------------- access policy */
    const tabAccess = () => {
      const a = s.access;
      $("#s-body").innerHTML = `<div class="card" style="max-width:860px"><div class="card-h"><h3>นโยบายการเข้า-ออก</h3></div><div class="card-b grid" style="gap:16px">
        <div class="field"><label>รถที่ไม่ได้ลงทะเบียน</label><div class="seg" id="a-unknown"><button data-v="deny" class="${a.unknown_action === "deny" ? "on" : ""}">ปฏิเสธ (แนะนำ)</button><button data-v="open" class="${a.unknown_action === "open" ? "on" : ""}">เปิดให้ผ่านและบันทึก</button></div></div>
        ${switchHTML("a-auto", a.auto_open, "<b>เปิดไม้กั้นอัตโนมัติ</b> — สวิตช์หลัก ปิดเพื่อบันทึกอย่างเดียวโดยไม่สั่งงาน I/O")}
        ${switchHTML("a-fuzzy", a.fuzzy_match, "<b>Fuzzy Matching</b> — ยอมให้ต่างกัน 1 ตัวอักษรเมื่อเทียบรายชื่อ (เพิ่มอัตราผ่าน แต่ลดความปลอดภัย)")}
        ${switchHTML("a-alarm", a.blocklist_alarm, "<b>แจ้งเตือนบัญชีดำ</b> — Pulse ไปยัง Alarm Output ที่กำหนดเป็น “ไซเรน” ของช่องทางนั้น")}
        <div class="notice">${icon("info")}<div>ลำดับการตัดสินสิทธิ์: ทะเบียนถูกระงับ → วันที่มีผล → บัญชีดำ → ช่องทางที่กลุ่มได้รับอนุญาต → ตารางเวลาของกลุ่ม → อนุญาตและสั่งเปิดไม้กั้นของช่องทางนั้น</div></div>
        ${saveBtn("a-save")}</div></div>`;
      segBind($("#a-unknown"));
      $("#a-save") && ($("#a-save").onclick = () => save("access", { unknown_action: segVal($("#a-unknown")), auto_open: $("#a-auto").checked, fuzzy_match: $("#a-fuzzy").checked, blocklist_alarm: $("#a-alarm").checked }));
    };

    /* ---------------------------------------------------------------- system */
    const tabSystem = () => {
      const y = s.system, io = s.io;
      $("#s-body").innerHTML = `<div class="grid g-2">
        <div class="card"><div class="card-h"><h3>ทั่วไป</h3></div><div class="card-b grid">
          <div class="field"><label>ชื่อไซต์</label><input class="input" id="y-site" value="${esc(y.site_name)}" ${admin ? "" : "disabled"}></div>
          ${range("y-ret", "เก็บประวัติเหตุการณ์และภาพ", y.retention_days, 7, 365, 1, " วัน", "ภาพในชุดข้อมูลสอน AI จะไม่ถูกลบตามระยะนี้")}
          ${range("y-lw", "ความละเอียดภาพสดบนเว็บ", y.live_width, 640, 1920, 160, " px")}
          ${range("y-lq", "คุณภาพภาพสด (JPEG)", y.live_quality, 40, 95, 1, "")}
        </div></div>
        <div class="card"><div class="card-h"><h3>Alarm I/O</h3></div><div class="card-b grid">
          ${range("y-poll", "ความถี่อ่านสถานะ Alarm Input (สำรอง)", io.poll_ms, 100, 2000, 50, " ms", "ระบบรับเหตุการณ์ Input จาก Event Stream ของกล้องแบบทันที ค่านี้ใช้เป็นช่องทางสำรอง")}
          <div class="notice">${icon("info")}<div>ช่อง Output ไม้กั้น / ไซเรน และระยะเวลา Pulse ตั้งแยกแต่ละกล้องที่หน้า <a href="#/device">กล้อง & I/O</a></div></div>
        </div></div></div>
        <div class="card" style="margin-top:12px"><div class="card-h"><h3>เกี่ยวกับระบบ</h3></div><div class="card-b"><dl class="kv" style="grid-template-columns:170px 1fr">
          <dt>ซอฟต์แวร์</dt><dd>AI-LPR v1.2</dd><dt>AI Engine</dt><dd>YOLOv9 License Plate Detector (ONNX) + Thai CTC OCR + Multi-frame consensus + Plate grammar</dd>
          <dt>สถานะ AI</dt><dd class="mono">${esc(App.status?.engine.status || "-")} · ${esc(App.status?.engine.model || "")} · OCR ${esc(App.status?.engine.ocr_model || "base")}</dd>
          <dt>กล้อง</dt><dd>${App.cams.length} ตัว · Hikvision ISAPI</dd></dl></div></div>
        ${saveBtn("y-save")}`;
      bindRanges($("#s-body"), { "y-ret": " วัน", "y-lw": " px", "y-poll": " ms" });
      $("#y-save") && ($("#y-save").onclick = async () => {
        await save("system", { site_name: $("#y-site").value, retention_days: +$("#y-ret").value, live_width: +$("#y-lw").value, live_quality: +$("#y-lq").value }, true);
        await save("io", { poll_ms: +$("#y-poll").value });
      });
    };

    const tabs = { rec: tabRec, roi: tabRoi, access: tabAccess, system: tabSystem };
    const open = (t) => { roObs?.disconnect(); $$("#s-tabs button").forEach((x) => x.classList.toggle("on", x.dataset.t === t)); (tabs[t] || tabRec)(); };
    $$("#s-tabs button").forEach((b) => (b.onclick = () => open(b.dataset.t)));
    Promise.all([api("/api/settings"), loadCams()]).then(([r]) => {
      s = App.settings = r;
      if (!App.cams.some((c) => c.id === cam)) cam = App.cams[0]?.id;
      open(tabs[startTab] ? startTab : "rec");
    }).catch((e) => toast(e.message, "bad"));
    return () => roObs?.disconnect();
  },
};
