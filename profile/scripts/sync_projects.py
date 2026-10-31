#!/usr/bin/env python3
"""
Keeps the "Live Sites" grid in index.html in step with profile/projects.yaml,
and adds new deployed public repos of a GitHub user to projects.yaml.

    python profile/scripts/sync_projects.py           # discover new repos, then render
    python profile/scripts/sync_projects.py --render  # render only, no API calls

A repo counts as deployed when it is public, not a fork, not archived, and has
either a homepage URL or GitHub Pages enabled. Repos already in `projects`,
listed in `ignore`, or created before `created_after` are skipped.

New entries are appended to the end of projects.yaml as text, so the file's
comments and formatting are kept. Their description is the repo description
(or a placeholder), to be edited in the pull request the workflow opens.

Env vars:
  GITHUB_TOKEN   - optional for public data; raises the API rate limit and is
                   used to read the Pages URL (custom domain) of a repo.
  GITHUB_OWNER   - defaults to "aih".
  GITHUB_OUTPUT  - set by Actions; receives added=<comma-separated repo names>.
"""
import argparse
import html
import json
import os
import sys
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = ROOT.parent
PROJECTS_PATH = ROOT / "projects.yaml"
INDEX_PATH = REPO_ROOT / "index.html"

BEGIN = "<!-- BEGIN live-sites: generated from profile/projects.yaml by profile/scripts/sync_projects.py -->"
END = "<!-- END live-sites -->"

API = "https://api.github.com"


def session() -> requests.Session:
    s = requests.Session()
    s.headers["Accept"] = "application/vnd.github+json"
    s.headers["X-GitHub-Api-Version"] = "2022-11-28"
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        s.headers["Authorization"] = f"Bearer {token}"
    return s


def list_public_repos(s: requests.Session, owner: str) -> list:
    repos, page = [], 1
    while True:
        r = s.get(
            f"{API}/users/{owner}/repos",
            params={"type": "owner", "per_page": 100, "page": page, "sort": "created"},
            timeout=30,
        )
        r.raise_for_status()
        batch = r.json()
        repos.extend(batch)
        if len(batch) < 100:
            return repos
        page += 1


def site_url(s: requests.Session, owner: str, repo: dict) -> str | None:
    homepage = (repo.get("homepage") or "").strip()
    if homepage:
        return homepage if "://" in homepage else f"https://{homepage}"
    if not repo.get("has_pages"):
        return None
    r = s.get(f"{API}/repos/{owner}/{repo['name']}/pages", timeout=30)
    if r.ok and r.json().get("html_url"):
        return r.json()["html_url"].rstrip("/")
    return f"https://{owner}.github.io/{repo['name']}"


def title_from_name(name: str) -> str:
    return " ".join(w.capitalize() for w in name.replace("_", "-").split("-") if w)


def yaml_entry(entry: dict) -> str:
    """One projects list item, in the same layout as the hand-written ones."""
    q = lambda v: json.dumps(v, ensure_ascii=False)  # JSON is valid YAML flow style
    desc = entry["description"].replace("\n", " ")
    return (
        f"\n  - repo: {q(entry['repo'])}\n"
        f"    title: {q(entry['title'])}\n"
        f"    url: {q(entry['url'])}\n"
        f"    org: {q(entry['org'])}\n"
        f"    tags: {q(entry['tags'])}\n"
        f"    description: >-\n"
        f"      {desc}\n"
    )


def discover(data: dict, owner: str) -> list:
    known = {p["repo"] for p in data.get("projects") or []}
    ignore = set(data.get("ignore") or [])
    created_after = str(data.get("created_after") or "")
    s = session()
    added = []
    for repo in list_public_repos(s, owner):
        name = repo["name"]
        if (
            repo.get("private")
            or repo.get("fork")
            or repo.get("archived")
            or name in known
            or name in ignore
            or (created_after and repo["created_at"][:10] < created_after)
        ):
            continue
        url = site_url(s, owner, repo)
        if not url:
            continue
        topics = [t.replace("-", " ").title() for t in (repo.get("topics") or [])][:3]
        added.append(
            {
                "repo": name,
                "title": title_from_name(name),
                "url": url,
                "org": url.split("://", 1)[-1].split("/", 1)[0],
                "tags": topics or [repo.get("language") or "Project"],
                "description": repo.get("description")
                or f"TODO: describe {name}.",
            }
        )
    return added


def render_card(p: dict) -> str:
    e = lambda v: html.escape(str(v), quote=True)
    classes = ["ptag hot", "ptag green", "ptag"]
    tags = "".join(
        f'<span class="{classes[min(i, 2)]}">{e(t)}</span>'
        for i, t in enumerate((p.get("tags") or [])[:3])
    )
    desc = " ".join(str(p.get("description", "")).split())
    return (
        '          <div class="proj-card">\n'
        f'            <div class="proj-tags">{tags}</div>\n'
        f'            <div class="proj-org">{e(p.get("org", ""))}</div>\n'
        f'            <div class="proj-title"><a href="{e(p["url"])}" target="_blank" rel="noopener">'
        f'{e(p["title"])} ↗</a></div>\n'
        f'            <p class="proj-desc">{e(desc)}</p>\n'
        "          </div>\n"
    )


def render(data: dict) -> bool:
    text = INDEX_PATH.read_text(encoding="utf-8")
    start, end = text.find(BEGIN), text.find(END)
    if start < 0 or end < start:
        sys.exit(f"Markers not found in {INDEX_PATH}")
    cards = "\n".join(render_card(p) for p in data.get("projects") or [])
    block = f"{BEGIN}\n{cards}          {END}"
    new = text[:start] + block + text[end + len(END):]
    if new != text:
        INDEX_PATH.write_text(new, encoding="utf-8")
        return True
    return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--render", action="store_true", help="only regenerate index.html")
    args = ap.parse_args()

    data = yaml.safe_load(PROJECTS_PATH.read_text(encoding="utf-8")) or {}
    added = []
    if not args.render:
        added = discover(data, os.environ.get("GITHUB_OWNER", "aih"))
        if added:
            with PROJECTS_PATH.open("a", encoding="utf-8") as f:
                for entry in added:
                    f.write(yaml_entry(entry))
            data = yaml.safe_load(PROJECTS_PATH.read_text(encoding="utf-8"))

    changed = render(data)
    names = ",".join(a["repo"] for a in added)
    print(f"added: {names or 'none'}; index.html {'updated' if changed else 'unchanged'}")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"added={names}\n")


if __name__ == "__main__":
    main()
