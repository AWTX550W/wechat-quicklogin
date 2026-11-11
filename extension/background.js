'use strict';

/*
 * 后台：
 *  - 打开扩展内诊断页（诊断页在 chrome-extension:// origin 下，带 host_permissions，
 *    可以直接探测本机微信客户端的本地服务，不受第三方站点的 LNA 限制）
 *  - 记录最近一次检测结果，供 popup 展示
 */

function openDiag() {
  chrome.tabs.create({ url: chrome.runtime.getURL('diag.html') });
}

chrome.runtime.onMessage.addListener(function (msg, sender, sendResponse) {
  if (!msg || !msg.type) return;
  if (msg.type === 'WXQL_OPEN_DIAG') {
    openDiag();
    sendResponse({ ok: true });
    return;
  }
  if (msg.type === 'WXQL_SAVE_PROBE') {
    chrome.storage.local.set({ lastProbe: msg.data || null });
    sendResponse({ ok: true });
    return;
  }
  if (msg.type === 'WXQL_GET_PROBE') {
    chrome.storage.local.get('lastProbe', function (r) {
      sendResponse({ data: r.lastProbe || null });
    });
    return true;
  }
});

chrome.runtime.onInstalled.addListener(function (details) {
  if (details.reason === 'install') {
    chrome.tabs.create({ url: chrome.runtime.getURL('diag.html') });
  }
});
