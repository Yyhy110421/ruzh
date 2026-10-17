"""Resumable, multi-connection HTTP downloader.

Public mirrors of model repositories are often slow per connection, so large
ONNX files are fetched as independent byte-range chunks written to sidecar part
files. Every completed chunk is kept on disk, which makes an interrupted
download resumable without re-fetching anything.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

USER_AGENT = "ruzh/1.0 (+offline model fetcher)"

#: Chunk size for the parallel path. Large enough to amortise request overhead.
CHUNK_SIZE = 8 * 1024 * 1024

#: Files below this size are fetched in a single request.
PARALLEL_THRESHOLD = 4 * 1024 * 1024


class DownloadError(RuntimeError):
    pass


def _request(url: str, *, headers: dict | None = None, timeout: float = 30):
    hdrs = {"User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    return urllib.request.urlopen(req, timeout=timeout)


def probe(url: str, timeout: float = 30) -> tuple[int | None, bool]:
    """Return (total_size, range_supported) for *url*.

    Model mirrors answer HEAD with a Content-Length but often stream GET without
    one, and ``Accept-Ranges`` is not always advertised, so the size is taken from
    HEAD and range support from a real one-byte ranged GET.
    """
    size: int | None = None

    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT}, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            length = resp.headers.get("Content-Length")
            if length and length.isdigit():
                size = int(length)
    except Exception:  # noqa: BLE001
        pass

    range_ok = False
    try:
        with _request(url, headers={"Range": "bytes=0-1"}, timeout=timeout) as resp:
            if resp.status == 206:
                range_ok = True
                content_range = resp.headers.get("Content-Range", "")
                if "/" in content_range:
                    tail = content_range.rsplit("/", 1)[-1].strip()
                    if tail.isdigit():
                        size = int(tail)
            elif size is None:
                length = resp.headers.get("Content-Length")
                if length and length.isdigit():
                    size = int(length)
    except Exception:  # noqa: BLE001
        pass

    return size, range_ok


def _fetch_range(url: str, start: int, end: int, dest: str, timeout: float) -> int:
    """Download inclusive byte range [start, end] into *dest*. Returns byte count."""
    expect = end - start + 1
    if os.path.isfile(dest) and os.path.getsize(dest) == expect:
        return expect
    tmp = dest + ".tmp"
    with _request(url, headers={"Range": f"bytes={start}-{end}"}, timeout=timeout) as resp:
        if resp.status != 206:
            raise DownloadError("服务器不支持分片下载")
        written = 0
        with open(tmp, "wb") as fh:
            while True:
                block = resp.read(1 << 20)
                if not block:
                    break
                fh.write(block)
                written += len(block)
    if written != expect:
        os.remove(tmp)
        raise DownloadError(f"分片长度不符：期望 {expect}，实际 {written}")
    os.replace(tmp, dest)
    return written


def _download_single(url: str, dest: str, timeout: float, on_bytes=None) -> int:
    tmp = dest + ".tmp"
    written = 0
    with _request(url, timeout=timeout) as resp, open(tmp, "wb") as fh:
        while True:
            block = resp.read(1 << 20)
            if not block:
                break
            fh.write(block)
            written += len(block)
            if on_bytes:
                on_bytes(len(block))
    os.replace(tmp, dest)
    return written


def download(
    url: str,
    dest: str,
    *,
    connections: int = 6,
    timeout: float = 60.0,
    on_progress=None,
    retries: int = 3,
) -> str:
    """Download *url* to *dest*, resuming whenever sidecar chunks already exist.

    ``on_progress(done_bytes, total_bytes)`` is called periodically.
    """
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)

    total, range_ok = probe(url, timeout=timeout)

    if not range_ok or total is None or total <= PARALLEL_THRESHOLD or connections <= 1:
        if os.path.isfile(dest) and total is not None and os.path.getsize(dest) == total:
            if on_progress:
                on_progress(total, total)
            return dest
        last_error: Exception | None = None
        for attempt in range(retries):
            streamed = 0

            def on_bytes(count: int) -> None:
                nonlocal streamed
                streamed += count
                if on_progress:
                    on_progress(streamed, total or streamed)

            try:
                size = _download_single(url, dest, timeout, on_bytes)
                if total is not None and size != total:
                    raise DownloadError(f"大小不符：期望 {total}，实际 {size}")
                return dest
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if os.path.exists(dest + ".tmp"):
                    os.remove(dest + ".tmp")
                time.sleep(1.5 * (attempt + 1))
        raise DownloadError(f"下载失败 {url}: {last_error}")

    if os.path.isfile(dest) and os.path.getsize(dest) == total:
        if on_progress:
            on_progress(total, total)
        return dest

    part_dir = dest + ".parts"
    os.makedirs(part_dir, exist_ok=True)

    chunks = [(i * CHUNK_SIZE, min((i + 1) * CHUNK_SIZE - 1, total - 1))
              for i in range((total + CHUNK_SIZE - 1) // CHUNK_SIZE)]

    def part_path(idx: int) -> str:
        return os.path.join(part_dir, f"{idx:05d}.part")

    done_bytes = 0
    progress_lock = threading.Lock()
    for idx, (start, end) in enumerate(chunks):
        path = part_path(idx)
        if os.path.isfile(path) and os.path.getsize(path) == end - start + 1:
            done_bytes += end - start + 1
    if on_progress:
        on_progress(done_bytes, total)

    errors: list[Exception] = []

    def worker(idx: int, start: int, end: int) -> None:
        nonlocal done_bytes
        path = part_path(idx)
        need = end - start + 1
        if os.path.isfile(path) and os.path.getsize(path) == need:
            return
        last: Exception | None = None
        for attempt in range(retries):
            try:
                _fetch_range(url, start, end, path, timeout)
                with progress_lock:
                    done_bytes += need
                    snapshot = (done_bytes, total)
                if on_progress:
                    on_progress(*snapshot)
                return
            except Exception as exc:  # noqa: BLE001
                last = exc
                time.sleep(1.0 * (attempt + 1))
        errors.append(DownloadError(f"分片 {idx} 失败: {last}"))

    with ThreadPoolExecutor(max_workers=max(1, connections)) as pool:
        futures = [pool.submit(worker, i, s, e) for i, (s, e) in enumerate(chunks)]
        for fut in futures:
            fut.result()

    if errors:
        raise errors[0]

    tmp_out = dest + ".join"
    with open(tmp_out, "wb") as out:
        for idx in range(len(chunks)):
            with open(part_path(idx), "rb") as src:
                shutil.copyfileobj(src, out, 1 << 20)
    size = os.path.getsize(tmp_out)
    if size != total:
        os.remove(tmp_out)
        raise DownloadError(f"合并后大小不符：期望 {total}，实际 {size}")
    os.replace(tmp_out, dest)
    shutil.rmtree(part_dir, ignore_errors=True)
    return dest


def _fetch_files(root: str, repo: str, files: list[str], *, mirror: str,
                 on_file=None, on_progress=None, connections: int = 6) -> str:
    """Download every not-yet-present file of *repo* into *root*."""
    base = f"{mirror}/{repo}/resolve/main"
    for rel in files:
        target = os.path.join(root, rel.replace("/", os.sep))
        if os.path.isfile(target) and os.path.getsize(target) > 0:
            if on_file:
                on_file(rel, 1.0)
            continue
        if on_file:
            on_file(rel, 0.0)
        download(f"{base}/{rel}", target, connections=connections, on_progress=on_progress)
        if on_file:
            on_file(rel, 1.0)
    return root


def _write_meta(root: str, payload: dict) -> None:
    with open(os.path.join(root, "model.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def download_pair(models_root: str, src: str, tgt: str, *, on_file=None, on_progress=None,
                  mirror: str | None = None, connections: int = 6) -> str:
    """Fetch every file a single language pair needs. Returns the model directory."""
    from . import models as model_registry

    spec = model_registry.get_spec(src, tgt)
    if spec is None:
        raise DownloadError(f"没有 {src}->{tgt} 的可用模型")

    mirror = (mirror or model_registry.DEFAULT_MIRROR).rstrip("/")
    root = model_registry.cache_dir(models_root, src, tgt)
    _fetch_files(root, spec["repo"], spec["files"], mirror=mirror, on_file=on_file,
                 on_progress=on_progress, connections=connections)
    _write_meta(root, {"repo": spec["repo"], "src": src, "tgt": tgt, "label": spec["label"]})
    return root


def download_route(models_root: str, src: str, tgt: str, *, on_file=None, on_progress=None,
                   mirror: str | None = None, connections: int = 6) -> list[str]:
    """Fetch every leg of a route (a pivoted pair needs two models)."""
    from . import models as model_registry

    legs = model_registry.resolve_route(src, tgt)
    if not legs:
        raise DownloadError(f"没有 {src}->{tgt} 的可用模型")
    return [
        download_pair(models_root, leg_src, leg_tgt, on_file=on_file,
                      on_progress=on_progress, mirror=mirror, connections=connections)
        for leg_src, leg_tgt in legs
    ]


def download_multilingual(models_root: str, key: str, *, on_file=None, on_progress=None,
                          mirror: str | None = None, connections: int = 6) -> str:
    """Fetch a multilingual engine (one model serving every language pair)."""
    from . import models as model_registry

    spec = model_registry.multilingual_spec(key)
    if spec is None:
        raise DownloadError(f"没有名为 {key} 的多语言模型")

    mirror = (mirror or model_registry.DEFAULT_MIRROR).rstrip("/")
    root = model_registry.multilingual_dir(models_root, key)
    _fetch_files(root, spec["repo"], spec["files"], mirror=mirror, on_file=on_file,
                 on_progress=on_progress, connections=connections)
    _write_meta(root, {"repo": spec["repo"], "key": key, "label": spec["label"]})
    return root
