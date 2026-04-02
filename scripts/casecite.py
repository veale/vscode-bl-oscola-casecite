#!/usr/bin/env python3
"""
casecite — Look up UK and EU case law and produce @jurisdiction biblatex entries.

Usage:
    casecite.py search "data protection"          # free-text search (both APIs)
    casecite.py uk "[2017] EWCA Civ 121"          # look up by neutral citation
    casecite.py uk "Ittihadieh"                   # look up by party name
    casecite.py eu "C-553/07"                     # look up by case number
    casecite.py eu "62007CJ0553"                  # look up by CELEX
    casecite.py eu "EU:C:2009:293"                # look up by ECLI
    casecite.py cache list                         # list cached entries
    casecite.py cache export                       # dump cache as .bib
    casecite.py cache export -o cases.bib          # write cache to file

Environment:
    CASECITE_CACHE  — path to the SQLite cache (default: ~/.casecite/cache.db)
    CASECITE_BIB    — default .bib file to append to

The script can be called directly or invoked by the VS Code extension.
It communicates via JSON on stdout when --json is passed.
"""

import argparse
import hashlib
import io
import json
import os
import re
import sqlite3
import sys
import textwrap
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CACHE_DIR = Path(os.environ.get("CASECITE_CACHE_DIR", Path.home() / ".casecite"))
CACHE_DB = CACHE_DIR / "cache.db"

UK_API_BASE = "https://caselaw.nationalarchives.gov.uk"
EURLEX_SPARQL = "https://publications.europa.eu/webapi/rdf/sparql"
EURLEX_SEARCH = "https://eur-lex.europa.eu/search.html"
CELLAR_BASE = "https://publications.europa.eu/resource/celex"

# Map National Archives court paths to (base_court, division) tuples.
# For EWHC/UKUT/UKFTT, the neutral citation format is [year] COURT number (Division),
# i.e. the division comes AFTER the number.
# For EWCA, the division comes BEFORE: [year] EWCA Civ number.
#
# Sources: Practice Direction [2001] 1 WLR 194; Practice Direction [2002] 1 WLR 346;
# Practice Statement (Tribunals) 31 Oct 2008; Inner Temple Library neutral citation list;
# National Archives court codes; Zotero translator by Michael Veale.
COURT_MAP = {
    # Supreme Court / Privy Council
    "uksc": ("UKSC", None),
    "ukpc": ("UKPC", None),
    # Court of Appeal (E&W)
    "ewca/civ": ("EWCA Civ", None),
    "ewca/crim": ("EWCA Crim", None),
    # High Court (E&W) — division after number
    "ewhc/admin": ("EWHC", "Admin"),
    "ewhc/admlty": ("EWHC", "Admlty"),
    "ewhc/ch": ("EWHC", "Ch"),
    "ewhc/comm": ("EWHC", "Comm"),
    "ewhc/costs": ("EWHC", "Costs"),
    "ewhc/fam": ("EWHC", "Fam"),
    "ewhc/ipec": ("EWHC", "IPEC"),
    "ewhc/kb": ("EWHC", "KB"),
    "ewhc/qb": ("EWHC", "QB"),
    "ewhc/mercantile": ("EWHC", "Mercantile"),
    "ewhc/pat": ("EWHC", "Pat"),
    "ewhc/scco": ("EWHC", "SCCO"),
    "ewhc/tcc": ("EWHC", "TCC"),
    # Specialist E&W courts
    "ewfc": ("EWFC", None),
    "ewcop": ("EWCOP", None),
    "ewcc": ("EWCC", None),
    # Upper Tribunal — chamber after number
    "ukut/aac": ("UKUT", "AAC"),
    "ukut/iac": ("UKUT", "IAC"),
    "ukut/lc": ("UKUT", "LC"),
    "ukut/tcc": ("UKUT", "TCC"),
    # First-tier Tribunal — chamber after number
    "ukftt/grc": ("UKFTT", "GRC"),
    "ukftt/tc": ("UKFTT", "TC"),
    "ukftt/iac": ("UKFTT", "IAC"),
    "ukftt/hesc": ("UKFTT", "HESC"),
    "ukftt/sec": ("UKFTT", "SEC"),
    "ukftt/pc": ("UKFTT", "PC"),
    "ukftt/rpv": ("UKFTT", "RPV"),
    "ukftt/wp": ("UKFTT", "WP"),
    "ukftt/wpafcc": ("UKFTT", "WPAFCC"),
    # Other E&W tribunals
    "eat": ("EAT", None),
    "cat": ("CAT", None),
    "ukiptrib": ("UKIPTrib", None),
    "uksiac": ("UKSIAC", None),
    # Northern Ireland
    "nica": ("NICA", None),
    "nikb": ("NIKB", None),
    "niqb": ("NIQB", None),
    "nich": ("NICh", None),
    "nifam": ("NIFam", None),
    "nicc": ("NICC", None),
    "nimaster": ("NIMaster", None),
    # Scotland
    "csih": ("CSIH", None),
    "csoh": ("CSOH", None),
    "hcj": ("HCJ", None),
    "sac": ("SAC", None),
    # Historic
    "ukist": ("UKIST", None),
    "ukccat": ("UKCCAT", None),
    "ukcmst": ("UKCMST", None),
    "uktr": ("UKTr", None),
}

# EU court codes from CELLAR to readable institution names
EU_COURT_MAP = {
    "CJ": "CJEU",
    "GC": "General Court",
    "CST": "Civil Service Tribunal",
}

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _init_cache():
    """Initialise the SQLite cache if it doesn't exist."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(CACHE_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cases (
            cite_key   TEXT PRIMARY KEY,
            bib_entry  TEXT NOT NULL,
            source     TEXT NOT NULL,       -- 'uk' or 'eu'
            citation   TEXT,                -- neutral citation or ECLI
            title      TEXT,
            date_added TEXT NOT NULL,
            raw_json   TEXT                 -- original API response for debugging
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_citation ON cases(citation)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_title ON cases(title)
    """)
    conn.commit()
    return conn


def cache_put(conn, cite_key: str, bib_entry: str, source: str,
              citation: str = "", title: str = "", raw_json: str = ""):
    """Insert or replace a cache entry."""
    conn.execute(
        "INSERT OR REPLACE INTO cases (cite_key, bib_entry, source, citation, title, date_added, raw_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (cite_key, bib_entry, source, citation, title, datetime.now().isoformat(), raw_json),
    )
    conn.commit()


def cache_get(conn, cite_key: str) -> Optional[str]:
    """Retrieve a cached bib entry by cite key."""
    row = conn.execute("SELECT bib_entry FROM cases WHERE cite_key = ?", (cite_key,)).fetchone()
    return row[0] if row else None


def cache_search(conn, term: str) -> list:
    """Search cache by citation or title substring."""
    rows = conn.execute(
        "SELECT cite_key, bib_entry, citation, title FROM cases "
        "WHERE citation LIKE ? OR title LIKE ? OR cite_key LIKE ?",
        (f"%{term}%", f"%{term}%", f"%{term}%"),
    ).fetchall()
    return [{"cite_key": r[0], "bib_entry": r[1], "citation": r[2], "title": r[3]} for r in rows]


def cache_list(conn) -> list:
    """List all cached entries."""
    rows = conn.execute(
        "SELECT cite_key, citation, title, source, date_added FROM cases ORDER BY date_added DESC"
    ).fetchall()
    return [{"cite_key": r[0], "citation": r[1], "title": r[2], "source": r[3], "date_added": r[4]} for r in rows]


def cache_export(conn) -> str:
    """Export all cached entries as a .bib string."""
    rows = conn.execute("SELECT bib_entry FROM cases ORDER BY cite_key").fetchall()
    return "\n\n".join(r[0] for r in rows)


def cache_clear(conn) -> int:
    """Delete all cached entries. Returns the number of entries deleted."""
    count = conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
    conn.execute("DELETE FROM cases")
    conn.commit()
    return count


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fetch_json(url: str, headers: Optional[dict] = None) -> dict:
    """Fetch a URL and return parsed JSON."""
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _fetch_xml(url: str, headers: Optional[dict] = None) -> ET.Element:
    """Fetch a URL and return parsed XML."""
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return ET.fromstring(resp.read())


def _fetch_text(url: str, headers: Optional[dict] = None) -> str:
    """Fetch a URL and return raw text."""
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode("utf-8")


def _sanitise_key(text: str) -> str:
    """
    Generate a cite key from the title using the first three words in camelCase.
    e.g. "Ittihadieh v 5-11 Cheyne Gardens RTM Company Ltd & Ors"
         → "ittihadiehV5"
    e.g. "College van burgemeester en wethouders van Rotterdam v M.E.E. Rijkeboer"
         → "collegeVanBurgemeester"
    e.g. "DPP v Lennon"
         → "dppVLennon"
    e.g. "La Quadrature du Net and Others v Premier ministre and Others"
         → "laQuadratureDu"
    """
    # Extract words (including short ones and numbers-with-letters)
    words = re.findall(r'[A-Za-z0-9]+', text)
    if not words:
        return "case"

    # Take the first three
    first_three = words[:3]

    # camelCase: first word lowercase, subsequent words capitalised
    key = first_three[0].lower()
    for w in first_three[1:]:
        key += w.capitalize()

    return key


def _escape_bibtex(text: str) -> str:
    """Escape special characters for biblatex and protect capitalisation."""
    text = text.replace("&", r"\&")
    # Wrap uppercase acronyms in braces to protect them
    text = re.sub(r'\b([A-Z]{2,})\b', r'{{\1}}', text)
    return text


def _fix_jrapp_title(title: str) -> str:
    """
    Convert National Archives judicial review title format to OSCOLA format.

    TNA stores:  "Applicant, R (on the application of) v Respondent"
    OSCOLA needs: "R (Applicant) v Respondent"

    Also handles:
      "Applicant & Anor, R (on the application of) v Resp"
      → "R (Applicant & Anor) v Resp"
    """
    m = re.match(
        r'^(.+?),\s*R\s*\(on the application of\)\s*v\s*(.+)$',
        title,
        re.IGNORECASE,
    )
    if m:
        applicant = m.group(1).strip()
        respondent = m.group(2).strip()
        return f"R ({applicant}) v {respondent}"
    return title


# ---------------------------------------------------------------------------
# UK Case Law (National Archives API)
# ---------------------------------------------------------------------------

def _parse_neutral_citation(cite: str) -> Optional[dict]:
    """
    Parse a neutral citation like [2017] EWCA Civ 121 into components.
    Returns dict with year, court_path, number or None.
    """
    # Match neutral citations for all UK courts and tribunals.
    # Format: [year] COURT number or [year] COURT number (Division)
    # EWCA has division before number: [year] EWCA Civ number
    #
    # The regex is permissive: it matches any uppercase letters as the court
    # abbreviation, with an optional word after (for EWCA Civ/Crim), then
    # digits, then an optional parenthesised division. This means new courts
    # are automatically supported even if not in COURT_MAP.
    m = re.match(
        r'\[(\d{4})\]\s+'
        r'([A-Z][A-Za-z]+(?:\s+[A-Z][a-z]+)?)'  # court + optional division word (EWCA Civ)
        r'\s+(\d+)'
        r'(?:\s*\(([A-Za-z0-9]+)\))?',           # optional (Division) suffix
        cite.strip(),
    )
    if not m:
        return None
    year, court_str, number, division = m.group(1), m.group(2), m.group(3), m.group(4)

    # Build the document URI path
    court_lower = court_str.lower().replace(" ", "/")
    if division:
        court_lower = f"{court_lower}/{division.lower()}"

    return {"year": year, "court": court_str, "number": number,
            "division": division, "uri": f"{court_lower}/{year}/{number}"}


def uk_lookup_by_citation(cite: str) -> Optional[dict]:
    """Look up a UK case by its neutral citation."""
    parsed = _parse_neutral_citation(cite)
    if not parsed:
        return None
    try:
        url = f'{UK_API_BASE}/{parsed["uri"]}'
        # Fetch the metadata XML
        xml_url = f"{url}/data.xml"
        xml_data = _fetch_text(xml_url)
        root = ET.fromstring(xml_data)

        # Extract party names from FRBRname
        name_el = root.find(".//{http://docs.oasis-open.org/legaldocml/ns/akn/3.0}FRBRname")
        title = name_el.get("value", "") if name_el is not None else ""

        # Extract date
        date_el = root.find(".//{http://docs.oasis-open.org/legaldocml/ns/akn/3.0}FRBRdate[@name='judgment']")
        if date_el is None:
            date_el = root.find(".//{http://docs.oasis-open.org/legaldocml/ns/akn/3.0}FRBRdate[@name='decision']")
        if date_el is None:
            date_el = root.find(".//{http://docs.oasis-open.org/legaldocml/ns/akn/3.0}FRBRdate")
        date_str = date_el.get("date", "") if date_el is not None else parsed["year"]

        return {
            "title": title,
            "date": date_str,
            "citation": cite.strip(),
            "year": parsed["year"],
            "uri": parsed["uri"],
            "source": "uk",
        }
    except Exception as e:
        return None


def uk_lookup_by_uri(uri: str) -> Optional[dict]:
    """Look up a UK case by its document URI (path or UUID style)."""
    uri = uri.strip("/")
    try:
        xml_url = f"{UK_API_BASE}/{uri}/data.xml"
        xml_data = _fetch_text(xml_url)
        root = ET.fromstring(xml_data)

        AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
        TNA = "https://caselaw.nationalarchives.gov.uk"

        name_el = root.find(f".//{{{AKN}}}FRBRname")
        title = name_el.get("value", "") if name_el is not None else ""

        date_el = root.find(f".//{{{AKN}}}FRBRdate[@name='judgment']")
        if date_el is None:
            date_el = root.find(f".//{{{AKN}}}FRBRdate[@name='decision']")
        if date_el is None:
            date_el = root.find(f".//{{{AKN}}}FRBRdate")
        date_str = date_el.get("date", "") if date_el is not None else ""

        # Try to extract neutral citation from multiple possible locations
        citation = ""

        # 1. Dedicated neutralCitation element
        ncn_el = root.find(f".//{{{AKN}}}neutralCitation")
        if ncn_el is not None:
            citation = "".join(ncn_el.itertext()).strip()

        # 2. TNA cite element
        if not citation:
            cite_el = root.find(f".//{{{TNA}}}cite")
            if cite_el is not None:
                citation = "".join(cite_el.itertext()).strip()

        # 3. Scan header for a neutral citation pattern
        if not citation:
            header = root.find(f".//{{{AKN}}}header")
            if header is not None:
                for p in header.iter(f"{{{AKN}}}p"):
                    text = "".join(p.itertext()).strip()
                    if text and re.match(r'\[\d{4}\]\s+\w+', text) and len(text) < 50:
                        citation = text
                        break

        # 4. Fall back to reconstructing from URI (only useful for path-style URIs)
        if not citation and "/" in uri:
            citation = _citation_from_uri(uri)

        year = date_str[:4] if date_str else ""
        if not year:
            parts = uri.split("/")
            for p in reversed(parts):
                if re.match(r'^\d{4}$', p):
                    year = p
                    break

        return {
            "title": title,
            "date": date_str,
            "citation": citation,
            "year": year,
            "uri": uri,
            "source": "uk",
        }
    except Exception:
        return None


def uk_search(query: str, per_page: int = 10) -> list:
    """
    Search National Archives case law API via the Atom feed.
    Returns a list of result dicts.
    """
    params = urllib.parse.urlencode({
        "query": query, "per_page": per_page, "order": "-date"
    })
    url = f"{UK_API_BASE}/atom.xml?{params}"
    try:
        text = _fetch_text(url)
        root = ET.fromstring(text)
        ns = {"atom": "http://www.w3.org/2005/Atom",
              "tna": "https://caselaw.nationalarchives.gov.uk"}

        results = []
        for entry in root.findall("atom:entry", ns):
            title_el = entry.find("atom:title", ns)
            link_el = entry.find("atom:link[@rel='alternate']", ns)
            published_el = entry.find("atom:published", ns)

            # URI comes from the tna:uri element
            uri_el = entry.find("tna:uri", ns)
            uri = uri_el.text.strip() if uri_el is not None and uri_el.text else ""

            # Extract neutral citation from identifiers
            citation = ""
            for ident in entry.findall("tna:identifier", ns):
                if ident.get("type") == "ukncn" and ident.text:
                    citation = ident.text.strip()
                    break

            results.append({
                "title": title_el.text if title_el is not None else "",
                "uri": uri,
                "citation": citation,
                "url": link_el.get("href", "") if link_el is not None else "",
                "date": published_el.text[:10] if published_el is not None and published_el.text else "",
            })
        return results
    except Exception as e:
        return []


def uk_lookup_by_party(party: str, per_page: int = 10) -> list:
    """Search UK cases by party name via the National Archives API."""
    params = urllib.parse.urlencode({
        "party": party, "per_page": per_page, "order": "-date"
    })
    url = f"{UK_API_BASE}/atom.xml?{params}"
    try:
        text = _fetch_text(url)
        root = ET.fromstring(text)
        ns = {"atom": "http://www.w3.org/2005/Atom",
              "tna": "https://caselaw.nationalarchives.gov.uk"}

        results = []
        for entry in root.findall("atom:entry", ns):
            title_el = entry.find("atom:title", ns)
            published_el = entry.find("atom:published", ns)
            link_el = entry.find("atom:link[@rel='alternate']", ns)

            uri_el = entry.find("tna:uri", ns)
            uri = uri_el.text.strip() if uri_el is not None and uri_el.text else ""

            citation = ""
            for ident in entry.findall("tna:identifier", ns):
                if ident.get("type") == "ukncn" and ident.text:
                    citation = ident.text.strip()
                    break

            results.append({
                "title": title_el.text if title_el is not None else "",
                "uri": uri,
                "citation": citation,
                "url": link_el.get("href", "") if link_el is not None else "",
                "date": published_el.text[:10] if published_el is not None and published_el.text else "",
            })
        return results
    except Exception:
        return []


def _citation_from_uri(uri: str) -> str:
    """
    Reconstruct a neutral citation from a document URI like ewca/civ/2017/121.
    Returns e.g. '[2017] EWCA Civ 121' or '[2006] EWHC 1201 (Admin)'.
    """
    parts = uri.strip("/").split("/")
    if len(parts) < 3:
        return uri

    # Find year and number (last two numeric segments)
    # URI format: court[/division]/year/number
    number = parts[-1]
    year = parts[-2]

    # Reconstruct court path (everything before year)
    court_path = "/".join(parts[:-2])
    court_info = COURT_MAP.get(court_path)

    if court_info:
        base_court, division = court_info
        if division:
            return f"[{year}] {base_court} {number} ({division})"
        else:
            return f"[{year}] {base_court} {number}"
    else:
        return f"[{year}] {court_path.upper()} {number}"


def uk_to_biblatex(case: dict, cite_key: str = "") -> str:
    """Convert a UK case dict to a @jurisdiction biblatex entry."""
    title = case.get("title", "")
    date = case.get("date", case.get("year", ""))
    citation = case.get("citation", "")

    # Fix judicial review title format: TNA → OSCOLA
    title = _fix_jrapp_title(title)

    # Use just the year for the date field (as in your examples)
    year = date[:4] if date else ""

    if not cite_key:
        cite_key = _sanitise_key(title)

    safe_title = _escape_bibtex(title)

    lines = [
        f"@jurisdiction{{{cite_key},",
        f"\tdate = {{{year}}},",
        f"\tkeywords = {{gb}},",
        f"\tnumber = {{{citation}}},",
        f"\tpagination = {{[]}},",
        f"\ttitle = {{{safe_title}}}}}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# EU Case Law (CELLAR SPARQL + EUR-Lex)
# ---------------------------------------------------------------------------

CELLAR_SPARQL_ENDPOINT = "https://publications.europa.eu/webapi/rdf/sparql"
CDM = "http://publications.europa.eu/ontology/cdm#"

# Virtuoso 8 fix (March 2026): use variable + STR()/FILTER instead of
# direct literal triple patterns. Plain literals no longer match typed
# literals in FILTER comparisons.

CASE_INFO_QUERY = """
PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>

SELECT ?ecli ?date ?court_code ?caseNumber ?parties ?advocate_general
WHERE {{
  ?work cdm:resource_legal_id_celex ?celex .
  FILTER(STR(?celex) = "{celex}")
  OPTIONAL {{ ?work cdm:case-law_ecli ?ecli . }}
  OPTIONAL {{ ?work cdm:work_date_document ?date . }}
  OPTIONAL {{ ?work cdm:case-law_delivered_by_court_formation ?courtUri .
              BIND(REPLACE(STR(?courtUri), "^.*/", "") AS ?court_code) }}
  OPTIONAL {{ ?work cdm:case-law_delivered_by_advocate-general ?agUri .
              ?agUri cdm:agent_name ?advocate_general }}
  OPTIONAL {{
    ?work cdm:resource_legal_number_natural_celex ?caseNumber .
  }}
  OPTIONAL {{
    ?expr cdm:expression_belongs_to_work ?work ;
          cdm:expression_uses_language <http://publications.europa.eu/resource/authority/language/ENG> ;
          cdm:expression_title ?parties .
  }}
}}
LIMIT 1
"""

SEARCH_QUERY = """
PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>

SELECT DISTINCT ?celex ?ecli ?date ?title ?short_parties ?caseNumber
WHERE {{
  ?work cdm:resource_legal_id_celex ?celex .
  FILTER(REGEX(STR(?celex), "^6[0-9]{{4}}(CJ|TJ|FJ|CC)"))
  ?expr cdm:expression_belongs_to_work ?work ;
        cdm:expression_uses_language <http://publications.europa.eu/resource/authority/language/ENG> ;
        cdm:expression_title ?title .
  OPTIONAL {{ ?expr cdm:expression_case-law_parties ?short_parties . }}
  FILTER(CONTAINS(LCASE(STR(?title)), "{search_lower}"))
  OPTIONAL {{ ?work cdm:case-law_ecli ?ecli . }}
  OPTIONAL {{ ?work cdm:work_date_document ?date . }}
  OPTIONAL {{ ?work cdm:resource_legal_number_natural_celex ?caseNumber . }}
}}
ORDER BY DESC(?date)
LIMIT {limit}
"""


def _sparql_query(query: str) -> list:
    """Execute a SPARQL query against the CELLAR endpoint with retry."""
    import time
    max_retries = 2
    for attempt in range(max_retries + 1):
        try:
            encoded = urllib.parse.urlencode({"query": query}).encode("utf-8")
            req = urllib.request.Request(
                CELLAR_SPARQL_ENDPOINT,
                data=encoded,
                headers={
                    "Accept": "application/sparql-results+json",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data.get("results", {}).get("bindings", [])
        except (urllib.error.URLError, OSError) as e:
            # Retry on DNS/network errors (transient)
            if attempt < max_retries:
                time.sleep(1)
                continue
            print(f"CELLAR SPARQL error after {max_retries + 1} attempts: {e}", file=sys.stderr)
            return []
        except Exception as e:
            print(f"CELLAR SPARQL error: {e}", file=sys.stderr)
            return []


def _celex_from_case_number(case_num: str) -> Optional[str]:
    """
    Convert a case number like C-553/07 to a CELEX search pattern.
    CELEX for judgments: 6{year}CJ{number} or 6{year}TJ{number}
    """
    m = re.match(r'C[_-]?(\d+)/(\d{2,4})(?:\s*P)?', case_num.strip(), re.IGNORECASE)
    if m:
        num, year_short = m.group(1), m.group(2)
        if len(year_short) == 2:
            prefix = "20" if int(year_short) < 50 else "19"
            year = prefix + year_short
        else:
            year = year_short
        return f"6{year}CJ{num.zfill(4)}"

    # Try T- cases (General Court)
    m = re.match(r'T[_-]?(\d+)/(\d{2,4})', case_num.strip(), re.IGNORECASE)
    if m:
        num, year_short = m.group(1), m.group(2)
        if len(year_short) == 2:
            prefix = "20" if int(year_short) < 50 else "19"
            year = prefix + year_short
        else:
            year = year_short
        return f"6{year}TJ{num.zfill(4)}"

    return None


def _celex_from_ecli(ecli: str) -> Optional[str]:
    """
    Convert an ECLI like EU:C:2009:293 to a CELEX lookup.
    This requires a SPARQL lookup since ECLI→CELEX isn't a simple mapping.
    """
    # Virtuoso 8 fix: use STR() on ?ecli variable
    query = f"""
    PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
    SELECT ?celex WHERE {{
      ?work cdm:case-law_ecli ?ecli .
      FILTER(STR(?ecli) = "ECLI:{ecli}")
      ?work cdm:resource_legal_id_celex ?celex .
    }} LIMIT 1
    """
    results = _sparql_query(query)
    if results:
        return results[0].get("celex", {}).get("value")
    return None


def _parse_eu_title(title_str: str) -> dict:
    """
    Parse a EUR-Lex title string like:
    "Judgment of the Court ...#Party A v Party B.#Reference...#Case C-553/07."
    Returns dict with parties, case_number.
    """
    parts = title_str.split("#")
    parties = ""
    case_number = ""
    for p in parts:
        p = p.strip().rstrip(".")
        if re.match(r'Case\s+[CT]-', p, re.IGNORECASE):
            case_number = re.sub(r'^Case\s+', '', p, flags=re.IGNORECASE)
        elif " v " in p or " v. " in p:
            parties = p
        elif re.match(r'Joined Cases', p, re.IGNORECASE):
            # Strip "Joined Cases" prefix — the multiple C-numbers suffice
            case_number = re.sub(r'^Joined Cases\s+', '', p, flags=re.IGNORECASE)

    return {"parties": parties, "case_number": case_number}


def eu_lookup_by_celex(celex: str) -> Optional[dict]:
    """Look up an EU case by its CELEX number.
    
    Tries CELLAR SPARQL first; falls back to Formex XML extraction
    if SPARQL returns nothing (timeout, Virtuoso issues, etc.).
    """
    results = _sparql_query(CASE_INFO_QUERY.format(celex=celex))
    if results:
        return _parse_sparql_case_result(celex, results[0])

    # Fallback: try extracting basic info from Formex XML
    return _eu_lookup_formex_fallback(celex)


def _parse_sparql_case_result(celex: str, r: dict) -> dict:
    """Parse a SPARQL result row into a case dict."""
    ecli = r.get("ecli", {}).get("value", "")
    date = r.get("date", {}).get("value", "")
    court_code_raw = r.get("court_code", {}).get("value", "")
    title_raw = r.get("parties", {}).get("value", "")
    case_num_raw = r.get("caseNumber", {}).get("value", "")
    ag_name_raw = r.get("advocate_general", {}).get("value", "")

    # Detect AG opinions from CELEX suffix (CC = conclusions/opinions)
    is_ag_opinion = "CC" in celex

    # Parse the court code — now comes pre-extracted from SPARQL REPLACE()
    institution = ""
    if court_code_raw:
        if court_code_raw.startswith("CHAMB") or court_code_raw == "CJ" or "GRAND" in court_code_raw or court_code_raw == "CC":
            institution = "CJEU"
        elif court_code_raw.startswith("GC") or court_code_raw == "GC":
            institution = "General Court"
        else:
            institution = EU_COURT_MAP.get(court_code_raw, court_code_raw)
    if not institution and ("CJ" in celex or "CC" in celex):
        institution = "CJEU"
    elif not institution and "TJ" in celex:
        institution = "General Court"

    # Parse title to get clean parties and case number
    parsed = _parse_eu_title(title_raw)
    parties = parsed["parties"] or title_raw.split("#")[1] if "#" in title_raw else title_raw
    case_number = parsed["case_number"] or case_num_raw

    # Clean up ECLI
    if ecli.startswith("ECLI:"):
        ecli = ecli[5:]

    # Clean up AG name
    ag_name = ag_name_raw.replace("_", " ").strip() if ag_name_raw else ""
    # If the name is a UUID (fallback extraction failed), discard it
    if ag_name and re.match(r'^[0-9a-f]{8}-', ag_name):
        ag_name = ""
    # If we couldn't get the AG name from SPARQL, try extracting from the title
    if is_ag_opinion and not ag_name:
        ag_match = re.search(r'Opinion of Advocate General\s+(\S+)', title_raw)
        if ag_match:
            ag_name = ag_match.group(1)

    return {
        "celex": celex,
        "ecli": ecli,
        "date": date,
        "institution": institution or "CJEU",
        "case_number": case_number,
        "title": parties.strip().rstrip("."),
        "source": "eu",
        "is_ag_opinion": is_ag_opinion,
        "ag_name": ag_name,
    }


def _eu_lookup_formex_fallback(celex: str) -> Optional[dict]:
    """
    Fallback: extract basic case info from Formex XML when SPARQL fails.
    This is slower (downloads the full Formex zip) but more reliable.
    """
    try:
        url = f"https://publications.europa.eu/resource/celex/{celex}"
        req = urllib.request.Request(url, headers={
            "Accept": "application/zip;mtype=fmx4",
            "Accept-Language": "eng",
        })
        with urllib.request.urlopen(req, timeout=20) as resp:
            if resp.status != 200:
                return None
            data = resp.read()
            zf = zipfile.ZipFile(io.BytesIO(data))
            xml_text = None
            for name in zf.namelist():
                if name.endswith((".xml", ".fmx", ".fmx4")):
                    xml_text = zf.read(name).decode("utf-8", errors="replace")
                    break
            if not xml_text:
                return None

        root = ET.fromstring(xml_text)

        # Extract ECLI
        ecli_el = root.find(".//NO.ECLI")
        ecli = ecli_el.get("ECLI", "") if ecli_el is not None else ""
        if ecli.startswith("ECLI:"):
            ecli = ecli[5:]

        # Extract case number
        case_num_el = root.find(".//NO.CASE")
        case_number = ""
        if case_num_el is not None and case_num_el.text:
            case_number = case_num_el.text.strip()
            # Normalise unicode dashes to ASCII
            case_number = case_number.replace("\u2011", "-").replace("\u2010", "-")

        # Extract date
        date = ""
        # Look for the judgment date in TITLE/TI/P/DATE
        for date_el in root.iter("DATE"):
            iso = date_el.get("ISO", "")
            if iso and len(iso) == 8:
                date = f"{iso[:4]}-{iso[4:6]}-{iso[6:8]}"
                break

        # Extract parties from PARTIES element
        parties = ""
        parties_el = root.find(".//PARTIES")
        if parties_el is not None:
            plaintiff_el = parties_el.find(".//PLAINTIFS")
            defendant_el = parties_el.find(".//DEFENDANTS")
            if plaintiff_el is not None and defendant_el is not None:
                p_text = "".join(plaintiff_el.itertext()).strip()
                d_text = "".join(defendant_el.itertext()).strip()
                if p_text and d_text:
                    parties = f"{p_text} v {d_text}"

        # Fallback: extract from PAGE.HEADER
        if not parties:
            header_el = root.find(".//PAGE.HEADER")
            if header_el is not None:
                for p in header_el.findall("P"):
                    text = "".join(p.itertext()).strip()
                    # The second P in PAGE.HEADER is typically the short name
                    if text and not text.startswith("JUDGMENT") and not text.startswith("OPINION"):
                        parties = text
                        break

        # Determine institution from CELEX
        if "CJ" in celex or "CC" in celex:
            institution = "CJEU"
        elif "TJ" in celex:
            institution = "General Court"
        else:
            institution = "CJEU"

        is_ag = "CC" in celex

        return {
            "celex": celex,
            "ecli": ecli,
            "date": date,
            "institution": institution,
            "case_number": case_number,
            "title": parties.strip().rstrip("."),
            "source": "eu",
            "is_ag_opinion": is_ag,
            "ag_name": "",
        }
    except Exception as e:
        print(f"Formex fallback error for {celex}: {e}", file=sys.stderr)
        return None


def eu_search(query: str, limit: int = 10) -> list:
    """Search EU case law by keyword (includes AG opinions).
    
    If query looks like a case number, CELEX, or ECLI, does a direct
    lookup instead of a slow text search.
    """
    query_stripped = query.strip()

    # Shortcut: direct CELEX (starts with 6 + 4 digits + court suffix)
    if re.match(r'^6\d{4}[A-Z]', query_stripped):
        result = eu_lookup_by_celex(query_stripped)
        if result:
            return [result]
        return []

    # Shortcut: ECLI
    ecli_match = re.match(r'(?:ECLI:)?(EU:[CT]:\d{4}:\d+)', query_stripped, re.IGNORECASE)
    if ecli_match:
        ecli = ecli_match.group(1)
        celex = _celex_from_ecli(ecli)
        if celex:
            result = eu_lookup_by_celex(celex)
            if result:
                return [result]
        return []

    # Shortcut: case number (C-21/23, T-123/20, etc.)
    case_num_match = re.match(r'^[CT][_-]?\d+/\d{2,4}(?:\s*P)?$', query_stripped, re.IGNORECASE)
    if case_num_match:
        celex = _celex_from_case_number(query_stripped)
        if celex:
            result = eu_lookup_by_celex(celex)
            if result:
                return [result]
            # Also try CC suffix for AG opinion
            alt_celex = celex[:5] + "CC" + celex[7:]
            result = eu_lookup_by_celex(alt_celex)
            if result:
                return [result]

    results = _sparql_query(SEARCH_QUERY.format(
        search_lower=query.lower().replace('"', '\\"'),
        limit=limit,
    ))
    seen = set()
    cases = []
    for r in results:
        celex = r.get("celex", {}).get("value", "")
        if celex in seen:
            continue
        seen.add(celex)

        ecli = r.get("ecli", {}).get("value", "")
        date = r.get("date", {}).get("value", "")
        title_raw = r.get("title", {}).get("value", "")
        short_parties = r.get("short_parties", {}).get("value", "")
        case_num = r.get("caseNumber", {}).get("value", "")

        # Prefer the short parties field if available
        if short_parties:
            parties = short_parties.strip().rstrip(".")
            parsed = _parse_eu_title(title_raw)
            case_number = parsed["case_number"] or case_num
        else:
            parsed = _parse_eu_title(title_raw)
            parties = parsed["parties"] or title_raw
            parties = parties.strip().rstrip(".")
            case_number = parsed["case_number"] or case_num

        if ecli.startswith("ECLI:"):
            ecli = ecli[5:]

        is_ag = "CC" in celex

        cases.append({
            "celex": celex,
            "ecli": ecli,
            "date": date,
            "case_number": case_number,
            "title": parties,
            "source": "eu",
            "is_ag_opinion": is_ag,
        })
    return cases


def eu_lookup(query: str) -> Optional[dict]:
    """
    Smart EU lookup: accepts CELEX, ECLI, or case number.
    """
    query = query.strip()

    # Direct CELEX (starts with 6, followed by digits + court suffix)
    if re.match(r'^6\d{4}[CT]', query) or re.match(r'^6\d{4}CC', query):
        return eu_lookup_by_celex(query)

    # ECLI (contains EU:C: or EU:T:)
    ecli_match = re.match(r'(?:ECLI:)?(EU:[CT]:\d{4}:\d+)', query, re.IGNORECASE)
    if ecli_match:
        ecli = ecli_match.group(1)
        celex = _celex_from_ecli(ecli)
        if celex:
            return eu_lookup_by_celex(celex)

    # Case number (C-553/07, T-123/20, etc.)
    if re.match(r'[CT][_-]?\d+/\d{2,4}', query, re.IGNORECASE):
        celex = _celex_from_case_number(query)
        if celex:
            result = eu_lookup_by_celex(celex)
            if result:
                return result
            # If exact CELEX didn't work, the case number might have
            # a different CELEX suffix (opinion vs judgment). Try search.
            # Try with different suffixes
            for suffix in ["CJ", "TJ", "CC", "CO"]:
                alt_celex = celex[:5] + suffix + celex[7:]
                result = eu_lookup_by_celex(alt_celex)
                if result:
                    return result

    return None


def eu_to_biblatex(case: dict, cite_key: str = "") -> str:
    """Convert an EU case dict to a @jurisdiction biblatex entry.

    AG Opinions get special treatment per OSCOLA:
    - ecli field has ', Opinion of AG [Name]' appended
    - pagination = {point} instead of being omitted
    """
    title = case.get("title", "")
    date = case.get("date", "")
    ecli = case.get("ecli", "")
    institution = case.get("institution", "CJEU")
    case_number = case.get("case_number", "")
    is_ag = case.get("is_ag_opinion", False)
    ag_name = case.get("ag_name", "")

    if not cite_key:
        cite_key = _sanitise_key(title)
        # For AG opinions, append AG to the key to distinguish from judgment
        if is_ag:
            cite_key = cite_key + "AG"

    safe_title = _escape_bibtex(title)

    # For AG opinions, append the AG attribution to the ECLI
    ecli_field = ecli
    if is_ag and ag_name:
        ecli_field = f"{ecli}, Opinion of AG {ag_name}"

    lines = [
        f"@jurisdiction{{{cite_key},",
        f"\tdate = {{{date}}},",
        f"\tecli = {{{ecli_field}}},",
        f"\tinstitution = {{{institution}}},",
        f"\tkeywords = {{eu}},",
        f"\tnumber = {{{case_number}}},",
    ]
    # AG opinions use 'point' pagination; judgments have no pagination field
    if is_ag:
        lines.append(f"\tpagination = {{point}},")
    lines.extend([
        f"\ttitle = {{{safe_title}}}}}",
    ])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# EU Legislation (CELLAR SPARQL — directives, regulations, decisions, treaties)
# ---------------------------------------------------------------------------

# Resource-type URIs from the Publications Office Named Authority List.
# These match the filters used by the eurlex R package (michalovadek/eurlex).
EU_LEG_TYPES = {
    "directive": [
        "http://publications.europa.eu/resource/authority/resource-type/DIR",
        "http://publications.europa.eu/resource/authority/resource-type/DIR_IMPL",
        "http://publications.europa.eu/resource/authority/resource-type/DIR_DEL",
    ],
    "regulation": [
        "http://publications.europa.eu/resource/authority/resource-type/REG",
        "http://publications.europa.eu/resource/authority/resource-type/REG_IMPL",
        "http://publications.europa.eu/resource/authority/resource-type/REG_FINANC",
        "http://publications.europa.eu/resource/authority/resource-type/REG_DEL",
    ],
    "decision": [
        "http://publications.europa.eu/resource/authority/resource-type/DEC",
        "http://publications.europa.eu/resource/authority/resource-type/DEC_IMPL",
        "http://publications.europa.eu/resource/authority/resource-type/DEC_DEL",
        "http://publications.europa.eu/resource/authority/resource-type/DEC_ENTSCHEID",
    ],
    "treaty": [],  # treaties use sector 1 CELEX, handled separately
}

# Combine all for "any" searches
EU_LEG_ALL_TYPES = []
for _types in EU_LEG_TYPES.values():
    EU_LEG_ALL_TYPES.extend(_types)


def _build_type_filter(leg_type: str = "any") -> str:
    """Build a SPARQL FILTER clause for EU legislation resource types."""
    if leg_type == "treaty":
        # Treaties are CELEX sector 1, not filtered by resource-type
        return 'FILTER(REGEX(STR(?celex), "^1"))'
    types = EU_LEG_TYPES.get(leg_type, EU_LEG_ALL_TYPES) if leg_type != "any" else EU_LEG_ALL_TYPES
    if not types:
        return ""
    conditions = " || ".join(f'?type = <{t}>' for t in types)
    return f"FILTER({conditions})"


def eu_legislation_search(query: str, leg_type: str = "any", limit: int = 15) -> list:
    """
    Search EU legislation via CELLAR SPARQL.

    leg_type: 'any', 'directive', 'regulation', 'decision', 'treaty'

    Uses a lightweight SPARQL query (CELEX sector filter + title CONTAINS only,
    no resource-type joins) to avoid CELLAR timeouts on text searches.
    Instrument type is determined from the CELEX descriptor after the fact.
    """
    query = query.strip()

    # Fast path: if query looks like a CELEX number, do a direct lookup
    if re.match(r'^[31]\d{4}[A-Z]', query):
        leg = eu_legislation_lookup(query)
        if leg:
            return [leg]
        return []

    query_escaped = query.lower().replace('"', '\\"')

    # Determine CELEX sector filter
    if leg_type == "treaty":
        celex_filter = 'FILTER(REGEX(STR(?celex), "^1"))'
    else:
        # Sector 3 = legislation. Optionally narrow by descriptor letter.
        descriptor_map = {
            "directive": "L",
            "regulation": "R",
            "decision": "D",
        }
        descriptor = descriptor_map.get(leg_type)
        if descriptor:
            celex_filter = f'FILTER(REGEX(STR(?celex), "^3\\\\d{{4}}{descriptor}"))'
        else:
            celex_filter = 'FILTER(REGEX(STR(?celex), "^3"))'

    # Lightweight query: no resource-type joins, no corrigendum filter,
    # no optional OJ fields — just CELEX + title + date.
    # This is fast because CELLAR can use its CELEX index + text scan.
    sparql = f"""
PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
SELECT DISTINCT ?celex ?date ?title
WHERE {{
  ?work cdm:resource_legal_id_celex ?celex .
  {celex_filter}
  ?expr cdm:expression_belongs_to_work ?work ;
        cdm:expression_uses_language <http://publications.europa.eu/resource/authority/language/ENG> ;
        cdm:expression_title ?title .
  FILTER(CONTAINS(LCASE(STR(?title)), "{query_escaped}"))
  OPTIONAL {{ ?work cdm:work_date_document ?date . }}
}}
ORDER BY DESC(?date)
LIMIT {limit}
"""

    results = _sparql_query(sparql)
    seen = set()
    items = []
    for r in results:
        celex = r.get("celex", {}).get("value", "")
        if celex in seen:
            continue
        seen.add(celex)
        # Skip corrigenda (CELEX contains "R(" suffix)
        if "R(" in celex:
            continue
        title = r.get("title", {}).get("value", "")
        date = r.get("date", {}).get("value", "")

        # Determine the instrument type from CELEX descriptor
        instr_type = _celex_to_instrument_type(celex)

        items.append({
            "celex": celex,
            "title": title,
            "date": date[:10] if date else "",
            "instrument_type": instr_type,
            "in_force": None,  # Not queried to keep SPARQL fast
            "source": "euleg",
        })
    return items


def _celex_to_instrument_type(celex: str) -> str:
    """Determine instrument type from CELEX number descriptor letter."""
    if len(celex) < 6:
        return "legislation"
    descriptor = celex[5:6]  # The letter(s) after the 4-digit year
    mapping = {
        "L": "directive",
        "R": "regulation",
        "D": "decision",
        "E": "treaty",
        "M": "treaty",  # TEU/TFEU consolidated
    }
    # Check sector 1 for treaties
    if celex.startswith("1"):
        return "treaty"
    return mapping.get(descriptor, "legislation")


def _extract_instrument_number(title: str, celex: str) -> str:
    """
    Extract the instrument number from the title.
    E.g. from "Council Directive 2002/60/EC ..." → "2002/60/EC"
    E.g. from "Regulation (EU) 2016/679 ..." → "2016/679"
    """
    # Try patterns like 2016/679, 2002/60/EC, No 593/2008
    m = re.search(r'(?:No\s+)?(\d{2,4}/\d+(?:/\w+)?)', title)
    if m:
        return m.group(1)
    # Try pattern like (EU) 2024/1689
    m = re.search(r'\((?:EU|EC|EEC)\)\s+(\d{4}/\d+)', title)
    if m:
        return m.group(1)
    # Fall back to the CELEX number's trailing digits
    if len(celex) > 6:
        num_part = celex[6:]
        return num_part.lstrip("0") or num_part
    return ""


def _extract_oj_from_title(title: str) -> dict:
    """
    Try to extract OJ reference from the instrument title.
    Many titles don't contain it — it's in the metadata.
    Returns dict with journaltitle, series, volume, pages (all may be empty).
    """
    # OJ references are usually not in the title, but in metadata
    # We'll get them from CELLAR metadata or EUR-Lex
    return {"journaltitle": "", "series": "", "volume": "", "pages": ""}


def eu_legislation_lookup(celex: str) -> Optional[dict]:
    """
    Look up a specific EU legislative act by CELEX number.
    Returns metadata for biblatex @legislation entry.
    """
    # Get title and date from CELLAR
    sparql = f"""
PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
SELECT ?title ?date ?force ?oj_id
WHERE {{
  ?work cdm:resource_legal_id_celex ?celex_val .
  FILTER(STR(?celex_val) = "{celex}")
  ?expr cdm:expression_belongs_to_work ?work ;
        cdm:expression_uses_language <http://publications.europa.eu/resource/authority/language/ENG> ;
        cdm:expression_title ?title .
  OPTIONAL {{ ?work cdm:work_date_document ?date . }}
  OPTIONAL {{ ?work cdm:resource_legal_in-force ?force . }}
  OPTIONAL {{ ?work cdm:work_part_of_work ?oj .
              ?oj cdm:resource_legal_id_celex ?oj_id .
              FILTER(REGEX(STR(?oj_id), "^C")) }}
}}
LIMIT 1
"""
    results = _sparql_query(sparql)
    if not results:
        return None

    r = results[0]
    title = r.get("title", {}).get("value", "")
    date = r.get("date", {}).get("value", "")
    force = r.get("force", {}).get("value", "")

    instr_type = _celex_to_instrument_type(celex)
    number = _extract_instrument_number(title, celex)

    # Try to get OJ reference from a separate SPARQL query
    oj = _fetch_oj_reference(celex)

    return {
        "celex": celex,
        "title": title,
        "date": date[:10] if date else "",
        "instrument_type": instr_type,
        "number": number,
        "in_force": force.lower() == "true" if force else None,
        "oj_journal": oj.get("journal", "OJ"),
        "oj_series": oj.get("series", ""),
        "oj_volume": oj.get("volume", ""),
        "oj_pages": oj.get("pages", ""),
        "source": "euleg",
    }


def _fetch_oj_reference(celex: str) -> dict:
    """
    Get Official Journal reference for EU legislation.

    Uses a layered strategy tested against real CELLAR data:
      1. Formex XML (most reliable — contains structured OJ metadata)
         - formex-05 (pre-2023): <PUBLICATION.REF> has <COLL>, <NO.OJ>;
           <DOC.MAIN.PUB> has <PAGE.FIRST>
         - formex-06 (2024+): <BIB.OJ> has <COLL>; NO per-document page
           numbers exist (new OJ format dropped them — OSCOLA says to omit
           pages and volume for these issues)
      2. CELEX descriptor fallback (always works for series letter)

    Returns dict with journal, series, volume, pages (any may be empty).
    """
    empty = {"journal": "OJ", "series": "", "volume": "", "pages": ""}

    # ----- Strategy 1: Formex XML -----
    try:
        formex_url = f"https://publications.europa.eu/resource/celex/{celex}"
        req = urllib.request.Request(
            formex_url,
            headers={
                "Accept": "application/zip;mtype=fmx4",
                "Accept-Language": "eng",
            },
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            if resp.status == 200:
                data = resp.read()
                zf = zipfile.ZipFile(io.BytesIO(data))
                xml_text = None
                for name in zf.namelist():
                    if name.endswith((".xml", ".fmx", ".fmx4")):
                        xml_text = zf.read(name).decode("utf-8", errors="replace")
                        break
                if not xml_text:
                    # Zip had no XML — fall through to strategy 2
                    raise ValueError("No XML in zip")

                root = ET.fromstring(xml_text)
                oj = _parse_formex_oj(root)
                if oj["series"]:
                    return oj
    except Exception:
        pass  # Formex unavailable (treaties, very old docs) — fall through

    # ----- Strategy 2: CELEX descriptor fallback -----
    # The CELEX descriptor letter tells us the OJ series:
    #   L-series = Directives, Regulations, Decisions (descriptor L, R, D)
    #   C-series = resolutions, opinions, etc.
    # Sector 1 (treaties) → C series; Sector 3 → usually L
    series = _celex_to_oj_series(celex)
    return {"journal": "OJ", "series": series, "volume": "", "pages": ""}


def _parse_formex_oj(root: ET.Element) -> dict:
    """
    Extract OJ reference from a Formex XML root element.
    Handles both formex-05 (<PUBLICATION.REF>) and formex-06 (<BIB.OJ>).
    """
    result = {"journal": "OJ", "series": "", "volume": "", "pages": ""}

    # ----- formex-05 style: <PUBLICATION.REF> with <COLL>, <NO.OJ> -----
    pub_ref = root.find(".//PUBLICATION.REF")
    if pub_ref is not None:
        coll_el = pub_ref.find("COLL")
        oj_el = pub_ref.find("NO.OJ")
        if coll_el is not None and coll_el.text:
            result["series"] = coll_el.text.strip()
        if oj_el is not None and oj_el.text:
            # NO.OJ may be zero-padded like "094" — strip leading zeros
            vol = oj_el.text.strip().lstrip("0") or "0"
            result["volume"] = vol

        # Page comes from <DOC.MAIN.PUB><PAGE.FIRST>
        main_pub = root.find(".//DOC.MAIN.PUB")
        if main_pub is not None:
            page_el = main_pub.find("PAGE.FIRST")
            if page_el is not None and page_el.text:
                result["pages"] = page_el.text.strip()

        return result

    # ----- formex-06 style: <BIB.OJ> with <COLL> -----
    # Post-2023 OJ format: no issue numbers, no per-document page numbers.
    # OSCOLA says to omit pages and volume in this case — just give series.
    bib_oj = root.find(".//BIB.OJ")
    if bib_oj is not None:
        coll_el = bib_oj.find("COLL")
        if coll_el is not None and coll_el.text:
            result["series"] = coll_el.text.strip()
        # volume and pages deliberately left empty — new OJ format
        return result

    # ----- Fallback within Formex: look for <COLL> anywhere -----
    coll_el = root.find(".//COLL")
    if coll_el is not None and coll_el.text:
        result["series"] = coll_el.text.strip()

    return result


def _celex_to_oj_series(celex: str) -> str:
    """
    Infer OJ series letter from CELEX number.
    Sector 3 legislation is almost always L-series.
    Sector 1 treaties are C-series.
    """
    if celex.startswith("1"):
        return "C"
    if celex.startswith("3"):
        return "L"
    return ""


def eu_legislation_to_biblatex(leg: dict, cite_key: str = "") -> str:
    """
    Convert EU legislation dict to @legislation biblatex entry.

    Follows OSCOLA / biblatex-oscola format as documented:
    - title: full title including number and enacting institution
    - type: directive / regulation / decision (lowercase)
    - number: instrument number (e.g. 2002/60/EC)
    - journaltitle: OJ
    - series: L or C
    - volume: OJ issue number
    - pages: starting page
    - date: year of publication
    - keywords: eu
    - pagination: article (for article-level pinpoints)
    - entrysubtype: directive / regulation / decision (for indexing)
    """
    title = leg.get("title", "")
    date = leg.get("date", "")
    instr_type = leg.get("instrument_type", "")
    number = leg.get("number", "")
    oj_journal = leg.get("oj_journal", "OJ")
    oj_series = leg.get("oj_series", "")
    oj_volume = leg.get("oj_volume", "")
    oj_pages = leg.get("oj_pages", "")
    celex = leg.get("celex", "")

    if not cite_key:
        # For legislation, use the number as cite key base
        if number:
            cite_key = re.sub(r'[^a-zA-Z0-9]', '', number)
            if not cite_key:
                cite_key = _sanitise_key(title)
        else:
            cite_key = _sanitise_key(title)

    safe_title = _escape_bibtex(title)
    year = date[:4] if date else ""

    lines = [
        f"@legislation{{{cite_key},",
        f"\ttitle = {{{safe_title}}},",
        f"\tdate = {{{year}}},",
    ]

    if instr_type and instr_type not in ("legislation", "treaty"):
        lines.append(f"\ttype = {{{instr_type}}},")
        lines.append(f"\tentrysubtype = {{{instr_type}}},")

    if instr_type == "treaty":
        lines.append(f"\tentrysubtype = {{eu-treaty}},")

    if number:
        lines.append(f"\tnumber = {{{number}}},")

    lines.append(f"\tjournaltitle = {{{oj_journal}}},")

    if oj_series:
        lines.append(f"\tseries = {{{oj_series}}},")

    if oj_volume:
        lines.append(f"\tissue = {{{oj_volume}}},")
    # Note: newer OJ issues don't have page numbers — omit if absent per OSCOLA
    if oj_pages:
        lines.append(f"\tpages = {{{oj_pages}}},")

    lines.append(f"\tkeywords = {{eu}},")
    lines.append(f"\tpagination = {{article}},")
    lines.append("}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# UK Legislation (legislation.gov.uk API)
# ---------------------------------------------------------------------------

# Type → (OSCOLA keywords, entrysubtype, pagination, number_prefix)
UK_LEG_TYPE_MAP = {
    # Primary — Westminster
    "ukpga": ("en", "primary", "section", ""),
    "ukla":  ("en", "primary", "section", ""),
    "ukppa": ("en", "primary", "section", ""),
    "ukcm":  ("en", "primary", "section", ""),
    # Primary — Scotland
    "asp":   ("sc", "primary", "section", "asp"),
    "aosp":  ("sc", "primary", "section", ""),
    # Primary — Wales
    "asc":   ("cy", "primary", "section", "asc"),
    "anaw":  ("cy", "primary", "section", "anaw"),
    "mwa":   ("cy", "primary", "section", "nawm"),
    # Primary — Northern Ireland
    "nia":   ("ni", "primary", "section", "c"),
    "apni":  ("ni", "primary", "section", "c"),
    "mnia":  ("ni", "primary", "section", ""),
    # Secondary — UK
    "uksi":  ("en", "secondary", "regulation", "SI"),
    "uksro": ("en", "secondary", "rule", r"SR\&O"),
    "ukci":  ("en", "secondary", "regulation", ""),
    "ukmo":  ("en", "secondary", "regulation", ""),
    # Secondary — Scotland
    "ssi":   ("sc", "secondary", "regulation", "SSI"),
    # Secondary — Wales
    "wsi":   ("cy", "secondary", "regulation", "WSI"),
    # Secondary — Northern Ireland
    "nisr":  ("ni", "secondary", "regulation", "SR"),
    "nisi":  ("ni", "secondary", "article", ""),
    "nisro": ("ni", "secondary", "rule", ""),
    # Draft
    "ukdsi": ("gb", "secondary", "regulation", ""),
    "sdsi":  ("sc", "secondary", "regulation", ""),
    "nidsr": ("ni", "secondary", "regulation", ""),
    "wdsi":  ("cy", "secondary", "regulation", ""),
    # EU retained / assimilated law (on legislation.gov.uk = UK domestic law)
    "eur":   ("eu, assimilated", "secondary", "article", ""),
    "eudn":  ("eu, assimilated", "secondary", "article", ""),
    "eudr":  ("eu, assimilated", "secondary", "article", ""),
}

UK_LEG_API = "https://www.legislation.gov.uk"


def _uk_leg_title_strip_year(title: str) -> str:
    """
    Strip the trailing year from a legislation title for the bib entry.
    OSCOLA expects just 'Unfair Contract Terms Act' not 'Unfair Contract Terms Act 1977'
    — the year goes in the date field.

    But only strip if the title ends with a 4-digit year.
    SI titles starting with 'The' also lose the 'The'.
    """
    s = title.strip()
    # Remove leading 'The ' for SIs
    if s.startswith("The "):
        s = s[4:]
    # Remove trailing year (e.g. "Data Protection Act 2018" → "Data Protection Act")
    m = re.match(r'^(.+?)\s+(\d{4})\s*$', s)
    if m:
        return m.group(1).strip(), m.group(2)
    return s, ""


def _uk_leg_format_number(leg_type: str, year: int, number: int) -> str:
    """
    Format the legislation number for the bib entry.
    Primary: 'asp 2' for Scottish Acts, 'asc 2' for Welsh, 'c 5' for NI, empty for ukpga
    Secondary: 'SI 2004/3166', 'SSI 2020/123', etc.
    """
    info = UK_LEG_TYPE_MAP.get(leg_type, ("en", "primary", "section", ""))
    prefix = info[3]
    subtype = info[1]

    if not prefix:
        return ""

    if subtype == "secondary":
        # SI YYYY/NNNN format
        return f"{prefix} {year}\\slash {number}"
    else:
        # Primary devolved: asp 2, asc 2, c 5
        return f"{prefix} {number}"


def uk_legislation_search(query: str, limit: int = 15) -> list:
    """
    Search UK legislation via legislation.gov.uk identifier search.
    Uses the /id?title= endpoint which has a fast text index.
    """
    try:
        encoded_title = urllib.parse.quote(query, safe="")
        url = f"{UK_LEG_API}/id?title={encoded_title}"
        req = urllib.request.Request(url, headers={"Accept": "application/xhtml+xml, text/html"})
        # Don't follow redirects — we want the 300 Multiple Choices response
        # or the 301 redirect to a single result
        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)
        opener = urllib.request.build_opener(_NoRedirect)

        try:
            resp = opener.open(req, timeout=15)
            # 200 = single result page, parse it
            html = resp.read().decode("utf-8", errors="replace")
            final_url = resp.url
            return _parse_uk_leg_search_html(html, final_url, limit)
        except urllib.error.HTTPError as e:
            if e.code in (300, 301, 303):
                # 300 Multiple Choices: parse the HTML list
                # 301/303: single result redirect
                if e.code in (301, 303):
                    location = e.headers.get("Location", "")
                    if location:
                        return _uk_leg_result_from_redirect(location)
                # Read the 300 response body
                html = e.read().decode("utf-8", errors="replace")
                return _parse_uk_leg_search_html(html, url, limit)
            raise
    except Exception as e:
        print(f"UK legislation search error: {e}", file=sys.stderr)
        return []


def _parse_uk_leg_search_html(html: str, base_url: str, limit: int) -> list:
    """Parse the HTML response from legislation.gov.uk identifier search."""
    results = []
    # Extract links from <li><a href="...">Title</a></li>
    for m in re.finditer(r'<a\s+href="(/id/[^"]+)"[^>]*>([^<]+)</a>', html):
        if len(results) >= limit:
            break
        path = m.group(1)  # e.g. /id/ukpga/1977/50
        title = m.group(2).strip()
        # Parse type/year/number from path
        parsed = _parse_uk_leg_path(path)
        if parsed:
            results.append({
                "title": title,
                "leg_type": parsed["type"],
                "year": parsed["year"],
                "number": parsed["number"],
                "source": "ukleg",
            })
    return results


def _uk_leg_result_from_redirect(location: str) -> list:
    """Create a single result from a redirect URL."""
    parsed = _parse_uk_leg_path(location)
    if not parsed:
        return []
    # We need to fetch the title from metadata
    try:
        meta_url = f"{UK_LEG_API}/{parsed['type']}/{parsed['year']}/{parsed['number']}/data.xml"
        xml_text = _fetch_text(meta_url, headers={"Accept": "application/xml"})
        root = ET.fromstring(xml_text)
        # Title is in <dc:title> or <ukm:Title>
        ns = {"dc": "http://purl.org/dc/elements/1.1/"}
        title_el = root.find(".//dc:title", ns)
        title = title_el.text.strip() if title_el is not None and title_el.text else ""
        if not title:
            title = f"{parsed['type'].upper()} {parsed['year']}/{parsed['number']}"
    except Exception:
        title = f"{parsed['type'].upper()} {parsed['year']}/{parsed['number']}"

    return [{
        "title": title,
        "leg_type": parsed["type"],
        "year": parsed["year"],
        "number": parsed["number"],
        "source": "ukleg",
    }]


def _parse_uk_leg_path(path: str) -> Optional[dict]:
    """Parse a legislation.gov.uk path like /id/ukpga/1977/50 into components."""
    # Strip /id/ prefix if present
    path = re.sub(r'^https?://[^/]+', '', path)
    path = path.rstrip("/")
    if path.startswith("/id/"):
        path = path[4:]
    elif path.startswith("/"):
        path = path[1:]

    parts = path.split("/")
    if len(parts) < 3:
        return None

    leg_type = parts[0]
    year_str = parts[1]
    number_str = parts[2]

    try:
        year = int(year_str)
        number = int(number_str)
    except ValueError:
        return None

    return {"type": leg_type, "year": year, "number": number}


def uk_legislation_lookup(leg_type: str, year: int, number: int) -> Optional[dict]:
    """
    Look up UK legislation by type/year/number via legislation.gov.uk metadata API.
    """
    try:
        # Fetch metadata JSON
        url = f"{UK_LEG_API}/{leg_type}/{year}/{number}/data.xml"
        xml_text = _fetch_text(url, headers={"Accept": "application/xml"})
        root = ET.fromstring(xml_text)

        ns = {
            "dc": "http://purl.org/dc/elements/1.1/",
            "ukm": "http://www.legislation.gov.uk/namespaces/metadata",
            "atom": "http://www.w3.org/2005/Atom",
        }

        # Title
        title_el = root.find(".//dc:title", ns)
        title = title_el.text.strip() if title_el is not None and title_el.text else ""

        return {
            "title": title,
            "leg_type": leg_type,
            "year": year,
            "number": number,
            "source": "ukleg",
        }
    except Exception as e:
        print(f"UK legislation lookup error: {e}", file=sys.stderr)
        return None


def uk_legislation_to_biblatex(leg: dict, cite_key: str = "") -> str:
    """
    Convert UK legislation dict to @legislation biblatex entry.

    Follows OSCOLA / biblatex-oscola format:
    - Primary: title (without year), date, entrysubtype=primary, pagination=section
    - Secondary: title (without year), date, SI number, entrysubtype=secondary, pagination=regulation
    - Devolved: keywords=sc/cy/ni, number=asp N / asc N / c N
    """
    title = leg.get("title", "")
    leg_type = leg.get("leg_type", "ukpga")
    year = leg.get("year", 0)
    number = leg.get("number", 0)

    type_info = UK_LEG_TYPE_MAP.get(leg_type, ("en", "primary", "section", ""))
    keywords, subtype, pagination, _ = type_info

    # Strip year from title and extract it
    title_clean, title_year = _uk_leg_title_strip_year(title)
    date_year = title_year or str(year)

    if not cite_key:
        # Generate key from title words + year
        words = re.findall(r'[A-Za-z]+', title_clean)
        key_parts = [w.lower() for w in words[:3] if w.lower() not in ("the", "of", "and", "for", "in")]
        cite_key = "".join(key_parts) + str(year)[-2:] if key_parts else f"leg{year}"

    safe_title = _escape_bibtex(title_clean)

    # Format number
    num_str = _uk_leg_format_number(leg_type, year, number)

    lines = [
        f"@legislation{{{cite_key},",
        f"\ttitle = {{{safe_title}}},",
        f"\tdate = {{{date_year}}},",
        f"\tentrysubtype = {{{subtype}}},",
        f"\tpagination = {{{pagination}}},",
        f"\tkeywords = {{{keywords}}},",
    ]

    if num_str:
        lines.append(f"\tnumber = {{{num_str}}},")

    lines.append("}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# ECHR Case Law (via echr_extractor / HUDOC)
# ---------------------------------------------------------------------------

# The echr_extractor package handles HUDOC API queries. We wrap it to
# produce OSCOLA-compliant @jurisdiction entries. The package is optional —
# if not installed, ECHR functions return empty results with a warning.

ECHR_FIELDS = [
    "docname", "appno", "judgementdate", "decisiondate", "kpdate",
    "doctype", "doctypebranch",
    "publishedby", "externalsources", "scl",
]

def _ensure_echr_extractor():
    """Check if echr_extractor is available. Returns get_echr or None with a message."""
    try:
        from echr_extractor import get_echr
        return get_echr
    except ImportError:
        return None


ECHR_MISSING_MSG = (
    "ECHR search requires the echr-extractor package. "
    "Install it with: pip install echr-extractor"
)


def _clean_echr_title(docname: str) -> str:
    """
    Clean HUDOC docname to standard 'Party A v Party B' format.
    Strips 'CASE OF ', '(MERITS)', '(JUST SATISFACTION)', etc.
    """
    if not docname:
        return ""
    s = docname.strip()
    # Remove "CASE OF " prefix
    s = re.sub(r'^CASE\s+OF\s+', '', s, flags=re.IGNORECASE)
    # Remove bracketed suffixes like (MERITS), (PRELIMINARY OBJECTIONS), etc.
    s = re.sub(r'\s*\([^)]*(?:MERIT|OBJECTION|SATISFACTION|STRIKING|REVISION|INTERPRETATION|ARTICLE\s+50)[^)]*\)', '', s, flags=re.IGNORECASE)
    # Normalise "v." to "v"
    s = re.sub(r'\bv\.\s', 'v ', s)
    # Title case (HUDOC often uses ALL CAPS)
    # Check if most alpha chars are uppercase (allowing for 'v' connector)
    alpha_chars = [c for c in s if c.isalpha() and c.lower() != 'v']
    is_mostly_upper = alpha_chars and sum(1 for c in alpha_chars if c.isupper()) > len(alpha_chars) * 0.7
    if is_mostly_upper:
        # Convert to title case, preserving 'v' as lowercase
        words = s.split()
        result = []
        for i, w in enumerate(words):
            if w.lower() == 'v' or w.lower() == 'v.':
                result.append('v')
            elif w.lower() in ('the', 'of', 'and', 'for') and i > 0:
                result.append(w.lower())
            else:
                result.append(w.capitalize())
        s = ' '.join(result)
    return s.strip()


def _detect_echr_institution(row: dict) -> str:
    """Determine if ECtHR or Commission from HUDOC data."""
    doctype = str(row.get("doctype", "")).upper()
    branch = str(row.get("doctypebranch", "")).upper()
    # Commission doctypes
    if doctype in ("HEDEC", "HFDEC", "COMOLD", "COMDEC"):
        return "Commission"
    if "COMMISSION" in branch:
        return "Commission"
    return "ECtHR"


def _parse_echr_reporter(row: dict) -> Optional[dict]:
    """
    Parse official reporter citation from HUDOC metadata.
    Returns dict with reporter, volume, pages — or None if unreported.
    """
    # Check multiple columns for reporter strings
    texts = []
    for col in ("publishedby", "externalsources", "scl"):
        val = row.get(col, "")
        if val and str(val) != "nan":
            texts.append(str(val))
    combined = " | ".join(texts)

    if not combined:
        return None

    # Series A: "Series A no. 122" or "Series A, no. 122"
    m = re.search(r'Series\s+A[,]?\s*no\.?\s*(\d+)', combined, re.IGNORECASE)
    if m:
        return {"reporter": "Series A", "pages": m.group(1), "volume": ""}

    # ECHR Reports: "Reports of Judgments and Decisions YYYY-VOL" or "ECHR YYYY-VOL"
    # e.g. "Reports of Judgments and Decisions 1998-VIII" or "ECHR 2003-XI, p. 3124"
    m = re.search(
        r'(?:Reports?\s+(?:of\s+)?Judgments?\s+and\s+Decisions?\s+|ECHR\s+)'
        r'(\d{4})[- ]*([IVXLC]+)',
        combined, re.IGNORECASE,
    )
    if m:
        year_str = m.group(1)
        volume_roman = m.group(2)
        # Try to find page number
        page = ""
        page_match = re.search(r'(?:p\.\s*|,\s*)(\d+)', combined[m.end():])
        if page_match:
            page = page_match.group(1)
        # Convert roman numeral volume to integer
        roman_map = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100}
        vol_int = 0
        for i, ch in enumerate(volume_roman):
            val = roman_map.get(ch, 0)
            if i + 1 < len(volume_roman) and val < roman_map.get(volume_roman[i + 1], 0):
                vol_int -= val
            else:
                vol_int += val
        return {"reporter": "ECHR", "date_year": year_str,
                "volume": str(vol_int), "pages": page}

    # Commission: Decisions and Reports — "D.R. 64, p. 188" or "DR 64 p. 188"
    m = re.search(r'D\.?R\.?\s*(\d+)[\s,]*(?:p\.?\s*)?(\d+)?', combined, re.IGNORECASE)
    if m:
        return {"reporter": "DR", "volume": m.group(1),
                "pages": m.group(2) or "", "journaltitle": "DR"}

    return None


def _clean_appno(appno: str) -> str:
    """Clean application number: take first if multiple, strip whitespace."""
    if not appno:
        return ""
    # Split on semicolons (multiple app numbers)
    parts = [p.strip() for p in str(appno).split(";") if p.strip()]
    return parts[0] if parts else ""


def _normalise_echr_date(date_str: str) -> str:
    """
    Normalise HUDOC date to YYYY-MM-DD format.
    Handles:
      - '2006-04-25T00:00:00.000Z' → '2006-04-25'
      - '25/04/2006' → '2006-04-25'
      - '2006-04-25' → '2006-04-25'
    """
    if not date_str or date_str == "nan":
        return ""
    s = date_str.strip()
    # ISO format with time: 2006-04-25T00:00:00.000Z
    if "T" in s:
        return s[:10]
    # Already YYYY-MM-DD
    if re.match(r'^\d{4}-\d{2}-\d{2}$', s):
        return s
    # DD/MM/YYYY
    m = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{4})$', s)
    if m:
        return f"{m.group(3)}-{m.group(2).zfill(2)}-{m.group(1).zfill(2)}"
    # Just return first 10 chars as fallback
    return s[:10]


def echr_search(query: str, limit: int = 10) -> list:
    """
    Search ECHR cases via echr_extractor.
    Returns list of result dicts for the sidebar.
    """
    get_echr = _ensure_echr_extractor()
    if get_echr is None:
        print(ECHR_MISSING_MSG, file=sys.stderr)
        return []

    try:
        kwargs = {
            "fields": ECHR_FIELDS,
            "save_file": "n",
            "language": ["ENG"],  # English only — excludes translations
        }
        # Try as application number first (e.g. 47940/99)
        if re.match(r'^\d+/\d{2}$', query.strip()):
            kwargs["query_payload"] = f'appno:"{query.strip()}"'
        else:
            kwargs["query_payload"] = f'docname:"{query.strip()}"'

        df = get_echr(**kwargs)

        if df is False or df is None or (hasattr(df, 'empty') and df.empty):
            return []

        results = []
        seen_appnos = set()
        for _, row in df.head(limit * 3).iterrows():
            docname_raw = str(row.get("docname", ""))

            # Skip summary translations that sneak through
            if "[" in docname_raw and "translation]" in docname_raw.lower():
                continue

            title = _clean_echr_title(docname_raw)
            appno = _clean_appno(str(row.get("appno", "")))

            # Try multiple date fields
            date = ""
            for date_col in ("judgementdate", "decisiondate", "kpdate"):
                raw = str(row.get(date_col, ""))
                if raw and raw != "nan" and raw.strip():
                    date = _normalise_echr_date(raw)
                    if date:
                        break

            institution = _detect_echr_institution(row.to_dict())

            # Deduplicate by appno
            if appno and appno in seen_appnos:
                continue
            if appno:
                seen_appnos.add(appno)

            results.append({
                "title": title,
                "appno": appno,
                "date": date,
                "institution": institution,
                "docname": docname_raw,  # raw docname for precise lookup
                "source": "echr",
            })
            if len(results) >= limit:
                break
        return results
    except Exception as e:
        print(f"ECHR search error: {e}", file=sys.stderr)
        return []


def echr_lookup(query: str) -> Optional[dict]:
    """
    Look up a specific ECHR case.
    Accepts raw docname (from sidebar click), case name, or application number.
    Returns a dict with all fields needed for biblatex formatting.
    """
    get_echr = _ensure_echr_extractor()
    if get_echr is None:
        print(ECHR_MISSING_MSG, file=sys.stderr)
        return None

    try:
        kwargs = {
            "fields": ECHR_FIELDS,
            "save_file": "n",
            "language": ["ENG"],
        }
        # Try application number
        if re.match(r'^\d+/\d{2}$', query.strip()):
            kwargs["query_payload"] = f'appno:"{query.strip()}"'
        else:
            # Use docname for precise lookup
            kwargs["query_payload"] = f'docname:"{query.strip()}"'

        df = get_echr(**kwargs)

        if df is False or df is None or (hasattr(df, 'empty') and df.empty):
            return None

        # Try exact docname match first (for sidebar click-through)
        best_row = None
        if "docname" in df.columns:
            for _, row in df.iterrows():
                if str(row.get("docname", "")).strip() == query.strip():
                    best_row = row.to_dict()
                    break
        # Fall back to first result
        if best_row is None:
            best_row = df.iloc[0].to_dict()

        title = _clean_echr_title(str(best_row.get("docname", "")))
        appno = _clean_appno(str(best_row.get("appno", "")))

        # Try multiple date fields — HUDOC uses different fields for different doc types
        date_full = ""
        for date_col in ("judgementdate", "decisiondate", "kpdate", "dateofdelivery"):
            raw = str(best_row.get(date_col, ""))
            if raw and raw != "nan" and raw.strip():
                date_full = _normalise_echr_date(raw)
                if date_full:
                    break

        institution = _detect_echr_institution(best_row)
        reporter = _parse_echr_reporter(best_row)

        return {
            "title": title,
            "appno": appno,
            "date": date_full,
            "institution": institution,
            "reporter": reporter,
            "source": "echr",
        }
    except Exception as e:
        print(f"ECHR lookup error: {e}", file=sys.stderr)
        return None


def echr_to_biblatex(case: dict, cite_key: str = "") -> str:
    """
    Convert ECHR case dict to @jurisdiction biblatex entry.

    Routes to one of four OSCOLA templates:
    A) Reported - Series A (reporter + pages)
    B) Reported - ECHR (reporter + volume + pages)
    C) Unreported ECtHR (number + full date)
    D) Commission (institution = Commission, may have DR reporter)
    """
    title = case.get("title", "")
    date = case.get("date", "")
    institution = case.get("institution", "ECtHR")
    appno = case.get("appno", "")
    reporter = case.get("reporter")  # None or dict

    if not cite_key:
        # Generate key from first party + 2-digit year
        first_party = re.split(r'\s+v\s+', title, maxsplit=1)[0] if title else "case"
        first_word = re.findall(r'[A-Za-z]+', first_party)
        key_name = first_word[0].lower() if first_word else "case"
        year_short = date[2:4] if len(date) >= 4 else "00"
        cite_key = key_name + year_short

    safe_title = _escape_bibtex(title)

    lines = [f"@jurisdiction{{{cite_key},"]
    lines.append(f"\ttitle = {{{safe_title}}},")

    if reporter and reporter.get("reporter") == "Series A":
        # Template A: Series A reported
        lines.append(f"\treporter = {{Series A}},")
        if reporter.get("pages"):
            lines.append(f"\tpages = {{{reporter['pages']}}},")
        if date:
            lines.append(f"\tdate = {{{date[:4]}}},")

    elif reporter and reporter.get("reporter") == "ECHR":
        # Template B: ECHR Reports reported
        lines.append(f"\treporter = {{ECHR}},")
        year = reporter.get("date_year", date[:4] if date else "")
        if year:
            lines.append(f"\tdate = {{{year}}},")
        if reporter.get("volume"):
            lines.append(f"\tvolume = {{{reporter['volume']}}},")
        if reporter.get("pages"):
            lines.append(f"\tpages = {{{reporter['pages']}}},")

    elif reporter and reporter.get("journaltitle") == "DR":
        # Template D: Commission with DR reporter
        if date:
            lines.append(f"\tdate = {{{date[:4]}}},")
        if reporter.get("volume"):
            lines.append(f"\tvolume = {{{reporter['volume']}}},")
        lines.append(f"\tjournaltitle = {{DR}},")
        if reporter.get("pages"):
            lines.append(f"\tpages = {{{reporter['pages']}}},")

    else:
        # Template C (or D unreported): no official reporter
        if appno:
            lines.append(f"\tnumber = {{{appno}}},")
        if date:
            lines.append(f"\tdate = {{{date}}},")

    lines.append(f"\tinstitution = {{{institution}}},")
    lines.append(f"\tkeywords = {{echr}},")
    lines.append("}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Unified interface
# ---------------------------------------------------------------------------

def lookup_and_format(source: str, query: str, cite_key: str = "") -> Optional[str]:
    """
    Look up a case and return a formatted biblatex entry.
    source: 'uk', 'eu', 'euleg', 'echr', or 'auto'
    """
    case = None

    # ECHR lookup
    if source == "echr":
        echr_case = echr_lookup(query)
        if echr_case:
            return echr_to_biblatex(echr_case, cite_key)
        return None

    # UK legislation lookup
    if source == "ukleg":
        # Try parsing as type/year/number (e.g. "ukpga/2018/12")
        parsed = _parse_uk_leg_path(query)
        if parsed:
            leg = uk_legislation_lookup(parsed["type"], parsed["year"], parsed["number"])
            if leg:
                return uk_legislation_to_biblatex(leg, cite_key)
        # Otherwise search and return first result
        results = uk_legislation_search(query, limit=5)
        if results:
            r = results[0]
            leg = uk_legislation_lookup(r["leg_type"], r["year"], r["number"])
            if leg:
                return uk_legislation_to_biblatex(leg, cite_key)
        return None

    # EU legislation lookup
    if source == "euleg":
        # If query looks like a CELEX (starts with 3 or 1 + 4 digits)
        if re.match(r'^[31]\d{4}', query.strip()):
            leg = eu_legislation_lookup(query.strip())
            if leg:
                return eu_legislation_to_biblatex(leg, cite_key)
        # Otherwise, search and return first result
        results = eu_legislation_search(query, limit=5)
        if results:
            leg = eu_legislation_lookup(results[0]["celex"])
            if leg:
                return eu_legislation_to_biblatex(leg, cite_key)
        return None

    if source in ("uk", "auto"):
        # Try as neutral citation first
        parsed = _parse_neutral_citation(query)
        if parsed:
            case = uk_lookup_by_citation(query)
            if case:
                return uk_to_biblatex(case, cite_key)

        # Try as a document URI (path like ewhc/admin/2006/1201, or UUID like d-2e2967ec-...)
        if not case and ("/" in query or query.startswith("d-")) and not query.startswith("["):
            case = uk_lookup_by_uri(query)
            if case:
                return uk_to_biblatex(case, cite_key)

    if source in ("eu", "auto"):
        case = eu_lookup(query)
        if case:
            return eu_to_biblatex(case, cite_key)

    if source in ("uk", "auto") and not case:
        # Try party name search — return first result
        results = uk_lookup_by_party(query, per_page=5)
        if results:
            first = results[0]
            uri = first.get("uri", "")
            if uri:
                # Prefer the citation from the feed; fall back to reconstructed
                cite = first.get("citation") or _citation_from_uri(uri)
                case = {
                    "title": first.get("title", ""),
                    "date": first.get("date", ""),
                    "citation": cite,
                    "year": first.get("date", "")[:4],
                    "uri": uri,
                    "source": "uk",
                }
                return uk_to_biblatex(case, cite_key)

    return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Look up UK/EU case law and produce @jurisdiction biblatex entries.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            examples:
              casecite.py uk "[2017] EWCA Civ 121"
              casecite.py eu "C-553/07"
              casecite.py eu "EU:C:2009:293"
              casecite.py search "data protection"
              casecite.py cache list
              casecite.py cache export -o cases.bib
        """),
    )
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON (for VS Code extension)")
    parser.add_argument("--key", "-k", default="",
                        help="Override the cite key")
    parser.add_argument("--append", "-a", default="",
                        help="Append to this .bib file")

    subparsers = parser.add_subparsers(dest="command")

    # uk subcommand
    uk_parser = subparsers.add_parser("uk", help="Look up a UK case")
    uk_parser.add_argument("query", help="Neutral citation or party name")

    # eu subcommand
    eu_parser = subparsers.add_parser("eu", help="Look up an EU case")
    eu_parser.add_argument("query", help="Case number, CELEX, or ECLI")

    # euleg subcommand
    euleg_parser = subparsers.add_parser("euleg", help="Look up EU legislation")
    euleg_parser.add_argument("query", help="CELEX number or search terms")

    # echr subcommand
    echr_parser = subparsers.add_parser("echr", help="Look up ECHR case")
    echr_parser.add_argument("query", help="Case name or application number")

    # ukleg subcommand
    ukleg_parser = subparsers.add_parser("ukleg", help="Look up UK legislation")
    ukleg_parser.add_argument("query", help="Title or type/year/number (e.g. ukpga/2018/12)")

    # search subcommand
    search_parser = subparsers.add_parser("search", help="Search UK, EU, and ECHR")
    search_parser.add_argument("query", help="Search terms")
    search_parser.add_argument("--limit", "-l", type=int, default=10)
    search_parser.add_argument("--source", "-s", default="all",
                               choices=["all", "uk", "eu", "euleg", "echr", "ukleg"],
                               help="Which source to search (default: all)")

    # cache subcommand
    cache_parser = subparsers.add_parser("cache", help="Manage the local cache")
    cache_sub = cache_parser.add_subparsers(dest="cache_command")
    cache_sub.add_parser("list", help="List cached entries")
    export_parser = cache_sub.add_parser("export", help="Export cache as .bib")
    export_parser.add_argument("-o", "--output", default="",
                               help="Output file (default: stdout)")
    search_cache_parser = cache_sub.add_parser("search", help="Search the cache")
    search_cache_parser.add_argument("term", help="Search term")
    cache_sub.add_parser("clear", help="Delete all cached entries")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    conn = _init_cache()
    output_json = args.json

    if args.command in ("uk", "eu", "euleg", "echr", "ukleg"):
        source = args.command
        bib = lookup_and_format(source, args.query, args.key)
        if bib:
            # Cache it
            # Extract cite_key from the entry
            key_match = re.match(r'@jurisdiction\{(\w+),', bib)
            cite_key = key_match.group(1) if key_match else "unknown"
            citation = args.query
            title_match = re.search(r'title = \{(.+?)\}', bib)
            title = title_match.group(1) if title_match else ""
            cache_put(conn, cite_key, bib, source, citation, title)

            if output_json:
                print(json.dumps({"success": True, "bib": bib, "cite_key": cite_key}))
            else:
                print(bib)

            # Optionally append to file
            append_to = args.append or os.environ.get("CASECITE_BIB", "")
            if append_to:
                with open(append_to, "a") as f:
                    f.write("\n\n" + bib)
                if not output_json:
                    print(f"\n→ Appended to {append_to}", file=sys.stderr)
        else:
            if output_json:
                print(json.dumps({"success": False, "error": f"No results for: {args.query}"}))
            else:
                print(f"No results for: {args.query}", file=sys.stderr)
            sys.exit(1)

    elif args.command == "search":
        src = args.source

        uk_results = uk_search(args.query, per_page=args.limit) if src in ("all", "uk") else []
        eu_results = eu_search(args.query, limit=args.limit) if src in ("all", "eu") else []
        euleg_results = eu_legislation_search(args.query, limit=args.limit) if src in ("all", "euleg") else []
        echr_results = echr_search(args.query, limit=args.limit) if src in ("all", "echr") else []
        ukleg_results = uk_legislation_search(args.query, limit=args.limit) if src in ("all", "ukleg") else []
        echr_available = _ensure_echr_extractor() is not None

        if output_json:
            result = {
                "uk": [{"title": r["title"], "uri": r["uri"], "date": r["date"],
                         "citation": r.get("citation", ""), "url": r.get("url", "")}
                        for r in uk_results],
                "eu": [{"celex": r["celex"], "title": r["title"], "date": r["date"],
                         "case_number": r.get("case_number", ""),
                         "is_ag_opinion": r.get("is_ag_opinion", False)}
                        for r in eu_results],
                "euleg": [{"celex": r["celex"], "title": r["title"], "date": r["date"],
                           "instrument_type": r.get("instrument_type", ""),
                           "in_force": r.get("in_force")}
                          for r in euleg_results],
                "echr": [{"title": r["title"], "appno": r.get("appno", ""),
                          "date": r["date"], "institution": r.get("institution", "ECtHR"),
                          "docname": r.get("docname", "")}
                         for r in echr_results],
                "ukleg": [{"title": r["title"], "leg_type": r.get("leg_type", ""),
                           "year": r.get("year", ""), "number": r.get("number", "")}
                          for r in ukleg_results],
            }
            if not echr_available and src in ("all", "echr"):
                result["echr_warning"] = ECHR_MISSING_MSG
            print(json.dumps(result))
        else:
            if not uk_results and not eu_results and not euleg_results and not echr_results:
                print("No results found.", file=sys.stderr)
            if uk_results:
                print("=== UK Cases ===")
                for r in uk_results:
                    cite = _citation_from_uri(r["uri"]) if r["uri"] else ""
                    print(f"  {cite:30s}  {r['title'][:60]:60s}  {r['date']}")
            if eu_results:
                print("\n=== EU Cases ===")
                for r in eu_results:
                    num = r.get("case_number", r.get("celex", ""))
                    print(f"  {num:20s}  {r['title'][:60]:60s}  {r['date']}")
            if euleg_results:
                print("\n=== EU Legislation ===")
                for r in euleg_results:
                    itype = r.get("instrument_type", "")
                    print(f"  {r['celex']:16s}  {itype:12s}  {r['title'][:50]:50s}  {r['date']}")
            if echr_results:
                print("\n=== ECHR Cases ===")
                for r in echr_results:
                    print(f"  {r.get('appno', ''):16s}  {r['title'][:60]:60s}  {r['date']}")

    elif args.command == "cache":
        if args.cache_command == "list":
            entries = cache_list(conn)
            if output_json:
                print(json.dumps(entries))
            else:
                for e in entries:
                    print(f"  {e['cite_key']:20s}  {e['citation']:30s}  {e['source']:4s}  {e['date_added'][:10]}")

        elif args.cache_command == "export":
            bib_str = cache_export(conn)
            if args.output:
                Path(args.output).write_text(bib_str)
                print(f"Exported to {args.output}", file=sys.stderr)
            else:
                print(bib_str)

        elif args.cache_command == "search":
            results = cache_search(conn, args.term)
            if output_json:
                print(json.dumps(results))
            else:
                for r in results:
                    print(f"\n% {r['cite_key']} — {r['citation']}")
                    print(r["bib_entry"])

        elif args.cache_command == "clear":
            if output_json:
                count = cache_clear(conn)
                print(json.dumps({"success": True, "deleted": count}))
            else:
                entries = cache_list(conn)
                if not entries:
                    print("Cache is already empty.")
                else:
                    print(f"This will delete {len(entries)} cached entries.")
                    confirm = input("Type 'yes' to confirm: ").strip().lower()
                    if confirm == "yes":
                        count = cache_clear(conn)
                        print(f"Deleted {count} entries.")
                    else:
                        print("Cancelled.")

    conn.close()


if __name__ == "__main__":
    main()