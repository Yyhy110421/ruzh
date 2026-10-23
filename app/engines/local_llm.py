"""Translation through a local GGUF model served by llama.cpp.

The offline Marian/NLLB routes are translation-only models: they produce word-level
substitutions ("买方方", "预付款款", "45个历日") because nothing in them models grammar.
A small instruction model does, and on this machine a 1.5B Q4 model answers a contract
sentence in about three seconds - slower than the pivot route's two, and far better.

The model is not loaded by this process. llama-server.exe is started once and speaks the
OpenAI chat API, so the request path is the one already proven for hosted models; this
module only supervises the process and points that path at 127.0.0.1.
"""
from __future__ import annotations

import copy
import os
import socket
import subprocess
import time
from pathlib import Path

from ..config import APP_DIR
from .base import EngineSpec

#: Model and runtime live together so a customer package is one folder to copy.
SERVER_NAME = "llama-server.exe"
#: How long to wait for the process to answer /health on a cold start. A 1.5B model took
#: about 25 s to load here; four times that is a safe ceiling on a slower machine.
START_TIMEOUT = 100.0
#: A translation of one contract sentence took 2.6-5.5 s measured here.
REQUEST_TIMEOUT = 300.0

PROMPT = (
    "你是中俄贸易合同的专业翻译。把{src}翻成{tgt}，只输出译文，不要解释。\n"
    "硬性规则：\n"
    "1. рубль 是俄罗斯货币，一律译成「卢布」，绝不能译成「人民币」「元」。\n"
    "2. юань 是人民币，译成「人民币」。\n"
    "3. 所有数字原样照抄，不改写、不换算、不加千分位。\n"
    "4. НДС 译成「增值税」，предоплата 译成「预付款」，неустойка 译成「违约金」，"
    "срок поставки 译成「交货期」，календарный день 译成「日历日」。"
)

_started: dict[str, object] = {}


def _folders() -> list[Path]:
    return [APP_DIR / "llm", APP_DIR.parent / "llm", Path.cwd() / "_llm"]


def _server_exe() -> Path | None:
    for folder in _folders():
        if not folder.is_dir():
            continue
        for found in folder.rglob(SERVER_NAME):
            return found
    return None


def _model_file() -> Path | None:
    for folder in _folders():
        if not folder.is_dir():
            continue
        for found in sorted(folder.rglob("*.gguf")):
            return found
    return None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _healthy(port: int, timeout: float = 2.0) -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout) as resp:
            return b"ok" in resp.read(64)
    except Exception:  # noqa: BLE001
        return False


def ensure_server() -> tuple[str, str]:
    """Start llama-server if needed; return (base_url, model_name). Raises RuntimeError."""
    exe = _server_exe()
    model = _model_file()
    if exe is None or model is None:
        raise RuntimeError("本地模型未安装（缺 llama-server.exe 或 .gguf）")

    port = _started.get("port")
    if isinstance(port, int) and _healthy(port):
        return f"http://127.0.0.1:{port}/v1", str(_started.get("model"))

    port = _free_port()
    creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        [str(exe), "-m", str(model), "--port", str(port), "-t",
         str(max(2, (os.cpu_count() or 4) // 2))],
        cwd=str(exe.parent), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=creation,
    )
    deadline = time.time() + START_TIMEOUT
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"本地模型启动失败（退出码 {process.returncode}）")
        if _healthy(port):
            _started.update({"port": port, "process": process, "model": model.stem})
            return f"http://127.0.0.1:{port}/v1", model.stem
        time.sleep(1.0)
    try:
        process.terminate()
    except Exception:  # noqa: BLE001
        pass
    raise RuntimeError("本地模型启动超时")


def _availability(cfg, _models_root: str) -> tuple[bool, str]:
    missing = [name for name, found in (("llama-server.exe", _server_exe()),
                                        ("模型 .gguf", _model_file())) if found is None]
    if missing:
        return False, "缺 " + "、".join(missing)
    return True, "本地大模型已就绪（首次启动约 25 秒）"


def _factory(cfg, _models_root: str):
    from ..translator import TranslationRoute
    from .http_engine import OpenAIChatTranslator

    base_url, model = ensure_server()
    # A shallow copy so the local endpoint does not overwrite what the user typed into the
    # "openai-compatible" engine; both engines must be usable side by side.
    local = copy.copy(cfg)
    local.openai_base_url = base_url
    local.openai_model = model
    local.openai_prompt = PROMPT
    local.openai_timeout = REQUEST_TIMEOUT
    local.openai_api_key = "local"

    translator = OpenAIChatTranslator(local, cfg.source_lang, cfg.target_lang, cfg.translate)
    translator.label = "本地大模型 · " + model + "  " + cfg.source_lang + " -> " + cfg.target_lang
    route = TranslationRoute([translator], label=translator.label, is_pivot=False)
    # The model reads digits correctly on its own (measured: "600 000" came back intact),
    # while the [N1] placeholders made it write "600 000 人民币" nonsense.
    route.masks_numbers = False
    return route


LOCAL_LLM_SPEC = EngineSpec(
    id="local-llm",
    label="离线 · 本地大模型（质量优先）",
    kind="local",
    hint="不上网、不填 key。用打包进来的 llama.cpp + Qwen2.5-1.5B 直译中俄，"
         "一句约 3 秒，译文可以直接发给客户；比两跳小模型慢一倍，但不再是"
         "「买方方」「预付款款」这种词快。",
    factory=_factory,
    availability=_availability,
)
