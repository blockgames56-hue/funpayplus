const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { webcrypto } = require("node:crypto");
const ts = require("typescript");
const code = ts.transpileModule(fs.readFileSync(path.join(__dirname, "../src/request-id.ts"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;

test("HTTP fallback generates unique RFC 4122 version 4 request identifiers", () => {
  let draws = 0;
  const context = { exports: {}, crypto: { getRandomValues(bytes) { draws++; return webcrypto.getRandomValues(bytes); } } };
  vm.runInNewContext(code, context);
  const ids = new Set();
  for (let i = 0; i < 1000; i++) {
    const id = context.exports.requestId();
    assert.match(id, /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/);
    ids.add(id);
  }
  assert.equal(ids.size, 1000);
  assert.equal(draws, 1000);
});

test("secure origins keep using the browser's native UUID implementation", () => {
  let calls = 0;
  const context = { exports: {}, crypto: { randomUUID() { calls++; return "native-uuid"; }, getRandomValues() { throw new Error("Unexpected fallback"); } } };
  vm.runInNewContext(code, context);
  assert.equal(context.exports.requestId(), "native-uuid");
  assert.equal(calls, 1);
});
