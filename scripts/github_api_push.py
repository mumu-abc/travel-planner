"""用 GitHub REST API 把本地 HEAD 推到远端。

为什么需要这个脚本：本机网络下 `git push`（走 github.com:443）会因代理
CONNECT 502 或直连超时而失败，但 `api.github.com` 是通的。
所以走 API 手工构造 commit：

    blobs（每个文件） → tree → commit → PATCH refs/heads/main

前提：环境变量 GITHUB_TOKEN 有该仓库 Contents 读写权限。
没有 token 时脚本会明确报错退出，不会静默失败。
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

OWNER = "mumu-abc"
REPO = "travel-planner"
BRANCH = "main"
API = "https://api.github.com"


def token() -> str:
    """取 token：优先环境变量，其次 git 凭据管理器里缓存的凭据。

    本机 git 凭据管理器（manager）已经存过 GitHub 登录，所以即使
    github.com:443 连不上、git push 用不了，我们仍能从凭据里拿到
    同一个 token，改走 api.github.com（那条路是通的）。
    """
    t = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if t:
        return t

    try:
        out = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=github.com\n\n",
            capture_output=True, text=True, timeout=25,
        ).stdout
    except Exception:
        out = ""
    for line in out.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1].strip()

    print("找不到可用凭据：GITHUB_TOKEN 未设置，git 凭据管理器里也没有缓存。")
    print("请先执行一次 git push（浏览器登录一次），或显式设置 GITHUB_TOKEN。")
    sys.exit(2)


def call(method: str, path: str, tk: str, payload: dict | None = None) -> dict:
    url = f"{API}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {tk}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "travel-planner-push")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            body = r.read().decode()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        print(f"  ✗ {method} {path} -> {e.code}")
        print("   ", e.read().decode()[:500])
        sys.exit(1)


def git(*args: str) -> str:
    """跑 git 并返回 stdout。

    注意：默认 `core.quotepath=true` 会把非 ASCII 路径转义成 `\\351\\235\\242...`，
    导致中文文件名被当成「删除」（git show 取不到内容 → 以为是删文件）。
    这里统一关掉，让路径以真实 UTF-8 返回。
    """
    return subprocess.run(
        ["git", "-c", "core.quotepath=false", *args],
        capture_output=True, text=True, check=True, encoding="utf-8",
    ).stdout.strip()


def main() -> int:
    tk = token()

    local_head = git("rev-parse", "HEAD")
    local_msg = git("log", "-1", "--format=%s%n%n%b")
    parent = git("rev-parse", "HEAD~1")
    base = parent   # diff 基准，稍后可能被改成 remote_head

    remote = call("GET", f"/repos/{OWNER}/{REPO}", tk)
    print(f"远端默认分支 = {remote['default_branch']}")

    remote_head = call("GET", f"/repos/{OWNER}/{REPO}/git/ref/heads/{BRANCH}", tk)["object"]["sha"]
    print(f"远端 HEAD    = {remote_head[:7]}")
    print(f"本地 HEAD    = {local_head[:7]}")

    if remote_head == local_head:
        print("远端已是最新，无需推送。")
        return 0

    # 关键：不能用「父提交 SHA 相等」来判断是否分叉。
    # 走 API 建出来的提交，GitHub 会按自己的时区/签名重算 SHA，
    # 于是「内容完全相同的一笔提交」在本地和远端会拿到两个不同的 SHA。
    # 这种情况下父 SHA 对不上，但其实并没有分叉。
    #
    # 正确判据：沿着本地提交链往回找，看有没有哪一笔的 **tree** 等于远端 HEAD 的 tree。
    # 找到 → 说明远端的内容就在我的历史里，本地是在它之上继续做的，属于正常推进。
    # 找不到 → 才可能是真分叉（本地丢了远端的改动）。
    remote_info = call("GET", f"/repos/{OWNER}/{REPO}/git/commits/{remote_head}", tk)
    remote_tree = remote_info["tree"]["sha"]
    print(f"远端 HEAD tree      = {remote_tree[:12]}")

    # 从 HEAD 往回最多看 20 笔
    chain = git("rev-list", "--max-count=20", "HEAD").splitlines()
    match_idx = None
    for idx, sha in enumerate(chain):
        if git("rev-parse", f"{sha}^{{tree}}") == remote_tree:
            match_idx = idx
            break

    if match_idx is not None:
        # 远端内容就在本地历史第 match_idx 笔上
        if match_idx == 0:
            print("  (本地 HEAD 与远端内容一致，无需构造新提交)")
        else:
            print(f"  ✓ 远端内容 = 本地历史第 {match_idx} 笔（往回 {match_idx} 个提交待推）")
        base_for_diff = chain[match_idx] if match_idx < len(chain) else parent
        base = remote_head
    else:
        print(f"⚠️ 本地最近 20 笔提交里，没有一笔的 tree 等于远端 HEAD tree。")
        print("   本地与远端确实已分叉（可能本地丢了远端改动），脚本不强制覆盖，请人工确认。")
        return 1

    # 这次要推的内容 = 本地 HEAD 相对「远端内容那一笔」的差异
    files = [f for f in git("diff", "--name-only", base_for_diff, local_head).splitlines() if f]
    print(f"本次改动 {len(files)} 个文件  (基准 {base_for_diff[:7]})")

    # 提交信息：
    # 走 API 推送时，远端历史上那些用 API 建的提交 SHA 会被重算，本地这一串
    # 提交里有一部分「内容其实已经在远端了」。所以不能把 chain[match_idx+1:] 全列出来
    # —— 那会把早就推过的东西也算成新的。这里只取【从远端内容那一笔到现在】之间
    # 真正产生文件差异的提交标题。
    pending = chain[match_idx + 1:] if match_idx is not None and match_idx > 0 else []
    titles = []
    prev = base_for_diff
    for sha in reversed(pending):          # 从旧到新
        changed = git("diff", "--name-only", prev, sha).splitlines()
        if changed:
            titles.append(git("log", "-1", "--format=%s", sha))
        prev = sha
    if len(titles) > 1:
        local_msg = "\n".join(titles)
        print(f"（本次将 {len(titles)} 笔有实际改动的提交合并为一次推送）")
    elif len(titles) == 1:
        local_msg = titles[0]

    tree_entries = []
    for path in files:
        blob = subprocess.run(
            ["git", "show", f"{local_head}:{path}"],
            capture_output=True, check=False,
        )
        if blob.returncode != 0:
            # 该文件在这次提交里被删除了
            tree_entries.append({
                "path": path, "mode": "100644", "type": "blob", "sha": None,
            })
            print(f"  - {path} (删除)")
            continue
        b64 = base64.b64encode(blob.stdout).decode()
        r = call("POST", f"/repos/{OWNER}/{REPO}/git/blobs", tk,
                 {"content": b64, "encoding": "base64"})
        mode = git("ls-tree", local_head, path).split()[0]
        tree_entries.append({
            "path": path, "mode": mode, "type": "blob", "sha": r["sha"],
        })
        print(f"  + {path}")

    # 基于父 tree 打补丁，保留未改动文件
    new_tree = call("POST", f"/repos/{OWNER}/{REPO}/git/trees", tk, {
        "base_tree": remote_tree,
        "tree": [e for e in tree_entries if e["sha"]],
    })
    # 删除的文件要单独用 sha=null 表达
    deleted = [e for e in tree_entries if not e["sha"]]
    if deleted:
        new_tree = call("POST", f"/repos/{OWNER}/{REPO}/git/trees", tk, {
            "base_tree": new_tree["sha"],
            "tree": [{"path": e["path"], "mode": e["mode"], "type": "blob", "sha": None}
                     for e in deleted],
        })

    commit = call("POST", f"/repos/{OWNER}/{REPO}/git/commits", tk, {
        "message": local_msg,
        "tree": new_tree["sha"],
        "parents": [remote_head],   # 挂在远端 HEAD 上，保证历史收敛（否则每次都要重算 SHA）
    })
    print(f"新 commit = {commit['sha'][:7]}")

    call("PATCH", f"/repos/{OWNER}/{REPO}/git/refs/heads/{BRANCH}", tk, {
        "sha": commit["sha"], "force": False,
    })
    print("✓ 已更新远端分支")
    return 0


if __name__ == "__main__":
    sys.exit(main())
