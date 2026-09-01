"use strict";
// 本地前端预览：每次请求前自动同步 data → web/data（brain 更新后刷新页面即可），静态托管 web/
const http = require("http");
const fs = require("fs");
const path = require("path");
const sync = require("./sync.js");

const PORT = Number(process.env.PORT) || 8000;
const ROOT = path.join(__dirname, "..", "web");

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
};

http
  .createServer((req, res) => {
    sync();
    const urlPath = decodeURIComponent(new URL(req.url, "http://localhost").pathname);
    const rel = urlPath === "/" ? "index.html" : urlPath.replace(/^\/+/, "");
    const file = path.join(ROOT, rel);
    if (!file.startsWith(ROOT)) {
      res.writeHead(403);
      res.end("Forbidden");
      return;
    }
    fs.readFile(file, (err, buf) => {
      if (err) {
        res.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
        res.end("404 Not Found");
        return;
      }
      res.writeHead(200, { "Content-Type": MIME[path.extname(file)] || "application/octet-stream" });
      res.end(buf);
    });
  })
  .listen(PORT, () => {
    console.log(`FPL AI Manager 前端已启动: http://localhost:${PORT}`);
  });
