"""Three-tab, offline desktop interface for keyword inspection and MakeItSafe."""
from pathlib import Path
import os
import json
import tempfile
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from scanner import Keywords, Plan, make_safe_folder, scan_folder


class App(tk.Tk):
    def __init__(self, settings_path=None):
        super().__init__()
        icon = Path(__file__).resolve().parent / 'assets' / 'app.ico'
        if os.name == 'nt' and icon.is_file():
            self.iconbitmap(default=str(icon))
        self.title('SafeLoad | Offline Keyword Scanner')
        self.settings_path = Path(settings_path) if settings_path is not None else (
            Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'SafeLoad' / 'settings.json')
        width = min(1120, self.winfo_screenwidth() - 50)
        height = min(780, self.winfo_screenheight() - 100)
        self.geometry(f'{width}x{height}')
        self.minsize(min(920, width), min(660, height))
        self.configure(bg='#eef2f7')
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('.', font=('Segoe UI', 10))
        style.configure('TFrame', background='#eef2f7')
        style.configure('TLabel', background='#eef2f7', foreground='#25334a')
        style.configure('Title.TLabel', font=('Segoe UI', 23, 'bold'))
        style.configure('Status.TLabel', font=('Segoe UI', 13, 'bold'), padding=(8, 6))
        style.configure('TButton', padding=(12, 7))
        style.configure('TNotebook.Tab', padding=(16, 6))
        style.configure('Treeview', rowheight=29)
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.report = None
        self.safe_report = None
        self.running = False
        self.source = tk.StringVar()
        self.case = tk.BooleanVar(value=False)
        self.safe_case = tk.BooleanVar(value=False)
        self.state_text = tk.StringVar(value='Not Scanned — Choose A Folder Or File And Enter Keywords')
        self.progress_text = tk.StringVar(value='All file processing stays on this computer.')
        self.protocol('WM_DELETE_WINDOW', self.close)
        shell = ttk.Frame(self, padding=16)
        shell.pack(fill='both', expand=True)
        ttk.Label(shell, text='SafeLoad', style='Title.TLabel').pack(anchor='w')
        ttk.Label(shell, text='Scan Folders For Keywords. Review File Status.').pack(anchor='w', pady=(2, 10))
        self.tabs = tabs = ttk.Notebook(shell)
        tabs.pack(fill='x')
        directory = ttk.Frame(tabs, padding=(12, 8))
        keywords = ttk.Frame(tabs, padding=(12, 8))
        tabs.add(directory, text='1  Directory')
        tabs.add(keywords, text='2  Keywords')
        makesafe = ttk.Frame(tabs, padding=(12, 8))
        tabs.add(makesafe, text='3  MakeItSafe')
        directory.columnconfigure(1, weight=1)
        ttk.Label(directory, text='Folder Or File').grid(row=0, column=0, sticky='w', padx=(0, 16))
        self.source_entry = ttk.Entry(directory, textvariable=self.source)
        self.source_entry.grid(row=0, column=1, sticky='ew', pady=5)
        self.browse_source = ttk.Button(directory, text='Browse Folder…', command=self.choose_source)
        self.browse_source.grid(row=0, column=2, padx=(10, 0))
        self.browse_file = ttk.Button(directory, text='Browse File…', command=self.choose_file)
        self.browse_file.grid(row=0, column=3, padx=(8, 0))
        ttk.Label(directory, text='A folder includes its subfolders; choose a single file to process only that file. '
                 'Scanning only reads files; matching files appear first.').grid(
            row=1, column=0, columnspan=4, sticky='w', pady=(4, 0))
        keyword_header = ttk.Frame(keywords)
        keyword_header.pack(fill='x')
        ttk.Label(keyword_header, text='Keywords Or Phrases, Separated By Commas').pack(side='left')
        self.case_check = ttk.Checkbutton(keyword_header, text='Case-Sensitive Matching', variable=self.case)
        self.case_check.pack(side='right')
        self.keywords = tk.Text(keywords, height=2, font=('Segoe UI', 11), wrap='word', relief='solid', bd=1)
        self.keywords.pack(fill='x', pady=4)
        ttk.Label(keywords, text='Literal substring matches, including inside longer words. Commas separate entries; no regex.').pack(anchor='w', pady=(4, 0))
        makesafe.columnconfigure(0, weight=1, uniform='safe')
        makesafe.columnconfigure(1, weight=1, uniform='safe')
        ttk.Label(makesafe, text='Reverse These Keywords (git → tig)').grid(row=0, column=0, sticky='w')
        ttk.Label(makesafe, text='Remove Every Line Containing These Keywords').grid(row=0, column=1, sticky='w', padx=(12, 0))
        self.safe_keywords = tk.Text(makesafe, height=2, font=('Segoe UI', 11), wrap='word', relief='solid', bd=1)
        self.safe_keywords.grid(row=1, column=0, sticky='ew', pady=4)
        self.safe_clean = tk.Text(makesafe, height=2, font=('Segoe UI', 11), wrap='word', relief='solid', bd=1)
        self.safe_clean.grid(row=1, column=1, sticky='ew', pady=4, padx=(12, 0))
        safe_row = ttk.Frame(makesafe)
        safe_row.grid(row=2, column=0, columnspan=2, sticky='ew')
        self.safe_case_check = ttk.Checkbutton(safe_row, text='Case-Sensitive Matching', variable=self.safe_case)
        self.safe_case_check.pack(side='left', padx=(0, 12))
        ttk.Label(safe_row, text='Uses the folder or file from tab 1. The button below becomes MakeItSafe Folder or MakeItSafe File. Lines are removed first, then matches reversed.').pack(side='left')
        controls = ttk.Frame(shell)
        controls.pack(fill='x', pady=8)
        self.scan_button = ttk.Button(controls, text='Scan Folder', command=self.act)
        self.scan_button.pack(side='left')
        self.stop_button = ttk.Button(controls, text='Cancel', command=self.cancel.set, state='disabled')
        self.stop_button.pack(side='left', padx=8)
        self.export_button = ttk.Button(controls, text='Save Report…', command=self.export, state='disabled')
        self.export_button.pack(side='right')
        self.status = ttk.Label(shell, textvariable=self.state_text, style='Status.TLabel')
        self.status.pack(fill='x')
        ttk.Label(shell, textvariable=self.progress_text).pack(anchor='w', pady=(4, 4))
        self.bar = ttk.Progressbar(shell, mode='indeterminate')
        self.bar.pack(fill='x', pady=(0, 8))
        panes = ttk.Panedwindow(shell, orient='horizontal')
        panes.pack(fill='both', expand=True)
        listing = ttk.Frame(panes)
        details = ttk.Frame(panes)
        panes.add(listing, weight=3)
        panes.add(details, weight=2)
        self.table = ttk.Treeview(listing, columns=('status', 'path', 'matches'), height=6, show='headings', selectmode='browse')
        for name, title, column_width in [('status', 'Status', 110), ('path', 'File', 390),
                                   ('matches', 'Matches', 65)]:
            self.table.heading(name, text=title)
            self.table.column(name, width=column_width, minwidth=45, stretch=name == 'path')
        scroll = ttk.Scrollbar(listing, orient='vertical', command=self.table.yview)
        horizontal = ttk.Scrollbar(listing, orient='horizontal', command=self.table.xview)
        self.table.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        self.table.grid(row=0, column=0, sticky='nsew')
        scroll.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        listing.rowconfigure(0, weight=1)
        listing.columnconfigure(0, weight=1)
        self.table.tag_configure('Failed', foreground='#a61b29', background='#fff0f0')
        self.table.tag_configure('OK', foreground='#11633a')
        self.table.tag_configure('Not Scanned', foreground='#8b4300', background='#fff4de')
        self.table.tag_configure('Processed', foreground='#11633a')
        self.table.tag_configure('Partly Processed', foreground='#8b4300', background='#fff4de')
        self.table.tag_configure('Skipped', foreground='#8b4300', background='#fff4de')
        self.table.tag_configure('Error', foreground='#a61b29', background='#fff0f0')
        self.table.bind('<<TreeviewSelect>>', self.show_details)
        self.detail = tk.Text(details, width=34, height=6, wrap='word', state='disabled',
                              font=('Segoe UI', 10), padx=12, pady=10, relief='flat')
        detail_scroll = ttk.Scrollbar(details, command=self.detail.yview)
        self.detail.configure(yscrollcommand=detail_scroll.set)
        detail_scroll.pack(side='right', fill='y')
        self.detail.pack(fill='both', expand=True)
        ttk.Label(shell, wraplength=min(1000, width - 60), text='Safe = no configured keyword matches in inspected text, within documented format limits. '
                  'Not a security certification.\nOK = no keyword match. Failed = keyword match. Not Scanned = unable to check the file.').pack(before=panes, side='bottom', fill='x', pady=(8, 0))
        self.load_settings()
        self.after(100, self.poll)
        for variable in (self.source, self.case):
            variable.trace_add('write', self.invalidate)
        self.source.trace_add('write', self.tab_changed)
        self.keywords.bind('<<Modified>>', self.keywords_modified)
        tabs.bind('<<NotebookTabChanged>>', self.tab_changed)

    def load_settings(self):
        try:
            settings = json.loads(self.settings_path.read_text(encoding='utf-8'))
            if not isinstance(settings, dict) or not isinstance(settings.get('keywords'), str):
                raise ValueError('Invalid saved settings.')
            self.keywords.insert('1.0', settings['keywords'])
            self.case.set(settings.get('case_sensitive') is True)
            if isinstance(settings.get('safe_keywords'), str):
                self.safe_keywords.insert('1.0', settings['safe_keywords'])
            if isinstance(settings.get('safe_clean_keywords'), str):
                self.safe_clean.insert('1.0', settings['safe_clean_keywords'])
            self.safe_case.set(settings.get('safe_case_sensitive') is True)
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            self.progress_text.set('Saved Keywords Could Not Be Loaded. Enter Them Again.')

    def save_settings(self):
        temporary = None
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            settings = {'keywords': self.keywords.get('1.0', 'end-1c'),
                        'case_sensitive': self.case.get(),
                        'safe_keywords': self.safe_keywords.get('1.0', 'end-1c'),
                        'safe_clean_keywords': self.safe_clean.get('1.0', 'end-1c'),
                        'safe_case_sensitive': self.safe_case.get()}
            handle, temporary = tempfile.mkstemp(prefix='.settings-', dir=self.settings_path.parent)
            with os.fdopen(handle, 'w', encoding='utf-8') as stream:
                json.dump(settings, stream, ensure_ascii=False, indent=2)
            os.replace(temporary, self.settings_path)
            return True
        except OSError as exc:
            messagebox.showerror('Cannot Save Keywords',
                                 f'Keywords could not be saved locally: {exc}')
            return False
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    def invalidate(self, *_):
        if self.running or not self.report:
            return
        self.report = None
        self.state_text.set('Not Scanned — Settings Changed; Scan Again')
        self.status.configure(foreground='#25334a')
        self.export_button.configure(state='disabled')
        self.table.delete(*self.table.get_children())
        self.set_details('Scan again to check the current folder or file and keywords.')

    def keywords_modified(self, _=None):
        if self.keywords.edit_modified():
            self.invalidate()
            self.keywords.edit_modified(False)

    def choose_source(self):
        path = filedialog.askdirectory(title='Choose Source Folder')
        if path:
            self.source.set(path)

    def choose_file(self):
        path = filedialog.askopenfilename(title='Choose Source File')
        if path:
            self.source.set(path)

    def start(self):
        if self.running:
            return
        try:
            keys = Keywords(self.keywords.get('1.0', 'end'), self.case.get())
            if not self.source.get().strip():
                raise ValueError('Choose a source folder or file.')
        except ValueError as exc:
            messagebox.showerror('Cannot Start', str(exc))
            return
        if not self.save_settings():
            return
        self.report = None
        self.safe_report = None
        self.table.heading('matches', text='Matches')
        self.running = True
        self.cancel.clear()
        self.table.delete(*self.table.get_children())
        self.set_details('Select a file to inspect match locations and review reasons.')
        self.state_text.set('Scanning — No Safety Result Yet')
        self.status.configure(foreground='#25334a')
        self.lock(True)
        self.export_button.configure(state='disabled')
        self.stop_button.configure(state='normal')
        self.bar.start(12)
        source = self.source.get()

        def work():
            try:
                report = scan_folder(source, keys, self.cancel,
                                     lambda n, p: self.events.put(('progress', (n, p))))
                self.events.put(('done', report))
            except Exception as exc:
                self.events.put(('error', f'{type(exc).__name__}: {exc}'))
        threading.Thread(target=work, daemon=True).start()

    def lock(self, busy):
        state = 'disabled' if busy else 'normal'
        for control in (self.scan_button, self.source_entry, self.browse_source, self.browse_file,
                        self.case_check, self.safe_case_check, self.keywords, self.safe_keywords, self.safe_clean):
            control.configure(state=state)
        self.tab_changed()

    def making_safe(self):
        return self.tabs.index(self.tabs.select()) == 2

    def tab_changed(self, *_):
        """One action button: Scan Folder on tabs 1 and 2, MakeItSafe Folder on tab 3."""
        if not self.running:
            path = self.source.get().strip()
            noun = 'File' if path and Path(path).is_file() else 'Folder'
            self.scan_button.configure(text=('MakeItSafe ' if self.making_safe() else 'Scan ') + noun)

    def act(self):
        self.start_safe() if self.making_safe() else self.start()

    def start_safe(self):
        if self.running:
            return
        try:
            plan = Plan(self.safe_keywords.get('1.0', 'end'), self.safe_clean.get('1.0', 'end'),
                        self.safe_case.get())
            if not self.source.get().strip():
                raise ValueError('Choose a source folder or file in tab 1.')
        except ValueError as exc:
            messagebox.showerror('Cannot Start', str(exc))
            return
        lines = []
        if plan.reverse_words:
            lines.append('Written backwards: ' + ', '.join(f'{w} → {w[::-1]}' for w in plan.reverse_words[:5])
                         + (' …' if len(plan.reverse_words) > 5 else ''))
        if plan.clean_words:
            lines.append('Lines removed that contain: ' + ', '.join(plan.clean_words[:5])
                         + (' …' if len(plan.clean_words) > 5 else ''))
        if not messagebox.askyesno('Confirm MakeItSafe',
                                   f'This rewrites matching files in place, with no backup:\n{self.source.get()}\n\n'
                                   + '\n'.join(lines) + '\n\nContinue?', icon='warning'):
            return
        if not self.save_settings():
            return
        self.report = None
        self.safe_report = None
        self.table.heading('matches', text='Changes')
        self.running = True
        self.cancel.clear()
        self.table.delete(*self.table.get_children())
        self.set_details('Select a file to see what was replaced.')
        self.state_text.set('Processing — Files Are Being Changed')
        self.status.configure(foreground='#25334a')
        self.lock(True)
        self.export_button.configure(state='disabled')
        self.stop_button.configure(state='normal')
        self.bar.start(12)
        source = self.source.get()

        def work():
            try:
                report = make_safe_folder(source, plan, self.cancel,
                                          lambda n, p: self.events.put(('progress', (n, p))))
                self.events.put(('safe_done', report))
            except Exception as exc:
                self.events.put(('error', f'{type(exc).__name__}: {exc}'))
        threading.Thread(target=work, daemon=True).start()

    def finish_safe(self, report):
        self.running = False
        self.bar.stop()
        self.lock(False)
        self.stop_button.configure(state='disabled')
        self.safe_report = report
        self.export_button.configure(state='normal')
        count = {}
        for item in report.files:
            count[item.status] = count.get(item.status, 0) + 1
        changed = count.get('Processed', 0) + count.get('Partly Processed', 0)
        clean = not report.cancelled and not report.issues and not (
            count.get('Partly Processed') or count.get('Error'))
        if report.cancelled:
            state = f'MakeItSafe Cancelled — {changed} Files Were Already Changed'
        elif clean:
            state = f'MakeItSafe Complete — {changed} Files Processed'
        else:
            state = f'MakeItSafe Finished — {changed} Files Processed, Review Remaining Items'
        self.state_text.set(state)
        self.status.configure(foreground='#11633a' if clean else '#a61b29')
        self.progress_text.set(f'{len(report.files)} files · ' + ' · '.join(
            f'{count.get(s, 0)} {s}' for s in ('Processed', 'Partly Processed', 'Skipped', 'Unchanged', 'Error')))
        for i, item in enumerate(report.files):
            self.table.insert('', 'end', iid=str(i), values=(item.status, item.path,
                              sum(item.replaced.values()) + sum(item.removed.values())), tags=(item.status,))
        for i, issue in enumerate(report.issues):
            self.table.insert('', 0, iid=f'issue-{i}', values=('Error', issue, ''), tags=('Error',))
        lines = ['MakeItSafe target:', report.source]
        if report.reverse_words:
            lines.extend(['', 'Reversed:'])
            for word, total in report.totals.items():
                files = sum(word in item.replaced for item in report.files)
                lines.append(f'"{word}" has been replaced with "{word[::-1]}": {total} time(s) in {files} file(s)')
        if report.clean_words:
            lines.extend(['', 'Removed:'])
            for word, total in report.removed_totals.items():
                files = sum(word in item.removed for item in report.files)
                lines.append(f'Lines containing "{word}" have been removed: {total} line(s) in {files} file(s)')
        lines.extend(['', 'Select a file to see what changed. Run a scan with all of the words '
                      'to confirm nothing remains.'])
        self.set_details('\n'.join(lines))

    def poll(self):
        latest = None
        while True:
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'progress':
                latest = value
            elif kind == 'safe_done':
                latest = None
                self.finish_safe(value)
            else:
                latest = None
                self.finish(kind, value)
        if latest:
            self.progress_text.set(f'Inspected {latest[0]} files · {latest[1]}')
        self.after(100, self.poll)

    def finish(self, kind, value):
        self.running = False
        self.bar.stop()
        self.lock(False)
        self.stop_button.configure(state='disabled')
        if kind == 'error':
            self.state_text.set('Scan Incomplete — Unable To Finish')
            self.status.configure(foreground='#a61b29')
            self.progress_text.set(value)
            messagebox.showerror('Scan Failed', value)
            return
        self.report = value
        self.export_button.configure(state='normal')
        failed = sum(f.status == 'Failed' for f in value.files)
        unscanned = sum(f.status == 'Not Scanned' for f in value.files)
        state = 'Unsafe — Keyword Matches Found' if failed else 'Safe — No Keyword Matches Found'
        if not failed and not value.safe:
            state = 'Scan Incomplete — Some Files Could Not Be Checked'
        if value.cancelled:
            state = 'Scan Incomplete — Cancelled'
        self.state_text.set(state)
        self.status.configure(foreground='#11633a' if value.safe else '#a61b29')
        self.progress_text.set(f'{len(value.files)} files · {failed} Failed · '
                               f'{unscanned} Not Scanned · {len(value.files) - failed - unscanned} OK')
        for i, item in enumerate(value.files):
            self.table.insert('', 'end', iid=str(i), values=(item.status, item.path,
                              sum(h.count for h in item.found)), tags=(item.status,))
        # Keep folder-level inspection failures above the OK files, after matches.
        position = failed + unscanned
        for i, issue in enumerate(value.issues):
            self.table.insert('', position, iid=f'issue-{i}', values=('Not Scanned', issue, ''), tags=('Not Scanned',))
            position += 1
        self.set_details('Scanned:\n' + value.source +
                         '\n\nSelect a failed file to see keyword locations or inspection issues. '
                         'Resolve files manually, then scan again.\n\nScanning never modifies files. '
                         'Reports include matched keyword names and may contain sensitive terms and paths.')

    def set_details(self, text):
        self.detail.configure(state='normal')
        self.detail.delete('1.0', 'end')
        self.detail.insert('1.0', text)
        self.detail.configure(state='disabled')

    def show_details(self, _=None):
        selection = self.table.selection()
        if self.safe_report and selection:
            self.show_safe_details(selection[0])
            return
        if not selection or not self.report:
            return
        key = selection[0]
        if key.startswith('issue-'):
            self.set_details('Status: Not Scanned\nReason: ' + self.report.issues[int(key.split('-')[1])])
            return
        item = self.report.files[int(key)]
        reason = {'OK': 'No Keyword Match Found.', 'Failed': 'Keyword Match Found.',
                  'Not Scanned': item.issues[0] if item.issues else 'No Scan Result Was Available.'}[item.status]
        lines = [item.path, '', 'Status: ' + item.status, 'Reason: ' + reason]
        if item.status == 'Not Scanned' and len(item.issues) > 1:
            lines.extend(['', 'Additional Scan Details:'] + item.issues[1:])
        lines.extend(['', 'Keyword Matches:'])
        for hit in item.found:
            keyword = json.dumps(hit.keyword_text, ensure_ascii=False) if hit.keyword_text else f'#{hit.keyword}'
            lines.append(f'Keyword {keyword} · {hit.location} · {hit.count} match(es)')
        if not item.found:
            lines.append('Not Checked.' if item.status == 'Not Scanned' else 'None In Inspected Text.')
        if item.issues and item.status != 'Not Scanned':
            lines.extend(['', 'Scan Notes:'] + item.issues)
        if item.status == 'Failed':
            lines.extend(['', 'Review this file manually, resolve keyword matches, then scan again.'])
        self.set_details('\n'.join(lines))

    def show_safe_details(self, key):
        if key.startswith('issue-'):
            self.set_details('Status: Error\nReason: ' + self.safe_report.issues[int(key.split('-')[1])])
            return
        item = self.safe_report.files[int(key)]
        lines = [item.path, '', 'Status: ' + item.status]
        if item.replaced:
            lines.extend(['', 'Reversed:'])
            lines.extend(f'"{w}" has been replaced with "{w[::-1]}": {n} time(s)' for w, n in item.replaced.items())
        if item.removed:
            lines.extend(['', 'Removed:'])
            lines.extend(f'Lines containing "{w}" have been removed: {n} line(s)' for w, n in item.removed.items())
        if not item.replaced and not item.removed:
            lines.extend(['', 'Nothing changed.'])
        if item.issues:
            lines.extend(['', 'Notes:'] + item.issues)
        self.set_details('\n'.join(lines))

    def export(self):
        current = self.safe_report or self.report
        if not current:
            return
        path = filedialog.asksaveasfilename(title='Save Report Outside Scanned Folder',
                                          defaultextension='.json', initialfile='makeitsafe-report.json' if self.safe_report else 'keyword-report.json',
                                          filetypes=[('JSON report', '*.json')])
        if path:
            try:
                target = Path(path).resolve()
                folder = Path(current.source)
                if target == folder or folder in target.parents:
                    raise ValueError('Save reports outside the scanned folder; reports can contain sensitive paths.')
                current.save(target)
            except Exception as exc:
                messagebox.showerror('Cannot Save Report', str(exc))

    def close(self):
        if not self.save_settings():
            return
        if self.running:
            self.cancel.set()
            self.state_text.set('Cancelling — Waiting For Current File; Folder Is Not Verified')
            self.after(150, self.wait_close)
        else:
            self.destroy()

    def wait_close(self):
        if self.running:
            self.after(150, self.wait_close)
        else:
            self.destroy()


if __name__ == '__main__':
    App().mainloop()
