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
          nodes{name stargazerCount primaryLanguage{name} languages(first:5,orderBy:{field:SIZE,direction:DESC}){edges{size node{name}}}}}}}""",
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
        "repo_info": {r["name"]: (r["stargazerCount"], (r["primaryLanguage"] or {}).get("name", ""))
                      for r in u["repositories"]["nodes"]},
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


SEASON_ORDER = ("winter", "spring", "summer", "autumn")
SEASON_OF_MONTH = {m: SEASON_ORDER[(m % 12) // 3] for m in range(1, 13)}
# palette, share of tips in leaf, leaves per tip, dot size
FOLIAGE = {
    "winter": (["#e5eff7", "#a9cbe0", "#88c0d0"], .5, 1, 3.0),
    "spring": (["#f2b8cf", "#e88bb0", "#b9dd8f"], 1.0, 2, 3.3),
    "summer": (["#a3be8c", "#8fbf78", "#c6e79a"], 1.0, 3, 3.5),
    "autumn": (["#f0a35b", "#d97b4a", "#ebcb8b", "#bf616a"], .85, 2, 3.7),
}
# A plain bracketed L-system: X is a growing tip, F is wood.
AXIOM, RULES = "X", {"X": "F[+X]F[-X]+X", "F": "FF"}
BOUNDS = (24, 100, 876, 0)  # left, top, right; bottom is set per render


def season_years(days, today):
    """[(year, {season: contributions})] from the first active year to now; winter starts in December."""
    totals = {}
    for d, c in days.items():
        if c:
            key = (d.year + (d.month == 12), SEASON_OF_MONTH[d.month])
            totals[key] = totals.get(key, 0) + c
    if not totals:
        return []
    first, last = min(y for y, _ in totals), max(today.year + (today.month == 12), max(y for y, _ in totals))
    return [(y, {s: totals.get((y, s), 0) for s in SEASON_ORDER}) for y in range(first, last + 1)]


def lsystem(n):
    s = AXIOM
    for _ in range(n):
        s = "".join(RULES.get(ch, ch) for ch in s)
    return s


def turtle(program, heading, rnd):
    """Walk the program in unit steps; returns wood segments (x1, y1, x2, y2, depth) and tip points."""
    x = y = 0.0
    depth, stack, segs, tips = 0, [], [], []
    for ch in program:
        if ch == "F":
            if segs and rnd.random() < .3:  # skipped wood gives each bough its own shape
                continue
            heading += ((-90 - heading + 180) % 360 - 180) * .06  # lean towards the light
            nx, ny = x + math.cos(math.radians(heading)), y + math.sin(math.radians(heading))
            segs.append((x, y, nx, ny, depth))
            x, y = nx, ny
        elif ch == "X":
            tips.append((x, y))
        elif ch in "+-":
            heading += (1 if ch == "+" else -1) * (24 + rnd.uniform(-9, 9))
        elif ch == "[":
            stack.append((x, y, heading, depth))
            depth += 1
        elif ch == "]":
            x, y, heading, depth = stack.pop()
    return segs, tips


def ribbon(points, widths, fill):
    """A filled outline of a polyline with a width at each point."""
    left, right = [], []
    for i, (x, y) in enumerate(points):
        (ax, ay), (bx, by) = points[max(i - 1, 0)], points[min(i + 1, len(points) - 1)]
        n = math.hypot(bx - ax, by - ay) or 1
        ox, oy = -(by - ay) / n * widths[i] / 2, (bx - ax) / n * widths[i] / 2
        left.append(f"{x + ox:.1f} {y + oy:.1f}")
        right.append(f"{x - ox:.1f} {y - oy:.1f}")
    return f'<path d="M{"L".join(left)}L{"L".join(reversed(right))}Z" fill="{fill}"/>'


def bough(origin, heading, count, peak, season, girth, rnd, bounds):
    ratio = math.log1p(count) / math.log1p(peak)
    segs, tips = turtle(lsystem(3 + round(3 * ratio)), heading, rnd)
    pts = [(s[0], s[1]) for s in segs] + [(s[2], s[3]) for s in segs]
    scale = (36 + 130 * ratio) / max(math.hypot(x, y) for x, y in pts)
    ox, oy = origin
    left, top, right, bottom = bounds
    for room, extent in ((ox - left, -min(x for x, _ in pts)), (right - ox, max(x for x, _ in pts)),
                         (oy - top, -min(y for _, y in pts)), (bottom - oy, max(y for _, y in pts))):
        if extent > 0:  # shrink rather than spill out of the frame
            scale = min(scale, max(room - 6, 1) / extent)

    def at(x, y):
        return ox + x * scale, oy + y * scale

    by_depth = {}
    for x1, y1, x2, y2, d in segs:
        (a, b), (c, e) = at(x1, y1), at(x2, y2)
        by_depth.setdefault(d, []).append(f"M{a:.1f} {b:.1f}L{c:.1f} {e:.1f}")
    out = [f'<g><title>{season}: {count:,} contributions</title>']
    for d, cmds in sorted(by_depth.items()):
        out.append(f'<path d="{"".join(cmds)}" stroke="#8f9bb8" stroke-width="{max(.5, girth * .8 * .7 ** d):.2f}" '
                   'stroke-linecap="round" fill="none"/>')
    palette, share, cluster, size = FOLIAGE[season]
    dots = {}
    for x, y in rnd.sample(tips, max(1, round(len(tips) * share))):
        tx, ty = at(x, y)
        for k in range(cluster):
            spread = (1.5 + 2.5 * ratio) if k else 0
            lx = min(max(tx + rnd.gauss(0, spread), left), right)
            ly = min(max(ty + rnd.gauss(0, spread), top), bottom)
            key = (rnd.choice(palette), rnd.choice((.8, 1.0, 1.3)))
            dots.setdefault(key, []).append(f"M{lx:.1f} {ly:.1f}h0")
    out.append('<g filter="url(#glow)" opacity=".92">')
    for (colour, weight), d in sorted(dots.items()):
        out.append(f'<path d="{"".join(d)}" stroke="{colour}" stroke-width="{size * weight * (.8 + .4 * ratio):.1f}" '
                   'stroke-linecap="round"/>')
    out.append("</g></g>")
    return "".join(out)


def tree_svg(p):
    years = season_years(p["days"], p["today"])
    w, h = 900, 660
    base, crown, cx = h - 120, 290, w / 2
    bounds = (BOUNDS[0], BOUNDS[1], BOUNDS[2], base - 8)
    totals = [sum(s.values()) for _, s in years] or [0]
    peak_year = max(totals) or 1
    peak_season = max((c for _, s in years for c in s.values()), default=1) or 1
    rnd = random.Random(years[0][0] if years else 0)

    # Trunk: a gently wandering line from the roots to the crown.
    ys = [base - (base - crown) * i / 60 for i in range(61)]
    trunk = [(cx + 7 * math.sin((base - y) / 75) - 4 * math.sin((base - y) / 31), y) for y in ys]
    girth = [6 + 16 * ((y - crown) / (base - crown)) ** 1.4 + 14 * math.exp(-(base - y) / 14) for y in ys]

    def trunk_at(y):
        return min(trunk, key=lambda pt: abs(pt[1] - y))

    parts = []
    for angle, length, rw in ((195, 52, 10), (168, 32, 7), (-15, 56, 11), (12, 30, 7), (182, 22, 5)):
        sx, ex = cx + math.cos(math.radians(angle)) * 6, cx + math.cos(math.radians(angle)) * length
        line = [(sx + (ex - sx) * t, base - 10 + 14 * t + 4 * abs(math.sin(math.radians(angle))) * t * t) for t in
                (i / 12 for i in range(13))]
        parts.append(ribbon(line, [rw * (1 - i / 13) + .6 for i in range(13)], "url(#bark)"))
    parts.append(ribbon(trunk, girth, "url(#bark)"))

    n = len(years)
    side, knots, limbs = -1, [], []
    for i, ((year, seasons), total) in enumerate(zip(years, totals)):
        frac = (i + 1) / (n + 1) if n > 1 else .5
        y = base - 60 - frac * (base - 60 - crown - 20)
        x, _ = trunk_at(y)
        if not total:
            knots.append(f'<ellipse cx="{x:.1f}" cy="{y:.1f}" rx="2.6" ry="3.8" fill="#4c566a" stroke="#8f9bb8" '
                         f'stroke-width=".6"><title>{year}: no public contributions</title></ellipse>')
            continue
        side = -side
        ratio = math.log1p(total) / math.log1p(peak_year)
        leader = i == n - 1
        lean = math.radians((10 if leader else 62 - 30 * frac) + rnd.uniform(-5, 5))
        length = (60 + 120 * ratio) * (.45 if leader else 1 - .3 * frac)
        end = (x + side * math.sin(lean) * length, y - math.cos(lean) * length)
        ctrl = (x + side * math.sin(lean + .5) * length * .55, y - math.cos(lean + .5) * length * .55)
        curve = [((1 - t) ** 2 * x + 2 * (1 - t) * t * ctrl[0] + t * t * end[0],
                  (1 - t) ** 2 * y + 2 * (1 - t) * t * ctrl[1] + t * t * end[1]) for t in (k / 16 for k in range(17))]
        thick = 2.5 + 5 * ratio
        widths = [1 + (thick - 1) * (1 - k / 16) ** 1.2 for k in range(17)]
        sway = random.Random(year * 31)
        dur = 7 + sway.uniform(0, 3)
        g = [f'<g class="limb" style="transform-origin:{x:.1f}px {y:.1f}px;animation-duration:{dur:.1f}s;'
             f'animation-delay:-{sway.uniform(0, dur):.1f}s"><title>{year}: {total:,} public contributions</title>',
             ribbon(curve, widths, "url(#limb)")]
        active = [(s, c) for s, c in seasons.items() if c]
        for slot, (season, count) in enumerate(active):
            last = slot == len(active) - 1
            k = 16 if last else round(16 * (.35 + .45 * slot / max(len(active) - 1, 1)))
            (ax, ay), (bx, by) = curve[max(k - 1, 0)], curve[min(k + 1, 16)]
            heading = math.degrees(math.atan2(by - ay, bx - ax))
            if not last:
                heading += (1 if slot % 2 else -1) * side * (52 + rnd.uniform(-6, 6))
            g.append(bough(curve[k], heading, count, peak_season, season, widths[k],
                           random.Random(f"{year}{season}"), bounds))
        g.append("</g>")
        limbs.append("".join(g))
    parts += limbs + knots

    total = sum(totals)
    first_year = years[0][0] if years else p["today"].year
    legend = "".join(
        f'<circle cx="{44 + i * 110}" cy="{base + 44}" r="4.5" fill="{FOLIAGE[s][0][0 if s != "winter" else 1]}"/>'
        f'<text x="{56 + i * 110}" y="{base + 48}" font-size="12" fill="#d8dee9">{s}</text>'
        for i, s in enumerate(SEASON_ORDER)
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img">'
        f'<title>A tree of commits: {total:,} public contributions since {first_year}</title>'
        '<desc>Each active season-year is a limb, oldest at the roots and newest in the crown. Each season in that '
        'year grows its own bough and foliage; empty years are knots in the trunk. Winter starts in December.</desc>'
        '<defs><linearGradient id="bark" x1="0" x2="1"><stop offset="0" stop-color="#5e6a85"/>'
        '<stop offset=".55" stop-color="#9aa6c0"/><stop offset="1" stop-color="#5e6a85"/></linearGradient>'
        '<linearGradient id="limb" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#9aa6c0"/>'
        '<stop offset="1" stop-color="#76839f"/></linearGradient>'
        '<linearGradient id="aurora" x1="0" x2="1"><stop offset="0" stop-color="#bf616a"/>'
        '<stop offset=".25" stop-color="#d08770"/><stop offset=".5" stop-color="#ebcb8b"/>'
        '<stop offset=".75" stop-color="#a3be8c"/><stop offset="1" stop-color="#b48ead"/></linearGradient>'
        '<filter id="glow"><feGaussianBlur stdDeviation=".8" result="b"/><feMerge><feMergeNode in="b"/>'
        '<feMergeNode in="SourceGraphic"/></feMerge></filter></defs>'
        '<style>@keyframes sway{0%,100%{transform:rotate(-.6deg)}50%{transform:rotate(.6deg)}}'
        '.limb{animation:sway 8s ease-in-out infinite}@media(prefers-reduced-motion:reduce){.limb{animation:none}}</style>'
        f'<rect width="{w}" height="{h}" rx="16" fill="#2e3440"/>'
        f'<g font-family="{MONO}">'
        '<text x="40" y="52" font-size="22" font-weight="700" fill="#eceff4" letter-spacing="2">A TREE OF COMMITS</text>'
        '<text x="40" y="76" font-size="12" fill="#a3adc2">Every active season-year grows a limb, '
        'with foliage for its seasons.</text>'
        f'<path d="M16 {base + 6}Q{w * .2:.0f} {base - 16} {w * .45:.0f} {base - 4}T{w - 16} {base + 4}V{base + 20}H16Z" '
        'fill="#3b4252" opacity=".7"/>'
        f'<rect x="16" y="{base + 18}" width="{w - 32}" height="2" fill="url(#aurora)" opacity=".7"/>'
        f'{"".join(parts)}{legend}'
        f'<text x="40" y="{base + 78}" font-size="10.5" fill="#7f8aa3">Oldest at the roots, newest in the crown. '
        f'Size follows activity on a log scale. {total:,} public contributions since {first_year}.</text>'
        f'<text x="40" y="{base + 96}" font-size="10.5" fill="#7f8aa3">Winter begins the previous December; '
        f'empty years are knots. Refreshed {p["today"]:%Y-%m-%d}.</text>'
        "</g></svg>"
    )


PROJECTS = [  # slug, repo, kicker, title, blurb, highlight, accent
    ("morph", "morph", "FILE CONVERTER / 4 PLATFORMS", "Morph",
     "Drop any file, tap a key, and a wheel of the actions that fit it opens under the cursor. "
     "Convert, compress, blur faces, merge PDFs. All on device.",
     "App Store / Play / MS Store", "#88c0d0"),
    ("uyuni", "gsoc-2026-uyuni-mqtt", "GSOC 2026 / OPENSUSE", "Uyuni MQTT",
     "Live MQTT events from Uyuni's Java core and a Node-RED node package, "
     "so automations react to registrations and Salt jobs instead of polling.",
     "+5,211 lines / 69 files", "#a3be8c"),
    ("doorman", "m5-StickC-plus2-doorman", "ESP32 FIRMWARE", "Doorman",
     "Signs in to a hotel or campus captive portal once, WPA2-Enterprise included, "
     "and shares a plain network with every smart device behind it.",
     "flash it from the browser", "#ebcb8b"),
    ("retroos", "Retro-OS", "VANILLA JS / ZERO DEPS", "RetroOS",
     "A tiling window manager and desktop with workspaces and a real terminal, "
     "running on a CRT inside a 3D room.",
     "live on geetanshgoyal.in", "#b48ead"),
]


def wrap(text, width):
    lines, line = [], ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    return lines + [line]


def project_svg(p, project):
    _, repo, kicker, title, blurb, highlight, accent = project
    stars, lang = p["repo_info"].get(repo, (0, ""))
    lang = {"uyuni": "Java"}.get(project[0], lang)  # that repo is mostly docs; the PR itself is Java
    w, h = 440, 250
    body = "".join(
        f'<text x="28" y="{118 + i * 21}" font-size="13" fill="#d8dee9">{escape(line)}</text>'
        for i, line in enumerate(wrap(blurb, 50)[:4])
    )
    chips, x = [], 28
    for label in [c for c in (lang, f"\u2605 {stars}" if stars else "", highlight) if c]:
        cw = 7.3 * len(label) + 20
        chips.append(f'<rect x="{x}" y="{h - 46}" width="{cw:.0f}" height="24" rx="12" fill="none" stroke="#4c566a"/>'
                     f'<text x="{x + cw / 2:.0f}" y="{h - 29}" font-size="11.5" fill="#a3adc2" '
                     f'text-anchor="middle">{escape(label)}</text>')
        x += cw + 8
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img">'
        f'<title>{escape(title)}: {escape(blurb)}</title>'
        f'<rect x=".5" y=".5" width="{w - 1}" height="{h - 1}" rx="14" fill="#2e3440" stroke="#3b4252"/>'
        f'<rect x="0" y="22" width="5" height="54" rx="2" fill="{accent}"/>'
        f'<g font-family="{MONO}">'
        f'<text x="28" y="40" font-size="11" letter-spacing="1.5" fill="{accent}">{escape(kicker)}</text>'
        f'<text x="28" y="76" font-size="26" font-weight="700" fill="#eceff4">{escape(title)}</text>'
        f'<text x="{w - 26}" y="40" font-size="16" fill="#4c566a" text-anchor="end">\u2197</text>'
        f'{body}{"".join(chips)}</g></svg>'
    )



def main():
    login, out = sys.argv[1], sys.argv[2]
    os.makedirs(out, exist_ok=True)
    p = fetch(login)
    assert p["days"], "no contribution data returned"
    for name, fn in (("activity", activity_svg), ("profile", profile_svg), ("tree", tree_svg)):
        with open(os.path.join(out, f"{name}.svg"), "w") as f:
            f.write(fn(p))
    for project in PROJECTS:
        with open(os.path.join(out, f"project-{project[0]}.svg"), "w") as f:
            f.write(project_svg(p, project))
    with open(os.path.join(out, "history.json"), "w") as f:
        json.dump([{"year": y, "seasons": s} for y, s in season_years(p["days"], p["today"])], f, indent=1)
    print(f"wrote {out}/activity.svg profile.svg tree.svg ({len(p['days'])} days)")


if __name__ == "__main__":
    # Self-check for the streak logic.
    t = dt.date(2026, 1, 10)
    assert streaks({t - dt.timedelta(days=i): 1 for i in range(1, 4)}, t) == (3, 3)
    assert streaks({t: 0, t - dt.timedelta(days=5): 2}, t) == (0, 1)
    # December counts towards the next year's winter.
    assert season_years({dt.date(2025, 12, 5): 3, dt.date(2026, 4, 1): 1}, t) == [
        (2026, {"winter": 3, "spring": 1, "summer": 0, "autumn": 0})]
    main()
