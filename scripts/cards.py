"""Render the profile cards (activity, profile, commit tree) from public GitHub data.

Usage: GH_TOKEN=... python scripts/cards.py <login> <out_dir>
Stdlib only, so the workflow needs no pip install.
"""
import base64
import datetime as dt
import json
import math
import os
import random
import sys
import urllib.request
from xml.sax.saxutils import escape

API = "https://api.github.com/graphql"
MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,monospace"


def gql(query, **variables):
    req = urllib.request.Request(
        API,
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Authorization": f"bearer {os.environ['GH_TOKEN']}", "User-Agent": "profile-cards"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        body = json.load(r)
    if body.get("errors"):
        raise SystemExit(f"GraphQL error: {body['errors']}")
    return body["data"]


def fetch(login):
    u = gql(
        """query($l:String!){user(login:$l){name login avatarUrl location company createdAt
        followers{totalCount}
        repositories(first:100,ownerAffiliations:OWNER,privacy:PUBLIC,isFork:false){totalCount
          nodes{stargazerCount languages(first:5,orderBy:{field:SIZE,direction:DESC}){edges{size node{name}}}}}}}""",
        l=login,
    )["user"]
    prs = gql(
        "query($q:String!){search(query:$q,type:ISSUE){issueCount}}",
        q=f"author:{login} is:pr is:merged",
    )["search"]["issueCount"]

    days = {}
    start = dt.datetime.fromisoformat(u["createdAt"].replace("Z", "+00:00")).year
    now = dt.datetime.now(dt.timezone.utc)
    for year in range(start, now.year + 1):
        frm = f"{year}-01-01T00:00:00Z"
        to = min(now, dt.datetime(year, 12, 31, 23, 59, 59, tzinfo=dt.timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
        cal = gql(
            """query($l:String!,$f:DateTime!,$t:DateTime!){user(login:$l){contributionsCollection(from:$f,to:$t){
            contributionCalendar{weeks{contributionDays{date contributionCount}}}}}}""",
            l=login, f=frm, t=to,
        )["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]
        for w in cal:
            for d in w["contributionDays"]:
                days[dt.date.fromisoformat(d["date"])] = d["contributionCount"]

    langs = {}
    for repo in u["repositories"]["nodes"]:
        for e in repo["languages"]["edges"]:
            langs[e["node"]["name"]] = langs.get(e["node"]["name"], 0) + e["size"]
    photo = os.path.join(os.path.dirname(__file__), "..", "assets", "avatar.jpg")
    if os.path.exists(photo):  # a real photo beats the GitHub avatar
        with open(photo, "rb") as f:
            avatar = "data:image/jpeg;base64," + base64.b64encode(f.read()).decode()
    else:
        with urllib.request.urlopen(u["avatarUrl"] + "&s=240", timeout=60) as r:
            avatar = "data:" + r.headers.get_content_type() + ";base64," + base64.b64encode(r.read()).decode()

    return {
        "name": u["name"] or u["login"],
        "login": u["login"],
        "location": u["location"] or "",
        "company": (u["company"] or "").lstrip("@"),
        "joined": u["createdAt"][:10],
        "repos": u["repositories"]["totalCount"],
        "stars": sum(r["stargazerCount"] for r in u["repositories"]["nodes"]),
        "followers": u["followers"]["totalCount"],
        "language": max(langs, key=langs.get) if langs else "",
        "merged_prs": prs,
        "avatar": avatar,
        "days": days,
        "today": now.date(),
    }


def streaks(days, today):
    longest = run = 0
    for d in sorted(days):
        run = run + 1 if days[d] else 0
        longest = max(longest, run)
    current, d = 0, today
    if not days.get(d):  # today may simply not have started yet
        d -= dt.timedelta(days=1)
    while days.get(d):
        current += 1
        d -= dt.timedelta(days=1)
    return current, longest


# 5x7 dot-matrix glyphs for the big counter.
GLYPHS = {
    "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
    "3": ["11110", "00001", "00001", "01110", "00001", "00001", "11110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    "5": ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
    "6": ["00110", "01000", "10000", "11110", "10001", "10001", "01110"],
    "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    "9": ["01110", "10001", "10001", "01111", "00001", "00010", "01100"],
    ",": ["00000", "00000", "00000", "00000", "00110", "00100", "01000"],
}


def dot_text(text, x, y, dot, fill):
    out, pitch = [], dot * 1.75
    for ch in text:
        g = GLYPHS[ch]
        for r, row in enumerate(g):
            for c, on in enumerate(row):
                if on == "1":
                    out.append(f'<rect x="{x + c * pitch:.1f}" y="{y + r * pitch:.1f}" width="{dot}" height="{dot}" rx="0.6"/>')
        x += pitch * (3.4 if ch == "," else 6.2)
    return f'<g fill="{fill}">{"".join(out)}</g>', x


PAPER, INK, FAINT, RULE = "#f2ece0", "#25231f", "#8a8274", "#d6cebd"


def paper(w, h, body, title):
    holes = "".join(
        f'<circle cx="{cx}" cy="{y}" r="4.5" fill="#1b1a17" opacity=".85"/>'
        for y in range(40, h - 20, 34)
        for cx in (16, w - 16)
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img">'
        f"<title>{escape(title)}</title>"
        f'<rect width="{w}" height="{h}" rx="14" fill="{PAPER}"/>{holes}'
        f'<g font-family="{MONO}" fill="{INK}">{body}</g></svg>'
    )


def activity_svg(p):
    today = p["today"]
    start = today - dt.timedelta(days=364)
    year = sum(c for d, c in p["days"].items() if d >= start)
    cur, longest = streaks(p["days"], today)
    w, h = 820, 420
    head = (
        f'<text x="44" y="46" font-size="13" letter-spacing="2">GITHUB / ACTIVITY RECORDER</text>'
        f'<text x="{w - 44}" y="46" font-size="11" fill="{FAINT}" text-anchor="end" letter-spacing="1">'
        f'PERIOD {f"{start:%b %Y} – {today:%b %Y}".upper()}   @{escape(p["login"])}</text>'
        f'<line x1="44" y1="60" x2="{w - 44}" y2="60" stroke="{INK}" stroke-width="1.5"/>'
        f'<text x="44" y="88" font-size="15" letter-spacing="1">{escape(p["name"].upper())} / @{escape(p["login"])}</text>'
    )
    number, nx = dot_text(f"{year:,}", 44, 104, 9, INK)
    side = 560
    stat = lambda y, v, label: (
        f'<text x="{side}" y="{y}" font-size="34" font-weight="700">{v}</text>'
        f'<text x="{side + 110}" y="{y - 4}" font-size="11" fill="{FAINT}" letter-spacing="1">{label}</text>'
    )
    stats = (
        stat(140, cur, "CURRENT STREAK")
        + f'<line x1="{side}" y1="156" x2="{w - 44}" y2="156" stroke="{RULE}"/>'
        + stat(196, longest, "LONGEST STREAK")
        + f'<line x1="{side}" y1="212" x2="{w - 44}" y2="212" stroke="{RULE}"/>'
        + stat(252, f'{p["merged_prs"]}', "MERGED PRS")
    )
    caption = f'<text x="44" y="232" font-size="12" letter-spacing="1" fill="{FAINT}">CONTRIBUTIONS / LAST 12 MONTHS</text>'
    # 53-week heatmap, darker dot = busier day.
    first = start - dt.timedelta(days=(start.weekday() + 1) % 7)
    peak = max([c for d, c in p["days"].items() if d >= start] or [1]) or 1
    cells, gx, gy, pitch = [], 44, 270, 13.9
    for i in range((today - first).days + 1):
        d = first + dt.timedelta(days=i)
        if d < start:
            continue
        c = p["days"].get(d, 0)
        level = 0 if c == 0 else 1 + min(3, int(4 * math.log1p(c) / math.log1p(peak)))
        op = [0.13, 0.35, 0.55, 0.78, 1][level]
        cells.append(f'<rect x="{gx + (i // 7) * pitch:.1f}" y="{gy + (i % 7) * pitch:.1f}" width="10" height="10" rx="1.5" fill="{INK}" opacity="{op}"/>')
    foot = (
        f'<text x="44" y="{h - 26}" font-size="10" fill="{FAINT}" letter-spacing="1">WEEK 01</text>'
        f'<text x="{w - 44}" y="{h - 26}" font-size="10" fill="{FAINT}" text-anchor="end" letter-spacing="1">'
        f'UPDATED {today:%Y-%m-%d} · {sum(p["days"].values()):,} PUBLIC CONTRIBUTIONS SINCE {p["joined"][:4]}</text>'
    )
    return paper(w, h, head + number + stats + caption + "".join(cells) + foot,
                 f'{year:,} contributions in the last 12 months, current streak {cur}, longest streak {longest}')


def profile_svg(p):
    w, h = 820, 330
    joined = dt.date.fromisoformat(p["joined"])
    fields = [
        ("ORGANIZATION", p["company"] or "openSUSE"),
        ("LOCATION", p["location"]),
        ("MEMBER SINCE", f"{joined:%b %Y}".upper()),
        ("PRIMARY LANGUAGE", p["language"]),
        ("PUBLIC REPOS", str(p["repos"])),
    ]
    col = (w - 88) / len(fields)
    cells = "".join(
        f'<text x="{44 + i * col:.0f}" y="268" font-size="10" fill="{FAINT}" letter-spacing="1">{k}</text>'
        f'<text x="{44 + i * col:.0f}" y="292" font-size="15" font-weight="700">{escape(v)}</text>'
        + (f'<line x1="{36 + i * col:.0f}" y1="252" x2="{36 + i * col:.0f}" y2="300" stroke="{RULE}"/>' if i else "")
        for i, (k, v) in enumerate(fields)
    )
    first, _, rest = p["name"].partition(" ")
    body = (
        f'<text x="44" y="46" font-size="13" letter-spacing="2">GITHUB / PROFILE</text>'
        f'<text x="{w - 44}" y="46" font-size="11" fill="{FAINT}" text-anchor="end" letter-spacing="1">UPDATED {p["today"]:%Y-%m-%d}</text>'
        f'<line x1="44" y1="60" x2="{w - 44}" y2="60" stroke="{INK}" stroke-width="1.5"/>'
        '<defs><clipPath id="av"><circle cx="134" cy="150" r="70"/></clipPath>'
        '<filter id="mono"><feColorMatrix type="saturate" values="0"/>'
        '<feComponentTransfer><feFuncR type="linear" slope="1.9" intercept=".08"/><feFuncG type="linear" slope="1.9" intercept=".08"/><feFuncB type="linear" slope="1.9" intercept=".08"/></feComponentTransfer><feComponentTransfer><feFuncR type="discrete" tableValues="0.15 0.45 0.75 0.95"/>'
        '<feFuncG type="discrete" tableValues="0.14 0.43 0.72 0.93"/>'
        '<feFuncB type="discrete" tableValues="0.12 0.39 0.66 0.88"/></feComponentTransfer></filter></defs>'
        f'<image href="{p["avatar"]}" x="64" y="80" width="140" height="140" clip-path="url(#av)" filter="url(#mono)"/>'
        f'<circle cx="134" cy="150" r="70" fill="none" stroke="{INK}" stroke-width="1.5"/>'
        f'<text x="244" y="136" font-size="46" font-weight="800" letter-spacing="1">{escape(first)}</text>'
        f'<text x="244" y="186" font-size="46" font-weight="800" letter-spacing="1">{escape(rest)}</text>'
        f'<text x="246" y="216" font-size="15" fill="{FAINT}">@{escape(p["login"])} · {p["stars"]} stars · {p["merged_prs"]} merged PRs</text>'
        f'<line x1="44" y1="244" x2="{w - 44}" y2="244" stroke="{INK}" stroke-width="1"/>' + cells
    )
    return paper(w, h, body, f'GitHub profile of {p["name"]} (@{p["login"]})')


SEASONS = {  # name: (months, palette)
    "winter": ((12, 1, 2), ["#cfe3f0", "#a9cbe0", "#ffffff"]),
    "spring": ((3, 4, 5), ["#f2b8cf", "#e88bb0", "#b9dd8f"]),
    "summer": ((6, 7, 8), ["#9fd27a", "#6fb85a", "#c6e79a"]),
    "autumn": ((9, 10, 11), ["#f0a35b", "#e2703f", "#f3c969", "#c94f3a"]),
}


def season_years(days):
    totals = {}
    for d, c in days.items():
        if not c:
            continue
        name = next(n for n, (ms, _) in SEASONS.items() if d.month in ms)
        year = d.year + 1 if d.month == 12 else d.year  # winter starts the previous December
        totals[(year, name)] = totals.get((year, name), 0) + c
    order = list(SEASONS)
    return sorted(totals.items(), key=lambda kv: (kv[0][0], order.index(kv[0][1])))


def tree_svg(p):
    limbs = season_years(p["days"])
    w, h = 900, 640
    ground, top = h - 70, 300
    cx = w / 2
    peak = max(c for _, c in limbs)
    lines, leaves = [], []
    trunk_w = 22

    def branch(x, y, ang, length, width, depth, rnd, pal, density):
        x2, y2 = x + math.cos(ang) * length, y - math.sin(ang) * length
        lines.append(f'<line x1="{x:.1f}" y1="{y:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke-width="{width:.2f}"/>')
        if depth == 0 or length < 7:
            for _ in range(density):
                r = rnd.uniform(2.2, 4.4)
                leaves.append(
                    f'<circle cx="{x2 + rnd.gauss(0, 9):.1f}" cy="{y2 + rnd.gauss(0, 7):.1f}" r="{r:.1f}" '
                    f'fill="{rnd.choice(pal)}" opacity="{rnd.uniform(.65, .95):.2f}"/>'
                )
            return
        for spread in (rnd.uniform(.25, .55), -rnd.uniform(.25, .55)):
            branch(x2, y2, ang + spread, length * rnd.uniform(.62, .78), width * .66, depth - 1, rnd, pal, density)

    n = len(limbs)
    for i, ((year, season), count) in enumerate(limbs):
        rnd = random.Random(f"{year}{season}")
        t = (i + 1) / (n + 1)
        y = ground - 60 - t * (ground - 60 - top)
        side = 1 if i % 2 else -1
        scale = math.log1p(count) / math.log1p(peak)
        ang = math.pi / 2 + side * (1.15 - 0.55 * t) + rnd.uniform(-.08, .08)
        length = (45 + 55 * scale) * (1.05 - 0.3 * t)
        depth = 3 + round(2 * scale)
        branch(cx, y, ang, length, 2 + 6 * scale, depth, rnd, SEASONS[season][1], 2 + round(4 * scale))
    trunk = (
        f'<path d="M{cx - trunk_w} {ground} C{cx - 8} {ground - 120} {cx - 4} {top + 80} {cx - 2} {top} '
        f'L{cx + 2} {top} C{cx + 4} {top + 80} {cx + 8} {ground - 120} {cx + trunk_w} {ground} Z" fill="#8f9bb8"/>'
    )
    first_year = limbs[0][0][0] if limbs else p["today"].year
    legend = "".join(
        f'<circle cx="{60 + i * 120}" cy="{h - 30}" r="5" fill="{pal[0]}"/>'
        f'<text x="{72 + i * 120}" y="{h - 26}" font-size="13" fill="#d9dee8">{name}</text>'
        for i, (name, (_, pal)) in enumerate(SEASONS.items())
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img">'
        f'<title>A tree of {sum(p["days"].values()):,} public contributions, one limb per active season</title>'
        f'<defs><clipPath id="frame"><rect x="0" y="96" width="{w}" height="{h - 150}"/></clipPath></defs>'
        f'<rect width="{w}" height="{h}" rx="16" fill="#2e3440"/>'
        f'<g font-family="{MONO}">'
        f'<text x="40" y="52" font-size="24" font-weight="700" fill="#eceff4" letter-spacing="2">A TREE OF COMMITS</text>'
        f'<text x="40" y="78" font-size="13" fill="#a3adc2">{len(limbs)} active seasons since {first_year}, '
        f'{sum(p["days"].values()):,} public contributions. Oldest at the roots, newest in the crown.</text>'
        f'<ellipse cx="{cx}" cy="{ground + 4}" rx="360" ry="16" fill="#3b4252"/>'
        f'<g clip-path="url(#frame)"><g stroke="#8f9bb8" stroke-linecap="round">{"".join(lines)}</g>{trunk}{"".join(leaves)}</g>'
        f'<line x1="40" y1="{h - 52}" x2="{w - 40}" y2="{h - 52}" stroke="#434c5e"/>{legend}'
        f'<text x="{w - 40}" y="{h - 26}" font-size="11" fill="#7f8aa3" text-anchor="end">refreshed {p["today"]:%Y-%m-%d}</text>'
        f"</g></svg>"
    )


def main():
    login, out = sys.argv[1], sys.argv[2]
    os.makedirs(out, exist_ok=True)
    p = fetch(login)
    assert p["days"], "no contribution data returned"
    for name, fn in (("activity", activity_svg), ("profile", profile_svg), ("tree", tree_svg)):
        with open(os.path.join(out, f"{name}.svg"), "w") as f:
            f.write(fn(p))
    print(f"wrote {out}/activity.svg profile.svg tree.svg ({len(p['days'])} days)")


if __name__ == "__main__":
    # Self-check for the streak logic.
    t = dt.date(2026, 1, 10)
    assert streaks({t - dt.timedelta(days=i): 1 for i in range(1, 4)}, t) == (3, 3)
    assert streaks({t: 0, t - dt.timedelta(days=5): 2}, t) == (0, 1)
    main()
