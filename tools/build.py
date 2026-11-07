"""One Austrian case-law repo from the fetched RIS decision texts, straight into git fast-import.

  python build.py <group> <fetched dir> <out repo dir> --today YYYY-MM-DD --retrieved YYYY-MM-DD

groups: verfassungsgerichtshof (Vfgh), oberster-gerichtshof (Justiz, OGH), justiz (Justiz, other courts),
verwaltungsgerichtshof (Vwgh), bundesverwaltungsgericht (Bvwg), landesverwaltungsgerichte (Lvwg). One commit per
decision text (Kind: court), dated the day decided (noon Vienna); a Lexifina documentation commit with README (credit:
"Datenquelle: RIS", CC BY 4.0), LICENSE, REPRODUCE.md, corpus.json, decision-index.tsv and the judgment overlay
judgments/austria/bund/acts/<file>.jsonl for the acts austria-law holds (B-VG, DSG), each row pinned to the provision's
own version."""
from __future__ import annotations

import argparse
import glob
import gzip
import importlib.util
import json
import re
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import regex

HERE = Path(__file__).resolve().parent
_r = importlib.util.spec_from_file_location('nl_reconcile', HERE.parent / 'nl-caselaw' / 'reconcile.py')
_rec = importlib.util.module_from_spec(_r)
_r.loader.exec_module(_rec)
fix_row, versions = _rec.fix_row, _rec.versions

LAW_CORPUS = 'austria/bund/acts'
VIE = timezone(timedelta(hours=1))
COMMITTER = b'Lexifina <corpus@lexifina.com>'
EPOCH_NOON = 11 * 3600
ISO = re.compile(r'^\d{4}-\d{2}-\d{2}$')
GROUPS = {'verfassungsgerichtshof': ('Vfgh', None, 'Verfassungsgerichtshof (VfGH)'),
          'oberster-gerichtshof': ('Justiz', 'OGH', 'Oberster Gerichtshof (OGH)'),
          'justiz': ('Justiz', '!OGH', 'ordinary courts other than the OGH (Justiz)'),
          'verwaltungsgerichtshof': ('Vwgh', None, 'Verwaltungsgerichtshof (VwGH)'),
          'bundesverwaltungsgericht': ('Bvwg', None, 'Bundesverwaltungsgericht (BVwG)'),
          'landesverwaltungsgerichte': ('Lvwg', None, 'Landesverwaltungsgerichte (LVwG)')}
LICENCE = 'Creative Commons Namensnennung 4.0 International (CC BY 4.0), https://creativecommons.org/licenses/by/4.0/deed.de'


def credit(retrieved: str) -> str:
    return f'Datenquelle: RIS - Rechtsinformationssystem des Bundes (www.ris.bka.gv.at), Bundeskanzleramt; {LICENCE}; abgerufen {retrieved}'


class Resolver:
    """Art 7 B-VG, Art. 140 Abs. 1 B-VG, § 1 DSG, §§ 4 und 5 DSG. Tried at each B-VG / DSG mention on the 80
    characters before it (bounded); only the acts austria-law holds."""
    ART = regex.compile(r'Art(?:ikel|\.)?\s*(\d+[a-z]?)(?:\s*Abs\.?\s*\d+[a-z]?)?(?:\s*(?:Z|lit\.?)\s*\w+)?\s*$')
    PAR = regex.compile(r'§§?\s*(\d+[a-z]?)(?:\s*Abs\.?\s*\d+[a-z]?)?(?:\s*(?:Z|lit\.?)\s*\w+)?\s*$')

    def __init__(self, law_files: set):
        self.files = law_files
        self.dsg = {}
        for p in law_files:
            m = re.match(r'dsg/art-\d+/par-0*(\d+[a-z]?)\.md$', p)
            if m:
                self.dsg[m.group(1)] = p
        self.timeouts = 0

    def find(self, text: str) -> list[tuple[str, str]]:
        out = []
        for m in re.finditer(r'(?<![\w-])(B-VG|DSG)(?![\w-])', text or ''):
            win = text[max(0, m.start() - 80):m.start()]
            try:
                hit = (self.ART if m.group(1) == 'B-VG' else self.PAR).search(win, timeout=0.05)
            except TimeoutError:
                self.timeouts += 1
                continue
            if not hit:
                continue
            n = hit.group(1)
            if m.group(1) == 'B-VG':
                num = re.match(r'(\d+)([a-z]?)', n)
                p = f'b-vg/art-{int(num.group(1)):03d}{num.group(2)}.md'
                p = p if p in self.files else None
            else:
                p = self.dsg.get(n)
            if p:
                out.append((p, re.sub(r'\s+', ' ', win[hit.start():] + m.group(1)).strip()[:120]))
        return out


def render(r: dict, day: str, group: str, res: Resolver, ver: dict, retrieved: str, extra: tuple = ()):
    court = r.get('court') or GROUPS[group][2]
    # RIS Geschaeftszahlen sometimes carry a leading '#' or brackets ("#1693/96", "(G123/11-6)"); RIS gives
    # Entscheidungsart "Undefined" where it has none (about 6,200 VfGH texts): left out rather than written as a type.
    dockets = [d.strip().lstrip('#').strip('()').strip() for d in r.get('docket') or [] if d and d.strip().lstrip('#').strip('()').strip()]
    if (r.get('art') or '').strip() in ('Undefined', ''):
        r = {**r, 'art': None}
    docket = dockets[0] if dockets else r['id']
    slug = re.sub(r'[^A-Za-z0-9._-]+', '-', r['id']).strip('-')
    path = f'decisions/{re.sub(r"[^a-z0-9]+", "-", court.lower()).strip("-")[:40]}/{day[:4]}/{slug}.md'
    cites = res.find(r['text'])
    fm = {'id': r['id'], 'ecli': r.get('ecli'), 'court': court, 'docket': ', '.join(dockets) or None, 'decided': day,
          'decision_type': r.get('art'), 'norms': r.get('normen') or None, 'keywords': r.get('schlagworte'), 'source_url': r.get('url'),
          'source': 'RIS - Rechtsinformationssystem des Bundes, Bundeskanzleramt', 'licence': 'CC BY 4.0', 'source_retrieved': retrieved}
    lines = ['---'] + [f'{k}: {json.dumps(v, ensure_ascii=False)}' for k, v in fm.items() if v not in (None, '', [])]
    uniq = sorted({p for p, _ in cites})
    if uniq:
        lines.append('cites_law: ' + json.dumps([f'{LAW_CORPUS}/{p}' for p in uniq], ensure_ascii=False))
    title = f'{court} {docket}'
    lines += ['---', '', f'# {title}', '', r['text'].strip(), '']
    body = '\n'.join(lines).encode('utf-8')
    author = title.replace('<', '').replace('>', '')[:200]
    email = re.sub(r'[^a-z0-9]+', '-', r['id'].lower()).strip('-')[:60] + '@caselaw.invalid'
    kind = 'correction' if any(t.startswith('Corrected:') for t in extra) else 'court'
    tr = [f'Kind: {kind}', f'In-force: {day}', f'Decided: {day}', f'Court: {court}', f'Docket: {docket}', f"Id: {r['id']}"]
    if r.get('ecli'):
        tr.append(f"ECLI: {r['ecli']}")
    if r.get('url'):
        tr.append(f"Source: {r['url']}")
    seen = set()
    for p, how in cites:
        if p not in seen and len(seen) < 60:
            seen.add(p)
            tr.append(f'Cites: {LAW_CORPUS}/{p} ({how})')
    tr += [t for t in extra if t and not t.rstrip().endswith(':')]
    msg = (f'{title}: {r.get("art") or "Entscheidung"}'[:200] + '\n\n' + '\n'.join(tr) + '\n').encode('utf-8')
    counts = {}
    for p, _ in cites:
        counts[p] = counts.get(p, 0) + 1
    rows, done = [], set()
    for p, how in cites:
        if p in done:
            continue
        done.add(p)
        row = {'decided': day, 'name': title, 'court': court, 'citation': r.get('ecli') or docket, 'url': r.get('url') or '',
               'how': how, 'mentions': counts[p], 'decision': path}
        rows.append((p, fix_row(row, p, ver)))
    refs = sorted({x for x in (*dockets, r.get('ecli'), r['id']) if x})
    return path, body, author, email, msg, rows, refs


def load(fetched: Path, group: str) -> list[dict]:
    app, court_rule, _ = GROUPS[group]
    recs = {}
    for f in sorted(glob.glob(str(fetched / f'{app}.*.jsonl.gz'))):
        try:
            with gzip.open(f, 'rt', encoding='utf-8') as fh:
                for line in fh:
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    c = (r.get('court') or '')
                    if court_rule == 'OGH' and c != 'OGH':
                        continue
                    if court_rule == '!OGH' and c == 'OGH':
                        continue
                    if (r.get('text') or '').strip():
                        recs[r['id']] = r
        except (EOFError, OSError):
            pass
    return list(recs.values())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('group'), ap.add_argument('fetched'), ap.add_argument('out')
    ap.add_argument('--today', required=True), ap.add_argument('--retrieved', required=True)
    ap.add_argument('--law-repo', default=str(Path.home() / 'Documents' / 'caselaw-law-mirrors' / 'austria-law.git'))
    ap.add_argument('--chunk-bytes', type=int, default=2_000_000_000)
    a = ap.parse_args()
    out = Path(a.out)
    law = Path(a.law_repo)
    files = set(subprocess.run(['git', '-C', str(law), 'log', 'main', '--format=', '--name-only'], capture_output=True, text=True,
                               encoding='utf-8').stdout.split()) | set(subprocess.run(['git', '-C', str(law), 'ls-tree', '-r', '--name-only', 'main'],
                                                                                      capture_output=True, text=True, encoding='utf-8').stdout.split())
    res = Resolver(files)
    ver = versions(law, ref='main')
    recs = load(Path(a.fetched), a.group)
    recs.sort(key=lambda r: ((r.get('decided') or '9999')[:10], r['id']))
    print(f'{a.group}: {len(recs)} decisions', file=sys.stderr, flush=True)
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f'{out} is not empty')
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run(['git', 'init', '-q', '-b', 'main', str(out)], check=True)
    fi = subprocess.Popen(['git', '-C', str(out), 'fast-import', '--quiet', '--done', f'--export-marks={out / ".git" / "marks"}'],
                          stdin=subprocess.PIPE)
    w = fi.stdin.write
    overlay, dindex, undated, checkpoints = {}, [], [], []
    mark = raw = clamp = 0
    first = None
    for r in recs:
        day = (r.get('decided') or '')[:10]
        if not ISO.match(day) or day > a.today:
            undated.append(r)
            continue
        first = first or day
        path, body, author, email, msg, rows, refs = render(r, day, a.group, res, ver, a.retrieved)
        dindex += [x + '\t' + path for x in refs]
        raw += len(body)
        d0 = date.fromisoformat(day)
        if d0.year < 1970:
            clamp += 1
            ts = EPOCH_NOON + clamp - 3600
        else:
            ts = int(datetime(d0.year, d0.month, d0.day, 12, tzinfo=VIE).timestamp())
        mark += 1
        w(b'commit refs/heads/main\nmark :%d\n' % mark)
        w(b'author %s <%s> %d +0100\ncommitter %s %d +0100\n' % (author.encode('utf-8'), email.encode(), ts, COMMITTER, ts))
        w(b'data %d\n%s\n' % (len(msg), msg))
        w(b'M 100644 inline %s\ndata %d\n%s\n' % (path.encode('utf-8'), len(body), body))
        if raw >= (len(checkpoints) + 1) * a.chunk_bytes:
            checkpoints.append(mark)
        for p, row in rows:
            overlay.setdefault(p, []).append(row)
    cr = credit(a.retrieved)
    where = GROUPS[a.group][2]
    gl_path = f'austria/{a.group}/caselaw'
    docs = {}
    for p, rows in overlay.items():
        rows.sort(key=lambda x: (x['decided'], x['citation']))
        docs[f'judgments/{LAW_CORPUS}/{p[:-3]}.jsonl'] = ''.join(json.dumps(x, ensure_ascii=False, sort_keys=True) + '\n' for x in rows)
    for r in undated:
        docs[f"decisions/undated/{r['id']}.md"] = f"---\nid: {json.dumps(r['id'])}\nsource_url: {json.dumps(r.get('url'))}\n---\n\n# {r['id']}\n\n{r['text']}\n"
    n_refs = sum(len(v) for v in overlay.values())
    docs['decision-index.tsv'] = 'reference' + chr(9) + 'path' + chr(10) + ''.join(x + chr(10) for x in sorted(set(dindex)))
    docs['unresolved.csv'] = 'note\naustria-law holds the B-VG and the DSG only; citations of other acts are not linked yet.\n'
    docs['corpus.json'] = json.dumps({
        'corpus': f'at/{a.group}/caselaw', 'path': gl_path, 'name': f'Case law: Austria, {where}', 'label': f'Austria case law ({where})',
        'language': 'de', 'kind': 'judgments', 'history': 'in-force',
        'coverage': f'{mark} decision texts (Entscheidungstexte) from RIS, one file and one commit each, dated the day decided; {n_refs} citations of the B-VG and the DSG linked to austria-law.',
        'history_from': first, 'record_to': a.retrieved, 'checked_on': a.today,
        'record_note': 'Court decisions, not laws: every commit is Kind: court. Only full decision texts are taken, not the Rechtssätze (headnote summaries).',
        'in_force_trailer': 'In-force', 'correction_trailer': 'Corrected', 'dates': {'commit': 'decided', 'time': '12:00 Europe/Vienna'},
        'source': {'publisher': 'Bundeskanzleramt, RIS - Rechtsinformationssystem des Bundes', 'dataset': f'RIS OGD Judikatur ({GROUPS[a.group][0]})',
                   'url': 'https://data.bka.gv.at/ris/api/v2.6/Judikatur', 'updated': a.retrieved},
        'licence': 'Creative Commons Namensnennung 4.0 International (CC BY 4.0)', 'credit': cr,
        'overlay': {'path': f'judgments/{LAW_CORPUS}/<file>.jsonl', 'law_corpus': LAW_CORPUS},
    }, ensure_ascii=False, indent=2) + '\n'
    docs['README.md'] = f"""# Austrian case law: {where}

Part of [lexifina/global-law](https://github.com/lexifina/global-law), one git history for every country's law: this is the case law of the {where}, Austria, which global-law reads at `{gl_path}/`.

Every decision text (Entscheidungstext) of the {where} published in RIS, as a git history: one file per decision, one
commit per decision, dated the day it was decided. {mark:,} decisions from {first} to {a.retrieved}.

## Datenquelle und Lizenz / Source and licence

{cr}.

The decisions come from the Rechtsinformationssystem des Bundes (RIS) of the Bundeskanzleramt through its Open Government
Data interface, under the Creative Commons Namensnennung 4.0 International licence. Changes made here: each decision text
converted from HTML to Markdown and dated by its decision day; links to the law added in `judgments/`. The Rechtssätze
(headnote summaries) are not included.

## Questions as git commands

| Question | Command |
|---|---|
| A decision | `cat decisions/<court>/<year>/<id>.md` |
| By Geschäftszahl or ECLI | `grep 'G 123/2024' decision-index.tsv` |
| Decisions given in a window | `git log --since=2024-03-01 --until=2024-03-31 --format='%ad %an' --date=short` |
| Decisions applying Art. 7 B-VG | `cat judgments/austria/bund/acts/b-vg/art-007.jsonl` |
| Who wrote a line? | `git blame decisions/...`: each line is credited to the decision (court and Geschäftszahl) |

## Dates and authors
The commit date is the day the decision was given, at noon Vienna (`In-force:` and `Decided:`). The author is the court
and the Geschäftszahl, at an address that cannot exist. These are court rulings, not laws: every commit is `Kind: court`.
"""
    docs['LICENSE'] = f'Entscheidungen / Decisions: {LICENCE}\n{cr}.\n\nThe arrangement of this repository (layout, commits, overlay, tools): CC0 1.0.\n'
    docs['REPRODUCE.md'] = f"""# Reproducing this repository

Input: the RIS OGD API v2.6 (Judikatur, Applikation {GROUPS[a.group][0]}, decision texts only), fetched up to {a.retrieved}
by `tools/fetch.py` (at most 8 requests a second); austria-law `main` for the overlay.

```
python tools/fetch.py <fetched> --apps {GROUPS[a.group][0]}
python tools/build.py {a.group} <fetched> <empty folder> --today {a.today} --retrieved {a.retrieved}
```
"""
    for f in ('fetch.py', 'build.py'):
        docs[f'tools/{f}'] = (HERE / f).read_text(encoding='utf-8')
    ts = int(datetime.fromisoformat(a.today + 'T12:00:00+01:00').timestamp())
    msg = f'Documentation: README, LICENSE (CC BY 4.0, Datenquelle: RIS), REPRODUCE, corpus.json and the judgment overlay ({n_refs} citations)\n'.encode()
    w(b'commit refs/heads/main\nauthor Lexifina <corpus@lexifina.com> %d +0100\ncommitter %s %d +0100\n' % (ts, COMMITTER, ts))
    w(b'data %d\n%s\n' % (len(msg), msg))
    for p in sorted(docs):
        b = docs[p].encode('utf-8')
        w(b'M 100644 inline %s\ndata %d\n%s\n' % (p.encode('utf-8'), len(b), b))
    w(b'done\n')
    fi.stdin.close()
    if fi.wait():
        raise SystemExit('fast-import failed')
    print(json.dumps({'group': a.group, 'decisions': mark, 'undated': len(undated), 'overlay_rows': n_refs,
                      'regex_timeouts': res.timeouts, 'raw_gb': round(raw / 1e9, 3)}), flush=True)


if __name__ == '__main__':
    main()
