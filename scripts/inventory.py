#!/usr/bin/env python3
"""YYC³ Cube Hub 仓库清点器。

用 GitHub REST API 清点双组织（YYC-Cube / YanYuCloudCube）全部仓库：
stars / 更新时间 / Pages 状态 / 归档状态，落盘为 data/repos.json，
并对照上次清点输出变更摘要，重新生成 README.md 看板。

仅依赖 Python 3.9+ 标准库。认证：环境变量 GITHUB_TOKEN（CI 自动注入）
或 GH_TOKEN（本地 gh CLI 会话）。

用法：
    python3 scripts/inventory.py            # 清点 + 生成（仓库根目录执行）
    python3 scripts/inventory.py --check    # 只比对，不改写文件（exit 1 = 有差异）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ORGS = ["YYC-Cube", "YanYuCloudCube"]
# 账号类型缓存：YYC-Cube 实为个人账号（/users/ 端点），YanYuCloudCube 为组织
_ACCOUNT_TYPE: dict[str, str] = {}
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DATA_FILE = DATA_DIR / "repos.json"
README_FILE = ROOT / "README.md"

UA = "yyc3-cube-hub-inventory/1.0"


def api_get(url: str, token: str) -> tuple[object, dict[str, str]]:
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": UA,
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            links = _parse_link(resp.headers.get("Link", ""))
            return json.loads(resp.read().decode("utf-8")), links
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None, {}
        raise


def _parse_link(header: str) -> dict[str, str]:
    links = {}
    for part in header.split(","):
        section = part.split(";")
        if len(section) == 2 and 'rel="next"' in section[1]:
            links["next"] = section[0].strip().strip("<>")
    return links


def list_org_repos(org: str, token: str) -> list[dict]:
    """List all repos for an org or a personal account (YYC-Cube is a User)."""
    account_type = _ACCOUNT_TYPE.get(org)
    if account_type is None:
        payload, _ = api_get(f"https://api.github.com/users/{org}", token)
        account_type = str(payload.get("type", "")) if isinstance(payload, dict) else ""
        _ACCOUNT_TYPE[org] = account_type
    base = "orgs" if account_type == "Organization" else "users"
    repos: list[dict] = []
    url: str | None = f"https://api.github.com/{base}/{org}/repos?per_page=100&type=owner"
    while url:
        payload, links = api_get(url, token)
        repos.extend(payload or [])
        url = links.get("next")
    return repos


def pages_info(full_name: str, token: str) -> dict:
    """Pages 配置信息；API 返回配置即视为已启用（status 单独记录，可能为 building）。"""
    payload, _ = api_get(f"https://api.github.com/repos/{full_name}/pages", token)
    if not isinstance(payload, dict):
        return {"enabled": False}
    return {
        "enabled": True,
        "status": payload.get("status", ""),
        "url": payload.get("html_url", ""),
        "cname": payload.get("cname", "") or "",
    }


def inventory(token: str) -> list[dict]:
    out = []
    for org in ORGS:
        for repo in sorted(list_org_repos(org, token), key=lambda r: r["name"].lower()):
            entry = {
                "org": org,
                "name": repo["name"],
                "fullName": repo["full_name"],
                "description": (repo.get("description") or "").strip(),
                "url": repo["html_url"],
                "stars": repo.get("stargazers_count", 0),
                "forks": repo.get("forks_count", 0),
                "language": repo.get("language") or "",
                "archived": bool(repo.get("archived")),
                "private": bool(repo.get("private")),
                "createdAt": repo.get("created_at", ""),
                "pushedAt": repo.get("pushed_at", ""),
                "updatedAt": repo.get("updated_at", ""),
                "defaultBranch": repo.get("default_branch", ""),
            }
            entry["pages"] = pages_info(entry["fullName"], token) if repo.get("has_pages") else {
                "enabled": False
            }
            out.append(entry)
    return out


def load_previous() -> tuple[list[dict], str]:
    if not DATA_FILE.exists():
        return [], ""
    doc = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    return doc.get("repos", []), doc.get("generatedAt", "")


def diff_repos(prev: list[dict], curr: list[dict]) -> dict[str, list[str]]:
    prev_map = {r["fullName"]: r for r in prev}
    curr_map = {r["fullName"]: r for r in curr}
    added = sorted(set(curr_map) - set(prev_map))
    removed = sorted(set(prev_map) - set(curr_map))
    archived = sorted(n for n in set(prev_map) & set(curr_map) if curr_map[n]["archived"] and not prev_map[n]["archived"])
    unarchived = sorted(n for n in set(prev_map) & set(curr_map) if prev_map[n]["archived"] and not curr_map[n]["archived"])
    return {"added": added, "removed": removed, "archived": archived, "unarchived": unarchived}


def render_readme(doc: dict, changes: dict[str, list[str]]) -> str:
    repos = doc["repos"]
    lines = [
        "# YYC³ Cube Hub · 仓库矩阵数据底座",
        "",
        "双组织（[YYC-Cube](https://github.com/YYC-Cube) / "
        "[YanYuCloudCube](https://github.com/YanYuCloudCube)）全部仓库的自动清点看板。",
        "",
        f"- 清点时间：{doc['generatedAt']}（UTC）",
        f"- 仓库总数：{doc['total']}（" + " + ".join(f"{o} {c}" for o, c in doc['byOrg'].items()) + "）",
        "- 数据来源：GitHub REST API（`scripts/inventory.py`，每周一 03:23 UTC 自动运行，可手动触发）",
        "- 明细数据：[`data/repos.json`](data/repos.json)（stars / forks / 更新时间 / Pages 状态 / 归档标记）",
        "",
        "## 变更摘要（对照上次清点）",
        "",
    ]
    if any(changes.values()):
        for label, key in [("新增", "added"), ("移除", "removed"), ("已归档", "archived"), ("取消归档", "unarchived")]:
            if changes[key]:
                lines.append(f"- **{label}**（{len(changes[key])}）：" + "、".join(changes[key]))
    else:
        lines.append("- 无变更")
    lines += [
        "",
        "## 仓库总览",
        "",
        "| 仓库 | 组织 | Stars | 语言 | 最近推送 | Pages | 归档 |",
        "|---|---|---:|---|---|---|---|",
    ]
    for r in repos:
        pushed = (r["pushedAt"] or "")[:10]
        pages = "—"
        if r["pages"].get("enabled"):
            cname = r["pages"].get("cname") or "github.io"
            pages = f"[{cname}]({r['pages'].get('url', '')})" if r["pages"].get("url") else cname
            if r["pages"].get("status") and r["pages"]["status"] != "built":
                pages += f"（{r['pages']['status']}）"
        archived = "✅" if r["archived"] else ""
        lines.append(
            f"| [{r['name']}]({r['url']}) | {r['org']} | {r['stars']} | "
            f"{r['language'] or '—'} | {pushed or '—'} | {pages} | {archived} |"
        )
    lines += [
        "",
        "## 复现与手动运行",
        "",
        "```bash",
        "export GITHUB_TOKEN=$(gh auth token)   # 或 GH_TOKEN",
        "python3 scripts/inventory.py",
        "```",
        "",
        "加 `--check` 只做比对不写文件（CI 门禁可用）。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="只比对，不写文件")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print("error: GITHUB_TOKEN / GH_TOKEN is required", file=sys.stderr)
        return 2

    repos = inventory(token)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    by_org: dict[str, int] = {}
    for r in repos:
        by_org[r["org"]] = by_org.get(r["org"], 0) + 1

    prev, prev_at = load_previous()
    changes = diff_repos(prev, repos)
    doc = {
        "generatedAt": generated_at,
        "previousGeneratedAt": prev_at,
        "orgs": ORGS,
        "total": len(repos),
        "byOrg": by_org,
        "repos": repos,
    }

    if args.check:
        current = json.loads(DATA_FILE.read_text(encoding="utf-8")) if DATA_FILE.exists() else {}
        same = current.get("repos") == repos
        print(f"check: {'up-to-date' if same else 'stale'} ({len(repos)} repos)")
        return 0 if same else 1

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    README_FILE.write_text(render_readme(doc, changes), encoding="utf-8")

    print(f"inventory: {len(repos)} repos " + ", ".join(f"{o}={c}" for o, c in by_org.items()))
    for key, label in [("added", "新增"), ("removed", "移除"), ("archived", "归档"), ("unarchived", "取消归档")]:
        if changes[key]:
            print(f"  {label}: " + "、".join(changes[key]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
