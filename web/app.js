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

/** 北京时间 M月D日 HH:MM（DDL 时间点展示；基于 UTC+8 换算） */
function fmtDateCN(iso) {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  const p2 = (n) => String(n).padStart(2, "0");
  const bj = new Date(d.getTime() + 8 * 3600 * 1000); // UTC → Asia/Shanghai
  return `${bj.getUTCMonth() + 1}月${bj.getUTCDate()}日 ${p2(bj.getUTCHours())}:${p2(bj.getUTCMinutes())}`;
}

/** 距截止倒计时文案：`3 天 21:05:43`；已过 → 「已截止」 */
function countdownText(iso) {
  const t = iso ? new Date(iso).getTime() : NaN;
  if (Number.isNaN(t)) return "";
  const diff = t - Date.now();
  if (diff <= 0) return "已截止";
  const p2 = (n) => String(n).padStart(2, "0");
  const s = Math.floor(diff / 1000);
  const dd = Math.floor(s / 86400);
  const hh = Math.floor((s % 86400) / 3600);
  const mm = Math.floor((s % 3600) / 60);
  const ss = s % 60;
  const cd = `${p2(hh)}:${p2(mm)}:${p2(ss)}`;
  return dd > 0 ? `${dd} 天 ${cd}` : cd;
}

/** ① DDL 倒计时走字：每秒刷新 #tw-countdown；过期定格「已截止」 */
let _cdTimer = null;
function startCountdown() {
  if (_cdTimer) { clearInterval(_cdTimer); _cdTimer = null; }
  const el = $("#tw-countdown");
  if (!el) return;
  const tick = () => {
    if (!el.isConnected) { clearInterval(_cdTimer); _cdTimer = null; return; }
    const txt = countdownText(el.dataset.iso);
    const over = txt === "已截止";
    el.textContent = txt;
    const wrap = el.closest(".tw-cd");
    if (wrap) {
      wrap.classList.toggle("tw-cd-over", over);
      const pre = wrap.querySelector(".tw-cd-pre");
      if (pre) pre.style.display = over ? "none" : "";
      if (over) { clearInterval(_cdTimer); _cdTimer = null; }
    }
  };
  tick();
  _cdTimer = setInterval(tick, 1000);
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

/* ---------------- 展示辅助（design-ia-ux.md §3.2 / §4.4） ---------------- */

/** 阵型 "352" → "3-5-2"（展示友好） */
function fmtFormation(f) {
  if (f == null || f === "-") return "-";
  const s = String(f).trim();
  return /^\d{3,4}$/.test(s) ? s.split("").join("-") : s;
}

/** GW 三态标签：已结算 = points/rank/overall_rank 任一已回填；否则按有无决策区分 */
function gwStateLabel(entry) {
  if (!entry) return { label: "未开始", cls: "st-pending" };
  const settled = entry.points != null || entry.rank != null || entry.overall_rank != null;
  return settled ? { label: "已结算", cls: "st-settled" } : { label: "进行中", cls: "st-live" };
}

/** 最近一笔已结算数据（用于「本轮待结算」卡上的参照小字） */
function lastSettledInfo(rows) {
  for (let i = rows.length - 1; i >= 0; i--) {
    const r = rows[i];
    if (r.points != null || r.rank != null || r.overall_rank != null) {
      return { gw: r.gw, points: r.points, rank: r.rank, overall_rank: r.overall_rank };
    }
  }
  return null;
}


async function loadJSON(url) {
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`${url} -> HTTP ${resp.status}`);
  return resp.json();
}

/* ---------------- 常量与映射 ---------------- */

const STYLE_LABEL = { market_consensus: "市场共识型" };

/** 站点品牌抬头（index.html <h1> 同步此名） */
const MANAGER_NAME = "ZCJenius";

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
  const formation = fmtFormation(
    favFormation(rows) || (state.decision && state.decision.formation) || state.formation,
  );

  $("#season").textContent = season;
  $("#profile-name").textContent = MANAGER_NAME;
  // 真实 FPL 队名（若有）作为 title 提示保留，不再占行
  $("#profile-name").title = state.manager_name || "";
  $("#profile-season").textContent = season;
  $("#p-style").textContent = styleLabel(snap && snap.strategy);
  $("#p-risk").textContent = riskLevel(snap);
  $("#p-formation").textContent = formation;

  // 移动端摘要条（同数据源）
  $("#m-name").textContent = MANAGER_NAME;
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

/* ---------------- Main ② 本轮成绩摘要卡 ---------------- */

function renderSummaryCards(state, rows) {
  const entry = curEntry(state, rows);
  const curPoints = entry ? entry.points : null;
  const curRank = entry ? entry.rank : null;
  const settled = lastSettledInfo(rows);
  const refNote = settled
    ? `最近已结算 GW${settled.gw}`
    : "";
  const refDetail = settled
    ? `${settled.points != null ? `${settled.points} 分` : ""}${settled.rank != null ? ` · 单轮 ${fmtAxis(settled.rank)}` : ""}`.replace(/^ · /, "")
    : "";

  const cards = [
    {
      label: "GW Points",
      value: accText(curPoints, "points"),
      cls: "",
      note: curPoints == null && refNote ? `${refNote}：${refDetail || "—"}` : refNote,
    },
    {
      label: "GW Rank",
      value: accText(curRank, "rank"),
      cls: "",
      note: curRank == null && refNote ? `参照 GW${settled.gw} 单轮排名` : (curRank != null ? `GW${entry && entry.gw}` : ""),
    },
    { label: "Total Points", value: fmtNumber(state.points, "points"), cls: "", note: "赛季累计" },
    { label: "Bank", value: fmtBank(state.bank), cls: "", note: "可用资金" },
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

  // GW 徽章 + 三态标签（design-ia-ux.md §2.3 / §3.2）
  const st = gwStateLabel(entry);
  const gwLine = `<div class="tw-gwline">
      <span class="gw-pill">GW${state.current_gw ?? "?"}</span>
      <span class="gw-state ${st.cls}">${st.label}</span>
    </div>`;

  // DDL 截止行：时间点 + 实时倒计时（主人 09-09 反馈；见 startCountdown）
  const dlIso = state.next_deadline;
  const ddlHtml = dlIso
    ? `<div class="tw-ddl">
        <span class="tw-dl-label">🕒 GW${state.current_gw ?? "?"} 截止 ${fmtDateCN(dlIso)}（北京时间）</span>
        <span class="tw-cd"><i class="tw-cd-pre">距截止</i><b id="tw-countdown" data-iso="${esc(dlIso)}">${countdownText(dlIso)}</b></span>
      </div>`
    : "";

  let html = gwLine + ddlHtml + `<div class="tw-meta">
      <span class="tw-item">阵型 <b>${esc(fmtFormation(d.formation || state.formation))}</b></span>
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
    // CTA：跳到 ③ 阵容区并切「转会后」视图（design-ia-ux.md §4.2）
    html += `<div class="tw-cta-row">
        <button type="button" class="leaf-btn" data-goto-sug>查看转会后阵容 🍃</button>
        <span class="muted tw-cta-hint">切换到「转会后」阵容视图对比</span>
      </div>`;
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
  // 当前阵容视图：XI/替补与 C/V 直接读 AI 决策（decision.starting_xi/bench/captain/vice）
  // 注：「转会后阵容」不再走这里，由 buildSuggestedPlan 前端推导（v1.4）。
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

/** 球员卡：Form / Goal Potential / Fixture / TSB（design.md §4.3）
 *  flags: { sug, inIds, outIds } —— sug 视图标「新入」，cur 视图标「拟转出」
 */
function playerCard(p, capId, viceId, benchNo, flags) {
  const badges = [];
  if (capId != null && capId === p.id) badges.push('<span class="badge badge-c">C</span>');
  else if (viceId != null && viceId === p.id) badges.push('<span class="badge badge-v">V</span>');
  if (flags) {
    if (flags.sug && flags.inIds && flags.inIds.has(p.id)) badges.push('<span class="badge badge-in">新入</span>');
    if (!flags.sug && flags.outIds && flags.outIds.has(p.id)) badges.push('<span class="badge badge-out">拟转出</span>');
  }
  const bd = p.score_breakdown || {};
  const no = benchNo != null ? `<span class="bench-no">${benchNo}</span>` : "";
  const bars =
    metricBar("Form", bd.form, "近期状态 Form%") +
    metricBar("Goal Pot.", bd.projection, "进球预测 Goal Potential%") +
    metricBar("Fixture", bd.fixture, "赛程友好度 Fixture% · 越高越友好") +
    metricBar("TSB", typeof p.selected_by === "number" ? p.selected_by : null, "持有率 TSB%");
  return `<div class="player-card">
      <div class="player-head">
        <span class="player-name">${no}${esc(p.name)}</span><span class="badge-group">${badges.join("")}</span>
      </div>
      <div class="player-sub">${esc(p.pos || "·")} · ${esc(p.team || "—")} · £${p.price ?? "?"}m</div>
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

/* ---- 「转会后阵容」推导（v1.4）
 * 目标：点「查看转会后阵容」时，不只是把新队员塞进阵容，而是——
 *  1) 从 AI 决策同一套首发/替补序列（decision.starting_xi / decision.bench）出发，
 *     把每笔转会 out→in 原位替换 → 得到完整 11 首发 + 4 替补（与后端
 *     _build_suggested_squad 的「保留出场槽位」语义一致）；
 *  2) 队长/副队长沿用 AI 决策 C/V（与 ① 本周动态同口径）；若 AI 队长恰好被转出，
 *     则按 captain_scores 从新首发里顶替选人；
 *  3) state.suggested_squad 降级为「入队球员卡面数据」字典（快照缺失也能渲染）。
 * 这样「转会后视图」不再出现 C 徽章与决策队长打架的问题。
 */

function transferMaps(state) {
  const rec = Array.isArray(state && state.decision && state.decision.recommended_transfers)
    ? state.decision.recommended_transfers
    : [];
  const outIds = new Set(rec.map((t) => t.out && t.out.id).filter((v) => v != null));
  const inIds = new Set(rec.map((t) => t.in && t.in.id).filter((v) => v != null));
  return { rec, outIds, inIds };
}

function squadViewEnabled(state) {
  if (!state) return false;
  const { rec } = transferMaps(state);
  if (!rec.length) return false; // 无建议转会 → 「转会后」无意义
  const teamOk = Array.isArray(state.team) && state.team.length > 0;
  const snapOk = Array.isArray(state.suggested_squad) && state.suggested_squad.length > 0;
  return teamOk || snapOk;
}

/** AI 决策 C/V 仍首发 → 沿用（与 ① 本周动态同口径）；否则按 captain_scores 在首发里顶替 */
function resolveCaptain(state, xiIds) {
  const d = state.decision || {};
  const cs = (state.captain_scores && typeof state.captain_scores === "object") ? state.captain_scores : {};
  const xi = new Set(xiIds);
  const cand = d.captain && d.captain.id != null ? d.captain.id : null;
  const vcand = d.vice && d.vice.id != null ? d.vice.id : null;
  const topByScore = (exclude) => {
    let best = null;
    let bv = -1;
    xiIds.forEach((id) => {
      if (id === exclude) return;
      const s = cs[id];
      if (typeof s === "number" && s > bv) { bv = s; best = id; }
    });
    return best;
  };
  let capId = cand != null && xi.has(cand) ? cand : (vcand != null && xi.has(vcand) ? vcand : topByScore(null));
  let viceId = null;
  if (capId != null) {
    viceId = vcand != null && vcand !== capId && xi.has(vcand) ? vcand : topByScore(capId);
  }
  return { capId, viceId };
}

/** 推导「转会后」完整阵容：返回 { xi, bench, capId, viceId }，球员对象池取优先序
 *  队内卡 → 后端快照卡（含完整 breakdown）→ 转会 in 简卡。 */
function buildSuggestedPlan(state) {
  const d = state.decision || {};
  const { rec, outIds } = transferMaps(state);
  if (!rec.length) return null;
  const team = Array.isArray(state.team) ? state.team : [];
  const snap = Array.isArray(state.suggested_squad) ? state.suggested_squad : [];
  const teamById = new Map(team.map((p) => [p.id, p]));
  const snapById = new Map(snap.map((p) => [p.id, p]));

  // 兜底：当前队缺失但后端快照完整 → 直接按快照标志渲染（保底可见）
  if (!team.length && snap.length >= 15) {
    const xi = snap.filter((p) => p.starting);
    const bench = snap.filter((p) => !p.starting);
    const cap = snap.find((p) => p.is_captain) || {};
    const vc = snap.find((p) => p.is_vice_captain) || {};
    return { xi, bench, capId: cap.id, viceId: vc.id, pool: snap };
  }
  if (!team.length) return null;

  // 主路径：AI 同一套首发/替补 id 序列（缺 layout 时退回 team.starting 标志）
  const xiIds = (Array.isArray(d.starting_xi) ? d.starting_xi : [])
    .map((o) => o.id).filter((v) => v != null);
  const benchIds = (Array.isArray(d.bench) ? d.bench : [])
    .map((o) => o.id).filter((v) => v != null);
  const baseXi = xiIds.length ? xiIds.slice() : team.filter((p) => p.starting).map((p) => p.id);
  const baseBench = benchIds.length ? benchIds.slice() : team.filter((p) => !p.starting).map((p) => p.id);
  const inBase = new Set([...baseXi, ...baseBench]);
  team.forEach((p) => { if (!inBase.has(p.id) && !baseBench.includes(p.id)) baseBench.push(p.id); });

  // 逐笔转会原位替换（out 在首发→首发同位；在替补→替补同位；不在 15 人→新队员挂替补末位）
  rec.forEach((t) => {
    const oid = (t.out || {}).id;
    const iid = (t.in || {}).id;
    if (oid == null || iid == null) return;
    const ix = baseXi.indexOf(oid);
    if (ix >= 0) { baseXi[ix] = iid; return; }
    const jx = baseBench.indexOf(oid);
    if (jx >= 0) { baseBench[jx] = iid; return; }
    if (!inBase.has(oid) && !baseBench.includes(iid)) baseBench.push(iid);
  });

  // 球员卡组装：队内卡 → 快照卡（含完整 breakdown/TSB）→ 转会 in 简卡（至少 name/pos/price）
  const cardById = (id) => {
    if (teamById.has(id)) return teamById.get(id);
    if (snapById.has(id)) return snapById.get(id);
    const tf = rec.find((t) => t.in && t.in.id === id);
    return tf && tf.in ? { ...tf.in } : null;
  };
  const seen = new Set();
  const pool = [];
  [...baseXi, ...baseBench].forEach((id) => {
    if (seen.has(id)) return;
    seen.add(id);
    const c = cardById(id);
    if (c) pool.push(c);
  });

  const { capId, viceId } = resolveCaptain(state, baseXi);
  const xiSet = new Set(baseXi);
  return {
    xi: pool.filter((p) => xiSet.has(p.id)),
    bench: pool.filter((p) => !xiSet.has(p.id)),
    capId,
    viceId,
    pool,
  };
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
  const { rec, outIds, inIds } = transferMaps(state);
  const isSug = squadView === "sug" && squadViewEnabled(state);

  // 转会后视图：从 AI 决策首发/替补序列推导完整阵容 + 队长（v1.4，见 buildSuggestedPlan）
  let xi, bench, capId, viceId;
  if (isSug) {
    const plan = buildSuggestedPlan(state);
    if (plan) { xi = plan.xi; bench = plan.bench; capId = plan.capId; viceId = plan.viceId; }
  }
  // 当前阵容视图（或推导失败兜底）：直接读 decision 布局
  if (!xi) {
    const team = Array.isArray(state.team) ? state.team : [];
    const byId = new Map(team.map((p) => [p.id, p]));
    const r = playerResolve(state, { d: state.decision || {}, team, byId, suggested: false });
    xi = r.xi; bench = r.bench; capId = r.capId; viceId = r.viceId;
  }

  // diff 角标数据：cur 视图高亮「拟转出」，sug 视图高亮「新入」
  const flags = { sug: isSug, inIds, outIds };

  const formation = fmtFormation((state.decision && state.decision.formation) || state.formation);
  $("#formation-line").textContent = isSug
    ? `阵型 ${formation} · 转会后首发 ${xi.length} 人 / 替补 ${bench.length} 人`
    : `阵型 ${formation} · 首发 ${xi.length} 人 / 替补 ${bench.length} 人`;

  // 转会后摘要（视图注记，非转会时不显示）
  const noteEl = $("#squad-note");
  if (noteEl) {
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
        .map((p) => playerCard(p, capId, viceId, undefined, flags))
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
    .map((p, idx) => playerCard(p, capId, viceId, idx + 1, flags))
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

/** 历史条目可见性（主人 09-09 反馈）：FPL 无真赛行的空洞轮（如新账号 GW2，
 *  无结果、无决策时间、无预算）整条不展示；有结算结果，或带决策时间/预算的
 *  「进行中」轮次保留并标注状态，不写「待结算」占位。 */
function historyVisible(r) {
  if (!r) return false;
  if (r.points != null || r.rank != null || r.overall_rank != null) return true;
  if (r.decided_at) return true;
  if (r.budget_before != null || r.budget_after != null) return true;
  return false;
}

/** 历史条目结算态：任一结果已回填 = 已结算；否则视为进行中/未开赛 */
function historySettled(r) {
  return r.points != null || r.rank != null || r.overall_rank != null;
}

function renderHistoryAccordion(state, rows) {
  const el = $("#history-accordion");
  const vis = rows.filter(historyVisible);
  if (!vis.length) {
    el.innerHTML = '<p class="muted">暂无历史记录（第一轮 GW 结束后自动生成）。</p>';
    return;
  }
  const html = [...vis].reverse().map((r) => {
    const settled = historySettled(r);
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

    // 摘要：已结算 → 结果三列；进行中 → 状态徽章（不写占位）
    const summaryKv = settled
      ? `<span class="acc-kv">积分 <b>${accText(r.points, "points")}</b></span>
         <span class="acc-kv">当轮排名 <b>${accText(r.rank, "rank")}</b></span>
         <span class="acc-kv">总排名 <b>${accText(r.overall_rank, "rank")}</b></span>`
      : `<span class="acc-state gw-state st-live">本轮进行中</span>`;

    // 展开区「比赛结果」：已结算 → 数值；进行中 → 说明行（不写「待结算」占位）
    const resultHtml = settled
      ? `<dl class="kv-list">
          <div><dt>积分</dt><dd>${accText(r.points, "points")}</dd></div>
          <div><dt>Gameweek Rank</dt><dd>${accText(r.rank, "rank")}</dd></div>
          <div><dt>Overall Rank</dt><dd>${accText(r.overall_rank, "rank")}</dd></div>
        </dl>`
      : `<p class="muted">本轮比赛进行中 / 尚未开赛，结束并结算后自动回填积分与排名。</p>`;

    return `<details class="acc">
        <summary>
          <span class="acc-gw">GW${r.gw}</span>
          ${summaryKv}
        </summary>
        <div class="acc-body">
          <div class="acc-sec">
            <h4>比赛结果</h4>
            ${resultHtml}
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
  { key: "overall_rank", title: "Overall Rank", cn: "总排名", reverse: true, color: "#E9A13B" },
  { key: "points", title: "GW Points", cn: "每轮积分", reverse: false, color: "#58A854" },
  { key: "rank", title: "GW Rank", cn: "单轮排名", reverse: true, color: "#4E9FD0" },
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
  // 与历史记录同口径：空洞轮（FPL 无真赛行，如新账号 GW2）不进入趋势 X 轴
  const vis = rows.filter(historyVisible);
  const meta = TREND_META.find((m) => m.key === trendKey) || TREND_META[0];
  const svg = drawTrend(vis, meta);
  const box = $("#trend-box");
  box.innerHTML = svg;
  // 供 debug/测试访问
  window.__lastTrend = { key: trendKey, rows: vis.length };
}

/* ---------------- External Links（「关于我」：品牌 + 平台导航，桌面左栏 & 移动端主区尾部） ---------------- */

function renderExternalLinks(state) {
  const cfg = window.SITE_CONFIG || {};
  // 品牌头：优先新结构 brand{name,handle,tagline}；兼容旧 brandText
  const brand = (cfg.brand && (cfg.brand.name || cfg.brand.handle))
    ? cfg.brand
    : (cfg.brandText ? { name: cfg.brandText } : null);
  const links = Array.isArray(cfg.externalLinks) ? cfg.externalLinks : [];

  // 平台导航（辅助：小图标 + 文字，仅作链接）
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

  // 品牌头（主人 09-09 反馈：头像在上 → 名字强调在下；tagline 已废弃不渲染）
  let brandHtml = "";
  if (brand) {
    const name = esc(brand.name || "");
    const handle = brand.handle ? `<span class="ext-handle">${esc(brand.handle)}</span>` : "";
    brandHtml = `<div class="ext-brand">${name}${handle}</div>`;
  }
  const avatar = cfg.avatar && brand
    ? `<img class="ext-avatar" src="${esc(cfg.avatar)}" alt="${esc(brand.name || brand.handle || "头像")}" loading="lazy">`
    : "";

  const hasCard = !!brand || links.length;
  const inner = `<h2>关于我</h2>${avatar}${brandHtml}${navHtml}`;
  // 桌面：左栏 Sidebar 挂载点；移动端：主区末尾挂载点（同一内容，CSS 控制各显示其一）
  [$("#external-links-desktop"), $("#external-links-mobile")].forEach((el) => {
    if (!el) return;
    el.innerHTML = hasCard ? inner : "";
    el.classList.toggle("ext-empty", !hasCard);
  });
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

/** ① 转会 CTA：滚动到 ③ 并切换「转会后」视图（design-ia-ux.md §4.2 #2） */
function bindThisWeekCta(state) {
  const sec = $("#this-week");
  if (!sec || sec.dataset.ctaBound) return;
  sec.dataset.ctaBound = "1";
  sec.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-goto-sug]");
    if (!btn) return;
    if (!squadViewEnabled(state)) return;
    squadView = "sug";
    renderSquadToggle(state);
    renderTeam(state);
    const target = document.getElementById("sec-squad");
    if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
  });
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
    startCountdown();
    bindThisWeekCta(state);
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
