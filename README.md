# Austrian case law: Verwaltungsgerichtshof (VwGH)

Part of [lexifina/global-law](https://github.com/lexifina/global-law), one git history for every country's law: this is the case law of the Verwaltungsgerichtshof (VwGH), Austria, which global-law reads at `austria/verwaltungsgerichtshof/caselaw/`.

Every decision text (Entscheidungstext) of the Verwaltungsgerichtshof (VwGH) published in RIS, as a git history: one file per decision, one
commit per decision, dated the day it was decided. 141,418 decisions from 1876-10-26 to 2026-11-07.

## Datenquelle und Lizenz / Source and licence

Datenquelle: RIS - Rechtsinformationssystem des Bundes (www.ris.bka.gv.at), Bundeskanzleramt; Creative Commons Namensnennung 4.0 International (CC BY 4.0), https://creativecommons.org/licenses/by/4.0/deed.de; abgerufen 2026-11-07.

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
