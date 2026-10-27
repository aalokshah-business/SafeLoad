"""Old binary Office files (.doc, .xls, .ppt): find their text through the file's own structures
and edit it in place, never changing a stream's size.

Text is located exactly (Word piece table, Excel shared strings, PowerPoint text atoms), so only
text bytes are rewritten. Removing a line blanks it with spaces; reversing writes a match backwards.
"""
import io
import struct

import olefile

LEGACY = {'.doc': ('WordDocument',), '.xls': ('Workbook', 'Book'), '.ppt': ('PowerPoint Document',)}
SUMMARY = ('\x05SummaryInformation', '\x05DocumentSummaryInformation')
SEPARATORS = '\r\x07\x0b\x0c\n\x00'


class Run:
    """Text kept in a stream. chars[i] occupies slots[i] = (byte offset, width in bytes)."""

    def __init__(self, buffer):
        self.buffer = buffer
        self.chars = []
        self.slots = []

    def add8(self, offset, count):
        for i in range(count):
            self.chars.append(chr(self.buffer[offset + i]))
            self.slots.append((offset + i, 1))

    def add16(self, offset, count):
        for i in range(count):
            self.chars.append(chr(struct.unpack_from('<H', self.buffer, offset + 2 * i)[0]))
            self.slots.append((offset + 2 * i, 2))

    @property
    def text(self):
        return ''.join(self.chars)

    def put(self, index, char):
        offset, width = self.slots[index]
        if width == 1:
            if ord(char) > 255:
                return False
            self.buffer[offset] = ord(char)
        else:
            struct.pack_into('<H', self.buffer, offset, ord(char))
        self.chars[index] = char
        return True


def check(condition, message='Unsupported or damaged old Office file.'):
    if not condition:
        raise ValueError(message)


def word_runs(word, read_table):
    check(len(word) > 0x1AA and struct.unpack_from('<H', word, 0)[0] == 0xA5EC, 'Not a Word 97-2003 document.')
    flags = struct.unpack_from('<H', word, 10)[0]
    check(not flags & 0x0100, 'Encrypted Word document cannot be changed.')
    csw = struct.unpack_from('<H', word, 32)[0]
    cslw = struct.unpack_from('<H', word, 34 + csw * 2)[0]
    blob = 34 + csw * 2 + 2 + cslw * 4 + 2
    fc_clx, lcb_clx = struct.unpack_from('<II', word, blob + 33 * 8)
    table = read_table('1Table' if flags & 0x0200 else '0Table')
    check(fc_clx + lcb_clx <= len(table))
    position, end, plc = fc_clx, fc_clx + lcb_clx, None
    while position < end:
        kind = table[position]
        if kind == 1:
            position += 3 + struct.unpack_from('<H', table, position + 1)[0]
        elif kind == 2:
            length = struct.unpack_from('<I', table, position + 1)[0]
            plc = table[position + 5:position + 5 + length]
            break
        else:
            check(False)
    check(plc is not None and len(plc) >= 4)
    pieces = (len(plc) - 4) // 12
    cps = struct.unpack_from('<%dI' % (pieces + 1), plc, 0)
    run = Run(word)
    for i in range(pieces):
        fc = struct.unpack_from('<I', plc, (pieces + 1) * 4 + i * 8 + 2)[0]
        count = cps[i + 1] - cps[i]
        check(count >= 0)
        if fc & 0x40000000:
            offset = (fc & 0x3FFFFFFF) // 2
            check(offset + count <= len(word))
            run.add8(offset, count)
        else:
            check(fc + 2 * count <= len(word))
            run.add16(fc, count)
    return [run]


class Segments:
    """The data of an SST record and its CONTINUE records, read as one sequence."""

    def __init__(self, parts):
        self.parts = parts  # [(offset, length)]
        self.part = 0
        self.used = 0

    def available(self):
        return self.parts[self.part][1] - self.used

    def offset(self):
        return self.parts[self.part][0] + self.used

    def finished(self):
        return self.part == len(self.parts) - 1 and self.available() == 0

    def next_part(self):
        check(self.part + 1 < len(self.parts))
        self.part += 1
        self.used = 0

    def skip(self, count):
        while count:
            if self.available() == 0:
                self.next_part()
            step = min(count, self.available())
            self.used += step
            count -= step

    def read(self, stream, count):
        out = b''
        while count:
            if self.available() == 0:
                self.next_part()
            step = min(count, self.available())
            out += bytes(stream[self.offset():self.offset() + step])
            self.used += step
            count -= step
        return out


def excel_runs(book):
    runs, position, parts = [], 0, None
    while position + 4 <= len(book):
        kind, length = struct.unpack_from('<HH', book, position)
        data = position + 4
        check(data + length <= len(book))
        check(kind != 0x002F, 'Encrypted Excel workbook cannot be changed.')
        if kind == 0x00FC and parts is None:
            parts = [(data, length)]
        elif kind == 0x003C and parts is not None:
            parts.append((data, length))
        elif parts is not None:
            break
        position = data + length
    if parts is None:
        return runs
    walker = Segments(parts)
    unique = struct.unpack('<II', walker.read(book, 8))[1]
    for _ in range(unique):
        cch, flags = struct.unpack('<HB', walker.read(book, 3))
        runs_count = struct.unpack('<H', walker.read(book, 2))[0] if flags & 8 else 0
        extra = struct.unpack('<I', walker.read(book, 4))[0] if flags & 4 else 0
        high, remaining, run = flags & 1, cch, Run(book)
        while remaining:
            width = 2 if high else 1
            if walker.available() == 0:
                walker.next_part()
                high = walker.read(book, 1)[0] & 1
                continue
            step = min(remaining, walker.available() // width)
            check(step > 0)
            (run.add16 if high else run.add8)(walker.offset(), step)
            walker.skip(step * width)
            remaining -= step
        walker.skip(4 * runs_count + extra)
        runs.append(run)
    check(walker.finished(), 'Excel shared strings have an unusual layout; file not changed.')
    return runs


def powerpoint_runs(stream):
    runs = []

    def walk(position, end):
        while position + 8 <= end:
            version, kind, length = struct.unpack_from('<HHI', stream, position)
            body = position + 8
            check(body + length <= end)
            if version & 0xF == 0xF:
                walk(body, body + length)
            elif kind in (0x0FA0, 0x0FBA):
                check(length % 2 == 0)
                run = Run(stream)
                run.add16(body, length // 2)
                runs.append(run)
            elif kind == 0x0FA8:
                run = Run(stream)
                run.add8(body, length)
                runs.append(run)
            position = body + length
        check(position == end)

    walk(0, len(stream))
    return runs


def locate(ole, suffix):
    """Return (main stream name, text runs) after reading every stream into bytearrays."""
    names = ['/'.join(s) for s in ole.listdir(streams=True, storages=False)]
    main = next((n for n in LEGACY[suffix] if n in names), None)
    check(main is not None, 'Not a valid old Office file.')
    buffers = {name: bytearray(ole.openstream(name).read()) for name in [main, *[n for n in SUMMARY if n in names]]}
    if suffix == '.doc':
        word = buffers[main]
        flags = struct.unpack_from('<H', word, 10)[0] if len(word) > 12 else 0
        table = '1Table' if flags & 0x0200 else '0Table'
        check(table in names)
        buffers[table] = bytearray(ole.openstream(table).read())
        runs = word_runs(word, lambda name: buffers[name])
    elif suffix == '.xls':
        runs = excel_runs(buffers[main])
    else:
        runs = powerpoint_runs(buffers[main])
    return main, buffers, runs


def read_chars(buffer, start, width):
    if width == 1:
        return [chr(b) for b in buffer]
    count = (len(buffer) - start) // 2
    return [chr(u) for u in struct.unpack_from('<%dH' % count, buffer, start)]


def write_chars(buffer, start, width, index, text):
    for i, char in enumerate(text):
        if width == 1:
            buffer[index + i] = ord(char)
        else:
            struct.pack_into('<H', buffer, start + 2 * (index + i), ord(char))


RAW_VIEWS = ((0, 1), (0, 2), (1, 2))  # 8-bit text, UTF-16 at both byte alignments


def raw_edit(buffer, reverse, clean, counts, removed, views=RAW_VIEWS):
    """Edit text found by scanning raw bytes (used for the small document property streams)."""
    for start, width in views:
        for keys, found, blank in ((clean, removed, True), (reverse, counts, False)):
            if keys is None:
                continue
            text = ''.join(read_chars(buffer, start, width))
            for index, a, b in keys.matches(text):
                write_chars(buffer, start, width, a, ' ' * (b - a) if blank else text[a:b][::-1])
                found[keys.words[index - 1]] = found.get(keys.words[index - 1], 0) + 1


def edit_run(run, reverse, clean, counts, removed):
    if clean:
        text, begin = run.text, 0
        for number in range(len(text) + 1):
            if number == len(text) or text[number] in SEPARATORS:
                hits = clean.matching(text[begin:number])
                if hits:
                    for index in range(begin, number):
                        run.put(index, ' ')
                    for hit in hits:
                        word = clean.words[hit - 1]
                        removed[word] = removed.get(word, 0) + 1
                begin = number + 1
    if reverse:
        text = run.text
        for index, a, b in reverse.matches(text):
            if len({run.slots[i][1] for i in range(a, b)}) != 1:
                continue
            if all(run.put(a + k, c) for k, c in enumerate(text[a:b][::-1])):
                word = reverse.words[index - 1]
                counts[word] = counts.get(word, 0) + 1


def open_ole(data, write=False):
    check(olefile.isOleFile(data), 'Not a valid old Office file.')
    return olefile.OleFileIO(io.BytesIO(data) if not write else data, write_mode=write)


def texts(data, suffix):
    """Yield (location, text) for scanning: structured text first, then raw bytes of every inspected stream."""
    with open_ole(data) as ole:
        _, buffers, runs = locate(ole, suffix)
    label = {'.doc': 'Document text', '.xls': 'Cell text', '.ppt': 'Slide text'}[suffix]
    for number, run in enumerate(runs, 1):
        yield (label if suffix == '.doc' else f'{label} {number}'), run.text
    for name, buffer in buffers.items():
        for start, width in RAW_VIEWS:
            yield 'Raw bytes of ' + name.replace('\x05', ''), ''.join(read_chars(buffer, start, width))


def edit(data, suffix, reverse, clean, verify):
    """Return (new bytes or None, reversed counts, removed counts, [(word, location)] still present)."""
    counts, removed = {}, {}
    with open_ole(data) as ole:
        _, buffers, runs = locate(ole, suffix)
    originals = {name: bytes(buffer) for name, buffer in buffers.items()}
    for run in runs:
        edit_run(run, reverse, clean, counts, removed)
    for name, buffer in buffers.items():
        if name in SUMMARY:
            raw_edit(buffer, reverse, clean, counts, removed)
        elif name.endswith('Table'):  # Word keeps copies of the title and similar strings here, as UTF-16
            raw_edit(buffer, reverse, clean, counts, removed, RAW_VIEWS[1:])
    changed = {name: bytes(buffer) for name, buffer in buffers.items() if bytes(buffer) != originals[name]}
    left = []
    if changed:
        stream = io.BytesIO(data)
        ole = olefile.OleFileIO(stream, write_mode=True)
        try:
            for name, content in changed.items():
                ole.write_stream(name, content)
        finally:
            ole.close()
        new = stream.getvalue()
        for where, text in texts(new, suffix):
            for index, _, _ in verify.matches(text):
                left.append((verify.words[index - 1], where))
        return new, counts, removed, list(dict.fromkeys(left))
    return None, counts, removed, left
