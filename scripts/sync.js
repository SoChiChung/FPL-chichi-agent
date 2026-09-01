"use strict";
// 将 data/*.json 同步到 web/data/（与 Vercel 构建行为一致，跨平台版）
const fs = require("fs");
const path = require("path");

const SRC = path.join(__dirname, "..", "data");
const DST = path.join(__dirname, "..", "web", "data");

function sync() {
  fs.mkdirSync(DST, { recursive: true });
  let count = 0;
  for (const name of fs.readdirSync(SRC)) {
    if (name.endsWith(".json")) {
      fs.copyFileSync(path.join(SRC, name), path.join(DST, name));
      count++;
    }
  }
  return count;
}

if (require.main === module) {
  console.log(`已同步 ${sync()} 个 JSON 文件到 web/data/`);
}
module.exports = sync;
