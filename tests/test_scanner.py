import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

from lxml import etree
import pymupdf

from scanner import Keywords, inspect_file, scan_folder

TEST_TMP = Path(__file__).resolve().parents[1] / 'tmp' / 'tests'
TEST_TMP.mkdir(parents=True, exist_ok=True)


def package(main, body, extra=None):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr(main, body)
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return output.getvalue()


class ScannerTests(unittest.TestCase):
    def test_literals_overlaps_cascade_and_case(self):
        keys = Keywords('secret, SECRET, a.b, aba, bab')
        self.assertEqual(keys.remove('SECRET a.b abab'), '  ')
        self.assertEqual(Keywords('secret').remove('seSECRETcret'), '')
        self.assertEqual(Keywords('secret', True).remove('SECRET secret'), 'SECRET ')
        with self.assertRaises(ValueError):
            Keywords(' , ')

    def test_text_encoding_line_locations_and_source_preserved(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as directory:
            base = Path(directory)
            source = base / 'input'
            source.mkdir()
            (source / 'nested').mkdir()
            original = 'Keep\r\nSecret secret\r\n'.encode('utf-16')
            (source / 'nested' / 'test.py').write_bytes(original)
            keys = Keywords('secret')
            before = scan_folder(source, keys)
            self.assertFalse(before.safe)
            self.assertEqual(before.files[0].found[0].location, 'Line 2')
            result = scan_folder(source, keys, base / 'clean')
            self.assertTrue(result.safe, result)
            self.assertTrue(result.files[0].changed)
            self.assertEqual((source / 'nested' / 'test.py').read_bytes(), original)
            self.assertEqual((base / 'clean' / 'nested' / 'test.py').read_bytes().decode('utf-16'), 'Keep\r\n \r\n')

    def test_office_split_runs_and_metadata_all_three_types(self):
        for suffix, main, body in [
            ('.docx', 'word/document.xml', '<w:document xmlns:w="urn:w"><w:p><w:r><w:t>Keep Sec</w:t></w:r><w:r><w:t>ret End</w:t></w:r></w:p></w:document>'),
            ('.xlsx', 'xl/workbook.xml', '<s:sst xmlns:s="urn:s"><s:si><s:r><s:t>Keep Sec</s:t></s:r><s:r><s:t>ret End</s:t></s:r></s:si></s:sst>'),
            ('.pptx', 'ppt/presentation.xml', '<a:root xmlns:a="urn:a"><a:p><a:r><a:t>Keep Sec</a:t></a:r><a:r><a:t>ret End</a:t></a:r></a:p></a:root>'),
        ]:
            with self.subTest(suffix=suffix):
                data = package(main, body, {'docProps/core.xml': '<root><title>Secret title</title></root>'})
                cleaned, before = inspect_file(data, Path('x' + suffix), Keywords('secret'), True)
                self.assertEqual(sum(h.count for h in before.hits), 2)
                _, after = inspect_file(cleaned, Path('x' + suffix), Keywords('secret'))
                self.assertFalse(after.hits)
                self.assertFalse(after.issues)
                with zipfile.ZipFile(io.BytesIO(cleaned)) as z:
                    root = etree.fromstring(z.read(main))
                    self.assertEqual(''.join(root.itertext()), 'Keep  End')

    def test_office_images_and_attribute_matches_block_safe(self):
        data = package('word/document.xml', '<root name="Secret"><p><t>Secret</t></p></root>',
                       {'word/media/image.png': b'not-inspected'})
        cleaned, _ = inspect_file(data, Path('x.docx'), Keywords('secret'), True)
        _, result = inspect_file(cleaned, Path('x.docx'), Keywords('secret'))
        self.assertTrue(result.hits)
        self.assertIn('attribute', result.hits[0].location)
        self.assertTrue(result.issues)

    def test_pdf_redaction_metadata_and_fail_closed_status(self):
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_text((72, 72), 'Keep Secret End')
            doc.set_metadata({'title': 'Secret title'})
            original = doc.tobytes()
        cleaned, before = inspect_file(original, Path('x.pdf'), Keywords('secret'), True)
        self.assertTrue(before.hits)
        _, after = inspect_file(cleaned, Path('x.pdf'), Keywords('secret'))
        self.assertFalse(after.hits)
        self.assertTrue(after.issues, 'PDF must require review even with no extractable matches')
        with pymupdf.open(stream=cleaned, filetype='pdf') as doc:
            self.assertNotIn('Secret', doc[0].get_text())
            self.assertIn('Keep', doc[0].get_text())
            self.assertNotIn('Secret', doc.metadata['title'])

    def test_unknown_legacy_binary_and_corrupt_files_block_safe(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as directory:
            root = Path(directory) / 'input'
            root.mkdir()
            for name, data in [('old.doc', b'legacy'), ('archive.zip', b'zip'),
                               ('bad.docx', b'invalid'), ('bad.txt', b'\x00\x01')]:
                (root / name).write_bytes(data)
            result = scan_folder(root, Keywords('secret'), Path(directory) / 'clean')
            self.assertFalse(result.safe)
            self.assertTrue(all(f.status == 'REVIEW' for f in result.files))
            self.assertEqual((Path(directory) / 'clean' / 'old.doc').read_bytes(), b'legacy')

    def test_path_keywords_are_not_silently_renamed(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as directory:
            root = Path(directory) / 'input'
            root.mkdir()
            (root / 'secret.txt').write_text('fine')
            report = scan_folder(root, Keywords('secret'), Path(directory) / 'clean')
            self.assertFalse(report.safe)
            self.assertEqual(report.files[0].remaining[0].location, 'File or directory name')

    def test_cancel_empty_nested_output_and_existing_output(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as directory:
            root = Path(directory)
            self.assertFalse(scan_folder(root, Keywords('secret')).safe)
            cancel = threading.Event()
            cancel.set()
            result = scan_folder(root, Keywords('secret'), cancel=cancel)
            self.assertTrue(result.cancelled)
            self.assertFalse(result.safe)
            for target in [root, root / 'nested', root.parent]:
                with self.assertRaises(ValueError):
                    scan_folder(root, Keywords('secret'), target)
            source = root / 'input'
            target = root / 'clean'
            source.mkdir()
            target.mkdir()
            with self.assertRaises(FileExistsError):
                scan_folder(source, Keywords('secret'), target)

    def test_report_no_keyword_values_or_file_contents(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as directory:
            root = Path(directory) / 'input'
            root.mkdir()
            (root / 'test.txt').write_text('super-sensitive content')
            report = scan_folder(root, Keywords('super-sensitive'))
            path = Path(directory) / 'report.json'
            report.save(path)
            text = path.read_text()
            self.assertNotIn('super-sensitive', text)
            self.assertFalse(json.loads(text)['safe_for_configured_keywords'])

    def test_xml_entities_refused(self):
        data = package('word/document.xml', '<!DOCTYPE root [<!ENTITY x SYSTEM "file:///C:/secrets">]><root>&x;</root>')
        with self.assertRaises(ValueError):
            inspect_file(data, Path('x.docx'), Keywords('secret'))

    def test_match_limit_requires_review(self):
        with patch('scanner.MAX_MATCHES', 2):
            with self.assertRaises(ValueError):
                inspect_file(b'xxx', Path('test.txt'), Keywords('x'))


if __name__ == '__main__':
    unittest.main()
