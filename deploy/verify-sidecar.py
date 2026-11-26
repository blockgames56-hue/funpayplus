"""Fail a release build if its embedded KOSell adapter differs from current sources."""

import sys
from pathlib import Path
from types import CodeType

from PyInstaller.archive.readers import CArchiveReader


def signature(code):
    # File paths/line tables can differ in a frozen module; executable code and
    # constants must match, including all nested functions and class methods.
    return (
        code.co_code,
        code.co_names,
        code.co_varnames,
        code.co_freevars,
        code.co_cellvars,
        code.co_flags,
        code.co_argcount,
        code.co_posonlyargcount,
        code.co_kwonlyargcount,
        tuple(signature(v) if isinstance(v, CodeType) else v for v in code.co_consts),
    )


def verify(executable):
    root = Path(__file__).resolve().parents[1]
    archive = CArchiveReader(str(executable))
    modules = archive.open_embedded_archive(
        next(name for name in archive.toc if name.startswith("PYZ"))
    )
    for name in ("kosellrent", "kosellrent_api", "kosellrent_schema", "kosellrent_source"):
        source = root / "backend/app" / f"{name}.py"
        expected = compile(source.read_bytes(), str(source), "exec", dont_inherit=True, optimize=0)
        if signature(modules.extract("app." + name)) != signature(expected):
            raise RuntimeError(
                f"Embedded app.{name} is stale. Rebuild the sidecar before bundling."
            )
    print("PASS: embedded KOSell adapter matches current source code")


if __name__ == "__main__":
    verify(Path(sys.argv[1]).resolve())
