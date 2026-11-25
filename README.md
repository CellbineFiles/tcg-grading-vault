# TCG Grading Vault (`v0.73`)

A **100% local, privacy-first Pokémon & TCG grading tracker**. All submissions, card details, financial records, eBay bookkeeping, and uploaded slab scans stay strictly on your own computer in `./data/tcg_tracker.db` and `./data/uploads/`. No cloud accounts or external databases are used.

---

## Key Features
- **Home Portfolio Dashboard**: Track Completed, In Grading, Not Shipped / Preload cards, Gem Rate (`10s %`), Collective Costs, **Total Card Values** (graded market value or acquisition cost), **Realized & Unrealized Profit**, Overall Portfolio ROI, and your Top Profitable Slab.
- **Unified Smart Import**: One-click import from **PSA Order CSVs**, **PSA Submission Form PDFs** (auto-deduplicates Copy #1 vs. Copy #2 and splits grading & return shipping fees evenly), **Fanatics Collect CSVs**, **GameStop / BGS PDFs & text**, Excel/Word cert lists, and screenshots (OCR).
- **Fast Cert Scan & Value Pulling**: 12x parallel PSA CloudFront order zip scan extraction, official cert scan fetching for **PSA, CGC, Beckett (BGS), and TAG**, and PriceCharting graded market value lookups.
- **eBay Sales & Zero-PII Bookkeeping**: Import eBay Paid & Shipped CSVs with buyer PII stripped automatically, strict grade/year/card # matching, multi-match submission pickers, profit previews, hover zoom, and a one-click **Import Without Marking Sold** option.
- **Collectr Export & Themed PNG Infographics**: Export any submission to a Collectr / TCGplayer-compatible `.csv` or `.txt`, and generate themed shareable PNG infographics for your overall vault, a submission, or any card.

---

## Quick Installation

### Windows (1-Click Setup Wizard)
1. Install **Python 3.10+** from [python.org/downloads](https://www.python.org/downloads/) (check **"Add python.exe to PATH"** during setup).
2. Download and extract this folder anywhere on your computer (for example `Documents\TCG Grading Vault`).
3. Double-click **`setup_windows.bat`** — it launches the Setup Wizard, installs all packages and Playwright Chromium, and creates a **TCG Grading Vault** shortcut on your Desktop.

### macOS / Linux / Manual Setup
See **`INSTALL.txt`** for step-by-step terminal instructions (or paste `INSTALL.txt` into Gemini for an interactive walkthrough).

---

## Moving Your Folder
You can move the `tcg_grader_tracker` folder anywhere on your computer after stopping the app (`Ctrl+C`). Your `data/` folder moves with it automatically. If `.venv` breaks after moving on Windows, delete `.venv` (keep `data/`!) and double-click `setup_windows.bat` or `start.bat`.

---

## Contact & Feedback
- **Current Version**: `v0.73` (Pre-release; `v1.0` planned for broader public release)
- **Email**: `Cellbinefiles@gmail.com`
- **Discord**: `@dots0576`
