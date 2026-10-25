/* AI Training: review reads -> build a site dataset -> fine-tune the Thai OCR -> compare & switch models */
"use strict";
App.pages.training = {
  render(el) {
    const admin = can("admin");
    el.innerHTML = `
    <div class="section-title"><div><h2>AI Training</h2><p>สอน AI ให้อ่านป้ายของหน้างานนี้แม่นขึ้น — ยืนยัน/แก้ผลอ่าน → สร้างชุดข้อมูล → เทรนต่อยอดโมเดล → เปรียบเทียบและเลือกใช้</p></div></div>
    <div class="tabs" id="ai-tabs">
      <button data-t="overview">${icon("brain")}ภาพรวม & เทรน</button>
      <button data-t="review">${icon("check")}ตรวจสอบผลอ่าน <b class="badge-count hidden" id="rv-count" style="margin-left:4px"></b></button>
      <button data-t="data">${icon("database")}ชุดข้อมูล</button>
      <button data-t="upload">${icon("upload")}นำเข้ารูปภาพ</button>
    </div><div id="ai-body"></div>`;
    let sum = null;
    const offs = [];
    const STATUS_TH = { verified: "ยืนยันแล้ว", auto: "อัตโนมัติ", excluded: "ไม่ใช้" };
    const statusBadge = (s) => `<span class="badge ${s === "verified" ? "b-ok" : s === "auto" ? "b-info" : "b-mute"}">${STATUS_TH[s] || s}</span>`;

    const loadSummary = async () => {
      sum = await api("/api/ai/summary");
      const b = $("#rv-count");
      if (b) { b.textContent = sum.review_pending; b.classList.toggle("hidden", !sum.review_pending); }
      return sum;
    };

    /* ---------------------------------------------------------------- overview + training */
    const tabOverview = async () => {
      await loadSummary();
      const c = sum.counts, t = sum.trainer;
      const usable = (c.verified || 0) + (c.auto || 0);
      const kpi = (label, val, sub = "") => `<div class="card kpi"><div class="card-b"><div class="muted" style="font-size:12px">${label}</div><div style="font-size:26px;font-weight:700" class="mono">${val}</div><div class="muted" style="font-size:11.5px">${sub}</div></div></div>`;
      $("#ai-body").innerHTML = `
      <div class="grid g-4">${kpi("ตัวอย่างที่ยืนยันแล้ว", fmtNum(c.verified || 0), "จากการแก้ไข / ยืนยันโดยเจ้าหน้าที่")}${kpi("ตัวอย่างอัตโนมัติ", fmtNum(c.auto || 0), "อ่านตรงทะเบียนในระบบ ≥ 90%")}
        ${kpi("รอตรวจสอบ", fmtNum(sum.review_pending), "ผลอ่านล่าสุดที่ยังไม่ยืนยัน")}${kpi("โมเดล OCR ที่ใช้อยู่", esc(sum.ocr_model === "base" ? "Base" : sum.ocr_model), sum.active ? "ปรับจูนจากข้อมูลหน้างาน" : "โมเดลมาตรฐาน")}</div>
      <div class="grid" style="grid-template-columns:minmax(0,1.3fr) minmax(0,1fr);margin-top:12px;align-items:start">
        <div class="grid" style="align-content:start">
          <div class="card"><div class="card-h"><h3>เทรนโมเดล</h3><span class="sub">ทำงานเบื้องหลังบน CPU ระบบอ่านป้ายยังทำงานปกติ</span></div><div class="card-b">
            <div class="form-grid">
              <div class="field"><label>จำนวนรอบ (Epochs)</label><input class="input mono" type="number" id="t-ep" min="1" max="30" value="8"></div>
              <div class="field"><label>Augmentation ต่อภาพ</label><select class="input" id="t-aug"><option value="2">×3 (เร็ว)</option><option value="3" selected>×4 (แนะนำ)</option><option value="5">×6 (ข้อมูลน้อย)</option></select></div>
              <div class="field full">${switchHTML("t-auto", true, "รวมตัวอย่างอัตโนมัติ (เพิ่มปริมาณข้อมูล)")}</div>
              <div class="field full">${switchHTML("t-act", true, "เปิดใช้งานทันทีถ้าแม่นยำกว่าโมเดลเดิม")}</div>
            </div>
            <div style="margin-top:14px" id="t-state"></div>
            ${admin ? `<div class="toolbar" style="margin-top:12px"><button class="btn primary" id="t-go" ${usable < sum.min_samples ? "disabled" : ""}>${icon("play")}เริ่มเทรน</button>
              <button class="btn danger hidden" id="t-cancel">${icon("x")}ยกเลิก</button>
              <span class="muted" style="font-size:12px">${usable < sum.min_samples ? `ต้องมีตัวอย่างอย่างน้อย ${sum.min_samples} ภาพ (มี ${usable})` : `ใช้ ${usable} ภาพ · แบ่ง 20% ไว้ทดสอบ`}</span></div>` : ""}
          </div></div>
          <div class="card"><div class="card-h"><h3>โมเดล OCR</h3><span class="sub">เลือกใช้ / ย้อนกลับได้ทุกเมื่อ</span></div>
            <div class="table-wrap"><table class="table"><thead><tr><th>โมเดล</th><th>วันที่</th><th>ข้อมูลเทรน</th><th>ความแม่นยำ (ชุดทดสอบ)</th><th></th></tr></thead><tbody>
              <tr><td><b>Base</b><div class="muted" style="font-size:11.5px">EasyOCR Thai มาตรฐาน</div></td><td>—</td><td>—</td><td>—</td>
                <td style="text-align:right">${!sum.active ? `<span class="badge b-ok">ใช้งานอยู่</span>` : admin ? `<button class="btn sm" data-act="0">ใช้โมเดลนี้</button>` : ""}</td></tr>
              ${sum.models.map((m) => `<tr><td><b class="mono">${esc(m.name)}</b><div class="muted" style="font-size:11.5px">${esc(m.note || "")}</div></td><td class="mono" style="font-size:12px">${fmtDate(m.created_at)}</td>
                <td>${m.samples} ป้าย · ทดสอบ ${m.val_samples}</td>
                <td><span class="mono">${pct(m.base_acc)} → <b style="color:${m.acc > m.base_acc ? "var(--ok)" : "var(--text)"}">${pct(m.acc)}</b></span></td>
                <td style="text-align:right;white-space:nowrap">${sum.active === m.id ? `<span class="badge b-ok">ใช้งานอยู่</span>` : admin ? `<button class="btn sm" data-act="${m.id}">ใช้โมเดลนี้</button> <button class="btn sm ghost icon" data-del="${m.id}">${icon("trash")}</button>` : ""}</td></tr>`).join("")}
            </tbody></table></div></div>
        </div>
        <div class="grid" style="align-content:start">
          <div class="card"><div class="card-h"><h3>ตัวอักษรที่อ่านผิดบ่อย</h3><span class="sub">จากการแก้ไขของเจ้าหน้าที่</span></div><div class="card-b">
            ${sum.confusion.length ? sum.confusion.map((x) => `<div class="conf-pair"><span class="cp-ch" style="color:var(--bad)">${esc(x.read)}</span>${icon("chevron")}<span class="cp-ch" style="color:var(--ok)">${esc(x.truth)}</span>
              <div class="cp-bar"><i style="width:${Math.round(x.n / sum.confusion[0].n * 100)}%"></i></div><span class="mono">${x.n}</span></div>`).join("")
              : `<div class="muted">ยังไม่มีข้อมูล — เมื่อแก้ไขทะเบียนที่อ่านผิดในหน้า “ค้นหาเหตุการณ์” หรือ “ตรวจสอบผลอ่าน” สถิติจะแสดงที่นี่</div>`}</div></div>
          <div class="card"><div class="card-h"><h3>ตัวอักษรในชุดข้อมูล</h3><span class="sub">ตัวที่มีน้อยจะเรียนรู้ได้น้อย</span></div><div class="card-b">
            <div class="letter-chips">${sum.letters.length ? sum.letters.map(([ch, n]) => `<span><b>${esc(ch)}</b>${n}</span>`).join("") : `<span class="muted" style="border:0;background:none">ยังไม่มีข้อมูล</span>`}</div></div></div>
          <div class="card"><div class="card-h"><h3>การเก็บข้อมูลอัตโนมัติ</h3></div><div class="card-b grid">
            ${switchHTML("t-autos", App.settings?.ai?.auto_samples !== false, "เก็บภาพป้ายที่อ่านตรงกับทะเบียนในระบบ (ความมั่นใจ ≥ 90%) เป็นตัวอย่างอัตโนมัติ")}
            <div class="notice">${icon("info")}<div><b>วิธีที่ได้ผลที่สุด:</b> ตรวจสอบผลอ่านที่ความมั่นใจต่ำสัก 100-300 ป้ายจากกล้องจริง แล้วกดเทรน — โมเดลจะเรียนรู้ฟอนต์ มุมกล้อง และแสงของหน้างานนี้โดยตรง</div></div></div></div>
        </div>
      </div>`;
      const paintTrain = (t) => {
        const box = $("#t-state");
        if (!box) return;
        const running = ["preparing", "training", "saving"].includes(t.state);
        $("#t-go") && $("#t-go").classList.toggle("hidden", running);
        $("#t-cancel") && $("#t-cancel").classList.toggle("hidden", !running);
        const R = t.result;
        box.innerHTML = t.state === "idle" ? `<div class="muted">ยังไม่ได้เริ่มเทรนในรอบนี้</div>` : `
          <div style="display:flex;justify-content:space-between;margin-bottom:6px"><b>${{ preparing: "กำลังเตรียมข้อมูล", training: "กำลังเทรน", saving: "กำลังบันทึก", done: "เสร็จสิ้น", error: "ผิดพลาด", cancelled: "ยกเลิกแล้ว" }[t.state] || t.state}</b><span class="mono">${Math.round((t.progress || 0) * 100)}%</span></div>
          <div class="progress"><i style="width:${Math.round((t.progress || 0) * 100)}%"></i></div>
          <div class="muted" style="font-size:12px;margin:6px 0">${esc(t.message || "")}</div>
          ${R ? `<div class="notice ${R.activated ? "" : "warn"}" style="margin:8px 0">${icon(R.activated ? "check" : "info")}<div>ความแม่นยำบนชุดทดสอบ ${pct(R.base_acc)} → <b>${pct(R.acc)}</b> ${R.activated ? "· เปิดใช้งานโมเดลใหม่แล้ว" : R.saved ? "· บันทึกไว้แต่ยังไม่เปิดใช้ (ไม่ดีกว่าเดิม)" : ""}</div></div>` : ""}
          ${t.log?.length ? `<div class="train-log">${t.log.map((l) => esc(l)).join("<br>")}</div>` : ""}`;
      };
      paintTrain(t);
      offs.push(Bus.on("train", (x) => { paintTrain(x); if (x.state === "done") setTimeout(() => $("#ai-tabs .on")?.dataset.t === "overview" && tabOverview(), 800); }));
      $("#t-go") && ($("#t-go").onclick = async () => {
        try { paintTrain(await api("/api/ai/train", { body: { epochs: +$("#t-ep").value, augment: +$("#t-aug").value, include_auto: $("#t-auto").checked, activate: $("#t-act").checked } })); } catch (e) { toast(e.message, "bad"); }
      });
      $("#t-cancel") && ($("#t-cancel").onclick = () => api("/api/ai/train/cancel", { method: "POST" }).catch((e) => toast(e.message, "bad")));
      $$("[data-act]").forEach((b) => (b.onclick = async () => {
        try { const r = await busy(b, () => api(`/api/ai/models/${b.dataset.act}/activate`, { method: "POST" })); toast(`เปลี่ยนเป็นโมเดล ${r.ocr_model}`); tabOverview(); } catch (e) { toast(e.message, "bad"); }
      }));
      $$("[data-del]").forEach((b) => (b.onclick = async () => {
        if (!(await confirmBox("ลบโมเดล", "ลบไฟล์โมเดลนี้ออกจากระบบ?", "ลบ"))) return;
        try { await api(`/api/ai/models/${b.dataset.del}`, { method: "DELETE" }); tabOverview(); } catch (e) { toast(e.message, "bad"); }
      }));
      $("#t-autos").onchange = async () => {
        if (!admin) return;
        try { App.settings.ai = await api("/api/settings/ai", { method: "PUT", body: { auto_samples: $("#t-autos").checked } }); toast("บันทึกแล้ว"); } catch (e) { toast(e.message, "bad"); }
      };
      if (!admin) $$("#ai-body input, #ai-body select").forEach((x) => (x.disabled = true));
    };

    /* ---------------------------------------------------------------- review queue */
    const rv = { page: 1, order: "conf", cam: 0 };
    const tabReview = async () => {
      $("#ai-body").innerHTML = `<div class="card"><div class="card-b toolbar" style="border-bottom:1px solid var(--line)">
          <div class="seg" id="rv-order"><button data-v="conf" class="${rv.order === "conf" ? "on" : ""}">ความมั่นใจต่ำก่อน</button><button data-v="new" class="${rv.order === "new" ? "on" : ""}">ล่าสุดก่อน</button></div>
          ${App.cams.length > 1 ? camSelect("rv-cam", rv.cam, { all: true }) : ""}
          <span class="muted" style="font-size:12px">พิมพ์ทะเบียนที่ถูกต้องแล้วกด Enter หรือ “ถูกต้อง” · ภาพที่ไม่ชัด/ไม่ใช่ป้ายให้กด “ข้าม”</span></div>
        <div class="card-b"><div class="sample-grid" id="rv-grid"></div></div><div id="rv-pager"></div></div>`;
      segBind($("#rv-order"), (v) => { rv.order = v; rv.page = 1; loadReview(); });
      $("#rv-cam") && ($("#rv-cam").onchange = () => { rv.cam = +$("#rv-cam").value; rv.page = 1; loadReview(); });
      loadReview();
    };
    const loadReview = async () => {
      const r = await api(`/api/ai/review?page=${rv.page}&size=24&order=${rv.order}&cam=${rv.cam}`);
      $("#rv-grid").innerHTML = r.items.map((e) => `<div class="sample" data-id="${e.id}">
          <img src="${media(e.plate_url)}" loading="lazy" data-zoom="${media(e.snapshot_url || e.plate_url)}">
          <div class="s-b"><input class="input" value="${esc(e.plate)}" data-plate>
            <input class="input" value="${esc(e.province || "")}" list="prov-rv" placeholder="จังหวัด" data-prov style="font-size:12.5px;font-weight:400;height:28px">
            <div class="s-meta"><span class="mono">${pct(e.confidence)}</span>·<span>${esc(e.camera || "")}</span>·<span class="mono">${fmtDate(e.ts)}</span></div>
            <div class="s-act"><button class="btn sm success" data-ok>${icon("check")}ถูกต้อง</button><button class="btn sm" data-skip>ข้าม</button></div></div></div>`).join("")
        || `<div style="grid-column:1/-1">${emptyHTML("ไม่มีผลอ่านที่รอตรวจสอบ", "check")}</div>`;
      if (!$("#prov-rv")) document.body.insertAdjacentHTML("beforeend", `<datalist id="prov-rv">${PROVINCES.map((p) => `<option value="${p}">`).join("")}</datalist>`);
      $("#rv-pager").replaceChildren(pager(r.total, rv.page, 24, (p) => { rv.page = p; loadReview(); }));
      $$("#rv-grid .sample").forEach((card) => {
        const id = card.dataset.id;
        const send = async (action) => {
          try {
            await api(`/api/ai/review/${id}`, { body: { action, plate: $("[data-plate]", card).value, province: $("[data-prov]", card).value } });
            card.classList.add("done");
            loadSummary();
            const next = card.nextElementSibling?.querySelector("[data-plate]");
            next && next.focus();
          } catch (e) { toast(e.message, "bad"); }
        };
        $("[data-ok]", card).onclick = () => send("confirm");
        $("[data-skip]", card).onclick = () => send("skip");
        $("[data-plate]", card).onkeydown = (ev) => { if (ev.key === "Enter") send("confirm"); };
        $("img", card).onclick = (ev) => lightbox(ev.target.dataset.zoom);
      });
    };

    /* ---------------------------------------------------------------- dataset */
    const ds = { page: 1, status: "", q: "" };
    const tabData = () => {
      $("#ai-body").innerHTML = `<div class="card"><div class="card-b toolbar" style="border-bottom:1px solid var(--line)">
          <div class="seg" id="ds-st">${[["", "ทั้งหมด"], ["verified", "ยืนยันแล้ว"], ["auto", "อัตโนมัติ"], ["excluded", "ไม่ใช้"]].map(([v, t]) => `<button data-v="${v}" class="${ds.status === v ? "on" : ""}">${t}</button>`).join("")}</div>
          <div class="input-icon" style="width:220px">${icon("search")}<input class="input" id="ds-q" placeholder="ค้นหาทะเบียน..." value="${esc(ds.q)}"></div></div>
        <div class="card-b"><div class="sample-grid" id="ds-grid"></div></div><div id="ds-pager"></div></div>`;
      segBind($("#ds-st"), (v) => { ds.status = v; ds.page = 1; loadData(); });
      $("#ds-q").oninput = debounce(() => { ds.q = $("#ds-q").value; ds.page = 1; loadData(); }, 350);
      loadData();
    };
    const loadData = async () => {
      const r = await api(`/api/ai/samples?page=${ds.page}&size=48&status=${ds.status}&q=${encodeURIComponent(ds.q)}`);
      $("#ds-grid").innerHTML = r.items.map((x) => `<div class="sample" data-id="${x.id}">
          <img src="${media(x.image_url)}" loading="lazy">
          <div class="s-b"><input class="input" value="${esc(x.label_text)}" data-plate ${can("operator") ? "" : "disabled"}>
            <div class="s-meta">${statusBadge(x.status)}${x.read_key && x.read_key !== x.label ? `<span>อ่านได้: <s>${esc(x.read_key)}</s></span>` : ""}<span class="mono">${fmtDate(x.created_at)}</span></div>
            ${can("operator") ? `<div class="s-act"><button class="btn sm" data-save>${icon("check")}บันทึก</button>
              <button class="btn sm" data-toggle>${x.status === "excluded" ? "ใช้" : "ไม่ใช้"}</button><button class="btn sm ghost icon" data-del>${icon("trash")}</button></div>` : ""}</div></div>`).join("")
        || `<div style="grid-column:1/-1">${emptyHTML("ยังไม่มีตัวอย่าง", "database")}</div>`;
      $("#ds-pager").replaceChildren(pager(r.total, ds.page, 48, (p) => { ds.page = p; loadData(); }));
      $$("#ds-grid .sample").forEach((card) => {
        const id = card.dataset.id, x = r.items.find((i) => String(i.id) === id);
        $("img", card).onclick = (ev) => lightbox(ev.target.src);
        $("[data-save]", card) && ($("[data-save]", card).onclick = async () => {
          try { await api(`/api/ai/samples/${id}`, { method: "PUT", body: { plate: $("[data-plate]", card).value, status: "verified" } }); toast("บันทึกแล้ว"); loadData(); } catch (e) { toast(e.message, "bad"); }
        });
        $("[data-toggle]", card) && ($("[data-toggle]", card).onclick = async () => {
          try { await api(`/api/ai/samples/${id}`, { method: "PUT", body: { status: x.status === "excluded" ? "verified" : "excluded" } }); loadData(); } catch (e) { toast(e.message, "bad"); }
        });
        $("[data-del]", card) && ($("[data-del]", card).onclick = async () => {
          try { await api(`/api/ai/samples/${id}`, { method: "DELETE" }); card.remove(); } catch (e) { toast(e.message, "bad"); }
        });
      });
    };

    /* ---------------------------------------------------------------- upload */
    const tabUpload = () => {
      $("#ai-body").innerHTML = `<div class="grid g-2" style="align-items:start">
        <div class="card"><div class="card-h"><h3>นำเข้ารูปป้ายทะเบียน</h3><span class="sub">ภาพเต็มหรือภาพป้ายอย่างเดียวก็ได้</span></div><div class="card-b grid">
          <div class="form-grid"><div class="field"><label>ทะเบียนที่ถูกต้อง *</label><input class="input mono" id="up-plate" placeholder="เช่น 1กข 1234" style="font-size:15px"></div>
            <div class="field"><label>จังหวัด</label><input class="input" id="up-prov" list="prov-up" placeholder="กรุงเทพมหานคร"><datalist id="prov-up">${PROVINCES.map((p) => `<option value="${p}">`).join("")}</datalist></div></div>
          <div class="dropzone" id="up-dz">${icon("upload")}<div style="font-weight:600">ลากรูปมาวาง หรือคลิกเพื่อเลือก</div><div class="muted" style="font-size:12.5px;margin-top:4px">ระบบจะตัดเฉพาะป้ายทะเบียนให้อัตโนมัติ</div><input type="file" accept="image/*" id="up-file" hidden></div>
          <div class="notice">${icon("info")}<div>เหมาะสำหรับเพิ่มตัวอย่างตัวอักษรที่หายาก (เช่น ฎ ฏ ฬ ฮ) หรือป้ายจากกล้องอื่น ภาพควรมาจากมุมกล้องจริงเพื่อผลดีที่สุด</div></div></div></div>
        <div class="card"><div class="card-h"><h3>ผลการนำเข้า</h3></div><div class="card-b" id="up-log"><div class="muted">ยังไม่มี</div></div></div></div>`;
      const dz = $("#up-dz"), file = $("#up-file");
      const send = async (f) => {
        if (!$("#up-plate").value.trim()) return toast("กรุณาระบุทะเบียนที่ถูกต้องก่อน", "bad");
        const fd = new FormData();
        fd.append("file", f); fd.append("plate", $("#up-plate").value); fd.append("province", $("#up-prov").value);
        try {
          const r = await api("/api/ai/samples/upload", { body: fd });
          const log = $("#up-log");
          if (log.querySelector(".muted")) log.innerHTML = "";
          log.insertAdjacentHTML("afterbegin", `<div class="check-row"><div class="ci ${r.correct ? "ico-green" : "ico-red"}">${icon(r.correct ? "check" : "x")}</div>
            <div><b>${esc(f.name)}</b><div class="muted" style="font-size:12px">ทะเบียน ${esc(r.label)} · AI อ่านได้ ${esc(r.read || "—")} ${r.correct ? "(ถูกต้อง)" : "(ผิด — ตัวอย่างนี้มีประโยชน์มากสำหรับการเทรน)"}</div></div></div>`);
          $("#up-plate").value = ""; $("#up-plate").focus();
        } catch (e) { toast(e.message, "bad"); }
      };
      dz.onclick = () => file.click();
      file.onchange = () => file.files[0] && send(file.files[0]);
      dz.ondragover = (e) => { e.preventDefault(); dz.classList.add("drag"); };
      dz.ondragleave = () => dz.classList.remove("drag");
      dz.ondrop = (e) => { e.preventDefault(); dz.classList.remove("drag"); e.dataTransfer.files[0] && send(e.dataTransfer.files[0]); };
    };

    const tabs = { overview: tabOverview, review: tabReview, data: tabData, upload: tabUpload };
    const open = (t) => {
      offs.splice(0).forEach((f) => f());
      $$("#ai-tabs button").forEach((x) => x.classList.toggle("on", x.dataset.t === t));
      Promise.resolve((tabs[t] || tabOverview)()).catch((e) => toast(e.message, "bad"));
    };
    $$("#ai-tabs button").forEach((b) => (b.onclick = () => open(b.dataset.t)));
    open("overview");
    return () => offs.forEach((f) => f());
  },
};
