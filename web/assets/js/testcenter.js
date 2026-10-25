/* Test center */
"use strict";
App.pages.test = {
  render(el) {
    let cam = camPref.get("test");
    el.innerHTML = `
    <div class="section-title"><div><h2>ศูนย์ทดสอบระบบ</h2><p>ทดสอบ AI อ่านป้าย การตัดสินสิทธิ์ และการสั่งงาน I/O ได้โดยตรงจากซอฟต์แวร์</p></div>
      <div class="right"><span class="muted">กล้องที่ทดสอบ</span>${camSelect("t-cam", cam)}</div></div>
    <div class="grid" style="grid-template-columns:minmax(0,1.4fr) minmax(0,1fr)">
      <div class="grid" style="align-content:start">
        <div class="card"><div class="card-h"><h3>ทดสอบ AI อ่านป้ายทะเบียน</h3>
          <div class="right"><div class="seg" id="t-src"><button data-v="live" class="on">${icon("camera")} กล้องสด</button><button data-v="image">${icon("image")} อัปโหลดรูป</button></div></div></div>
          <div class="card-b">
            <div id="t-live"><div class="toolbar"><button class="btn primary lg" id="t-cap" data-busy="AI กำลังวิเคราะห์...">${icon("scan")}จับภาพและวิเคราะห์</button>
              ${switchHTML("t-commit", false, "บันทึกเป็นเหตุการณ์ & สั่งงาน I/O จริง")}</div></div>
            <div id="t-img" class="hidden"><div class="dropzone" id="t-dz">${icon("upload")}<div style="font-weight:600">ลากรูปภาพรถมาวาง หรือคลิกเพื่อเลือกไฟล์</div>
              <div class="muted" style="font-size:12.5px;margin-top:4px">รองรับ JPG / PNG — ใช้ทดสอบความแม่นยำก่อนติดตั้งจริง</div><input type="file" accept="image/*" id="t-file" hidden></div>
              <div class="toolbar" style="margin-top:12px">${switchHTML("t-img-commit", false, "บันทึกเป็นเหตุการณ์")}${switchHTML("t-img-gate", false, "สั่งเปิดไม้กั้นจริงหากผ่านสิทธิ์")}</div></div>
            <div id="t-result" style="margin-top:18px"></div>
          </div></div>
        <div class="card"><div class="card-h"><h3>AI Console</h3><span class="sub">ผลการอ่านทุกเฟรมแบบเรียลไทม์</span><div class="right"><button class="btn sm ghost" id="t-clear">ล้าง</button></div></div>
          <div class="card-b"><div class="console" id="t-console"></div></div></div>
      </div>
      <div class="grid" style="align-content:start">
        <div class="card"><div class="card-h"><h3>จำลองรถเข้า (Simulate)</h3><span class="sub">ไม่ต้องใช้รถจริง</span></div><div class="card-b">
          <div class="form-grid"><div class="field"><label>ทะเบียน</label><input class="input mono" id="s-plate" placeholder="1กข 1234" style="font-size:15px"></div>
          <div class="field"><label>จังหวัด</label><input class="input" id="s-prov" list="prov-list4" placeholder="กรุงเทพมหานคร"><datalist id="prov-list4">${PROVINCES.map((p) => `<option value="${p}">`).join("")}</datalist></div>
          <div class="field full">${switchHTML("s-act", false, "สั่งงาน Alarm Output จริง (เปิดไม้กั้น / ไซเรน)")}</div></div>
          <button class="btn primary block" style="margin-top:14px" id="s-go">${icon("play")}จำลองเหตุการณ์</button>
          <div id="s-res" style="margin-top:14px"></div></div></div>
        <div class="card"><div class="card-h"><h3>ทดสอบ I/O</h3><span class="sub">สถานะสด</span></div><div class="card-b">
          <div class="io-grid" id="t-io"></div>
          <div class="toolbar" style="margin-top:14px"><button class="btn success" id="t-gate">${icon("gate")}เปิดไม้กั้น</button>
            <button class="btn" id="t-arm">${icon("zap")}จำลอง Loop Trigger</button></div>
          <div class="notice" style="margin-top:14px">${icon("info")}<div>ทดสอบ Loop Detector จริง: ช็อตขา ALARM IN กับ GND ของกล้อง ไฟสถานะ IN จะติดและบันทึกลง I/O Log ทันที</div></div></div></div>
      </div>
    </div>`;

    const log = (html, cls = "") => {
      const c = $("#t-console");
      if (!c) return;
      const d = new Date();
      c.insertAdjacentHTML("afterbegin", `<div><span class="ts">${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}.${String(d.getMilliseconds()).padStart(3, "0")}</span><span class="${cls}">${html}</span></div>`);
      while (c.children.length > 300) c.lastChild.remove();
    };
    const showReads = (r) => {
      const reads = r.reads || [];
      $("#t-result").innerHTML = `
        ${r.image ? `<img class="result-img" src="${r.image}">` : ""}
        <div style="display:flex;justify-content:space-between;margin:14px 0 10px"><b>พบป้ายทะเบียน ${reads.length} ป้าย</b>${r.ms ? `<span class="mono muted">${r.ms} ms</span>` : ""}</div>
        <div class="grid">${reads.map((x) => `<div class="read-row">${x.crop ? `<img src="${x.crop}">` : ""}${plateHTML(x.text, x.province, "sm")}
          <div style="flex:1;min-width:0"><div class="mono" style="font-size:12px" title="ข้อความดิบ">${esc(x.raw)}</div>
          <div style="margin-top:6px">${confBar(x.conf)}</div><div class="muted" style="font-size:11.5px;margin-top:4px">Detector ${pct(x.det_conf)} · OCR ${pct(x.ocr_conf)} · รูปแบบ ${pct(x.format_score)}</div></div>
          ${x.decision ? `<div style="text-align:right">${decisionBadge(x.decision)}<div class="muted" style="font-size:11.5px;margin-top:4px;max-width:160px">${esc(x.reason)}</div></div>` : ""}</div>`).join("")
          || `<div class="notice warn">${icon("alert")}<div>ไม่พบป้ายทะเบียนในภาพ ลองปรับมุมกล้อง ระยะ หรือแสงสว่าง</div></div>`}</div>
        ${r.event ? `<div class="notice" style="margin-top:12px">${icon("check")}<div>บันทึกเป็นเหตุการณ์ #${r.event.id} แล้ว — ${esc(r.event.gate_action)}</div></div>` : ""}`;
    };

    segBind($("#t-src"), (v) => { $("#t-live").classList.toggle("hidden", v !== "live"); $("#t-img").classList.toggle("hidden", v !== "image"); $("#t-result").innerHTML = ""; });
    $("#t-cap").onclick = async (e) => {
      try {
        const r = await busy(e.currentTarget, () => api("/api/test/capture", { body: { commit: $("#t-commit").checked, cam } }));
        if (r.event) { r.image = null; r.reads = r.reads.map((x) => ({ ...x, decision: r.event.decision, reason: r.event.reason })); r.event = r.event; }
        showReads(r);
        log(`capture → ${r.reads.length ? r.reads.map((x) => x.text).join(", ") : "ไม่พบป้าย"}`, r.reads.length ? "ok" : "warn");
      } catch (err) { toast(err.message, "bad"); }
    };
    const dz = $("#t-dz"), file = $("#t-file");
    const sendImg = async (f) => {
      const fd = new FormData();
      fd.append("file", f);
      fd.append("commit", $("#t-img-commit").checked);
      fd.append("open_gate", $("#t-img-gate").checked);
      fd.append("cam", cam);
      $("#t-result").innerHTML = `<div class="muted" style="display:flex;gap:10px;align-items:center"><span class="spin" style="border-color:rgba(255,255,255,.2);border-top-color:var(--accent);width:16px;height:16px;border-width:2px;border-style:solid;border-radius:50%;display:inline-block;animation:spin .7s linear infinite"></span>AI กำลังวิเคราะห์ภาพ...</div>`;
      try {
        const r = await api("/api/test/image", { body: fd });
        showReads(r);
        log(`image ${esc(f.name)} → ${r.reads.map((x) => `${esc(x.text)} ${esc(x.province)} (${pct(x.conf)})`).join(", ") || "ไม่พบป้าย"}`, r.reads.length ? "ok" : "warn");
      } catch (e) { $("#t-result").innerHTML = ""; toast(e.message, "bad"); }
    };
    dz.onclick = () => file.click();
    file.onchange = () => file.files[0] && sendImg(file.files[0]);
    dz.ondragover = (e) => { e.preventDefault(); dz.classList.add("drag"); };
    dz.ondragleave = () => dz.classList.remove("drag");
    dz.ondrop = (e) => { e.preventDefault(); dz.classList.remove("drag"); e.dataTransfer.files[0] && sendImg(e.dataTransfer.files[0]); };

    $("#s-go").onclick = async () => {
      const plate = $("#s-plate").value.trim();
      if (!plate) return toast("กรุณาระบุทะเบียน", "bad");
      try {
        const ev = await api("/api/test/simulate", { body: { plate, province: $("#s-prov").value, act: $("#s-act").checked, cam } });
        const ok = ev.decision === "granted";
        $("#s-res").innerHTML = `<div class="decision ${ok ? "granted" : "denied"} flash" style="margin:0"><div class="d-ico">${icon(ok ? "check" : "x")}</div>
          <div style="min-width:0"><div class="d-title">${ok ? "อนุญาต" : "ไม่อนุญาต"} · ${esc(ev.plate)}</div><div class="d-sub">${esc(ev.reason)} · ${esc(ev.gate_action)}</div></div></div>`;
      } catch (e) { toast(e.message, "bad"); }
    };
    $("#s-plate").onkeydown = (e) => e.key === "Enter" && $("#s-go").click();
    $("#t-gate").onclick = async () => { try { await api("/api/gate/open", { body: { cam } }); toast("สั่งเปิดไม้กั้นเรียบร้อย"); } catch (e) { toast(e.message, "bad"); } };
    $("#t-arm").onclick = async () => { try { await api("/api/recognition/arm", { body: { seconds: 6, cam } }); toast("จำลอง Loop Trigger แล้ว"); } catch (e) { toast(e.message, "bad"); } };
    $("#t-clear").onclick = () => ($("#t-console").innerHTML = "");

    const paintIO = (io) => {
      if (!io || !$("#t-io")) return;
      $("#t-io").innerHTML = [...io.inputs.map((p) => `<div class="io-tile"><span class="led ${p.active ? "on-amber" : ""}"></span><div><div class="io-name">IN${p.id}</div><div class="io-sub">${p.active ? "ACTIVE" : "ปกติ"}</div></div></div>`),
        ...io.outputs.map((p) => `<div class="io-tile"><span class="led ${p.active ? "on-green" : ""}"></span><div><div class="io-name">OUT${p.id}</div><div class="io-sub">${p.active ? "ทำงาน" : "ปกติ"}</div></div>
          <button class="btn sm" data-p="${p.id}">Pulse</button></div>`)].join("") || `<div class="muted">I/O ออฟไลน์</div>`;
      $$("[data-p]", $("#t-io")).forEach((b) => (b.onclick = () => api(`/api/io/output/${b.dataset.p}?cam=${cam}`, { body: { action: "pulse" } }).then(() => toast(`Pulse OUT${b.dataset.p}`)).catch((e) => toast(e.message, "bad"))));
    };
    const loadIO = () => api(`/api/io?cam=${cam}`).then(paintIO).catch(() => {});
    $("#t-cam").onchange = () => { cam = +$("#t-cam").value; camPref.set("test", cam); loadIO(); log(`เลือกกล้อง ${esc(camName(cam))}`, "ev"); };
    loadIO();
    log("พร้อมรับข้อมูลจาก AI Engine", "ev");
    const cn = (id) => (App.cams.length > 1 ? `[${esc(camName(id))}] ` : "");
    const offs = [
      Bus.on("io", (io) => { if (io.cam === cam) paintIO(io); }),
      Bus.on("read", (r) => log(`${cn(r.cam)}read  ${esc(r.text)}  conf=${pct(r.conf)}  ${r.ms}ms`)),
      Bus.on("cross", (c) => log(`${cn(c.cam)}track #${c.track} ข้ามเส้น (${c.dir})`, "warn")),
      Bus.on("event", (e) => log(`${cn(e.camera_id)}EVENT ${esc(e.plate)} ${esc(e.province)} ${e.direction ? DIR_TH[e.direction] : ""} → ${e.decision === "granted" ? "อนุญาต" : "ปฏิเสธ"} · ${esc(e.gate_action)}`, e.decision === "granted" ? "ok" : "bad")),
      Bus.on("armed", (a) => log(`${cn(a.cam)}TRIGGER ${a.seconds}s (${esc(a.source)})`, "warn")),
      Bus.on("gate", (g) => log(`${cn(g.cam)}GATE OUT${g.port} opened by ${esc(g.user)}`, "ok")),
    ];
    return () => offs.forEach((f) => f());
  },
};
