"""Fetch Austrian court decision texts (Entscheidungstexte) from the RIS OGD API v2.6 (Judikatur), politely (at most
--rate requests a second), one court database at a time; keep one gzip JSONL record per decision text (metadata from
the search hit + the text extracted from the document's HTML). Rechtssätze are not taken (they are summaries, not
decisions). Resumable: ids already saved are skipped; the search is re-paged from the last page reached.

  python fetch.py <out dir> --apps Vfgh,Justiz,Vwgh,Bvwg,Lvwg [--rate 8] [--workers 24]

Datenquelle: RIS - https://www.ris.bka.gv.at (Creative Commons Namensnennung 4.0)."""
from __future__ import annotations

import argparse
import gzip
import html as H
import json
import re
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

UA = 'lexifina-gitlaw (case law archive)'
API = 'https://data.bka.gv.at/ris/api/v2.6/Judikatur'
lock = threading.Lock()
nxt = [0.0]
TAG = re.compile(r'<[^>]+>')
BR = re.compile(r'<br\s*/?>|</p>|</div>|</h\d>|</li>|</tr>|</table>', re.I)


LOOKAHEAD = 2  # search pages requested ahead of the one being downloaded


def get(url: str, rate: float, accept: str = '*/*') -> bytes:
    with lock:
        wait = nxt[0] - time.time()
        nxt[0] = max(nxt[0], time.time()) + 1 / rate
    if wait > 0:
        time.sleep(wait)
    err = None
    for i in range(6):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': accept}), timeout=120).read()
        except Exception as e:
            err = e
            time.sleep(4 * (i + 1))
    raise err


def text_of(html: str) -> str:
    body = re.search(r'<body[^>]*>(.*)</body>', html, re.S | re.I)
    t = H.unescape(TAG.sub('', BR.sub('\n', body.group(1) if body else html)))
    lines = [l.strip() for l in t.replace('\r', '').split('\n')]
    out, blank = [], False
    for l in lines:
        if not l:
            blank = bool(out)
            continue
        if blank:
            out.append('')
        out.append(l)
        blank = False
    return '\n'.join(out)


def items(v):
    if v is None:
        return []
    if isinstance(v, dict) and 'item' in v:
        v = v['item']
    return v if isinstance(v, list) else [v]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('out'), ap.add_argument('--apps', default='Vfgh'), ap.add_argument('--rate', type=float, default=8)
    ap.add_argument('--workers', type=int, default=24), ap.add_argument('--max-pages', type=int, default=0)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for app in a.apps.split(','):
        done = set()
        for f in out.glob(f'{app}.*.jsonl.gz'):
            try:  # a shard cut short by a killed run still yields its complete lines
                with gzip.open(f, 'rt', encoding='utf-8') as fh:
                    for line in fh:
                        try:
                            done.add(json.loads(line)['id'])
                        except ValueError:
                            pass
            except (EOFError, OSError):
                pass
        state = out / f'{app}.page'
        page = int(state.read_text()) if state.exists() else 1
        w = gzip.open(out / f'{app}.{int(time.time())}.jsonl.gz', 'wt', encoding='utf-8', compresslevel=5)
        wl = threading.Lock()
        n = [0]

        def one(hit):
            try:
                one_(hit)
            except Exception as e:  # one odd record must not end the run; it is retried on the next run
                print(f'{app}: skipped a record: {e!r}', flush=True)

        def one_(hit):
            md = hit['Data']['Metadaten']
            j = md['Judikatur']
            did = md['Technisch']['ID']
            urls = []  # ContentReference, Urls and ContentUrl can each be one object or a list of them
            for cr in items((hit['Data'].get('Dokumentliste') or {}).get('ContentReference')):
                if isinstance(cr, dict):
                    for us in items(cr.get('Urls')):
                        if isinstance(us, dict):
                            urls += [u for u in items(us.get('ContentUrl')) if isinstance(u, dict)]
            h = next((u['Url'] for u in urls if u.get('DataType') == 'Html' and u.get('Url')), None)
            if not h:
                return
            txt = text_of(get(h, a.rate).decode('utf-8', 'replace'))
            court_part = next((v for k, v in j.items() if isinstance(v, dict) and k not in ('Geschaeftszahl', 'Normen')), {})
            if isinstance(court_part, list):
                court_part = next((x for x in court_part if isinstance(x, dict)), {})
            rec = {'id': did, 'app': app, 'organ': md['Technisch'].get('Organ'), 'court': court_part.get('Gericht') or md['Technisch'].get('Organ'),
                   'docket': items(j.get('Geschaeftszahl')), 'decided': j.get('Entscheidungsdatum'), 'ecli': j.get('EuropeanCaseLawIdentifier'),
                   'normen': items(j.get('Normen')), 'art': court_part.get('Entscheidungsart'), 'schlagworte': j.get('Schlagworte'),
                   'published': md['Allgemein'].get('Veroeffentlicht'), 'changed': md['Allgemein'].get('Geaendert'),
                   'url': md['Allgemein'].get('DokumentUrl'), 'html': h, 'text': txt}
            with wl:
                w.write(json.dumps(rec, ensure_ascii=False) + '\n')
                n[0] += 1

        total = [None]  # the database's hit count: RIS answers a page past the end with HTTP 500, not an empty page

        def search(p):
            """One search page's hits. A malformed or error response is retried (it once ended whole runs), then raised."""
            if total[0] is not None and (p - 1) * 100 >= total[0]:
                return []
            q = urllib.parse.urlencode({'Applikation': app, 'Dokumenttyp.SucheInRechtssaetzen': 'false',
                                        'Dokumenttyp.SucheInEntscheidungstexten': 'true', 'DokumenteProSeite': 'OneHundred',
                                        'Seitennummer': p, 'Sortierung.SortDirection': 'Ascending'})
            for i in range(6):
                try:
                    d = json.loads(get(f'{API}?{q}', a.rate, 'application/json'))
                    r = d['OgdSearchResult'].get('OgdDocumentResults') or {}
                    total[0] = int(r['Hits']['#text'])
                    hits = [x for x in items(r.get('OgdDocumentReference')) if isinstance(x, dict)]
                    for x in hits:
                        x['Data']['Metadaten']['Judikatur'].get('Dokumenttyp'), x['Data']['Metadaten']['Technisch']['ID']
                    return hits
                except Exception as e:
                    print(f'{app}: page {p} unreadable ({e!r}), retry {i + 1}', flush=True)
                    if i == 5:
                        raise
                    time.sleep(30 * (i + 1))

        # Searches run ahead of the document downloads (a BVwG search page takes ~20 s on the server); pages are
        # still written and recorded in order.
        ahead = ThreadPoolExecutor(1 + LOOKAHEAD)
        if page > 1:
            try:  # learn the hit count first, so no request goes past the end
                search(1)
            except Exception:
                pass
        pending = {p: ahead.submit(search, p) for p in range(page, page + 1 + LOOKAHEAD)}
        with ThreadPoolExecutor(a.workers) as ex:
            while True:
                hits = pending.pop(page).result()
                nxt_p = page + 1 + LOOKAHEAD
                if hits:
                    pending[nxt_p] = ahead.submit(search, nxt_p)
                if not hits:
                    for f in pending.values():
                        f.cancel()
                    break
                todo = [x for x in hits if x['Data']['Metadaten']['Judikatur'].get('Dokumenttyp') == 'Text'
                        and x['Data']['Metadaten']['Technisch']['ID'] not in done]
                list(ex.map(one, todo))
                w.flush()
                page += 1
                state.write_text(str(page))
                if page % 20 == 0:
                    print(f'{app}: page {page}, {n[0]} texts saved this run', flush=True)
                if a.max_pages and page > a.max_pages:
                    break
        w.close()
        (out / f'{app}.DONE').write_text(time.strftime('%Y-%m-%dT%H:%M:%S'))
        print(f'{app} DONE: {n[0]} texts this run', flush=True)


if __name__ == '__main__':
    main()
