"""Verified FunPay avatar images cached in the local application storage."""

import asyncio
import hashlib
import io
import os
import time
from urllib.parse import urljoin

import httpx
from fastapi.responses import FileResponse
from PIL import Image, UnidentifiedImageError

from .avatar_urls import normalize_avatar_url
from .config import config
from .funpay import FunPayError

MAX_BYTES = 2 * 1024 * 1024
MAX_PIXELS = 4_000_000
MAX_AGE = 7 * 86400
FORMATS = {
    "JPEG": ("jpg", "image/jpeg"),
    "PNG": ("png", "image/png"),
    "WEBP": ("webp", "image/webp"),
    "GIF": ("gif", "image/gif"),
}
locks: dict[str, asyncio.Lock] = {}


def valid_avatar_url(url: str) -> bool:
    return normalize_avatar_url(url) == url


def image_format(data: bytes) -> tuple[str, str]:
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > MAX_PIXELS or image.format not in FORMATS:
                raise FunPayError("Неподдерживаемая аватарка FunPay")
            result = FORMATS[image.format]
            image.verify()
            return result
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError) as error:
        raise FunPayError("FunPay вернул не изображение") from error


async def download(url: str) -> bytes:
    async with httpx.AsyncClient(
        timeout=15,
        follow_redirects=False,
        trust_env=False,
        headers={"Referer": "https://funpay.com/", "User-Agent": "FunPayPlus/0.1"},
    ) as client:
        for _ in range(3):
            if not valid_avatar_url(url):
                raise FunPayError("Небезопасный адрес аватарки")
            try:
                async with client.stream("GET", url) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        url = urljoin(str(response.url), response.headers.get("location", ""))
                        continue
                    response.raise_for_status()
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > MAX_BYTES:
                            raise FunPayError("Аватарка слишком большая")
                    return bytes(content)
            except httpx.HTTPError as error:
                raise FunPayError("Не удалось загрузить аватарку FunPay") from error
    raise FunPayError("Слишком много перенаправлений аватарки")


async def cached_avatar(url: str) -> FileResponse:
    if not valid_avatar_url(url):
        raise FunPayError("Небезопасный адрес аватарки")
    digest = hashlib.sha256(url.encode()).hexdigest()
    directory = config.storage_dir / "avatars"
    directory.mkdir(parents=True, exist_ok=True)
    async with locks.setdefault(digest, asyncio.Lock()):
        existing = next(
            (
                p
                for p in directory.glob(f"{digest}.*")
                if p.suffix.lstrip(".") in {value[0] for value in FORMATS.values()}
            ),
            None,
        )
        if not existing or time.time() - existing.stat().st_mtime > MAX_AGE:
            try:
                data = await download(url)
                suffix, media_type = image_format(data)
                target = directory / f"{digest}.{suffix}"
                temporary = directory / f"{digest}.{os.getpid()}.tmp"
                temporary.write_bytes(data)
                os.replace(temporary, target)
                if existing and existing != target:
                    existing.unlink(missing_ok=True)
                existing = target
            except FunPayError:
                if not existing:
                    raise
        media_type = next(
            value[1] for value in FORMATS.values() if existing.suffix == f".{value[0]}"
        )
        return FileResponse(
            existing, media_type=media_type, headers={"Cache-Control": "private, max-age=86400"}
        )
