# SafeLoad

Local Windows desktop application with **Directory** and **Keywords** tabs. No server, network requests, telemetry, cloud processing, or Office automation. File contents and keywords stay on the computer running the app.

## Run

Requires Python 3.10 or newer with Tkinter. From this folder:

```powershell
python -m pip install -r requirements.txt
python app.py
```

Or double-click `Start SafeLoad.cmd` after installing dependencies.

## Use

1. Choose the source directory. All subfolders, including hidden files, are visited; links and junctions are flagged rather than followed.
2. In **Keywords**, enter comma-separated words or phrases. Matching is literal and case-insensitive by default, including inside longer words. Empty entries and exact duplicates are ignored; report numbers follow the resulting unique keyword list. Commas cannot be part of a keyword.
3. **Scan folder** inspects without modifying files. Matching files are red; files with inspection limitations are amber. Select a file to see keyword numbers and locations.
4. **Remove keywords into cleaned folder** creates a new directory, removes supported matches, reopens the serialized output for verification, and leaves originals untouched. The output directory must not exist and must be outside the source tree. Unsupported files are copied unchanged when readable and within size limits, and remain flagged. Files that cannot be copied are reported. Cancellation leaves an incomplete output folder marked not safe.
5. **Save report** exports local JSON outside both folders. Reports include original matches, remaining matches, file changes, and review reasons. Keyword numbers match their entry order in the Keywords tab. Keyword contents and surrounding file text are omitted; paths and format error messages can still be sensitive. Keep the report and originals out of the upload.

**Safe means no configured keyword matches remain in inspected text within the supported scope. It is not a security certification or a guarantee that uploading is permitted.** A missing keyword in the list cannot be detected. Review your organization's upload policy. Close files and keep the source/output folders unchanged while scanning; results describe the scan snapshot, not future edits.

## Format coverage

| Format | Inspection and removal |
| --- | --- |
| Source code and text | Common source/config/data extensions and extensionless UTF-8 text; BOM-marked UTF-8/16/32. Reports line numbers. Unsupported encodings require conversion. |
| Word `.docx` | XML text throughout the package, including paragraphs, split formatting runs, tables, headers, footers, comments and properties. Reports package part and text group. |
| Excel `.xlsx` | XML text throughout the package, including shared strings, inline strings, worksheets, formulas, cached values, comments and properties. Split rich-text runs supported. |
| PowerPoint `.pptx` | XML text throughout the package, including slides, notes, comments, charts and properties. Split formatting runs supported. |
| PDF | Searchable page text and standard metadata inspected. Page matches physically redacted, metadata matches removed, XML metadata cleared on cleaning. Every PDF remains **review required**, because text extraction cannot certify images, outlined letters, arbitrary objects, forms, annotations and embedded data. Non-ASCII/case/search differences can leave residual matches, which are reported. |
| Images, archives, old `.doc`/`.xls`/`.ppt`, macro formats, binaries, password-protected documents | **Review required**. No OCR, password guessing, execution, or silent certification. |

Office media, embedded files, macros, ZIP comments/extra data and external relationships cause **review required**. Office XML attributes and XML tails are inspected but not rewritten, because editing structural fields could corrupt a file; remaining matches block Safe. Package part names and file/directory names are inspected but not renamed. NTFS alternate streams are detected and flagged, not scanned or copied. Unsupported file systems that cannot enumerate streams require review.

Removal preserves surviving Office text in its original formatting runs. It can still change meaning, break source code, invalidate formulas, signatures or document layout. PDFs may remove nearby characters where bounding boxes overlap. Inspect cleaned documents in the relevant applications before use. This is keyword cleaning, not antivirus, DLP certification, image redaction, or a universal file decoder.

Limits: 100 MiB per file, 250 MiB total uncompressed Office package, 10,000 Office package entries, 100,000 matches per text section, 500 keywords, 500 characters per keyword. Limit violations, unreadable content, scan failures and empty folders never receive Safe. Unchanged supported files are copied byte-for-byte. Cleaned files do not retain file-system ACLs, timestamps or alternate streams; the destination inherits its parent's access controls. Choose an appropriately restricted output directory.

## Build for an offline office computer

Run `Build Windows App.cmd` on an approved connected Windows build machine. It installs dependencies into `.venv` and creates a PyInstaller desktop bundle. Copy the **entire** `dist\SafeLoad` directory to the office computer and run `SafeLoad.exe`. Python and Microsoft Office are not required on the destination. The running application does not download anything. The executable is unsigned; deployment must follow your organization's normal software approval process.

For a disconnected build machine, download Windows wheels on a matching Python/Windows machine first:

```powershell
python -m pip download -r requirements.txt pyinstaller==6.16.0 -d wheelhouse
python -m pip install --no-index --find-links wheelhouse -r requirements.txt pyinstaller==6.16.0
python -m PyInstaller --noconfirm --clean --windowed --onedir --name SafeLoad --collect-all pymupdf app.py
```

PyMuPDF is distributed under AGPL or a commercial license. Review its license before distributing an office deployment; dependency licenses are included in the build environment. See [PyMuPDF licensing](https://pymupdf.readthedocs.io/en/latest/about.html#license).

## Verification

```powershell
python -m unittest discover -s tests -v
```

Tests exercise recursive copying, original preservation, encodings, overlapping literal matches, cascading removals, split Office runs, metadata, unsupported content, PDF redaction, cancellation, output isolation, reports and XML entity refusal.
