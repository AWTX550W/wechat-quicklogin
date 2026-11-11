/*
 * 微信电脑确认 · QuickLogin 修复  —  content script
 *
 * 作用：把 Chrome / Edge 142+ 之后失效的「微信网站应用快速登录」通道补回来。
 *
 * 原理（微信开放平台官方文档 2026-05-27 版）：
 *   微信登录 iframe 会向 https://localhost.weixin.qq.com:14013/api/check-login
 *   发请求；该域名 DNS 指向 127.0.0.1，由已登录的 PC 微信客户端在本地提供。
 *   请求成功 -> 页面显示头像 +「快捷登录」，电脑上点一下确认即可，无需手机扫码。
 *   请求失败 -> 静默回退成二维码登录。
 *   Chrome 142+ 把「公网页面访问 loopback」纳入 Local Network Access (LNA) 权限，
 *   且权限必须显式通过 iframe 的 allow 属性下放给跨域 iframe，否则请求被拦。
 *   很多站点的 wxLogin.js 是缓存里的旧版，生成的 iframe 没有 allow 属性 -> 快速登录挂掉。
 *
 * 本脚本不改网站业务、不碰账号凭证，只做两件事：
 *   1) 给微信登录 iframe 补齐 allow="local-network-access ..."（用户侧 DOM 补丁）
 *   2) 在页面上给一个可见的状态条，告诉用户当前通道是否可用、怎么放行权限
 *
 * 该文件同时可在 Node 下 require，用于跑纯函数单测（见 test/patch.test.js）。
 */
(function (root, factory) {
  'use strict';
  var api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.WXQL = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  // Chrome 145 起权限名拆成 loopback-network / local-network，
  // 旧名 local-network-access 仍是 alias。三个都下发，覆盖 142~145+ 各版本。
  var LNA_TOKENS = ['local-network-access', 'loopback-network', 'local-network'];

  // 微信开放平台扫码登录 iframe 的特征。以官方 qrconnect/confirm 域名为主，
  // 不用 fast_login= 这类宽泛参数做判据（普通页面也可能有同名参数，会误伤）。
  var WX_FRAME_RE = /open\.weixin\.qq\.com\/connect\/(?:qrconnect|confirm)|wxLogin|wxopensdk|scope=snsapi_login/i;

  /** 判断一个 iframe 的 src 是不是微信网站应用登录框 */
  function isWeChatLoginFrame(src) {
    if (!src || typeof src !== 'string') return false;
    return WX_FRAME_RE.test(src);
  }

  /** 把 LNA token 合并进已有的 allow 字符串；返回 newAllow 与 changed */
  function mergeAllow(currentAllow) {
    var raw = (currentAllow || '').trim();
    var parts = raw ? raw.split(/[\s;,]+/).filter(Boolean) : [];
    var lower = parts.map(function (s) { return s.toLowerCase(); });
    var changed = false;
    LNA_TOKENS.forEach(function (t) {
      if (lower.indexOf(t) === -1) { parts.push(t); changed = true; }
    });
    return { value: parts.join('; '), changed: changed };
  }

  /** 站点显式传了 fast_login=0 会主动关闭快速登录，需要识别出来（默认只提示，不擅自改业务参数） */
  function hasFastLoginDisabled(src) {
    if (!isWeChatLoginFrame(src)) return false;
    return /[?&]fast_login=0(?:&|#|$)/i.test(src);
  }

  /**
   * 计算一个 iframe 需要的补丁 —— 纯函数，单测直接打它
   * @returns {{target:boolean, allow:string, allowChanged:boolean, fastLoginDisabled:boolean}}
   */
  function computeFramePatch(src, currentAllow) {
    var target = isWeChatLoginFrame(src);
    var m = mergeAllow(currentAllow);
    return {
      target: target,
      allow: m.value,
      allowChanged: m.changed,
      fastLoginDisabled: target && hasFastLoginDisabled(src)
    };
  }

  /**
   * Permissions Policy 规范：allow 属性只在 iframe 装载新文档时生效，
   * 对已经加载完成的 iframe 事后 setAttribute 不起作用。所以补完属性必须重载一次。
   * 参考：MDN "Does a new allow value reach a page that has already loaded? No."
   * 只在真实浏览器环境做（Node 单测里 IS_BROWSER=false，保持纯函数行为）。
   */
  function scheduleReload(frame, src) {
    if (!IS_BROWSER || !src || frame.getAttribute('data-wxql-reloading') === '1') return;
    frame.setAttribute('data-wxql-reloading', '1');
    try {
      frame.setAttribute('src', 'about:blank');
      setTimeout(function () {
        try {
          frame.setAttribute('src', src);
        } finally {
          frame.removeAttribute('data-wxql-reloading');
        }
      }, 0);
    } catch (e) { frame.removeAttribute('data-wxql-reloading'); }
  }

  /** 把一个 patch 应用到真实 iframe 元素上，返回是否发生 DOM 写入 */
  function applyFramePatch(frame, opts) {
    var src = frame.getAttribute('src') || frame.src || '';
    var allow = frame.getAttribute('allow') || '';
    var p = computeFramePatch(src, allow);
    if (!p.target) return false;
    var wrote = false;
    if (p.allowChanged) {
      frame.setAttribute('allow', p.allow);
      frame.setAttribute('data-wxql-patched', '1');
      wrote = true;
      if (!(opts && opts.reload === false)) scheduleReload(frame, src);
    }
    return wrote;
  }

  /* -------------------- 以下只在浏览器 content script 环境执行 -------------------- */
  var IS_BROWSER = typeof document !== 'undefined' && typeof window !== 'undefined';
  var HAS_CHROME = typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.id;

  function scanFrames() {
    if (!IS_BROWSER) return 0;
    var frames = document.querySelectorAll('iframe');
    var hits = 0;
    for (var i = 0; i < frames.length; i++) {
      var f = frames[i];
      if (isWeChatLoginFrame(f.getAttribute('src') || f.src || '')) {
        hits++;
        applyFramePatch(f);
      }
    }
    return hits;
  }

  function startObserver(onChange) {
    if (!IS_BROWSER) return null;
    var ob = new MutationObserver(function (records) {
      var touched = false;
      records.forEach(function (r) {
        if (!r.addedNodes) return;
        for (var i = 0; i < r.addedNodes.length; i++) {
          var n = r.addedNodes[i];
          if (n.nodeType !== 1) continue;
          if (n.tagName === 'IFRAME' && applyFramePatch(n)) touched = true;
          if (n.querySelectorAll) {
            var inner = n.querySelectorAll('iframe');
            for (var j = 0; j < inner.length; j++) {
              if (applyFramePatch(inner[j])) touched = true;
            }
          }
        }
        // src / allow 属性被脚本后改的情况
        if (r.type === 'attributes' && r.target && r.target.tagName === 'IFRAME') {
          if (applyFramePatch(r.target)) touched = true;
        }
      });
      if (touched && typeof onChange === 'function') onChange();
    });
    ob.observe(document.documentElement || document, {
      childList: true, subtree: true, attributes: true,
      attributeFilter: ['src', 'allow']
    });
    return ob;
  }

  /* 状态条：Shadow DOM 隔离，避免被站点 CSS 污染 */
  var SHADOW_CSS = [
    ':host{all:initial}',
    '.box{position:fixed;right:18px;bottom:18px;z-index:2147483647;',
    'font:13px/1.5 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;',
    'background:#1e2430;color:#e8edf5;border:1px solid #3a4557;',
    'border-radius:10px;box-shadow:0 8px 28px rgba(0,0,0,.35);',
    'padding:12px 14px;min-width:288px;max-width:360px}',
    '.hd{font-weight:600;font-size:13px;margin-bottom:6px;display:flex;align-items:center;gap:7px}',
    '.dot{width:8px;height:8px;border-radius:50%;background:#f0b429;display:inline-block;flex:none}',
    '.dot.ok{background:#3ddc84}.dot.bad{background:#f0616d}',
    '.tx{font-size:12px;color:#aeb9c9;margin-bottom:9px}',
    '.row{display:flex;gap:7px;flex-wrap:wrap}',
    'button{all:unset;cursor:pointer;font-size:12px;padding:5px 10px;border-radius:6px;',
    'background:#2b3648;color:#e8edf5;border:1px solid #46556b}',
    'button:hover{background:#36455e}',
    'button.pri{background:#1a7f4b;border-color:#249c5e}',
    '.close{position:absolute;top:7px;right:9px;color:#8794a8;cursor:pointer;font-size:15px;background:none;border:none;padding:0}'
  ].join('');

  function mountBadge(opts) {
    if (!IS_BROWSER || !document.body) return null;
    if (document.getElementById('__wxql_host__')) return null;
    var host = document.createElement('div');
    host.id = '__wxql_host__';
    document.body.appendChild(host);
    var sh = host.attachShadow({ mode: 'open' });
    var style = document.createElement('style');
    style.textContent = SHADOW_CSS;
    sh.appendChild(style);

    var box = document.createElement('div');
    box.className = 'box';
    box.innerHTML =
      '<span class="close" title="关闭">&times;</span>' +
      '<div class="hd"><span class="dot" id="d"></span><span id="t">检测到微信登录</span></div>' +
      '<div class="tx" id="m">已补齐电脑端快速登录权限</div>' +
      '<div class="row">' +
      '<button class="pri" id="retry">重新检测</button>' +
      '<button id="perm">放行浏览器权限</button>' +
      '<button id="diag">诊断</button>' +
      '</div>';
    sh.appendChild(box);

    var $ = function (id) { return sh.getElementById(id); };
    sh.querySelector('.close').addEventListener('click', function () {
      try { localStorage.setItem(CLOSE_KEY, '1'); } catch (e) { /* 站点禁用存储时忽略 */ }
      host.remove();
    });

    $('retry').addEventListener('click', function () {
      scanFrames();
      $('m').textContent = reloadLoginFrame()
        ? '已重新发起电脑端检测，请看登录框是否出现头像/快捷登录'
        : '未找到微信登录框，请先打开登录页';
    });

    // 「放行」必须真的发一次请求：Chrome 的 LNA 授权弹窗只认用户手势里的真实请求。
    // 以前这里只给一段文字说明，用户还得自己去找设置，是这次调研补上的关键点。
    $('perm').addEventListener('click', function () {
      $('m').textContent = '已发起一次本地网络请求，浏览器应弹出「允许访问设备上的应用」——请点「允许」…';
      probeLocal(14013, 8000).then(function (r) {
        if (r === 'ok') {
          $('m').textContent = '已放行，正在重新装载登录框，稍候应出现头像 +「快捷登录」。';
          reloadLoginFrame();
          if (opts && opts.onRefresh) setTimeout(opts.onRefresh, 900);
        } else if (r === 'pending') {
          $('m').textContent = '授权弹窗还开着，请在浏览器弹窗里点「允许」，然后点「重新检测」。' +
            '（请求挂起属正常，不是微信没装）';
        } else {
          $('m').textContent = '仍被拦截。若之前点过「阻止」，请到地址栏左侧锁图标 →「网站设置」→' +
            '把「本地网络访问」改为「允许」，再回来点「重新检测」。扩展无法覆盖浏览器的拒绝决定。';
        }
      });
    });

    $('diag').addEventListener('click', function () {
      if (HAS_CHROME) chrome.runtime.sendMessage({ type: 'WXQL_OPEN_DIAG' });
    });

    if (opts && opts.onStatus) opts.onStatus(function (level, title, msg) {
      var d = $('d'); var t = $('t'); var m = $('m');
      if (!d || !t || !m) return;
      d.className = 'dot' + (level === 'ok' ? ' ok' : level === 'bad' ? ' bad' : '');
      t.textContent = title; m.textContent = msg;
    });

    return host;
  }

  /** 查询本页 LNA 权限状态（Chrome 145 拆名，逐个试） */
  function queryLnaState() {
    if (!IS_BROWSER || !navigator.permissions) return Promise.resolve('unsupported');
    var names = ['loopback-network', 'local-network', 'local-network-access'];
    return names.reduce(function (chain, name) {
      return chain.then(function (found) {
        if (found && found !== 'unsupported') return found;
        try {
          return navigator.permissions.query({ name: name }).then(function (r) { return r.state; });
        } catch (e) { return 'unsupported'; }
      });
    }, Promise.resolve('unsupported'));
  }

  /** 微信快捷登录会遍历本地端口，14013 最常见但不是唯一（社区有 13015 的案例） */
  var PROBE_PORTS = [14013, 13015, 14016, 14019, 14022, 14023];
  var CLOSE_KEY = 'wxql-banner-closed:v2';

  /**
   * 发一次真实的 loopback 请求。Chrome 的 LNA 授权弹窗只能由「用户手势内的一次真实请求」
   * 触发，脚本自己弹不出来。请求侧要显式声明 targetAddressSpace，才会走 LNA 流程。
   * 关键：授权框打开时请求会挂起，必须把 AbortError 判成 pending，
   * 不能当成 blocked（更不能报「微信没装」）——这是这类检测最典型的误报。
   */
  function probeLocal(port, ms) {
    if (!IS_BROWSER) return Promise.resolve('blocked');
    var url = 'https://localhost.weixin.qq.com:' + (port || 14013) + '/api/check-login';
    var ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
    var timer = setTimeout(function () { if (ctrl) ctrl.abort(); }, ms || 2500);
    var init = { mode: 'no-cors', method: 'POST', targetAddressSpace: 'loopback' };
    if (ctrl) init.signal = ctrl.signal;
    return fetch(url, init).then(
      function () { return 'ok'; },
      function (e) { return (e && e.name === 'AbortError') ? 'pending' : 'blocked'; }
    ).then(function (r) { clearTimeout(timer); return r; });
  }

  /** 权限状态优先、探测只作辅助的组合判断；超时永不解释为「客户端没装」 */
  function judgeLna() {
    return queryLnaState().then(function (state) {
      if (state === 'denied') return 'denied';
      if (state === 'granted') return 'granted';
      return probeLocal(14013).then(function (r) {
        if (state === 'unsupported' && r === 'ok') return 'granted';
        if (state === 'prompt' || r === 'pending') return 'prompt';
        return 'unknown';
      });
    });
  }

  /** 重新装载微信登录 iframe，让本地 check-login 探测重跑 */
  function reloadLoginFrame() {
    if (!IS_BROWSER) return false;
    var f = document.querySelector('iframe[data-wxql-patched]') ||
            Array.prototype.find.call(document.querySelectorAll('iframe'), function (x) {
              return isWeChatLoginFrame(x.getAttribute('src') || x.src || '');
            });
    if (!f) return false;
    var s = f.getAttribute('src');
    f.setAttribute('src', 'about:blank');
    setTimeout(function () { f.setAttribute('src', s); }, 60);
    return true;
  }

  function boot() {
    if (!IS_BROWSER || !HAS_CHROME) return;
    var setStatus = function () {};
    var badgeShown = false;

    function ensureBadge() {
      if (badgeShown) return;
      try { if (localStorage.getItem(CLOSE_KEY) === '1') return; } catch (e) { /* ignore */ }
      badgeShown = true;
      mountBadge({
        onStatus: function (fn) { setStatus = fn; },
        onRefresh: function () { refresh(); }
      });
    }

    function refresh() {
      var hits = scanFrames();
      if (!hits) return;
      ensureBadge();
      judgeLna().then(function (v) {
        if (v === 'denied') {
          setStatus('bad', '电脑端快速登录被浏览器拦截',
            '本站「本地网络访问」被记为「阻止」，扩展无法覆盖浏览器的拒绝决定。' +
            '请到地址栏左侧锁图标 →「网站设置」改回「允许」，再点「重新检测」。');
        } else if (v === 'prompt') {
          setStatus('warn', '需要在电脑上点一次「允许」',
            '点「放行浏览器权限」→ 浏览器会弹授权框 → 点「允许」。' +
            '授权框打开时请求会挂起，这是正常现象，不是微信没装。');
        } else if (v === 'granted') {
          setStatus('ok', '权限已放行 · 请核对微信客户端状态',
            '权限没问题。若仍只有二维码，多半是 PC 微信未登录/被锁定，或站点自己传了 fast_login=0；' +
            'granted 状态下探测失败不能归咎于权限。');
        } else {
          setStatus('warn', '检测到微信登录 · 已补齐权限',
            '无法确认权限状态。可点「重新检测」，或打开「诊断」看本机通道；' +
            '扫码前也可先用「诊断」排除微信客户端未登录的情况。');
        }
      });
    }

    // 权限被用户在站点设置里改动时自动重新判断
    ['loopback-network', 'local-network', 'local-network-access'].forEach(function (n) {
      try {
        navigator.permissions.query({ name: n }).then(function (status) {
          if (status) status.onchange = function () { refresh(); };
        });
      } catch (e) { /* 旧版本不支持这个权限名 */ }
    });

    startObserver(refresh);
    // DOM 就绪后扫一遍，再延迟扫一次（有些站点登录框是异步挂载的）
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', refresh);
    } else { refresh(); }
    setTimeout(refresh, 1500);
    setTimeout(refresh, 4000);

    chrome.runtime.onMessage.addListener(function (msg, sender, sendResponse) {
      if (msg && msg.type === 'WXQL_QUERY') {
        var frames = Array.prototype.filter.call(document.querySelectorAll('iframe'), function (f) {
          return isWeChatLoginFrame(f.getAttribute('src') || f.src || '');
        });
        judgeLna().then(function (verdict) {
          sendResponse({
            found: frames.length,
            patched: frames.filter(function (f) { return f.getAttribute('data-wxql-patched') === '1'; }).length,
            fastLoginDisabled: frames.some(function (f) {
              return hasFastLoginDisabled(f.getAttribute('src') || '');
            }),
            lna: verdict,
            url: location.href
          });
        });
        return true;
      }
    });
  }

  if (IS_BROWSER && HAS_CHROME) boot();

  return {
    LNA_TOKENS: LNA_TOKENS,
    PROBE_PORTS: PROBE_PORTS,
    isWeChatLoginFrame: isWeChatLoginFrame,
    mergeAllow: mergeAllow,
    hasFastLoginDisabled: hasFastLoginDisabled,
    computeFramePatch: computeFramePatch,
    applyFramePatch: applyFramePatch,
    scanFrames: scanFrames,
    queryLnaState: queryLnaState,
    probeLocal: probeLocal,
    judgeLna: judgeLna,
    reloadLoginFrame: reloadLoginFrame
  };
});
