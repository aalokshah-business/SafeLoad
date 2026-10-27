# SafeLoad

Offline Windows desktop keyword scanner. Choose a source folder, enter comma-separated keywords, and select **Scan Folder**. Matching files appear first with **Failed** status. Files with no keyword matches show **OK**. Unreadable or unsupported files show **Not Scanned**. Select a failed file for locations and reasons, resolve it manually, and scan again. SafeLoad never edits, copies, deletes, cleans or redacts source files.

**MakeItSafe (tab 3):** two keyword boxes and one **MakeItSafe Folder** button. After a confirmation, SafeLoad rewrites text, Word, Excel and PowerPoint files in the source folder in place. **Remove Every Line Containing These Keywords** deletes whole lines (Word and PowerPoint paragraphs, Excel cell text, and unused external links to that keyword); a paragraph that a table cell or slide needs is emptied instead. This runs first. **Reverse These Keywords** then writes every remaining match backwards (`git` becomes `tig`, `business` becomes `ssenisub`; case is kept). Either box may be left empty. The result list shows each file and a summary such as "git has been replaced with tig" and "Lines containing github have been removed". Reverse keywords that read the same backwards are rejected. PDFs, images and other formats are skipped and reported, file and folder names are never renamed, and no backup is kept. Reversal can be undone by running it again with the reversed words; removed lines cannot be restored.

## Install On Windows With One Command

See the [Windows Installation Guide](INSTALL.md) for the full setup, offline installation, saved settings, App Control troubleshooting, and an implementation comparison with AskAi.

Run in PowerShell on Windows 10/11 x64:

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/aalokshah-business/SafeLoad/main/install.ps1)))
```

The installer downloads the latest Windows release from this public repository, verifies its SHA-256 checksum, installs under `%LOCALAPPDATA%\Programs\SafeLoad\versions`, creates Desktop and Start Menu shortcuts with the SafeLoad icon, and opens SafeLoad. The same icon appears on the taskbar while the app runs. No administrator account, Git, Python or Microsoft Office installation is required. GitHub access is required during installation; the app runs offline afterward. The executable is unsigned and normal organization software policies still apply. The command does not change PowerShell or Windows security policies. You can [review the installer](install.ps1) before running it.

To keep the icon on the taskbar after closing the app, right-click its running taskbar icon and select **Pin To Taskbar**. Windows requires a user action for permanent taskbar pinning; the installer does not change taskbar policy.

Run the same command to install a newer release. Each installation uses a new version directory so an existing running app is not overwritten; shortcuts point to the new version. Older versions are retained. To uninstall, close SafeLoad, delete its installation directory and remove its two shortcuts.

For a disconnected computer, download [the latest ZIP and checksum](https://github.com/aalokshah-business/SafeLoad/releases/latest) on an approved machine. Transfer them and `install.ps1`, then run:

```powershell
& .\install.ps1 -ArchivePath .\SafeLoad-Windows-x64.zip -ChecksumPath .\SafeLoad-Windows-x64.zip.sha256
```

Alternatively extract the **entire** ZIP and open `SafeLoad\SafeLoad.exe` without installing.

## Use

1. In **Directory**, choose the source folder. All subfolders and hidden files are visited. Links/junctions are flagged rather than followed.
2. In **Keywords**, enter comma-separated words or phrases. Matching is literal and case-insensitive by default, including inside longer words. Spaces before and after commas are trimmed. Empty entries and exact duplicates are ignored. Commas cannot be part of a keyword. Use **Case-Sensitive Matching** when needed. Keywords and this option are saved locally when scanning or closing and restored on startup.
3. Select **Scan Folder**. The list shows keyword matches first, **Not Scanned** files next, and **OK** files last. All **Failed** entries are highlighted. Select an entry to see the matched keyword names and locations, or the specific reason a file was not scanned.
4. Resolve files manually and select **Scan Folder** again. No automatic cleanup is available.
5. Optionally select **Save Report** to save JSON outside the source directory. Reports include matched keyword names, locations, and reasons for files that were not scanned. File excerpts are omitted. Matched terms, paths and parser errors may contain sensitive information.

**OK** means no configured keyword matches were found in inspected text. **Failed** means a keyword matched. Format limitations and scan notes do not cause Failed. **Not Scanned** means an error prevented the file from being checked; the reason is shown separately. Folder **Safe** requires every file to be OK and the scan to finish without errors. Empty, cancelled or incomplete scans never receive Safe. This checks configured keywords; it is not a security certification or approval to upload. Keep source files unchanged during scans; the result describes the scan snapshot.

Saved keyword settings live in `%LOCALAPPDATA%\SafeLoad\settings.json`, separate from the installed version and scanned folders. Updates retain them. These settings contain your configured keywords and are never uploaded by SafeLoad.

## Inspection Coverage

| Format | Coverage |
| --- | --- |
| Code, text and configuration files | Common source/data extensions and extensionless UTF-8; BOM-marked UTF-8/16/32. Matches report line numbers. Unsupported encodings require manual review. |
| Word `.docx` | Package XML text, including split formatting runs, paragraphs, tables, headers, footers, comments and properties. |
| Excel `.xlsx` | Package XML text, including shared/inline strings, split rich-text runs, formulas, cached values, comments and properties. |
| PowerPoint `.pptx` | Package XML text, including split runs, slides, notes, comments, charts and properties. |
| PDF | Searchable page text, metadata, annotations and form values. Status follows keyword matches in extracted text. Scope notes explain that images, outlined letters, arbitrary objects and attachments are not searched. |
| Images, archives, binaries, legacy `.doc`/`.xls`/`.ppt`, macro formats, encrypted files | Not Scanned. No OCR or password guessing. |

Office media/embedded data, ZIP comments/extra data, external relationships and NTFS alternate streams appear as scan notes. They do not cause a keyword failure. Unreadable content is Not Scanned; errors enumerating directories leave the folder scan incomplete. Office attributes, XML tails, package part names and file/directory names are also checked. Source content and keywords never leave the app: no network requests, telemetry, cloud processing, document execution or Office automation.

Limits: 100 MiB per file; 250 MiB uncompressed Office package; 10,000 package entries; 100,000 matches per text section; 500 keywords, each at most 500 characters. Limit violations require manual review.

## Develop And Build

Python 3.10+ with Tkinter:

```powershell
python -m pip install -r requirements.txt
python app.py
python -m unittest discover -s tests -v
```

`Start SafeLoad.cmd` opens an existing build or the Python application. `Build Windows App.cmd` creates `.venv`, installs pinned runtime dependencies and PyInstaller, builds `dist\SafeLoad\SafeLoad.exe`, then packages the Windows ZIP and SHA-256 file. `scripts/package_release.py --version MAJOR.MINOR.PATCH` packages a built bundle with matching source and dependency licenses. Build on Windows x64; the destination needs no Python runtime.

Maintainers can run **Build Windows Release** in GitHub Actions with a new version to build, test and publish a release. The PowerShell installer uses the latest release's fixed asset names. SafeLoad is distributed under the GNU Affero General Public License v3.0 or later; see [LICENSE](LICENSE). The ZIP includes the matching application source and dependency notices. Runtime dependencies include PyMuPDF, distributed under AGPL or a commercial license; see [its license](https://pymupdf.readthedocs.io/en/latest/about.html#license).
