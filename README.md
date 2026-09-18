# PDF Toolkit

A modern desktop application for merging and splitting PDFs and converting
Word documents, on Windows. Built with Python and CustomTkinter, it looks and
behaves like commercial desktop software: dark theme, native file dialogs,
drag-and-drop, progress reporting, cancellable jobs and friendly error
handling.

Everything runs locally — no file ever leaves the machine.

---

## Features

### Merge PDFs
* Two input methods: **Select folder** (finds every PDF inside, ignoring
  everything else) and **Select files** (native multi-select picker limited to
  `*.pdf`).
* Drag PDFs — or whole folders — from Explorer straight onto the window.
* A review list showing, for each file, the **first-page thumbnail**, name,
  **page count**, **file size** and **last modified date**.
* Re-order by **dragging rows**, or with **Move up / Move down**, **Sort A–Z**,
  **Reverse** and **Reset order**.
* The review list is the single source of truth: the order on screen is the
  order written to the merged file, never the order the OS supplied.
* Add more files, remove one, or clear the list at any time. Duplicates are
  detected and skipped.
* *Save As* destination, live progress with the current file name, and a
  completion dialog with **Open output folder**.

### Split PDF
* **Split every page** — one PDF per page (`Page_001.pdf`, `Page_002.pdf`, …).
* **Split by page ranges** — `1-5, 6-10, 11-20` produces one file per range.
* **Extract specific pages** — `1, 4, 7, 10` produces a single new document.
* Live validation of the page expression as you type, with a preview of how
  many files will be produced.
* First-page preview and document details, output folder picker, and a switch
  choosing between replacing existing files or auto-numbering them.

### Convert Word files
* Input as **one document**, **many documents**, or a **whole folder**
  (optionally including sub-folders) — by picker or by drag-and-drop.
* Output to **PDF** or **Markdown**.
* Two output modes: **one file per document**, or **collate everything into a
  single file** in the order shown in the review list.
* Batch conversions can **recreate the source sub-folder structure** in the
  output folder, so a nested archive converts in place.
* Word lock files (`~$name.docx`) and non-Word files are ignored automatically;
  a document that cannot be read is reported and the rest of the batch
  continues.
* Structure is preserved: headings, bold/italic, bullet and numbered lists,
  quotes and tables all survive the conversion.
* **PDF engine** is chosen automatically — Microsoft Word (via `pywin32`) or
  LibreOffice when installed, otherwise the built-in renderer, which needs no
  other software. You can force a specific engine from the drop-down.

### Throughout
* Dark theme, responsive layout, consistent spacing, large readable controls.
* Status bar, progress bars, cancellable long-running jobs.
* Confirmation dialogs before anything destructive.
* Keyboard shortcuts (press **F1** in the app for the full list).
* Rotating log file at `%USERPROFILE%\.pdf_toolkit\pdf_toolkit.log`.

---

## Requirements

* Windows 10 or 11 (the code is cross-platform, the packaging notes are Windows)
* Python 3.13 or newer

## Installation

```bash
git clone <your-repository-url> pdf-toolkit
cd pdf-toolkit
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
```

## Running

```bash
python app.py
```

---

## Project structure

```
pdf-toolkit/
├── app.py                  Application entry point, window and view routing
│
├── core/                   All document logic - imports no UI code
│   ├── constants.py        Configurable constants (paths, sizes, templates)
│   ├── docx_converter.py   DocxConvertTool, OutputFormat, PdfEngine
│   ├── docx_reader.py      Word -> format-neutral block model
│   ├── exceptions.py       User-facing exception hierarchy
│   ├── logging_config.py   Rotating file + console logging
│   ├── markdown_writer.py  Block model -> Markdown
│   ├── merger.py           PdfMergeTool
│   ├── models.py           FileInfo, PdfFileInfo, DocxFileInfo, OperationResult
│   ├── pdf_renderer.py     Block model -> PDF (built-in Word->PDF engine)
│   ├── progress.py         Progress reporting and cancellation tokens
│   ├── splitter.py         PdfSplitTool, SplitMode
│   ├── thumbnails.py       First-page rendering with graceful fallback
│   ├── utils.py            Sizes, dates, natural sort, folder scanning
│   └── validator.py        PDF/Word inspection and page-range parsing
│
├── ui/                     All presentation - imports no pypdf
│   ├── base_view.py        BaseView + AppController protocol
│   ├── convert_view.py     Word conversion workflow
│   ├── dialogs.py          Themed modal dialogs, native file pickers
│   ├── dnd.py              Optional Explorer drag-and-drop
│   ├── file_list.py        Re-orderable review list (drag + buttons)
│   ├── home.py             Launcher screen and the tool registry
│   ├── merge_view.py       Merge workflow
│   ├── split_view.py       Split workflow
│   ├── theme.py            Colours, fonts, spacing, button styles
│   ├── widgets.py          Header, cards, progress panel, status bar
│   └── workers.py          Threaded jobs marshalled back to the UI thread
│
├── assets/                 logo.png, app.ico, icons/ (+ make_assets.py)
├── tests/                  pytest suite for the core layer
├── requirements.txt
├── pdf_toolkit.spec        PyInstaller build specification
└── README.md
```

**Architectural rule:** the UI layer contains no document processing logic, and
the core layer imports no Tkinter. Core tools are therefore usable from scripts and
are covered by unit tests that need no display.

---

## Keyboard shortcuts

| Shortcut          | Action                                     |
|-------------------|--------------------------------------------|
| `Ctrl + O`        | Select files (or the PDF to split)         |
| `Ctrl + D`        | Select a folder                            |
| `Alt + ↑` / `↓`   | Move the selected file up / down           |
| `Delete`          | Remove the selected file                   |
| `Ctrl + Enter`    | Run the current tool                       |
| `Esc`             | Back to the home screen                    |
| `F1`              | Shortcut help                              |
| `Ctrl + Q`        | Exit                                       |

---

## Tests

```bash
python -m pip install pytest
python -m pytest -q
```

The suite covers page-range parsing, corrupt/missing/empty file handling,
merge ordering, overwrite behaviour, all three split modes, Word reading,
Markdown and PDF output, folder mirroring, collation order and cancellation.

---

## Packaging a standalone Windows executable

The build produces a single `.exe` that runs without Python installed.

```bash
python -m pip install pyinstaller
pyinstaller pdf_toolkit.spec --noconfirm --clean
```

The executable is written to `dist\PDF Toolkit.exe`.

Notes:

* The provided `pdf_toolkit.spec` already bundles the CustomTkinter theme
  files, the `tkdnd` Tcl library used by `tkinterdnd2`, the python-docx and
  reportlab data files, and the `assets` folder, and sets `console=False` so
  no terminal window appears.
* A one-line equivalent, if you prefer not to use the spec file:

  ```bash
  pyinstaller app.py --name "PDF Toolkit" --onefile --windowed --icon assets/app.ico --add-data "assets;assets" --collect-data customtkinter --collect-data tkinterdnd2 --collect-data docx --collect-data reportlab --hidden-import PIL._tkinter_finder
  ```

* First launch of a `--onefile` build is slower because the bundle unpacks to
  a temporary folder. Use `--onedir` if start-up time matters more than having
  a single file.
* Some antivirus products flag freshly built, unsigned PyInstaller binaries.
  Sign the executable for distribution.

Regenerate the branding assets at any time with:

```bash
python assets/make_assets.py
```

---

## Extending the toolkit

The application is structured so that new tools slot in without touching the
existing ones. To add, say, a *Rotate pages* tool:

1. **Core** — create `core/rotator.py` with a `PdfRotateTool` class exposing a
   `display_name` and a method that accepts a `ProgressReporter` and a
   `CancellationToken` and returns an `OperationResult`. Raise the existing
   exception types for anything the user needs to know about.
2. **UI** — create `ui/rotate_view.py` with a `BaseView` subclass
   (`view_name = "rotate"`), using `JobRunner` to run the tool off the UI
   thread and `ProgressPanel` to show progress.
3. **Register** — add the view class to `_create_views` in `app.py` and add a
   `ToolEntry` to `HomeView.tool_entries` in `ui/home.py`.

The same pattern covers the roadmap: compress, OCR, rotate, delete pages,
rearrange pages, watermark, add/remove password, images → PDF, PDF → images,
metadata editor, recursive folder combining and batch processing.

---

## Troubleshooting

| Symptom | Cause and fix |
|---------|---------------|
| Dragging files from Explorer does nothing | `tkinterdnd2` is missing or its Tcl library failed to load. Re-install with `pip install --force-reinstall tkinterdnd2`. Buttons and shortcuts still work. |
| Converted PDFs do not match Word's layout exactly | The built-in renderer reproduces structure, not Word's exact pagination. Install Microsoft Word (`pip install pywin32`) or LibreOffice and the converter will use it automatically. |
| A `.doc` file cannot be added | Only the modern `.docx` format is supported. Open it in Word and save as `.docx`. |
| Thumbnails show a generic page glyph | No rasteriser is installed. `pip install pypdfium2`. |
| "This file is password protected" | Encrypted PDFs are refused. Remove the password first. |
| Nothing happens after choosing a folder | The folder contains no PDFs; sub-folders are not scanned. |
| Diagnosing anything else | See `%USERPROFILE%\.pdf_toolkit\pdf_toolkit.log`. |

---
