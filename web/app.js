"use strict";

const $ = (sel) => document.querySelector(sel);

function fmtNumber(n) {
  return (n ?? 0).toLocaleString("zh-CN");
}

function fmtNum(n) {
  return typeof n === "number" ? n.toLocaleString("zh-CN", { maximumFractionDigits: 1 }) : "-";
}

function fmtTime(iso) {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("zh-CN", { hour12: false });
}

async function loadJSON(url) {
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`${url} -> HTTP ${resp.status}`);
  return resp.json();
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));
}

/* ---------------- AI 决策上下文（state.decision + state.team 分数行） ---------------- */

function decisionCtx(state) {
  const d = state.decision || {};
  const team = Array.isArray(state.team) ? state.team : [];
  const byId = new Map(team.map((p) => [p.id, p]));

  // 推荐 XI/替补：优先 decision 的 id 顺序（引擎已按位置分组 + 替补排序）
  const xiIds = (Array.isArray(d.starting_xi) ? d.starting_xi : []).map((o) => o.id);
  const benchIds = (Array.isArray(d.bench) ? d.bench : []).map((o) => o.id);
  const resolve = (ids) => ids.map((id) => byId.get(id)).filter(Boolean);

  const xi = xiIds.length ? resolve(xiIds) : team.filter((p) => p.starting);
  const bench = benchIds.length ? resolve(benchIds) : team.filter((p) => !p.starting);

  const capId = d.captain && d.captain.id;
  const viceId = d.vice && d.vice.id;
  return {
    d,
    team,
    xi,
    bench,
    capId,
    viceId,
    captain: capId ? byId.get(capId) || d.captain : null,
    vice: viceId ? byId.get(viceId) || d.vice : null,
  };
}

/* ---------------- 转会建议 ---------------- */

function renderAdvice(state) {
  const el = $("#transfer-advice");
  const d = state.decision;
  if (!d) {
    el.innerHTML = '<p class="muted">暂无建议数据（决策引擎首次运行后生成）。</p>';
    return;
  }
  const transfers = Array.isArray(d.recommended_transfers) ? d.recommended_transfers : [];
  const notes = Array.isArray(d.transfer_notes) ? d.transfer_notes : [];
  const weights = state.market && state.market.weights ? state.market.weights : null;

  let html = "";
  if (weights) {
    html += `<p class="muted advice-meta">权重 TSB ${weights.tsb} / Trend ${weights.trend}（GW${state.current_gw}）</p>`;
  }
  const src = d.squad_source || {};
  if (src.type === "establish") {
    html += '<p class="muted advice-meta">已执行新账号 establish：按 Market Score 生成 15 人阵容</p>';
  } else if (src.type === "auto_pick") {
    html += `<p class="muted advice-meta">当前阵容来自 GW${src.gw} FPL auto-pick</p>`;
  } else if (src.type === "picks") {
    html += `<p class="muted advice-meta">当前阵容来自 FPL（GW${src.gw}）</p>`;
  }
  if (d.transfer_status === "unlimited") {
    html += '<p class="muted advice-meta">新账号：转会次数暂不受限</p>';
  } else if (typeof d.free_transfers === "number") {
    html += `<p class="muted advice-meta">可用免费转会：${d.free_transfers} 次</p>`;
  }

  if (transfers.length) {
    html += `<div class="transfer-list">${transfers
      .map((t) => {
        const o = t.out || {};
        const i = t.in || {};
        const score = (p) => (typeof p.market_score === "number" ? p.market_score : "-");
        return `
        <div class="transfer-card">
          <div class="transfer-side">
            <div class="transfer-label">建议转出</div>
            <div class="transfer-name">${esc(o.name || "?")}</div>
            <div class="transfer-sub">${esc(o.pos || "-")} · ${esc(o.team || "-")} · £${o.price ?? "-"}m</div>
            <div class="transfer-score">Market Score ${score(o)}</div>
          </div>
          <div class="transfer-arrow">→</div>
          <div class="transfer-side">
            <div class="transfer-label">建议转入</div>
            <div class="transfer-name">${esc(i.name || "?")}</div>
            <div class="transfer-sub">${esc(i.pos || "-")} · ${esc(i.team || "-")} · £${i.price ?? "-"}m</div>
            <div class="transfer-score">Market Score ${score(i)}</div>
          </div>
          <div class="transfer-meta">
            <span class="badge badge-gap">Market Gap ${t.market_gap ?? "-"}</span>
            <span class="transfer-reason">${esc(t.reason || "")}</span>
          </div>
        </div>`;
      })
      .join("")}</div>`;
  } else {
    const reasons = notes.length
      ? notes.map((n) => `<p class="muted advice-note">· ${esc(n)}</p>`).join("")
      : '<p class="muted advice-note">· 市场共识未明显转向，暂无转会建议。</p>';
    html += `<div class="no-transfer">${reasons}</div>`;
  }
  el.innerHTML = html;
}

/* ---------------- 球员卡片（含 Phase 2 评分） ---------------- */

function scoreChips(p) {
  const has = (v) => typeof v === "number";
  const chips = [];
  if (has(p.market_score)) {
    chips.push(`<span class="chip chip-m" title="Market Score · 长期资产价值">M ${fmtNum(p.market_score)}</span>`);
  }
  if (has(p.lineup_score)) {
    chips.push(`<span class="chip chip-l" title="Lineup Score · 本轮价值（决定首发/替补）">本轮 ${fmtNum(p.lineup_score)}</span>`);
  }
  if (has(p.captain_score) && p.captain_score > 0) {
    chips.push(`<span class="chip chip-c" title="Captain Score · 单轮爆发（仅首发计算）">爆发 ${fmtNum(p.captain_score)}</span>`);
  }
  return chips.join("");
}

function breakdownTitle(p) {
  const bd = p.score_breakdown;
  if (!bd || typeof bd !== "object") return "";
  const labels = {
    projection: "进球预测",
    form: "近期状态",
    streak: "连续回报",
    clean_sheet: "零封概率",
    fixture: "赛程难度",
    attack: "进攻值",
    attack_potential: "爆发潜力",
  };
  const parts = Object.keys(labels)
    .filter((k) => typeof bd[k] === "number")
    .map((k) => `${labels[k]} ${fmtNum(bd[k])}`);
  return parts.length ? `成分：${parts.join(" · ")}` : "";
}

function playerCard(p, ctx, benchNo) {
  const badges = [];
  if (ctx.capId != null && ctx.capId === p.id) {
    badges.push('<span class="badge badge-c" title="AI 队长（本轮爆发）">C</span>');
  } else if (ctx.viceId != null && ctx.viceId === p.id) {
    badges.push('<span class="badge badge-v" title="AI 副队长">V</span>');
  }
  const name = benchNo != null ? `<span class="bench-no">${benchNo}</span>` : "";
  const oldMeta =
    `<span title="持有率">${p.selected_by ?? "-"}%</span>` +
    `<span title="FPL 官方近况">form ${p.form ?? "-"}</span>`;
  return `
    <div class="player-card" title="${esc(breakdownTitle(p))}">
      <div class="player-head">
        <span class="player-name">${name}${esc(p.name)}</span>${badges.join("")}
      </div>
      <div class="player-sub">${esc(p.pos)} · ${esc(p.team)} · £${p.price}m</div>
      <div class="player-score">${scoreChips(p)}</div>
      <div class="player-meta">${oldMeta}</div>
    </div>`;
}

/* ---------------- 区块渲染 ---------------- */

function renderOverview(state, ctx) {
  const d = ctx.d;
  const capName = ctx.captain ? ctx.captain.name : d.captain && d.captain.name;
  const viceName = ctx.vice ? ctx.vice.name : d.vice && d.vice.name;
  const xiTotal = ctx.xi.reduce((s, p) => s + (p.lineup_score || 0), 0);
  const meta = state.score_meta || {};
  const cards = [
    ["当前 GW", state.current_gw, "gameweek"],
    ["总积分", fmtNumber(state.points), ""],
    ["总排名", fmtNumber(state.rank), ""],
    ["银行", `£${state.bank}m`, ""],
    ["首发阵型", (d && d.formation) || state.formation || "-", "formation"],
    ["AI 队长", capName || "-", "captain"],
    ["AI 副队长", viceName || "-", "vice"],
    ["AI 首发总分", xiTotal ? fmtNum(xiTotal) : "-", "lineup-total"],
  ];
  if (meta.target_gw != null) {
    cards.push(["决策目标 GW", `GW${meta.target_gw}`, "target"]);
  }
  $("#overview").innerHTML = cards
    .map(
      ([label, value, cls]) => `
      <div class="card ${cls}">
        <div class="card-label">${label}</div>
        <div class="card-value">${value}</div>
      </div>`,
    )
    .join("");
}

function renderEngineMeta(state, ctx) {
  const meta = state.score_meta || {};
  const src = meta.score_source || {};
  const srcTxt = [
    ["projection", "进球", src.projection],
    ["clean_sheet", "零封", src.clean_sheet],
    ["fixture", "赛程", src.fixture],
  ]
    .map(([, label, v]) => `${label}=${v ?? "缺失"}`)
    .join(" · ");
  const warnings = Array.isArray(meta.warnings) ? meta.warnings : [];
  const warnTxt = warnings
    .filter((w) => !w.includes("选源"))
    .map((w) => `<div class="engine-warn">注意：${esc(w)}</div>`)
    .join("");
  $("#engine-meta").innerHTML =
    `<div class="engine-source">目标 GW${meta.target_gw != null ? meta.target_gw : "?"}` +
    ` · 数据源（FPL Joe）：${srcTxt}</div>${warnTxt}`;
}

const POS_LABEL = { GKP: "门将", DEF: "后卫", MID: "中场", FWD: "前锋" };

function renderTeam(state, ctx) {
  $("#formation-line").textContent =
    `首发 ${(ctx.d && ctx.d.formation) || state.formation || "-"} · ` +
    `${state.team.length} 名球员 · AI 首发总分 ${ctx.xi.reduce((s, p) => s + (p.lineup_score || 0), 0).toFixed(2)}`;

  // 首发按位置分行：门将 / 后卫 / 中场 / 前锋（自左向右、行内按 Lineup Score 排）
  const rows = ["GKP", "DEF", "MID", "FWD"]
    .filter((pos) => ctx.xi.some((p) => p.pos === pos))
    .map((pos) => {
      const cards = ctx.xi
        .filter((p) => p.pos === pos)
        .map((p) => playerCard(p, ctx))
        .join("");
      return `<div class="pitch-row">
          <div class="pitch-pos">${POS_LABEL[pos] || pos}</div>
          <div class="pitch-cards">${cards}</div>
        </div>`;
    })
    .join("");
  $("#team-xi").innerHTML = rows;

  // 替补展示：门将恒列第 1 位，其后 3 人按决策优先级（Lineup Score 降序）
  const benchOrder = [
    ...ctx.bench.filter((p) => p.pos === "GKP"),
    ...ctx.bench.filter((p) => p.pos !== "GKP"),
  ];
  $("#team-bench").innerHTML = benchOrder
    .map((p, idx) => playerCard(p, ctx, idx + 1))
    .join("");
}

function renderHistory(history) {
  const rows = history.history;
  if (!rows.length) {
    $("#history-table").innerHTML =
      '<p class="muted">暂无历史记录（第一轮 GW 结束后自动生成）。</p>';
    return;
  }
  const first = rows[rows.length - 1] || {};
  const m = first.metrics || {};
  const hasLineup = typeof m.team_lineup_score === "number";
  const hasCaptain = typeof m.captain_score === "number";
  const head = `
    <table>
      <thead><tr>
        <th>GW</th><th>积分</th><th>当轮排名</th><th>总排名</th>
        ${hasLineup ? "<th>AI 首发总分</th>" : ""}
        ${hasCaptain ? "<th>C 爆发分</th>" : ""}
      </tr></thead>`;
  const body = rows
    .map((r) => {
      const rm = r.metrics || {};
      return `<tr>
        <td>GW${r.gw}</td>
        <td>${r.points ?? "-"}</td>
        <td>${r.rank == null ? "-" : fmtNumber(r.rank)}</td>
        <td>${r.overall_rank == null ? "-" : fmtNumber(r.overall_rank)}</td>
        ${hasLineup ? `<td>${fmtNum(rm.team_lineup_score)}</td>` : ""}
        ${hasCaptain ? `<td>${fmtNum(rm.captain_score)}</td>` : ""}
      </tr>`;
    })
    .join("");
  $("#history-table").innerHTML = `${head}<tbody>${body}</tbody></table>`;
}

function renderEmpty(hint) {
  $("#overview").innerHTML = `
    <div class="card empty-card">
      <div class="card-label">暂无数据</div>
      <div class="card-value">等待第一次自动运行</div>
      <p class="muted">${hint}</p>
    </div>`;
  $("#team-xi").innerHTML = "";
  $("#team-bench").innerHTML = "";
  $("#history-table").innerHTML = '<p class="muted">暂无历史记录。</p>';
}

function setLoadStatus(msg, isError = false) {
  const el = $("#load-status");
  if (!el) return;
  el.textContent = msg;
  el.classList.toggle("load-error", isError);
  el.style.display = "";
}

async function init() {
  setLoadStatus("正在读取最新 FPL 数据……");
  try {
    const [state, history] = await Promise.all([
      loadJSON("data/state.json"),
      loadJSON("data/history.json"),
    ]);
    $("#season").textContent = `${state.season} 赛季`;
    $("#last-update").textContent = `数据更新时间: ${fmtTime(state.last_update)}`;
    const ctx = decisionCtx(state);
    renderOverview(state, ctx);
    renderEngineMeta(state, ctx);
    renderTeam(state, ctx);
    renderHistory(history);
    renderAdvice(state);
    setLoadStatus("");
    document.querySelector("#load-status").style.display = "none";
  } catch (err) {
    console.error(err);
    setLoadStatus(
      "数据加载失败：请检查 TEAM_ID、FPL API 状态，以及 npm run brain 的输出。",
      true,
    );
    renderEmpty("GitHub Actions 首次运行后会自动生成数据。请确认 TEAM_ID 已配置。");
    const advice = $("#transfer-advice");
    if (advice) {
      advice.innerHTML = '<p class="muted advice-note">数据加载失败，暂时无法生成建议。</p>';
    }
  }
}

init();
