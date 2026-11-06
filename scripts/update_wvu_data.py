"""Build wvu-data.json: the WVU data ESPN doesn't carry, pulled from the official sources.

  Rifle schedule, venue, field   wvusports.com (Sidearm schedule data embedded in the page)
  Rifle records (every team)     ncaarifle.org team results (the NCAA's official rifle scoring system)
  Rifle ranks                    ncaa.com CRCA Top 20
  GARC conference records/rank   conference-marked matches on the GARC schools' own schedules
  Baseball schedule              wvusports.com (used until ESPN posts the season)
  Soccer conference standings    sunbeltsports.org (men), big12sports.com (women)
  Team abbreviations/conference  ESPN

Run by .github/workflows/update-wvu-data.yml. Any source that fails is left out of the file, and the
page falls back to the values typed into index.html for that part.
Usage: python scripts/update_wvu_data.py [output.json]
"""
import html
import json
import re
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo('America/New_York')
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36'
NOW = datetime.now(timezone.utc)
TODAY = NOW.astimezone(ET).date()
SEASON_START = date(TODAY.year if TODAY.month >= 7 else TODAY.year - 1, 7, 1)   # rifle season: fall through March
log = lambda *a: print(*a, file=sys.stderr)


def get(url, data=None):
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=45) as r:
        return r.read().decode('utf-8', 'replace')


def get_json(url):
    return json.loads(get(url))


def text(s):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', s))).strip()


# ---------- Sidearm (Nuxt) schedule pages: data is in <script id="__NUXT_DATA__"> as a flattened array ----------
def nuxt_payload(page):
    m = re.search(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    a = json.loads(m.group(1))
    memo = {}

    def r(i):
        if i in memo:
            return memo[i]
        v = a[i]
        if isinstance(v, list):
            if v and isinstance(v[0], str) and v[0] in ('Reactive', 'ShallowReactive', 'Ref', 'ShallowRef', 'EmptyRef', 'EmptyShallowRef'):
                out = r(v[1]) if len(v) > 1 else None
                memo[i] = out
                return out
            if v and v[0] == 'Set':
                return [r(x) for x in v[1:]]
            if v and v[0] == 'Date':
                return v[1]
            out = []
            memo[i] = out
            out.extend(r(x) if isinstance(x, int) else x for x in v)
            return out
        if isinstance(v, dict):
            out = {}
            memo[i] = out
            for k, x in v.items():
                out[k] = r(x) if isinstance(x, int) and x >= 0 else (None if x == -1 else x)
            return out
        return v
    return r(0)


def sidearm_games(url):
    """All games on a Sidearm Nuxt schedule page, plus the schedule's own conference abbreviation."""
    sch = nuxt_payload(get(url))['pinia']['schedule']['schedules']
    if not sch:
        raise RuntimeError('no schedule data in ' + url)
    s = next(iter(sch.values()))
    return s['games'], (s.get('conference') or {}).get('abbreviation')


# ---------- team names: every source spells them differently ----------
ALIASES = {
    'ga southern': 'georgia southern', 'uab': 'alabama birmingham', 'army west point': 'army', 'ohio st': 'ohio state',
    'murray': 'murray state', 'murray st': 'murray state', 'alaska': 'alaska fairbanks', 'uaf': 'alaska fairbanks',
    'jax state': 'jacksonville state', 'jacksonville st': 'jacksonville state', 'morehead': 'morehead state',
    'morehead st': 'morehead state', 'the citadel': 'citadel', 'john jay college': 'john jay', 'vmi (mixed)': 'vmi',
    'mississippi': 'ole miss', 'massachusetts institute of technology': 'mit', 'schreiner': 'schreiner university',
    'usafa': 'air force', 'usna': 'navy', 'charleston': 'college of charleston', 'st john s': 'st johns',
}


def norm(name):
    n = re.sub(r'^\s*(#\s*\d+|no\.\s*\d+)\s+', '', name or '', flags=re.I).lower().strip()   # "#13 Army", "No. 4 Nebraska"
    n = re.sub(r'[.’\'-]', ' ', n)
    n = re.sub(r'\s+', ' ', n).strip()
    return ALIASES.get(n, n)


# ---------- ESPN: abbreviations (WVU, UK, NEB, COFC) and baseball conferences ----------
def espn_teams(path='basketball/mens-college-basketball'):
    d = get_json(f'https://site.api.espn.com/apis/site/v2/sports/{path}/teams?limit=1000')
    out = {}
    for t in (x['team'] for x in d['sports'][0]['leagues'][0]['teams']):
        out[norm(t['location'])] = t
    return out


try:
    ESPN = espn_teams()
except Exception as e:  # abbreviations then fall back to school names
    log('ESPN teams failed:', e)
    ESPN = {}


# schools ESPN doesn't list (not Division I in basketball), as rifle results abbreviate them
SHORT = {'alaska fairbanks': 'UAF', 'mount aloysius': 'MAC', 'massachusetts institute of technology': 'MIT', 'mit': 'MIT',
         'john jay': 'JJAY', 'norwich': 'NOR', 'north georgia': 'UNG', 'schreiner university': 'SCH'}


def abbr(name):
    t = ESPN.get(norm(name))
    if t:
        return t['abbreviation'].upper()
    return SHORT.get(norm(name)) or re.sub(r'^\s*(#\s*\d+|no\.\s*\d+)\s+', '', name).strip()


# ---------- small formatting helpers ----------
AP_STATES = {'Ala.': 'AL', 'Alaska': 'AK', 'Ariz.': 'AZ', 'Ark.': 'AR', 'Calif.': 'CA', 'Colo.': 'CO', 'Conn.': 'CT', 'Del.': 'DE',
             'Fla.': 'FL', 'Ga.': 'GA', 'Hawaii': 'HI', 'Idaho': 'ID', 'Ill.': 'IL', 'Ind.': 'IN', 'Iowa': 'IA', 'Kan.': 'KS',
             'Ky.': 'KY', 'La.': 'LA', 'Maine': 'ME', 'Md.': 'MD', 'Mass.': 'MA', 'Mich.': 'MI', 'Minn.': 'MN', 'Miss.': 'MS',
             'Mo.': 'MO', 'Mont.': 'MT', 'Neb.': 'NE', 'Nev.': 'NV', 'N.H.': 'NH', 'N.J.': 'NJ', 'N.M.': 'NM', 'N.Y.': 'NY',
             'N.C.': 'NC', 'N.D.': 'ND', 'Ohio': 'OH', 'Okla.': 'OK', 'Ore.': 'OR', 'Pa.': 'PA', 'R.I.': 'RI', 'S.C.': 'SC',
             'S.D.': 'SD', 'Tenn.': 'TN', 'Texas': 'TX', 'Utah': 'UT', 'Vt.': 'VT', 'Va.': 'VA', 'Wash.': 'WA', 'W.Va.': 'WV',
             'Wis.': 'WI', 'Wyo.': 'WY', 'D.C.': 'DC'}


def place(loc):
    """'Morgantown, W.Va.' -> 'Morgantown, WV'"""
    if not loc or ',' not in loc:
        return loc or None
    city, st = loc.rsplit(',', 1)
    st = st.strip()
    return city.strip() + ', ' + AP_STATES.get(st, AP_STATES.get(st.replace(' ', ''), st))


def when(g):
    """Sidearm dates are local Eastern; returns (UTC ISO string, time TBA?)"""
    local = datetime.fromisoformat(g['date'])
    tba = bool(g.get('tbd')) or not (g.get('time') or '').strip() or (g.get('time') or '').strip().upper() == 'TBA'
    if tba:
        local = local.replace(hour=12, minute=0)   # noon ET keeps the date right in every US time zone
    return local.replace(tzinfo=ET).astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), tba


def rec_str(w, l, t=0):
    return f'{w}-{l}' + (f'-{t}' if t else '')


def ordinal(n):
    return f'{n}' + ('th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th'))


def tba_or(v):
    return v if v and v.strip() else None


# ======================================== RIFLE ========================================
def rifle_results():
    """Every match on ncaarifle.org this season -> overall W-L-T per team (head-to-head inside each match)."""
    page = get('http://ncaarifle.org/Home/TeamResults', {
        'Filtered': 'True', 'SelectedTeamId': '0', 'SelectedSort': 'date',
        'DateFilterStart': SEASON_START.strftime('%m/%d/%Y'), 'DateFilterEnd': TODAY.strftime('%m/%d/%Y')})
    matches = defaultdict(list)
    for row in re.findall(r'<tr.*?</tr>', page, re.S):
        m = re.search(r'MatchId=(\d+)', row)
        if not m:
            continue
        c = [text(x) for x in re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)]
        try:
            agg = int(c[5])
        except (IndexError, ValueError):
            continue
        if agg > 0:   # the site keeps some empty duplicate matches with 0 scores
            matches[m.group(1)].append((norm(c[0]), c[2], agg))
    if len(matches) < 3:
        raise RuntimeError('ncaarifle.org returned almost no matches')
    rec = defaultdict(lambda: [0, 0, 0])
    for teams in matches.values():
        for a in teams:
            for b in teams:
                if a is not b:
                    rec[a[0]][0 if a[2] > b[2] else 1 if a[2] < b[2] else 2] += 1
    return rec, matches


def crca_ranks():
    page = get('https://www.ncaa.com/rankings/rifle/nc/crca')
    t = re.search(r'<table.*?</table>', page, re.S)
    ranks = {}
    for row in re.findall(r'<tr.*?</tr>', t.group(0), re.S):
        c = [text(x) for x in re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)]
        if len(c) >= 2 and c[0].isdigit():
            ranks[norm(c[1])] = int(c[0])
    if len(ranks) < 10:
        raise RuntimeError('CRCA table not found')
    return ranks


# GARC members and where each publishes its schedule with conference matches marked.
# Kentucky's site doesn't mark them, but every GARC match has at least one of these five in it.
GARC_SIDEARM = {'army': 'https://goarmywestpoint.com/sports/rifle/schedule',
                'memphis': 'https://gotigersgo.com/sports/rifle/schedule',
                'akron': 'https://gozips.com/sports/rifle/schedule'}
GARC_LEGACY = {'navy': 'https://navysports.com/sports/rifle/schedule'}
GARC = {'west virginia', 'navy', 'army', 'kentucky', 'memphis', 'akron'}


def legacy_conf_games(url):
    """Older Sidearm pages: (date, opponent) for each game marked with the conference badge."""
    page = get(url)
    out = []
    for li in re.split(r'<li[^>]*class="sidearm-schedule-game ', page)[1:]:
        conf = re.search(r'sidearm-schedule-game-conference-conference[^>]*>(.*?)</div>', li, re.S)
        if not conf or not text(conf.group(1)):
            continue
        d = re.search(r'sidearm-schedule-game-opponent-date[^>]*>\s*<span>([A-Z][a-z]{2}) (\d+)', li)
        o = re.search(r'sidearm-schedule-game-opponent-name[^>]*>(.*?)</div>', li, re.S)
        if d and o:
            mo = datetime.strptime(d.group(1), '%b').month
            yr = SEASON_START.year if mo >= 7 else SEASON_START.year + 1
            out.append((date(yr, mo, int(d.group(2))), norm(text(o.group(1)))))
    return out


def garc_standings(wvu_games, matches):
    """Conference W-L for every GARC team, from conference-marked games (scores from ncaarifle.org)."""
    pairs = set()

    def add(team, d, opp):
        if opp in GARC and team != opp:
            pairs.add((d, frozenset((team, opp))))
    for g in wvu_games:
        if g['conference']:
            add('west virginia', datetime.fromisoformat(g['date']).date(), norm((g['opponent'] or {}).get('title')))
    for team, url in GARC_SIDEARM.items():
        try:
            for g in sidearm_games(url)[0]:
                if g['conference']:
                    add(team, datetime.fromisoformat(g['date']).date(), norm((g['opponent'] or {}).get('title')))
        except Exception as e:
            log('GARC schedule failed:', team, e)
    for team, url in GARC_LEGACY.items():
        try:
            for d, opp in legacy_conf_games(url):
                add(team, d, opp)
        except Exception as e:
            log('GARC schedule failed:', team, e)
    # scores: the ncaarifle.org match on that date with both teams in it
    by_day = defaultdict(list)
    for teams in matches.values():
        day = teams[0][1]   # "Oct. 03"
        mo = datetime.strptime(day.split('.')[0][:3], '%b').month
        yr = SEASON_START.year if mo >= 7 else SEASON_START.year + 1
        by_day[date(yr, mo, int(re.sub(r'\D', '', day)))].append({t[0]: t[2] for t in teams})
    conf = {t: [0, 0, 0] for t in GARC}
    for d, pair in pairs:
        a, b = sorted(pair)
        m = next((x for x in by_day.get(d, []) if a in x and b in x), None)
        if not m:
            continue   # not played yet (or not scored yet)
        if m[a] == m[b]:
            conf[a][2] += 1
            conf[b][2] += 1
        else:
            win, lose = (a, b) if m[a] > m[b] else (b, a)
            conf[win][0] += 1
            conf[lose][1] += 1
    return conf


def conf_rank(conf, team):
    pct = lambda r: (r[0] + r[2] / 2) / sum(r) if sum(r) else 0.5   # no conference matches yet counts as even
    return ordinal(1 + sum(pct(r) > pct(conf[team]) for r in conf.values())) + ' of ' + str(len(conf))


def rifle():
    games, conf_abbr = sidearm_games('https://wvusports.com/sports/rifle/schedule')
    rec, matches = rifle_results()
    try:
        ranks = crca_ranks()
    except Exception as e:
        log('CRCA ranks failed:', e)
        ranks = {}
    conf = garc_standings(games, matches)
    known = set(rec) | set(ranks) | GARC   # real teams, as opposed to "GARC Championship (SB)" entries

    def team_rec(t, with_conf):
        r = rec.get(t, [0, 0, 0])
        s = rec_str(*r)
        return s + (f' ({rec_str(*conf[t])})' if with_conf and t in conf else '')

    def field_line(name):
        t = norm(name)
        rk = ranks.get(t)
        return ' '.join(x for x in ['#' + str(rk) if rk else None, abbr(name.strip()), team_rec(t, True)] if x)

    # group: games of one tournament (or several opponents on the same day and site) are one event
    events = []
    for g in sorted(games, key=lambda g: g['date']):
        d = datetime.fromisoformat(g['date']).date()
        key = (g.get('tournament') or {}).get('title') or (g['date'][:10] + '|' + (g['location'] or ''))
        last = events[-1] if events else None
        if last and last['key'] == key and (d - last['last']).days <= 3:
            last['games'].append(g)
            last['last'] = max(last['last'], d)
        else:
            events.append({'key': key, 'games': [g], 'last': d})

    wv = {'rank': ranks.get('west virginia'), 'name': 'West Virginia', 'short': 'WVU', 'nick': 'Mountaineers',
          'logo': 'WVU_CARD_LOGO', 'record': team_rec('west virginia', True)}
    out = []
    for ev in events:
        first, gs = ev['games'][0], ev['games']
        start, tba = when(first)
        end = max(gs, key=lambda g: g.get('enddate') or g['date'])
        end_day = datetime.fromisoformat(end.get('enddate') or end['date']).date()
        through = None
        if end_day > datetime.fromisoformat(first['date']).date():
            t = datetime.fromisoformat(first['date']).time() if not tba else datetime.min.time().replace(hour=12)
            through = datetime.combine(end_day, t, ET).astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        if datetime.fromisoformat(through or start.replace('Z', '+00:00')) < NOW - timedelta(hours=4):
            continue   # already over
        teams = []
        for g in gs:
            nm = ((g['opponent'] or {}).get('title') or '').strip()
            if norm(nm) in known and nm not in teams:
                teams.append(nm)
        venue = (first.get('facility') or {}).get('title')
        tv = tba_or((first.get('media') or {}).get('tv'))
        rows = [['Venue', venue or 'TBA']] + ([['Broadcast', tv]] if tv else [])
        tourney = (first.get('tournament') or {}).get('title')
        game = {'date': start, 'where': place(first['location']), 'us': wv, 'rows': rows}
        if through:
            game['through'] = through
        if tba:
            game['timeTBA'] = True
        if tourney or len(teams) != 1:
            # a meet with a field: second column is headed Opponents, one team per row
            home = first.get('location_indicator') == 'H'
            game.update({'prefix': 'at' if home or tourney else first['at_vs'],
                         'them': {'name': tourney or ' / '.join(t.strip() for t in teams) or ((first['opponent'] or {}).get('title') or 'TBA').strip(),
                                  'logo': 'wvu-logo.png' if home else ((first['opponent'] or {}).get('image') or {}).get('fullpath'),
                                  'noOutline': home},
                         'noOpp': True, 'pairs': [['Conference', conf_abbr, None], ['Conf. rank', conf_rank(conf, 'west virginia'), None]]})
            if teams:
                game.update({'oppHead': 'Opponents', 'field': [field_line(t) for t in teams]})
        else:
            o = first['opponent']
            t = norm(o['title'])
            game.update({'prefix': first['at_vs'],
                         'them': {'rank': ranks.get(t), 'name': o['title'].strip(), 'short': abbr(o['title'].strip()),
                                  'nick': o.get('mascot') or None, 'logo': (o.get('image') or {}).get('fullpath'),
                                  'record': team_rec(t, True)},
                         'pairs': [['Conference', conf_abbr, conf_abbr if t in GARC else None],
                                   ['Conf. rank', conf_rank(conf, 'west virginia'), conf_rank(conf, t) if t in GARC else None]]})
        out.append(game)
    return out


# ======================================== BASEBALL ========================================
BASEBALL_TEAMS = None


def espn_conference(name, season):
    global BASEBALL_TEAMS
    if BASEBALL_TEAMS is None:   # ESPN numbers teams separately per sport
        BASEBALL_TEAMS = espn_teams('baseball/college-baseball')
    t = BASEBALL_TEAMS.get(norm(name))
    if not t:
        return None
    for yr in (season, season - 1):   # next season's conference isn't posted until close to the season
        try:
            team = get_json(f'https://sports.core.api.espn.com/v2/sports/baseball/leagues/college-baseball/seasons/{yr}/teams/{t["id"]}')
            ref = (team.get('groups') or {}).get('$ref')
            if ref:
                g = get_json(ref.replace('http:', 'https:'))
                name = g.get('shortName') or g.get('name')
                return None if 'Division' in (name or '') else name   # a team listed under no conference
        except Exception:
            continue
    return None


def baseball():
    games, conf_abbr = sidearm_games('https://wvusports.com/sports/baseball/schedule')
    played = [g for g in games if (g.get('result') or {}).get('status') in ('W', 'L', 'T')]
    w = sum(g['result']['status'] == 'W' for g in played)
    l = sum(g['result']['status'] == 'L' for g in played)
    cw = sum(g['result']['status'] == 'W' and g['conference'] for g in played)
    cl = sum(g['result']['status'] == 'L' and g['conference'] for g in played)
    season = datetime.fromisoformat(games[0]['date']).year if games else TODAY.year
    out = []
    for g in sorted(games, key=lambda g: g['date']):
        start, tba = when(g)
        if datetime.fromisoformat(start.replace('Z', '+00:00')) < NOW - timedelta(hours=4):
            continue
        o = g['opponent'] or {}
        nm = (o.get('title') or 'TBA').strip()
        tv = tba_or((g.get('media') or {}).get('tv'))
        game = {'date': start, 'where': place(g['location']), 'prefix': g['at_vs'],
                'us': {'name': 'West Virginia', 'short': 'WVU', 'nick': 'Mountaineers', 'logo': 'WVU_CARD_LOGO',
                       'record': f'{w}-{l} ({cw}-{cl})'},
                'them': {'name': nm, 'short': abbr(nm), 'nick': o.get('mascot') or None,
                         'logo': (o.get('image') or {}).get('fullpath')},
                'pairs': [['Conference', conf_abbr, espn_conference(nm, season)]],
                'rows': [['Venue', (g.get('facility') or {}).get('title') or 'TBA']] + ([['Broadcast', tv]] if tv else [])}
        if not played:
            game['them']['record'] = '0-0 (0-0)'   # opponents' records only matter before ESPN takes over the card
        if tba:
            game['timeTBA'] = True
        out.append(game)
        if len(out) == 5:
            break
    return out


# ======================================== SOCCER STANDINGS ========================================
def sidearm_standings(url):
    page = get(url)
    table = []
    for row in re.findall(r'<tr.*?</tr>', page, re.S):
        c = [text(x) for x in re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)]
        if not c or not c[0]:
            continue
        rec = next((x for x in c[1:] if re.fullmatch(r'\d+-\d+(-\d+)?', x)), None)
        if rec and c[0] not in (n for n, _ in table):
            table.append([c[0], rec if rec.count('-') == 2 else rec + '-0'])   # W-L-T, like the page counts them
    if len(table) < 4:
        raise RuntimeError('standings table not found: ' + url)
    return table


def soccer():
    out = {}
    try:
        out['usa.ncaa.m.1'] = sidearm_standings('https://sunbeltsports.org/standings.aspx?path=msoc')
    except Exception as e:
        log('Sun Belt standings failed:', e)
    try:
        # Big 12's standings page needs this season's id, which its soccer page links to
        sport = get('https://big12sports.com/sports/wsoc')
        sid = re.search(r'standings\.aspx\?standings=(\d+)&(?:amp;)?path=soc', sport).group(1)
        out['usa.ncaa.w.1'] = sidearm_standings(f'https://big12sports.com/standings.aspx?standings={sid}&path=soc')
    except Exception as e:
        log('Big 12 standings failed:', e)
    return out


def main():
    data = {}
    for key, fn in (('Rifle', rifle), ('Baseball', baseball)):
        try:
            data.setdefault('manual', {})[key] = fn()
        except Exception as e:
            log(key, 'failed:', repr(e))
    try:
        data['soccerStandings'] = soccer()
    except Exception as e:
        log('soccer failed:', e)
    path = sys.argv[1] if len(sys.argv) > 1 else 'wvu-data.json'
    old = None
    try:
        old = json.load(open(path, encoding='utf-8'))
    except Exception:
        pass
    # only rewrite (and bump "updated") when something actually changed, so the job doesn't commit every run
    if old and {k: v for k, v in old.items() if k != 'updated'} == data:
        log('no change')
        return
    data = {'updated': NOW.strftime('%Y-%m-%dT%H:%M:%SZ'), **data}
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
        f.write('\n')
    log('wrote', path)


if __name__ == '__main__':
    main()
