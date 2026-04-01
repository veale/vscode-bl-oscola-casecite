# CaseCite — UK, EU & ECHR Law → BibLaTeX

A VS Code extension (with a standalone Python script) that searches legal databases and produces `@jurisdiction` and `@legislation` biblatex entries compatible with OSCOLA / [biblatex-oscola](https://github.com/PaulStanley/oscola-biblatex).

Install it [here](https://marketplace.visualstudio.com/items?itemName=mveale.casecite).

<img width="250" alt="Image of UK legislation query" src="https://github.com/user-attachments/assets/6ce89bf9-aeb6-4780-a5cc-3d3b8d3bd447" />
<img width="250" alt="Image of EU case query" src="https://github.com/user-attachments/assets/52dfa0a6-f2db-4bcd-9d0f-c27a075027c3" />
<img width="250"  alt="Image of EU legislation query" src="https://github.com/user-attachments/assets/32b7b14b-e425-4d92-99cf-c5cbce95a289" />
<img width="250"  alt="Image of ECHR query" src="https://github.com/user-attachments/assets/42be1afc-52f4-4d3f-9849-58babb28910f" />
<img width="250"  alt="Image of UK case query" src="https://github.com/user-attachments/assets/07b3b235-4b4b-49c9-8c26-7eaefd878bf5" />


## Sources

| Tab | Source | What it searches | Entry type |
|-----|--------|-----------------|------------|
| **UK** | [National Archives Case Law](https://caselaw.nationalarchives.gov.uk) | UK court judgments and tribunal decisions | `@jurisdiction` |
| **UK Leg** | [legislation.gov.uk](https://www.legislation.gov.uk) | UK Acts, SIs, devolved legislation, assimilated EU law | `@legislation` |
| **EU Cases** | [CELLAR SPARQL](https://publications.europa.eu/webapi/rdf/sparql) | CJEU and General Court judgments, AG opinions | `@jurisdiction` |
| **EU Leg** | [CELLAR SPARQL](https://publications.europa.eu/webapi/rdf/sparql) | EU directives, regulations, decisions, treaties | `@legislation` |
| **ECHR** | [HUDOC](https://hudoc.echr.coe.int) via [echr-extractor](https://github.com/maastrichtlawtech/echr-extractor) | ECtHR judgments, Commission decisions | `@jurisdiction` |

## Output examples

### UK case
```bibtex
@jurisdiction{ittihadieh,
	date = {2017},
	keywords = {gb},
	number = {[2017] EWCA Civ 121},
	pagination = {[]},
	title = {Ittihadieh v 5-11 Cheyne Gardens {RTM} Company Ltd \& Ors}}
```

### UK primary legislation
```bibtex
@legislation{ucta,
	title = {Unfair Contract Terms Act},
	date = {1977},
	entrysubtype = {primary},
	pagination = {section},
	keywords = {en},
}
```

### UK statutory instrument
```bibtex
@legislation{disorderly,
	title = {Penalties for Disorderly Behaviour (Amendment of Minimum Age) Order},
	date = {2004},
	entrysubtype = {secondary},
	pagination = {regulation},
	keywords = {en},
	number = {SI 2004\slash 3166},
}
```

### Devolved legislation (Scottish, Welsh, NI)
```bibtex
@legislation{welshtax22,
	title = {Welsh Tax Acts etc. (Power to Modify) Act},
	date = {2022},
	entrysubtype = {primary},
	pagination = {section},
	keywords = {cy},
	number = {asc 2},
}
```

### Assimilated EU law (via UK Leg tab)
```bibtex
@legislation{regulationeu16,
	title = {Regulation ({EU}) 2016/679 of the European Parliament...},
	date = {2016},
	entrysubtype = {secondary},
	pagination = {article},
	keywords = {eu, assimilated},
}
```

### EU judgment
```bibtex
@jurisdiction{rijkeboer,
	date = {2009-05-07},
	ecli = {EU:C:2009:293},
	institution = {CJEU},
	keywords = {eu},
	number = {C-553/07},
	title = {College van burgemeester en wethouders van Rotterdam v M.E.E. Rijkeboer}}
```

### EU AG Opinion
```bibtex
@jurisdiction{rijkeboerAG,
	date = {2008-12-22},
	ecli = {EU:C:2008:773, Opinion of AG Ruiz-Jarabo Colomer},
	institution = {CJEU},
	keywords = {eu},
	number = {C-553/07},
	pagination = {point},
	title = {College van burgemeester en wethouders van Rotterdam v M.E.E. Rijkeboer}}
```

### EU legislation
```bibtex
@legislation{200260EC,
	title = {Council Directive 2002/60/{EC} of 27 June 2002...},
	date = {2002},
	type = {directive},
	entrysubtype = {directive},
	number = {2002/60/EC},
	journaltitle = {OJ},
	series = {L},
	issue = {192},
	pages = {27},
	keywords = {eu},
	pagination = {article},
}
```

### ECHR — Reported (Series A)
```bibtex
@jurisdiction{johnston86,
	title = {Johnston v Ireland},
	reporter = {Series A},
	pages = {122},
	date = {1986},
	institution = {ECtHR},
	keywords = {echr},
}
```

### ECHR — Reported (ECHR Reports)
```bibtex
@jurisdiction{osman98,
	title = {Osman v UK},
	reporter = {ECHR},
	date = {1998},
	volume = {8},
	pages = {3124},
	institution = {ECtHR},
	keywords = {echr},
}
```

### ECHR — Unreported
```bibtex
@jurisdiction{balogh04,
	title = {Balogh v Hungary},
	number = {47940/99},
	date = {2004-07-20},
	institution = {ECtHR},
	keywords = {echr},
}
```

### ECHR — Commission Decision (with DR)
```bibtex
@jurisdiction{simpson86,
	title = {Simpson v UK},
	date = {1989},
	volume = {64},
	journaltitle = {DR},
	pages = {188},
	institution = {Commission},
	keywords = {echr},
}
```

## Installation

### Prerequisites
- Python 3.8+ (standard library only for UK/EU features)
- VS Code 1.80+
- **Optional**: `pip install echr-extractor` for ECHR support (you'll be prompted if needed)

### As a VS Code extension

Install from the [VS Code Marketplace](https://marketplace.visualstudio.com/items?itemName=mveale.casecite)

But if you want to use it from here:

1. Clone or download this folder.
2. Open it in VS Code.
3. Press `F5` to launch the Extension Development Host.
4. The CaseCite icon appears in the activity bar (sidebar).

To package for permanent installation: `vsce package --allow-missing-repository` then install the `.vsix`.

### As a standalone CLI

```bash
chmod +x scripts/casecite.py
ln -s "$(pwd)/scripts/casecite.py" ~/.local/bin/casecite
```

## VS Code sidebar

The sidebar has five focused tabs — each queries only its own source for fast results:

| Tab | Accepts | Examples |
|-----|---------|---------|
| **UK** | Neutral citation, party name | `[2024] UKSC 30`, `Ittihadieh` |
| **UK Leg** | Legislation title | `Data Protection Act`, `Unfair Contract Terms` |
| **EU Cases** | Case number, CELEX, ECLI, keyword | `C-553/07`, `62013CJ0212`, `data protection` |
| **EU Leg** | CELEX, keyword | `32016R0679`, `platform`, `artificial intelligence` |
| **ECHR** | Case name, application number | `Osman v UK`, `47940/99` |

Features:
- **Live bib preview** — click a result to see the formatted entry; cite key, shorthand, and shorttitle are editable inline
- **One-click actions** — Insert at cursor, Append to .bib file, Copy to clipboard
- **Keyboard navigation** — Arrow keys move through results
- **Smart title display** — EU legislation titles strip institutional boilerplate ("of the European Parliament and of the Council of DD Month YYYY") so you can see the actual subject matter

## Supported courts

The extension parses neutral citations for all UK courts and tribunals, including:

**England & Wales**: UKSC, UKPC, EWCA Civ/Crim, EWHC (all divisions), EWFC, EWCOP, EAT, CAT

**Tribunals**: UKUT (AAC, IAC, LC, TCC), UKFTT (GRC, TC, IAC, HESC, SEC, PC, RPV, WP, WPAFCC), UKIPTrib, UKSIAC

**Northern Ireland**: NICA, NIKB, NIQB, NICh, NIFam, NICC, NIMaster

**Scotland**: CSIH, CSOH, HCJ, SAC

The neutral citation regex is permissive — new court abbreviations are parsed automatically even before `COURT_MAP` is updated.

## UK legislation type mapping

| legislation.gov.uk type | OSCOLA keywords | entrysubtype | pagination | number format |
|---|---|---|---|---|
| `ukpga` | `en` | `primary` | `section` | *(not needed)* |
| `uksi` | `en` | `secondary` | `regulation` | `SI YYYY/NNNN` |
| `asp` | `sc` | `primary` | `section` | `asp N` |
| `asc` | `cy` | `primary` | `section` | `asc N` |
| `anaw` | `cy` | `primary` | `section` | `anaw N` |
| `mwa` | `cy` | `primary` | `section` | `nawm N` |
| `nia` | `ni` | `primary` | `section` | `c N` |
| `ssi` | `sc` | `secondary` | `regulation` | `SSI YYYY/NNNN` |
| `wsi` | `cy` | `secondary` | `regulation` | `WSI YYYY/NNNN` |
| `nisr` | `ni` | `secondary` | `regulation` | `SR YYYY/NNNN` |
| `eur` / `eudn` / `eudr` | `eu, assimilated` | `secondary` | `article` | — |

## Commands

| Command | Keybinding | Description |
|---------|-----------|-------------|
| `CaseCite: Focus search` | `Ctrl+Shift+C` / `Cmd+Shift+C` | Focus the sidebar search box |
| `CaseCite: Look up UK case` | | Quick lookup via command palette |
| `CaseCite: Look up UK legislation` | | Quick lookup via command palette |
| `CaseCite: Look up EU case` | | Quick lookup via command palette |
| `CaseCite: Look up EU legislation` | | Quick lookup via command palette |
| `CaseCite: Look up ECHR case` | | Quick lookup via command palette |
| `CaseCite: Export cache` | | Save all cached entries as `.bib` |
| `CaseCite: Clear cache` | | Delete all cached entries |

## CLI usage

```
casecite.py {uk,eu,euleg,echr,ukleg,search,cache} [options]
```

### Examples

```bash
# UK cases
casecite.py uk "[2024] UKSC 30"
casecite.py uk "[2025] UKFTT 798 (GRC)"
casecite.py uk "Ittihadieh"

# UK legislation
casecite.py ukleg "Data Protection Act"
casecite.py ukleg "ukpga/2018/12"

# EU cases
casecite.py eu "C-553/07"
casecite.py eu "62013CJ0212"
casecite.py eu "ECLI:EU:C:2014:2428"

# EU legislation
casecite.py euleg "32016R0679"
casecite.py euleg "platform"

# ECHR
casecite.py echr "Osman v UK"
casecite.py echr "47940/99"

# Targeted search (only queries one source — fast)
casecite.py search "data protection" --source uk
casecite.py search "transport" --source ukleg
casecite.py search "privacy" --source echr

# Options
casecite.py eu "C-553/07" --key rijkeboer
casecite.py eu "C-553/07" --append ~/thesis/cases.bib

# Cache
casecite.py cache list
casecite.py cache export -o ~/all-cases.bib
casecite.py cache clear
```

## Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `casecite.pythonPath` | `python3` | Path to your Python interpreter |
| `casecite.defaultBibFile` | *(blank)* | If set, "Append to .bib" uses this file directly |
| `casecite.cacheDir` | *(blank)* | Cache directory (default: `~/.casecite/`) |
| `casecite.autoCache` | `true` | Automatically cache every looked-up case |

## Architecture

```
casecite/
├── package.json           # VS Code extension manifest
├── src/
│   ├── extension.js       # Extension entry (commands, completion, registration)
│   └── sidebar.js         # WebviewViewProvider (sidebar UI)
├── scripts/
│   └── casecite.py        # Python script (all API logic, formatting, caching)
└── README.md
```

The extension is a thin JS wrapper. `sidebar.js` renders the webview and relays messages to the extension host, which spawns `casecite.py --json` as a subprocess. All API logic, OSCOLA formatting, and caching lives in the Python script — no npm dependencies beyond the VS Code API.

## Technical notes

### CELLAR SPARQL
All CELLAR queries use the `STR()` / `FILTER` pattern required since the Virtuoso 7→8 upgrade (March 2026). The search queries are lightweight (CELEX sector filter + title `CONTAINS` only) to avoid SPARQL timeouts. Direct CELEX/ECLI/case-number lookups bypass text search entirely.

### EU legislation OJ references
Extracted from Formex XML via content negotiation with CELLAR. Handles both the old schema (formex-05, pre-2023: `<PUBLICATION.REF>` with volume and page) and the new schema (formex-06, 2024+: `<BIB.OJ>` with series only — no page numbers per the new OJ format). Falls back to CELEX descriptor letter for the OJ series when Formex is unavailable.

### ECHR
Requires `pip install echr-extractor`. The extension checks on first use and displays an install instruction if missing — no auto-install. Reporter parsing handles Series A, ECHR Reports (with Roman numeral volume conversion), and Commission Decisions and Reports (DR). HUDOC dates are normalised from DD/MM/YYYY or ISO formats to YYYY-MM-DD.

### Network resilience
SPARQL queries retry up to 2 times on transient DNS/network errors. If SPARQL fails entirely, EU case lookups fall back to extracting metadata from Formex XML (parties, ECLI, case number, date).

## API documentation

- **National Archives**: https://caselaw.nationalarchives.gov.uk
- **legislation.gov.uk**: https://www.legislation.gov.uk (identifier search + XML metadata)
- **CELLAR SPARQL**: https://publications.europa.eu/webapi/rdf/sparql
- **HUDOC**: https://hudoc.echr.coe.int (via `echr-extractor`)

## Licence

MIT
