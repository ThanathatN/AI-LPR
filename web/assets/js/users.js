/* Operator accounts */
"use strict";
App.pages.users = {
  render(el) {
    el.innerHTML = `
    <div class="section-title"><div><h2>ผู้ใช้งานระบบ</h2><p>จัดการบัญชีผู้ใช้และระดับสิทธิ์การเข้าถึงซอฟต์แวร์</p></div>
      <div class="right"><button class="btn primary" id="u-add">${icon("plus")}เพิ่มผู้ใช้</button></div></div>
    <div class="grid g-3" style="margin-bottom:18px">
      ${[["admin", "shield", "ico-blue", "จัดการได้ทุกส่วน: ผู้ใช้ กลุ่มสิทธิ์ การตั้งค่าอุปกรณ์และระบบ"],
         ["operator", "gate", "ico-green", "เฝ้าระวัง เปิดไม้กั้น จัดการทะเบียนรถ แก้ไขเหตุการณ์ และทดสอบระบบ"],
         ["viewer", "eye", "ico-cyan", "ดูภาพสด แดชบอร์ด และประวัติการเข้า-ออกเท่านั้น"]].map(([r, ic, cls, d]) =>
        `<div class="card kpi"><div class="k-top"><div class="k-ico ${cls}">${icon(ic)}</div><b style="color:var(--text)">${ROLE_TH[r]}</b><span class="badge b-mute mono" style="margin-left:auto">${r}</span></div><div class="muted" style="font-size:12.5px">${d}</div></div>`).join("")}
    </div>
    <div class="card"><div class="table-wrap"><table class="table"><thead><tr><th>ผู้ใช้</th><th>บทบาท</th><th>สถานะ</th><th>สร้างเมื่อ</th><th>เข้าใช้ล่าสุด</th><th></th></tr></thead><tbody id="u-body"></tbody></table></div></div>`;
    const load = async () => {
      const users = await api("/api/users");
      $("#u-body").innerHTML = users.map((u) => `<tr>
        <td><div style="display:flex;gap:12px;align-items:center"><span class="avatar">${esc((u.full_name || u.username)[0].toUpperCase())}</span>
          <div><div style="font-weight:600">${esc(u.full_name || u.username)}</div><div class="muted mono" style="font-size:12px">${esc(u.username)}</div></div></div></td>
        <td><span class="badge ${u.role === "admin" ? "b-info" : u.role === "operator" ? "b-ok" : "b-mute"}">${ROLE_TH[u.role]}</span></td>
        <td>${u.enabled ? `<span class="badge b-ok">ใช้งาน</span>` : `<span class="badge b-mute">ระงับ</span>`}</td>
        <td class="mono muted" style="font-size:12.5px">${fmtDate(u.created_at)}</td><td class="mono muted" style="font-size:12.5px">${u.last_login ? fmtDate(u.last_login) : "—"}</td>
        <td><div class="actions"><button class="btn sm ghost icon" data-e="${u.id}">${icon("edit")}</button>${u.id !== App.user.id ? `<button class="btn sm ghost icon" data-d="${u.id}">${icon("trash")}</button>` : ""}</div></td></tr>`).join("");
      $$("[data-e]").forEach((b) => (b.onclick = () => editUser(users.find((u) => u.id == b.dataset.e), load)));
      $$("[data-d]").forEach((b) => (b.onclick = async () => {
        const u = users.find((x) => x.id == b.dataset.d);
        if (!(await confirmBox("ลบผู้ใช้", `ลบบัญชี <b>${esc(u.username)}</b> หรือไม่?`, "ลบ"))) return;
        try { await api(`/api/users/${u.id}`, { method: "DELETE" }); toast("ลบผู้ใช้เรียบร้อย"); load(); } catch (e) { toast(e.message, "bad"); }
      }));
    };
    $("#u-add").onclick = () => editUser(null, load);
    load().catch((e) => toast(e.message, "bad"));
  },
};

function editUser(u, onSaved) {
  const isNew = !u;
  u = u || { role: "operator", enabled: 1 };
  modal({
    title: isNew ? "เพิ่มผู้ใช้งาน" : `แก้ไขผู้ใช้: ${esc(u.username)}`, icon: "user",
    body: `<div class="form-grid">
      <div class="field"><label>ชื่อผู้ใช้ (Username)</label><input class="input mono" id="u-name" value="${esc(u.username || "")}" ${isNew ? "" : "disabled"}></div>
      <div class="field"><label>ชื่อ-นามสกุล</label><input class="input" id="u-full" value="${esc(u.full_name || "")}"></div>
      <div class="field"><label>บทบาท</label><select class="input" id="u-role">${Object.entries(ROLE_TH).map(([k, v]) => `<option value="${k}" ${k === u.role ? "selected" : ""}>${v}</option>`).join("")}</select></div>
      <div class="field"><label>${isNew ? "รหัสผ่าน" : "ตั้งรหัสผ่านใหม่ (เว้นว่าง = ไม่เปลี่ยน)"}</label><input class="input" type="password" id="u-pass" autocomplete="new-password"></div>
      <div class="field full">${switchHTML("u-en", u.enabled, "เปิดใช้งานบัญชี")}</div></div>`,
    footer: `<button class="btn" data-close>ยกเลิก</button><button class="btn primary" id="u-save">${icon("check")}บันทึก</button>`,
    onMount: (m, close) => {
      $("#u-save", m).onclick = async () => {
        const body = { username: $("#u-name", m).value, full_name: $("#u-full", m).value, role: $("#u-role", m).value, password: $("#u-pass", m).value, enabled: $("#u-en", m).checked };
        try {
          await api(isNew ? "/api/users" : `/api/users/${u.id}`, { method: isNew ? "POST" : "PUT", body });
          toast("บันทึกผู้ใช้เรียบร้อย");
          close();
          onSaved();
        } catch (e) { toast(e.message, "bad"); }
      };
    },
  });
}
