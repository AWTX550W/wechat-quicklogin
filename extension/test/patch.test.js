'use strict';
/*
 * 纯逻辑单测：验证「修复前 -> 修复后」的 iframe 属性 diff。
 * 运行：node --test extension/test/patch.test.js
 * 或：  node extension/test/patch.test.js   （打印人类可读的 diff）
 */
const assert = require('node:assert/strict');
const nt = require('node:test');
const WXQL = require('../content.js');

// 极简 DOM mock，只实现 content.js 用到的接口
function makeFrame(src, allow) {
  const attrs = Object.create(null);
  if (src !== undefined && src !== null) attrs.src = src;
  if (allow !== undefined && allow !== null) attrs.allow = allow;
  return {
    tagName: 'IFRAME',
    src: src || '',
    getAttribute(k) { return Object.prototype.hasOwnProperty.call(attrs, k) ? attrs[k] : null; },
    setAttribute(k, v) { attrs[k] = String(v); if (k === 'src') this.src = String(v); },
    dump() { return Object.assign({}, attrs); }
  };
}

const QRCONNECT = 'https://open.weixin.qq.com/connect/qrconnect?appid=wx123&redirect_uri=https%3A%2F%2Fexample.com%2Fcb&response_type=code&scope=snsapi_login&state=abc#wechat_redirect';

// ---- 修复前基线：站点缓存里的旧版 wxLogin.js 生成的 iframe ----
const BEFORE = makeFrame(QRCONNECT, null);
const beforeDump = BEFORE.dump();

// ---- 修复后 ----
WXQL.applyFramePatch(BEFORE);
const afterDump = BEFORE.dump();

function printDiff() {
  const keys = Array.from(new Set([...Object.keys(beforeDump), ...Object.keys(afterDump)]));
  console.log('=== 修复前 / 修复后 · iframe 属性 diff ===');
  keys.forEach(k => {
    const a = beforeDump[k] === undefined ? '(未设置)' : JSON.stringify(beforeDump[k]);
    const b = afterDump[k] === undefined ? '(未设置)' : JSON.stringify(afterDump[k]);
    console.log(`  ${k.padEnd(20)} ${a === b ? '=' : '≠'}  ${a}  ->  ${b}`);
  });
  console.log('  src 变化:', beforeDump.src === afterDump.src ? '无（不改业务参数）' : '有');
}

printDiff();

if (nt && nt.describe) {
  const describe = nt.describe, test = nt.test;
  describe('computeFramePatch', () => {
    test('识别微信 qrconnect 登录 iframe', () => {
      assert.equal(WXQL.computeFramePatch(QRCONNECT, null).target, true);
    });
    test('普通 iframe 不打补丁', () => {
      assert.equal(WXQL.computeFramePatch('https://example.com/ad.html', null).target, false);
      assert.equal(WXQL.applyFramePatch(makeFrame('https://example.com/ad.html', null)), false);
    });
    test('空 src 不误判', () => {
      assert.equal(WXQL.computeFramePatch('', null).target, false);
      assert.equal(WXQL.computeFramePatch(null, null).target, false);
    });
    test('修复前无 allow，修复后补齐 3 个 LNA token', () => {
      const f = makeFrame(QRCONNECT, null);
      assert.equal(f.getAttribute('allow'), null);                 // 修复前
      assert.equal(WXQL.applyFramePatch(f), true);
      const allow = f.getAttribute('allow');                      // 修复后
      WXQL.LNA_TOKENS.forEach(t => assert.ok(allow.includes(t), '缺少 ' + t));
      assert.equal(f.getAttribute('data-wxql-patched'), '1');
    });
    test('已有部分 token 时只补齐，不重复、不丢原有 permission', () => {
      const f = makeFrame(QRCONNECT, 'camera; local-network-access');
      WXQL.applyFramePatch(f);
      const allow = f.getAttribute('allow');
      const tokens = allow.split(/[;,\s]+/).filter(Boolean).map(s => s.toLowerCase());
      assert.ok(tokens.includes('camera'));
      // 精确 token 计数（不能按子串数：local-network-access 里也含 local-network）
      WXQL.LNA_TOKENS.forEach(t => {
        assert.equal(tokens.filter(x => x === t).length, 1, t + ' 出现重复或缺失');
      });
    });
    test('幂等：第二次 patch 不再写入', () => {
      const f = makeFrame(QRCONNECT, null);
      assert.equal(WXQL.applyFramePatch(f), true);
      const once = f.getAttribute('allow');
      assert.equal(WXQL.applyFramePatch(f), false);
      assert.equal(f.getAttribute('allow'), once);
    });
    test('绝不改写 src（业务参数保持原样）', () => {
      const f = makeFrame(QRCONNECT, null);
      WXQL.applyFramePatch(f);
      assert.equal(f.getAttribute('src'), QRCONNECT);
    });
    test('识别站点主动传 fast_login=0', () => {
      assert.equal(WXQL.hasFastLoginDisabled(QRCONNECT + '&fast_login=0'), true);
      assert.equal(WXQL.computeFramePatch(QRCONNECT + '&fast_login=1', null).fastLoginDisabled, false);
    });
    test('普通页面里的同名参数不被误判成微信登录框', () => {
      assert.equal(WXQL.isWeChatLoginFrame('https://example.com/?fast_login=0'), false);
      assert.equal(WXQL.hasFastLoginDisabled('https://example.com/?fast_login=0'), false);
      assert.equal(WXQL.computeFramePatch('https://example.com/?fast_login=0', null).target, false);
    });
    test('Chrome 145 新权限名 loopback-network / local-network 都在补丁里', () => {
      assert.ok(WXQL.LNA_TOKENS.includes('loopback-network'));
      assert.ok(WXQL.LNA_TOKENS.includes('local-network'));
      assert.ok(WXQL.LNA_TOKENS.includes('local-network-access'));
    });
  });
}
