"""Create (or re-create) the git repository for this project.

Uses dulwich, a pure-Python git implementation, so the project can be versioned on a
machine where git is not installed - which is the situation on the machine this was
written on. The ignore rules from .gitignore are applied via dulwich's own matcher so
the staged set matches what git itself would stage.

    python scripts/init_repo.py                 # create the repo and first commit
    python scripts/init_repo.py --message "..."  # custom commit message
    python scripts/init_repo.py --dry-run        # only list what would be added
"""

from __future__ import annotations

import argparse
import os
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

#: Author recorded on new commits. Read from the environment first so a fork does not
#: commit as the original author, and fall back to the value this repository shipped
#: with. The email is deliberately the host's no-reply address rather than a real
#: mailbox, so publishing does not leak an address that can be harvested.
def _default_author() -> str:
    for name in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        who = os.environ.get(name, "").strip()
        if who:
            mail = (os.environ.get("GIT_AUTHOR_EMAIL")
                    or os.environ.get("GIT_COMMITTER_EMAIL") or "").strip()
            return f"{who} <{mail}>" if mail else who
    return "Y_yhy <Y_yhy@users.noreply.github.com>"


DEFAULT_AUTHOR = _default_author()
#: Derived rather than repeated, so the two can never drift apart - an earlier version
#: hardcoded the email in two places and one of them kept the old value.
DEFAULT_EMAIL = (DEFAULT_AUTHOR[DEFAULT_AUTHOR.index("<") + 1:-1]
                 if "<" in DEFAULT_AUTHOR else "")

FIRST_MESSAGE = """初始提交：守译

框选屏幕区域 -> 离线 OCR -> 翻译 -> 悬浮字幕，翻译层模型无关。

- 引擎插件化：内置离线模型与在线服务都只是引擎之一，用户可在
  engines_custom/ 放自己的实现，或指向任意 OpenAI 兼容接口
- 术语表：强制指定人名/专业词译法，支持正则、分语言对、TSV 互转
- 悬浮窗对截屏隐身，避免把自己的译文再翻译一遍
- 流水线去抖：逐字浮现等成型、识别抖动不重复翻译

回归测试见 tests/，环境自检见 scripts/doctor.py
"""


#: Only these paths belong to this project. The workspace also holds other things
#: (another project's folder, agent artifacts), and a blacklist would eventually miss
#: one of them - an allowlist cannot.
INCLUDE_FILES = [
    ".gitignore", ".gitattributes", "LICENSE", "README.md", "requirements.txt",
    "main.py", "install.bat", "启动.bat", "启动-调试.bat", "交接说明.md",
    # The user-facing manual. It ships inside the portable folder and used to live only
    # there, which put it outside version control and let it drift from the product.
    "使用说明.txt",
    # These three shipped inside the portable folder while existing nowhere else, so the
    # one build that actually reached a user could not be reproduced from the repository.
    "stage_runtime.py", "make_shortcut.py", "安装桌面快捷方式.bat",
]
INCLUDE_DIRS = ["app", "scripts", "assets", "engines_custom", "tests"]

#: Never stage these even if they sit under an included directory.
EXCLUDE_SUFFIX = (".pyc", ".pyo", ".log", ".err")
EXCLUDE_NAMES = {"__pycache__", ".pytest_cache"}
#: Not ours: a different workflow happens to keep its files in tests/.
EXCLUDE_PREFIX = ("tests/douyin-", "tests/hindsight-")


def iter_files() -> list[str]:
    """Every file that belongs to this project, relative and posix-style."""
    out: list[str] = []
    for name in INCLUDE_FILES:
        if (ROOT / name).is_file():
            out.append(name)
    for directory in INCLUDE_DIRS:
        base = ROOT / directory
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames
                           if d not in EXCLUDE_NAMES and d != ".git"]
            for filename in filenames:
                full = Path(dirpath) / filename
                rel = full.relative_to(ROOT).as_posix()
                if rel.endswith(EXCLUDE_SUFFIX):
                    continue
                if rel.startswith(EXCLUDE_PREFIX):
                    continue
                out.append(rel)
    return sorted(set(out))


def make_filter(repo=None):
    """Apply .gitignore on top of the allowlist, the way git itself would."""
    try:
        from dulwich.ignore import IgnoreFilterManager

        if repo is None:
            from dulwich.repo import Repo

            repo = Repo(str(ROOT))
        manager = IgnoreFilterManager.from_repo(repo)

        def ignored(path: str) -> bool:
            return bool(manager.is_ignored(path))

        return ignored
    except Exception:  # noqa: BLE001
        # The allowlist above already keeps the big things out; this is belt and
        # braces for anything .gitignore additionally names.
        patterns = []
        ignore_file = ROOT / ".gitignore"
        if ignore_file.is_file():
            for line in ignore_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and not line.startswith("!"):
                    patterns.append(line.strip("/"))

        def ignored(path: str) -> bool:
            base = path.rsplit("/", 1)[-1]
            for pattern in patterns:
                if pattern.startswith("*."):
                    if base.endswith(pattern[1:]):
                        return True
                    continue
                if pattern in path.split("/"):
                    return True
                if path.startswith(pattern + "/"):
                    return True
            return False

        return ignored


def identity_for(args) -> bytes:
    author = args.author or os.environ.get("GIT_AUTHOR_NAME")
    if not author:
        return DEFAULT_AUTHOR.encode("utf-8")
    if "<" in author:
        return author.encode("utf-8")
    email = os.environ.get("GIT_AUTHOR_EMAIL") or DEFAULT_EMAIL
    return f"{author} <{email}>".encode("utf-8")


def commit_changes(args) -> int:
    """Stage the allowlisted files into the existing repo and commit them."""
    from dulwich import porcelain
    from dulwich.repo import Repo

    repo = Repo(str(ROOT))
    ignored = make_filter(repo)
    keep = [p for p in iter_files() if not ignored(p)]

    try:
        status = porcelain.status(repo)
    except Exception:  # noqa: BLE001
        status = None

    if status is not None:
        # dulwich reports these as bytes AND with backslashes on Windows, so comparing
        # them against the posix allowlist would silently always come out empty and
        # skip the commit.
        def as_text(value) -> str:
            text = value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)
            return text.replace("\\", "/").rstrip("/")

        changed = {as_text(p) for p in status.unstaged}
        changed |= {as_text(p) for p in status.untracked}
        for group in status.staged.values():
            changed |= {as_text(p) for p in group}
        relevant = sorted(p for p in changed if p in set(keep))
        if not relevant:
            print("没有需要提交的改动（按白名单判断）。")
            return 0
        print(f"改动 {len(relevant)} 个文件：")
        for path in relevant[:40]:
            print(f"  M {path}")
        if len(relevant) > 40:
            print(f"  … 还有 {len(relevant) - 40} 个")

    porcelain.add(repo, paths=keep)
    identity = identity_for(args)
    commit_id = porcelain.commit(repo, message=args.message.encode("utf-8"),
                                 author=identity, committer=identity)
    print(f"\n已提交 {commit_id.decode()[:12]}（作者 {identity.decode()}）")
    print("推送到远端：python scripts/publish.py --check 然后去掉 --check")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="创建/重建本项目的 git 仓库")
    parser.add_argument("--message", default=FIRST_MESSAGE, help="提交信息")
    parser.add_argument("--dry-run", action="store_true", help="只列出会加入的文件")
    parser.add_argument("--author", default=None, help="覆盖作者，如 '名字 <邮箱>'")
    parser.add_argument("--force", action="store_true", help="已存在 .git 时也重建")
    args = parser.parse_args()

    from dulwich import porcelain
    from dulwich.repo import Repo

    if (ROOT / ".git").exists() and not args.force and not args.dry_run:
        # An existing repo means "commit what changed", not "start over" - the whole
        # point is that this tool can stand in for git on a machine without it.
        return commit_changes(args)

    if args.dry_run:
        ignored = make_filter()
        keep = [p for p in iter_files() if not ignored(p)]
        skip = [p for p in iter_files() if ignored(p)]
        print(f"会加入 {len(keep)} 个文件：")
        for path in keep:
            print(f"  + {path}")
        print(f"\n被 .gitignore 额外排除 {len(skip)} 个，例如：")
        for path in skip[:12]:
            print(f"  - {path}")
        return 0

    # porcelain.init has no default_branch parameter in dulwich 1.2, and Repo.init's
    # mkdir would try to create the project directory itself. So: init, then point
    # HEAD at main explicitly - that API is stable across versions.
    repo = porcelain.init(str(ROOT))
    try:
        repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/main")
    except Exception as exc:  # noqa: BLE001
        print(f"（无法把默认分支设为 main：{exc}）", file=sys.stderr)
    ignored = make_filter(repo)
    keep = [p for p in iter_files() if not ignored(p)]

    if not keep:
        print("没有任何文件要加入 —— 检查 .gitignore 是不是写太宽了", file=sys.stderr)
        return 1

    porcelain.add(repo, paths=keep)

    identity = identity_for(args)

    commit_id = porcelain.commit(repo, message=args.message.encode("utf-8"),
                                 author=identity, committer=identity)
    head = repo.head().decode()
    print(f"仓库已创建：{ROOT / '.git'}")
    print(f"分支：main   提交：{head[:12]}")
    print(f"文件：{len(keep)} 个")
    total = sum((ROOT / p).stat().st_size for p in keep if (ROOT / p).is_file())
    print(f"入库体积：{total / 1024 / 1024:.2f} MB")
    print(f"作者：{(author or DEFAULT_AUTHOR)}")
    print()
    print("大文件是否被正确排除：")
    for pattern in (".venv", "models/", "__pycache__"):
        hit = [p for p in keep if pattern in p]
        print(f"  {pattern:16s} {'❌ 仍在' if hit else '✅ 已排除'}"
              + (f"（{len(hit)} 个）" if hit else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
