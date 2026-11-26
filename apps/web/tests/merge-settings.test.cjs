const {test}=require("node:test"), assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path"),ts=require("typescript");
const code=ts.transpileModule(fs.readFileSync(path.join(__dirname,"../src/merge-settings.ts"),"utf8"),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
const api={};new Function("exports",code)(api);
test("unchanged local settings adopt remote edits",()=>{assert.deepEqual(api.mergeSettings({a:1},{a:2},{a:1}),{a:2})});
test("local edits and unrelated remote settings both survive",()=>{assert.deepEqual(api.mergeSettings({a:1,b:1,nested:{x:1,y:1}},{a:1,b:2,nested:{x:2,y:1}},{a:3,b:1,nested:{x:1,y:4}}),{a:3,b:2,nested:{x:2,y:4}})});
test("arrays remain atomic and a pending local change wins the same field",()=>{assert.deepEqual(api.mergeSettings({list:[1],a:1},{list:[2],a:2},{list:[3],a:3}),{list:[3],a:3})});
