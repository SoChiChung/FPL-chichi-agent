"use strict";
// npm start 入口：brain → build → serve，按顺序执行。
// 任一阶段失败立即以非零退出码结束，绝不启动服务器，避免旧数据看起来像最新数据。
const { spawnSync } = require("child_process");
const path = require("path");

const ROOT = path.join(__dirname, "..");

function detectPython() {
  for (const cmd of ["python", "python3"]) {
    const r = spawnSync(cmd, ["--version"], { stdio: "ignore" });
    if (!r.error && r.status === 0) return cmd;
  }
  return "python";
}

function runStep(label, cmd, args) {
  console.log(`[${label}] 执行: ${cmd} ${args.join(" ")}`);
  const r = spawnSync(cmd, args, { cwd: ROOT, stdio: "inherit" });
  if (r.error) {
    console.error(`[${label}] 失败: 无法执行 ${cmd}（${r.error.message}）`);
    return null;
  }
  if (r.status !== 0) {
    console.error(`[${label}] 失败: 退出码 ${r.status}`);
    return r.status;
  }
  console.log(`[${label}] 完成`);
  return 0;
}

function main() {
  console.log("=== FPL AI Manager 启动 ===");
  const brainCmd = process.env.FPL_BRAIN_CMD || detectPython();
  const brainArgs = process.env.FPL_BRAIN_ARGS
    ? process.env.FPL_BRAIN_ARGS.split(" ")
    : ["-m", "brain"];

  console.log("[1/3] 运行决策引擎（npm run brain）...");
  const brainCode = runStep("1/3 brain", brainCmd, brainArgs);
  if (brainCode !== 0) {
    process.exit(brainCode === null ? 1 : brainCode);
  }

  console.log("[2/3] 同步数据到 web/data/（npm run build）...");
  let count;
  try {
    count = require("./sync.js")();
  } catch (e) {
    console.error(`[2/3] 失败: ${e.message}`);
    process.exit(1);
  }
  console.log(`[2/3] 完成: 已同步 ${count} 个 JSON 文件`);

  console.log("[3/3] 启动静态服务器...");
  try {
    require("./serve.js");
  } catch (e) {
    console.error(`[3/3] 失败: ${e.message}`);
    process.exit(1);
  }
}

main();
