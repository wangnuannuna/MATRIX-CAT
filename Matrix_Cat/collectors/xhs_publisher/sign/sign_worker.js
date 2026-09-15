// Matrix_Cat · 小红书常驻签名 worker(纯 Node,不开浏览器)——出 X-s / X-t / X-S-Common 三件套
// 协议:NDJSON。启动先发一行 {"ready":true,"common":true};之后 stdin 每来一行请求,stdout 回一行结果。
//   请求: {"id":N,"path":"/api/...","data":{...}|null,"a1":"...","b1":"可选(本号真b1)","xt":可选钉死时间戳,"common":true/false}
//   响应: {"id":N,"ok":true,"xs":"XYW_...","xt":1784...,"xsc":"..."} 或 {"id":N,"ok":false,"err":"..."}
// 两块签名 JS 都是小红书自己的:bundle/xhs_webmsxyw.js 出 X-s/X-t、bundle/xhs_common.js 的 get_common 出 X-S-Common。
const readline = require('readline');
const fs = require('fs');
const path = require('path');
const _Buffer = Buffer;                                // webmsxyw bundle 会 delete Buffer / delete global,先存
const _now = Date.now;

// 压掉两 bundle 内部的 console.log 噪音(它们往 stdout 打会污染 NDJSON);转 stderr。
console.log = function () { try { process.stderr.write('[b] ' + Array.from(arguments).join(' ').slice(0, 60) + '\n'); } catch (e) {} };

// 共用浏览器环境 mock:navigator / window / localStorage(b1 可注入)/ document
// UA 必须与 curl 请求头的 UA、curl_cffi 的 JA3 impersonate 版本一致(X-S-Common 会编码 navigator.userAgent,
// 与请求头 UA 不自洽会被风控软封)。默认对齐 config.CURL_UA(Chrome131),并支持按请求覆盖。
const DEFAULT_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36';
globalThis.navigator = globalThis.navigator || { userAgent: DEFAULT_UA };
globalThis.window = globalThis.window || {};
globalThis.localStorage = { b1: '', getItem: function (k) { return this[k]; }, setItem: function (k, v) { this[k] = v; } };
globalThis.document = globalThis.document || { cookie: '' };

// 1) X-S-Common 生成器 get_common(先 eval,趁 Buffer 还在;非严格模式下函数声明会漏进本模块作用域)
let _hasCommon = false;
try {
  eval(fs.readFileSync(path.join(__dirname, 'bundle', 'xhs_common.js'), 'utf8'));
  _hasCommon = (typeof get_common === 'function');   // eslint-disable-line no-undef
} catch (e) { process.stderr.write('common bundle 加载失败: ' + e.message + '\n'); }
// 2) X-s/X-t 生成器(会 delete global/Buffer)
const { sign } = require('./bundle/xhs_webmsxyw.js');
if (typeof globalThis.Buffer === 'undefined') globalThis.Buffer = _Buffer;

function out(o) { process.stdout.write(JSON.stringify(o) + '\n'); }
out({ ready: true, common: _hasCommon });

const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
rl.on('line', (line) => {
  line = (line || '').trim();
  if (!line) return;
  let req;
  try { req = JSON.parse(line); } catch (e) { out({ ok: false, err: 'bad_json' }); return; }
  // op=gena1:本地生成一个全新 a1(蚁小二云端下发的就是它;getA1 来自 xhs_common.js)。
  if (req.op === 'gena1') {
    try { out({ id: req.id, ok: true, a1: getA1() }); }   // eslint-disable-line no-undef
    catch (e) { out({ id: req.id, ok: false, err: 'gena1: ' + String((e && e.message) || e) }); }
    return;
  }
  try {
    if (req.b1) globalThis.localStorage.b1 = req.b1;   // 注入本号真实 b1(指纹一致)
    if (req.ua) { try { globalThis.navigator.userAgent = req.ua; } catch (e) {} }  // UA 与请求头对齐
    if (req.xt) Date.now = () => req.xt;
    const r = sign(req.path, req.data, req.a1) || {};
    const xs = r['X-s'] || '', xt = r['X-t'];
    let xsc = '';
    if (_hasCommon && req.common !== false) {
      try { xsc = get_common({ 'X-s': xs, 'X-t': xt }, req.a1) || ''; } catch (e) { xsc = ''; }  // eslint-disable-line no-undef
    }
    // usedBodyStr:POST 时发送体必须用【和签名同一个 JS 引擎序列化】的串,否则字节漂移 → 签名失效。
    const usedBodyStr = (req.data !== null && req.data !== undefined) ? JSON.stringify(req.data) : '';
    out({ id: req.id, ok: true, xs: xs, xt: xt, xsc: xsc, usedBodyStr: usedBodyStr });
  } catch (e) {
    out({ id: req.id, ok: false, err: String((e && e.message) || e) });
  } finally {
    Date.now = _now;
  }
});
rl.on('close', () => process.exit(0));
