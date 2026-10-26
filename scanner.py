"""Offline keyword inspection. Unknown content always requires review."""
from __future__ import annotations

import codecs
import io
import json
import os
from pathlib import Path
import re
import stat
import threading
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from lxml import etree
import pymupdf

MAX_FILE = 100 * 1024 * 1024
MAX_PACKAGE = 250 * 1024 * 1024
MAX_MATCHES = 100000
TEXT_EXTENSIONS = set((
    '.txt .md .rst .csv .tsv .json .jsonl .xml .yaml .yml .toml .ini .cfg '
    '.conf .log .sql .py .pyw .js .jsx .ts .tsx .html .htm .css .scss .less '
    '.c .h .cpp .hpp .cs .java .kt .go .rs .rb .php .sh .bat .cmd .ps1 '
    '.r .swift .vue .svelte .env .properties .tex .ipynb .gitignore'
).split())
OFFICE = {'.docx': 'word/document.xml', '.xlsx': 'xl/workbook.xml',
          '.pptx': 'ppt/presentation.xml'}


@dataclass
class Hit:
    keyword: int
    location: str
    count: int
    keyword_text: str = ''


@dataclass
class Inspection:
    hits: list[Hit] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


@dataclass
class FileResult:
    path: str
    status: str
    found: list[Hit]
    issues: list[str]


@dataclass
class Report:
    source: str
    started: str
    files: list[FileResult] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    cancelled: bool = False

    @property
    def safe(self):
        return bool(self.files) and not self.cancelled and not self.issues and all(
            f.status == 'OK' for f in self.files)

    def save(self, path: Path):
        body = asdict(self)
        body['safe_for_configured_keywords'] = self.safe
        body['scope'] = ('Keyword inspection only; not a security certification. '
                         'Matched keyword names are included. '
                         'Report terms and paths may contain sensitive information.')
        path.write_text(json.dumps(body, indent=2, ensure_ascii=False), encoding='utf-8')


class Keywords:
    def __init__(self, text: str, case_sensitive=False):
        self.words = list(dict.fromkeys(w.strip() for w in text.split(',') if w.strip()))
        if not self.words:
            raise ValueError('Enter at least one comma-separated keyword.')
        if len(self.words) > 500 or any(len(w) > 500 for w in self.words):
            raise ValueError('Use at most 500 keywords, each at most 500 characters.')
        flags = 0 if case_sensitive else re.IGNORECASE
        self.patterns = [re.compile(re.escape(w), flags) for w in self.words]

    def ranges(self, text):
        # Look ahead one character after every match so overlapping matches count.
        count = 0
        for index, pattern in enumerate(self.patterns, 1):
            start = 0
            while start <= len(text):
                match = pattern.search(text, start)
                if match is None:
                    break
                count += 1
                if count > MAX_MATCHES:
                    raise ValueError('More than 100,000 matches in one text section; manual review required.')
                yield index, match.start(), match.end()
                start = match.start() + 1

    def inspect(self, text, location, inspection):
        counts = {}
        for index, _, _ in self.ranges(text):
            counts[index] = counts.get(index, 0) + 1
        inspection.hits.extend(Hit(i, location, n, self.words[i - 1]) for i, n in counts.items())

def decode_text(data):
    for bom, encoding in [(codecs.BOM_UTF32_LE, 'utf-32'),
                          (codecs.BOM_UTF32_BE, 'utf-32'),
                          (codecs.BOM_UTF8, 'utf-8-sig'),
                          (codecs.BOM_UTF16_LE, 'utf-16'),
                          (codecs.BOM_UTF16_BE, 'utf-16')]:
        if data.startswith(bom):
            return data.decode(encoding), encoding
    value = data.decode('utf-8')
    if any(ord(c) < 32 and c not in '\n\r\t\f' for c in value):
        raise ValueError('Binary or unsupported text encoding; convert to UTF-8 for inspection.')
    return value, 'utf-8'


def office(data, suffix, keys):
    result = Inspection()
    with zipfile.ZipFile(io.BytesIO(data)) as package:
        entries = package.infolist()
        if len(entries) > 10000 or sum(e.file_size for e in entries) > MAX_PACKAGE:
            raise ValueError('Office package exceeds inspection limits.')
        names = [e.filename for e in entries]
        if len(names) != len(set(names)) or OFFICE[suffix] not in names:
            raise ValueError('Invalid or ambiguous Office package.')
        keys.inspect(package.comment.decode('utf-8', errors='replace'), 'ZIP comment', result)
        if package.comment:
            result.issues.append('ZIP comment requires manual review.')
        for entry in entries:
            keys.inspect(entry.filename, 'Package part name: ' + entry.filename, result)
            if entry.flag_bits & 1:
                raise ValueError('Encrypted package entry cannot be inspected.')
            content = package.read(entry)
            name = entry.filename
            if entry.extra or entry.comment:
                result.issues.append('ZIP extra data/comment requires review: ' + name)
            if name.endswith(('.xml', '.rels')):
                parser = etree.XMLParser(resolve_entities=False, no_network=True,
                                         remove_blank_text=False, strip_cdata=False)
                tree = etree.fromstring(content, parser)
                if tree.getroottree().docinfo.doctype:
                    raise ValueError('XML document type declarations are not supported.')
                nodes = list(tree.iter())
                # Paragraphs and spreadsheet strings: detect terms split across runs.
                groups, used = [], set()
                for node in nodes:
                    if not isinstance(node.tag, str):
                        continue
                    local = etree.QName(node).localname
                    if local in {'p', 'si', 'is'}:
                        texts = [n for n in node.iter() if isinstance(n.tag, str)
                                 and etree.QName(n).localname == 't' and n.text is not None
                                 and n not in used]
                        if texts:
                            groups.append(texts)
                            used.update(texts)
                groups.extend([n] for n in nodes if n not in used and n.text)
                for number, group in enumerate(groups, 1):
                    text = ''.join(n.text or '' for n in group)
                    location = f'{name}: text group {number}'
                    keys.inspect(text, location, result)
                for node in nodes:
                    for attr, value in node.attrib.items():
                        keys.inspect(value, f'{name}: attribute {attr}', result)
                    if node.tail:
                        keys.inspect(node.tail, f'{name}: XML tail', result)
                    if isinstance(node.tag, str) and etree.QName(node).localname == 'Relationship':
                        if node.get('TargetMode') == 'External':
                            result.issues.append('External relationship requires review: ' + name)
            elif content and not entry.is_dir():
                result.issues.append('Uninspected media, macro, or embedded part: ' + name)
    return result


def pdf(data, keys):
    result = Inspection()
    with pymupdf.open(stream=data, filetype='pdf') as doc:
        if doc.needs_pass:
            raise ValueError('Password-protected PDF cannot be inspected.')
        # Text extraction cannot certify every PDF object, outlined glyph or image.
        result.issues.append('PDF text scope: searchable text, metadata, annotations and form values inspected; '
                             'images, outlined text, object streams and attachments are not searched.')
        for field_name, value in doc.metadata.items():
            if isinstance(value, str):
                keys.inspect(value, 'PDF metadata: ' + field_name, result)
        keys.inspect(doc.get_xml_metadata(), 'PDF XML metadata', result)
        for page in doc:
            text = page.get_text(sort=True)
            keys.inspect(text, f'Page {page.number + 1}', result)
            for annotation in page.annots() or []:
                for name, value in annotation.info.items():
                    keys.inspect(str(value), f'Page {page.number + 1}: annotation {name}', result)
            for widget in page.widgets() or []:
                keys.inspect(str(widget.field_value), f'Page {page.number + 1}: form field', result)
    return result


def inspect_file(data, path, keys):
    suffix = path.suffix.lower()
    if len(data) > MAX_FILE:
        raise ValueError('File exceeds 100 MiB inspection limit.')
    if data.startswith(b'%PDF-') or suffix == '.pdf':
        return pdf(data, keys)
    if suffix in OFFICE:
        return office(data, suffix, keys)
    if suffix not in TEXT_EXTENSIONS and path.name.lower() not in TEXT_EXTENSIONS and suffix:
        raise ValueError(f'Unsupported file type: {suffix}. No text reader is available for this format.')
    text, _ = decode_text(data)
    result = Inspection()
    # Scan the entire string so multi-line keywords are not missed.
    for index, a, _ in keys.ranges(text):
        result.hits.append(Hit(index, f'Line {text.count(chr(10), 0, a) + 1}', 1, keys.words[index - 1]))
    return result


def is_link(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def alternate_streams(path):
    """Do not silently certify NTFS alternate data streams as inspected."""
    if os.name != 'nt':
        return []
    import ctypes
    from ctypes import wintypes

    class StreamData(ctypes.Structure):
        _fields_ = [('size', ctypes.c_longlong), ('name', wintypes.WCHAR * 296)]

    api = ctypes.WinDLL('kernel32', use_last_error=True)
    first = api.FindFirstStreamW
    first.argtypes = [wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(StreamData), wintypes.DWORD]
    first.restype = wintypes.HANDLE
    next_stream = api.FindNextStreamW
    next_stream.argtypes = [wintypes.HANDLE, ctypes.POINTER(StreamData)]
    next_stream.restype = wintypes.BOOL
    api.FindClose.argtypes = [wintypes.HANDLE]
    value = StreamData()
    handle = first(str(path), 0, ctypes.byref(value), 0)
    if handle == wintypes.HANDLE(-1).value:
        code = ctypes.get_last_error()
        if code == 38:  # ERROR_HANDLE_EOF: no streams, including empty directories.
            return []
        raise OSError(code, 'Cannot inspect alternate data streams')
    found = []
    try:
        while True:
            if value.name != '::$DATA':
                found.append(value.name)
            if not next_stream(handle, ctypes.byref(value)):
                code = ctypes.get_last_error()
                if code != 38:
                    raise OSError(code, 'Cannot enumerate alternate data streams')
                break
    finally:
        api.FindClose(handle)
    return found


def scan_folder(source, keys, cancel=None, progress=None):
    source = Path(source).absolute()
    if not source.is_dir() or is_link(source):
        raise ValueError('Choose an existing, non-linked directory.')
    source = source.resolve()
    report = Report(str(source), datetime.now(timezone.utc).isoformat())
    root_check = Inspection()
    keys.inspect(source.name, 'Selected folder name', root_check)
    if root_check.hits:
        report.issues.append('Selected folder name contains a configured keyword.')
    cancel = cancel or threading.Event()

    def error(exc):
        report.issues.append(f'Cannot enumerate directory: {exc.filename} ({type(exc).__name__})')

    for directory, folders, files in os.walk(source, followlinks=False, onerror=error):
        if cancel.is_set():
            report.cancelled = True
            break
        parent = Path(directory)
        try:
            if alternate_streams(parent):
                report.issues.append('Uninspected directory alternate streams: ' + str(parent.relative_to(source)))
        except OSError as exc:
            report.issues.append(f'Cannot inspect directory alternate streams: {parent.relative_to(source)} ({exc})')
        for folder in list(folders):
            try:
                if is_link(parent / folder):
                    folders.remove(folder)
                    report.issues.append('Linked directory not inspected: ' + str((parent / folder).relative_to(source)))
            except OSError as exc:
                folders.remove(folder)
                error(exc)
        for filename in sorted(files):
            if cancel.is_set():
                report.cancelled = True
                break
            path = parent / filename
            relative = path.relative_to(source)
            result = Inspection()
            checked = False
            keys.inspect(str(relative), 'File or directory name', result)
            try:
                if is_link(path) or not stat.S_ISREG(path.stat().st_mode):
                    raise ValueError('Linked or special file not inspected.')
                info = path.stat()
                if info.st_size > MAX_FILE:
                    raise ValueError('File exceeds 100 MiB inspection limit.')
                data = path.read_bytes()
                inspected = inspect_file(data, path, keys)
                checked = True
                result.hits.extend(inspected.hits)
                result.issues.extend(inspected.issues)
                after = path.stat()
                if (info.st_size, info.st_mtime_ns, info.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                    checked = False
                    result.issues.append('Source changed during scan; retry with files closed.')
                if alternate_streams(path):
                    result.issues.append('Uninspected alternate data streams; manual review required.')
            except Exception as exc:
                result.issues.append(f'{type(exc).__name__}: {exc}')
            status = 'Failed' if result.hits else 'OK' if checked else 'Not Scanned'
            report.files.append(FileResult(str(relative), status, result.hits,
                                           list(dict.fromkeys(result.issues))))
            if progress:
                progress(len(report.files), str(relative))
    if not report.files:
        report.issues.append('No files inspected.')
    if cancel.is_set():
        report.cancelled = True
    # Actual keyword matches first, inspection failures next, OK files last.
    status_order = {'Failed': 0, 'Not Scanned': 1, 'OK': 2}
    report.files.sort(key=lambda item: (status_order[item.status], item.path.casefold()))
    return report
