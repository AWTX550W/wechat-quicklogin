'use strict';

function el(id) { return document.getElementById(id); }

function label(state) {
  return ({ granted: '<span class="ok">已允许</span>',
    prompt: '<span class="warn">待询问（首次会弹窗）</span>',
    denied: '<span class="bad">已阻止 · 需改回允许</span>' })[state] ||
    '<span>' + (state || '未知') + '</span>';
}

function queryActive() {
  chrome.tabs.query({ active: true, currentWindow: true }, function (tabs) {
    var tab = tabs && tabs[0];
    if (!tab) return;
    try { el('url').textContent = new URL(tab.url).hostname || tab.url; }
    catch (e) { el('url').textContent = tab.url || '—'; }

    chrome.tabs.sendMessage(tab.id, { type: 'WXQL_QUERY' }, function (res) {
      if (chrome.runtime.lastError || !res) {
        el('found').textContent = '未注入 / 刷新页面后重试';
        el('patched').textContent = '—';
        el('lna').innerHTML = label('unknown');
        return;
      }
      el('found').innerHTML = res.found
        ? '<span class="ok">检测到 ' + res.found + ' 个</span>'
        : '<span>本页没有微信登录框</span>';
      el('patched').innerHTML = res.patched
        ? '<span class="ok">' + res.patched + ' 个已补齐 allow</span>'
        : '<span>—</span>';
      el('lna').innerHTML = label(res.lna);
      if (res.fastLoginDisabled) {
        el('tip').textContent = '注意：这个站点自己传了 fast_login=0，主动关掉了快速登录，只能走二维码。可用 desktop-scan/ 的热键工具在电脑上识别。';
      }
    });
  });
}

el('diag').addEventListener('click', function () {
  chrome.runtime.sendMessage({ type: 'WXQL_OPEN_DIAG' });
});
el('perm').addEventListener('click', function () {
  el('tip').textContent =
    '点地址栏左侧的锁/设置图标 →「网站设置 / 权限」→ 把「本地网络访问」改成「允许」；' +
    'Chrome 142+ / Edge 143+ 首次会主动弹窗，记得点「允许」。之前点过「阻止」的必须在这里改回来。';
});
el('reload').addEventListener('click', function () {
  chrome.tabs.query({ active: true, currentWindow: true }, function (tabs) {
    if (tabs && tabs[0]) chrome.tabs.reload(tabs[0].id);
  });
  window.close();
});

queryActive();
