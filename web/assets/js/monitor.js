/* Monitoring: camera tree + I/O | live view (1 or 2×2) | latest result, passing-vehicle records below */
"use strict";
App.pages.monitor = {
  render(el) {
    let sel = camPref.get("monitor");
    let layout = localStorage.getItem("lpr_mon_layout") || (App.cams.length > 1 ? "n4" : "n1");
    el.innerHTML = `
    <div class="monitor">
      <div class="grid res-panel" style="align-content:start">
        <div class="card"><div class="card-h"><h3>ช่องทาง / กล้อง</h3><div class="right">${can("admin") ? `<a class="btn sm ghost" href="#/device" title="เพิ่มกล้อง">${icon("plus")}</a>` : ""}</div></div>
          <div class="res-tree" id="tree"></div></div>
        <div class="card"><div class="card-h"><h3>Alarm I/O</h3><span class="sub" id="io-cam"></span><div class="right"><a class="btn sm ghost" href="#/device">${icon("settings")}</a></div></div>
          <div class="card-b"><div class="io-grid" id="io" style="grid-template-columns:1fr"></div></div></div>
      </div>

      <div class="grid" style="align-content:start">
        <div class="card video-card">
          <div class="card-h"><h3>${icon("monitor")}<span id="cam-name">Live View</span></h3><span class="sub" id="mode-lbl"></span>
            <div class="right"><div class="seg" id="lay"><button data-v="n1" title="ภาพเดียว">${icon("square")}</button><button data-v="n4" title="แบ่ง 4 จอ">${icon("grid")}</button></div>
            <button class="btn sm ghost icon" id="btn-full" title="เต็มจอ">${icon("maximize")}</button></div></div>
          <div id="views"></div>
          <div class="video-ctrl">
            <button class="btn success btn-gate" id="btn-gate" data-busy="กำลังสั่งเปิด...">${icon("gate")}เปิดไม้กั้น</button>
            <button class="btn" id="btn-capture" data-busy="กำลังอ่านป้าย...">${icon("scan")}จับภาพ & อ่านป้าย</button>
            <button class="btn" id="btn-arm">${icon("zap")}Trigger</button>
            <span class="spacer"></span>
            <a class="btn" id="btn-roi" href="#/settings?tab=roi">${icon("target")}ตั้งเส้น / พื้นที่ตรวจจับ</a>
          </div>
        </div>
        <div class="card"><div class="card-h"><h3>รถในพื้นที่ตรวจจับ</h3><span class="sub" id="tr-cam"></span></div>
          <div class="track-list" id="tracks"></div></div>
      </div>

      <div class="grid" style="align-content:start">
        <div class="card"><div class="card-h"><h3>ผลการตรวจสอบล่าสุด</h3><span class="sub mono" id="res-time"></span></div>
          <div class="card-b" id="result"></div></div>
      </div>

      <div class="card" style="grid-column:1 / -1">
        <div class="card-h"><h3>บันทึกรถผ่าน</h3><span class="sub">อัปเดตแบบเรียลไทม์</span><div class="right"><a class="btn sm" href="#/events">ค้นหาทั้งหมด</a></div></div>
        <div class="table-wrap" style="max-height:330px;overflow-y:auto"><table class="table"><thead><tr><th>เวลา</th><th>กล้อง</th><th>ทิศทาง</th><th>ภาพป้าย</th><th>ทะเบียน</th><th>จังหวัด</th><th>เจ้าของ</th><th>กลุ่ม</th><th>ผลลัพธ์</th><th>การสั่งงาน</th><th>ความแม่นยำ</th><th>ทริกเกอร์</th></tr></thead>
          <tbody id="rec-body"></tbody></table></div>
      </div>
    </div>`;

    let events = [];
    let selId = null;
    const pillTimers = {};
    const imgs = [];

    /* ---------------- live views */
    const streamUrl = (id, small) => media(`/api/live.mjpg?cam=${id}&fps=${small ? 12 : 25}${small ? "&width=960" : ""}&r=${Date.now()}`);
    const stopStreams = () => { imgs.forEach((i) => { i.onerror = null; i.src = ""; }); imgs.length = 0; };
    const paintViews = () => {
      stopStreams();
      const box = $("#views");
      const list = layout === "n1" ? App.cams.filter((c) => c.id === sel) : App.cams.filter((c) => c.enabled).slice(0, 4);
      if (layout === "n1") {
        box.innerHTML = `<div class="video-wrap" id="vw">
          <img class="live" id="live-${sel}" alt="">
          <div class="nosignal hidden" id="nosig"><div>${icon("camera")}<div style="margin-top:6px">ไม่มีสัญญาณภาพ</div><div class="muted" style="font-size:12px" id="nosig-err"></div></div></div>
          <div class="hud"><span class="tag"><span class="rec">● REC</span></span><span class="tag mono" id="hud-time"></span>
            <div class="right"><span class="tag mono" id="hud-fps"></span><span class="tag mono" id="hud-ai"></span></div></div>
          <div class="hud-bottom"><span class="read-pill" id="pill-${sel}" style="opacity:0"></span><span class="armed-banner hidden" id="armed"></span></div></div>`;
      } else {
        box.innerHTML = `<div class="cam-grid n4" id="vw">${list.map((c) => `<div class="cam-tile ${c.id === sel ? "sel" : ""}" data-cam="${c.id}">
          <img id="live-${c.id}" alt=""><div class="ct-name"><span class="dot" id="tdot-${c.id}"></span>${esc(c.name)} <span class="muted">${LANE_TH[c.lane] || ""}</span></div>
          <div class="ct-read" id="pill-${c.id}" style="opacity:0"></div></div>`).join("")
          || `<div class="cam-tile"><div class="nosignal">ยังไม่มีกล้อง</div></div>`}</div>`;
        $$(".cam-tile[data-cam]", box).forEach((t) => (t.onclick = () => select(+t.dataset.cam, false)));
      }
      list.forEach((c) => {
        const img = $(`#live-${c.id}`);
        if (!img) return;
        img.src = streamUrl(c.id, layout === "n4");
        img.onerror = () => setTimeout(() => { if (img.isConnected) img.src = streamUrl(c.id, layout === "n4"); }, 2000);
        imgs.push(img);
      });
    };

    const select = (id, repaintViews = true) => {
      sel = id;
      camPref.set("monitor", id);
      $("#btn-roi").href = `#/settings?tab=roi&cam=${id}`;
      if (repaintViews || layout === "n1") paintViews();
      else $$(".cam-tile").forEach((t) => t.classList.toggle("sel", +t.dataset.cam === id));
      paintTree(App.status);
      paintStatus(App.status);
      api(`/api/io?cam=${id}`).then((d) => { App.io[id] = d; paintIO(); }).catch(() => paintIO());
    };

    /* ---------------- result + records */
    const paintResult = (ev, flash = false) => {
      const box = $("#result");
      if (!ev) {
        box.innerHTML = `<div class="decision idle"><div class="d-ico">${icon("scan")}</div><div><div class="d-title">รอรถผ่าน</div><div class="d-sub">ระบบพร้อมตรวจจับป้ายทะเบียน</div></div></div>
          <div style="display:grid;place-items:center;padding:8px 0">${plateHTML("", "", "lg")}</div>`;
        return;
      }
      const ok = ev.decision === "granted";
      const v = ev.vehicle || {};
      $("#res-time").textContent = ev.ts;
      box.innerHTML = `
        <div class="decision ${ok ? "granted" : "denied"} ${flash ? "flash" : ""}"><div class="d-ico">${icon(ok ? "check" : ev.list_type === "deny" ? "ban" : "x")}</div>
          <div style="min-width:0"><div class="d-title">${ok ? "อนุญาตให้ผ่าน" : "ไม่อนุญาต"}</div><div class="d-sub">${esc(ev.reason)}</div></div></div>
        <div style="display:flex;justify-content:center;margin-bottom:10px">${plateHTML(ev.plate, ev.province, "lg")}</div>
        ${ev.plate_url ? `<img src="${media(ev.plate_url)}" style="width:100%;height:64px;object-fit:contain;background:#0c0c0d;border:1px solid var(--line);margin-bottom:10px">` : ""}
        <dl class="kv">
          <dt>ช่องทาง</dt><dd>${esc(ev.camera || "—")} ${dirBadge(ev.direction)}</dd>
          <dt>เจ้าของรถ</dt><dd>${esc(ev.owner || v.owner || "—")}</dd>
          <dt>กลุ่มสิทธิ์</dt><dd>${groupTag(ev.group_name, ev.group_color)} ${listBadge(ev.list_type)}</dd>
          ${v.department ? `<dt>หน่วยงาน</dt><dd>${esc(v.department)}</dd>` : ""}
          ${v.brand || v.color ? `<dt>รถ</dt><dd>${esc([VTYPE_TH[v.vehicle_type] || "", v.brand, v.color].filter(Boolean).join(" · "))}</dd>` : ""}
          <dt>การสั่งงาน</dt><dd>${esc(ev.gate_action)}</dd>
          <dt>ความแม่นยำ</dt><dd>${confBar(ev.confidence)}</dd>
          <dt>ทริกเกอร์</dt><dd>${SOURCE_TH[ev.source] || ev.source} · <span class="mono">${ev.latency_ms} ms</span></dd>
        </dl>
        ${ev.snapshot_url ? `<img class="snap" style="margin-top:10px" src="${media(ev.snapshot_url)}" id="res-snap">` : ""}
        ${!ok && can("operator") ? `<button class="btn success block" style="margin-top:10px" id="res-override">${icon("gate")}อนุญาตด้วยตนเอง (เปิดไม้กั้น)</button>` : ""}`;
      $("#res-snap") && ($("#res-snap").onclick = () => lightbox(media(ev.snapshot_url)));
      $("#res-override") && ($("#res-override").onclick = (e) => openGate(e.currentTarget, ev.camera_id || sel));
    };

    const paintRecords = (newId) => {
      $("#rec-body").innerHTML = events.length ? events.map((e) => `<tr class="clickable ${e.id === selId ? "sel" : ""}" data-id="${e.id}" ${e.id === newId ? 'style="animation:flash .8s"' : ""}>
        <td class="mono">${fmtTime(e.ts)}</td><td style="white-space:nowrap">${esc(e.camera || "—")}</td><td>${dirBadge(e.direction)}</td>
        <td>${e.plate_url ? `<img class="thumb" src="${media(e.plate_url)}" loading="lazy">` : `<div class="thumb"></div>`}</td>
        <td style="font-weight:700;font-size:14px">${esc(e.plate)}</td><td>${esc(e.province || "—")}</td>
        <td>${esc(e.owner || "—")}</td><td>${e.group_name ? groupTag(e.group_name, e.group_color) : listBadge(e.list_type)}</td>
        <td>${decisionBadge(e.decision)}</td><td style="font-size:12px">${esc(e.gate_action)}</td>
        <td style="width:120px">${confBar(e.confidence)}</td><td class="muted">${SOURCE_TH[e.source] || e.source}</td></tr>`).join("")
        : `<tr><td colspan="12">${emptyHTML("ยังไม่มีรถผ่าน", "car")}</td></tr>`;
      $$("#rec-body tr[data-id]").forEach((tr) => (tr.onclick = () => {
        selId = +tr.dataset.id;
        paintResult(events.find((x) => x.id === selId));
        paintRecords();
      }));
    };

    /* ---------------- I/O + tree + status */
    const roleTH = { barrier: "ไม้กั้น", alarm: "ไซเรน", trigger: "Loop Detector", safety: "Radar กันไม้ตี", none: "ไม่ได้กำหนด" };
    const paintIO = () => {
      const io = App.io[sel];
      if (!$("#io")) return;
      $("#io-cam").textContent = camName(sel);
      if (!io) { $("#io").innerHTML = `<div class="muted">กำลังโหลด...</div>`; return; }
      const tiles = [
        ...io.inputs.map((p) => `<div class="io-tile"><span class="led ${p.active ? "on-amber" : ""}"></span><div><div class="io-name">IN${p.id} ${esc(p.name || "")}</div><div class="io-sub">${roleTH[p.role] || p.role} · ${p.active ? "ACTIVE" : "ปกติ"}</div></div></div>`),
        ...io.outputs.map((p) => `<div class="io-tile"><span class="led ${p.active ? "on-green" : ""}"></span><div><div class="io-name">OUT${p.id} ${esc(p.name || "")}</div><div class="io-sub">${roleTH[p.role] || p.role} · ${p.active ? "ทำงาน" : "ปกติ"}</div></div>
          ${can("operator") ? `<button class="btn sm" data-pulse="${p.id}">Pulse</button>` : ""}</div>`),
      ];
      let html = io.online ? tiles.join("") || `<div class="muted">กล้องไม่มีพอร์ต I/O</div>` : `<div class="notice warn">${icon("alert")}<div>เชื่อมต่อ I/O ไม่ได้ ${esc(io.error || "")}</div></div>`;
      if (io.conflicts?.length) html = `<div class="notice bad">${icon("alert")}<div>กล้องมี Linkage สั่ง Alarm Output เอง <a href="#/device">แก้ไข</a></div></div>` + html;
      if (io.safety_hold) html = `<div class="notice warn">${icon("radar")}<div>Radar พบวัตถุ — ค้างไม้กั้น</div></div>` + html;
      $("#io").innerHTML = html;
      $$("[data-pulse]", $("#io")).forEach((b) => (b.onclick = async () => {
        try { await api(`/api/io/output/${b.dataset.pulse}?cam=${sel}`, { body: { action: "pulse" } }); toast(`ส่งสัญญาณ OUT${b.dataset.pulse}`); } catch (e) { toast(e.message, "bad"); }
      }));
    };

    const paintTree = (s) => {
      if (!$("#tree")) return;
      const groups = { entry: [], exit: [], both: [] };
      App.cams.forEach((c) => groups[c.lane || "entry"].push(c));
      $("#tree").innerHTML = `<div class="group">${esc(App.settings?.system?.site_name || "ไซต์หลัก")}</div>` +
        Object.entries(groups).filter(([, l]) => l.length).map(([lane, list]) => `<div class="node">${icon("layers")}<span>${LANE_TH[lane]}</span></div>` +
          list.map((c) => {
            const st = (s?.cameras || []).find((x) => x.id === c.id);
            const ok = st?.video?.connected;
            return `<div class="node child cam ${c.id === sel ? "sel" : ""}" data-cam="${c.id}"><span class="dot ${!c.enabled ? "" : ok ? "ok" : "bad"}"></span>${icon("camera")}
              <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(c.name)}</span>${st?.tracks?.length ? `<span class="badge b-warn">${st.tracks.length}</span>` : ""}</div>`;
          }).join("")).join("") || `<div class="node muted">ยังไม่มีกล้อง</div>`;
      $$("#tree .node.cam").forEach((n) => (n.onclick = () => select(+n.dataset.cam, layout === "n1")));
    };

    const paintStatus = (s) => {
      if (!s || !$("#views")) return;
      paintTree(s);
      (s.cameras || []).forEach((c) => { const d = $(`#tdot-${c.id}`); if (d) d.className = `dot ${c.video.connected ? "ok" : "bad"}`; });
      const c = (s.cameras || []).find((x) => x.id === sel);
      if (!c) return;
      $("#cam-name").textContent = c.name;
      $("#tr-cam").textContent = c.name;
      if ($("#hud-time")) {
        $("#hud-time").textContent = s.time;
        $("#hud-fps").textContent = `${c.video.width}×${c.video.height} · ${c.video.fps} fps`;
        $("#hud-ai").textContent = s.engine.status === "ready" ? `AI ${c.recognition.proc_fps} fps` : `AI ${s.engine.status}`;
        $("#nosig").classList.toggle("hidden", c.video.connected);
        $("#nosig-err").textContent = c.video.error || "";
        const a = $("#armed");
        const mode = c.recognition.mode;
        a.classList.toggle("hidden", mode !== "input" || !c.recognition.armed);
        a.textContent = `TRIGGER ACTIVE · ${c.recognition.armed_left}s`;
      }
      const mode = c.recognition.mode;
      $("#mode-lbl").textContent = `${LANE_TH[c.lane] || ""} · ทริกเกอร์: ${MODE_TH[mode] || mode}${c.recognition.enabled ? "" : " (ปิดการอ่าน)"}`;
      $("#btn-arm").classList.toggle("hidden", !["input", "mixed"].includes(mode));
      const motionTH = { approach: "เข้าหากล้อง", leave: "ออกจากกล้อง" };
      const tr = c.tracks || [];
      $("#tracks").innerHTML = tr.length ? tr.map((t) => `<div class="tl"><span class="badge b-mute mono">#${t.id}</span>
        <b style="min-width:120px">${esc(t.label || "กำลังอ่าน...")}</b><span class="muted">อ่านแล้ว ${t.reads} ครั้ง${t.motion ? ` · ${motionTH[t.motion]}` : ""}</span>
        <span style="margin-left:auto">${t.result === "granted" ? decisionBadge("granted") : t.result === "denied" ? decisionBadge("denied") : t.result === "dup" ? `<span class="badge b-mute">ซ้ำ</span>` : t.result === "skip" ? `<span class="badge b-mute">ทิศทางไม่ตรง</span>` : t.crossed ? `<span class="badge b-warn">${t.dir === "input" ? "Loop ทำงาน" : "ข้ามเส้นแล้ว"}</span>` : `<span class="badge b-info">ติดตาม</span>`}</span></div>`).join("")
        : `<div class="tl muted">ไม่มีรถในพื้นที่ตรวจจับ</div>`;
    };

    const openGate = async (btn, cam = sel) => {
      try { await busy(btn, () => api("/api/gate/open", { body: { cam } })); toast(`สั่งเปิดไม้กั้น — ${camName(cam)}`); } catch (e) { toast(e.message, "bad"); }
    };
    $("#btn-gate").onclick = (e) => openGate(e.currentTarget);
    $("#btn-gate").disabled = !can("operator");
    $("#btn-capture").onclick = async (e) => {
      try {
        const r = await busy(e.currentTarget, () => api("/api/test/capture", { body: { commit: true, cam: sel } }));
        if (!r.found) toast("ไม่พบป้ายทะเบียนในภาพขณะนี้", "info");
      } catch (err) { toast(err.message, "bad"); }
    };
    $("#btn-arm").onclick = async () => { try { await api("/api/recognition/arm", { body: { seconds: 6, cam: sel } }); } catch (e) { toast(e.message, "bad"); } };
    $("#btn-full").onclick = () => $("#vw")?.requestFullscreen?.();
    $$("#lay button").forEach((b) => b.classList.toggle("on", b.dataset.v === layout));
    segBind($("#lay"), (v) => { layout = v; localStorage.setItem("lpr_mon_layout", v); paintViews(); });

    paintResult(null);
    select(sel);
    api("/api/events?size=30").then((r) => { events = r.items; paintRecords(); if (events[0]) paintResult(events[0]); }).catch(() => {});

    const offs = [
      Bus.on("event", (ev) => { events = [ev, ...events].slice(0, 50); selId = ev.id; paintRecords(ev.id); paintResult(ev, true); }),
      Bus.on("io", (d) => { if (d.cam === sel) paintIO(); }),
      Bus.on("status", paintStatus),
      Bus.on("read", (r) => {
        const p = $(`#pill-${r.cam}`);
        if (!p) return;
        p.textContent = `#${r.track} ▸ ${r.text}  ${pct(r.conf)}  ${r.ms}ms`;
        p.style.opacity = "1";
        clearTimeout(pillTimers[r.cam]);
        pillTimers[r.cam] = setTimeout(() => (p.style.opacity = "0"), 1500);
      }),
    ];
    return () => { offs.forEach((f) => f()); stopStreams(); };
  },
};
