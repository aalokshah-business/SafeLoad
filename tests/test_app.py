from pathlib import Path
import json
import tempfile
import time
import unittest
from tkinter import ttk
from app import App
from scanner import FileResult, Hit, Report


def make_app():
    base = Path(__file__).resolve().parents[1] / 'tmp' / 'tests'
    base.mkdir(parents=True, exist_ok=True)
    state = Path(tempfile.mkdtemp(dir=base)) / 'settings.json'
    return App(settings_path=state)


def close_app(app):
    app.update()
    for callback in app.tk.call('after', 'info'):
        app.after_cancel(callback)
    app.destroy()


class DesktopTests(unittest.TestCase):
    def test_scan_only_sorting_title_case_and_manual_rescan(self):
        base = Path(__file__).resolve().parents[1] / 'tmp' / 'tests'
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as folder:
            root = Path(folder)
            (root / 'a.txt').write_text('Fine')
            (root / 'z.txt').write_text('Secret')
            app = make_app()
            app.withdraw()
            try:
                self.assertFalse(hasattr(app, 'clean_button'))
                self.assertFalse(hasattr(app, 'output'))
                def check_buttons(widget):
                    for child in widget.winfo_children():
                        if isinstance(child, ttk.Button):
                            title = child.cget('text')
                            self.assertTrue(all(w[0].isupper() for w in title.split()), title)
                        check_buttons(child)
                check_buttons(app)
                app.source.set(str(root))
                app.keywords.insert('1.0', 'secret')
                app.update()
                for safe in (False, True):
                    if safe:
                        (root / 'z.txt').write_text('Manually resolved')
                    app.scan_button.invoke()
                    deadline = time.monotonic() + 10
                    while app.running and time.monotonic() < deadline:
                        app.update()
                        time.sleep(.01)
                    self.assertFalse(app.running)
                    self.assertIsNotNone(app.report)
                    self.assertEqual(app.report.safe, safe)
                    rows = [app.table.item(i, 'values') for i in app.table.get_children()]
                    if not safe:
                        self.assertEqual(rows[0][:2], ('Failed', 'z.txt'))
                        self.assertEqual((root / 'z.txt').read_text(), 'Secret')
                    else:
                        self.assertTrue(all(row[0] == 'OK' for row in rows))
                        self.assertEqual(app.state_text.get(), 'Safe — No Keyword Matches Found')
                app.case.set(True)
                self.assertIsNone(app.report)
                self.assertIn('Not Scanned', app.state_text.get())
            finally:
                close_app(app)


class ResultLayoutTests(unittest.TestCase):
    def test_details_name_keywords_and_explain_skipped_files(self):
        app = make_app()
        app.withdraw()
        try:
            report = Report('C:/example', 'test', files=[
                FileResult('book.json', 'Failed', [Hit(4, 'Line 17', 1, 'secret key')], []),
                FileResult('locked.bin', 'Not Scanned', [], ['PermissionError: Access is denied.']),
            ], issues=['Cannot enumerate directory: private'])
            app.finish('done', report)
            app.table.selection_set('0')
            app.show_details()
            details = app.detail.get('1.0', 'end')
            self.assertIn('Keyword "secret key" · Line 17 · 1 match(es)', details)
            self.assertNotIn('Keyword #4', details)
            app.table.selection_set('1')
            app.show_details()
            details = app.detail.get('1.0', 'end')
            self.assertIn('Reason: PermissionError: Access is denied.', details)
            self.assertLess(details.index('Access is denied.'), details.index('Keyword Matches:'))
            app.table.selection_set('issue-0')
            app.show_details()
            self.assertIn('Reason: Cannot enumerate directory: private', app.detail.get('1.0', 'end'))
        finally:
            close_app(app)

    def test_status_sequence_including_folder_errors(self):
        app = make_app()
        app.withdraw()
        try:
            report = Report('C:/example', 'test', files=[
                FileResult('z.txt', 'Failed', [Hit(1, 'Line 1', 1)], []),
                FileResult('a.bin', 'Not Scanned', [], ['Unsupported file']),
                FileResult('b.txt', 'OK', [], []),
            ], issues=['Unreadable directory'])
            app.finish('done', report)
            rows = [app.table.item(i, 'values') for i in app.table.get_children()]
            self.assertEqual([r[0] for r in rows], ['Failed', 'Not Scanned', 'Not Scanned', 'OK'])
        finally:
            close_app(app)

    def test_compact_tabs_leave_room_for_results(self):
        app = make_app()
        try:
            app.geometry('1000x660')
            app.update()
            def find_tabs(widget):
                for child in widget.winfo_children():
                    if isinstance(child, ttk.Notebook):
                        return child
                    match = find_tabs(child)
                    if match is not None:
                        return match
            tabs = find_tabs(app)
            for tab in tabs.tabs():
                tabs.select(tab)
                app.update()
                self.assertLess(tabs.winfo_height(), 170)
                self.assertGreater(app.table.winfo_height(), 160)
        finally:
            close_app(app)


class KeywordSettingsTests(unittest.TestCase):
    def test_keywords_survive_close_and_reopen(self):
        base = Path(__file__).resolve().parents[1] / 'tmp' / 'tests'
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as directory:
            path = Path(directory) / 'settings.json'
            app = App(settings_path=path)
            app.withdraw()
            text = '  alpha, beta, project key  '
            app.keywords.insert('1.0', text)
            app.case.set(True)
            app.update()
            for callback in app.tk.call('after', 'info'):
                app.after_cancel(callback)
            app.close()
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')),
                             {'keywords': text, 'case_sensitive': True})
            reopened = App(settings_path=path)
            reopened.withdraw()
            try:
                self.assertEqual(reopened.keywords.get('1.0', 'end-1c'), text)
                self.assertTrue(reopened.case.get())
            finally:
                close_app(reopened)

    def test_invalid_settings_do_not_prevent_startup(self):
        base = Path(__file__).resolve().parents[1] / 'tmp' / 'tests'
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as directory:
            path = Path(directory) / 'settings.json'
            path.write_text('{bad json', encoding='utf-8')
            app = App(settings_path=path)
            app.withdraw()
            try:
                self.assertEqual(app.keywords.get('1.0', 'end-1c'), '')
                self.assertIn('Could Not Be Loaded', app.progress_text.get())
            finally:
                close_app(app)


if __name__ == '__main__':
    unittest.main()
