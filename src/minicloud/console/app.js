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
};

const $ = (id) => document.getElementById(id);
const state = { org: null, project: null, timer: null };

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
}

function logout(clear = true) {
  localStorage.removeItem(KEY_STORAGE);
  clearTimeout(state.timer);
  $("who").textContent = "";
  if (clear) clearBanner();
  show("auth");
}

async function enterDashboard() {
  const me = await api("GET", "/v1/me");
  const orgs = await api("GET", "/v1/orgs");
  state.org = orgs[0];
  const projects = await api("GET", `/v1/orgs/${state.org.id}/projects`);
  state.project = projects[0];
  $("who").textContent = `${me.name} · ${state.org.name}`;
  $("project-name").textContent = `project ${state.project.name}`;
  show("dash");
  await loadCatalog();
  await refresh();
}

async function loadCatalog() {
  if ($("flavor").options.length > 0) return;
  const [flavors, images] = await Promise.all([
    api("GET", "/v1/catalog/flavors"),
    api("GET", "/v1/catalog/images"),
  ]);
  for (const f of flavors) {
    const label = `${f.name} · ${f.vcpus} vCPU · ${f.memory_mib / 1024} GB · ${fmt(f.credits_per_hour_container)} credit/giờ`;
    $("flavor").append(el("option", { value: f.name }, label));
  }
  for (const image of images) $("image").append(el("option", { value: image.key }, image.title));
}

async function refresh() {
  clearTimeout(state.timer);
  const [instances, billing, usage] = await Promise.all([
    api("GET", `/v1/projects/${state.project.id}/instances`),
    api("GET", `/v1/orgs/${state.org.id}/billing`),
    api("GET", `/v1/orgs/${state.org.id}/usage?limit=20`),
  ]);
  renderInstances(instances);
  renderBilling(billing);
  renderUsage(usage, instances);
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
    if (inst.status === "running") actions.append(actionButton("Dừng", () => act(inst, "stop")));
    if (inst.status === "stopped" || inst.status === "error") {
      actions.append(actionButton("Bật", () => act(inst, "start")));
    }
    if (["running", "stopped", "error"].includes(inst.status)) {
      actions.append(actionButton("Xoá", () => remove(inst), "danger"));
    }
    const kind = inst.type === "virtual-machine" ? "Máy ảo" : "Container";
    const meta = el("div", { class: "meta" }, `${kind} · ${inst.flavor} · ${inst.image}`,
      inst.ip ? ` · IP ${inst.ip}` : "");
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

function renderBilling(billing) {
  $("credits").textContent = fmt(billing.credits);
  $("spent").textContent = fmt(billing.spent_last_24h);
  $("suspended").hidden = !billing.suspended;
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

$("logout").addEventListener("click", () => logout());

if (localStorage.getItem(KEY_STORAGE)) {
  enterDashboard().catch((err) => {
    showError(err);
    if (err.status !== 401) show("auth");
  });
} else {
  show("auth");
}
