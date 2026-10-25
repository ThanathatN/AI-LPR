/* Vehicle registry */
"use strict";
App.pages.vehicles = {
  render(el) {
    const st = { q: "", group_id: "", status: "", page: 1, size: 20, selected: new Set(), items: [] };
    el.innerHTML = `
    <div class="section-title"><div><h2>ทะเบียนรถ</h2><p>จัดการรายชื่อรถ กำหนดกลุ่มสิทธิ์ และระยะเวลาที่อนุญาต</p></div>
      <div class="right">
        <button class="btn" id="v-tpl">${icon("file")}แม่แบบ CSV</button>
        <button class="btn" id="v-import">${icon("upload")}นำเข้า</button>
        <button class="btn" id="v-export">${icon("download")}ส่งออก</button>
        <button class="btn primary" id="v-add">${icon("plus")}เพิ่มทะเบียนรถ</button>
      </div></div>
    <div class="card">
      <div class="card-b toolbar" style="border-bottom:1px solid var(--line)">
        <div class="input-icon" style="width:300px">${icon("search")}<input class="input" id="v-q" placeholder="ค้นหาทะเบียน ชื่อเจ้าของ เบอร์โทร..."></div>
        <select class="input" id="v-group" style="width:200px"><option value="">ทุกกลุ่มสิทธิ์</option></select>
        <select class="input" id="v-status" style="width:170px"><option value="">ทุกสถานะ</option><option value="enabled">ใช้งาน</option><option value="disabled">ระงับ</option><option value="expired">หมดอายุ</option></select>
        <div style="flex:1"></div>
        <div id="bulk" class="toolbar hidden"><span class="muted" id="bulk-n"></span>
          <select class="input" id="bulk-group" style="width:170px"><option value="">ย้ายไปกลุ่ม...</option></select>
          <button class="btn sm" data-b="enable">เปิดใช้</button><button class="btn sm" data-b="disable">ระงับ</button>
          <button class="btn sm danger" data-b="delete">${icon("trash")}ลบ</button></div>
      </div>
      <div class="table-wrap"><table class="table"><thead><tr>
        <th style="width:36px"><input type="checkbox" id="v-all"></th><th>ทะเบียน</th><th>เจ้าของ</th><th>หน่วยงาน / ติดต่อ</th><th>ยานพาหนะ</th>
        <th>กลุ่มสิทธิ์</th><th>ระยะเวลาสิทธิ์</th><th>สถานะ</th><th>พบล่าสุด</th><th></th></tr></thead><tbody id="v-body"></tbody></table></div>
      <div id="v-pager"></div>
    </div>`;
    const canEdit = can("operator");
    ["v-add", "v-import"].forEach((id) => ($("#" + id).disabled = !canEdit));

    const fillGroups = () => {
      const opts = App.groups.map((g) => `<option value="${g.id}">${esc(g.name)}</option>`).join("");
      $("#v-group").innerHTML = `<option value="">ทุกกลุ่มสิทธิ์</option>${opts}`;
      $("#bulk-group").innerHTML = `<option value="">ย้ายไปกลุ่ม...</option>${opts}`;
    };
    const load = async () => {
      const qs = new URLSearchParams({ q: st.q, group_id: st.group_id || 0, status: st.status, page: st.page, size: st.size });
      const r = await api(`/api/vehicles?${qs}`);
      st.items = r.items;
      const today = new Date().toISOString().slice(0, 10);
      $("#v-body").innerHTML = r.items.length ? r.items.map((v) => {
        const expired = v.valid_to && v.valid_to < today;
        const status = !v.enabled ? `<span class="badge b-mute">ระงับ</span>` : expired ? `<span class="badge b-warn">หมดอายุ</span>` : `<span class="badge b-ok">ใช้งาน</span>`;
        return `<tr data-id="${v.id}"><td><input type="checkbox" data-sel="${v.id}" ${st.selected.has(v.id) ? "checked" : ""}></td>
        <td>${plateHTML(v.plate, v.province, "sm")}</td>
        <td><div style="font-weight:600">${esc(v.owner || "—")}</div><div class="muted" style="font-size:12px">${esc(v.note || "")}</div></td>
        <td><div>${esc(v.department || "—")}</div><div class="muted mono" style="font-size:12px">${esc(v.phone || "")}</div></td>
        <td><div>${VTYPE_TH[v.vehicle_type] || esc(v.vehicle_type)}</div><div class="muted" style="font-size:12px">${esc([v.brand, v.color].filter(Boolean).join(" · "))}</div></td>
        <td>${groupTag(v.group_name, v.group_color)}<div style="margin-top:3px">${v.list_type ? listBadge(v.list_type) : ""}</div></td>
        <td class="mono" style="font-size:12px">${v.valid_from || v.valid_to ? `${v.valid_from ? fmtDate(v.valid_from).slice(0, 10) : "—"}<br>${v.valid_to ? "ถึง " + fmtDate(v.valid_to).slice(0, 10) : "ไม่จำกัด"}` : `<span class="muted">ไม่จำกัด</span>`}</td>
        <td>${status}</td>
        <td class="mono muted" style="font-size:12px">${v.last_seen ? fmtDate(v.last_seen) : "—"}</td>
        <td><div class="actions">${canEdit ? `<button class="btn sm ghost icon" data-edit="${v.id}" title="แก้ไข">${icon("edit")}</button><button class="btn sm ghost icon" data-del="${v.id}" title="ลบ">${icon("trash")}</button>` : ""}</div></td></tr>`;
      }).join("") : `<tr><td colspan="10">${emptyHTML(st.q ? "ไม่พบทะเบียนที่ค้นหา" : "ยังไม่มีทะเบียนรถในระบบ — กด “เพิ่มทะเบียนรถ” เพื่อเริ่มต้น", "car")}</td></tr>`;
      $("#v-pager").replaceChildren(pager(r.total, st.page, st.size, (p) => { st.page = p; load(); }));
      $$("[data-edit]").forEach((b) => (b.onclick = () => editVehicle(st.items.find((x) => x.id == b.dataset.edit), load)));
      $$("[data-del]").forEach((b) => (b.onclick = async () => {
        const v = st.items.find((x) => x.id == b.dataset.del);
        if (!(await confirmBox("ลบทะเบียนรถ", `ต้องการลบทะเบียน <b>${esc(v.plate)} ${esc(v.province)}</b> ออกจากระบบหรือไม่?`, "ลบ"))) return;
        try { await api(`/api/vehicles/${v.id}`, { method: "DELETE" }); toast("ลบเรียบร้อย"); load(); } catch (e) { toast(e.message, "bad"); }
      }));
      $$("[data-sel]").forEach((c) => (c.onchange = () => { c.checked ? st.selected.add(+c.dataset.sel) : st.selected.delete(+c.dataset.sel); bulkUI(); }));
      bulkUI();
    };
    const bulkUI = () => {
      $("#bulk").classList.toggle("hidden", !st.selected.size || !canEdit);
      $("#bulk-n").textContent = `เลือก ${st.selected.size} รายการ`;
    };
    const bulk = async (action, extra = {}) => {
      if (action === "delete" && !(await confirmBox("ลบหลายรายการ", `ต้องการลบ ${st.selected.size} รายการที่เลือกหรือไม่?`, "ลบทั้งหมด"))) return;
      await api("/api/vehicles/bulk", { body: { ids: [...st.selected], action, ...extra } });
      st.selected.clear();
      $("#v-all").checked = false;
      toast("ดำเนินการเรียบร้อย");
      load();
    };
    $$("[data-b]").forEach((b) => (b.onclick = () => bulk(b.dataset.b)));
    $("#bulk-group").onchange = (e) => e.target.value && bulk("group", { group_id: +e.target.value });
    $("#v-all").onchange = (e) => { st.items.forEach((v) => (e.target.checked ? st.selected.add(v.id) : st.selected.delete(v.id))); load(); };
    $("#v-q").oninput = debounce((e) => { st.q = e.target.value; st.page = 1; load(); }, 300);
    $("#v-group").onchange = (e) => { st.group_id = e.target.value; st.page = 1; load(); };
    $("#v-status").onchange = (e) => { st.status = e.target.value; st.page = 1; load(); };
    $("#v-add").onclick = () => editVehicle(null, load);
    $("#v-export").onclick = () => download("/api/vehicles/export", `vehicles_${new Date().toISOString().slice(0, 10)}.csv`);
    $("#v-tpl").onclick = () => download("/api/vehicles/template", "vehicle_template.csv");
    $("#v-import").onclick = () => importDialog(load);
    loadGroups().then(fillGroups).then(load).catch((e) => toast(e.message, "bad"));
  },
};

function editVehicle(v, onSaved) {
  const isNew = !v;
  v = v || { enabled: 1, vehicle_type: "car", group_id: App.groups.find((g) => g.type === "allow")?.id };
  const gOpts = App.groups.map((g) => `<option value="${g.id}" ${g.id == v.group_id ? "selected" : ""}>${esc(g.name)} · ${LIST_TH[g.type]}</option>`).join("");
  const vOpts = Object.entries(VTYPE_TH).map(([k, t]) => `<option value="${k}" ${k === v.vehicle_type ? "selected" : ""}>${t}</option>`).join("");
  modal({
    title: isNew ? "เพิ่มทะเบียนรถ" : "แก้ไขทะเบียนรถ", icon: "car", wide: true,
    body: `
    <div style="display:grid;grid-template-columns:260px 1fr;gap:24px">
      <div style="display:flex;flex-direction:column;align-items:center;gap:14px;padding:18px;border-radius:14px;background:#0a0f17;border:1px solid var(--line)">
        <div class="muted" style="font-size:12px">ตัวอย่างป้ายทะเบียน</div>
        <div id="pv">${plateHTML(v.plate || "1กข 1234", v.province || "กรุงเทพมหานคร", "")}</div>
        <div class="notice" style="font-size:12px">${icon("info")}<div>พิมพ์ทะเบียนเช่น <b>1กข 1234</b>, <b>กข 123</b> หรือ <b>70-1234</b> ระบบจะจัดรูปแบบให้อัตโนมัติ</div></div>
      </div>
      <div class="form-grid">
        <div class="field"><label>หมายเลขทะเบียน *</label><input class="input mono" id="f-plate" value="${esc(v.plate || "")}" placeholder="1กข 1234" style="font-size:16px;font-weight:600"></div>
        <div class="field"><label>จังหวัด</label><input class="input" id="f-province" list="prov-list" value="${esc(v.province || "")}" placeholder="กรุงเทพมหานคร">
          <datalist id="prov-list">${PROVINCES.map((p) => `<option value="${p}">`).join("")}</datalist></div>
        <div class="field"><label>ชื่อเจ้าของ / ผู้ขับขี่</label><input class="input" id="f-owner" value="${esc(v.owner || "")}"></div>
        <div class="field"><label>เบอร์โทรศัพท์</label><input class="input mono" id="f-phone" value="${esc(v.phone || "")}"></div>
        <div class="field"><label>หน่วยงาน / แผนก / บ้านเลขที่</label><input class="input" id="f-dept" value="${esc(v.department || "")}"></div>
        <div class="field"><label>กลุ่มสิทธิ์ *</label><select class="input" id="f-group">${gOpts}</select></div>
        <div class="field"><label>ประเภทรถ</label><select class="input" id="f-vtype">${vOpts}</select></div>
        <div class="field" style="display:grid;grid-template-columns:1fr 1fr;gap:10px"><div class="field"><label>ยี่ห้อ / รุ่น</label><input class="input" id="f-brand" value="${esc(v.brand || "")}"></div>
          <div class="field"><label>สี</label><input class="input" id="f-color" value="${esc(v.color || "")}"></div></div>
        <div class="field"><label>วันเริ่มสิทธิ์</label><input class="input" type="date" id="f-from" value="${esc(v.valid_from || "")}"></div>
        <div class="field"><label>วันสิ้นสุดสิทธิ์</label><input class="input" type="date" id="f-to" value="${esc(v.valid_to || "")}"><span class="hint">เว้นว่าง = ไม่จำกัด</span></div>
        <div class="field full"><label>หมายเหตุ</label><input class="input" id="f-note" value="${esc(v.note || "")}"></div>
        <div class="field full">${switchHTML("f-enabled", v.enabled, "เปิดใช้งานสิทธิ์ของทะเบียนนี้")}</div>
      </div>
    </div>`,
    footer: `<button class="btn" data-close>ยกเลิก</button><button class="btn primary" id="f-save" data-busy="กำลังบันทึก...">${icon("check")}บันทึก</button>`,
    onMount: (m, close) => {
      const pv = () => ($("#pv", m).innerHTML = plateHTML($("#f-plate", m).value || "1กข 1234", $("#f-province", m).value, ""));
      $("#f-plate", m).oninput = pv;
      $("#f-province", m).oninput = pv;
      $("#f-plate", m).focus();
      $("#f-save", m).onclick = async (e) => {
        const body = {
          plate: $("#f-plate", m).value, province: $("#f-province", m).value, owner: $("#f-owner", m).value, phone: $("#f-phone", m).value,
          department: $("#f-dept", m).value, group_id: $("#f-group", m).value, vehicle_type: $("#f-vtype", m).value, brand: $("#f-brand", m).value,
          color: $("#f-color", m).value, valid_from: $("#f-from", m).value, valid_to: $("#f-to", m).value, note: $("#f-note", m).value,
          enabled: $("#f-enabled", m).checked,
        };
        try {
          await busy(e.currentTarget, () => api(isNew ? "/api/vehicles" : `/api/vehicles/${v.id}`, { method: isNew ? "POST" : "PUT", body }));
          toast(isNew ? "เพิ่มทะเบียนรถเรียบร้อย" : "บันทึกการแก้ไขเรียบร้อย");
          close();
          onSaved && onSaved();
        } catch (err) { toast(err.message, "bad"); }
      };
    },
  });
}

function importDialog(onDone) {
  modal({
    title: "นำเข้าทะเบียนรถจาก CSV", icon: "upload",
    body: `<div class="dropzone" id="dz">${icon("upload")}<div style="font-weight:600">ลากไฟล์ CSV มาวาง หรือคลิกเพื่อเลือกไฟล์</div>
      <div class="muted" style="font-size:12.5px;margin-top:4px">คอลัมน์: plate, province, owner, phone, department, vehicle_type, brand, color, group, valid_from, valid_to, enabled, note</div>
      <input type="file" accept=".csv,text/csv" id="dz-file" hidden></div><div id="imp-res" style="margin-top:14px"></div>`,
    footer: `<button class="btn" data-close>ปิด</button>`,
    onMount: (m) => {
      const dz = $("#dz", m), inp = $("#dz-file", m);
      const send = async (file) => {
        const fd = new FormData();
        fd.append("file", file);
        $("#imp-res", m).innerHTML = `<div class="muted">กำลังนำเข้า...</div>`;
        try {
          const r = await api("/api/vehicles/import", { body: fd });
          $("#imp-res", m).innerHTML = `<div class="notice">${icon("check")}<div>เพิ่มใหม่ <b>${r.added}</b> · อัปเดต <b>${r.updated}</b> รายการ
            ${r.errors.length ? `<div style="color:#fca5a5;margin-top:6px">${r.errors.map(esc).join("<br>")}</div>` : ""}</div></div>`;
          onDone && onDone();
        } catch (e) { $("#imp-res", m).innerHTML = `<div class="notice warn">${icon("alert")}${esc(e.message)}</div>`; }
      };
      dz.onclick = () => inp.click();
      inp.onchange = () => inp.files[0] && send(inp.files[0]);
      dz.ondragover = (e) => { e.preventDefault(); dz.classList.add("drag"); };
      dz.ondragleave = () => dz.classList.remove("drag");
      dz.ondrop = (e) => { e.preventDefault(); dz.classList.remove("drag"); e.dataTransfer.files[0] && send(e.dataTransfer.files[0]); };
    },
  });
}
