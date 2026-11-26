"use strict";
// minicloud web console. No framework and no build step: the control plane serves these files.
// Every piece of data from the API goes into the page through DOM nodes and textContent, never
// as HTML, and the API key lives in localStorage (not in a cookie the customers' apps could see).

const KEY_STORAGE = "minicloud.apiKey";
const TRANSITIONAL = new Set(["pending", "provisioning", "starting", "stopping", "deleting"]);
const STATUS_TEXT = {
  pending: "Đang chờ",
  provisioning: "Đang tạo",
  running: "Đang chạy",
  starting: "Đang bật",
  stopping: "Đang dừng",
  stopped: "Đã dừng",
  deleting: "Đang xoá",
  deleted: "Đã xoá",
  error: "Lỗi",
};
const ERROR_TEXT = {
  quota_exceeded: "Vượt giới hạn của project",
  insufficient_credits: "Tổ chức đã hết credit",
  conflict: "Chưa làm được",
  forbidden: "Không được phép",
  unauthorized: "API key không hợp lệ",
  not_found: "Không tìm thấy",
  rate_limited: "Thử quá nhiều lần, hãy chờ một lúc rồi thử lại",
  invalid_month: "Tháng không hợp lệ",
};

const $ = (id) => document.getElementById(id);
const state = {
  org: null, project: null, timer: null, meter: null, meterTimer: null, pricing: null, greenAt: 0,
  term: null, admin: false, providerAt: 0,
};

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs)) {
    if (name === "class") node.className = value;
    else if (name.startsWith("on")) node.addEventListener(name.slice(2), value);
    else node.setAttribute(name, value);
  }
  for (const child of children) {
    if (child !== null && child !== undefined && child !== "") node.append(child);
  }
  return node;
}

const fmt = (n) => Number(n).toLocaleString("vi-VN", { maximumFractionDigits: 2 });
const fmt3 = (n) => Number(n).toLocaleString("vi-VN", { minimumFractionDigits: 3, maximumFractionDigits: 3 });
const vnd = (n) => `${Math.round(n).toLocaleString("vi-VN")} đ`;
const credit = (n) => `${fmt(n)} credit`;
const monthLabel = (month, final) => `Tháng ${Number(month.slice(5))}/${month.slice(0, 4)}${final ? "" : " (đang chạy)"}`;

async function api(method, path, body) {
  const headers = { Accept: "application/json" };
  const key = localStorage.getItem(KEY_STORAGE);
  if (key) headers.Authorization = `Bearer ${key}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const resp = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data = null;
  try {
    data = await resp.json();
  } catch {
    data = null;
  }
  if (!resp.ok) {
    const code = data?.error?.code;
    let message = data?.error?.message;
    if (!message && Array.isArray(data?.detail)) message = data.detail.map((d) => d.msg).join("; ");
    if (!message) message = `HTTP ${resp.status}`;
    const err = new Error(ERROR_TEXT[code] ? `${ERROR_TEXT[code]}: ${message}` : message);
    err.status = resp.status;
    throw err;
  }
  return data;
}

function banner(text, kind) {
  const node = $("banner");
  node.textContent = text;
  node.className = `banner ${kind}`;
  node.hidden = false;
}

function clearBanner() {
  $("banner").hidden = true;
}

function showError(err) {
  banner(err.message || String(err), "error");
  if (err.status === 401) logout(false);
}

function show(view) {
  for (const id of ["auth", "new-key", "dash"]) $(id).hidden = id !== view;
  $("logout").hidden = view !== "dash";
  if (view === "auth") loadPricing().catch(() => {}); // the price list is a nice-to-have here
}

function logout(clear = true) {
  localStorage.removeItem(KEY_STORAGE);
  clearTimeout(state.timer);
  clearInterval(state.meterTimer);
  state.meter = null;
  state.greenAt = 0;
  state.providerAt = 0;
  state.admin = false;
  $("provider").hidden = true;
  state.term = null;
  $("terminal").hidden = true;
  $("term-output").replaceChildren();
  $("who").textContent = "";
  if (clear) clearBanner();
  show("auth");
}

async function getPricing() {
  if (!state.pricing) state.pricing = await api("GET", "/v1/catalog/pricing");
  return state.pricing;
}

async function loadPricing() {
  if ($("price-rows").children.length > 0) return;
  const [flavors, pricing] = await Promise.all([api("GET", "/v1/catalog/flavors"), getPricing()]);
  for (const f of flavors) {
    $("price-rows").append(el("tr", {},
      el("td", {}, f.name),
      el("td", {}, String(f.vcpus)),
      el("td", {}, `${fmt(f.memory_mib / 1024)} GB`),
      el("td", {}, `${f.disk_gib} GB`),
      el("td", {}, `${fmt(f.credits_per_hour_container)}/giờ`),
      el("td", {}, `${fmt(f.credits_per_hour_vm)}/giờ`),
      el("td", {}, `${fmt(f.credits_per_hour_when_stopped)}/giờ`)));
  }
  const small = flavors[0];
  const monthly = small.credits_per_hour_container * pricing.hours_per_month;
  const notes = [
    "Giá tính bằng credit mỗi giờ, trừ theo từng giây máy chạy, tính từ lúc máy chạy xong.",
    `Hệ thống ngừng quá ${fmt(pricing.outage_free_after_minutes)} phút (laptop tắt hoặc ngủ) thì máy đang chạy không bị tính tiền cho khoảng đó.`,
    `Máy ảo giá gấp ${fmt(pricing.vm_price_percent / 100)} lần container. Máy đang dừng chỉ trả tiền ổ đĩa: ${fmt(pricing.storage_credits_per_gib_hour)} credit mỗi GB mỗi giờ.`,
    `Đăng ký được ${fmt(pricing.trial_credits)} credit dùng thử. Một container ${small.name} chạy cả tháng hết ${fmt(monthly)} credit.`,
    `Quy đổi tham khảo: 1 credit ≈ ${fmt(pricing.vnd_per_credit)} đồng, để so với giá của nhà cung cấp thật (${small.name} cả tháng ≈ ${vnd(monthly * pricing.vnd_per_credit)}). Credit không phải tiền thật; không ai phải trả tiền.`,
    "Hết credit thì các máy bị dừng; nhóm vận hành nạp thêm khi cần.",
  ];
  $("price-notes").replaceChildren(...notes.map((text) => el("li", {}, text)));
}

async function enterDashboard() {
  const me = await api("GET", "/v1/me");
  const orgs = await api("GET", "/v1/orgs");
  state.org = orgs[0];
  const projects = await api("GET", `/v1/orgs/${state.org.id}/projects`);
  state.project = projects[0];
  $("who").textContent = `${me.name} · ${state.org.name}`;
  state.admin = me.is_platform_admin === true;
  $("provider").hidden = !state.admin;
  $("project-name").textContent = `project ${state.project.name}`;
  const owner = state.org.role === "owner";
  for (const control of $("budget-form").elements) control.disabled = !owner;
  if (!owner) $("budget-status").textContent = "Chỉ owner của tổ chức đặt được ngân sách.";
  show("dash");
  await loadCatalog();
  await loadStatementMonths();
  await refresh();
  await refreshKeys();
  clearInterval(state.meterTimer);
  state.meterTimer = setInterval(tickMeter, 1000);
}

async function loadStatementMonths() {
  const months = await api("GET", `/v1/orgs/${state.org.id}/statements`);
  const select = $("statement-month");
  const chosen = select.value;
  select.replaceChildren(...months.map((m) => el("option", { value: m.month }, monthLabel(m.month, m.final))));
  if (months.some((m) => m.month === chosen)) select.value = chosen;
}

async function refreshStatement() {
  const month = $("statement-month").value;
  if (!month) return;
  renderStatement(await api("GET", `/v1/orgs/${state.org.id}/statements/${month}`));
}

function renderStatement(st) {
  $("st-org").textContent = st.org_name;
  $("st-period").textContent = monthLabel(st.month, st.final);
  $("st-opening").textContent = credit(st.opening_credits);
  $("st-added").textContent = credit(st.added_credits);
  $("st-charged").textContent = `${credit(st.charged_credits)} ≈ ${vnd(st.charged_credits * st.vnd_per_credit)}`;
  $("st-closing").textContent = credit(st.closing_credits);
  $("st-energy").textContent = `${fmt(st.energy_wh)} Wh ≈ ${fmt(st.co2_grams)} g CO₂`;
  const tbody = $("st-lines");
  tbody.replaceChildren();
  for (const line of st.lines) {
    const kind = line.type === "virtual-machine" ? "máy ảo" : "container";
    tbody.append(el("tr", {},
      el("td", {}, line.name),
      el("td", {}, `${line.flavor} · ${kind}`),
      el("td", {}, fmt(line.compute_hours)),
      el("td", {}, fmt(line.storage_hours)),
      el("td", {}, fmt(line.credits)),
      el("td", {}, fmt(line.energy_wh))));
  }
  if (st.lines.length === 0) {
    tbody.append(el("tr", {}, el("td", { colspan: "6", class: "empty" }, "Chưa có phí nào trong tháng này.")));
  }
  $("st-note").textContent = [
    "Tháng tính theo giờ Việt Nam.",
    st.final ? "Tháng đã kết thúc, số liệu không đổi nữa." : "Tháng chưa kết thúc: số liệu được ghi sổ mỗi phút.",
    "Credit không phải tiền thật; số đồng chỉ là quy đổi tham khảo.",
    "Điện năng là ước tính: điện lúc rảnh của laptop chia theo số vCPU được cấp, phần còn lại chia theo thời gian CPU thật sự dùng.",
  ].join(" ");
}

async function downloadStatementCsv() {
  const month = $("statement-month").value;
  if (!month) return;
  const resp = await fetch(`/v1/orgs/${state.org.id}/statements/${month}.csv`, {
    headers: { Authorization: `Bearer ${localStorage.getItem(KEY_STORAGE)}` },
  });
  if (!resp.ok) throw new Error(`Không tải được sao kê (HTTP ${resp.status})`);
  const url = URL.createObjectURL(await resp.blob());
  const link = el("a", { href: url, download: `minicloud-sao-ke-${month}.csv` });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

function tickMeter() {
  const m = state.meter;
  if (!m) return;
  const value = m.base + (m.rate * (Date.now() - m.at)) / 3_600_000;
  $("mtd").textContent = fmt3(value);
  if (state.pricing) $("mtd-unit").textContent = `credit ≈ ${vnd(value * state.pricing.vnd_per_credit)}`;
}

async function saveBudget(monthlyCredits) {
  clearBanner();
  const billing = await api("PUT", `/v1/orgs/${state.org.id}/budget`, { monthly_credits: monthlyCredits });
  renderBilling(billing);
  banner(monthlyCredits === null ? "Đã bỏ ngân sách tháng." : "Đã lưu ngân sách tháng.", "info");
}

const when = (iso) => (iso ? new Date(`${iso}Z`).toLocaleString("vi-VN") : "chưa dùng"); // UTC

async function refreshKeys() {
  const keys = await api("GET", "/v1/me/api-keys");
  const ul = $("keys");
  ul.replaceChildren();
  for (const key of keys) {
    const badge = key.revoked
      ? el("span", { class: "badge" }, "Đã thu hồi")
      : key.current ? el("span", { class: "badge running" }, "Đang dùng") : null;
    const revoke = key.revoked ? null : actionButton("Thu hồi", () => revokeKey(key), "danger");
    ul.append(el("li", { class: key.revoked ? "revoked" : "" },
      el("div", { class: "grow" },
        el("strong", {}, key.name), " ", badge,
        el("div", { class: "meta" },
          `${key.prefix}… · tạo ${when(key.created_at)} · dùng lần cuối ${when(key.last_used_at)}`)),
      revoke));
  }
}

async function revokeKey(key) {
  const warning = key.current
    ? `Thu hồi key "${key.name}" đang dùng? Bảng điều khiển sẽ đăng xuất; đăng nhập lại bằng key khác.`
    : `Thu hồi key "${key.name}"? Mọi script đang dùng key này sẽ bị từ chối.`;
  if (!window.confirm(warning)) return;
  clearBanner();
  await api("DELETE", `/v1/me/api-keys/${key.id}`);
  if ($("fresh-key-value").textContent.startsWith(key.prefix)) {
    $("fresh-key-value").textContent = ""; // no point keeping a revoked key on screen
    $("fresh-key").hidden = true;
  }
  if (key.current) {
    logout(false);
    banner("Đã thu hồi key đang dùng. Đăng nhập lại bằng một key khác.", "info");
    return;
  }
  await refreshKeys();
}

async function loadCatalog() {
  if ($("flavor").options.length > 0) return;
  const [flavors, images] = await Promise.all([
    api("GET", "/v1/catalog/flavors"),
    api("GET", "/v1/catalog/images"),
    getPricing(),
  ]);
  for (const f of flavors) {
    const label = `${f.name} · ${f.vcpus} vCPU · ${f.memory_mib / 1024} GB · ${fmt(f.credits_per_hour_container)} credit/giờ`;
    $("flavor").append(el("option", { value: f.name }, label));
    $("group-flavor").append(el("option", { value: f.name }, label));
  }
  for (const image of images) $("image").append(el("option", { value: image.key }, image.title));
  if (state.pricing && state.pricing.vms_available === false) {
    const vm = $("create-form").querySelector("option[value=virtual-machine]");
    vm.disabled = true;
    vm.textContent = "Máy ảo: chưa có trên cụm này (laptop chưa bật KVM)";
  }
}

async function refresh() {
  clearTimeout(state.timer);
  const [instances, billing, usage] = await Promise.all([
    api("GET", `/v1/projects/${state.project.id}/instances`),
    api("GET", `/v1/orgs/${state.org.id}/billing`),
    api("GET", `/v1/orgs/${state.org.id}/usage?limit=20`),
  ]);
  renderInstances(instances);
  await refreshGroups();
  state.running = instances.some((i) => i.status === "running");
  renderBilling(billing);
  renderUsage(usage, instances);
  const select = $("statement-month");
  if (select.value && !Array.from(select.options).some((o) => o.value === billing.month)) {
    await loadStatementMonths(); // a new month has started since the page was opened
  }
  if (!select.value || select.value === billing.month) await refreshStatement();
  await refreshGreen();
  if (state.admin) await refreshProvider();
  const busy = instances.some((i) => TRANSITIONAL.has(i.status));
  state.timer = setTimeout(() => refresh().catch(showError), busy ? 2000 : 15000);
}

function actionButton(text, handler, cls = "") {
  return el("button", {
    type: "button",
    class: cls,
    onclick: async (event) => {
      const btn = event.currentTarget;
      btn.disabled = true;
      try {
        await handler();
      } catch (err) {
        showError(err);
      } finally {
        btn.disabled = false;
      }
    },
  }, text);
}

async function act(inst, action) {
  clearBanner();
  await api("POST", `/v1/instances/${inst.id}/${action}`);
  await refresh();
}

async function remove(inst) {
  if (!window.confirm(`Xoá máy ${inst.name}? Mọi dữ liệu trong máy sẽ mất.`)) return;
  clearBanner();
  await api("DELETE", `/v1/instances/${inst.id}`);
  await refresh();
}

function renderInstances(list) {
  const ul = $("instances");
  ul.replaceChildren();
  $("no-instances").hidden = list.length > 0;
  for (const inst of list) {
    const actions = el("div", { class: "actions" });
    if (inst.status === "running") {
      actions.append(actionButton("Chạy lệnh", () => openTerminal(inst)));
      actions.append(actionButton("Dừng", () => act(inst, "stop")));
    }
    if (inst.status === "stopped" || inst.status === "error") {
      actions.append(actionButton("Bật", () => act(inst, "start")));
    }
    if (["running", "stopped", "error"].includes(inst.status)) {
      actions.append(actionButton("Xoá", () => remove(inst), "danger"));
    }
    const kind = inst.type === "virtual-machine" ? "Máy ảo" : "Container";
    const cpu = inst.status === "running" && inst.cpu_util !== null && inst.cpu_util !== undefined
      ? ` · CPU ${fmt(inst.cpu_util * 100)}%` : "";
    const meta = el("div", { class: "meta" }, `${kind} · ${inst.flavor} · ${inst.image}`,
      inst.ip ? ` · IP ${inst.ip}` : "", cpu, inst.group_id ? " · thuộc nhóm tự co giãn" : "");
    const url = typeof inst.public_url === "string" && inst.public_url.startsWith("https://")
      ? inst.public_url : null;
    const link = url && inst.status === "running"
      ? el("a", { class: "url", href: url, target: "_blank", rel: "noopener noreferrer" },
        url.slice("https://".length))
      : null;
    ul.append(el("li", { class: "instance" },
      el("div", { class: "head" },
        el("strong", {}, inst.name),
        el("span", { class: `badge ${inst.status}` }, STATUS_TEXT[inst.status] || inst.status)),
      meta, link, actions));
  }
}

const TERM_EXAMPLES = [
  ["Xem tài nguyên", "uptime; free -h; df -h /"],
  ["Cài web server nginx", "apt-get update -qq && apt-get install -y -qq nginx && systemctl enable --now nginx && echo 'Đã cài nginx'"],
  ["Tạo trang chào", "echo '<h1>Xin chào từ minicloud</h1>' > /var/www/html/index.html && echo 'Mở địa chỉ HTTPS của máy để xem'"],
];
const TERM_KEEP = 200000; // characters of transcript kept on screen

// The demo app for autoscaling is demo-app.sh next to this file, served as it is.

async function refreshGroups() {
  const groups = await api("GET", `/v1/projects/${state.project.id}/groups`);
  const ul = $("groups");
  ul.replaceChildren();
  for (const g of groups) {
    const url = typeof g.public_url === "string" && g.public_url.startsWith("https://") ? g.public_url : null;
    const cpu = g.cpu_percent === null ? "chưa đo CPU" : `CPU trung bình ${fmt(g.cpu_percent)}%`;
    const members = el("div", { class: "meta" }, g.members
      .map((m) => `${m.name} ${STATUS_TEXT[m.status] || m.status}${m.cpu_percent === null ? "" : ` ${fmt(m.cpu_percent)}%`}`)
      .join(" · ") || "chưa có máy");
    ul.append(el("li", { class: "instance" },
      el("div", { class: "head" },
        el("strong", {}, g.name),
        el("span", { class: "badge running" }, `${g.size} máy (${g.min_size}–${g.max_size})`)),
      el("div", { class: "meta" }, `${g.flavor} · ${cpu}`),
      url ? el("a", { class: "url", href: url, target: "_blank", rel: "noopener noreferrer" }, url.slice("https://".length)) : null,
      members,
      el("div", { class: "actions" }, actionButton("Xoá nhóm", () => removeGroup(g), "danger"))));
  }
}

async function removeGroup(group) {
  if (!window.confirm(`Xoá nhóm ${group.name} và mọi máy của nhóm?`)) return;
  clearBanner();
  await api("DELETE", `/v1/groups/${group.id}`);
  await refresh();
}

function openTerminal(inst) {
  if (state.term?.inst?.id !== inst.id) $("term-output").replaceChildren();
  state.term = { inst, busy: false };
  $("term-target").textContent = inst.name;
  $("terminal").hidden = false;
  $("terminal").scrollIntoView({ behavior: "smooth", block: "nearest" });
  $("term-form").elements.command.focus();
}

function termWrite(text, cls) {
  const out = $("term-output");
  out.append(el("span", { class: cls }, text));
  while (out.textContent.length > TERM_KEEP && out.firstChild) out.firstChild.remove();
  out.scrollTop = out.scrollHeight;
}

async function runCommand(command) {
  const term = state.term;
  if (!term?.inst || term.busy) return;
  term.busy = true;
  const submit = $("term-form").querySelector("button[type=submit]");
  submit.disabled = true;
  termWrite(`$ ${command}\n`, "cmd");
  try {
    let res = await api("POST", `/v1/instances/${term.inst.id}/exec`, { command });
    while (res.status === "running") {
      termWrite("… lệnh vẫn đang chạy\n", "note");
      res = await api("GET", `/v1/instances/${term.inst.id}/exec/${res.id}`);
    }
    if (res.stdout) termWrite(res.stdout, "out");
    if (res.stderr) termWrite(res.stderr, "err");
    if (res.truncated) termWrite("[đầu ra quá dài nên đã bị cắt]\n", "note");
    const code = res.exit_code ?? "không rõ";
    const timedOut = res.exit_code === 124 ? ": quá 10 phút nên bị dừng" : "";
    termWrite(`[mã thoát ${code}${timedOut}]\n`, res.exit_code === 0 ? "note" : "err");
  } catch (err) {
    termWrite(`${err.message}\n`, "err");
  } finally {
    term.busy = false;
    submit.disabled = false;
  }
}

const statTile = (label, value, unit) => el("div", { class: "stat" },
  el("span", { class: "label" }, label), el("span", { class: "value" }, value), el("span", { class: "unit" }, unit));

async function refreshProvider(force = false) {
  if (!force && Date.now() - state.providerAt < 30000) return; // the overview asks Incus about every laptop
  state.providerAt = Date.now();
  const results = await Promise.allSettled([
    api("GET", "/v1/admin/overview"),
    api("GET", "/v1/admin/orgs"),
    api("GET", "/v1/admin/instances"),
    api("GET", "/v1/admin/stats"),
    api("GET", "/v1/admin/audit?limit=50"),
  ]);
  const renderers = [renderOverview, renderCustomers, renderFleet, renderTrial, renderPlatformAudit];
  results.forEach((result, i) => {
    if (result.status === "fulfilled") renderers[i](result.value);
  });
  if (results.some((result) => result.status === "rejected")) {
    $("provider-note").textContent = "Chưa đọc được một phần số liệu; thử lại sau nửa phút.";
  }
}

function renderOverview(view) {
  $("provider-stats").replaceChildren(
    statTile("Doanh thu tháng này", fmt(view.revenue_credits_month), `credit ≈ ${vnd(view.revenue_vnd_month)}`),
    statTile("Công suất cả cụm", fmt(view.cluster_watts), "W, ước tính"),
    statTile("Máy đang chạy", String(view.instances_running), `của ${view.organizations_running}/${view.organizations} tổ chức`),
    statTile("vCPU đã cấp", view.vcpu_overcommit === null ? "—" : `${fmt(view.vcpu_overcommit)}×`,
      view.memory_allocated_percent === null ? "" : `số luồng thật; RAM đã cấp ${fmt(view.memory_allocated_percent)}%`));
  const tbody = $("provider-nodes");
  tbody.replaceChildren();
  for (const n of view.nodes) {
    const status = !n.online ? (n.asleep ? "Đang ngủ" : "Mất kết nối") : "Đang thức";
    tbody.append(el("tr", {},
      el("td", {}, n.name),
      el("td", {}, status),
      el("td", {}, `${n.allocated_vcpus}/${n.vcpus}`),
      el("td", {}, `${fmt(n.allocated_memory_mib / 1024)}/${fmt(n.memory_mib / 1024)} GB`),
      el("td", {}, String(n.instances))));
  }
  $("provider-note").textContent = `Tháng ${Number(view.month.slice(5))}/${view.month.slice(0, 4)}: ${fmt(view.energy_wh_month)} Wh điện cho máy của khách. ${view.groups} nhóm máy tự co giãn. Doanh thu là credit mô phỏng.`;
}

function emptyRow(columns, text) {
  return el("tr", {}, el("td", { class: "empty", colspan: String(columns) }, text));
}

// "10:32 25/11": the provider's tables have many columns, so their times leave out the year.
function shortWhen(iso, seconds = false) {
  const at = new Date(`${iso}Z`); // the API speaks UTC
  const pad = (n) => String(n).padStart(2, "0");
  const time = `${pad(at.getHours())}:${pad(at.getMinutes())}${seconds ? `:${pad(at.getSeconds())}` : ""}`;
  return `${time} ${pad(at.getDate())}/${pad(at.getMonth() + 1)}`;
}

function renderCustomers(orgs) {
  const tbody = $("provider-orgs");
  tbody.replaceChildren();
  if (orgs.length === 0) tbody.append(emptyRow(6, "Chưa có tổ chức nào."));
  for (const org of orgs) {
    const amount = el("input", {
      type: "number", min: "1", max: "100000", step: "1", value: "500",
      "aria-label": `Số credit nạp cho ${org.name}`,
    });
    tbody.append(el("tr", {},
      el("td", {}, el("strong", {}, org.name), el("div", { class: "muted small" }, org.id)),
      el("td", {}, org.owner_email || "—",
        el("div", { class: "muted small" }, `đăng ký ${shortWhen(org.created_at)}`)),
      el("td", { class: "num" }, fmt(org.credits),
        org.suspended ? el("div", {}, el("span", { class: "badge error" }, "Bị khoá")) : null),
      el("td", { class: "num" }, fmt(org.spent_month)),
      el("td", { class: "num" }, `${org.instances_running}/${org.instances_total}`),
      el("td", {}, el("div", { class: "grant" }, amount,
        actionButton("Nạp", () => grantCredits(org, amount.value))))));
  }
}

async function grantCredits(org, raw) {
  const credits = Number(raw);
  if (!Number.isInteger(credits) || credits < 1 || credits > 100000) {
    throw new Error("Số credit phải là số nguyên từ 1 tới 100.000.");
  }
  clearBanner();
  const billing = await api("POST", `/v1/admin/orgs/${org.id}/credits`,
    { credits, reason: "nạp từ mục Nhà cung cấp" });
  banner(`Đã nạp ${fmt(credits)} credit cho ${org.name}; số dư mới ${fmt(billing.credits)} credit.`, "info");
  await refreshProvider(true);
  if (org.id === state.org.id) await refresh(); // the operator topped up their own organization
}

function renderFleet(list) {
  const tbody = $("provider-instances");
  tbody.replaceChildren();
  if (list.length === 0) tbody.append(emptyRow(6, "Chưa có máy nào."));
  for (const inst of list) {
    const kind = inst.type === "virtual-machine" ? "Máy ảo" : "Container";
    const host = typeof inst.public_hostname === "string" ? inst.public_hostname : null;
    let address = "—";
    if (host && !inst.public_blocked && inst.status === "running") {
      address = el("a", { href: `https://${host}`, target: "_blank", rel: "noopener noreferrer" }, host);
    } else if (host) {
      address = el("span", { class: "muted" }, inst.public_blocked ? `${host} (đã gỡ)` : host);
    }
    const toggle = host
      ? actionButton(inst.public_blocked ? "Mở lại" : "Gỡ địa chỉ",
        () => setPublished(inst, inst.public_blocked), inst.public_blocked ? "" : "danger")
      : null;
    tbody.append(el("tr", {},
      el("td", {}, el("strong", {}, inst.name), el("div", { class: "muted small" }, `${kind} · ${inst.flavor}`)),
      el("td", {}, inst.org_name),
      el("td", {}, el("span", { class: `badge ${inst.status}` }, STATUS_TEXT[inst.status] || inst.status)),
      el("td", {}, inst.node || "—"),
      el("td", { class: "host" }, address),
      el("td", {}, toggle)));
  }
}

async function setPublished(inst, publish) {
  clearBanner();
  await api("POST", `/v1/admin/instances/${inst.id}/${publish ? "publish" : "unpublish"}`);
  banner(publish ? `Đã mở lại địa chỉ công khai của ${inst.name}.`
    : `Đã gỡ địa chỉ công khai của ${inst.name}; máy vẫn chạy.`, "info");
  await refreshProvider(true);
}

function renderTrial(figures) {
  const container = figures.create_seconds ? figures.create_seconds.container : null;
  const failed = Object.values(figures.create_failed || {}).reduce((sum, n) => sum + n, 0);
  $("provider-trial").replaceChildren(
    statTile("Người dùng", String(figures.users),
      `${figures.organizations} tổ chức, ${figures.organizations_running_now} đang có máy chạy`),
    statTile("Tạo container", container ? `${fmt(container.p50)} giây` : "—",
      container ? `trung vị; p95 ${fmt(container.p95)} giây; ${container.count} lần` : "chưa có lần nào"),
    statTile("Tạo máy lỗi", String(failed), "lần"),
    statTile("Giờ máy chạy", fmt(figures.compute_hours), `giờ; ${figures.commands_run} lệnh qua console`));
  $("provider-trial-note").textContent = `Tính từ người dùng đầu tiên (${figures.since ? when(figures.since) : "chưa có"}). Đã trừ ${fmt(figures.credits_charged)} credit; điện ${fmt(figures.energy_wh)} Wh, khoảng ${fmt(figures.co2_grams)} g CO₂.`;
}

const OUTCOME_TEXT = { allowed: "Được phép", denied: "Bị chặn" };

function detailText(detail) {
  if (!detail) return "";
  const text = Object.entries(detail)
    .map(([key, value]) => `${key}: ${value !== null && typeof value === "object" ? JSON.stringify(value) : value}`)
    .join(", ");
  return text.length > 90 ? `${text.slice(0, 89)}…` : text;
}

function renderPlatformAudit(events) {
  const tbody = $("provider-audit");
  tbody.replaceChildren();
  if (events.length === 0) tbody.append(emptyRow(5, "Chưa có sự kiện nào."));
  for (const event of events) {
    tbody.append(el("tr", { class: event.outcome === "denied" ? "denied" : "" },
      el("td", {}, shortWhen(event.at, true)),
      el("td", {}, event.org_name || "—"),
      el("td", {}, event.actor_email || event.actor_id || "hệ thống"),
      el("td", { class: "wrap" }, event.action, el("div", { class: "muted small" }, detailText(event.detail))),
      el("td", {}, OUTCOME_TEXT[event.outcome] || event.outcome)));
  }
}

async function refreshGreen() {
  if (Date.now() - state.greenAt < 60000) return; // the cluster view changes slowly
  state.greenAt = Date.now();
  try {
    renderGreen(await api("GET", "/v1/green"));
  } catch {
    $("green-cluster").textContent = "Chưa đọc được công suất của cụm; thử lại sau một phút.";
  }
}

function renderGreen(g) {
  const ul = $("green-nodes");
  ul.replaceChildren();
  for (const n of g.nodes) {
    const status = n.awake ? `${fmt(n.watts)} W · ${n.instances} máy đang chạy` : "đang ngủ";
    ul.append(el("li", { class: n.awake ? "" : "asleep" },
      el("strong", {}, n.name), el("span", { class: "meta" }, status)));
  }
  const parts = [`Cả cụm đang dùng khoảng ${fmt(g.cluster_watts)} W, ${g.awake}/${g.total} laptop thức.`];
  if (g.saved_watts > 0) parts.push(`Laptop đang ngủ tiết kiệm khoảng ${fmt(g.saved_watts)} W.`);
  parts.push("Lúc cụm vắng, máy được gom về ít laptop hơn để laptop còn lại ngủ.");
  $("green-cluster").textContent = parts.join(" ");
}

function renderBilling(billing) {
  let mine = "Không có máy nào của bạn đang chạy.";
  if (billing.watts > 0) mine = `Máy của bạn đang dùng khoảng ${fmt(billing.watts)} W điện của laptop.`;
  else if (state.running) mine = "Máy của bạn vừa chạy; công suất được đo mỗi phút.";
  $("green-mine").textContent = mine
    + ` Tháng này: ${fmt(billing.month_energy_wh)} Wh ≈ ${fmt(billing.month_co2_grams)} g CO₂.`;
  $("credits").textContent = fmt(billing.credits);
  state.meter = { base: billing.month_to_date, rate: billing.burn_rate_per_hour, at: Date.now() };
  tickMeter();
  $("burn").textContent = fmt(billing.burn_rate_per_hour);
  const runway = billing.runway_hours;
  if (runway === null || runway === undefined) {
    $("runway").textContent = "—";
    $("runway-unit").textContent = "không có máy nào đang tính tiền";
  } else if (runway >= 48) {
    $("runway").textContent = fmt(Math.floor(runway / 24));
    $("runway-unit").textContent = "ngày, với mức tiêu hiện tại";
  } else {
    $("runway").textContent = fmt(Math.floor(runway));
    $("runway-unit").textContent = "giờ, với mức tiêu hiện tại";
  }
  $("suspended").hidden = !billing.suspended;

  const warnings = [];
  const used = billing.budget_used_percent;
  if (used !== null && used >= 80) {
    warnings.push(`Đã dùng ${fmt(used)}% ngân sách tháng (${fmt(billing.month_to_date)} trên ${credit(billing.monthly_budget)}).`
      + (used >= 100 && billing.burn_rate_per_hour > 0 ? " Máy vẫn chạy; hãy dừng hoặc xoá máy không cần." : ""));
  }
  if (!billing.suspended && runway !== null && runway < 24) {
    warnings.push(`Credit chỉ còn đủ khoảng ${fmt(Math.floor(runway))} giờ; hết credit thì mọi máy bị dừng.`);
  }
  $("budget-warning").textContent = warnings.join(" ");
  $("budget-warning").hidden = warnings.length === 0;
  if (state.org.role === "owner") {
    $("budget-status").textContent = billing.monthly_budget
      ? `Ngân sách hiện tại: ${credit(billing.monthly_budget)} mỗi tháng, đã dùng ${fmt(used)}%.`
      : "Chưa đặt ngân sách.";
  }
}

function renderUsage(rows, instances) {
  const names = Object.fromEntries(instances.map((i) => [i.id, i.name]));
  const tbody = $("usage");
  tbody.replaceChildren();
  for (const r of rows) {
    const when = new Date(`${r.period_end}Z`).toLocaleString("vi-VN"); // the API speaks UTC
    tbody.append(el("tr", {},
      el("td", {}, when),
      el("td", {}, names[r.instance_id] || r.instance_id),
      el("td", {}, r.kind === "compute" ? "Đang chạy" : "Lưu trữ"),
      el("td", {}, String(r.seconds)),
      el("td", {}, fmt(r.credits))));
  }
}

for (const tab of document.querySelectorAll(".tab")) {
  tab.addEventListener("click", () => {
    for (const t of document.querySelectorAll(".tab")) t.classList.toggle("active", t === tab);
    $("signup-form").hidden = tab.dataset.tab !== "signup";
    $("login-form").hidden = tab.dataset.tab !== "login";
    clearBanner();
  });
}

$("signup-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  clearBanner();
  const form = new FormData(event.currentTarget);
  const body = { email: form.get("email"), name: form.get("name"), org_name: form.get("org_name") };
  const invite = String(form.get("invite_code") || "").trim();
  if (invite) body.invite_code = invite;
  try {
    const result = await api("POST", "/v1/signup", body);
    localStorage.setItem(KEY_STORAGE, result.api_key);
    $("new-key-value").textContent = result.api_key;
    show("new-key");
  } catch (err) {
    showError(err);
  }
});

$("copy-key").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText($("new-key-value").textContent);
    banner("Đã sao chép API key.", "info");
  } catch {
    banner("Không sao chép tự động được; hãy bôi đen key rồi sao chép.", "info");
  }
});

$("key-saved").addEventListener("click", () => {
  clearBanner();
  enterDashboard().catch(showError);
});

$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  clearBanner();
  const key = String(new FormData(event.currentTarget).get("api_key") || "").trim();
  localStorage.setItem(KEY_STORAGE, key);
  try {
    await enterDashboard();
  } catch (err) {
    showError(err);
  }
});

$("create-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  clearBanner();
  const formNode = event.currentTarget;
  const form = new FormData(formNode);
  const body = {
    name: form.get("name"),
    type: form.get("type"),
    flavor: form.get("flavor"),
    image: form.get("image"),
  };
  const sshKey = String(form.get("ssh_key") || "").trim();
  if (sshKey) body.ssh_keys = [sshKey];
  const submit = formNode.querySelector("button[type=submit]");
  submit.disabled = true;
  try {
    await api("POST", `/v1/projects/${state.project.id}/instances`, body);
    formNode.reset();
    banner(`Đang tạo ${body.name}. Container thường chạy sau vài giây; lần đầu có thể mất một phút.`, "info");
    await refresh();
  } catch (err) {
    showError(err);
  } finally {
    submit.disabled = false;
  }
});

$("key-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  clearBanner();
  const formNode = event.currentTarget;
  const name = String(new FormData(formNode).get("name") || "").trim();
  try {
    const created = await api("POST", "/v1/me/api-keys", { name });
    formNode.reset();
    $("fresh-key-value").textContent = created.api_key;
    $("fresh-key").hidden = false;
    await refreshKeys();
  } catch (err) {
    showError(err);
  }
});

$("copy-fresh-key").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText($("fresh-key-value").textContent);
    banner("Đã sao chép key mới.", "info");
  } catch {
    banner("Không sao chép tự động được; hãy bôi đen key rồi sao chép.", "info");
  }
});

$("hide-fresh-key").addEventListener("click", () => {
  $("fresh-key-value").textContent = "";
  $("fresh-key").hidden = true;
});

for (const [label, command] of TERM_EXAMPLES) {
  $("term-examples").append(el("button", {
    type: "button",
    class: "small",
    onclick: () => { $("term-form").elements.command.value = command; },
  }, label));
}

$("term-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const input = event.currentTarget.elements.command;
  const command = input.value.trim();
  if (!command) return;
  input.value = "";
  runCommand(command);
});

$("group-template").addEventListener("click", async () => {
  const form = $("group-form").elements;
  try {
    const resp = await fetch("demo-app.sh", { cache: "no-store" });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    form.script.value = await resp.text();
    if (!form.name.value) form.name.value = "web";
  } catch (err) {
    banner(`Không tải được app mẫu: ${err.message}`, "error");
  }
});

$("group-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  clearBanner();
  const formNode = event.currentTarget;
  const form = new FormData(formNode);
  const body = {
    name: String(form.get("name") || "").trim(),
    min_size: Number(form.get("min_size")),
    max_size: Number(form.get("max_size")),
    flavor: form.get("flavor"),
    script: String(form.get("script") || ""),
  };
  if (body.min_size > body.max_size) {
    banner("Số máy ít nhất phải không lớn hơn số máy nhiều nhất.", "error");
    return;
  }
  const submit = formNode.querySelector("button[type=submit]");
  submit.disabled = true;
  try {
    await api("POST", `/v1/projects/${state.project.id}/groups`, body);
    formNode.reset();
    $("group-create").open = false;
    banner(`Đang tạo nhóm ${body.name} với ${body.min_size} máy. Địa chỉ chung mở được khi máy đầu tiên chạy xong script.`, "info");
    await refresh();
  } catch (err) {
    showError(err);
  } finally {
    submit.disabled = false;
  }
});

$("term-close").addEventListener("click", () => {
  $("terminal").hidden = true;
});

$("statement-month").addEventListener("change", () => refreshStatement().catch(showError));
$("statement-csv").addEventListener("click", () => downloadStatementCsv().catch(showError));
$("statement-print").addEventListener("click", () => window.print());

$("budget-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const formNode = event.currentTarget;
  const value = Number(new FormData(formNode).get("monthly_credits"));
  if (!Number.isInteger(value) || value <= 0) {
    banner("Ngân sách phải là số credit nguyên, lớn hơn 0.", "error");
    return;
  }
  try {
    await saveBudget(value);
    formNode.reset();
  } catch (err) {
    showError(err);
  }
});

$("budget-clear").addEventListener("click", () => saveBudget(null).catch(showError));

$("logout").addEventListener("click", () => logout());

if (localStorage.getItem(KEY_STORAGE)) {
  enterDashboard().catch((err) => {
    showError(err);
    if (err.status !== 401) show("auth");
  });
} else {
  show("auth");
}
