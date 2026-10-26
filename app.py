"""Two-tab, offline desktop interface for keyword inspection."""
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from scanner import Keywords, scan_folder


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('SafeLoad | Offline Keyword Scanner')
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
        self.running = False
        self.source = tk.StringVar()
        self.case = tk.BooleanVar(value=False)
        self.state_text = tk.StringVar(value='Not Scanned — Choose A Folder And Enter Keywords')
        self.progress_text = tk.StringVar(value='All file processing stays on this computer.')
        self.protocol('WM_DELETE_WINDOW', self.close)
        shell = ttk.Frame(self, padding=16)
        shell.pack(fill='both', expand=True)
        ttk.Label(shell, text='SafeLoad', style='Title.TLabel').pack(anchor='w')
        ttk.Label(shell, text='Scan Folders For Keywords. Review File Status.').pack(anchor='w', pady=(2, 10))
        tabs = ttk.Notebook(shell)
        tabs.pack(fill='x')
        directory = ttk.Frame(tabs, padding=(12, 8))
        keywords = ttk.Frame(tabs, padding=(12, 8))
        tabs.add(directory, text='1  Directory')
        tabs.add(keywords, text='2  Keywords')
        directory.columnconfigure(1, weight=1)
        ttk.Label(directory, text='Source Folder').grid(row=0, column=0, sticky='w', padx=(0, 16))
        self.source_entry = ttk.Entry(directory, textvariable=self.source)
        self.source_entry.grid(row=0, column=1, sticky='ew', pady=5)
        self.browse_source = ttk.Button(directory, text='Browse…', command=self.choose_source)
        self.browse_source.grid(row=0, column=2, padx=(10, 0))
        ttk.Label(directory, text='Subfolders included. Files are only read; matching files appear first.').grid(
            row=1, column=0, columnspan=3, sticky='w', pady=(4, 0))
        keyword_header = ttk.Frame(keywords)
        keyword_header.pack(fill='x')
        ttk.Label(keyword_header, text='Keywords Or Phrases, Separated By Commas').pack(side='left')
        self.case_check = ttk.Checkbutton(keyword_header, text='Case-Sensitive Matching', variable=self.case)
        self.case_check.pack(side='right')
        self.keywords = tk.Text(keywords, height=2, font=('Segoe UI', 11), wrap='word', relief='solid', bd=1)
        self.keywords.pack(fill='x', pady=4)
        ttk.Label(keywords, text='Literal substring matches, including inside longer words. Commas separate entries; no regex.').pack(anchor='w', pady=(4, 0))
        controls = ttk.Frame(shell)
        controls.pack(fill='x', pady=8)
        self.scan_button = ttk.Button(controls, text='Scan Folder', command=self.start)
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
        self.table.bind('<<TreeviewSelect>>', self.show_details)
        self.detail = tk.Text(details, width=34, height=6, wrap='word', state='disabled',
                              font=('Segoe UI', 10), padx=12, pady=10, relief='flat')
        detail_scroll = ttk.Scrollbar(details, command=self.detail.yview)
        self.detail.configure(yscrollcommand=detail_scroll.set)
        detail_scroll.pack(side='right', fill='y')
        self.detail.pack(fill='both', expand=True)
        ttk.Label(shell, wraplength=min(1000, width - 60), text='Safe = no configured keyword matches in inspected text, within documented format limits. '
                  'Not a security certification.\nOK = no keyword match. Failed = keyword match. Not Scanned = unable to check the file.').pack(before=panes, side='bottom', fill='x', pady=(8, 0))
        self.after(100, self.poll)
        for variable in (self.source, self.case):
            variable.trace_add('write', self.invalidate)
        self.keywords.bind('<<Modified>>', self.keywords_modified)

    def invalidate(self, *_):
        if self.running or not self.report:
            return
        self.report = None
        self.state_text.set('Not Scanned — Settings Changed; Scan Again')
        self.status.configure(foreground='#25334a')
        self.export_button.configure(state='disabled')
        self.table.delete(*self.table.get_children())
        self.set_details('Scan again to check the current folder and keywords.')

    def keywords_modified(self, _=None):
        if self.keywords.edit_modified():
            self.invalidate()
            self.keywords.edit_modified(False)

    def choose_source(self):
        path = filedialog.askdirectory(title='Choose Source Folder')
        if path:
            self.source.set(path)

    def start(self):
        if self.running:
            return
        try:
            keys = Keywords(self.keywords.get('1.0', 'end'), self.case.get())
            if not self.source.get().strip():
                raise ValueError('Choose a source folder.')
        except ValueError as exc:
            messagebox.showerror('Cannot Start', str(exc))
            return
        self.report = None
        self.running = True
        self.cancel.clear()
        self.table.delete(*self.table.get_children())
        self.set_details('Select a file to inspect match locations and review reasons.')
        self.state_text.set('Scanning — No Safety Result Yet')
        self.status.configure(foreground='#25334a')
        for control in (self.scan_button, self.export_button, self.source_entry,
                        self.browse_source, self.case_check):
            control.configure(state='disabled')
        self.keywords.configure(state='disabled')
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

    def poll(self):
        latest = None
        while True:
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'progress':
                latest = value
            else:
                latest = None
                self.finish(kind, value)
        if latest:
            self.progress_text.set(f'Inspected {latest[0]} files · {latest[1]}')
        self.after(100, self.poll)

    def finish(self, kind, value):
        self.running = False
        self.bar.stop()
        for control in (self.scan_button, self.source_entry, self.browse_source, self.case_check):
            control.configure(state='normal')
        self.keywords.configure(state='normal')
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
        self.set_details('Scanned folder:\n' + value.source +
                         '\n\nSelect a failed file to see keyword locations or inspection issues. '
                         'Resolve files manually, then scan again.\n\nFiles are never modified. '
                         'Reports may contain sensitive paths. Keyword numbers follow the unique entries in the Keywords tab.')

    def set_details(self, text):
        self.detail.configure(state='normal')
        self.detail.delete('1.0', 'end')
        self.detail.insert('1.0', text)
        self.detail.configure(state='disabled')

    def show_details(self, _=None):
        selection = self.table.selection()
        if not selection or not self.report:
            return
        key = selection[0]
        if key.startswith('issue-'):
            self.set_details(self.report.issues[int(key.split('-')[1])])
            return
        item = self.report.files[int(key)]
        reason = {'OK': 'No Keyword Match Found.', 'Failed': 'Keyword Match Found.',
                  'Not Scanned': 'Unable To Check This File.'}[item.status]
        lines = [item.path, '', 'Status: ' + item.status, 'Reason: ' + reason]
        if item.status == 'Not Scanned':
            lines.extend(['', 'Scan Error:'] + item.issues)
        lines.extend(['', 'Keyword Matches:'])
        lines.extend(f'Keyword #{h.keyword} · {h.location} · {h.count} match(es)' for h in item.found)
        if not item.found:
            lines.append('Not Checked.' if item.status == 'Not Scanned' else 'None In Inspected Text.')
        if item.issues and item.status != 'Not Scanned':
            lines.extend(['', 'Scan Notes:'] + item.issues)
        if item.status == 'Failed':
            lines.extend(['', 'Review this file manually, resolve keyword matches, then scan again.'])
        self.set_details('\n'.join(lines))

    def export(self):
        if not self.report:
            return
        path = filedialog.asksaveasfilename(title='Save Report Outside Scanned Folder',
                                          defaultextension='.json', initialfile='keyword-report.json',
                                          filetypes=[('JSON report', '*.json')])
        if path:
            try:
                target = Path(path).resolve()
                folder = Path(self.report.source)
                if target == folder or folder in target.parents:
                    raise ValueError('Save reports outside the source folder; reports can contain sensitive paths.')
                self.report.save(target)
            except Exception as exc:
                messagebox.showerror('Cannot Save Report', str(exc))

    def close(self):
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
