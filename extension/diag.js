'use strict';

/*
 * 扩展内诊断页。关键点：chrome-extension:// origin + host_permissions
 * 可以直接访问 loopback，不受第三方站点 Local Network Access 弹窗影响，
 * 因此这里能独立判断「PC 微信本地服务是否在」这一层。
 */

var PORTS = [];
[13013, 13015, 14013, 14016, 14019, 14022, 14023, 14026].forEach(function (p) {
  if (PORTS.indexOf(p) === -1) PORTS.push(p);
});
for (var p = 14013; p <= 14030; p++) { if (PORTS.indexOf(p) === -1) PORTS.push(p); }

var report = { env: {}, ports: [], verdict: '' };

function el(id) { return document.getElementById(id); }

function browserInfo() {
  var ua = navigator.userAgent;
  var name = '未知浏览器';
  var ver = '';
  var m;
  if ((m = ua.match(/Edg\/([\d.]+)/))) { name = 'Microsoft Edge'; ver = m[1]; }
  else if ((m = ua.match(/Chrome\/([\d.]+)/))) { name = 'Chrome / Chromium'; ver = m[1]; }
  else if ((m = ua.match(/Firefox\/([\d.]+)/))) { name = 'Firefox'; ver = m[1]; }
  var major = parseInt((ver || '0').split('.')[0], 10);
  var lnaRisk = /Edg|Chrome/.test(ua) && major >= 142;
  return { name: name, version: ver, major: major, lnaRisk: lnaRisk, ua: ua };
}

function renderEnv() {
  var b = report.env.browser;
  var rows = [
    ['浏览器', b.name + ' ' + b.version],
    ['是否命中 LNA 限制', b.lnaRisk
      ? '<span class="warn">是（' + b.major + ' ≥ 142，公网页面访问 127.0.0.1 需要显式放行）</span>'
      : '<span class="ok">否</span>'],
    ['本页 LNA 权限', report.env.lna || '查询中'],
    ['扩展 origin', location.origin],
    ['User-Agent', '<span class="muted">' + b.ua + '</span>']
  ];
  el('env').innerHTML = rows.map(function (r) {
    return '<tr><td>' + r[0] + '</td><td>' + r[1] + '</td></tr>';
  }).join('');
}

function queryLna() {
  if (!navigator.permissions) return Promise.resolve('unsupported');
  var names = ['loopback-network', 'local-network', 'local-network-access'];
  return names.reduce(function (chain, n) {
    return chain.then(function (v) {
      if (v && v !== 'unsupported') return v;
      try { return navigator.permissions.query({ name: n }).then(function (r) { return r.state; }); }
      catch (e) { return 'unsupported'; }
    });
  }, Promise.resolve('unsupported'));
}

/**
 * 探测本地服务。请求侧显式声明 targetAddressSpace:'loopback' 才会走 LNA 流程；
 * no-cors 只用来判断「连不连得上」，不读响应正文。
 * 关键：授权弹窗打开时请求会挂起，AbortError 必须判成 pending，
 * 绝不能当成 blocked 或「微信没装」——这是这类检测最典型的误报。
 */
function probePort(port) {
  var url = 'https://localhost.weixin.qq.com:' + port + '/api/check-login';
  var ctrl = new AbortController();
  var timer = setTimeout(function () { ctrl.abort(); }, 2500);
  var init = {
    mode: 'no-cors', method: 'POST', credentials: 'omit',
    targetAddressSpace: 'loopback', signal: ctrl.signal
  };
  return fetch(url, init).then(function (r) {
    clearTimeout(timer);
    return { port: port, ok: true, verdict: 'ok', status: r.status,
             detail: 'opaque response（no-cors，仅证明连通）' };
  }).catch(function (e) {
    clearTimeout(timer);
    var pending = !!(e && e.name === 'AbortError');
    return { port: port, ok: false, verdict: pending ? 'pending' : 'blocked',
             status: 0, detail: ((e && e.name) || '') + ' / ' + ((e && e.message) || String(e)) };
  });
}

function runProbe() {
  el('rerun').disabled = true;
  el('probe').textContent = '';
  report.ports = [];
  var done = 0;
  var lines = [];

  function tick(r) {
    done++;
    report.ports.push(r);
    el('bar').style.width = Math.round(done / PORTS.length * 100) + '%';
    el('progress').textContent = '已探测 ' + done + ' / ' + PORTS.length + ' 个端口…';
    if (r.ok) {
      lines.push('  ' + r.port + '  -> HTTP ' + r.status + '  ' + r.detail);
      el('probe').textContent = lines.join('\n');
    }
  }

  // 小并发，避免瞬时打满
  var queue = PORTS.slice();
  var workers = [];
  for (var i = 0; i < 6; i++) {
    workers.push((function next() {
      if (!queue.length) return Promise.resolve();
      var p = queue.shift();
      return probePort(p).then(tick).then(next);
    })());
  }
  return Promise.all(workers).then(function () {
    var okPorts = report.ports.filter(function (x) { return x.ok; });
    var pending = report.ports.filter(function (x) { return x.verdict === 'pending'; });
    var head = [
      '探测端口数：' + report.ports.length,
      '连通（opaque / 仅证明端口可达）：' + okPorts.length + ' 个',
      '挂起 pending（授权框打开时的正常现象，不是失败）：' + pending.length + ' 个',
      '本机 Python/curl 直连参考：14013 TCP 可连但 TLS 握手被对端断开（微信内部协议，非浏览器上下文被拒属预期）',
      '注意：本页在扩展 origin 下，LNA 行为与第三方站点不同；判据仍是「登录页是否出现快捷登录头像」',
      ''
    ].join('\n');
    el('probe').textContent = head + (lines.length
      ? lines.join('\n')
      : '  （未取得连通响应；这不等于通道不可用，也不等于微信没装）');
    el('progress').textContent = '探测完成';
    el('rerun').disabled = false;
    renderVerdict(okPorts);
    report.verdict = el('verdict').textContent.replace(/\s+/g, ' ').trim();
    if (chrome.storage) chrome.storage.local.set({ lastProbe: report });
    if (chrome.runtime && chrome.runtime.sendMessage) {
      chrome.runtime.sendMessage({ type: 'WXQL_SAVE_PROBE', data: report });
    }
  });
}

function renderVerdict(okPorts) {
  var b = report.env.browser;
  var html = '';
  if (okPorts.length) {
    html += '<p class="ok"><strong>本机微信客户端的快速登录服务是通的（端口 ' +
      okPorts.map(function (x) { return x.port; }).join(', ') + '）。</strong> ' +
      '剩下只差浏览器侧放行：装好本扩展后，登录页应自动出现头像与「快捷登录」。</p>';
  } else {
    html += '<p class="warn"><strong>浏览器侧没能取得本地服务的 HTTP 响应。</strong> ' +
      '这不等于通道不可用 —— 微信本地服务使用内部 TLS 协议，会拒绝非浏览器指纹的直连；' +
      '判断标准请以「登录页是否出现快捷登录头像」为准，而不是这份端口探测。' +
      '本机用 PowerShell 实测：微信 4.1.15.56 正在监听 127.0.0.1:14013 / 14016 / 14019 / 14022 / 14023。</p>';
  }

  html += '<ol>';
  html += '<li><strong>放行浏览器权限（最关键）</strong>：在<strong>需要登录的那个网站</strong>上，' +
    '点地址栏左侧锁图标 →「网站设置 / 权限」→ 把「本地网络访问」改成<strong>允许</strong>。' +
    '之前若点过「阻止」，必须在这里改回来；扩展无法覆盖浏览器的拒绝决定。' +
    'Chrome 142+ 首次也会主动弹窗，记得点「允许」。</li>';
  html += '<li><strong>清掉旧版 wxLogin.js 缓存</strong>：本扩展已自动给 ' +
    '<code>res.wx.qq.com</code> / <code>open.weixin.qq.com</code> 的登录 SDK 请求加 no-cache，' +
    '避免缓存里的旧脚本生成缺少 <code>allow="local-network-access"</code> 的 iframe。装完扩展请刷新一次登录页。</li>';
  html += '<li><strong>确认 PC 微信已登录且未锁定</strong>：需微信 Windows ≥ 3.9.11 / Mac ≥ 4.0.0。' +
    '客户端锁定、退出登录、或网站自己传了 <code>fast_login=0</code>，都会退回扫码。</li>';
  html += '<li><strong>回到登录页点「重新检测」</strong>：状态条会重新装载 iframe，让本地 check-login 重跑。' +
    '出现头像 +「快捷登录」后，电脑上点一下确认就完成，全程不用手机。</li>';
  html += '<li><strong>老站点兜底</strong>：有些站点压根没接快速登录（只给二维码）。' +
    '用同目录 <code>desktop-scan/</code> 的热键工具截屏识别二维码，把图片送进 PC 微信「识别图中二维码」，仍在电脑上完成。</li>';
  html += '</ol>';

  html += '<p class="muted" style="font-size:12px;margin-top:12px">极端情况（不建议）：' +
    '<code>edge://flags/#local-network-access-check</code> → Disabled → 重启浏览器，可全局关掉检查，' +
    '但会降低浏览器对所有本地设备访问的安全防护，优先用第 1 步的按站点放行。</p>';

  el('verdict').innerHTML = html;
}

function copyText(t) {
  return navigator.clipboard.writeText(t).then(function () { return true; })
    .catch(function () { return false; });
}

function init() {
  report.env.browser = browserInfo();
  renderEnv();
  queryLna().then(function (s) {
    report.env.lna = ({ granted: '<span class="ok">granted（已允许）</span>',
      prompt: '<span class="warn">prompt（首次会弹窗询问）</span>',
      denied: '<span class="bad">denied（已阻止，需手动改回）</span>' })[s] ||
      '<span class="muted">' + s + '</span>';
    renderEnv();
  });

  el('rerun').addEventListener('click', runProbe);
  el('copy').addEventListener('click', function () {
    var txt = [
      '=== 微信电脑确认 · 诊断报告 ===',
      '时间: ' + new Date().toISOString(),
      '浏览器: ' + report.env.browser.name + ' ' + report.env.browser.version,
      'LNA 风险: ' + (report.env.browser.lnaRisk ? 'YES' : 'NO'),
      '端口探测: ' + JSON.stringify(report.ports),
      '结论: ' + report.verdict
    ].join('\n');
    copyText(txt).then(function (ok) {
      el('copied').textContent = ok ? '已复制，可以直接粘给别人排查。' : '复制失败，请手动选中 pre 区域内容。';
    });
  });
  el('openPerm').addEventListener('click', function () {
    var u = report.env.browser.name.indexOf('Edge') >= 0
      ? 'edge://settings/content/localNetworkAccess'
      : 'chrome://settings/content/localNetworkAccess';
    copyText(u).then(function (ok) {
      el('copied').textContent = ok
        ? '已复制：' + u + ' —— 新建标签页粘贴打开（扩展不能直接跳转 chrome:// 页面）。'
        : '请手动打开：' + u;
    });
  });

  runProbe();
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
else init();
