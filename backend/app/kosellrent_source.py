"""Load the supplied Cardinal plugin without changing its business functions.

Only Cardinal's Telegram imports and import-time pip/filesystem setup are omitted.
The web adapter supplies UI, persistence and lifecycle; API methods compile unchanged.
"""

import ast
import hashlib
from pathlib import Path
from types import ModuleType, SimpleNamespace

SOURCE = Path(__file__).parent / "vendor/kosellrent/KOSellRent.py"
SOURCE_SHA256 = "4603ba8f4d6ff79663a16571ec27d616f0ce9b7aa69e71ed7d05a9b72dccfeff"


class Keyboard:
    """Telegram markup is presentation-only in the original plugin."""

    def __init__(self, *args, **kwargs):
        pass

    def add(self, *args, **kwargs):
        return self

    row = add


def load_source():
    source = SOURCE.read_bytes()
    if hashlib.sha256(source).hexdigest() != SOURCE_SHA256:
        raise RuntimeError("Контрольная сумма исходного KOSellRent не совпадает")
    tree = ast.parse(source, filename=str(SOURCE))
    kept = []
    for node in tree.body:
        if isinstance(node, ast.Import) and any(n.name == "telebot" for n in node.names):
            continue
        if isinstance(node, ast.ImportFrom) and node.module in {"telebot.types", "tg_bot"}:
            continue
        if isinstance(node, ast.Try) and any(
            isinstance(child, ast.ImportFrom) and child.module == "telebot.handler_backends"
            for child in node.body
        ):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            if isinstance(node.value.func, ast.Name) and node.value.func.id in {
                "_ensure_deps",
                "_migrate_plugin_dir",
            }:
                continue
        kept.append(node)
    tree.body = kept
    module = ModuleType("kosellrent_original")
    module.__file__ = str(SOURCE)
    module.__dict__.update(
        K=Keyboard,
        B=Keyboard,
        ContinueHandling=None,
        CBT=SimpleNamespace(PLUGIN_SETTINGS="web"),
        telebot=SimpleNamespace(),
        source_sha256=hashlib.sha256(source).hexdigest(),
    )
    exec(compile(tree, str(SOURCE), "exec"), module.__dict__)
    return module
