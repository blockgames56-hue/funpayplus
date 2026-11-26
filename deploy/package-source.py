"""Create a GitHub-ready source ZIP without local accounts, keys or build outputs."""

import os
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {
    "node_modules", "target", "dist", "build", "binaries", "storage", "backups",
    ".git", ".venv", ".venv-core", ".verification", ".toolchains", ".npm-cache",
    "artifacts", "__pycache__", ".pytest_cache", ".ruff_cache", ".transfer",
}


def source_files(root: Path):
    for directory, folders, names in os.walk(root):
        folders[:] = sorted(name for name in folders if name not in EXCLUDED and not (Path(directory) / name).is_symlink())
        for name in sorted(names):
            file = Path(directory) / name
            if file.is_symlink() or not file.is_file():
                continue
            if file.name == ".env" or file.name.startswith(".env.") and file.name != ".env.example":
                continue
            if file.suffix in {".db", ".db-shm", ".db-wal", ".key", ".dpapi", ".log", ".pyc", ".spec", ".tsbuildinfo"}:
                continue
            yield file, file.relative_to(root).as_posix()


if __name__ == "__main__":
    destination = ROOT / "artifacts/FunPay-Plus-GitHub.zip"
    destination.parent.mkdir(exist_ok=True)
    with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
        count = 0
        for file, name in source_files(ROOT):
            archive.write(file, name)
            count += 1
    print(f"Готово: {destination} ({count} файлов, {destination.stat().st_size // 1024} КБ)")
