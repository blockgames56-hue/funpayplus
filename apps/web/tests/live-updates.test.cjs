const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs"),
  path = require("node:path"),
  ts = require("typescript");
const code = ts.transpileModule(
  fs.readFileSync(path.join(__dirname, "../src/live-updates.ts"), "utf8"),
  {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
  },
).outputText;
const api = {};
new Function("exports", code)(api);
test(
  "burst of 200 events causes one refresh and keeps affected plugin scopes",
  { timeout: 2000 },
  async () => {
    let calls = 0,
      done;
    const completed = new Promise((resolve) => (done = resolve));
    const batch = api.createLiveRefresh((events) => {
      calls++;
      done(events);
    }, 15);
    for (let i = 0; i < 100; i++) {
      batch.push({ channel: "chats", data: {} });
      batch.push({ channel: "logs", data: { source_plugin: "kosell-rent" } });
    }
    assert.equal(calls, 0);
    const events = await completed;
    assert.equal(calls, 1);
    assert.equal(events.length, 2);
    assert(api.matchesLiveQuery(["dashboard", "/dashboard"], events));
    assert(api.matchesLiveQuery(["messages", 1], events));
    assert(api.matchesLiveQuery(["plugin-logs", "kosell-rent"], events));
    assert(!api.matchesLiveQuery(["plugin-logs", "steam-rent"], events));
    assert(!api.matchesLiveQuery(["settings", "/settings/app"], events));
    batch.dispose();
  },
);
test(
  "later windows refresh again and ignored pings do not trigger requests",
  { timeout: 2000 },
  async () => {
    let done;
    let completed = new Promise((resolve) => (done = resolve));
    let calls = 0;
    const batch = api.createLiveRefresh((events) => {
      calls++;
      done(events);
    }, 10);
    batch.push({ channel: "ping", data: {} });
    batch.push({ channel: "orders", data: {} });
    let events = await completed;
    assert.equal(calls, 1);
    assert(
      api.matchesLiveQuery(["kosell-rent", "/plugins/kosell-rent"], events),
    );
    completed = new Promise((resolve) => (done = resolve));
    batch.push({ channel: "lots", data: {} });
    events = await completed;
    assert.equal(calls, 2);
    assert(api.matchesLiveQuery(["lots", "/lots"], events));
    batch.dispose();
  },
);
test(
  "dispose cancels pending work and rejects late events",
  { timeout: 2000 },
  async () => {
    let calls = 0;
    const batch = api.createLiveRefresh(() => calls++, 5);
    batch.push({ channel: "chats", data: {} });
    batch.dispose();
    batch.push({ channel: "orders", data: {} });
    await new Promise((resolve) => setTimeout(resolve, 30));
    assert.equal(calls, 0);
  },
);

test("invalid WebSocket frames are ignored while valid frames remain usable", () => {
  for (const raw of ["not JSON", "null", "[]", "{}", '{"channel":"chats"}', '{"channel":"logs","data":null}', '{"channel":"logs","data":{"level":5}}']) assert.equal(api.parseLiveEvent(raw), null);
  assert.deepEqual(api.parseLiveEvent('{"channel":"chats","data":{"event":"NEW_MESSAGE"}}'), {channel:"chats",data:{event:"NEW_MESSAGE"}});
});

test("unknown prototype-like channels cannot break query refresh", () => {
  for (const channel of ["__proto__", "constructor", "toString"])
    assert.equal(api.matchesLiveQuery(["dashboard", "/dashboard"], [{channel, data:{}}]), false);
  let calls=0;const batch=api.createLiveRefresh(()=>calls++);
  batch.push({channel:"constructor",data:{}});batch.dispose();assert.equal(calls,0);
});
