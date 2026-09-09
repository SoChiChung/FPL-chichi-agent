"use strict";

/* ============================================================
 * FPL AI Manager 前端（Phase 2.5 内容架构，design.md v1.1）
 * Dashboard Layout：Sidebar(Profile/战绩/最近思考/External) + Main(①-⑥)
 * 本阶段只做内容与布局，不做视觉主题（Phase 3 延后）。
 * ============================================================ */

const $ = (sel) => document.querySelector(sel);

/* ---------------- 基础工具 ---------------- */

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

/**
 * 空态占位文案（字段感知，BugFix UX v1.0）：
 * 排名类（rank/overall_rank）→「待结算排名」；积分类（points）→「待结算」。
 * 避免「当轮排名待结算」被误读为「分数待结算」。
 */
function nullText(kind) {
  return kind === "rank" ? "待结算排名" : "待结算";
}

function fmtNumber(n, kind) {
  return n == null ? nullText(kind) : Number(n).toLocaleString("zh-CN");
}

function fmtNum(n) {
  return typeof n === "number" ? n.toLocaleString("zh-CN", { maximumFractionDigits: 1 }) : "-";
}

/**
 * Bank 展示：state.bank 现为 £m 浮点（与球员 price 同单位，如 2.0 = £2.0m）。
 * 后端已把 FPL API 的 0.1M 整数（20 = £2.0m）在 context.build_state 里 /10 归一，
 * 前端不再自行换算；若再除 10 会把 £2.0m 错显示成 £0.2m。
 */
function fmtBank(raw) {
  const m = Number(raw) || 0;
  return `£${m.toFixed(1)}m`;
}

/** £m 通用金额格式（净花费 / 预算等） */
function fmtMoney(m) {
  return `£${(Number(m) || 0).toFixed(1)}m`;
}

/** 北京时间（Asia/Shanghai）格式化；无效输入原样返回 */
function fmtTime(iso) {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false });
}

/** 数字压缩显示：1_240_000 → 1.24M；234_000 → 234k */
function fmtAxis(v) {
  if (v == null) return "待结算";
  const abs = Math.abs(v);
  if (abs >= 1e6) {
    const s = (v / 1e6).toFixed(2).replace(/\.?0+$/, "");
    return `${s}M`;
  }
  if (abs >= 1e3) {
    const s = (v / 1e3).toFixed(1).replace(/\.0$/, "");
    return `${s}k`;
  }
  return String(Math.round(v));
}

function truncate(s, n) {
  const t = String(s ?? "");
  return t.length > n ? `${t.slice(0, n - 1)}…` : t;
}

async function loadJSON(url) {
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`${url} -> HTTP ${resp.status}`);
  return resp.json();
}

/* ---------------- 常量与映射 ---------------- */

const STYLE_LABEL = { market_consensus: "市场共识型" };

function styleLabel(v) {
  return (v != null && STYLE_LABEL[v]) || v || "-";
}

/** Risk Level 映射（design.md §6.2） */
function riskLevel(s) {
  if (!s) return "-";
  if (s.allow_hits === true) return "High";
  if (s.allow_hits === false || s.max_free_transfers >= 5) return "Low";
  return "Medium";
}

/* notes 折叠区 topic：不进正文，收进「更多细节」（design.md §5.1.4） */
const NOTE_FOLD = new Set(["external_source", "score_source", "data_missing", "warning"]);
/* 单条 notes 优先级（转会类单独配对处理，恒排最前） */
const NOTE_ORDER = { captain: 1, no_transfer: 2, lineup: 3, transfer_status: 4, squad_source: 5 };
const TOPIC_TAG = {
  transfer_out: "转会", transfer_in: "转会", captain: "队长", no_transfer: "转会判断",
  lineup: "阵容", transfer_status: "转会额度", squad_source: "阵容来源",
  external_source: "数据更新", score_source: "数据源", data_missing: "数据缺失", warning: "告警",
};

const MAX_BLOCKS = 3; // ≤3 段
const MAX_BLOCK_CHARS = 120; // 单段 ≤120 字
const MAX_SIDEBAR_CHARS = 60; // Sidebar 摘要 ≤60 字

/* ---------------- 数据准备 ---------------- */

/** history.json 结构：{ history: [...] }，统一按 gw 升序 */
function historyRowsAsc(json) {
  const rows = (json && Array.isArray(json.history)) ? json.history.slice() : [];
  return rows.sort((a, b) => (a.gw ?? 0) - (b.gw ?? 0));
}

/** 当前轮条目：优先 gw === state.current_gw，其次取最新 */
function curEntry(state, rows) {
  if (!rows.length) return null;
  return rows.find((r) => r.gw === state.current_gw) || rows[rows.length - 1];
}

/** 最新一份 strategy_snapshot（从最新条目向上找第一个携带者） */
function latestSnapshot(rows) {
  for (let i = rows.length - 1; i >= 0; i--) {
    const s = rows[i] && rows[i].decision && rows[i].decision.strategy_snapshot;
    if (s) return s;
  }
  return null;
}

/** 最近一次已结算轮次的当轮排名 */
function lastSettledGwRank(rows) {
  for (let i = rows.length - 1; i >= 0; i--) {
    if (rows[i].rank != null) return { gw: rows[i].gw, rank: rows[i].rank };
  }
  return null;
}

/** 近 N 轮阵型众数 */
function favFormation(rows, n = 5) {
  const forms = rows
    .slice(-n)
    .map((r) => r.decision && r.decision.formation)
    .filter(Boolean);
  if (!forms.length) return null;
  const cnt = {};
  forms.forEach((f) => { cnt[f] = (cnt[f] || 0) + 1; });
  return Object.entries(cnt).sort((a, b) => b[1] - a[1])[0][0];
}

/* ---------------- 思考日志聚合（design.md §5.1） ---------------- */

/** 单条 note → 正文字段文本 */
function noteBodyText(n) {
  const d = String(n.detail || "").trim();
  switch (n.topic) {
    case "transfer_out":
      return d ? `转出 ${n.player || "?"}：${d}` : `转出 ${n.player || "?"}`;
    case "transfer_in":
      return d ? `转入 ${n.player || "?"}：${d}` : `转入 ${n.player || "?"}`;
    case "captain":
      return `队长 ${n.player || "?"}：${d}`;
    case "no_transfer": {
      const core = d.replace(/，?\s*不进行转会\s*$/, "").replace(/^[\s，、:：]+/, "");
      return core ? `本轮未进行转会 —— ${core}` : "本轮未进行转会";
    }
    case "lineup":
    case "transfer_status":
    case "squad_source":
      return d;
    default:
      return d ? `${TOPIC_TAG[n.topic] || n.topic}：${d}` : d;
  }
}

/** 聚合 notes：
 *  returns { blocks: string[]（≤3 段正文）, extras: string[]（更多细节） }
 */
function buildThoughts(notes) {
  const list = Array.isArray(notes) ? notes : [];
  const fold = [];
  const cand = [];
  list.forEach((n) => {
    if (!n || !n.topic) return;
    if (NOTE_FOLD.has(n.topic)) fold.push(n);
    else cand.push(n);
  });

  // 转会配对：按原始顺序 out→in 成对，落单的退回单条候选
  const trans = cand.filter((n) => n.topic === "transfer_out" || n.topic === "transfer_in");
  const singles = cand.filter((n) => n.topic !== "transfer_out" && n.topic !== "transfer_in");
  const pairs = [];
  let pending = null;
  trans.forEach((n) => {
    if (n.topic === "transfer_out") {
      if (pending) singles.push(pending); // 上一笔 out 落单
      pending = n;
    } else if (pending) {
      pairs.push([pending, n]);
      pending = null;
    } else {
      singles.push(n); // 无配对的 in
    }
  });
  if (pending) singles.push(pending);

  const pairText = ([o, i]) => {
    const reason = String((i && i.detail) || (o && o.detail) || "").trim();
    const t = `转出 ${o && o.player ? o.player : "?"}、转入 ${i && i.player ? i.player : "?"}`;
    return reason ? `${t}：${reason}` : t;
  };

  // 叙事顺序：转会操作(配对，保原始顺序) → 队长 → 排兵/额度…
  singles.sort((a, b) => (NOTE_ORDER[a.topic] ?? 99) - (NOTE_ORDER[b.topic] ?? 99));
  const ordered = [...pairs.map(pairText), ...singles.map(noteBodyText)]
    .map((t) => truncate(t, MAX_BLOCK_CHARS))
    .filter(Boolean);

  // 正文最多 MAX_BLOCKS 段；超出部分 + 折叠类 topic → 「更多细节」
  const blocks = ordered.slice(0, MAX_BLOCKS);
  const extras = [
    ...ordered.slice(MAX_BLOCKS),
    ...fold.map((n) => {
      const tag = TOPIC_TAG[n.topic] || n.topic;
      const who = n.player ? `${n.player}：` : "";
      return `[${tag}] ${who}${n.detail || ""}`;
    }),
  ];
  return { blocks, extras };
}

/** 渲染思考日志到容器：<p> 正文（≤3 段）+ <details> 更多细节 */
function renderThoughtsInto(el, notes) {
  const { blocks, extras } = buildThoughts(notes);
  if (!blocks.length && !extras.length) {
    el.innerHTML = '<p class="muted">本轮暂无思考日志。</p>';
    return;
  }
  let html = blocks.map((t) => `<p class="thought-p">${esc(t)}</p>`).join("");
  if (extras.length) {
    html +=
      `<details class="thought-more"><summary>更多细节（${extras.length}）</summary>` +
      `<ul>${extras.map((t) => `<li>${esc(t)}</li>`).join("")}</ul></details>`;
  }
  el.innerHTML = html;
}

/* ---------------- Sidebar：Profile ---------------- */

function renderProfile(state, rows) {
  const snap = latestSnapshot(rows);
  const season = state.season ? `${state.season} 赛季` : "-";
  const mgr = state.manager_name || "FPL AI Manager";
  const formation = favFormation(rows) || (state.decision && state.decision.formation) || state.formation || "-";

  $("#manager-line").textContent = state.manager_name ? `经理：${state.manager_name}` : "";
  $("#season").textContent = season;

  $("#profile-name").textContent = mgr;
  $("#profile-season").textContent = season;
  $("#p-style").textContent = styleLabel(snap && snap.strategy);
  $("#p-risk").textContent = riskLevel(snap);
  $("#p-formation").textContent = formation;

  // 移动端摘要条（同数据源）
  $("#m-name").textContent = mgr;
  $("#m-sub").textContent = season;
}

/* ---------------- Sidebar：当前战绩 ---------------- */

function statItems(state, rows) {
  const gwRank = lastSettledGwRank(rows);
  return [
    { label: "Overall Rank", value: fmtNumber(state.rank, "rank"), note: "" },
    {
      label: "Gameweek Rank",
      value: gwRank ? fmtNumber(gwRank.rank, "rank") : nullText("rank"),
      note: gwRank ? `最近已结算 GW${gwRank.gw}` : "暂无已结算轮次",
    },
    { label: "Total Points", value: fmtNumber(state.points, "points"), note: "" },
  ];
}

function renderRecords(state, rows) {
  const items = statItems(state, rows);
  const desktop = items
    .map(
      (it) => `<div class="rec-row">
          <span class="rec-label">${it.label}</span>
          <span class="rec-value">${esc(it.value)}</span>
          ${it.note ? `<span class="rec-note">${esc(it.note)}</span>` : ""}
        </div>`,
    )
    .join("");
  $("#records").innerHTML = desktop;

  const mobile = items
    .map(
      (it) => `<div class="ms-item">
          <div class="ms-label">${it.label}</div>
          <div class="ms-value">${esc(it.value)}</div>
        </div>`,
    )
    .join("");
  $("#m-stats").innerHTML = mobile;
}

/* ---------------- Sidebar：最近一次思考（≤60 字） ---------------- */

function renderRecentThought(entry) {
  const el = $("#recent-thought");
  if (!entry) {
    el.innerHTML = '<p class="muted">暂无思考记录。</p>';
    return;
  }
  const { blocks } = buildThoughts(entry.notes);
  const text = blocks[0] || "";
  if (!text) {
    el.innerHTML = '<p class="muted">暂无思考记录。</p>';
    return;
  }
  el.innerHTML =
    `<a class="recent-link" href="#sec-thoughts">` +
    `<p class="recent-text">${esc(truncate(text, MAX_SIDEBAR_CHARS))}</p>` +
    `<span class="recent-go muted">查看本轮完整思考 ↓</span></a>`;
}

/* ---------------- Main ① 本轮成绩摘要卡 ---------------- */

function renderSummaryCards(state, rows) {
  const gwRank = lastSettledGwRank(rows);
  const cards = [
    { label: "当前 GW", value: `GW${state.current_gw ?? "-"}`, cls: "gameweek" },
    { label: "Overall Rank", value: fmtNumber(state.rank, "rank"), cls: "" },
    {
      label: "Gameweek Rank",
      value: gwRank ? fmtNumber(gwRank.rank, "rank") : nullText("rank"),
      cls: "",
      note: gwRank ? `GW${gwRank.gw}` : "",
    },
    { label: "Total Points", value: fmtNumber(state.points, "points"), cls: "" },
    { label: "Bank", value: fmtBank(state.bank), cls: "" },
  ];
  $("#summary-cards").innerHTML = cards
    .map(
      (c) => `<div class="card ${c.cls}">
          <div class="card-label">${c.label}${c.note ? ` <span class="card-note">${esc(c.note)}</span>` : ""}</div>
          <div class="card-value">${esc(c.value)}</div>
        </div>`,
    )
    .join("");
}

/* ---------------- Main ② AI 本周动态 ---------------- */

function renderThisWeek(state, entry) {
  const d = state.decision || {};
  const capName = (d.captain && d.captain.name) || "-";
  const viceName = (d.vice && d.vice.name) || "-";
  const ftText =
    d.transfer_status === "unlimited"
      ? "转会次数暂不受限（新账号）"
      : typeof d.free_transfers === "number"
        ? `可用免费转会 ${d.free_transfers} 次`
        : "";

  let html = `<div class="tw-meta">
      <span class="tw-item">阵型 <b>${esc((d.formation) || state.formation || "-")}</b></span>
      <span class="tw-item">队长 <b class="txt-cap">C ${esc(capName)}</b></span>
      <span class="tw-item">副队长 <b class="txt-vice">V ${esc(viceName)}</b></span>
      ${ftText ? `<span class="tw-item muted">${esc(ftText)}</span>` : ""}
    </div>
    <p class="muted disclaimer">以下为 AI 建议，不自动提交到 FPL。</p>`;

  const transfers = Array.isArray(d.recommended_transfers) ? d.recommended_transfers : [];
  if (transfers.length) {
    html += `<div class="tw-transfers">` +
      transfers
        .map((t) => {
          const o = t.out || {};
          const i = t.in || {};
          return `<div class="xfer">
            <div class="xfer-line">
              <span class="x-out">${esc(o.name || "?")}</span>
              <span class="x-arrow">→</span>
              <span class="x-in">${esc(i.name || "?")}</span>
              <span class="muted x-meta">${esc(o.pos || "-")} £${o.price ?? "-"}m → ${esc(i.pos || "-")} £${i.price ?? "-"}m</span>
            </div>
            <div class="xfer-reason muted">${esc(t.reason || "")}</div>
          </div>`;
        })
        .join("") + `</div>`;
    const pkg = d.transfer_package || {};
    if (pkg.budget_before != null) {
      html += `<p class="muted xfer-summary">预算校验：净花费 ${fmtMoney(pkg.transfer_cost)}（卖出回血 ${fmtMoney(pkg.total_out_price)}，买入 ${fmtMoney(pkg.total_in_price)}），转会后 Bank ${fmtBank(pkg.budget_after)}${pkg.gain != null ? `；包总增益 +${fmtNum(pkg.gain)}` : ""}</p>`;
    }
  } else {
    const noTransfer = Array.isArray(entry && entry.notes)
      ? (entry.notes.find((n) => n.topic === "no_transfer") || {}).detail
      : null;
    html += noTransfer
      ? `<p class="no-xfer">本轮未进行转会 —— ${esc(noTransfer)}</p>`
      : '<p class="no-xfer">本轮未进行转会。</p>';
  }
  $("#this-week").innerHTML = html;
}

/* ---------------- Main ③ 本轮阵容 ---------------- */

const POS_LABEL = { GKP: "门将", DEF: "后卫", MID: "中场", FWD: "前锋" };

function playerResolve(state, ctx) {
  const d = ctx.d;
  const byId = ctx.byId;
  // 转会后视图（suggested）：阵容 = 后端快照，XI/替补与 C/V 均由快照内
  // starting / is_captain / is_vice_captain 标志决定（转会顶替已保留槽位）
  if (ctx.suggested) {
    const pool = Array.from(byId.values());
    const xi = pool.filter((p) => p.starting);
    const bench = pool.filter((p) => !p.starting);
    const cap = pool.find((p) => p.is_captain) || {};
    const vice = pool.find((p) => p.is_vice_captain) || {};
    return { xi, bench, capId: cap.id, viceId: vice.id };
  }
  const xiIds = (Array.isArray(d.starting_xi) ? d.starting_xi : []).map((o) => o.id);
  const benchIds = (Array.isArray(d.bench) ? d.bench : []).map((o) => o.id);
  const pick = (ids) => ids.map((id) => byId.get(id)).filter(Boolean);
  const xi = xiIds.length ? pick(xiIds) : ctx.team.filter((p) => p.starting);
  const bench = benchIds.length ? pick(benchIds) : ctx.team.filter((p) => !p.starting);
  const capId = d.captain && d.captain.id;
  const viceId = d.vice && d.vice.id;
  return { xi, bench, capId, viceId };
}

/** 指标进度条：无值（null）整条不渲染 */
function metricBar(label, value, title) {
  if (typeof value !== "number") return "";
  const v = Math.max(0, Math.min(100, value));
  return `<div class="mb" title="${esc(title || label)}">
      <span class="mb-label">${label} <b>${v.toFixed(0)}%</b></span>
      <span class="mb-track"><span class="mb-fill" style="width:${v}%"></span></span>
    </div>`;
}

/** 球员卡：Form / Goal Potential / Fixture / TSB（design.md §4.3） */
function playerCard(p, capId, viceId, benchNo) {
  const badges = [];
  if (capId != null && capId === p.id) badges.push('<span class="badge badge-c">C</span>');
  else if (viceId != null && viceId === p.id) badges.push('<span class="badge badge-v">V</span>');
  const bd = p.score_breakdown || {};
  const no = benchNo != null ? `<span class="bench-no">${benchNo}</span>` : "";
  const bars =
    metricBar("Form", bd.form, "近期状态 Form%") +
    metricBar("Goal Pot.", bd.projection, "进球预测 Goal Potential%") +
    metricBar("Fixture", bd.fixture, "赛程友好度 Fixture% · 越高越友好") +
    metricBar("TSB", typeof p.selected_by === "number" ? p.selected_by : null, "持有率 TSB%");
  return `<div class="player-card">
      <div class="player-head">
        <span class="player-name">${no}${esc(p.name)}</span>${badges.join("")}
      </div>
      <div class="player-sub">${esc(p.pos)} · ${esc(p.team)} · £${p.price}m</div>
      <div class="player-bars">${bars}</div>
    </div>`;
}

/** 阵容视图：cur = 当前阵容（state.team + decision 布局）；sug = 转会后阵容 */
let squadView = "cur";

/** URL 参数可指定初始视图（?view=sug 用于直达/分享转会后阵容） */
function _resolveInitialView() {
  try {
    const v = new URLSearchParams(location.search).get("view");
    if (v === "sug" || v === "cur") squadView = v;
  } catch (_) {}
}
_resolveInitialView();

function squadViewEnabled(state) {
  return Array.isArray(state.suggested_squad) && state.suggested_squad.length > 0;
}

function renderSquadToggle(state) {
  const toggle = $("#squad-toggle");
  if (!toggle) return;
  toggle.hidden = !squadViewEnabled(state);
  toggle.querySelectorAll(".seg-btn").forEach((b) => {
    b.classList.toggle("active", b.dataset.v === squadView);
  });
}

function renderTeam(state) {
  const suggested = squadView === "sug" && squadViewEnabled(state)
    ? state.suggested_squad
    : null;
  const team = Array.isArray(state.team) ? state.team : [];
  const pool = suggested || team;
  const byId = new Map(pool.map((p) => [p.id, p]));
  const { xi, bench, capId, viceId } = playerResolve(state, {
    d: state.decision || {}, team: pool, byId, suggested: !!suggested,
  });

  const formation = (state.decision && state.decision.formation) || state.formation || "-";
  $("#formation-line").textContent = suggested
    ? `阵型 ${formation} · 转会后首发 ${xi.length} 人 / 替补 ${bench.length} 人`
    : `阵型 ${formation} · 首发 ${xi.length} 人 / 替补 ${bench.length} 人`;

  // 转会后摘要（视图注记，非转会时不显示）
  const noteEl = $("#squad-note");
  if (noteEl) {
    const rec = suggested && Array.isArray(state.decision && state.decision.recommended_transfers)
      ? state.decision.recommended_transfers
      : [];
    const parts = rec
      .map((t) => `${esc((t.out || {}).name || "?")} → ${esc((t.in || {}).name || "?")}`)
      .join("；");
    noteEl.textContent = parts ? `按 AI 建议（${rec.length} 笔）：${parts}` : "";
  }

  const rows = ["GKP", "DEF", "MID", "FWD"]
    .filter((pos) => xi.some((p) => p.pos === pos))
    .map((pos) => {
      const cards = xi
        .filter((p) => p.pos === pos)
        .map((p) => playerCard(p, capId, viceId))
        .join("");
      return `<div class="pitch-row">
          <div class="pitch-pos">${POS_LABEL[pos] || pos}</div>
          <div class="pitch-cards">${cards}</div>
        </div>`;
    })
    .join("");
  $("#team-xi").innerHTML = rows;

  const benchOrder = [
    ...bench.filter((p) => p.pos === "GKP"),
    ...bench.filter((p) => p.pos !== "GKP"),
  ];
  $("#team-bench").innerHTML = benchOrder
    .map((p, idx) => playerCard(p, capId, viceId, idx + 1))
    .join("");
}

/* ---------------- Main ④ AI 思考日志（本轮） ---------------- */

function renderThoughtsCurrent(entry) {
  const gwEl = $("#thoughts-gw");
  const bodyEl = $("#thoughts-current");
  if (!entry) {
    gwEl.textContent = "";
    bodyEl.innerHTML = '<p class="muted">本轮决策尚未生成，暂无思考日志。</p>';
    return;
  }
  // 标题 + 决策时间（北京时间）——decision time 展示在正文上方（UX v1.0）
  const timeHtml = entry.decided_at
    ? `<span class="thoughts-time">决策时间 ${fmtTime(entry.decided_at)}（北京时间）</span>`
    : "";
  gwEl.innerHTML =
    `<span class="thoughts-gw-tag">GW${entry.gw} 的决策过程（最多展示 3 段，点开「更多细节」查看次要记录）</span>` +
    timeHtml;
  renderThoughtsInto(bodyEl, entry.notes);
}

/* ---------------- Main ⑤ 历史记录（Accordion） ---------------- */

function accText(v, kind) {
  return v == null ? nullText(kind) : fmtNumber(v, kind);
}

function renderHistoryAccordion(state, rows) {
  const el = $("#history-accordion");
  if (!rows.length) {
    el.innerHTML = '<p class="muted">暂无历史记录（第一轮 GW 结束后自动生成）。</p>';
    return;
  }
  const html = [...rows].reverse().map((r) => {
    const d = r.decision || {};
    const transfers = Array.isArray(d.recommended_transfers) ? d.recommended_transfers : [];
    const xferHtml = transfers.length
      ? transfers
          .map((t) => {
            const o = t.out || {};
            const i = t.in || {};
            return `<div class="xfer xfer-sm">
                <span class="x-out">${esc(o.name || "?")}</span>
                <span class="x-arrow">→</span>
                <span class="x-in">${esc(i.name || "?")}</span>
                <span class="muted xfer-reason-inline">${esc(t.reason || "")}</span>
              </div>`;
          })
          .join("")
      : '<p class="muted no-xfer">本轮未进行转会。</p>';

    // 预算复盘行（历史条目带 transfer_package 时展示；旧数据缺字段则跳过）
    const pkgH = (d.transfer_package) || {};
    let budgetHtml = "";
    if (transfers.length && pkgH.budget_before != null) {
      budgetHtml = `<p class="muted xfer-summary">预算：Bank ${fmtBank(pkgH.budget_before)} → 转会后 ${fmtBank(pkgH.budget_after)}（净花费 ${fmtMoney(pkgH.transfer_cost)}）</p>`;
    } else if (r.budget_before != null) {
      budgetHtml = `<p class="muted xfer-summary">预算：Bank ${fmtBank(r.budget_before)}${r.transfer_cost != null ? `，净花费 ${fmtMoney(r.transfer_cost)} → 转会后 ${fmtBank(r.budget_after)}` : ""}</p>`;
    }

    const notesHtml = (() => {
      const box = document.createElement("div");
      renderThoughtsInto(box, r.notes);
      return box.innerHTML;
    })();

    return `<details class="acc">
        <summary>
          <span class="acc-gw">GW${r.gw}</span>
          <span class="acc-kv">积分 <b>${accText(r.points, "points")}</b></span>
          <span class="acc-kv">当轮排名 <b>${accText(r.rank, "rank")}</b></span>
          <span class="acc-kv">总排名 <b>${accText(r.overall_rank, "rank")}</b></span>
        </summary>
        <div class="acc-body">
          <div class="acc-sec">
            <h4>比赛结果</h4>
            <dl class="kv-list">
              <div><dt>积分</dt><dd>${accText(r.points, "points")}</dd></div>
              <div><dt>Gameweek Rank</dt><dd>${accText(r.rank, "rank")}</dd></div>
              <div><dt>Overall Rank</dt><dd>${accText(r.overall_rank, "rank")}</dd></div>
            </dl>
          </div>
          <div class="acc-sec">
            <h4>转会选择</h4>
            <div>${xferHtml}${budgetHtml}</div>
          </div>
          <div class="acc-sec">
            <h4>AI 思考日志</h4>
            <div class="acc-thoughts">${notesHtml}</div>
          </div>
          <div class="acc-sec">
            <h4>决策时间</h4>
            <p class="muted">${fmtTime(r.decided_at)}</p>
          </div>
        </div>
      </details>`;
  }).join("");
  el.innerHTML = html;
}

/* ---------------- Main ⑥ 趋势曲线（三条，Tab 切换 + 手绘 SVG） ---------------- */

const TREND_META = [
  { key: "overall_rank", title: "Overall Rank", cn: "总排名", reverse: true, color: "#ffd166" },
  { key: "points", title: "GW Points", cn: "每轮积分", reverse: false, color: "#00ff87" },
  { key: "rank", title: "GW Rank", cn: "单轮排名", reverse: true, color: "#7c8cff" },
];
let trendKey = "overall_rank";

function trendLabel(m) {
  return `${m.title}（${m.cn}）`;
}

function renderTrendTabs() {
  $("#trend-tabs").innerHTML = TREND_META.map(
    (m) =>
      `<button type="button" role="tab" class="trend-tab${m.key === trendKey ? " active" : ""}" data-k="${m.key}">${trendLabel(m)}</button>`,
  ).join("");
  $("#trend-tabs").querySelectorAll(".trend-tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      trendKey = btn.dataset.k;
      renderTrendTabs();
      renderTrendChart(historyRowsAsc(historyJson));
    });
  });
}

function drawTrend(rows, meta) {
  const W = 680;
  const H = 260;
  const pad = { l: 58, r: 16, t: 22, b: 32 };
  const plotW = W - pad.l - pad.r;
  const plotH = H - pad.t - pad.b;
  const n = rows.length;
  if (!n) return '<p class="muted">暂无历史数据。</p>';

  const vals = rows.map((r) => {
    const v = r[meta.key];
    return v == null ? null : Number(v);
  });
  const valid = vals.filter((v) => v != null);
  if (!valid.length) {
    return '<p class="muted">暂无已结算数据，结算后即显示趋势曲线。</p>';
  }

  // 域名（rank 类反向映射：数字越小越靠上）
  const lo = Math.min(...valid);
  const hi = Math.max(...valid);
  const span = hi - lo || Math.max(Math.abs(hi) * 0.05, 1);
  const dLo = lo - span * 0.08;
  const dHi = hi + span * 0.08;
  const xAt = (i) => (n > 1 ? pad.l + (i / (n - 1)) * plotW : pad.l + plotW / 2);
  const yAt = (v) =>
    pad.t + (meta.reverse ? (v - dLo) / (dHi - dLo) : (dHi - v) / (dHi - dLo)) * plotH;

  let svg = `<svg class="trend-svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="${trendLabel(meta)}趋势图">`;

  // 网格线 + Y 轴刻度（rank 用千/百万压缩格式）
  const GRID = 4;
  for (let g = 0; g <= GRID; g++) {
    const f = g / GRID;
    const v = dLo + (dHi - dLo) * f;
    const y = yAt(v);
    svg += `<line class="t-grid" x1="${pad.l}" y1="${y}" x2="${W - pad.r}" y2="${y}"></line>`;
    svg += `<text class="t-y" x="${pad.l - 6}" y="${y + 3}" text-anchor="end">${fmtAxis(v)}</text>`;
  }

  // 反向轴提示
  if (meta.reverse) {
    svg += `<text class="t-hint" x="${W - pad.r}" y="${pad.t - 8}" text-anchor="end">排名越小越好，Y 轴反向 ↑</text>`;
  }

  // 折线（有值点按行号分段连线；null 断开）
  const xByRow = rows.map((_, i) => xAt(i));
  let path = "";
  let segOpen = false;
  vals.forEach((v, i) => {
    if (v == null) { segOpen = false; return; }
    const cmd = segOpen ? "L" : "M";
    path += `${cmd} ${xByRow[i].toFixed(1)},${yAt(v).toFixed(1)} `;
    segOpen = true;
  });
  if (path) {
    svg += `<path class="t-line" d="${path.trim()}" fill="none" stroke="${meta.color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"></path>`;
  }

  // 数据点 + X 轴 GW 标签
  const labelEvery = n > 18 ? Math.ceil(n / 18) : 1;
  const showValueText = valid.length <= 12;
  vals.forEach((v, i) => {
    const gwx = rows[i].gw;
    if (v != null) {
      const y = yAt(v);
      svg += `<circle class="t-dot" cx="${xByRow[i].toFixed(1)}" cy="${y.toFixed(1)}" r="3.2" fill="${meta.color}"></circle>`;
      if (showValueText) {
        const ty = y - 8 < pad.t ? y + 14 : y - 8;
        svg += `<text class="t-val" x="${xByRow[i].toFixed(1)}" y="${ty.toFixed(1)}" text-anchor="middle">${meta.key === "points" ? v : fmtAxis(v)}</text>`;
      }
    }
    if (gwx != null && i % labelEvery === 0) {
      svg += `<text class="t-gw" x="${xByRow[i].toFixed(1)}" y="${H - 10}" text-anchor="middle">GW${gwx}</text>`;
    }
  });

  svg += "</svg>";
  return svg;
}

function renderTrendChart(rows) {
  const meta = TREND_META.find((m) => m.key === trendKey) || TREND_META[0];
  const svg = drawTrend(rows, meta);
  const box = $("#trend-box");
  box.innerHTML = svg;
  // 供 debug/测试访问
  window.__lastTrend = { key: trendKey, rows: rows.length };
}

/* ---------------- External Links（「关于我」品牌卡 + 平台导航） ---------------- */

/** heroMetrics 中配置的 state 字段 → 展示元信息 */
const EXT_METRIC = {
  rank: { label: "Overall Rank", kind: "rank", get: (s) => s.rank },
  points: { label: "Total Points", kind: "points", get: (s) => s.points },
  season: { label: "赛季", kind: "text", get: (s) => s.season },
};

function renderExternalLinks(state) {
  const cfg = window.SITE_CONFIG || {};
  // 品牌头：优先新结构 brand{name,handle,tagline}；兼容旧 brandText
  const brand = (cfg.brand && (cfg.brand.name || cfg.brand.handle))
    ? cfg.brand
    : (cfg.brandText ? { name: cfg.brandText } : null);
  const links = Array.isArray(cfg.externalLinks) ? cfg.externalLinks : [];

  // 平台导航（视觉最弱：小图标 + 文字，仅作链接）
  const navHtml = links.length
    ? `<ul class="ext-list">` +
      links
        .map(
          (l) =>
            `<li><a href="${esc(l.url || "#")}" title="${esc(l.label)}" target="_blank" rel="noopener">
              <img src="${esc(l.icon || "")}" alt="${esc(l.label)}" loading="lazy"><span>${esc(l.label)}</span>
            </a></li>`,
        )
        .join("") +
      `</ul>`
    : "";

  // 核心数据行：从 state 实时取（与 ① 摘要卡同源）
  let heroHtml = "";
  if (state && Array.isArray(cfg.heroMetrics)) {
    const rows = cfg.heroMetrics
      .map((key) => EXT_METRIC[key])
      .filter(Boolean)
      .map((m) => {
        const raw = m.get(state);
        const val = m.kind === "text" ? esc(raw || "-") : fmtNumber(raw, m.kind);
        return `<div class="ext-hero-row"><span>${m.label}</span><b>${val}</b></div>`;
      });
    if (rows.length) heroHtml = `<div class="ext-hero">${rows.join("")}</div>`;
  }

  // 品牌头（视觉最大）
  let brandHtml = "";
  if (brand) {
    const name = esc(brand.name || "");
    const handle = brand.handle ? `<span class="ext-handle">${esc(brand.handle)}</span>` : "";
    const tagline = brand.tagline ? `<div class="ext-tagline">${esc(brand.tagline)}</div>` : "";
    brandHtml = `<div class="ext-brand">${name}${handle}</div>${tagline}`;
  }

  const hasCard = !!brand || links.length;
  const desktop = $("#external-links-desktop");
  const footer = $("#external-links-footer");
  if (hasCard) {
    desktop.innerHTML = `<h2>关于我</h2>${brandHtml}${heroHtml}${navHtml}`;
    footer.innerHTML = navHtml;
    desktop.classList.remove("ext-empty");
    footer.classList.remove("ext-empty");
  } else {
    desktop.innerHTML = "";
    footer.innerHTML = "";
    desktop.classList.add("ext-empty");
    footer.classList.add("ext-empty");
  }
}

/* ---------------- 兜底 / 状态 ---------------- */

function setLoadStatus(msg, isError = false) {
  const el = $("#load-status");
  if (!el) return;
  el.textContent = msg;
  el.classList.toggle("load-error", isError);
  el.style.display = "";
}

function renderEmpty() {
  $("#summary-cards").innerHTML = "";
  $("#this-week").innerHTML = "";
  $("#team-xi").innerHTML = "";
  $("#team-bench").innerHTML = "";
  $("#thoughts-gw").textContent = "";
  $("#thoughts-current").innerHTML = "";
  $("#history-accordion").innerHTML = '<p class="muted">暂无历史记录。</p>';
  $("#trend-box").innerHTML = "";
}

function bindSquadToggle(state) {
  const toggle = $("#squad-toggle");
  if (!toggle || toggle.dataset.bound) return;
  toggle.dataset.bound = "1";
  toggle.addEventListener("click", (e) => {
    const btn = e.target.closest(".seg-btn");
    if (!btn) return;
    squadView = btn.dataset.v;
    renderSquadToggle(state);
    renderTeam(state);
  });
  renderSquadToggle(state);
}

/* ---------------- 入口 ---------------- */

let historyJson = null; // 供 trend Tab 重绘使用

async function init() {
  setLoadStatus("正在读取最新 FPL 数据……");
  try {
    const [state, history] = await Promise.all([
      loadJSON("data/state.json"),
      loadJSON("data/history.json"),
    ]);
    historyJson = history;
    const rows = historyRowsAsc(history);
    const entry = curEntry(state, rows);

    $("#last-update").textContent = `数据更新时间: ${fmtTime(state.last_update)}`;

    renderProfile(state, rows);
    renderRecords(state, rows);
    renderRecentThought(entry);
    renderSummaryCards(state, rows);
    renderThisWeek(state, entry);
    bindSquadToggle(state);
    renderTeam(state);
    renderThoughtsCurrent(entry);
    renderHistoryAccordion(state, rows);
    renderTrendTabs();
    renderTrendChart(rows);
    renderExternalLinks(state);

    setLoadStatus("");
    $("#load-status").style.display = "none";
  } catch (err) {
    console.error(err);
    setLoadStatus(
      "数据加载失败：请检查 TEAM_ID、FPL API 状态，以及 npm run brain 的输出。",
      true,
    );
    renderEmpty();
    renderExternalLinks(null);
  }
}

init();
