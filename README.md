# CaseCite — UK & EU Case Law → BibLaTeX

A VS Code extension (with a standalone Python script) that searches UK and EU
case law APIs and produces `@jurisdiction` biblatex entries compatible with
OSCOLA / biblatex-oscola.

## What it does

1. **UK cases** — Queries the [National Archives Case Law API](https://caselaw.nationalarchives.gov.uk)
   by neutral citation (e.g. `[2024] UKSC 30`) or party name.
2. **EU cases** — Queries the [CELLAR SPARQL endpoint](https://publications.europa.eu/webapi/rdf/sparql)
   and EUR-Lex by case number (`C-553/07`), CELEX (`62007CJ0553`), or
   ECLI (`EU:C:2009:293`).
3. **AG Opinions** — Automatically detected via CELEX suffix (`CC`) or
   CELLAR document type (`OPIN_AG`). Formatted with `pagination = {point}`
   and the AG name appended to the ECLI field per OSCOLA convention.
4. Formats results as `@jurisdiction` entries with the fields you need.
5. Maintains a **local SQLite cache** (`~/.casecite/cache.db`) so you never
   look up the same case twice.

## Output format

UK cases:
```bibtex
@jurisdiction{ittihadieh,
	date = {2017},
	keywords = {gb},
	number = {[2017] EWCA Civ 121},
	pagination = {[]},
	shorttitle = {Ittihadieh},
	title = {Ittihadieh v 5-11 Cheyne Gardens {RTM} Company Ltd \& Ors}}
```

EU judgments:
```bibtex
@jurisdiction{rijkeboer,
	date = {2009-05-07},
	ecli = {EU:C:2009:293},
	institution = {CJEU},
	keywords = {eu},
	number = {C-553/07},
	shorttitle = {Rijkeboer},
	title = {College van burgemeester en wethouders van Rotterdam v M.E.E. Rijkeboer}}
```

AG Opinions:
```bibtex
@jurisdiction{rijkeboerAG,
	date = {2008-12-22},
	ecli = {EU:C:2008:773, Opinion of AG Ruiz-Jarabo Colomer},
	institution = {CJEU},
	keywords = {eu},
	number = {C-553/07},
	pagination = {point},
	shorttitle = {Rijkeboer},
	title = {College van burgemeester en wethouders van Rotterdam v M.E.E. Rijkeboer}}
```

## Installation

### Prerequisites
- Python 3.8+ (no additional packages needed — uses only the standard library)
- VS Code 1.80+

### As a VS Code extension (recommended)

1. Clone or download this folder.
2. Open it in VS Code.
3. Press `F5` to launch the Extension Development Host.
4. The CaseCite icon appears in the activity bar (sidebar).

To package for permanent installation: `vsce package` then install the `.vsix`.

### As a standalone CLI

```bash
chmod +x scripts/casecite.py
ln -s "$(pwd)/scripts/casecite.py" ~/.local/bin/casecite

casecite uk "[2017] EWCA Civ 121"
casecite eu "C-553/07"
casecite eu "62007CC0553"    # AG opinion (CC suffix)
casecite search "data protection"
```

## VS Code sidebar

The primary interface is a **sidebar panel** in the activity bar. It provides:

- **Persistent search** — type to search, results appear with debounced
  queries (400ms). No modal dialogs.
- **Filter tabs** — All / UK / EU / Cache to narrow results.
- **Rich result cards** — party names, court badge (UK/EU), citation,
  date. Enough to disambiguate similar cases at a glance.
- **Live bib preview** — click a result to see the formatted
  `@jurisdiction` entry. The cite key is editable inline and updates
  the preview in real time.
- **One-click actions** — Insert at cursor, Append to .bib file, or
  Copy to clipboard.
- **Keyboard navigation** — Arrow keys move through results while the
  cursor stays in the search input.

## Commands and keybindings

| Command                       | Keybinding              | Description                                   |
|-------------------------------|-------------------------|-----------------------------------------------|
| `CaseCite: Focus search`     | `Ctrl+Shift+C` / `Cmd+Shift+C` | Open and focus the sidebar search box   |
| `CaseCite: Look up UK case`  |                         | Quick lookup via command palette              |
| `CaseCite: Look up EU case`  |                         | Quick lookup via command palette              |
| `CaseCite: Export cache`     |                         | Save all cached entries as a `.bib` file      |

## cite autocomplete

When editing `.tex` files, typing `\cite{` (or `\autocite{`, `\textcite{`,
etc.) triggers completion from your local cache. No network call — instant
suggestions from cases you've already looked up. The completion popup shows
the cite key, title, and source.

## Settings

| Setting                    | Default      | Description                                       |
|---------------------------|--------------|---------------------------------------------------|
| `casecite.pythonPath`     | `python3`    | Path to your Python interpreter                   |
| `casecite.defaultBibFile` | (blank)      | If set, "Append to .bib" uses this file directly  |
| `casecite.cacheDir`       | (blank)      | Cache directory (default: `~/.casecite/`)         |
| `casecite.autoCache`      | `true`       | Automatically cache every looked-up case          |

## CLI usage

```
casecite.py [-h] [--json] [--key KEY] [--append FILE] {uk,eu,search,cache} ...

Subcommands:
  uk        Look up a UK case
  eu        Look up an EU case
  search    Search both UK and EU
  cache     Manage the local cache (list | export | search)
```

### Examples

```bash
# UK neutral citation
casecite uk "[2024] UKSC 30"

# UK party name
casecite uk "Ittihadieh"

# EU judgment by case number
casecite eu "C-553/07"

# EU AG opinion by CELEX (CC suffix = AG opinion)
casecite eu "62018CC0311"

# EU by ECLI
casecite eu "EU:C:2009:293"

# Free-text search
casecite search "right to be forgotten"

# Custom cite key
casecite eu "C-553/07" --key rijkeboer

# Append to .bib
casecite eu "C-553/07" --append ~/thesis/cases.bib

# Cache operations
casecite cache list
casecite cache export -o ~/all-cases.bib
casecite cache search "data protection"
```

## Architecture

```
case-cite/
├── package.json           # VS Code extension manifest
├── src/
│   ├── extension.js       # Extension entry (commands, completion, registration)
│   └── sidebar.js         # WebviewViewProvider (sidebar UI)
├── scripts/
│   └── casecite.py        # Python script (API logic, formatting, caching)
└── README.md
```

The extension is a thin JS wrapper. `sidebar.js` renders the webview and
sends messages to the extension host, which calls the Python script with
`--json`. All API logic, OSCOLA formatting, AG opinion detection, and
caching lives in `casecite.py`.

## OSCOLA formatting notes

### UK cases
- `pagination = {[]}` — signals neutral citation (paragraph numbers in
  square brackets)
- `keywords = {gb}` — used to distinguish jurisdiction
- `number` holds the full neutral citation, e.g. `[2017] EWCA Civ 121`

### EU judgments
- `ecli` field holds the bare ECLI without the `ECLI:` prefix
- `institution` is `CJEU` or `General Court`
- `keywords = {eu}`
- No `pagination` field (paragraphs are the default)

### EU AG Opinions
- `ecli` field has `, Opinion of AG [Name]` appended after the ECLI
- `pagination = {point}` — AG opinions use "point" not "paragraph"
- Auto-generated cite key appends `AG` (e.g. `rijkeboerAG`)
- `shorttitle` omits the AG suffix for readability
- Detected automatically from CELEX (`CC` suffix) or CELLAR metadata

## SPARQL compatibility

All CELLAR SPARQL queries use the `STR()` / `FILTER` pattern required
since the Virtuoso 7 to 8 upgrade (March 2026). Direct literal triple
patterns no longer work.

## API documentation

- **National Archives**: https://caselaw.nationalarchives.gov.uk (Atom XML)
- **CELLAR SPARQL**: https://publications.europa.eu/webapi/rdf/sparql
- **EUR-Lex search**: https://eur-lex.europa.eu/content/help/eurlex-content/search-tips.html

## Licence

MIT
