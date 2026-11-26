// Run with: node deploy/test-server-connection.cjs
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const ts = require('typescript');
const source = fs.readFileSync(path.join(__dirname, '../apps/web/src/server-connection.ts'), 'utf8');
const code = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
const token = '9999999999.signed-session';
function client(fetch) {
  const result = {};
  new Function('exports', 'fetch', 'AbortController', 'setTimeout', 'clearTimeout', 'URL', code)(result, fetch, AbortController, setTimeout, clearTimeout, URL);
  return result;
}
(async () => {
  let calls = 0;
  const good = client(async (url, options) => {
    calls++;
    assert.equal(options.credentials, 'omit'); assert.equal(options.redirect, 'error');
    if(url.endsWith('/api/auth/connect')) {
      assert.equal(options.method,'POST'); assert.equal(options.headers.Authorization,undefined);
      return {ok:true,json:async()=>({ok:true,session_token:token})};
    }
    assert.equal(url,'https://panel.example.com/api/health');
    assert.equal(options.headers.Authorization,`Bearer ${token}`);
    return {ok:true,json:async()=>({ok:true,database:'postgresql'})};
  });
  assert.equal(good.serverAddress(' https://panel.example.com/api/ '), 'https://panel.example.com');
  assert.equal(good.serverAddress('http://127.0.0.1:8080/'), 'http://127.0.0.1:8080');
  for (const address of ['example.com', 'ftp://host', 'https://user:pass@host', 'https://host/dashboard', 'https://host/?token=x', 'https://host/#x']) {
    assert.throws(() => good.serverAddress(address));
  }
  assert.equal(await good.checkServer('https://panel.example.com'), token);
  assert.equal(calls,2);
  for (const data of [null,{ok:false},{ok:true},{ok:true,session_token:'not-a-session'}]) {
    const bad=client(async()=>({ok:true,json:async()=>data}));
    await assert.rejects(bad.checkServer('https://panel.example.com'), /не найдена/);
  }
  for (const status of [401,403,404,500]) {
    const bad=client(async()=>({ok:false,status}));await assert.rejects(bad.checkServer('https://panel.example.com'));
  }
  const timeout=client((_url,options)=>new Promise((_resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')))));
  await assert.rejects(timeout.checkServer('https://panel.example.com',5), /не ответил/);
  console.log('PASS: URL validation, keyless connection, signed session, health verification, errors, timeout and no redirects');
})().catch(error=>{console.error(error);process.exitCode=1});
