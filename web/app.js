"use strict";

const $ = (sel) => document.querySelector(sel);

function fmtNumber(n) {
  return (n ?? 0).toLocaleString("zh-CN");
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

function playerCard(p) {
  const badge = p.is_captain
    ? '<span class="badge badge-c" title="队长">C</span>'
    : p.is_vice_captain
      ? '<span class="badge badge-v" title="副队长">V</span>'
      : "";
  return `
    <div class="player-card">
      <div class="player-head">
        <span class="player-name">${p.name}</span>${badge}
      </div>
      <div class="player-sub">${p.pos} · ${p.team} · £${p.price}m</div>
      <div class="player-meta">
        <span title="持有率">${p.selected_by}%</span>
        <span title="近况">form ${p.form}</span>
        <span title="总积分">${p.total_points} pts</span>
      </div>
    </div>`;
}

function renderOverview(state) {
  const cards = [
    ["当前 GW", state.current_gw, "gameweek"],
    ["总积分", fmtNumber(state.points), ""],
    ["总排名", fmtNumber(state.rank), ""],
    ["银行", `£${state.bank}m`, ""],
    ["阵型", state.formation || "-", ""],
    ["队长", state.captain || "-", "captain"],
    ["副队长", state.vice || "-", "vice"],
    ["下一截止", fmtTime(state.next_deadline), "deadline"],
  ];
  $("#overview").innerHTML = cards
    .map(
      ([label, value, cls]) => `
      <div class="card ${cls}">
        <div class="card-label">${label}</div>
        <div class="card-value">${value}</div>
      </div>`,
    )
    .join("");
  $("#formation-line").textContent = `首发 ${state.formation || "-"} · ${state.team.length} 名球员`;
}

function renderTeam(state) {
  const xi = state.team.filter((p) => p.starting);
  const bench = state.team.filter((p) => !p.starting);
  $("#team-xi").innerHTML = xi.map(playerCard).join("");
  $("#team-bench").innerHTML = bench.map(playerCard).join("");
}

function renderHistory(history) {
  const rows = history.history;
  if (!rows.length) {
    $("#history-table").innerHTML =
      '<p class="muted">暂无历史记录（第一轮 GW 结束后自动生成）。</p>';
    return;
  }
  const head = `
    <table>
      <thead><tr><th>GW</th><th>积分</th><th>当轮排名</th><th>总排名</th></tr></thead>`;
  const body = rows
    .map(
      (r) => `
      <tr>
        <td>GW${r.gw}</td>
        <td>${r.points ?? "-"}</td>
        <td>${r.rank == null ? "-" : fmtNumber(r.rank)}</td>
        <td>${r.overall_rank == null ? "-" : fmtNumber(r.overall_rank)}</td>
      </tr>`,
    )
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
    renderOverview(state);
    renderTeam(state);
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
