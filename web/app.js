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
        <td>${r.points}</td>
        <td>${fmtNumber(r.rank)}</td>
        <td>${fmtNumber(r.overall_rank)}</td>
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

async function init() {
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
  } catch (err) {
    console.error(err);
    renderEmpty("GitHub Actions 首次运行后会自动生成数据。请确认 TEAM_ID 已配置。");
  }
}

init();
