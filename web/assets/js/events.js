/* Event search, I/O log, operation log */
"use strict";
App.pages.events = {
  render(el) {
    const today = new Date();
    const d0 = `${today.getFullYear()}-${pad(today.getMonth() + 1)}-${pad(today.getDate())}`;
    const st = { tab: "events", page: 1, size: 25, f: { frm: `${d0}T00:00`, to: "", plate: "", decision: "", list_type: "", source: "", cam: "0", direction: "" }, items: [] };
    el.innerHTML = `
    <div class="section-title"><div><h2>ประวัติการเข้า-ออก & บันทึกระบบ</h2><p>ค้นหาเหตุการณ์ ดูภาพหลักฐาน แก้ไขทะเบียนที่อ่านผิด และส่งออกรายงาน</p></div></div>
    <div class="tabs" id="tabs">
      <button data-t="events" class="on">${icon("car")}เหตุการณ์ยานพาหนะ</button>
      <button data-t="io">${icon("zap")}บันทึก I/O</button>
      ${can("admin") ? `<button data-t="audit">${icon("file")}บันทึกการใช้งาน (Operation Log)</button>` : ""}
    </div>
    <div id="tab-body"></div>`;

    const renderEvents = () => {
      $("#tab-body").innerHTML = `
      <div class="card">
        <div class="card-b toolbar" style="border-bottom:1px solid var(--line)">
          <div class="field"><label>ตั้งแต่</label><input class="input" type="datetime-local" id="f-frm" value="${st.f.frm}"></div>
          <div class="field"><label>ถึง</label><input class="input" type="datetime-local" id="f-to" value="${st.f.to}"></div>
          <div class="field"><label>ทะเบียน / เจ้าของ</label><div class="input-icon" style="width:220px">${icon("search")}<input class="input" id="f-plate" value="${esc(st.f.plate)}" placeholder="ค้นหา..."></div></div>
          <div class="field"><label>ผลลัพธ์</label><select class="input" id="f-decision" style="width:130px"><option value="">ทั้งหมด</option><option value="granted">อนุญาต</option><option value="denied">ปฏิเสธ</option></select></div>
          <div class="field"><label>ประเภทรายการ</label><select class="input" id="f-list" style="width:150px"><option value="">ทั้งหมด</option><option value="allow">Allowlist</option><option value="visitor">Visitor</option><option value="deny">Blocklist</option><option value="unknown">ไม่ลงทะเบียน</option></select></div>
          <div class="field"><label>แหล่งที่มา</label><select class="input" id="f-source" style="width:140px"><option value="">ทั้งหมด</option>${Object.entries(SOURCE_TH).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select></div>
          ${App.cams.length > 1 ? `<div class="field"><label>กล้อง / ช่องทาง</label>${camSelect("f-cam", st.f.cam, { all: true })}</div>` : ""}
          <div class="field"><label>ทิศทาง</label><select class="input" id="f-dir" style="width:110px"><option value="">ทั้งหมด</option><option value="in">เข้า</option><option value="out">ออก</option></select></div>
          <div class="field"><label>&nbsp;</label><div class="toolbar"><button class="btn primary" id="f-go">${icon("search")}ค้นหา</button><button class="btn" id="f-export">${icon("download")}ส่งออก CSV</button></div></div>
        </div>
        <div class="table-wrap"><table class="table"><thead><tr><th>ภาพรถ</th><th>ภาพป้าย</th><th>ทะเบียน</th><th>เจ้าของ / กลุ่ม</th><th>ช่องทาง</th><th>ผลลัพธ์</th><th>การสั่งงาน</th><th>ความแม่นยำ</th><th>เวลา</th></tr></thead><tbody id="e-body"></tbody></table></div>
        <div id="e-pager"></div>
      </div>`;
      $("#f-decision").value = st.f.decision;
      $("#f-list").value = st.f.list_type;
      $("#f-source").value = st.f.source;
      $("#f-dir").value = st.f.direction;
      const read = () => Object.assign(st.f, { frm: $("#f-frm").value, to: $("#f-to").value, plate: $("#f-plate").value, decision: $("#f-decision").value, list_type: $("#f-list").value, source: $("#f-source").value,
        cam: $("#f-cam")?.value || "0", direction: $("#f-dir").value });
      $("#f-go").onclick = () => { read(); st.page = 1; loadEvents(); };
      $("#f-plate").onkeydown = (e) => { if (e.key === "Enter") { read(); st.page = 1; loadEvents(); } };
      $("#f-export").onclick = () => { read(); download(`/api/events/export?${new URLSearchParams(st.f)}`, `lpr_events_${d0}.csv`); };
      loadEvents();
    };
    const loadEvents = async () => {
      const r = await api(`/api/events?${new URLSearchParams({ ...st.f, page: st.page, size: st.size })}`);
      st.items = r.items;
      $("#e-body").innerHTML = r.items.length ? r.items.map((e) => `<tr class="clickable" data-id="${e.id}">
        <td>${e.snapshot_url ? `<img class="thumb-lg" src="${media(e.snapshot_url)}" loading="lazy">` : `<div class="thumb-lg"></div>`}</td>
        <td>${e.plate_url ? `<img class="thumb" src="${media(e.plate_url)}" loading="lazy">` : "—"}</td>
        <td>${plateHTML(e.plate, e.province, "sm")}</td>
        <td><div style="font-weight:600">${esc(e.owner || "—")}</div><div style="margin-top:3px">${e.group_name ? groupTag(e.group_name, e.group_color) : listBadge(e.list_type)}</div></td>
        <td style="white-space:nowrap"><div>${esc(e.camera || "—")}</div><div style="margin-top:3px">${dirBadge(e.direction)}</div></td>
        <td>${decisionBadge(e.decision)}<div class="muted" style="font-size:11.5px;margin-top:4px;max-width:220px">${esc(e.reason)}</div></td>
        <td style="font-size:12.5px">${esc(e.gate_action)}</td>
        <td style="min-width:120px">${confBar(e.confidence)}</td>
        <td class="mono" style="font-size:12.5px;white-space:nowrap">${fmtDate(e.ts)}<div class="muted" style="font-size:11px">${SOURCE_TH[e.source] || e.source}</div></td></tr>`).join("")
        : `<tr><td colspan="9">${emptyHTML("ไม่พบเหตุการณ์ตามเงื่อนไข", "search")}</td></tr>`;
      $("#e-pager").replaceChildren(pager(r.total, st.page, st.size, (p) => { st.page = p; loadEvents(); }));
      $$("#e-body tr[data-id]").forEach((tr) => (tr.onclick = () => App.showEvent(st.items.find((x) => String(x.id) === tr.dataset.id), loadEvents)));
    };

    const renderLog = async (kind) => {
      $("#tab-body").innerHTML = `<div class="card"><div class="table-wrap"><table class="table"><thead id="l-head"></thead><tbody id="l-body"></tbody></table></div><div id="l-pager"></div></div>`;
      const load = async () => {
        const r = await api(`/api/${kind === "io" ? "io-logs" : "audit"}?page=${st.page}&size=50`);
        if (kind === "io") {
          $("#l-head").innerHTML = `<tr><th>เวลา</th><th>กล้อง</th><th>พอร์ต</th><th>สถานะ</th><th>แหล่งที่มา</th><th>รายละเอียด</th></tr>`;
          $("#l-body").innerHTML = r.items.map((x) => `<tr><td class="mono" style="font-size:12.5px">${fmtDate(x.ts)}:${x.ts.slice(17, 19)}</td>
            <td style="white-space:nowrap">${esc(camName(x.camera_id))}</td>
            <td><span class="badge ${x.port_type === "input" ? "b-warn" : "b-info"}">${x.port_type === "input" ? "IN" : "OUT"}${x.port_id}</span></td>
            <td class="mono">${esc(x.state)}</td><td>${esc(x.source)}</td><td class="muted">${esc(x.detail || "")}</td></tr>`).join("") || `<tr><td colspan="6">${emptyHTML("ยังไม่มีบันทึก I/O", "zap")}</td></tr>`;
        } else {
          $("#l-head").innerHTML = `<tr><th>เวลา</th><th>ผู้ใช้</th><th>การกระทำ</th><th>รายละเอียด</th></tr>`;
          $("#l-body").innerHTML = r.items.map((x) => `<tr><td class="mono" style="font-size:12.5px">${fmtDate(x.ts)}:${x.ts.slice(17, 19)}</td>
            <td><span class="group-tag">${icon("user")}${esc(x.username)}</span></td><td><span class="badge b-mute mono">${esc(x.action)}</span></td>
            <td class="muted" style="max-width:520px;overflow:hidden;text-overflow:ellipsis">${esc(x.detail || "")}</td></tr>`).join("") || `<tr><td colspan="4">${emptyHTML("ยังไม่มีบันทึก", "file")}</td></tr>`;
        }
        $("#l-pager").replaceChildren(pager(r.total, st.page, 50, (p) => { st.page = p; load(); }));
      };
      load().catch((e) => toast(e.message, "bad"));
    };

    $$("#tabs button").forEach((b) => (b.onclick = () => {
      $$("#tabs button").forEach((x) => x.classList.toggle("on", x === b));
      st.tab = b.dataset.t;
      st.page = 1;
      st.tab === "events" ? renderEvents() : renderLog(st.tab);
    }));
    renderEvents();
    const off = Bus.on("event", debounce(() => st.tab === "events" && st.page === 1 && loadEvents(), 600));
    return off;
  },
};

App.showEvent = (e, onChange) => {
  if (!e) return;
  const ok = e.decision === "granted";
  drawer({
    title: `เหตุการณ์ #${e.id}`,
    body: `
      ${e.snapshot_url ? `<img class="snap" src="${media(e.snapshot_url)}" id="ev-snap" style="aspect-ratio:auto">` : ""}
      <div style="display:flex;gap:16px;align-items:center;margin:18px 0">${plateHTML(e.plate, e.province, "lg")}
        ${e.plate_url ? `<img src="${media(e.plate_url)}" style="flex:1;min-width:0;max-height:90px;object-fit:contain;border-radius:8px;border:1px solid var(--line);background:#05070b">` : ""}</div>
      <div class="decision ${ok ? "granted" : "denied"}"><div class="d-ico">${icon(ok ? "check" : "x")}</div><div><div class="d-title">${ok ? "อนุญาตให้ผ่าน" : "ไม่อนุญาต"}</div><div class="d-sub">${esc(e.reason)}</div></div></div>
      <dl class="kv">
        <dt>วันเวลา</dt><dd class="mono">${fmtDate(e.ts)}:${(e.ts || "").slice(17, 19)}</dd>
        <dt>เจ้าของรถ</dt><dd>${esc(e.owner || "—")}</dd>
        <dt>กลุ่มสิทธิ์</dt><dd>${groupTag(e.group_name, e.group_color)} ${listBadge(e.list_type)}</dd>
        <dt>การสั่งงาน</dt><dd>${esc(e.gate_action)}</dd>
        <dt>ความแม่นยำ</dt><dd>${confBar(e.confidence)}</dd>
        <dt>ข้อความดิบ OCR</dt><dd class="mono" style="font-size:12px">${esc(e.raw_text || "—")}</dd>
        <dt>ช่องทาง</dt><dd>${esc(e.camera || "—")} ${dirBadge(e.direction)}</dd>
        <dt>แหล่งที่มา</dt><dd>${SOURCE_TH[e.source] || esc(e.source)}</dd>
        <dt>เวลาประมวลผล</dt><dd class="mono">${e.latency_ms} ms</dd>
      </dl>
      ${can("operator") ? `<div class="card" style="margin-top:20px;box-shadow:none;border:1px solid var(--line)"><div class="card-h"><h3>แก้ไขทะเบียนที่อ่านผิด</h3></div>
        <div class="card-b"><div class="form-grid"><div class="field"><label>ทะเบียนที่ถูกต้อง</label><input class="input mono" id="cx-plate" value="${esc(e.plate)}"></div>
        <div class="field"><label>จังหวัด</label><input class="input" id="cx-prov" list="prov-list3" value="${esc(e.province || "")}"><datalist id="prov-list3">${PROVINCES.map((p) => `<option value="${p}">`).join("")}</datalist></div></div>
        <div class="toolbar" style="margin-top:14px"><button class="btn primary" id="cx-save">${icon("check")}บันทึกและประเมินสิทธิ์ใหม่</button>
        ${!ok ? `<button class="btn success" id="cx-open">${icon("gate")}เปิดไม้กั้นด้วยตนเอง</button>` : ""}</div>
        <div class="muted" style="font-size:12px;margin-top:8px">${icon("brain")} ภาพป้ายที่แก้ไขจะถูกเก็บเข้าชุดข้อมูลสอน AI อัตโนมัติ (หน้า AI Training)</div></div></div>` : ""}`,
    onMount: (m, close) => {
      $("#ev-snap", m) && ($("#ev-snap", m).onclick = () => lightbox(media(e.snapshot_url)));
      $("#cx-save", m) && ($("#cx-save", m).onclick = async () => {
        try {
          await api(`/api/events/${e.id}/correct`, { body: { plate: $("#cx-plate", m).value, province: $("#cx-prov", m).value } });
          toast("แก้ไขเหตุการณ์เรียบร้อย");
          close();
          onChange && onChange();
        } catch (err) { toast(err.message, "bad"); }
      });
      $("#cx-open", m) && ($("#cx-open", m).onclick = async () => {
        try { await api("/api/gate/open", { body: { cam: e.camera_id || 0 } }); toast("สั่งเปิดไม้กั้นเรียบร้อย"); } catch (err) { toast(err.message, "bad"); }
      });
    },
  });
};
