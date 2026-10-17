"""Pre-publish check: is anything private about to leave this machine?

Scans the files the repository actually tracks (not the whole disk) for three kinds of
trouble:

1. **Credentials** - passwords, tokens, API keys, cookies, private keys. The reading
   surface is small; this is a cheap way to keep it that way.
2. **Identity** - a real name, an absolute path containing the Windows account name,
   an email address. Harmless locally, unnecessary in public.
3. **Files that never belonged here** - a browser cookie jar for some unrelated tool
   is the classic example. It is one bad `git add -A` away from being published.

    python scripts/check_secrets.py            # 检查，有问题返回 1
    python scripts/check_secrets.py --quiet    # 只在有问题时输出

`scripts/publish.py` runs this before pushing and refuses to continue on a hit.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

#: (label, pattern). Kept narrow so ordinary code does not trip them - a scanner that
#: cries wolf gets ignored, which is worse than not having one.
SECRET_PATTERNS = [
    ("密码字段", re.compile(r"(password|passwd|pwd)\s*[:=]\s*['\"][^'\"]+['\"]", re.I)),
    ("令牌/密钥字段",
     re.compile(r"(token|secret|api[_-]?key|access[_-]?key)\s*[:=]\s*['\"][^'\"]{6,}['\"]",
                re.I)),
    ("百度凭据", re.compile(r"baidu_(appid|key)\s*[:=]\s*['\"][^'\"]+['\"]", re.I)),
    ("Bearer 令牌", re.compile(r"Bearer\s+[A-Za-z0-9._\-]{12,}", re.I)),
    ("私钥", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("常见令牌前缀", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{16,}|sk-[A-Za-z0-9]{16,})")),
    ("浏览器 Cookie 文件", re.compile(r"cookies?\.(txt|json)$|douyin-.*\.cjs$", re.I)),
]

IDENTITY_PATTERNS = [
    ("Windows 账户名", re.compile(r"[A-Za-z]:\\+Users\\+([^\\\s\"']+)")),
    # The whole dotted domain has to be captured, otherwise a check like "does this
    # end in users.noreply.github.com" only ever sees the first two labels.
    ("邮箱地址", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
]

#: Addresses that are obviously not real identity, so the scanner stays quiet.
EMAIL_ALLOW = re.compile(
    r"@(localhost"
    r"|example\.(?:com|org|net)"
    r"|users\.noreply\.github\.com"
    r"|user\.noreply\.gitee\.com"
    r"|noreply\.(?:gitcode\.com|codeberg\.org)"
    r")$",
    re.I,
)
#: Hosts that appear in links and prose, never as somebody's mailbox.
EMAIL_ALLOW_HOSTS = ("fanyi-api.baidu.com", "pypi.org", "python.org", "dulwich.io",
                     "gitee.com", "github.com", "gitcode.com", "codeberg.org")


def _allowlisted_working_files() -> list[str]:
    """The files a commit would pick up now, including brand new ones."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_init_repo", ROOT / "scripts" / "init_repo.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ignored = module.make_filter()
    return [p for p in module.iter_files() if not ignored(p)]


def tracked_files() -> list[str]:
    """What is already committed, plus what is about to be.

    Scanning only the committed tree would miss the case that matters most: a file
    written five minutes ago with a token in it, staged for its first push.
    """
    committed: list[str] = []
    try:
        from dulwich.repo import Repo

        repo = Repo(str(ROOT))
        head = repo[repo.head()]
        for entry in repo.object_store.iter_tree_contents(head.tree):
            path = entry.path.decode("utf-8", "replace") \
                if isinstance(entry.path, bytes) else entry.path
            committed.append(path)
    except Exception:  # noqa: BLE001
        pass

    try:
        pending = _allowlisted_working_files()
    except Exception:  # noqa: BLE001
        pending = []

    return sorted(set(committed) | set(pending))


def scan(quiet: bool) -> int:
    files = tracked_files()
    if not quiet:
        print(f"检查仓库里的 {len(files)} 个文件…\n")

    findings: list[str] = []
    for path in files:
        full = ROOT / path
        if not full.is_file():
            continue
        try:
            data = full.read_bytes()
        except OSError:
            continue
        if b"\x00" in data[:4096]:
            continue  # binary (icons, images)

        text = data.decode("utf-8", "replace")
        for label, pattern in SECRET_PATTERNS + IDENTITY_PATTERNS:
            for match in pattern.finditer(text):
                hit = match.group(0).replace("\n", " ")[:100]
                if label == "邮箱地址":
                    address = match.group(0)
                    if EMAIL_ALLOW.search(address):
                        continue
                    domain = address.rsplit("@", 1)[-1].lower()
                    if any(domain == host or domain.endswith("." + host)
                           for host in EMAIL_ALLOW_HOSTS):
                        continue
                    # A domain inside a URL is a link, not somebody's address.
                    if "://" in text[max(0, match.start() - 8):match.start()]:
                        continue
                line = text[:match.start()].count("\n") + 1
                findings.append(f"  [{label}] {path}:{line}\n      {hit}")

    if findings:
        print(f"发现 {len(findings)} 处需要你确认的内容：\n")
        for item in findings:
            print(item)
        print("\n如果这些是必要的（比如许可证里的署名），可以忽略；"
              "如果是密码或令牌，先改掉再推送。")
        return 1

    if not quiet:
        print("没有发现密码、令牌、Cookie 或个人信息。可以安全推送。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="推送前检查仓库里有没有敏感内容")
    parser.add_argument("--quiet", action="store_true", help="只在有问题时输出")
    args = parser.parse_args()
    return scan(args.quiet)


if __name__ == "__main__":
    sys.exit(main())
