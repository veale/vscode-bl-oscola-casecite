// src/sidebar.js — CaseCite Sidebar Webview Provider
//
// Renders a persistent sidebar panel with:
// - Search input with UK/EU/Cache filter tabs
// - Result cards with party names, court, date, headnote snippet
// - Detail panel with full bib preview, editable cite key, insert actions

const vscode = require("vscode");
const { execFile } = require("child_process");
const path = require("path");
const fs = require("fs");

class CaseCiteSidebarProvider {
  constructor(context) {
    this._context = context;
    this._view = undefined;
  }

  resolveWebviewView(webviewView) {
    this._view = webviewView;

    webviewView.webview.options = {
      enableScripts: true,
      localResourceRoots: [this._context.extensionUri],
    };

    webviewView.webview.html = this._getHtml();

    // Handle messages from the webview
    webviewView.webview.onDidReceiveMessage(async (msg) => {
      switch (msg.type) {
        case "search":
          await this._handleSearch(msg.query, msg.filter);
          break;
        case "lookup":
          await this._handleLookup(msg.source, msg.query, msg.citeKey);
          break;
        case "insert":
          await this._insertBibEntry(msg.bib);
          break;
        case "append":
          await this._appendToBibFile(msg.bib);
          break;
        case "copy":
          await vscode.env.clipboard.writeText(msg.bib);
          vscode.window.showInformationMessage("CaseCite: Copied to clipboard");
          break;
        case "openUrl":
          vscode.env.openExternal(vscode.Uri.parse(msg.url));
          break;
      }
    });
  }

  // ------------------------------------------------------------------
  // Python script interface
  // ------------------------------------------------------------------

  _getPythonPath() {
    return vscode.workspace.getConfiguration("casecite").get("pythonPath", "python3");
  }

  _getScriptPath() {
    return path.join(this._context.extensionPath, "scripts", "casecite.py");
  }

  _runScript(args) {
    return new Promise((resolve, reject) => {
      const env = { ...process.env };
      const cacheDir = vscode.workspace.getConfiguration("casecite").get("cacheDir", "");
      if (cacheDir) env.CASECITE_CACHE_DIR = cacheDir;

      execFile(
        this._getPythonPath(),
        [this._getScriptPath(), "--json", ...args],
        { env, timeout: 90000 },
        (err, stdout, stderr) => {
          if (err) return reject(new Error(stderr || err.message));
          try {
            resolve(JSON.parse(stdout));
          } catch (e) {
            reject(new Error(`Parse error: ${stdout.slice(0, 200)}`));
          }
        }
      );
    });
  }

  // ------------------------------------------------------------------
  // Command handlers
  // ------------------------------------------------------------------

  async _handleSearch(query, filter) {
    if (!query || !query.trim()) return;
    this._postMessage({ type: "searchStart" });

    try {
      const result = await this._runScript(["search", query.trim(), "--limit", "15"]);
      // Enrich with source labels
      const items = [];
      if (filter !== "eu" && result.uk) {
        for (const r of result.uk) {
          items.push({ ...r, source: "uk" });
        }
      }
      if (filter !== "uk" && result.eu) {
        for (const r of result.eu) {
          items.push({ ...r, source: "eu" });
        }
      }
      this._postMessage({ type: "searchResults", items, query });
    } catch (err) {
      this._postMessage({ type: "searchError", error: err.message });
    }
  }

  async _handleLookup(source, query, citeKey) {
    this._postMessage({ type: "lookupStart" });
    try {
      const args = [source, query];
      if (citeKey) args.push("--key", citeKey);
      const result = await this._runScript(args);
      this._postMessage({ type: "lookupResult", ...result });
    } catch (err) {
      this._postMessage({ type: "lookupError", error: err.message });
    }
  }

  async _insertBibEntry(bib) {
    const editor = vscode.window.activeTextEditor;
    if (editor) {
      await editor.edit((eb) => {
        eb.insert(editor.selection.active, bib + "\n\n");
      });
      vscode.window.showInformationMessage("CaseCite: Inserted at cursor");
    } else {
      await vscode.env.clipboard.writeText(bib);
      vscode.window.showInformationMessage("CaseCite: Copied to clipboard (no editor open)");
    }
  }

  async _appendToBibFile(bib) {
    const config = vscode.workspace.getConfiguration("casecite");
    let bibPath = config.get("defaultBibFile", "");

    if (!bibPath) {
      const uri = await vscode.window.showOpenDialog({
        filters: { "BibLaTeX files": ["bib"] },
        canSelectMany: false,
      });
      if (!uri || uri.length === 0) return;
      bibPath = uri[0].fsPath;
    }

    try {
      fs.appendFileSync(bibPath, "\n\n" + bib);
      vscode.window.showInformationMessage(
        `CaseCite: Appended to ${path.basename(bibPath)}`
      );
    } catch (err) {
      vscode.window.showErrorMessage(`CaseCite: ${err.message}`);
    }
  }

  _postMessage(msg) {
    if (this._view) {
      this._view.webview.postMessage(msg);
    }
  }

  // Allow external commands to focus the search box
  focusSearch() {
    if (this._view) {
      this._view.show(true);
      this._postMessage({ type: "focusSearch" });
    }
  }

  // ------------------------------------------------------------------
  // Webview HTML
  // ------------------------------------------------------------------

  _getHtml() {
    return /*html*/ `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: var(--vscode-font-family);
    font-size: var(--vscode-font-size);
    color: var(--vscode-foreground);
    background: var(--vscode-sideBar-background);
    padding: 8px;
  }

  /* Search box */
  .search-box {
    display: flex;
    flex-direction: column;
    gap: 6px;
    margin-bottom: 8px;
  }
  .search-input {
    width: 100%;
    padding: 6px 8px;
    border: 1px solid var(--vscode-input-border);
    background: var(--vscode-input-background);
    color: var(--vscode-input-foreground);
    border-radius: 3px;
    font-size: 12px;
    outline: none;
  }
  .search-input:focus {
    border-color: var(--vscode-focusBorder);
  }

  /* Filter tabs */
  .filter-tabs {
    display: flex;
    gap: 4px;
  }
  .filter-tab {
    padding: 2px 8px;
    font-size: 11px;
    border: 1px solid var(--vscode-input-border);
    background: transparent;
    color: var(--vscode-foreground);
    border-radius: 3px;
    cursor: pointer;
  }
  .filter-tab.active {
    background: var(--vscode-button-background);
    color: var(--vscode-button-foreground);
    border-color: var(--vscode-button-background);
  }

  /* Result list */
  .results {
    display: flex;
    flex-direction: column;
    gap: 4px;
    margin-bottom: 8px;
    max-height: 45vh;
    overflow-y: auto;
  }
  .result-card {
    padding: 6px 8px;
    border: 1px solid var(--vscode-input-border);
    border-radius: 3px;
    cursor: pointer;
    background: var(--vscode-editor-background);
    transition: border-color 0.1s;
  }
  .result-card:hover {
    border-color: var(--vscode-focusBorder);
  }
  .result-card.selected {
    border-color: var(--vscode-button-background);
    border-width: 2px;
    padding: 5px 7px;
  }
  .result-title {
    font-size: 12px;
    font-weight: 600;
    line-height: 1.3;
  }
  .result-meta {
    font-size: 11px;
    color: var(--vscode-descriptionForeground);
    margin-top: 2px;
  }
  .result-snippet {
    font-size: 11px;
    color: var(--vscode-textLink-foreground);
    margin-top: 2px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .source-badge {
    display: inline-block;
    font-size: 10px;
    padding: 0 4px;
    border-radius: 2px;
    font-weight: 600;
    vertical-align: middle;
  }
  .source-badge.uk {
    background: var(--vscode-charts-green);
    color: var(--vscode-editor-background);
  }
  .source-badge.eu {
    background: var(--vscode-charts-blue);
    color: var(--vscode-editor-background);
  }
  .source-badge.ag {
    background: var(--vscode-charts-purple, #9d4edd);
    color: var(--vscode-editor-background);
  }

  /* Detail panel */
  .detail-panel {
    border-top: 1px solid var(--vscode-input-border);
    padding-top: 8px;
  }
  .detail-title {
    font-size: 13px;
    font-weight: 600;
    margin-bottom: 4px;
    line-height: 1.3;
  }
  .detail-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 4px 8px;
    margin-bottom: 8px;
  }
  .detail-label {
    font-size: 10px;
    color: var(--vscode-descriptionForeground);
    text-transform: uppercase;
    letter-spacing: 0.3px;
  }
  .detail-value {
    font-size: 12px;
  }

  /* Bib preview */
  .bib-preview {
    background: var(--vscode-textCodeBlock-background);
    border: 1px solid var(--vscode-input-border);
    border-radius: 3px;
    padding: 8px;
    font-family: var(--vscode-editor-font-family);
    font-size: 11px;
    line-height: 1.5;
    white-space: pre-wrap;
    word-break: break-all;
    margin-bottom: 8px;
    max-height: 200px;
    overflow-y: auto;
  }

  /* Cite key input */
  .key-row {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-bottom: 6px;
  }
  .key-label {
    font-size: 11px;
    color: var(--vscode-descriptionForeground);
    min-width: 62px;
    display: flex;
    align-items: center;
    gap: 3px;
  }
  .key-input {
    flex: 1;
    padding: 3px 6px;
    border: 1px solid var(--vscode-input-border);
    background: var(--vscode-input-background);
    color: var(--vscode-input-foreground);
    border-radius: 3px;
    font-family: var(--vscode-editor-font-family);
    font-size: 12px;
  }
  .opt-fields { margin-bottom: 8px; }
  .info-dot {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 12px; height: 12px;
    border-radius: 50%;
    border: 1px solid var(--vscode-input-border);
    font-size: 8px;
    color: var(--vscode-descriptionForeground);
    cursor: help;
    position: relative;
  }
  .info-dot:hover .info-tip { display: block; }
  .info-tip {
    display: none;
    position: absolute;
    bottom: 120%;
    left: 50%;
    transform: translateX(-50%);
    width: 200px;
    padding: 6px 8px;
    background: var(--vscode-editorHoverWidget-background, #252526);
    color: var(--vscode-editorHoverWidget-foreground, #ccc);
    border: 1px solid var(--vscode-editorHoverWidget-border, #454545);
    font-size: 11px;
    line-height: 1.4;
    border-radius: 3px;
    z-index: 10;
    pointer-events: none;
    font-weight: normal;
    text-transform: none;
  }

  /* Action buttons */
  .actions {
    display: flex;
    gap: 4px;
    flex-wrap: wrap;
  }
  .btn {
    padding: 4px 10px;
    font-size: 11px;
    border-radius: 3px;
    cursor: pointer;
    border: 1px solid var(--vscode-button-border, transparent);
  }
  .btn-primary {
    background: var(--vscode-button-background);
    color: var(--vscode-button-foreground);
  }
  .btn-primary:hover {
    background: var(--vscode-button-hoverBackground);
  }
  .btn-secondary {
    background: var(--vscode-button-secondaryBackground);
    color: var(--vscode-button-secondaryForeground);
  }

  /* Links row */
  .links-row {
    margin-top: 6px;
    display: flex;
    gap: 10px;
  }
  .links-row a {
    font-size: 11px;
    color: var(--vscode-textLink-foreground);
    text-decoration: none;
    cursor: pointer;
  }
  .links-row a:hover {
    text-decoration: underline;
  }

  /* Status */
  .status {
    font-size: 11px;
    color: var(--vscode-descriptionForeground);
    text-align: center;
    padding: 12px 0;
  }
  .spinner {
    display: inline-block;
    width: 14px; height: 14px;
    border: 2px solid var(--vscode-descriptionForeground);
    border-top-color: transparent;
    border-radius: 50%;
    animation: spin 0.7s linear infinite;
    vertical-align: middle;
    margin-right: 6px;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>

<div class="search-box">
  <input class="search-input" id="searchInput" type="text"
         placeholder="Search UK &amp; EU cases..." />
  <div class="filter-tabs">
    <button class="filter-tab active" data-filter="all">All</button>
    <button class="filter-tab" data-filter="uk">UK</button>
    <button class="filter-tab" data-filter="eu">EU</button>
    <button class="filter-tab" data-filter="cache">Cache</button>
  </div>
</div>

<div class="results" id="results"></div>
<div id="detail"></div>

<script>
const vscode = acquireVsCodeApi();
let currentFilter = "all";
let currentResults = [];
let selectedIndex = -1;
let currentBib = "";

// --- Search ---
const searchInput = document.getElementById("searchInput");
searchInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    const q = searchInput.value.trim();
    if (q) vscode.postMessage({ type: "search", query: q, filter: currentFilter });
  }
  if (e.key === "ArrowDown" && currentResults.length > 0) {
    e.preventDefault();
    selectResult(Math.min(selectedIndex + 1, currentResults.length - 1));
  }
  if (e.key === "ArrowUp" && currentResults.length > 0) {
    e.preventDefault();
    selectResult(Math.max(selectedIndex - 1, 0));
  }
});

// --- Filters ---
document.querySelectorAll(".filter-tab").forEach(tab => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".filter-tab").forEach(t => t.classList.remove("active"));
    tab.classList.add("active");
    currentFilter = tab.dataset.filter;
    // Don't auto-search — user will press Enter when ready
    searchInput.focus();
  });
});

// --- Results ---
function renderResults(items, query) {
  currentResults = items;
  selectedIndex = -1;
  const el = document.getElementById("results");

  if (!items || items.length === 0) {
    el.innerHTML = '<div class="status">No results found</div>';
    document.getElementById("detail").innerHTML = "";
    return;
  }

  el.innerHTML = items.map((r, i) => {
    let badge = r.source === "uk"
      ? '<span class="source-badge uk">UK</span>'
      : '<span class="source-badge eu">EU</span>';
    if (r.is_ag_opinion) {
      badge += ' <span class="source-badge ag">AG</span>';
    }
    const meta = r.source === "uk"
      ? (r.citation || r.uri || "") + " · " + (r.date || "")
      : (r.case_number || r.celex || "") + " · " + (r.date || "");
    const title = r.title || r.celex || r.uri || "Untitled";
    const shortTitle = title.length > 80 ? title.slice(0, 77) + "..." : title;
    return '<div class="result-card" data-idx="' + i + '">'
      + '<div class="result-title">' + badge + " " + escHtml(shortTitle) + "</div>"
      + '<div class="result-meta">' + escHtml(meta) + "</div>"
      + "</div>";
  }).join("");

  el.querySelectorAll(".result-card").forEach(card => {
    card.addEventListener("click", () => {
      selectResult(parseInt(card.dataset.idx));
    });
  });
}

function selectResult(idx) {
  if (idx < 0 || idx >= currentResults.length) return;
  selectedIndex = idx;

  // Highlight
  document.querySelectorAll(".result-card").forEach((c, i) => {
    c.classList.toggle("selected", i === idx);
  });

  // Scroll into view
  const cards = document.querySelectorAll(".result-card");
  if (cards[idx]) cards[idx].scrollIntoView({ block: "nearest" });

  // Fetch the full bib entry
  const r = currentResults[idx];
  const source = r.source;
  let query;
  if (source === "uk") {
    // Prefer the neutral citation (works with uk_lookup_by_citation)
    if (r.citation) {
      query = r.citation;
    } else if (r.url) {
      // Extract path-style URI from URL
      var urlParts = r.url.split("nationalarchives.gov.uk/");
      query = urlParts.length > 1 ? urlParts[1] : r.uri;
    } else {
      query = r.uri;
    }
  } else {
    query = r.celex || r.case_number;
  }
  vscode.postMessage({ type: "lookup", source, query, citeKey: "" });
}

// --- Detail panel ---
function renderDetail(result) {
  const el = document.getElementById("detail");
  if (!result.success) {
    el.innerHTML = '<div class="status">Could not fetch case details</div>';
    return;
  }

  var originalBib = result.bib;
  currentBib = result.bib;
  const key = result.cite_key || "";

  el.innerHTML = ''
    + '<div class="detail-panel">'
    + '  <div class="bib-preview" id="bibPreview">' + escHtml(result.bib) + '</div>'
    + '  <div class="key-row">'
    + '    <span class="key-label">Cite key:</span>'
    + '    <input class="key-input" id="keyInput" type="text" value="' + escAttr(key) + '" />'
    + '  </div>'
    + '  <div class="opt-fields">'
    + '    <div class="key-row">'
    + '      <span class="key-label">shorthand <span class="info-dot" title="A shorter title introduced the first time cited, thereafter used in place of the full title. Listed in any table of abbreviations.">?</span></span>'
    + '      <input class="key-input" id="shorthandInput" type="text" placeholder="optional" />'
    + '    </div>'
    + '    <div class="key-row">'
    + '      <span class="key-label">shorttitle <span class="info-dot" title="Silently replaces the full title on second and subsequent citations. For household names.">?</span></span>'
    + '      <input class="key-input" id="shorttitleInput" type="text" placeholder="optional" />'
    + '    </div>'
    + '  </div>'
    + '  <div class="actions">'
    + '    <button class="btn btn-primary" id="btnInsert">Insert at cursor</button>'
    + '    <button class="btn btn-secondary" id="btnAppend">Append to .bib</button>'
    + '    <button class="btn btn-secondary" id="btnCopy">Copy</button>'
    + '  </div>'
    + '</div>';

  function rebuildBib() {
    var newKey = document.getElementById("keyInput").value.trim();
    var sh = document.getElementById("shorthandInput").value.trim();
    var st = document.getElementById("shorttitleInput").value.trim();
    if (!newKey) return;
    var NL = String.fromCharCode(10);
    var TAB = String.fromCharCode(9);
    // Split original bib into lines, replace key, add optional fields
    var lines = originalBib.split(NL);
    // Replace cite key in first line
    var firstLine = lines[0];
    var braceIdx = firstLine.indexOf("{");
    var commaIdx = firstLine.indexOf(",", braceIdx);
    if (braceIdx >= 0 && commaIdx >= 0) {
      lines[0] = firstLine.substring(0, braceIdx + 1) + newKey + firstLine.substring(commaIdx);
    }
    // Find the title line index
    var titleLineIdx = -1;
    for (var i = 0; i < lines.length; i++) {
      if (lines[i].indexOf("title = {") >= 0) { titleLineIdx = i; break; }
    }
    // Build new lines array without old shorthand/shorttitle, then add new ones
    var newLines = [];
    for (var i = 0; i < lines.length; i++) {
      if (lines[i].indexOf("shorthand = {") >= 0) continue;
      if (lines[i].indexOf("shorttitle = {") >= 0) continue;
      if (i === titleLineIdx) {
        if (sh) newLines.push(TAB + "shorthand = {" + sh + "},");
        if (st) newLines.push(TAB + "shorttitle = {" + st + "},");
      }
      newLines.push(lines[i]);
    }
    currentBib = newLines.join(NL);
    document.getElementById("bibPreview").textContent = currentBib;
  }

  document.getElementById("keyInput").addEventListener("input", rebuildBib);
  document.getElementById("shorthandInput").addEventListener("input", rebuildBib);
  document.getElementById("shorttitleInput").addEventListener("input", rebuildBib);

  document.getElementById("btnInsert").addEventListener("click", () => {
    vscode.postMessage({ type: "insert", bib: currentBib });
  });
  document.getElementById("btnAppend").addEventListener("click", () => {
    vscode.postMessage({ type: "append", bib: currentBib });
  });
  document.getElementById("btnCopy").addEventListener("click", () => {
    vscode.postMessage({ type: "copy", bib: currentBib });
  });
}

// --- Messages from extension ---
window.addEventListener("message", (event) => {
  const msg = event.data;
  switch (msg.type) {
    case "searchStart":
      document.getElementById("results").innerHTML =
        '<div class="status"><span class="spinner"></span> Searching...</div>';
      document.getElementById("detail").innerHTML = "";
      break;
    case "searchResults":
      renderResults(msg.items, msg.query);
      break;
    case "searchError":
      document.getElementById("results").innerHTML =
        '<div class="status">Error: ' + escHtml(msg.error) + "</div>";
      break;
    case "lookupStart":
      document.getElementById("detail").innerHTML =
        '<div class="status"><span class="spinner"></span> Loading...</div>';
      break;
    case "lookupResult":
      renderDetail(msg);
      break;
    case "lookupError":
      document.getElementById("detail").innerHTML =
        '<div class="status">Error: ' + escHtml(msg.error) + "</div>";
      break;
    case "focusSearch":
      searchInput.focus();
      searchInput.select();
      break;
  }
});

function escHtml(s) {
  const d = document.createElement("div");
  d.textContent = s || "";
  return d.innerHTML;
}
function escAttr(s) {
  return (s || "").replace(/"/g, "&quot;").replace(/</g, "&lt;");
}
</script>

</body>
</html>`;
  }
}

module.exports = { CaseCiteSidebarProvider };