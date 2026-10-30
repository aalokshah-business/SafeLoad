"""Offline keyword inspection. Unknown content always requires review."""
from __future__ import annotations

import codecs
import copy
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import threading
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from lxml import etree

import legacy

MAX_FILE = 100 * 1024 * 1024
MAX_PACKAGE = 250 * 1024 * 1024
MAX_MATCHES = 100000
TEXT_EXTENSIONS = set((
    '.txt .md .rst .csv .tsv .json .jsonl .xml .yaml .yml .toml .ini .cfg '
    '.conf .log .sql .py .pyw .js .jsx .ts .tsx .html .htm .css .scss .less '
    '.c .h .cpp .hpp .cs .java .kt .go .rs .rb .php .sh .bat .cmd .ps1 '
    '.r .swift .vue .svelte .env .properties .tex .ipynb .gitignore '
    '.mjs .cjs .mts .cts .mdc .mdx .markdown .svg .gradle .kts .lock .xhtml .plist '
    '.csproj .sln .editorconfig .gitattributes .dockerignore .npmrc .prettierrc .eslintrc'
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
        self.case_sensitive = case_sensitive
        self.patterns = [re.compile(re.escape(w), flags) for w in self.words]

    def palindromes(self):
        """Keywords that read the same backwards; reversing them removes nothing."""
        fold = (lambda w: w) if self.case_sensitive else str.lower
        return [w for w in self.words if fold(w) == fold(w)[::-1]]

    def matches(self, text):
        """Non-overlapping matches (index, start, end); the earliest and longest win."""
        found = sorted(self.ranges(text), key=lambda r: (r[1], r[1] - r[2]))
        chosen, position = [], 0
        for index, start, end in found:
            if start >= position:
                chosen.append((index, start, end))
                position = end
        return chosen

    def reverse(self, text):
        """Return text with every keyword match written backwards, plus per-keyword counts."""
        out, counts, position = [], {}, 0
        for index, start, end in self.matches(text):
            out.append(text[position:start])
            out.append(text[start:end][::-1])
            counts[index] = counts.get(index, 0) + 1
            position = end
        out.append(text[position:])
        return ''.join(out), counts

    def matching(self, text):
        """Indexes (1-based) of the keywords that occur in text."""
        return [i for i, pattern in enumerate(self.patterns, 1) if pattern.search(text)]

    def drop_lines(self, text):
        """Remove every line containing a keyword; counts lines per matching keyword."""
        kept, counts = [], {}
        for line in text.split('\n'):
            hits = self.matching(line)
            for index in hits:
                counts[index] = counts.get(index, 0) + 1
            if not hits:
                kept.append(line)
        return '\n'.join(kept), counts

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


def parse_xml(content):
    parser = etree.XMLParser(resolve_entities=False, no_network=True,
                             remove_blank_text=False, strip_cdata=False)
    tree = etree.fromstring(content, parser)
    if tree.getroottree().docinfo.doctype:
        raise ValueError('XML document type declarations are not supported.')
    return tree


def text_groups(nodes):
    """Paragraphs and spreadsheet strings: terms may be split across runs."""
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
    return groups


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
                tree = parse_xml(content)
                nodes = list(tree.iter())
                groups = text_groups(nodes)
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


def legacy_inspect(data, suffix, keys):
    result = Inspection()
    for location, text in legacy.texts(data, suffix):
        keys.inspect(text, location, result)
    result.issues.append('Old binary format: text read from document structures and raw bytes; '
                         'embedded objects are not inspected.')
    return result


def pdf_lib():
    """Load PyMuPDF on first PDF use. A blocked native library must not stop startup."""
    try:
        import pymupdf
    except (ImportError, OSError) as exc:
        raise ValueError(
            'PDF support is unavailable because Windows Application Control blocked the PDF component.'
        ) from exc
    return pymupdf


def pdf(data, keys):
    pymupdf = pdf_lib()
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
    if suffix in legacy.LEGACY:
        return legacy_inspect(data, suffix, keys)
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
    single = source.is_file() and not is_link(source)
    if not (single or source.is_dir()) or is_link(source):
        raise ValueError('Choose an existing, non-linked folder or file.')
    source = source.resolve()
    base = source.parent if single else source
    report = Report(str(source), datetime.now(timezone.utc).isoformat())
    root_check = Inspection()
    if not single:
        keys.inspect(source.name, 'Selected folder name', root_check)
    if root_check.hits:
        report.issues.append('Selected folder name contains a configured keyword.')
    cancel = cancel or threading.Event()

    def error(exc):
        report.issues.append(f'Cannot enumerate directory: {exc.filename} ({type(exc).__name__})')

    walk = [(str(base), [], [source.name])] if single else os.walk(source, followlinks=False, onerror=error)
    for directory, folders, files in walk:
        if cancel.is_set():
            report.cancelled = True
            break
        parent = Path(directory)
        try:
            if not single and alternate_streams(parent):
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
            relative = path.relative_to(base)
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



# ---------------------------------------------------------------- MakeItSafe
# Rewrites files in place. Two actions, removal first, then reversal:
#   clean   - every line (paragraph) containing a keyword is removed;
#   reverse - every remaining keyword match is written backwards ("git" -> "tig").
# Reversal keeps text length, so Office text split across runs can be redistributed exactly.
BOMS = [(codecs.BOM_UTF32_LE, 'utf-32-le'), (codecs.BOM_UTF32_BE, 'utf-32-be'),
        (codecs.BOM_UTF8, 'utf-8'), (codecs.BOM_UTF16_LE, 'utf-16-le'),
        (codecs.BOM_UTF16_BE, 'utf-16-be')]
KEEP_ATTRIBUTES = {'Type', 'ContentType', 'Extension', 'PartName'}  # package structure
KEEP_PARTS = {'[Content_Types].xml'}
STILL = 'Keyword still present: '


def has_words(text):
    return any(w.strip() for w in text.split(','))


class Plan:
    """What MakeItSafe does: keywords to reverse and keywords whose lines are removed."""

    def __init__(self, reverse_text='', clean_text='', case_sensitive=False):
        if not (has_words(reverse_text) or has_words(clean_text)):
            raise ValueError('Enter keywords to reverse, keywords whose lines are removed, or both.')
        self.reverse = Keywords(reverse_text, case_sensitive) if has_words(reverse_text) else None
        self.clean = Keywords(clean_text, case_sensitive) if has_words(clean_text) else None
        if self.reverse and self.reverse.palindromes():
            raise ValueError('These keywords read the same backwards, so reversing cannot remove them: '
                             + ', '.join(self.reverse.palindromes()))
        self.reverse_words = list(self.reverse.words) if self.reverse else []
        self.clean_words = list(self.clean.words) if self.clean else []
        # Every configured word is checked afterwards for matches that survived.
        self.check = Keywords(','.join(self.reverse_words + self.clean_words), case_sensitive)


@dataclass
class SafeFile:
    path: str
    status: str  # Processed, Partly Processed, Unchanged, Skipped or Error
    replaced: dict[str, int] = field(default_factory=dict)  # reverse keyword -> matches reversed
    removed: dict[str, int] = field(default_factory=dict)   # clean keyword -> lines removed
    issues: list[str] = field(default_factory=list)


@dataclass
class SafeReport:
    source: str
    started: str
    reverse_words: list[str]
    clean_words: list[str]
    files: list[SafeFile] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    cancelled: bool = False

    @staticmethod
    def total(files, attribute, words):
        totals = {w: 0 for w in words}
        for item in files:
            for word, number in getattr(item, attribute).items():
                totals[word] += number
        return totals

    @property
    def totals(self):
        return self.total(self.files, 'replaced', self.reverse_words)

    @property
    def removed_totals(self):
        return self.total(self.files, 'removed', self.clean_words)

    def save(self, path: Path):
        body = asdict(self)
        body['totals'] = self.totals
        body['removed_totals'] = self.removed_totals
        body['scope'] = ('Files were rewritten in place: lines containing clean keywords were removed and '
                         'every reverse keyword match was written backwards. '
                         'Report terms and paths may contain sensitive information.')
        path.write_text(json.dumps(body, indent=2, ensure_ascii=False), encoding='utf-8')


def remaining(inspection):
    return list(dict.fromkeys(f'{STILL}{h.keyword_text} · {h.location}' for h in inspection.hits))[:20]


def by_word(keys, counts):
    return {keys.words[i - 1]: n for i, n in counts.items()}


def merge(total, extra):
    for word, number in extra.items():
        total[word] = total.get(word, 0) + number


def safe_string(text, plan):
    """Remove lines containing clean keywords, then reverse reverse keywords: (new text, reversed, removed)."""
    reversed_counts, removed = {}, {}
    new = text
    if plan.clean:
        new, counts = plan.clean.drop_lines(new)
        removed = by_word(plan.clean, counts)
    if plan.reverse:
        new, counts = plan.reverse.reverse(new)
        reversed_counts = by_word(plan.reverse, counts)
    return new, reversed_counts, removed


@dataclass
class TextScan:
    hits: list[Hit]
    spans: list[tuple[int, int]]  # non-overlapping match ranges, for highlighting

    @property
    def safe(self):
        return not self.hits


def scan_text(text, keys):
    """Keyword check of pasted text: one Hit per keyword and line, plus the matched character ranges."""
    counts = {}
    for index, start, _ in keys.ranges(text):
        key = (index, text.count('\n', 0, start) + 1)
        counts[key] = counts.get(key, 0) + 1
    hits = [Hit(index, f'Line {line}', number, keys.words[index - 1])
            for (index, line), number in sorted(counts.items(), key=lambda item: (item[0][1], item[0][0]))]
    return TextScan(hits, [(a, b) for _, a, b in keys.matches(text)])


def make_safe_text(text, plan):
    """MakeItSafe for pasted text: (new text, reversed counts, removed counts, scan of what is left)."""
    new, reversed_counts, removed = safe_string(text, plan)
    return new, reversed_counts, removed, scan_text(new, plan.check)


def safe_text(data, plan):
    bom, codec = next(((b, c) for b, c in BOMS if data.startswith(b)), (b'', 'utf-8'))
    text, _ = decode_text(data)
    new, reversed_counts, removed = safe_string(text, plan)
    if new == text:
        return None, reversed_counts, removed, []
    left = Inspection()
    for index, start, _ in plan.check.ranges(new):
        left.hits.append(Hit(index, f'Line {new.count(chr(10), 0, start) + 1}', 1, plan.check.words[index - 1]))
    return bom + new.encode(codec), reversed_counts, removed, remaining(left)


def local(node):
    return etree.QName(node).localname if isinstance(node.tag, str) else ''


def paragraph_text(node):
    return ''.join(t.text or '' for t in node.iter() if local(t) == 't')


def detach(node):
    """Remove an element but keep the text that follows it."""
    parent, tail = node.getparent(), node.tail
    if tail:
        previous = node.getprevious()
        if previous is not None:
            previous.tail = (previous.tail or '') + tail
        else:
            parent.text = (parent.text or '') + tail
    parent.remove(node)


def deletable(node):
    """A paragraph may be deleted unless the format needs at least one (or a closing) paragraph."""
    parent = node.getparent()
    if parent is None or local(node) != 'p':
        return False
    if not any(s is not node and local(s) == 'p' for s in parent):
        return False
    return not (local(parent) == 'tc' and parent[-1] is node)


def clean_nodes(tree, clean):
    """Remove (or empty) every paragraph / string item whose text contains a clean keyword."""
    removed = {}
    root = tree.getroottree().getroot()
    for node in list(tree.iter()):
        if local(node) not in ('p', 'si', 'is'):
            continue
        if node is not root and root not in node.iterancestors():
            continue  # already removed together with an ancestor
        hits = clean.matching(paragraph_text(node))
        if not hits:
            continue
        for index in hits:
            removed[clean.words[index - 1]] = removed.get(clean.words[index - 1], 0) + 1
        if deletable(node):
            detach(node)
        else:
            for t in node.iter():
                if local(t) == 't':
                    t.text = ''
    return removed


def clean_relationships(tree, clean, source):
    """Drop external links to clean keywords that no remaining content refers to."""
    removed = {}
    for node in list(tree.iter()):
        if local(node) != 'Relationship' or node.get('TargetMode') != 'External':
            continue
        hits = clean.matching(node.get('Target') or '')
        identifier = (node.get('Id') or '').encode()
        if hits and identifier and (b'"' + identifier + b'"') not in source \
                and (b"'" + identifier + b"'") not in source:
            detach(node)
            for index in hits:
                removed[clean.words[index - 1]] = removed.get(clean.words[index - 1], 0) + 1
    return removed


def safe_xml(content, plan, source=None):
    """Return (new bytes or None, reversed counts, removed counts). source: bytes of the part a .rels file describes."""
    tree = parse_xml(content)
    reversed_counts, removed = {}, {}
    if plan.clean:
        removed = clean_relationships(tree, plan.clean, source) if source is not None \
            else clean_nodes(tree, plan.clean)
    if plan.reverse:
        nodes = list(tree.iter())

        def reverse(text):
            new, found = plan.reverse.reverse(text)
            merge(reversed_counts, by_word(plan.reverse, found))
            return new

        for group in text_groups(nodes):
            text = ''.join(n.text or '' for n in group)
            new = reverse(text)
            if new != text:
                position = 0
                for node in group:
                    size = len(node.text or '')
                    node.text = new[position:position + size]
                    position += size
        for node in nodes:
            if isinstance(node.tag, str):
                for attr, value in list(node.attrib.items()):
                    if etree.QName(attr).localname not in KEEP_ATTRIBUTES:
                        new = reverse(value)
                        if new != value:
                            node.set(attr, new)
            if node.tail:
                node.tail = reverse(node.tail)
    if not reversed_counts and not removed:
        return None, reversed_counts, removed
    info = tree.getroottree().docinfo
    return etree.tostring(tree.getroottree(), xml_declaration=True, encoding=info.encoding or 'UTF-8',
                          standalone=info.standalone), reversed_counts, removed


def pdf_lines(page):
    """Yield (text, chars) per text line; chars = (bbox, size, colour, baseline origin) per character."""
    for block in page.get_text('rawdict')['blocks']:
        if block.get('type') != 0:
            continue
        for line in block['lines']:
            text, chars = [], []
            for span in line['spans']:
                color = tuple(((span['color'] >> shift) & 255) / 255 for shift in (16, 8, 0))
                for char in span['chars']:
                    for letter in char['c']:
                        text.append(letter)
                        chars.append((char['bbox'], span['size'], color, char['origin']))
            yield ''.join(text), chars


def union(chars):
    pymupdf = pdf_lib()
    rect = pymupdf.Rect(chars[0][0])
    for char in chars[1:]:
        rect |= pymupdf.Rect(char[0])
    return rect


def safe_pdf(data, plan):
    """Remove matching text lines and rewrite matches backwards, using PDF redaction."""
    pymupdf = pdf_lib()
    counts, removed, issues = {}, {}, ['PDF: images, outlined text, form fields and attachments are not changed.']
    with pymupdf.open(stream=data, filetype='pdf') as doc:
        if doc.needs_pass:
            raise ValueError('Password-protected PDF cannot be changed.')
        changed = False
        for page in doc:
            inserts = []
            for text, chars in pdf_lines(page):
                if not text.strip():
                    continue
                hits = plan.clean.matching(text) if plan.clean else []
                if hits:
                    page.add_redact_annot(union(chars), fill=False, cross_out=False)
                    for index in hits:
                        word = plan.clean.words[index - 1]
                        removed[word] = removed.get(word, 0) + 1
                    continue
                for index, start, end in (plan.reverse.matches(text) if plan.reverse else []):
                    rect = union(chars[start:end])
                    page.add_redact_annot(pymupdf.Rect(rect.x0 + .3, rect.y0, rect.x1 - .3, rect.y1),
                                          fill=False, cross_out=False)
                    new = text[start:end][::-1]
                    size = chars[start][1]
                    width = pymupdf.get_text_length(new, fontname='helv', fontsize=size)
                    if width > rect.width:
                        size = max(3, size * rect.width / width)
                    inserts.append((chars[start][3], new, size, chars[start][2]))
                    word = plan.reverse.words[index - 1]
                    counts[word] = counts.get(word, 0) + 1
            if page.first_annot is not None and (removed or counts or inserts):
                page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                                      graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)
                changed = True
            for origin, new, size, color in inserts:
                page.insert_text(origin, new, fontname='helv', fontsize=size, color=color)
        meta = dict(doc.metadata or {})
        for key, value in list(meta.items()):
            if key in ('format', 'encryption') or not isinstance(value, str) or not value:
                continue
            hits = plan.clean.matching(value) if plan.clean else []
            if hits:
                meta[key] = ''
                for index in hits:
                    word = plan.clean.words[index - 1]
                    removed[word] = removed.get(word, 0) + 1
            elif plan.reverse:
                meta[key], found = plan.reverse.reverse(value)
                merge(counts, by_word(plan.reverse, found))
            changed = changed or meta[key] != value
        if changed:
            doc.set_metadata({k: v for k, v in meta.items() if k not in ('format', 'encryption')})
            if plan.check.matching(doc.get_xml_metadata() or ''):
                doc.set_xml_metadata('')
                issues.append('XMP metadata contained a keyword and was removed.')
        if not changed:
            return None, counts, removed, issues
        new = doc.tobytes(garbage=3, deflate=True)
    issues.extend(remaining(pdf(new, plan.check)))
    return new, counts, removed, issues


def relationship_source(name):
    """word/_rels/document.xml.rels -> word/document.xml"""
    folder, _, base = name.rpartition('/')
    if not folder.endswith('_rels'):
        return ''
    parent = folder[:-len('_rels')].rstrip('/')
    return (parent + '/' if parent else '') + base[:-len('.rels')]


def safe_office(data, suffix, plan):
    counts, removed, issues, changed = {}, {}, [], {}
    with zipfile.ZipFile(io.BytesIO(data)) as package:
        entries = package.infolist()
        if len(entries) > 10000 or sum(e.file_size for e in entries) > MAX_PACKAGE:
            raise ValueError('Office package exceeds size limits.')
        names = [e.filename for e in entries]
        if len(names) != len(set(names)) or OFFICE[suffix] not in names:
            raise ValueError('Invalid or ambiguous Office package.')
        media = False
        xml = [e for e in entries if e.filename.endswith(('.xml', '.rels')) and e.filename not in KEEP_PARTS]
        for entry in entries:
            if entry.flag_bits & 1:
                raise ValueError('Encrypted package entry cannot be changed.')
            if entry not in xml and entry.file_size and not entry.is_dir():
                media = True
        # Relationship files last: they need the final content of the part they describe.
        for entry in sorted(xml, key=lambda e: e.filename.endswith('.rels')):
            source = None
            if entry.filename.endswith('.rels'):
                name = relationship_source(entry.filename)
                source = changed[name] if name in changed else package.read(name) if name in names else b''
            new, found, gone = safe_xml(package.read(entry), plan, source)
            merge(counts, found)
            merge(removed, gone)
            if new is not None:
                changed[entry.filename] = new
        if not changed:
            return None, counts, removed, issues
        if media:
            issues.append('Embedded media, macro or binary parts were not changed.')
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as target:
            target.comment = package.comment
            for entry in entries:
                content = changed[entry.filename] if entry.filename in changed else package.read(entry)
                target.writestr(copy.copy(entry), content)
    new = output.getvalue()
    issues.extend(remaining(office(new, suffix, plan.check)))
    return new, counts, removed, issues


def safe_legacy(data, suffix, plan):
    """Edit an old binary Office file (.doc, .xls, .ppt) in place at the same length."""
    new, counts, removed, left = legacy.edit(data, suffix, plan.reverse, plan.clean, plan.check)
    if new is None:
        return None, counts, removed, []
    issues = ['Old binary format edited in place: embedded objects and formatting are not changed.']
    if removed:
        issues.append('Removed lines were blanked with spaces; the old format cannot delete text safely.')
    issues.extend(f'{STILL}{word} · {where}' for word, where in left[:20])
    return new, counts, removed, issues


def write_atomic(path, data):
    handle, temporary = tempfile.mkstemp(prefix='.safeload-', dir=path.parent)
    try:
        with os.fdopen(handle, 'wb') as stream:
            stream.write(data)
        shutil.copymode(path, temporary)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def make_safe_file(path, relative, plan):
    """Return a SafeFile for one file, rewriting it when keywords were found."""
    item = SafeFile(str(relative), 'Unchanged')
    named = Inspection()
    plan.check.inspect(str(relative), 'File or directory name', named)
    if named.hits:
        item.issues.append('File or folder name contains a keyword; rename it manually.')
    try:
        info = path.stat()
        if is_link(path) or not stat.S_ISREG(info.st_mode):
            raise ValueError('Linked or special file not changed.')
        if info.st_size > MAX_FILE:
            raise ValueError('File exceeds 100 MiB limit.')
        data = path.read_bytes()
        suffix = path.suffix.lower()
        if data.startswith(b'%PDF-') or suffix == '.pdf':
            new, counts, removed, left = safe_pdf(data, plan)
        elif suffix in OFFICE:
            new, counts, removed, left = safe_office(data, suffix, plan)
        elif suffix in legacy.LEGACY:
            new, counts, removed, left = safe_legacy(data, suffix, plan)
        elif suffix in TEXT_EXTENSIONS or path.name.lower() in TEXT_EXTENSIONS or not suffix:
            new, counts, removed, left = safe_text(data, plan)
        else:
            item.status = 'Skipped'
            item.issues.append(f'Unsupported file type: {suffix}. Not changed.')
            return item
        if new is not None:
            after = path.stat()
            if (info.st_size, info.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError('File changed while processing; nothing was written. Retry with files closed.')
            write_atomic(path, new)
            item.replaced, item.removed = counts, removed
            item.status = 'Processed'
        item.issues.extend(left)
    except Exception as exc:
        item.status = 'Error'
        item.issues.append(f'{type(exc).__name__}: {exc}')
    if item.status in ('Processed', 'Unchanged') and (
            named.hits or any(i.startswith(STILL) for i in item.issues)):
        item.status = 'Partly Processed'
    return item


def make_safe_folder(source, plan, cancel=None, progress=None):
    source = Path(source).absolute()
    single = source.is_file() and not is_link(source)
    if not (single or source.is_dir()) or is_link(source):
        raise ValueError('Choose an existing, non-linked folder or file.')
    source = source.resolve()
    base = source.parent if single else source
    report = SafeReport(str(source), datetime.now(timezone.utc).isoformat(),
                        list(plan.reverse_words), list(plan.clean_words))
    cancel = cancel or threading.Event()

    def error(exc):
        report.issues.append(f'Cannot enumerate directory: {exc.filename} ({type(exc).__name__})')

    walk = [(str(base), [], [source.name])] if single else os.walk(source, followlinks=False, onerror=error)
    for directory, folders, files in walk:
        parent = Path(directory)
        for folder in list(folders):
            try:
                if is_link(parent / folder):
                    folders.remove(folder)
                    report.issues.append('Linked directory not changed: ' + str((parent / folder).relative_to(source)))
            except OSError as exc:
                folders.remove(folder)
                error(exc)
        for filename in sorted(files):
            if cancel.is_set():
                report.cancelled = True
                break
            path = parent / filename
            report.files.append(make_safe_file(path, path.relative_to(base), plan))
            if progress:
                progress(len(report.files), str(path.relative_to(base)))
        if report.cancelled:
            break
    if not report.files and not report.cancelled:
        report.issues.append('No files found.')
    order = {'Error': 0, 'Partly Processed': 1, 'Processed': 2, 'Skipped': 3, 'Unchanged': 4}
    report.files.sort(key=lambda item: (order[item.status], item.path.casefold()))
    return report
