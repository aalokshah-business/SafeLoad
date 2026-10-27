import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

import pymupdf
from scanner import Keywords, Plan, inspect_file, make_safe_folder, scan_folder

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
        self.assertEqual([h.keyword_text for h in result.hits], keys.words)

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
            self.assertEqual(result.files[0].found[0].keyword_text, 'secret')

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

    def test_report_names_matched_keywords_without_file_excerpts(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as directory:
            root = Path(directory) / 'input'
            root.mkdir()
            (root / 'test.txt').write_text('super-sensitive unrelated-file-context')
            report = scan_folder(root, Keywords('super-sensitive'))
            target = Path(directory) / 'report.json'
            report.save(target)
            body = json.loads(target.read_text())
            self.assertEqual(body['files'][0]['found'][0]['keyword_text'], 'super-sensitive')
            self.assertNotIn('unrelated-file-context', target.read_text())
            self.assertFalse(body['safe_for_configured_keywords'])

    def test_xml_entities_and_match_limit(self):
        data = package('word/document.xml', '<!DOCTYPE root [<!ENTITY x SYSTEM "file:///C:/secrets">]><root>&x;</root>')
        with self.assertRaises(ValueError):
            inspect_file(data, Path('x.docx'), Keywords('secret'))
        with patch('scanner.MAX_MATCHES', 2):
            with self.assertRaises(ValueError):
                inspect_file(b'xxx', Path('x.txt'), Keywords('x'))


if __name__ == '__main__':
    unittest.main()


class MakeItSafeTests(unittest.TestCase):
    def test_reverse_overlaps_case_and_palindromes(self):
        keys = Keywords('git, business')
        self.assertEqual(keys.reverse('Git business GIT'), ('tiG ssenisub TIG', {1: 2, 2: 1}))
        self.assertEqual(Keywords('aba, bab').reverse('abab')[0], 'abab')
        self.assertEqual(Keywords('abc, Aba').palindromes(), ['Aba'])
        self.assertEqual(Keywords('Aba', True).palindromes(), [])

    def test_folder_rewrites_text_office_and_leaves_rest(self):
        body = ('<root><p><r><t>Use gi</t></r><r><t>t here</t></r></p>'
                '<link target="https://github.com/x"/></root>')
        docx = package('word/document.xml', body, {'word/media/a.png': b'png'})
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as directory:
            root = Path(directory)
            (root / 'sub').mkdir()
            (root / 'a.txt').write_bytes('Git\r\nbusiness'.encode('utf-16'))
            (root / 'sub' / 'b.txt').write_text('clean')
            (root / 'c.docx').write_bytes(docx)
            (root / 'd.bin').write_bytes(b'git')
            keys = Keywords('git, business')
            report = make_safe_folder(root, Plan('git, business'))
            status = {f.path: f for f in report.files}
            self.assertEqual(status['a.txt'].replaced, {'git': 1, 'business': 1})
            self.assertEqual(status['c.docx'].replaced, {'git': 2})
            self.assertEqual(status[str(Path('sub/b.txt'))].status, 'Unchanged')
            self.assertEqual(status['d.bin'].status, 'Skipped')
            self.assertEqual(report.totals, {'git': 3, 'business': 1})
            self.assertEqual((root / 'a.txt').read_bytes(), 'tiG\r\nssenisub'.encode('utf-16'))
            self.assertEqual((root / 'd.bin').read_bytes(), b'git')
            with zipfile.ZipFile(root / 'c.docx') as z:
                text = z.read('word/document.xml').decode()
                self.assertIn('Use ti', text)
                self.assertIn('g here', text)
                self.assertIn('tighub.com', text)
                self.assertEqual(z.read('word/media/a.png'), b'png')
            self.assertFalse(scan_folder(root, keys).files[0].status == 'Failed' and
                             any(f.status == 'Failed' and f.path != 'd.bin' for f in scan_folder(root, keys).files))
            self.assertFalse(list(root.rglob('.safeload-*')))

    def test_still_present_name_hits_and_cancel(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as directory:
            root = Path(directory)
            (root / 'git.txt').write_text('git tig')
            report = make_safe_folder(root, Plan('git, tig'))
            self.assertEqual(report.files[0].status, 'Partly Processed')
            self.assertTrue(any('still present' in i for i in report.files[0].issues))
            self.assertTrue(any('name contains' in i for i in report.files[0].issues))
            cancel = threading.Event()
            cancel.set()
            self.assertTrue(make_safe_folder(root, Plan('git'), cancel).cancelled)
            with self.assertRaises(ValueError):
                make_safe_folder(root, Plan('abba'))

    def test_plan_needs_words_and_clean_may_be_palindrome(self):
        with self.assertRaises(ValueError):
            Plan(' , ', '')
        self.assertEqual(Plan('', 'abba').clean_words, ['abba'])
        self.assertEqual(Plan('git', 'github').check.words, ['git', 'github'])

    def test_clean_removes_lines_then_reverse_applies(self):
        text = 'keep git\nsee GitHub here\r\nbusiness\nlast github'
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as directory:
            root = Path(directory)
            (root / 'a.txt').write_bytes(text.encode())
            report = make_safe_folder(root, Plan('git, business', 'github'))
            self.assertEqual((root / 'a.txt').read_bytes().decode(), 'keep tig\nssenisub')
            item = report.files[0]
            self.assertEqual((item.status, item.replaced, item.removed),
                             ('Processed', {'git': 1, 'business': 1}, {'github': 2}))
            self.assertEqual(report.removed_totals, {'github': 2})
            only_clean = Plan('', 'github')
            (root / 'b.txt').write_bytes(b'x\ngithub\ny\n')
            make_safe_folder(root, only_clean)
            self.assertEqual((root / 'b.txt').read_bytes().decode(), 'x\ny\n')

    def test_clean_office_paragraphs_cells_and_links(self):
        body = ('<root><body><p><t>Keep</t></p><p><r><t>see Git</t></r><r><t>Hub now</t></r></p>'
                '<p><t>Also keep</t></p></body>'
                '<tbl><tc><p><t>only github</t></p></tc></tbl>'
                '<sst><si><t>github row</t></si><si><t>fine</t></si></sst></root>')
        rels = ('<Relationships>'
                '<Relationship Id="rId1" Target="https://github.com/a" TargetMode="External"/>'
                '<Relationship Id="rId2" Target="https://github.com/b" TargetMode="External"/>'
                '<Relationship Id="rId3" Target="styles.xml"/></Relationships>')
        data = package('word/document.xml', body.replace('<tbl>', '<tbl id="rId2">'),
                       {'word/_rels/document.xml.rels': rels})
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as directory:
            root = Path(directory)
            (root / 'c.docx').write_bytes(data)
            report = make_safe_folder(root, Plan('', 'github'))
            item = report.files[0]
            with zipfile.ZipFile(root / 'c.docx') as z:
                text = z.read('word/document.xml').decode()
                links = z.read('word/_rels/document.xml.rels').decode()
            self.assertNotIn('Hub now', text)
            self.assertIn('Keep', text)
            self.assertIn('Also keep', text)
            self.assertIn('fine', text)
            self.assertNotIn('github row', text)
            self.assertNotIn('only github', text)
            self.assertIn('<tc><p>', text)  # last paragraph of a cell is emptied, not deleted
            self.assertNotIn('rId1', links)  # unreferenced link removed
            self.assertIn('rId2', links)  # still referenced, kept
            self.assertIn('rId3', links)
            self.assertEqual(item.removed['github'], 4)
            self.assertTrue(any('still present' in i for i in item.issues))

    def test_pdf_lines_removed_and_matches_reversed(self):
        document = pymupdf.open()
        page = document.new_page()
        page.insert_text((72, 72), 'keep git and business here', fontsize=12)
        page.insert_text((72, 100), 'see GitHub now', fontsize=12)
        page.insert_text((72, 130), 'last line stays', fontsize=12)
        document.set_metadata({'title': 'My git doc'})
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as directory:
            root = Path(directory)
            (root / 'a.pdf').write_bytes(document.tobytes())
            plan = Plan('git, business', 'github')
            report = make_safe_folder(root, plan)
            item = report.files[0]
            self.assertEqual((item.status, item.replaced, item.removed),
                             ('Processed', {'git': 2, 'business': 1}, {'github': 1}))
            with pymupdf.open(root / 'a.pdf') as result:
                text = result[0].get_text()
                self.assertEqual(result.metadata['title'], 'My tig doc')
            self.assertIn('last line stays', text)
            self.assertNotIn('GitHub', text)
            self.assertIn('ssenisub', text)
            self.assertFalse([h for f in scan_folder(root, plan.check).files for h in f.found])

    def test_modern_script_and_rule_files_are_processed(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as directory:
            root = Path(directory)
            for name in ('a.mjs', 'b.mdc', 'c.cjs'):
                (root / name).write_text('const git = 1\nsee github\n')
            report = make_safe_folder(root, Plan('git', 'github'))
            self.assertEqual({f.status for f in report.files}, {'Processed'})
            self.assertEqual((root / 'b.mdc').read_text(), 'const tig = 1\n')
