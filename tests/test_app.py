from pathlib import Path
import tempfile
import time
import unittest

from app import App


class DesktopTests(unittest.TestCase):
    def test_scan_clean_and_invalidate_result(self):
        base = Path(__file__).resolve().parents[1] / 'tmp' / 'tests'
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as folder:
            source = Path(folder) / 'input'
            source.mkdir()
            (source / 'example.txt').write_text('Keep Secret end', encoding='utf-8')
            app = App()
            app.withdraw()
            try:
                app.source.set(str(source))
                app.output.set(str(Path(folder) / 'clean'))
                app.keywords.insert('1.0', 'secret')
                app.update()
                for clean in (False, True):
                    app.start(clean)
                    deadline = time.monotonic() + 10
                    while app.running and time.monotonic() < deadline:
                        app.update()
                        time.sleep(.01)
                    self.assertFalse(app.running)
                    self.assertIsNotNone(app.report)
                    self.assertEqual(app.report.safe, clean)
                    self.assertEqual(len(app.table.get_children()), 1)
                self.assertEqual((Path(folder) / 'clean' / 'example.txt').read_text(), 'Keep  end')
                app.case.set(True)
                self.assertIsNone(app.report)
                self.assertIn('NOT SCANNED', app.state_text.get())
            finally:
                app.destroy()


if __name__ == '__main__':
    unittest.main()
