from pathlib import Path
import tempfile
import time
import unittest
from tkinter import ttk
from app import App


class DesktopTests(unittest.TestCase):
    def test_scan_only_sorting_title_case_and_manual_rescan(self):
        base = Path(__file__).resolve().parents[1] / 'tmp' / 'tests'
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as folder:
            root = Path(folder)
            (root / 'a.txt').write_text('Fine')
            (root / 'z.txt').write_text('Secret')
            app = App()
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
                app.case.set(True)
                self.assertIsNone(app.report)
                self.assertIn('NOT SCANNED', app.state_text.get())
            finally:
                app.destroy()


if __name__ == '__main__':
    unittest.main()
