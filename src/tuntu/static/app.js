"use strict";

const messages = {
  authentication_required: "登录已失效，请重新登录。",
  invalid_credentials: "管理员名称或密码错误。",
  setup_token_invalid: "Setup Token 无效、已过期或已被使用。",
  invalid_username: "管理员名称需为 3–50 位字母、数字、点、下划线或短横线。",
  invalid_password: "密码长度需为 12–256 位。",
  invalid_request: "请检查必填字段和输入格式。",
  invalid_destination_subdir: "目标子目录无效，不能使用绝对路径、空目录段或 ..。",
  enabled_profile_requires_sources: "启用订阅时至少选择一个榜单源和一个候选源。",
  source_scope_mismatch: "所选榜单源不支持当前周期。",
  invalid_rules: "筛选规则不正确，请检查数值范围和关键词。",
  cd2_not_configured: "请先保存 CloudDrive2 连接配置。",
  invalid_clouddrive_settings: "CloudDrive2 设置无效，请检查地址、认证、路径和数值。",
  invalid_cover_display_mode: "热榜封面模式无效。",
  watchlist_not_found: "关注清单不存在。",
  watchlist_item_not_found: "清单作品不存在。",
  invalid_watchlist_item_state: "作品状态无效。",
  watchlist_automation_incomplete: "请先选择下载配置并填写每日执行时间。",
  watchlist_requires_query_source: "所选下载配置需要至少启用一个可按标识查询的候选源。",
  invalid_daily_time: "每日执行时间无效。",
  metadata_only_required: "元数据导入不能包含磁力或种子下载链接。",
  invalid_metadata_url: "元数据中的封面和出处必须是 HTTP(S) 地址。",
  rights_confirmation_required: "请先确认你有权使用所填链接。",
  linked_source_unavailable: "JavDB 详情依赖 JavDB 榜单，但榜单来源当前不可用。",
  duplicate_download: "该内容或 BTIH 已被处理，未重复提交。",
  force_confirmation_required: "该操作需要二次确认。",
  confirmation_required: "请先确认本次提交。",
  force_not_required: "当前没有重复记录，不需要强制提交。",
  destination_busy: "目标目录仍由进行中的任务占用，请先检查原任务。",
  invalid_magnet: "Magnet 必须包含有效的 BitTorrent v1 BTIH。",
  profile_archived: "已归档订阅不能执行手工任务。",
  runtime_unavailable: "运行时不可用，请检查 CloudDrive2 设置。",
  internal_error: "服务暂时无法处理请求，请检查日志。",
};

function qs(selector, root = document) { return root.querySelector(selector); }
function qsa(selector, root = document) { return [...root.querySelectorAll(selector)]; }

async function api(path, options = {}) {
  const config = { ...options, headers: { ...(options.headers || {}) } };
  if (config.body && typeof config.body !== "string") {
    config.headers["Content-Type"] = "application/json";
    config.body = JSON.stringify(config.body);
  }
  if ((config.method || "GET").toUpperCase() !== "GET") {
    config.headers["X-Tuntu-CSRF"] = "1";
  }
  const response = await fetch(path, config);
  const type = response.headers.get("content-type") || "";
  const payload = type.includes("json") ? await response.json() : await response.text();
  if (!response.ok) {
    const code = payload?.error?.code || "internal_error";
    if (response.status === 401 && !path.includes("/auth/login")) {
      window.setTimeout(() => { window.location.href = "/login"; }, 500);
    }
    const error = new Error(messages[code] || payload?.error?.message || code);
    error.code = code;
    throw error;
  }
  return payload;
}

function setBusy(button, busy, label = "处理中…") {
  if (!button) return;
  if (busy) {
    button.dataset.originalLabel = button.textContent;
    button.textContent = label;
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
  } else {
    button.textContent = button.dataset.originalLabel || button.textContent;
    button.disabled = false;
    button.removeAttribute("aria-busy");
  }
}

let toastTimer;
function toast(message, isError = false) {
  const target = qs("#toast");
  if (!target) return;
  window.clearTimeout(toastTimer);
  target.textContent = message;
  target.classList.toggle("is-error", isError);
  target.classList.add("is-visible");
  toastTimer = window.setTimeout(() => target.classList.remove("is-visible"), 4200);
}

function formError(form, error) {
  const target = qs("[data-form-error]", form);
  if (target) target.textContent = error?.message ?? "请求失败，请重试。";
}

function formObject(form) {
  return Object.fromEntries(new FormData(form).entries());
}

function confirmTwice(first, second) {
  return window.confirm(first) && window.confirm(second);
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function renderBadge(status) {
  const badge = element("span", "badge badge-neutral", status);
  if (["accepted", "completed", "success"].includes(status)) badge.className = "badge badge-success";
  if (["submitted", "downloading", "attention_required"].includes(status)) badge.className = "badge badge-warning";
  if (["rejected", "failed"].includes(status)) badge.className = "badge badge-danger";
  return badge;
}

function initializeNavigation() {
  qsa("[data-nav-open]").forEach((button) => button.addEventListener("click", () => {
    document.body.classList.add("nav-open");
    qs("[data-nav-close]")?.focus();
  }));
  qsa("[data-nav-close]").forEach((button) => button.addEventListener("click", () => {
    document.body.classList.remove("nav-open");
    qs("[data-nav-open]")?.focus();
  }));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && document.body.classList.contains("nav-open")) {
      document.body.classList.remove("nav-open");
      qs("[data-nav-open]")?.focus();
    }
  });
}

function initializeAuth() {
  const setup = qs("#setup-form");
  setup?.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!setup.reportValidity()) return;
    const button = qs("button[type=submit]", setup);
    setBusy(button, true, "正在初始化…");
    formError(setup, { message: "" });
    try {
      await api("/api/v1/auth/setup", { method: "POST", body: formObject(setup) });
      window.location.href = "/dashboard";
    } catch (error) { formError(setup, error); setBusy(button, false); }
  });
  const login = qs("#login-form");
  login?.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!login.reportValidity()) return;
    const button = qs("button[type=submit]", login);
    setBusy(button, true, "正在登录…");
    try {
      await api("/api/v1/auth/login", { method: "POST", body: formObject(login) });
      window.location.href = "/dashboard";
    } catch (error) { formError(login, error); setBusy(button, false); }
  });
}

function initializeGlobalActions() {
  qsa('[data-action="logout"]').forEach((button) => button.addEventListener("click", async () => {
    setBusy(button, true, "正在退出…");
    try { await api("/api/v1/auth/logout", { method: "POST" }); } finally { window.location.href = "/login"; }
  }));
}

function profilePayload(form) {
  const data = formObject(form);
  const numeric = (name) => data[name] === "" ? null : Number(data[name]);
  const keywords = (name) => (data[name] || "").split(",").map((value) => value.trim()).filter(Boolean);
  const rules = {
    chinese_subtitles: data.rule_chinese_subtitles,
    uncensored: data.rule_uncensored,
    uhd: data.rule_uhd,
    include_keywords: keywords("rule_include_keywords"),
    exclude_keywords: keywords("rule_exclude_keywords"),
  };
  for (const [source, target] of [["rule_min_size_mb", "min_size_mb"], ["rule_max_size_mb", "max_size_mb"], ["rule_min_seeders", "min_seeders"]]) {
    const value = numeric(source);
    if (value !== null) rules[target] = value;
  }
  return {
    name: data.name,
    destination_subdir: data.destination_subdir,
    top_n: Number(data.top_n),
    daily_time: data.daily_time || null,
    enabled: qs('[name="enabled"]', form).checked,
    scope: data.scope,
    discovery_sources: qsa('[name="discovery_sources"]:checked', form).map((input) => input.value),
    candidate_sources: qsa('[name="candidate_sources"]:checked', form).map((input) => input.value),
    rules,
    auto_submit: qs('[name="auto_submit"]', form).checked,
  };
}

function initializeProfileForm() {
  const form = qs("#profile-form");
  if (!form) return;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!form.reportValidity()) return;
    const button = qs('[form="profile-form"][type="submit"]') || qs("button[type=submit]", form);
    setBusy(button, true, "正在保存…");
    formError(form, { message: "" });
    const id = form.dataset.profileId;
    try {
      const result = await api(id ? `/api/v1/profiles/${id}` : "/api/v1/profiles", {
        method: id ? "PUT" : "POST", body: profilePayload(form),
      });
      toast("订阅已保存。");
      window.location.href = `/profiles/${result.id}`;
    } catch (error) { formError(form, error); setBusy(button, false); }
  });
}

function initializeProfileActions() {
  qsa('[data-action="run-profile"]').forEach((button) => button.addEventListener("click", async () => {
    if (!window.confirm("立即执行一次强制预演？本次不会向 CloudDrive2 提交任务。")) return;
    setBusy(button, true);
    try {
      const result = await api(`/api/v1/profiles/${button.dataset.profileId}/run`, { method: "POST", body: { force_dry_run: true } });
      toast(result.status === "skipped" ? "运行被跳过，请检查是否已有任务。" : "运行已完成，正在打开详情。");
      if (result.run_id) window.location.href = `/runs/${result.run_id}`;
    } catch (error) { toast(error.message, true); setBusy(button, false); }
  }));
  qsa('[data-action="toggle-profile"]').forEach((button) => button.addEventListener("click", async () => {
    setBusy(button, true);
    try {
      await api(`/api/v1/profiles/${button.dataset.profileId}`, { method: "PUT", body: { enabled: button.dataset.enabled === "true" } });
      window.location.reload();
    } catch (error) { toast(error.message, true); setBusy(button, false); }
  }));
  qsa('[data-action="archive-profile"]').forEach((button) => button.addEventListener("click", async () => {
    if (!window.confirm("归档后将停止调度，但历史不会删除。继续吗？")) return;
    setBusy(button, true);
    try { await api(`/api/v1/profiles/${button.dataset.profileId}/archive`, { method: "POST" }); window.location.reload(); }
    catch (error) { toast(error.message, true); setBusy(button, false); }
  }));
  qsa('[data-action="restore-profile"]').forEach((button) => button.addEventListener("click", async () => {
    setBusy(button, true);
    try { await api(`/api/v1/profiles/${button.dataset.profileId}/restore`, { method: "POST" }); window.location.reload(); }
    catch (error) { toast(error.message, true); setBusy(button, false); }
  }));
}

function initializeWatchlists() {
  const createForm = qs("#watchlist-create-form");
  createForm?.addEventListener("submit", async (event) => {
    event.preventDefault(); if (!createForm.reportValidity()) return;
    const data = formObject(createForm); const button = qs("button[type=submit]", createForm);
    const aliases = (data.aliases || "").split(/[,，]/).map((value) => value.trim()).filter(Boolean);
    setBusy(button, true, "正在创建…"); formError(createForm, { message: "" });
    try {
      const result = await api("/api/v1/watchlists", { method: "POST", body: { name: data.name, subject_type: data.subject_type, query: data.query, aliases } });
      window.location.href = `/watchlists/${result.id}`;
    } catch (error) { formError(createForm, error); setBusy(button, false); }
  });

  const importForm = qs("#watchlist-import-form");
  importForm?.addEventListener("submit", async (event) => {
    event.preventDefault(); if (!importForm.reportValidity()) return;
    const data = formObject(importForm); const button = qs("button[type=submit]", importForm);
    const items = data.items.split(/\r?\n/).map((line) => line.trim()).filter(Boolean).map((line) => {
      const [key, title = "", sourceUrl = ""] = line.split("|").map((value) => value.trim());
      const metadata = {}; if (sourceUrl) metadata.source_url = sourceUrl;
      return { namespace: data.namespace, key, title, metadata };
    });
    setBusy(button, true, "正在导入…"); formError(importForm, { message: "" });
    try {
      const result = await api(`/api/v1/watchlists/${importForm.dataset.watchlistId}/items/import`, { method: "POST", body: { source_name: data.source_name, items } });
      toast(`已新增 ${result.imported} 条，清单共 ${result.summary.total} 条。`); window.setTimeout(() => window.location.reload(), 450);
    } catch (error) { formError(importForm, error); setBusy(button, false); }
  });

  const automationForm = qs("#watchlist-automation-form");
  automationForm?.addEventListener("submit", async (event) => {
    event.preventDefault(); if (!automationForm.reportValidity()) return;
    const data = formObject(automationForm); const button = qs('button[type="submit"]', automationForm);
    const autoSubmit = qs('[name="auto_submit"]', automationForm).checked;
    const rightsConfirmed = qs('[name="rights_confirmed"]', automationForm).checked;
    if (autoSubmit && !rightsConfirmed) { formError(automationForm, new Error(messages.rights_confirmation_required)); return; }
    const payload = {
      profile_id: data.profile_id ? Number(data.profile_id) : null,
      daily_time: data.daily_time || null,
      enabled: qs('[name="enabled"]', automationForm).checked,
      auto_submit: autoSubmit,
      rights_confirmed: rightsConfirmed,
    };
    setBusy(button, true, "正在保存…"); formError(automationForm, { message: "" });
    try {
      await api(`/api/v1/watchlists/${automationForm.dataset.watchlistId}/automation`, { method: "PUT", body: payload });
      toast("自动处理设置已保存。"); window.setTimeout(() => window.location.reload(), 450);
    } catch (error) { formError(automationForm, error); setBusy(button, false); }
  });

  qsa('[data-action="run-watchlist"]').forEach((button) => button.addEventListener("click", async () => {
    const dryRun = button.dataset.dryRun === "true";
    if (dryRun) {
      if (!window.confirm("立即查询待处理作品并执行预演？本次不会向 CloudDrive2 提交。")) return;
    } else if (!window.confirm("立即按当前自动处理设置运行？若已开启自动提交，合格候选会提交到 CloudDrive2。")) return;
    setBusy(button, true, dryRun ? "正在预演…" : "正在运行…");
    try {
      const result = await api(`/api/v1/watchlists/${button.dataset.watchlistId}/run`, { method: "POST", body: { force_dry_run: dryRun } });
      if (result.run_id) window.location.href = `/runs/${result.run_id}`;
      else { toast("没有待处理作品，或本次运行被跳过。"); setBusy(button, false); }
    } catch (error) { toast(error.message, true); setBusy(button, false); }
  }));

  qsa('[data-action="watchlist-state"]').forEach((button) => button.addEventListener("click", async () => {
    setBusy(button, true);
    try {
      await api(`/api/v1/watchlists/${button.dataset.watchlistId}/items/${button.dataset.contentId}`, { method: "PATCH", body: { state: button.dataset.state } });
      window.location.reload();
    } catch (error) { toast(error.message, true); setBusy(button, false); }
  }));

  qsa("[data-authorized-magnet]").forEach((form) => form.addEventListener("submit", async (event) => {
    event.preventDefault(); if (!form.reportValidity()) return;
    if (!window.confirm("确认你有权使用此链接，并将该作品提交到 CloudDrive2？")) return;
    const data = formObject(form); const button = qs("button[type=submit]", form);
    setBusy(button, true, "正在提交…"); formError(form, { message: "" });
    try {
      const result = await api(`/api/v1/watchlists/${form.dataset.watchlistId}/items/${form.dataset.contentId}/authorized-magnet`, { method: "POST", body: { profile_id: Number(data.profile_id), magnet_uri: data.magnet_uri, rights_confirmed: qs('[name="rights_confirmed"]', form).checked, confirmed: true } });
      window.location.href = `/downloads/${result.id}`;
    } catch (error) { formError(form, error); setBusy(button, false); }
  }));
}

function renderNumberPreview(result, request) {
  const target = qs("#manual-results");
  target.replaceChildren();
  const header = element("div", "panel-header");
  const heading = element("div");
  heading.append(element("p", "eyebrow", "FILTERED CANDIDATES"), element("h2", "", `${result.normalized_key} · ${result.candidates.length} 个候选`), element("p", "muted", `目标目录：${result.destination}`));
  header.append(heading);
  target.append(header);
  if (!result.candidates.length) target.append(element("p", "muted", "候选源没有返回可用 magnet。"));
  result.candidates.forEach((candidate) => {
    const card = element("article", "candidate-card");
    const head = element("div", "candidate-head");
    const title = element("div");
    title.append(element("strong", "", candidate.title || candidate.btih), element("code", "hash", candidate.btih));
    head.append(title, renderBadge(candidate.duplicate ? "duplicate" : (candidate.accepted ? "accepted" : "rejected")));
    const meta = element("div", "candidate-meta");
    [
      `来源 ${candidate.sources.join(", ") || "未知"}`,
      `体积 ${candidate.size_mb ?? "未知"} MB`,
      `做种 ${candidate.seeders ?? "未知"}`,
      `中字 ${candidate.chinese_subtitles}`,
      `无码 ${candidate.uncensored}`,
      `UHD ${candidate.uhd}`,
    ].forEach((value) => meta.append(element("span", "", value)));
    card.append(head, meta, element("p", "muted", `候选目标：${candidate.destination}`));
    if (candidate.duplicate) {
      const duplicate = element("p", "reason", `已由 ${candidate.duplicate.profile_name} 处理，任务状态 ${candidate.duplicate.status}。`);
      const link = element("a", "text-link", "查看原任务");
      link.href = `/downloads/${candidate.duplicate.task_id}`;
      duplicate.append(" ", link);
      card.append(duplicate);
    }
    candidate.reasons.forEach((reason) => card.append(element("small", "reason", reason.message)));
    if (candidate.accepted && !candidate.duplicate) {
      const button = element("button", "button button-primary", "确认提交此候选");
      button.type = "button";
      button.addEventListener("click", async () => {
        if (!window.confirm(`将 ${result.normalized_key} 提交到 ${candidate.destination}，继续吗？`)) return;
        setBusy(button, true, "正在提交…");
        try {
          const submitted = await api("/api/v1/manual/number/submit", { method: "POST", body: { profile_id: request.profile_id, run_id: result.run_id, candidate_id: candidate.candidate_id, confirmed: true } });
          window.location.href = `/downloads/${submitted.id}`;
        } catch (error) { toast(error.message, true); setBusy(button, false); }
      });
      const actions = element("div", "preview-actions"); actions.append(button); card.append(actions);
    }
    target.append(card);
  });
  target.hidden = false;
  target.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
}

function renderMagnetPreview(result, request) {
  const target = qs("#manual-results");
  target.replaceChildren();
  const header = element("div", "panel-header");
  const heading = element("div");
  heading.append(element("p", "eyebrow", "MAGNET PREVIEW"), element("h2", "", request.title || "直接 magnet"), element("code", "hash", result.btih));
  header.append(heading, renderBadge(result.duplicate ? "duplicate" : "ready"));
  target.append(header, element("p", "muted", `目标目录：${result.destination}`));
  if (result.duplicate) target.append(element("p", "reason", `已由 ${result.duplicate.profile_name} 处理，任务状态 ${result.duplicate.status}。`));
  const button = element("button", result.duplicate ? "button button-danger" : "button button-primary", result.duplicate ? "强制重复提交" : "确认提交");
  button.type = "button";
  button.addEventListener("click", async () => {
    const confirmed = result.duplicate ? confirmTwice("该 BTIH 已存在，确定要强制重复提交？", "再次确认：这可能产生重复文件或外部任务。") : window.confirm("确认将此 magnet 提交到 CloudDrive2？");
    if (!confirmed) return;
    setBusy(button, true, "正在提交…");
    try {
      const submitted = await api("/api/v1/manual/magnet/submit", { method: "POST", body: { ...request, force: Boolean(result.duplicate), confirmed: true } });
      window.location.href = `/downloads/${submitted.id}`;
    } catch (error) { toast(error.message, true); setBusy(button, false); }
  });
  const actions = element("div", "preview-actions"); actions.append(button); target.append(actions); target.hidden = false;
}

function initializeManual() {
  const numberForm = qs("#number-preview-form");
  numberForm?.addEventListener("submit", async (event) => {
    event.preventDefault(); if (!numberForm.reportValidity()) return;
    const button = qs("button[type=submit]", numberForm); const request = formObject(numberForm); request.profile_id = Number(request.profile_id);
    setBusy(button, true, "正在聚合候选…"); formError(numberForm, { message: "" });
    try { renderNumberPreview(await api("/api/v1/manual/number/preview", { method: "POST", body: request }), request); }
    catch (error) { formError(numberForm, error); } finally { setBusy(button, false); }
  });
  const magnetForm = qs("#magnet-preview-form");
  magnetForm?.addEventListener("submit", async (event) => {
    event.preventDefault(); if (!magnetForm.reportValidity()) return;
    const button = qs("button[type=submit]", magnetForm); const request = formObject(magnetForm); request.profile_id = Number(request.profile_id);
    setBusy(button, true, "正在检查…"); formError(magnetForm, { message: "" });
    try { renderMagnetPreview(await api("/api/v1/manual/magnet/preview", { method: "POST", body: request }), request); }
    catch (error) { formError(magnetForm, error); } finally { setBusy(button, false); }
  });
}

function settingsPayload(form) {
  const data = formObject(form);
  const payload = {};
  const numeric = new Set([
    "max_concurrent_runs", "provider_timeout_seconds", "provider_retries",
    "provider_backoff_seconds", "provider_cache_ttl_seconds",
    "provider_min_interval_seconds", "provider_max_response_bytes",
    "cd2_rpc_timeout_seconds", "cd2_task_list_timeout_seconds",
    "cd2_poll_interval_seconds", "cd2_attention_after_hours",
    "cd2_check_folder_after_seconds", "cd2_required_stable_observations",
    "cd2_max_tree_depth", "cd2_max_tree_entries",
  ]);
  const nullable = new Set(["outbound_proxy", "authorized_candidate_api_url", "cd2_endpoint", "cd2_username"]);
  const secrets = new Set(["javdb_cookie", "authorized_candidate_api_token", "cd2_api_token", "cd2_password", "cd2_ca_certificate"]);
  for (const [key, value] of Object.entries(data)) {
    if (secrets.has(key) && value === "") continue;
    if (numeric.has(key)) payload[key] = Number(value);
    else if (nullable.has(key) && value === "") payload[key] = null;
    else payload[key] = value;
  }
  payload.cd2_tls_verify = qs('[name="cd2_tls_verify"]', form).checked;
  return payload;
}

function initializeSettings() {
  const form = qs("#settings-form");
  if (!form) return;
  const authMode = qs('[name="cd2_auth_mode"]', form);
  const endpoint = qs('[name="cd2_endpoint"]', form);
  const syncCloudDriveAuth = () => {
    const configured = Boolean(endpoint?.value.trim());
    qsa("[data-auth-fields]", form).forEach((group) => {
      const active = group.dataset.authFields === authMode?.value;
      group.hidden = !active;
      qsa("input", group).forEach((input) => {
        input.disabled = !active;
        input.required = active && configured && input.dataset.secretConfigured !== "true";
      });
    });
  };
  authMode?.addEventListener("change", syncCloudDriveAuth);
  endpoint?.addEventListener("input", syncCloudDriveAuth);
  syncCloudDriveAuth();
  form.addEventListener("submit", async (event) => {
    event.preventDefault(); if (!form.reportValidity()) return;
    const button = qs('[form="settings-form"][type="submit"]'); setBusy(button, true, "正在保存…");
    try { await api("/api/v1/settings", { method: "PUT", body: settingsPayload(form) }); toast("设置已保存，运行时已重新加载。"); window.setTimeout(() => window.location.reload(), 500); }
    catch (error) { formError(form, error); setBusy(button, false); }
  });
  qsa('[data-action="test-clouddrive"]').forEach((button) => button.addEventListener("click", async () => {
    setBusy(button, true, "正在测试…");
    try { const result = await api("/api/v1/settings/clouddrive/test", { method: "POST" }); toast(`连接成功：${result.product_name} ${result.product_version}，测试目录 ${result.test_destination}`); }
    catch (error) { toast(error.message, true); } finally { setBusy(button, false); }
  }));
  const password = qs("#password-form");
  password?.addEventListener("submit", async (event) => {
    event.preventDefault(); if (!password.reportValidity()) return;
    if (!window.confirm("修改密码后所有会话都会退出。继续吗？")) return;
    const button = qs("button[type=submit]", password); setBusy(button, true, "正在修改…");
    try { await api("/api/v1/auth/password", { method: "POST", body: formObject(password) }); window.location.href = "/login"; }
    catch (error) { formError(password, error); setBusy(button, false); }
  });
}

function initializeDownloadActions() {
  qsa('[data-action="manual-complete"]').forEach((button) => button.addEventListener("click", async () => {
    if (!confirmTwice("只有确认文件已完整存在时才能人工标记完成。继续？", "再次确认：系统会明确记录这是人工结论。")) return;
    setBusy(button, true);
    try { await api(`/api/v1/downloads/${button.dataset.taskId}/manual-complete`, { method: "POST", body: { confirmed: true } }); window.location.reload(); }
    catch (error) { toast(error.message, true); setBusy(button, false); }
  }));
  qsa('[data-action="retry-download"]').forEach((button) => button.addEventListener("click", async () => {
    const high = button.dataset.risk === "high";
    const confirmed = high ? confirmTwice("当前任务不是普通失败状态，确定强制重提？", "再次确认：强制重提会创建新代次并保留关联。") : window.confirm("重试这个失败任务？");
    if (!confirmed) return;
    setBusy(button, true);
    try { const result = await api(`/api/v1/downloads/${button.dataset.taskId}/retry`, { method: "POST", body: { confirmed: high } }); window.location.href = `/downloads/${result.id}`; }
    catch (error) { toast(error.message, true); setBusy(button, false); }
  }));
}

function initializeSourceActions() {
  qsa('[data-action="test-source"]').forEach((button) => button.addEventListener("click", async () => {
    let query = null;
    if (button.dataset.probeMode === "query") {
      query = window.prompt("输入一个用于测试的番号。测试只查询，不提交下载：");
      if (!query) return;
    }
    setBusy(button, true, "正在测试…");
    try {
      const result = await api(`/api/v1/sources/${button.dataset.sourceName}/test`, { method: "POST", body: { query, scope: "weekly" } });
      const failedLabel = result.error_code?.startsWith("linked_") ? `联动前置失败：${result.error_code.slice(7)}` : `来源测试失败：${result.error_code}`;
      toast(result.status === "success" ? `来源正常：${result.result_count} 条结果，${result.latency_ms} ms` : failedLabel, result.status !== "success");
      window.setTimeout(() => window.location.reload(), 650);
    }
    catch (error) { toast(error.message, true); setBusy(button, false); }
  }));
  qsa('[data-action="toggle-source"]').forEach((button) => button.addEventListener("click", async () => {
    const enabled = button.dataset.enabled === "true";
    if (!window.confirm(`${enabled ? "启用" : "停用"}这个来源？已保存的订阅不会被删除。`)) return;
    setBusy(button, true);
    try {
      await api(`/api/v1/sources/${button.dataset.sourceName}`, { method: "PUT", body: { enabled } });
      window.location.reload();
    } catch (error) { toast(error.message, true); setBusy(button, false); }
  }));
}

document.addEventListener("DOMContentLoaded", () => {
  initializeNavigation();
  initializeAuth();
  initializeGlobalActions();
  initializeProfileForm();
  initializeProfileActions();
  initializeWatchlists();
  initializeManual();
  initializeSettings();
  initializeDownloadActions();
  initializeSourceActions();
});
