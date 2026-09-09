"use strict";

/**
 * web/config.js — 站点配置常量（Phase 2.5 内容架构）
 * External Links 数据驱动：空数组时整个区块不渲染。
 * 图标放在 web/img/（由根目录 img/ 经 vercel build 复制而来）。
 */
window.SITE_CONFIG = {
  externalLinks: [
    { label: "小红书", url: "#", icon: "img/小红书icon.png" },
    { label: "B站", url: "#", icon: "img/bilibili.png" },
    { label: "懂球帝", url: "#", icon: "img/dqd.png" },
    { label: "微信", url: "#", icon: "img/wechat-logo.png" }
  ],
  brandText: "FPL紫葱酱",
};
