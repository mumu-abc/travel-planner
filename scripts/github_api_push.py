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
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def main() -> int:
    tk = token()

    local_head = git("rev-parse", "HEAD")
    local_msg = git("log", "-1", "--format=%s%n%n%b")
    parent = git("rev-parse", "HEAD~1")

    remote = call("GET", f"/repos/{OWNER}/{REPO}", tk)
    print(f"远端默认分支 = {remote['default_branch']}")

    remote_head = call("GET", f"/repos/{OWNER}/{REPO}/git/ref/heads/{BRANCH}", tk)["object"]["sha"]
    print(f"远端 HEAD    = {remote_head[:7]}")
    print(f"本地 HEAD    = {local_head[:7]}")

    if remote_head == local_head:
        print("远端已是最新，无需推送。")
        return 0

    # 本地必须是远端 HEAD 的直接后继，否则说明分叉了，交给人工处理
    if parent != remote_head:
        print(f"⚠️ 本地 HEAD 的父提交 {parent[:7]} != 远端 HEAD {remote_head[:7]}")
        print("   本地与远端可能已分叉，脚本不做强制覆盖，请人工确认。")
        return 1

    # 算出这次提交涉及哪些文件（相对上一个提交）
    files = [f for f in git("diff", "--name-only", f"{parent}", f"{local_head}").splitlines() if f]
    print(f"本次改动 {len(files)} 个文件")

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
    parent_tree = call("GET", f"/repos/{OWNER}/{REPO}/git/commits/{parent}", tk)["tree"]["sha"]
    new_tree = call("POST", f"/repos/{OWNER}/{REPO}/git/trees", tk, {
        "base_tree": parent_tree,
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
        "parents": [parent],
    })
    print(f"新 commit = {commit['sha'][:7]}")

    call("PATCH", f"/repos/{OWNER}/{REPO}/git/refs/heads/{BRANCH}", tk, {
        "sha": commit["sha"], "force": False,
    })
    print("✓ 已更新远端分支")
    return 0


if __name__ == "__main__":
    sys.exit(main())
