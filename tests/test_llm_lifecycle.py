"""The local model server must not outlive the work that needed it.

A 1.5B model holds about 1 GB resident. Measured on this 7.7 GB machine, leaving it running
while another model loaded produced MemoryError inside unrelated code, which is the worst
kind of failure: it looks like the other model is broken.
"""
import io
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from app import bootstrap  # noqa: E402

bootstrap.prepare()

from app.engines import local_llm  # noqa: E402

failures = 0


def check(ok, label):
    global failures
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}")
    if not ok:
        failures += 1


print("=" * 74)
print("本地大模型服务启停")
print("=" * 74)

if local_llm._server_exe() is None or local_llm._model_file() is None:
    print("  跳过：这台机器没装 llama-server.exe 或 .gguf")
    print(f"失败 {failures} 项")
    sys.exit(1 if failures else 0)

started = time.perf_counter()
base_url, model = local_llm.ensure_server()
print(f"  启动用时 {time.perf_counter()-started:.1f}s，模型 {model}")
check(base_url.startswith("http://127.0.0.1:"), f"server 起在本地：{base_url}")
port = int(base_url.split(":")[2].split("/")[0])
check(local_llm._healthy(port), "服务健康检查通过")

# Calling ensure_server again must reuse it, not start a second copy.
same_url, _ = local_llm.ensure_server()
check(same_url == base_url, "第二次调用复用同一个服务，没有重复启动")

check(local_llm.stop_server() is True, "stop_server 报告停掉了一个服务")
time.sleep(2)
check(not local_llm._healthy(port, timeout=1.0), "停掉之后端口不再响应")
check(local_llm.stop_server() is False, "再停一次返回 False（幂等）")

print()
print("=" * 74)
print(f"失败 {failures} 项")
print("=" * 74)
sys.exit(1 if failures else 0)
