'use strict';
/* 首跑侦察：把抖音 bdms/secsdk 三件真身丢进 Node vm 补环境跑，看能不能 init + 给 create_v2 签出 a_bogus。
   关键要看：① 能不能 LOAD/init ② 缺哪些环境(Proxy记miss) ③ 它会不会 fetch 服务器要策略(phone-home)
   ④ 对 create_v2 URL 走一遍 hook 后，URL/headers 里有没有冒出 a_bogus / bd-ticket-guard。 */
const fs = require('fs'), path = require('path'), vm = require('vm'), nodeCrypto = require('crypto');
const V = path.join(__dirname, 'vendor');
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36';
const HREF = 'https://creator.douyin.com/creator-micro/content/upload';
const misses = [];
function guard(name, base) {
  return new Proxy(base, { get(t, p) {
    if (p in t) return t[p];
    if (typeof p !== 'symbol') misses.push(name + '.' + String(p));
    return undefined;
  }, set(t, p, v) { t[p] = v; return true; } });
}
const store = () => { const m = new Map(); return { getItem:k=>m.has(''+k)?m.get(''+k):null, setItem:(k,v)=>m.set(''+k,''+v), removeItem:k=>m.delete(''+k), clear:()=>m.clear(), key:i=>[...m.keys()][i]??null, get length(){return m.size;} }; };
const evt = () => ({ addEventListener(){}, removeEventListener(){}, dispatchEvent:()=>true });
function makeEl(tag){ return { tagName:(''+(tag||'')).toUpperCase(), style:{}, dataset:{}, children:[], childNodes:[], setAttribute(){}, getAttribute:()=>null, appendChild:c=>c, removeChild:c=>c, cloneNode:()=>makeEl(tag), getContext:()=>({ getImageData:()=>({data:new Uint8ClampedArray(4)}), fillText(){}, getParameter:()=>0, getExtension:()=>null }), toDataURL:()=>'data:image/png;base64,iVBORw0KGgo=', querySelector:()=>null, querySelectorAll:()=>[], getElementsByTagName:()=>[], contains:()=>false, ...evt(), innerHTML:'', textContent:'', getBoundingClientRect:()=>({top:0,left:0,width:0,height:0,x:0,y:0}) }; }

const CAP = [];
function fakeResp(){ return { ok:true, status:200, statusText:'OK', url:'', headers:{ get:()=>null, has:()=>false, forEach(){} }, clone(){return fakeResp();}, json:()=>Promise.resolve({}), text:()=>Promise.resolve('{}'), arrayBuffer:()=>Promise.resolve(new ArrayBuffer(0)) }; }
const capFetch = function(input, o){ const url = typeof input==='string'?input:(input&&input.url)||String(input); CAP.push({url, headers:(o&&o.headers)||null}); return Promise.resolve(fakeResp()); };

const sandbox = {};
Object.assign(sandbox, { console, setTimeout, clearTimeout, setInterval, clearInterval, queueMicrotask,
  TextEncoder, TextDecoder, atob, btoa, URL, URLSearchParams, Headers: globalThis.Headers, Request: globalThis.Request, Response: globalThis.Response,
  performance, Promise, Math, JSON, Object, Array, String, Number, Boolean, RegExp, Error, Symbol, Map, Set, WeakMap, WeakSet, Proxy, Reflect, BigInt, Function,
  ArrayBuffer, Uint8Array, Uint8ClampedArray, Int8Array, Uint16Array, Int16Array, Uint32Array, Int32Array, Float32Array, Float64Array, DataView,
  parseInt, parseFloat, isNaN, isFinite, encodeURIComponent, decodeURIComponent, encodeURI, decodeURI, Date });
sandbox.window = sandbox; sandbox.self = sandbox; sandbox.globalThis = sandbox; sandbox.top = sandbox; sandbox.parent = sandbox;
sandbox.crypto = globalThis.crypto || nodeCrypto.webcrypto; sandbox.msCrypto = sandbox.crypto;
sandbox.localStorage = store(); sandbox.sessionStorage = store();
sandbox.screen = { width:1920, height:1080, availWidth:1920, availHeight:1040, colorDepth:24, pixelDepth:24 };
sandbox.location = { href:HREF, protocol:'https:', host:'creator.douyin.com', hostname:'creator.douyin.com', port:'', pathname:'/creator-micro/content/upload', search:'', hash:'', origin:'https://creator.douyin.com', toString(){return HREF;} };
sandbox.navigator = guard('navigator', { userAgent:UA, platform:'Win32', language:'zh-CN', languages:['zh-CN','zh'], onLine:true, cookieEnabled:true, webdriver:false, hardwareConcurrency:8, deviceMemory:8, maxTouchPoints:0, userAgentData:{brands:[],mobile:false,platform:'Windows'}, plugins:{length:0,item:()=>null}, clipboard:{}, sendBeacon:()=>true, permissions:{query:()=>Promise.resolve({state:'granted'})} });
let cookieStr = 'ttwid=1%7Cabc; passport_csrf_token=xyz';
const document = guard('document', { ...evt(), readyState:'complete', visibilityState:'visible', hidden:false, referrer:'https://creator.douyin.com/', title:'', URL:HREF, domain:'douyin.com', characterSet:'UTF-8', location:sandbox.location, currentScript:{src:'https://lf-security.bytegoofy.com/obj/security-secsdk/runtime_bundler_40.js', getAttribute:()=>null}, documentElement:makeEl('html'), body:makeEl('body'), head:makeEl('head'), createElement:t=>makeEl(t), createElementNS:(n,t)=>makeEl(t), getElementsByTagName:()=>[], getElementById:()=>null, querySelector:()=>null, querySelectorAll:()=>[], write(){}, writeln(){}, execCommand(){}, get all(){return [];} });
Object.defineProperty(document, 'cookie', { get:()=>cookieStr, set:v=>{cookieStr=''+v;}, configurable:true });
sandbox.document = document;
sandbox.history = { pushState(){}, replaceState(){}, back(){}, forward(){} };
Object.assign(sandbox, evt());
sandbox.requestAnimationFrame = cb=>setTimeout(()=>cb(Date.now()),16); sandbox.cancelAnimationFrame=id=>clearTimeout(id);
sandbox.matchMedia = ()=>({matches:false, addListener(){}, removeListener(){}, addEventListener(){}, removeEventListener(){}});
sandbox.getComputedStyle = ()=>({getPropertyValue:()=>''});
sandbox.PerformanceObserver = class { observe(){} disconnect(){} takeRecords(){return[];} };
sandbox.MutationObserver = class { observe(){} disconnect(){} takeRecords(){return[];} };
sandbox.RTCPeerConnection = class { createDataChannel(){return{};} close(){} addEventListener(){} };
sandbox.WebSocket = class { send(){} close(){} addEventListener(){} };
sandbox.XMLHttpRequest = class { open(u){this._u=u;} setRequestHeader(k,v){(this._h=this._h||{})[k]=v; if(/a_bogus|bd-ticket|bogus|argus/i.test(k))CAP.push({xhrHeader:k,v});} send(){ CAP.push({xhr:this._u, headers:this._h}); } getAllResponseHeaders(){return'';} addEventListener(){} abort(){} };
sandbox.Image = class {}; sandbox.Worker = class { postMessage(){} terminate(){} addEventListener(){} };
sandbox.fetch = capFetch;
sandbox.name=''; sandbox.origin='https://creator.douyin.com'; sandbox.innerWidth=1920; sandbox.innerHeight=969; sandbox.devicePixelRatio=1;

// 补首跑发现的 2 个 miss
document.createEvent = () => ({ initEvent(){}, initCustomEvent(){}, preventDefault(){}, stopPropagation(){} });
sandbox.SDKNativeWebApi = sandbox.SDKNativeWebApi || {};

vm.createContext(sandbox);
function load(name){ try { vm.runInContext(fs.readFileSync(path.join(V,name),'utf8'), sandbox, {filename:name, timeout:20000}); return 'ok'; } catch(e){ console.log('   [stack]', (e&&e.stack||'').split('\n').slice(0,4).join(' | ')); return 'ERR '+(e&&e.message); } }
console.log('load secsdk :', load('secsdk-lastest.umd.js'), '| secsdk?', typeof sandbox.secsdk);
console.log('load runtime:', load('runtime_bundler_40.js'), '| SDKRuntime?', typeof sandbox.SDKRuntime, '| _SdkGlueInit?', typeof sandbox._SdkGlueInit);
console.log('load glue   :', load('sdk-glue.js'), '| _SdkGlueInit?', typeof sandbox._SdkGlueInit);
console.log('window keys(部分):', Object.keys(sandbox).filter(k=>/sdk|sec|bdms|glue|acrawler|argus/i.test(k)));

// 尝试 init（页面上的调用形态）
if (typeof sandbox._SdkGlueInit === 'function') {
  try {
    sandbox._SdkGlueInit({ self:{aid:2906, pageId:33638}, bdms:{ paths:{ include:['/web/api/media/aweme/create_v2/','/aweme/v1/','/web/api/media/'] } } });
    console.log('_SdkGlueInit 调用: ok');
  } catch(e){ console.log('_SdkGlueInit 抛错:', e && e.message); }
}
console.log('fetch 被 hook 了吗:', sandbox.fetch !== capFetch);

// 走一条 create_v2，看 hook 有没有注入 a_bogus
(async ()=>{
  CAP.length = 0;
  const testUrl = 'https://creator.douyin.com/web/api/media/aweme/create_v2/?aid=2906&msToken=abc';
  try { const r = sandbox.fetch(testUrl, {method:'POST', headers:{'user-agent':UA}, body:'{}'}); if (r&&r.then) await r; } catch(e){ console.log('test fetch 抛:', e&&e.message); }
  console.log('\n--- 捕获的请求(看 phone-home + 是否签了) ---');
  for (const c of CAP.slice(0,12)) console.log('  ', JSON.stringify(c).slice(0,240));
  const signed = CAP.find(c => /a_bogus=|X-Bogus|bd-ticket|X-Argus/i.test(JSON.stringify(c)));
  console.log('\n出现 a_bogus/签名了吗:', !!signed, signed?JSON.stringify(signed).slice(0,200):'');
  console.log('\nphone-home(bdms拉策略?) 的外部请求:');
  for (const c of CAP) { const u=c.url||c.xhr||''; if (/bytegoofy|byteapi|zijieapi|secsdk|bdms|mssdk|\/get\//i.test(u)) console.log('  ->', u.slice(0,140)); }
  console.log('\n环境 miss 前25:', [...new Set(misses)].slice(0,25).join(', '));
})();
