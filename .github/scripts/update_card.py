#!/usr/bin/env python3
"""
Automated live stats updater for ASCII GitHub Profile Cards (gh-ascii style).
Preserves the ASCII art, styling, dimensions, and custom contact sections,
while dynamically refreshing live stats (uptime, languages, repos, stars, forks,
followers, commits, PRs, issues, top repo, contributions, reviews, and sparkline).
"""

import calendar
import json
import math
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

INFO_COLS = 58
HALF = (INFO_COLS - 3) // 2  # 27
SPARK = " ▁▂▃▄▅▆▇█"

PALETTES = {
    "dark": {
        "key": "#ffa657",
        "dots": "#484f58",
        "value": "#c9d1d9",
        "number": "#79c0ff",
        "rule": "#3d444d",
        "spark": "#39d353",
    },
    "light": {
        "key": "#953800",
        "dots": "#8c959f",
        "value": "#24292f",
        "number": "#0550ae",
        "rule": "#d0d7de",
        "spark": "#2da44e",
    },
}


def get_token() -> str:
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        return token
    try:
        res = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=True)
        return res.stdout.strip()
    except Exception:
        return ""


def request_json(url: str, token: str, data: dict = None) -> dict:
    headers = {
        "User-Agent": "gh-ascii-updater",
        "Accept": "application/vnd.github+json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode("utf-8") if data else None
    if body:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.loads(res.read().decode("utf-8"))


def fetch_stats(username: str, token: str) -> dict:
    q_main = """
    query($login: String!) {
      user(login: $login) {
        createdAt
        location
        followers { totalCount }
        repositoriesContributedTo(contributionTypes: [COMMIT, PULL_REQUEST, REPOSITORY]) {
          totalCount
        }
        pullRequests { totalCount }
        issues { totalCount }
        contributionsCollection {
          totalPullRequestReviewContributions
          contributionCalendar {
            totalContributions
            weeks {
              contributionDays {
                contributionCount
              }
            }
          }
        }
        repositories(first: 100, ownerAffiliations: OWNER, orderBy: {field: STARGAZERS, direction: DESC}) {
          totalCount
          nodes {
            name
            stargazerCount
            forkCount
            isFork
            languages(first: 10, orderBy: {field: SIZE, direction: DESC}) {
              edges {
                size
                node { name }
              }
            }
          }
        }
      }
    }
    """
    res_main = request_json("https://api.github.com/graphql", token, {"query": q_main, "variables": {"login": username}})
    user = res_main.get("data", {}).get("user")
    if not user:
        raise ValueError(f"Could not retrieve user data for {username}: {res_main}")

    created_at = user["createdAt"]
    start_year = int(created_at[:4])
    curr_year = datetime.now(timezone.utc).year

    years_fields = [
        f'y{y}: contributionsCollection(from: "{y}-01-01T00:00:00Z", to: "{y}-12-31T23:59:59Z") {{ totalCommitContributions }}'
        for y in range(start_year, curr_year + 1)
    ]
    q_years = f"""query($login: String!) {{ user(login: $login) {{ {' '.join(years_fields)} }} }}"""
    res_years = request_json("https://api.github.com/graphql", token, {"query": q_years, "variables": {"login": username}})
    years_data = res_years.get("data", {}).get("user", {}) or {}
    total_commits = sum(v.get("totalCommitContributions", 0) for v in years_data.values() if v)

    try:
        rest_user = request_json(f"https://api.github.com/users/{username}", token)
        public_repos = rest_user.get("public_repos", len(user["repositories"]["nodes"]))
    except Exception:
        public_repos = len(user["repositories"]["nodes"])

    cal = user["contributionsCollection"]["contributionCalendar"]
    weeks_contribs = [sum(d["contributionCount"] for d in w["contributionDays"]) for w in cal["weeks"]]
    spark = generate_sparkline(weeks_contribs)

    all_repos = user["repositories"]["nodes"]
    total_stars = sum(r["stargazerCount"] for r in all_repos)
    total_forks = sum(r["forkCount"] for r in all_repos)
    followers = user["followers"]["totalCount"]
    prs = user["pullRequests"]["totalCount"]
    issues = user["issues"]["totalCount"]
    contributed = user["repositoriesContributedTo"]["totalCount"]
    reviews = user["contributionsCollection"]["totalPullRequestReviewContributions"]
    last_year_contribs = cal["totalContributions"]

    non_fork_repos = [r for r in all_repos if not r.get("isFork", False)]
    top_repo = non_fork_repos[0] if non_fork_repos else (all_repos[0] if all_repos else None)
    top_repo_str = f"{top_repo['name']} ({top_repo['stargazerCount']} ★)" if top_repo else ""

    lang_sizes = {}
    for r in all_repos:
        for edge in r["languages"]["edges"]:
            name = edge["node"]["name"]
            size = edge["size"]
            lang_sizes[name] = lang_sizes.get(name, 0) + size
    total_bytes = sum(lang_sizes.values())
    languages = [
        {"name": k, "share": v / total_bytes}
        for k, v in sorted(lang_sizes.items(), key=lambda x: x[1], reverse=True)
    ]
    langs_str = format_languages(languages, INFO_COLS - 17)

    return {
        "uptime": account_uptime(created_at),
        "languages_str": langs_str,
        "repos": str(public_repos),
        "stars": str(total_stars),
        "forks": str(total_forks),
        "followers": str(followers),
        "commits": str(total_commits),
        "contributed": str(contributed),
        "prs": str(prs),
        "issues": str(issues),
        "top_repo": top_repo_str,
        "contributions": str(last_year_contribs),
        "reviews": str(reviews),
        "sparkline": spark,
    }


def account_uptime(created_at_str: str, now: datetime = None) -> str:
    if now is None:
        now = datetime.now(timezone.utc)
    if created_at_str.endswith("Z"):
        created = datetime.fromisoformat(created_at_str[:-1]).replace(tzinfo=timezone.utc)
    else:
        created = datetime.fromisoformat(created_at_str)

    years = now.year - created.year
    months = now.month - created.month
    days = now.day - created.day
    if days < 0:
        months -= 1
        prev_year = now.year if now.month > 1 else now.year - 1
        prev_month = now.month - 1 if now.month > 1 else 12
        days += calendar.monthrange(prev_year, prev_month)[1]
    if months < 0:
        years -= 1
        months += 12

    parts = []
    if years > 0:
        parts.append(f"{years} year{'s' if years != 1 else ''}")
    if months > 0:
        parts.append(f"{months} month{'s' if months != 1 else ''}")
    parts.append(f"{days} day{'s' if days != 1 else ''}")
    return ", ".join(parts)


def format_languages(languages: list, max_width: int) -> str:
    parts = []
    for l in languages:
        pct = round(l["share"] * 100)
        if pct >= 1:
            parts.append(f"{l['name']} {pct}%")
    parts = parts[:5]
    while len(parts) > 1 and len(", ".join(parts)) > max_width:
        parts.pop()
    return ", ".join(parts)


def generate_sparkline(weeks: list) -> str:
    recent = weeks[-52:]
    max_count = max(recent) if recent else 1
    if max_count < 1:
        max_count = 1
    res = []
    for count in recent:
        if count <= 0:
            res.append(SPARK[0])
        else:
            level = 1 + int(math.floor((math.log1p(count) / math.log1p(max_count)) * 7))
            level = max(1, min(8, level))
            res.append(SPARK[level])
    return "".join(res)


def make_kv(key: str, value: str, c: dict) -> str:
    max_val_len = INFO_COLS - len(key) - 8
    val = value[: max_val_len - 1] + "…" if len(value) > max_val_len else value
    dot_count = max(2, INFO_COLS - len(key) - len(val) - 6)
    dots = "." * dot_count
    return (
        f'<tspan fill="{c["key"]}">. {key}: </tspan>'
        f'<tspan fill="{c["dots"]}">{dots}</tspan>'
        f'<tspan fill="{c["value"]}"> {val}</tspan>'
    )


def make_kv2(k1: str, v1: str, k2: str, v2: str, c: dict) -> str:
    dot1 = max(2, HALF - len(k1) - len(v1) - 6)
    dot2 = max(2, HALF - len(k2) - len(v2) - 6)
    dots1 = "." * dot1
    dots2 = "." * dot2
    return (
        f'<tspan fill="{c["key"]}">. {k1}: </tspan>'
        f'<tspan fill="{c["dots"]}">{dots1}</tspan>'
        f'<tspan fill="{c["number"]}"> {v1}</tspan>'
        f'<tspan fill="{c["rule"]}"> | </tspan>'
        f'<tspan fill="{c["key"]}">. {k2}: </tspan>'
        f'<tspan fill="{c["dots"]}">{dots2}</tspan>'
        f'<tspan fill="{c["number"]}"> {v2}</tspan>'
    )


def update_svg_file(path: Path, theme: str, stats: dict) -> bool:
    if not path.exists():
        print(f"File {path} not found, skipping.")
        return False

    with open(path, "r", encoding="utf-8") as f:
        original = f.read()

    c = PALETTES[theme]
    lines = original.splitlines(keepends=True)
    new_lines = []

    for line in lines:
        if "<text " in line and 'font-size="16"' in line:
            if ". Uptime:" in line:
                inner = make_kv("Uptime", stats["uptime"], c)
                line = re.sub(r"(<text [^>]+>).*(</text>)", rf"\1{inner}\2", line)
            elif ". Languages:" in line and stats.get("languages_str"):
                inner = make_kv("Languages", stats["languages_str"], c)
                line = re.sub(r"(<text [^>]+>).*(</text>)", rf"\1{inner}\2", line)
            elif ". Repos:" in line and ". Stars:" in line:
                inner = make_kv2("Repos", stats["repos"], "Stars", stats["stars"], c)
                line = re.sub(r"(<text [^>]+>).*(</text>)", rf"\1{inner}\2", line)
            elif ". Forks:" in line and ". Followers:" in line:
                inner = make_kv2("Forks", stats["forks"], "Followers", stats["followers"], c)
                line = re.sub(r"(<text [^>]+>).*(</text>)", rf"\1{inner}\2", line)
            elif ". Commits:" in line and ". Contributed:" in line:
                inner = make_kv2("Commits", stats["commits"], "Contributed", stats["contributed"], c)
                line = re.sub(r"(<text [^>]+>).*(</text>)", rf"\1{inner}\2", line)
            elif ". PRs:" in line and ". Issues:" in line:
                inner = make_kv2("PRs", stats["prs"], "Issues", stats["issues"], c)
                line = re.sub(r"(<text [^>]+>).*(</text>)", rf"\1{inner}\2", line)
            elif ". Top repo:" in line:
                inner = make_kv("Top repo", stats["top_repo"], c)
                line = re.sub(r"(<text [^>]+>).*(</text>)", rf"\1{inner}\2", line)
            elif ". Contributions:" in line and ". Reviews:" in line:
                inner = make_kv2("Contributions", stats["contributions"], "Reviews", stats["reviews"], c)
                line = re.sub(r"(<text [^>]+>).*(</text>)", rf"\1{inner}\2", line)
            elif 'y="423"' in line or any(ch in line for ch in "▁▂▃▄▅▆▇█"):
                inner = f'<tspan fill="{c["spark"]}">  {stats["sparkline"]}</tspan>'
                line = re.sub(r"(<text [^>]+>).*(</text>)", rf"\1{inner}\2", line)

        new_lines.append(line)

    updated = "".join(new_lines)
    if updated != original:
        with open(path, "w", encoding="utf-8") as f:
            f.write(updated)
        print(f"Updated {path.name} with live stats.")
        return True
    else:
        print(f"{path.name} is already up to date.")
        return False


def main():
    username = os.environ.get("GITHUB_ACTOR", "ayushdas27")
    token = get_token()
    if not token:
        print("Warning: GITHUB_TOKEN not found; proceeding with unauthenticated requests if possible.")

    print(f"Fetching live statistics for @{username}...")
    stats = fetch_stats(username, token)
    print("Live stats fetched:")
    for k, v in stats.items():
        if k != "sparkline":
            print(f"  {k}: {v}")

    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent.parent

    dark_path = repo_root / "dark_mode.svg"
    light_path = repo_root / "light_mode.svg"

    changed_dark = update_svg_file(dark_path, "dark", stats)
    changed_light = update_svg_file(light_path, "light", stats)

    if changed_dark or changed_light:
        print("Profile cards successfully updated.")
    else:
        print("No changes needed in SVG cards.")


if __name__ == "__main__":
    main()
