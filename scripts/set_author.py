"""Rewrite the author and committer of every commit in this repository.

The commit author is baked into the commit object and shows up permanently in public
history, so it is worth getting right *before* the first push. Afterwards the same
change means rewriting published history, which is much less pleasant.

Timestamps are preserved - only the identity strings change. Because the author is part
of the commit hash, every commit id changes; nothing has been pushed yet, so there is
no history to disrupt.

    python scripts/set_author.py --name Y_yhy \\
        --email "Y_yhy@users.noreply.github.com"
    python scripts/set_author.py --show      # 只看现在的作者是谁
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

#: The private address each host hands out, so a real mailbox is never published.
NOREPLY = {
    "github": "{user}@users.noreply.github.com",
    "gitee": "{user}@user.noreply.gitee.com",
    "gitcode": "{user}@noreply.gitcode.com",
    "codeberg": "{user}@noreply.codeberg.org",
}


def current_identities(repo) -> list[str]:
    return [f"{c.author.decode(errors='replace')}  ({c.id.decode()[:8]})"
            for c in (entry.commit for entry in repo.get_walker())]


def rewrite(name: str, email: str) -> int:
    from dulwich.objects import Commit
    from dulwich.repo import Repo

    repo = Repo(str(ROOT))
    identity = f"{name} <{email}>".encode("utf-8")

    # Oldest first, so each rewrite already knows its rewritten parent.
    history = [entry.commit for entry in repo.get_walker()]
    history.reverse()

    mapping: dict[bytes, bytes] = {}
    for old in history:
        new = Commit()
        new.tree = old.tree
        new.parents = [mapping.get(p, p) for p in old.parents]
        new.message = old.message
        # Keep the original times: rewriting identity should not rewrite when it
        # happened.
        new.author = identity
        new.author_time = old.author_time
        new.author_timezone = old.author_timezone
        new.committer = identity
        new.commit_time = old.commit_time
        new.commit_timezone = old.commit_timezone
        # Not every dulwich version exposes these; keep them optional and preserve
        # whatever is actually there rather than assuming a shape.
        for attribute in ("encoding", "extra", "gpgsig"):
            if hasattr(old, attribute):
                try:
                    setattr(new, attribute, getattr(old, attribute))
                except Exception:  # noqa: BLE001
                    pass
        repo.object_store.add_object(new)
        mapping[old.id] = new.id

    head_ref = b"refs/heads/main"
    old_head = repo.refs[head_ref]
    repo.refs[head_ref] = mapping[old_head]

    print(f"作者已改为：{name} <{email}>")
    print(f"重写了 {len(mapping)} 个提交\n")
    for old_id, new_id in mapping.items():
        print(f"  {old_id.decode()[:8]} -> {new_id.decode()[:8]}")
    print(f"\n分支 main 现在指向 {mapping[old_head].decode()[:8]}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="重写仓库里所有提交的作者")
    parser.add_argument("--name", help="作者名字")
    parser.add_argument("--email", help="作者邮箱")
    parser.add_argument("--host", choices=sorted(NOREPLY),
                        help="用某家托管的专用邮箱：配合 --user 自动生成")
    parser.add_argument("--user", help="托管网站上的用户名（配合 --host）")
    parser.add_argument("--show", action="store_true", help="只显示当前作者")
    args = parser.parse_args()

    from dulwich.repo import Repo

    if not (ROOT / ".git").is_dir():
        print("还没有仓库，先运行 python scripts/init_repo.py", file=sys.stderr)
        return 1

    repo = Repo(str(ROOT))

    if args.show or not (args.name and (args.email or args.host)):
        print("当前提交的作者：")
        for line in current_identities(repo):
            print(f"  {line}")
        if not args.show:
            print("\n要修改：")
            print("  python scripts/set_author.py --name Y_yhy "
                  "--email \"Y_yhy@users.noreply.github.com\"")
            print("  或者让脚本生成专用邮箱：")
            print("  python scripts/set_author.py --name Y_yhy --host github --user Y_yhy")
        return 0

    email = args.email
    if not email and args.host:
        if not args.user:
            print("--host 需要配合 --user（你在该网站上的用户名）", file=sys.stderr)
            return 1
        email = NOREPLY[args.host].format(user=args.user)

    return rewrite(args.name, email)


if __name__ == "__main__":
    sys.exit(main())
