import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

import pymupdf
from scanner import Keywords, inspect_file, scan_folder

TEST_TMP = Path(__file__).resolve().parents[1] / 'tmp' / 'tests'
TEST_TMP.mkdir(parents=True, exist_ok=True)


def package(main, body, extra=None):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', '<Types/>')
        z.writestr(main, body)
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return output.getvalue()


class ScannerTests(unittest.TestCase):
    def test_spaces_around_commas_are_ignored(self):
        keys = Keywords('  alpha  , beta,   project key  , ,gamma , alpha ')
        self.assertEqual(keys.words, ['alpha', 'beta', 'project key', 'gamma'])
        result = inspect_file(b'alpha beta project key gamma', Path('x.txt'), keys)
        self.assertEqual(sum(h.count for h in result.hits), 4)

    def test_literals_overlaps_and_case(self):
        self.assertEqual(len(list(Keywords('aba, bab').ranges('abab'))), 2)
        result = inspect_file(b'a.b axb SECRET secret', Path('x.txt'), Keywords('a.b, secret'))
        self.assertEqual(sum(h.count for h in result.hits), 3)
        result = inspect_file(b'SECRET secret', Path('x.txt'), Keywords('secret', True))
        self.assertEqual(len(result.hits), 1)
        with self.assertRaises(ValueError):
            Keywords(' , ')

    def test_recursive_scan_preserves_files_and_orders_failures(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as directory:
            root = Path(directory)
            (root / 'nested').mkdir()
            fixtures = {'a.txt': b'Fine', 'z.txt': b'Secret', 'unknown.bin': b'unknown',
                        'nested/test.py': 'Keep\r\nSecret secret\r\n'.encode('utf-16')}
            for name, data in fixtures.items():
                (root / name).write_bytes(data)
            def snapshot():
                return {p.relative_to(root): (p.read_bytes(), p.stat().st_mtime_ns)
                        for p in root.rglob('*') if p.is_file()}
            before = snapshot()
            result = scan_folder(root, Keywords('secret'))
            self.assertFalse(result.safe)
            self.assertEqual([f.status for f in result.files], ['Failed', 'Failed', 'Not Scanned', 'OK'])
            self.assertTrue(result.files[0].found and result.files[1].found)
            self.assertFalse(result.files[2].found)
            self.assertEqual(result.files[-1].path, 'a.txt')
            nested = next(f for f in result.files if f.path.endswith('test.py'))
            self.assertEqual(nested.found[0].location, 'Line 2')
            self.assertEqual(before, snapshot())

    def test_office_split_runs_and_metadata(self):
        for suffix, main in [('.docx', 'word/document.xml'), ('.xlsx', 'xl/workbook.xml'),
                             ('.pptx', 'ppt/presentation.xml')]:
            with self.subTest(suffix=suffix):
                body = '<root><p><r><t>Keep Sec</t></r><r><t>ret End</t></r></p></root>'
                data = package(main, body, {'docProps/core.xml': '<root><title>Secret title</title></root>'})
                result = inspect_file(data, Path('x' + suffix), Keywords('secret'))
                self.assertEqual(sum(h.count for h in result.hits), 2)
                self.assertFalse(result.issues)

    def test_office_images_and_attributes(self):
        data = package('word/document.xml', '<root name="Secret"><p><t>Secret</t></p></root>',
                       {'word/media/image.png': b'not-inspected'})
        result = inspect_file(data, Path('x.docx'), Keywords('secret'))
        self.assertEqual(sum(h.count for h in result.hits), 2)
        self.assertTrue(result.issues)

    def test_pdf_text_metadata_and_review_limit(self):
        with pymupdf.open() as doc:
            doc.new_page().insert_text((72, 72), 'Keep Secret End')
            doc.set_metadata({'title': 'Secret title'})
            data = doc.tobytes()
        result = inspect_file(data, Path('x.pdf'), Keywords('secret'))
        self.assertEqual(sum(h.count for h in result.hits), 2)
        self.assertTrue(result.issues)

    def test_unknown_corrupt_and_binary_files_fail(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as directory:
            root = Path(directory)
            for name, data in [('old.doc', b'legacy'), ('archive.zip', b'zip'),
                               ('bad.docx', b'invalid'), ('bad.txt', b'\x00\x01')]:
                (root / name).write_bytes(data)
            result = scan_folder(root, Keywords('secret'))
            self.assertFalse(result.safe)
            self.assertTrue(all(f.status == 'Not Scanned' and f.issues for f in result.files))

    def test_scan_notes_do_not_cause_keyword_failure(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as directory:
            root = Path(directory)
            (root / 'catalog.json').write_text('{"name": "catalog"}', encoding='utf-8')
            with patch('scanner.alternate_streams', return_value=[':Zone.Identifier:$DATA']):
                result = scan_folder(root, Keywords('secret'))
            self.assertEqual(result.files[0].status, 'OK')
            self.assertFalse(result.files[0].found)
            self.assertTrue(result.files[0].issues)

    def test_pdf_without_keyword_is_ok_despite_scope_notes(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as directory:
            root = Path(directory)
            with pymupdf.open() as doc:
                doc.new_page().insert_text((72, 72), 'Ordinary text')
                (root / 'example.pdf').write_bytes(doc.tobytes())
            result = scan_folder(root, Keywords('secret'))
            self.assertTrue(result.safe)
            self.assertEqual(result.files[0].status, 'OK')
            self.assertTrue(result.files[0].issues)

    def test_path_keywords(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as directory:
            root = Path(directory)
            (root / 'secret.txt').write_text('fine')
            result = scan_folder(root, Keywords('secret'))
            self.assertEqual(result.files[0].status, 'Failed')
            self.assertEqual(result.files[0].found[0].location, 'File or directory name')

    def test_empty_cancelled_and_safe_results(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as directory:
            root = Path(directory)
            self.assertFalse(scan_folder(root, Keywords('secret')).safe)
            (root / 'example.txt').write_text('fine')
            self.assertTrue(scan_folder(root, Keywords('secret')).safe)
            cancel = threading.Event()
            cancel.set()
            result = scan_folder(root, Keywords('secret'), cancel=cancel)
            self.assertTrue(result.cancelled)
            self.assertFalse(result.safe)

    def test_report_omits_keyword_values(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as directory:
            root = Path(directory) / 'input'
            root.mkdir()
            (root / 'test.txt').write_text('super-sensitive content')
            report = scan_folder(root, Keywords('super-sensitive'))
            target = Path(directory) / 'report.json'
            report.save(target)
            self.assertNotIn('super-sensitive', target.read_text())
            self.assertFalse(json.loads(target.read_text())['safe_for_configured_keywords'])

    def test_xml_entities_and_match_limit(self):
        data = package('word/document.xml', '<!DOCTYPE root [<!ENTITY x SYSTEM "file:///C:/secrets">]><root>&x;</root>')
        with self.assertRaises(ValueError):
            inspect_file(data, Path('x.docx'), Keywords('secret'))
        with patch('scanner.MAX_MATCHES', 2):
            with self.assertRaises(ValueError):
                inspect_file(b'xxx', Path('x.txt'), Keywords('x'))


if __name__ == '__main__':
    unittest.main()
