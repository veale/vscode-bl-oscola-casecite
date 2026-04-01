// src/extension.js — CaseCite VS Code Extension
//
// Provides:
// 1. A sidebar panel with persistent search, rich results, bib preview
// 2. Command-palette fallback commands for quick lookup
// 3. \cite{} completion from the local cache
// 4. Keyboard shortcut to focus the sidebar search

const vscode = require("vscode");
const { execFile } = require("child_process");
const path = require("path");
const fs = require("fs");
const { CaseCiteSidebarProvider } = require("./sidebar");

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function getPythonPath() {
  return vscode.workspace.getConfiguration("casecite").get("pythonPath", "python3");
}

function getScriptPath(context) {
  return path.join(context.extensionPath, "scripts", "casecite.py");
}

function runCasecite(context, args) {
  return new Promise((resolve, reject) => {
    const env = { ...process.env };
    const cacheDir = vscode.workspace.getConfiguration("casecite").get("cacheDir", "");
    if (cacheDir) env.CASECITE_CACHE_DIR = cacheDir;

    execFile(
      getPythonPath(),
      [getScriptPath(context), "--json", ...args],
      { env, timeout: 30000 },
      (err, stdout, stderr) => {
        if (err) return reject(new Error(stderr || err.message));
        try {
          resolve(JSON.parse(stdout));
        } catch (e) {
          reject(new Error(`Failed to parse: ${stdout.slice(0, 200)}`));
        }
      }
    );
  });
}

// ---------------------------------------------------------------------------
// \cite{} Completion Provider (cache only — no network latency)
// ---------------------------------------------------------------------------

class CiteCompletionProvider {
  constructor(context) {
    this._context = context;
    this._cache = [];
    this._lastRefresh = 0;
  }

  async _refreshCache() {
    if (Date.now() - this._lastRefresh < 30000 && this._cache.length > 0) return;
    try {
      const result = await runCasecite(this._context, ["cache", "list"]);
      this._cache = Array.isArray(result) ? result : [];
      this._lastRefresh = Date.now();
    } catch {
      // Silently fail
    }
  }

  async provideCompletionItems(document, position) {
    const lineText = document.lineAt(position).text;
    const prefix = lineText.substring(0, position.character);
    const citeMatch = prefix.match(/\\(?:cite|autocite|textcite|parencite|footcite)\{([^}]*)$/);
    if (!citeMatch) return [];

    await this._refreshCache();
    const typed = citeMatch[1].split(",").pop().trim();

    return this._cache
      .filter((c) =>
        !typed ||
        c.cite_key.includes(typed) ||
        (c.title && c.title.toLowerCase().includes(typed.toLowerCase()))
      )
      .map((c) => {
        const item = new vscode.CompletionItem(c.cite_key, vscode.CompletionItemKind.Reference);
        item.detail = c.citation || c.title || "";
        item.documentation = new vscode.MarkdownString(
          `**${c.title || ""}**\n\n${c.citation || ""}\n\nSource: ${c.source}`
        );
        item.sortText = "0" + c.cite_key;
        return item;
      });
  }
}

// ---------------------------------------------------------------------------
// Command-palette fallbacks (for when sidebar isn't convenient)
// ---------------------------------------------------------------------------

async function quickLookup(context, source) {
  const placeholders = {
    uk: "Neutral citation or party name, e.g. [2024] UKSC 30",
    eu: "Case number, CELEX, or ECLI, e.g. C-553/07",
    euleg: "CELEX (e.g. 32016R0679) or keyword (e.g. data protection)",
    echr: "Case name or app no, e.g. Osman v UK or 47940/99",
    ukleg: "Title, e.g. Data Protection Act or Unfair Contract Terms",
  };
  const input = await vscode.window.showInputBox({
    prompt: `Look up ${source.toUpperCase()} case`,
    placeHolder: placeholders[source],
  });
  if (!input) return;

  try {
    const result = await vscode.window.withProgress(
      {
        location: vscode.ProgressLocation.Notification,
        title: `CaseCite: Looking up ${source.toUpperCase()} case…`,
      },
      () => runCasecite(context, [source, input])
    );

    if (result.success) {
      const editor = vscode.window.activeTextEditor;
      if (editor) {
        await editor.edit((eb) => eb.insert(editor.selection.active, result.bib + "\n\n"));
      } else {
        await vscode.env.clipboard.writeText(result.bib);
        vscode.window.showInformationMessage("CaseCite: Copied to clipboard");
      }
    } else {
      vscode.window.showWarningMessage(`CaseCite: ${result.error}`);
    }
  } catch (err) {
    vscode.window.showErrorMessage(`CaseCite: ${err.message}`);
  }
}

async function cacheExport(context) {
  const uri = await vscode.window.showSaveDialog({
    filters: { BibLaTeX: ["bib"] },
    defaultUri: vscode.Uri.file("cases.bib"),
  });
  if (!uri) return;
  try {
    await runCasecite(context, ["cache", "export", "-o", uri.fsPath]);
    vscode.window.showInformationMessage(`CaseCite: Exported to ${uri.fsPath}`);
  } catch (err) {
    vscode.window.showErrorMessage(`CaseCite: ${err.message}`);
  }
}

async function cacheClear(context) {
  // First check how many entries there are
  let count = 0;
  try {
    const entries = await runCasecite(context, ["cache", "list"]);
    count = Array.isArray(entries) ? entries.length : 0;
  } catch {
    // If we can't even list, the cache may be corrupted — still offer to clear
  }

  const label = count > 0
    ? `Delete all ${count} cached case entries? This cannot be undone.`
    : "Clear the cache? (It may be empty or corrupted.)";

  const choice = await vscode.window.showWarningMessage(
    `CaseCite: ${label}`,
    { modal: true },
    "Clear cache"
  );

  if (choice !== "Clear cache") return;

  try {
    const result = await runCasecite(context, ["cache", "clear"]);
    const deleted = result.deleted || 0;
    vscode.window.showInformationMessage(`CaseCite: Deleted ${deleted} cached entries.`);
  } catch (err) {
    // If the script fails (e.g. corrupted DB), try deleting the file directly
    const cacheDir = vscode.workspace.getConfiguration("casecite").get("cacheDir", "");
    const dbPath = cacheDir
      ? require("path").join(cacheDir, "cache.db")
      : require("path").join(require("os").homedir(), ".casecite", "cache.db");

    try {
      if (require("fs").existsSync(dbPath)) {
        require("fs").unlinkSync(dbPath);
        vscode.window.showInformationMessage("CaseCite: Cache file deleted. It will be recreated on next lookup.");
      } else {
        vscode.window.showInformationMessage("CaseCite: No cache file found.");
      }
    } catch (fsErr) {
      vscode.window.showErrorMessage(`CaseCite: Could not clear cache: ${fsErr.message}`);
    }
  }
}

// ---------------------------------------------------------------------------
// Activation
// ---------------------------------------------------------------------------

function activate(context) {
  // Sidebar
  const sidebarProvider = new CaseCiteSidebarProvider(context);
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider("casecite.sidebarView", sidebarProvider, {
      webviewOptions: { retainContextWhenHidden: true },
    })
  );

  // Commands
  context.subscriptions.push(
    vscode.commands.registerCommand("casecite.focusSearch", () => sidebarProvider.focusSearch()),
    vscode.commands.registerCommand("casecite.lookupUK", () => quickLookup(context, "uk")),
    vscode.commands.registerCommand("casecite.lookupEU", () => quickLookup(context, "eu")),
    vscode.commands.registerCommand("casecite.lookupEULeg", () => quickLookup(context, "euleg")),
    vscode.commands.registerCommand("casecite.lookupECHR", () => quickLookup(context, "echr")),
    vscode.commands.registerCommand("casecite.lookupUKLeg", () => quickLookup(context, "ukleg")),
    vscode.commands.registerCommand("casecite.cacheExport", () => cacheExport(context)),
    vscode.commands.registerCommand("casecite.cacheClear", () => cacheClear(context)),
  );

  // \cite{} completion
  const citeProvider = new CiteCompletionProvider(context);
  context.subscriptions.push(
    vscode.languages.registerCompletionItemProvider(
      [{ scheme: "file", language: "latex" }, { scheme: "file", language: "bibtex" }],
      citeProvider,
      "{", ","
    )
  );

  // Python check
  execFile(getPythonPath(), ["--version"], (err) => {
    if (err) {
      vscode.window.showWarningMessage(
        `CaseCite: Python not found at '${getPythonPath()}'. Update casecite.pythonPath in settings.`
      );
    } else {
      // Check if echr-extractor is installed (optional dependency)
      execFile(getPythonPath(), ["-c", "import echr_extractor"], (echrErr) => {
        if (echrErr) {
          // Don't block — just note it's available in the status bar the first time
          // The user will see a clear error if they try to use ECHR features
        }
      });
    }
  });
}

function deactivate() {}

module.exports = { activate, deactivate };
