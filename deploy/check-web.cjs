// Syntactic validation only. npm run typecheck performs full semantic checking.
const fs = require('node:fs');
const path = require('node:path');
const ts = require(process.env.TYPESCRIPT_PATH || 'typescript');
const root = path.resolve(__dirname, '..');
const files = fs.readdirSync(path.join(root, 'apps/web/src')).filter((name) => /\.tsx?$/.test(name)).map((name) => 'apps/web/src/' + name).concat('apps/web/vite.config.ts');
let errors = 0;
for (const file of files) {
  const source = fs.readFileSync(path.join(root, file), 'utf8');
  const result = ts.transpileModule(source, { fileName: file, reportDiagnostics: true, compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX, isolatedModules: true, strict: true,
  } });
  for (const diagnostic of result.diagnostics || []) {
    if (diagnostic.category === ts.DiagnosticCategory.Error) {
      errors++;
      process.stdout.write(file + ': ' + ts.flattenDiagnosticMessageText(diagnostic.messageText, '\n') + '\n');
    }
  }
  const ast = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, file.endsWith('tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  function visit(node) {
    if (node.kind === ts.SyntaxKind.AnyKeyword) { errors++; process.stdout.write(`${file}: explicit any is prohibited\n`); }
    ts.forEachChild(node, visit);
  }
  visit(ast);
}
process.stdout.write(`${errors ? 'FAIL' : 'PASS'}: TypeScript syntax, no explicit any (${files.length} files)\n`);
process.exitCode = errors ? 1 : 0;
