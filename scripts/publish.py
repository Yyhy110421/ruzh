"""Publish the repository to a git host, without needing git installed.

Pushing needs three things this script cannot invent for you: a repository URL, an
account on that host, and a personal access token. Creating the account is something
only you can do (it wants an email or a phone number), so the script's job is to make
everything after that a single command.

    set GIT_REMOTE=https://gitee.com/<你>/<仓库>.git
    set GIT_TOKEN=<你的私人令牌>
    python scripts/publish.py --check      # 只测连通性，不推送
    python scripts/publish.py              # 推送 main

Or pass them explicitly:

    python scripts/publish.py --remote https://... --token xxx

**Host choice matters here.** On the machine this was written on, github.com accepts
a TCP connection and then stalls mid-handshake, so a push would hang; gitee.com and
gitcode.com both answer normally. Unless you have a proxy, use Gitee.

Never paste a token into a file that gets committed - read it from the environment or
pass it on the command line.
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

BRANCH = "main"

HOSTS = {
    "gitee": "https://gitee.com/<用户名>/<仓库名>.git",
    "github": "https://github.com/<用户名>/<仓库名>.git",
    "gitcode": "https://gitcode.com/<用户名>/<仓库名>.git",
    "codeberg": "https://codeberg.org/<用户名>/<仓库名>.git",
}


def with_credentials(url: str, token: str, user: str = "") -> str:
    """Put the token in the URL the way git does for HTTPS remotes."""
    if not token:
        return url
    parts = urllib.parse.urlsplit(url)
    if "@" in parts.netloc:
        return url
    who = urllib.parse.quote(user or "oauth2", safe="")
    secret = urllib.parse.quote(token, safe="")
    netloc = f"{who}:{secret}@{parts.netloc}"
    return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def redact(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    if "@" not in parts.netloc:
        return url
    host = parts.netloc.split("@", 1)[1]
    return urllib.parse.urlunsplit((parts.scheme, f"***@{host}", parts.path, parts.query,
                                    parts.fragment))


def check_reachability(url: str, timeout: float = 15.0) -> tuple[bool, str]:
    """Probe the endpoint a push actually uses, not the host's home page.

    Testing ``https://github.com`` is misleading in both directions: a GET can stall
    while a HEAD succeeds, and the home page says nothing about the git service. The
    smart-HTTP endpoint is the thing that matters, and it answers honestly -
    ``401 Unauthorized`` for an anonymous request means "reachable, give me
    credentials", which is exactly the state we want before pushing.
    """
    parts = urllib.parse.urlsplit(url)
    path = parts.path if parts.path.endswith(".git") else parts.path + ".git"
    probe = f"{parts.scheme}://{parts.netloc}{path}/info/refs?service=git-receive-pack"
    request = urllib.request.Request(
        probe, headers={"User-Agent": "git/2.0 (ruzh publish helper)"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            resp.read(64)
            return True, f"HTTP {resp.status} —— git 端点已响应"
    except urllib.error.HTTPError as exc:
        # Any HTTP status at all proves the git service is reachable and speaking.
        if exc.code in (401, 403):
            return True, f"HTTP {exc.code}（需要认证）—— git 端点正常"
        if exc.code == 404:
            return True, "HTTP 404 —— 端点通，但仓库还不存在（先在网页上建好空仓库）"
        return True, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def main() -> int:
    parser = argparse.ArgumentParser(description="把仓库推送到 Gitee / GitHub 等")
    parser.add_argument("--remote", default=os.environ.get("GIT_REMOTE", ""),
                        help="仓库地址，例如 https://gitee.com/you/repo.git")
    parser.add_argument("--token", default=os.environ.get("GIT_TOKEN", ""),
                        help="私人访问令牌（也可用环境变量 GIT_TOKEN）")
    parser.add_argument("--user", default=os.environ.get("GIT_USER", ""),
                        help="令牌对应的用户名（可选，默认 oauth2）")
    parser.add_argument("--branch", default=BRANCH, help=f"要推送的分支（默认 {BRANCH}）")
    parser.add_argument("--check", action="store_true", help="只测试连通性，不推送")
    parser.add_argument("--skip-scan", action="store_true",
                        help="跳过推送前的敏感内容检查（不推荐）")
    parser.add_argument("--quiet", action="store_true", help="少输出")
    parser.add_argument("--show-hosts", action="store_true", help="列出常见托管的地址格式")
    args = parser.parse_args()

    if args.show_hosts:
        print("常见托管的仓库地址格式：")
        for name, template in HOSTS.items():
            print(f"  {name:10s} {template}")
        print("\n实测（本机，2026-10）：")
        print("  gitee.com / gitcode.com / codeberg.org  —— 正常")
        print("  github.com —— git 端点返回 401（说明通、要认证），但 curl 的 GET")
        print("              会超时，大流量传输可能被限速掐断。可以试，失败就换 Gitee。")
        return 0

    if not (ROOT / ".git").is_dir():
        print(f"还没有仓库，请先运行：python scripts/init_repo.py", file=sys.stderr)
        return 1

    if not args.remote:
        print("没有指定仓库地址。可以：", file=sys.stderr)
        print("  set GIT_REMOTE=https://gitee.com/<你>/<仓库>.git", file=sys.stderr)
        print("  set GIT_TOKEN=<私人令牌>", file=sys.stderr)
        print("  python scripts/publish.py", file=sys.stderr)
        print("\n或者 python scripts/publish.py --show-hosts 看各家的地址格式。",
              file=sys.stderr)
        return 1

    host_ok, detail = check_reachability(args.remote)
    print(f"主机连通性：{'可以访问' if host_ok else '访问不通'}（{detail}）")
    if not host_ok:
        print("\n主机都连不上，推送一定会失败或长时间卡住 —— 先解决网络（或用 Gitee）。",
              file=sys.stderr)
        return 1

    if args.check:
        print("地址与网络都没问题。加上 --token 之后去掉 --check 即可推送。")
        return 0

    # Last line of defence: once it is pushed, it is public and permanent.
    import subprocess

    scan = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_secrets.py")],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if scan.returncode != 0:
        print("\n推送前检查没通过，先处理上面的内容：", file=sys.stderr)
        print(scan.stdout or scan.stderr, file=sys.stderr)
        if not args.skip_scan:
            print("确认这些内容没问题的话，可以加 --skip-scan 跳过。", file=sys.stderr)
            return 1
    elif not args.quiet:
        print("推送前检查：没有密码、令牌或个人信息。\n")

    if not args.token:
        print("\n没有令牌无法推送（推送需要认证）。", file=sys.stderr)
        print("  Gitee：  设置 -> 私人令牌 -> 生成新令牌（勾选 projects 权限）",
              file=sys.stderr)
        print("  GitHub：Settings -> Developer settings -> Personal access tokens",
              file=sys.stderr)
        print("然后用 set GIT_TOKEN=<令牌> 或 --token 传入。", file=sys.stderr)
        return 1

    from dulwich import porcelain

    url = with_credentials(args.remote, args.token, args.user)
    print(f"推送 {args.branch} -> {redact(url)}")

    refspec = f"refs/heads/{args.branch}:refs/heads/{args.branch}".encode()
    try:
        result = porcelain.push(str(ROOT), url, refspecs=[refspec.decode()])
    except Exception as exc:  # noqa: BLE001
        message = str(exc)
        print(f"\n推送失败：{message}", file=sys.stderr)
        lowered = message.lower()
        if "authentication" in lowered or "401" in lowered or "403" in lowered:
            print("看起来是令牌不对或权限不足 —— 令牌需要仓库读写权限。", file=sys.stderr)
        elif "timeout" in lowered or "timed out" in lowered or "disconnect" in lowered \
                or "reset" in lowered:
            print("连接被掐断。这台机器上 github.com 能握手但传不了数据。", file=sys.stderr)
            print("  有代理就开起来再试 —— 本脚本会自动读取 HTTPS_PROXY：", file=sys.stderr)
            print("    set HTTPS_PROXY=http://127.0.0.1:7890", file=sys.stderr)
            print("  没有代理就用 Gitee：https://gitee.com", file=sys.stderr)
        elif "not found" in lowered or "404" in lowered:
            print("仓库不存在？先在网页上把空仓库建好，注意不要勾选自动生成 README。",
                  file=sys.stderr)
        return 1

    print("推送完成。")
    for key, value in (result.ref_status or {}).items():
        print(f"  {key.decode() if isinstance(key, bytes) else key}: "
              f"{value.decode() if isinstance(value, bytes) else value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
