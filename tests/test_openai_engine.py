"""Bring-your-own-model engine, exercised against a local fake endpoint.

The failure this test exists for was found by reading the code, not by running it: the
address a person naturally pastes - https://api.deepseek.com, which is what DeepSeek's own
documentation calls the base URL - was turned into .../chat/completions without the /v1,
and answered 404. Nothing in the software said why.

Everything here runs against a server this file starts, so it needs no network and no key.

Run: python tests/test_openai_engine.py
"""
import io
import json
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_LOG = open(ROOT / "tests" / "openai_engine.log", "w", encoding="utf-8", errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare()

from app.config import AppConfig  # noqa: E402
from app.engines.http_engine import (  # noqa: E402
    OpenAIChatTranslator, endpoint_of, explain_http_error)
from app.text_tool import translate_text  # noqa: E402

failures = 0


def check(ok: bool, label: str, extra: str = "") -> None:
    global failures
    if not ok:
        failures += 1
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}" + (f"  {extra}" if extra else ""))


class Fake:
    """A stand-in for an OpenAI-compatible endpoint, with a scripted behaviour."""

    def __init__(self, reply="译文", status=200, script=None):
        self.reply = reply
        self.status = status
        self.script = list(script or [])
        self.requests = []
        self.server = None
        self.port = 0

    def start(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                fake.requests.append({"path": self.path,
                                      "headers": dict(self.headers),
                                      "body": body})
                status = fake.script.pop(0) if fake.script else fake.status
                if status == 200:
                    payload = {"choices": [{"message": {"role": "assistant",
                                                        "content": fake.reply}}]}
                else:
                    payload = {"error": {"message": "脚本安排的失败"}}
                raw = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.port}"

    def stop(self):
        if self.server is not None:
            self.server.shutdown()


def make_cfg(base_url: str, key: str = "sk-test", model: str = "test-model"):
    cfg = AppConfig()
    cfg.source_lang, cfg.target_lang = "ru", "zh"
    cfg.translate.engine = "openai-compatible"
    cfg.openai_base_url = base_url
    cfg.openai_model = model
    cfg.openai_api_key = key
    cfg.redact_enabled = True
    return cfg

def main() -> int:
    print("=" * 78)
    print("一、接口地址：填到哪一层都认")
    print("=" * 78)
    # The bare host is the case that was broken. Every service documents its base URL
    # this way, so this is what a customer will paste first.
    cases = [
        ("https://api.deepseek.com", "https://api.deepseek.com/v1/chat/completions"),
        ("https://api.deepseek.com/", "https://api.deepseek.com/v1/chat/completions"),
        ("http://localhost:11434", "http://localhost:11434/v1/chat/completions"),
        ("https://api.openai.com", "https://api.openai.com/v1/chat/completions"),
        ("https://api.deepseek.com/v1", "https://api.deepseek.com/v1/chat/completions"),
        ("https://api.deepseek.com/v1/",
         "https://api.deepseek.com/v1/chat/completions"),
        ("https://api.deepseek.com/v1/chat/completions",
         "https://api.deepseek.com/v1/chat/completions"),
        # A path the user chose is theirs: a gateway under /api/openai must not be
        # rewritten into /api/openai/v1.
        ("https://gw.example.com/api/openai",
         "https://gw.example.com/api/openai/chat/completions"),
        ("", ""),
    ]
    for given, want in cases:
        got = endpoint_of(given)
        check(got == want, f"{given or '(空)'}", f"got {got!r}" if got != want else "")
    print()

    print("=" * 78)
    print("二、真的能调通（本地假接口）")
    print("=" * 78)
    fake = Fake(reply="总额600000卢布。")
    base = fake.start()
    try:
        cfg = make_cfg(base + "/v1")
        engine = OpenAIChatTranslator(cfg, "ru", "zh", cfg.translate)
        engine.load()
        out = engine.translate("Общая сумма 600 000 рублей.")
        check(out == "总额600000卢布。", f"译文回来了：{out!r}")
        check(len(fake.requests) == 1, f"只发了一次请求：{len(fake.requests)}")
        req = fake.requests[0] if fake.requests else {}
        check(req.get("path") == "/v1/chat/completions",
              f"打到了 {req.get('path')}")
        check(req.get("headers", {}).get("Authorization") == "Bearer sk-test",
              "带上了 API Key")
        body = req.get("body", {})
        check(body.get("model") == "test-model", f"模型名传对了：{body.get('model')}")
        check(body.get("temperature") == 0, "temperature 是 0（翻译不该有随机性）")
        messages = body.get("messages") or []
        check(len(messages) == 2 and messages[0]["role"] == "system",
              "是一句系统提示 + 一句原文")
        check("俄语" in messages[0]["content"] and "中文" in messages[0]["content"],
              f"提示词里写了方向：{messages[0]['content'][:40]}")
        check(messages[-1]["content"] == "Общая сумма 600 000 рублей.",
              "原文原样发过去了")
        print()

        print("=" * 78)
        print("三、填错地址，报的话要能照着改")
        print("=" * 78)
        # A 404 on a base URL without /v1 is the most likely first mistake, and the raw
        # status line leaves the customer with nothing to do.
        text = explain_http_error(404, "", "https://a.com/chat/completions")
        check("404" in text and "/v1" in text, f"404 提了 /v1：{text[:60]}")
        text = explain_http_error(401, "", "https://a.com/v1/chat/completions")
        check("Api Key" in text or "API Key" in text, f"401 提了密钥：{text[:60]}")
        print()

        print("=" * 78)
        print("四、整条产品链路：脱敏和数字保护一样生效")
        print("=" * 78)
        # The engine is not used alone; it sits behind Translator, which masks company
        # names and numbers before anything leaves the machine. A big model is exactly
        # the case where that matters most.
        fake2 = Fake()
        base2 = fake2.start()
        try:
            cfg2 = make_cfg(base2 + "/v1")
            result = translate_text(cfg2, cfg2.resolved_models_root(),
                                    "ООО Ромашка поставит 600 000 рублей.",
                                    mode="paragraph")
            sent = fake2.requests[0]["body"]["messages"][-1]["content"]
            check("Ромашка" not in sent, f"公司名没发出去：{sent!r}")
            check("600 000" not in sent, f"金额没发出去：{sent!r}")
            check("[[" in sent, f"发出去的是占位符：{sent!r}")
            check(result.redacted == 1, f"脱敏记了 {result.redacted} 处")
            check(result.numbers_protected == 1, f"数字保护记了 "
                                                 f"{result.numbers_protected} 个")

            # And if the model does drop a placeholder, the software has to say so - a
            # translation with a company missing reads perfectly.
            fake3 = Fake(reply="译文里什么都没有")
            base3 = fake3.start()
            try:
                cfg3 = make_cfg(base3 + "/v1")
                lost = translate_text(cfg3, cfg3.resolved_models_root(),
                                      "ООО Ромашка поставит 600 000 рублей.",
                                      mode="paragraph")
                check(bool(lost.unrestored), f"占位符丢了会报：{lost.unrestored}")
                check(bool(lost.numbers_unrestored),
                      f"数字丢了会报：{lost.numbers_unrestored}")
            finally:
                fake3.stop()
        finally:
            fake2.stop()
        print()

        print("=" * 78)
        print("五、429 会重试，401 不会")
        print("=" * 78)
        retry = Fake(script=[429, 429, 200])
        base4 = retry.start()
        try:
            cfg4 = make_cfg(base4 + "/v1")
            engine4 = OpenAIChatTranslator(cfg4, "ru", "zh", cfg4.translate)
            engine4.load()
            out = engine4.translate("тест")
            check(out == "译文", f"重试后成功：{out!r}")
            check(len(retry.requests) == 3, f"试了 3 次：{len(retry.requests)}")
        finally:
            retry.stop()

        deny = Fake(script=[401, 200])
        base5 = deny.start()
        try:
            cfg5 = make_cfg(base5 + "/v1")
            engine5 = OpenAIChatTranslator(cfg5, "ru", "zh", cfg5.translate)
            engine5.load()
            try:
                engine5.translate("тест")
                check(False, "401 应该抛错")
            except Exception as exc:  # noqa: BLE001
                check("401" in str(exc), f"抛的是 401：{str(exc)[:50]}")
            check(len(deny.requests) == 1,
                  f"密钥错了不该重试（试了 {len(deny.requests)} 次）")
        finally:
            deny.stop()
        print()
    finally:
        fake.stop()

    print("=" * 78)
    print(f"失败 {failures} 项")
    print("=" * 78)
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
