import os
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

SOURCE_DIR = Path(__file__).resolve().parent
_VER_FILE = SOURCE_DIR / "version.txt"
APP_VERSION = _VER_FILE.read_text("utf-8").strip() if _VER_FILE.exists() else "0.93.2"
ICON_NAME = "static/vault.ico"


def detect_existing_install() -> tuple[Path | None, str]:
    """Checks Windows Registry first, then default install folder, for an existing install."""
    if os.name == "nt":
        try:
            import winreg
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\TCGGradingVault"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as k:
                loc, _ = winreg.QueryValueEx(k, "InstallLocation")
                ver, _ = winreg.QueryValueEx(k, "DisplayVersion")
                p = Path(loc)
                if p.exists():
                    return p, str(ver)
        except Exception:
            pass
    local_app = os.environ.get("LOCALAPPDATA")
    if local_app:
        cand = Path(local_app) / "TCG Grading Vault"
        if (cand / "app.py").exists():
            ver_file = cand / "version.txt"
            ver = ver_file.read_text("utf-8").strip() if ver_file.exists() else "0.70 or earlier"
            return cand, ver
    return None, ""


def default_install_dir() -> Path:
    existing_dir, _ = detect_existing_install()
    if existing_dir:
        return existing_dir
    if os.name == "nt":
        local_app = os.environ.get("LOCALAPPDATA")
        if local_app:
            return Path(local_app) / "TCG Grading Vault"
        return Path.home() / "Documents" / "TCG Grading Vault"
    return SOURCE_DIR


def is_playwright_chromium_installed() -> bool:
    """Checks Playwright's shared browser cache for an existing Chromium binary."""
    cache_dirs = []
    if os.name == "nt":
        local_app = os.environ.get("LOCALAPPDATA")
        if local_app:
            cache_dirs.append(Path(local_app) / "ms-playwright")
    else:
        cache_dirs.append(Path.home() / ".cache" / "ms-playwright")
        cache_dirs.append(Path.home() / "Library" / "Caches" / "ms-playwright")
    for d in cache_dirs:
        if d.exists():
            for sub in d.iterdir():
                if sub.is_dir() and "chromium" in sub.name.lower():
                    return True
    return False


def check_system_requirements(target_dir: Path) -> tuple[list[tuple[str, bool, str]], bool]:
    checks: list[tuple[str, bool, str]] = []
    py_ver = sys.version_info
    py_ok = (py_ver.major, py_ver.minor) >= (3, 10)
    checks.append((
        f"Python {py_ver.major}.{py_ver.minor}.{py_ver.micro}",
        py_ok,
        "OK (3.10+ required)" if py_ok else "Python 3.10+ recommended",
    ))

    try:
        import venv  # noqa: F401
        checks.append(("Python venv & pip", True, "Available"))
    except Exception:
        checks.append(("Python venv & pip", False, "Missing venv module"))

    has_app_browser = False
    if os.name == "nt":
        for p in (
            Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft/Edge/Application/msedge.exe",
            Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft/Edge/Application/msedge.exe",
            Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
        ):
            if p.exists():
                has_app_browser = True
                break
    else:
        has_app_browser = any(shutil.which(n) for n in ("microsoft-edge", "google-chrome", "chromium"))
    checks.append((
        "Desktop App Window (Edge/Chrome)",
        has_app_browser,
        "Detected (App Mode ready)" if has_app_browser else "Will use default browser",
    ))

    pw_installed = is_playwright_chromium_installed()
    checks.append((
        "Playwright Chromium",
        pw_installed,
        "Already installed on PC" if pw_installed else "Not found (will install if checked)",
    ))

    venv_py = target_dir / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    venv_ok = venv_py.exists()
    checks.append((
        "Virtual Environment (.venv)",
        venv_ok,
        "Already installed" if venv_ok else "Will be created during setup",
    ))

    tess_ok = bool(shutil.which("tesseract"))
    if not tess_ok and os.name == "nt":
        tess_ok = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe").exists()
    checks.append((
        "Tesseract OCR (optional)",
        tess_ok,
        "Installed" if tess_ok else "Optional (not required)",
    ))
    return checks, pw_installed


def create_windows_shortcuts(install_dir: Path, create_desktop: bool = True) -> list[str]:
    """Creates Start Menu shortcut (always) and Desktop shortcut (using SpecialFolders so OneDrive Desktop works)."""
    if os.name != "nt":
        return []
    created: list[str] = []
    pythonw_venv = install_dir / ".venv" / "Scripts" / "pythonw.exe"
    target_exe = str(pythonw_venv if pythonw_venv.exists() else (install_dir / "start.bat"))
    args = f'"{install_dir / "run.py"}"' if pythonw_venv.exists() else ""
    icon_path = str(install_dir / ICON_NAME)

    folders_expr = '@($ws.SpecialFolders("Programs")'
    if create_desktop:
        folders_expr += ', $ws.SpecialFolders("Desktop")'
    folders_expr += ')'

    ps_script = (
        f'$ws = New-Object -ComObject WScript.Shell; '
        f'foreach ($dir in {folders_expr}) {{ '
        f'  if ($dir -and (Test-Path $dir)) {{ '
        f'    $lnk = Join-Path $dir "TCG Grading Vault.lnk"; '
        f'    $sc = $ws.CreateShortcut($lnk); '
        f'    $sc.TargetPath = "{target_exe}"; '
        f'    $sc.Arguments = \'{args}\'; '
        f'    $sc.WorkingDirectory = "{install_dir}"; '
        f'    $sc.IconLocation = "{icon_path}"; '
        f'    $sc.Description = "Launch TCG Grading Vault (v{APP_VERSION})"; '
        f'    $sc.Save(); '
        f'    Write-Output $lnk '
        f'  }} '
        f'}}'
    )
    res = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
        check=True,
        capture_output=True,
        text=True,
        creationflags=0x08000000,
    )
    for ln in (res.stdout or "").splitlines():
        if ln.strip():
            created.append(ln.strip())
    return created


def register_windows_uninstaller(install_dir: Path) -> tuple[bool, str]:
    if os.name != "nt":
        return False, "Windows only"
    try:
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\TCGGradingVault"
        pythonw_exe = Path(sys.executable).with_name("pythonw.exe")
        py_cmd = str(pythonw_exe if pythonw_exe.exists() else Path(sys.executable))
        uninst_script = str(install_dir / "uninstall_wizard.py")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as k:
            winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, "TCG Grading Vault")
            winreg.SetValueEx(k, "DisplayVersion", 0, winreg.REG_SZ, APP_VERSION)
            winreg.SetValueEx(k, "Publisher", 0, winreg.REG_SZ, "TCG Grading Vault (Cellbinefiles@gmail.com)")
            winreg.SetValueEx(k, "DisplayIcon", 0, winreg.REG_SZ, str(install_dir / ICON_NAME))
            winreg.SetValueEx(k, "InstallLocation", 0, winreg.REG_SZ, str(install_dir))
            winreg.SetValueEx(k, "UninstallString", 0, winreg.REG_SZ, f'"{py_cmd}" "{uninst_script}"')
            winreg.SetValueEx(k, "NoModify", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(k, "NoRepair", 0, winreg.REG_DWORD, 1)
        return True, "Registered in Windows Installed Apps"
    except Exception as exc:
        return False, str(exc)


class InstallerWizard:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"TCG Grading Vault (v{APP_VERSION}) — Setup Wizard")
        self.root.geometry("670x650")
        self.root.minsize(630, 590)
        self.root.configure(bg="#0f172a")
        icon_src = SOURCE_DIR / ICON_NAME
        if icon_src.exists() and os.name == "nt":
            try:
                self.root.iconbitmap(str(icon_src))
            except Exception:
                pass

        init_dir = default_install_dir()
        self.install_dir_var = tk.StringVar(value=str(init_dir))
        self.create_shortcut_var = tk.BooleanVar(value=True)
        self.install_pw_var = tk.BooleanVar(value=not is_playwright_chromium_installed())
        self.running = False

        # Header
        hdr = tk.Frame(root, bg="#131c31", padx=20, pady=12)
        hdr.pack(side="top", fill="x")
        tk.Label(
            hdr,
            text=f"TCG Grading Vault — Setup Wizard (v{APP_VERSION})",
            font=("Segoe UI", 14, "bold"),
            fg="#f8fafc",
            bg="#131c31",
        ).pack(anchor="w")
        tk.Label(
            hdr,
            text="100% Local Vault, Cert Scanner, Market Values & eBay Bookkeeping",
            font=("Segoe UI", 9),
            fg="#94a3b8",
            bg="#131c31",
        ).pack(anchor="w", pady=(2, 0))

        # Footer packed FIRST at bottom so buttons are NEVER clipped
        ftr = tk.Frame(root, bg="#131c31", padx=20, pady=12)
        ftr.pack(side="bottom", fill="x")

        self.btn_install = tk.Button(
            ftr,
            text="Install / Update Now",
            command=self.start_install,
            bg="#0284c7",
            fg="#ffffff",
            font=("Segoe UI", 10, "bold"),
            relief="flat",
            padx=20,
            pady=7,
            cursor="hand2",
        )
        self.btn_install.pack(side="right")

        self.btn_launch = tk.Button(
            ftr,
            text="Launch TCG Grading Vault",
            command=self.launch_app,
            bg="#10b981",
            fg="#ffffff",
            font=("Segoe UI", 10, "bold"),
            relief="flat",
            padx=16,
            pady=7,
            cursor="hand2",
            state="disabled",
        )
        self.btn_launch.pack(side="right", padx=(0, 10))

        # Main Body
        body = tk.Frame(root, bg="#0f172a", padx=20, pady=12)
        body.pack(side="top", fill="both", expand=True)

        # Version & Backup Banner
        self.ver_banner = tk.Frame(body, bg="#172554", padx=12, pady=8)
        self.ver_banner.pack(fill="x", pady=(0, 10))
        self.ver_lbl = tk.Label(
            self.ver_banner,
            text="",
            font=("Segoe UI", 9, "bold"),
            fg="#93c5fd",
            bg="#172554",
            justify="left",
            wraplength=600,
        )
        self.ver_lbl.pack(anchor="w")
        self.backup_lbl = tk.Label(
            self.ver_banner,
            text="Tip: Updating preserves your 'data' folder, but consider backing up your database (data/tcg_tracker.db) to another folder first.",
            font=("Segoe UI", 8),
            fg="#cbd5e1",
            bg="#172554",
            justify="left",
            wraplength=600,
        )
        self.backup_lbl.pack(anchor="w", pady=(2, 0))

        # Install location picker + Open Folder button
        tk.Label(
            body,
            text="Install / Update Folder (choose where TCG Grading Vault is installed):",
            font=("Segoe UI", 9, "bold"),
            fg="#e2e8f0",
            bg="#0f172a",
        ).pack(anchor="w")
        loc_row = tk.Frame(body, bg="#0f172a")
        loc_row.pack(fill="x", pady=(3, 10))
        self.dir_entry = tk.Entry(
            loc_row,
            textvariable=self.install_dir_var,
            bg="#1e293b",
            fg="#f8fafc",
            insertbackground="#f8fafc",
            relief="flat",
            font=("Segoe UI", 9),
        )
        self.dir_entry.pack(side="left", fill="x", expand=True, ipady=5, padx=(0, 8))
        tk.Button(
            loc_row,
            text="Browse...",
            command=self.browse_folder,
            bg="#334155",
            fg="#f8fafc",
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            padx=12,
            pady=4,
            cursor="hand2",
        ).pack(side="left", padx=(0, 6))
        tk.Button(
            loc_row,
            text="Open Folder",
            command=self.open_target_folder,
            bg="#334155",
            fg="#f8fafc",
            font=("Segoe UI", 9),
            relief="flat",
            padx=10,
            pady=4,
            cursor="hand2",
        ).pack(side="left")
        tk.Button(
            loc_row,
            text="Restore Data...",
            command=self.restore_data_folder,
            bg="#1e3a8a",
            fg="#f8fafc",
            font=("Segoe UI", 9),
            relief="flat",
            padx=10,
            pady=4,
            cursor="hand2",
        ).pack(side="left", padx=(6, 0))

        # System requirements check box
        req_frame = tk.Frame(body, bg="#131c31", padx=12, pady=8)
        req_frame.pack(fill="x", pady=(0, 10))
        tk.Label(
            req_frame,
            text="System Requirements Check:",
            font=("Segoe UI", 9, "bold"),
            fg="#cbd5e1",
            bg="#131c31",
        ).pack(anchor="w", pady=(0, 4))
        self.req_list_frame = tk.Frame(req_frame, bg="#131c31")
        self.req_list_frame.pack(fill="x")
        self.refresh_status_and_reqs()

        # Checkboxes
        opts = tk.Frame(body, bg="#0f172a")
        opts.pack(fill="x", pady=(0, 8))
        tk.Checkbutton(
            opts,
            text="Install / Verify Playwright Chromium (needed for PSA / BGS / CGC cert scan pulling)",
            variable=self.install_pw_var,
            fg="#e2e8f0",
            bg="#0f172a",
            selectcolor="#1e293b",
            activebackground="#0f172a",
            activeforeground="#ffffff",
            font=("Segoe UI", 9),
        ).pack(anchor="w")
        tk.Checkbutton(
            opts,
            text="Create Desktop & Start Menu shortcuts + register in Windows Uninstall Programs",
            variable=self.create_shortcut_var,
            fg="#e2e8f0",
            bg="#0f172a",
            selectcolor="#1e293b",
            activebackground="#0f172a",
            activeforeground="#ffffff",
            font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(2, 0))

        self.status_lbl = tk.Label(
            body,
            text="Ready. Click 'Install / Update Now' to proceed.",
            font=("Segoe UI", 9, "bold"),
            fg="#38bdf8",
            bg="#0f172a",
        )
        self.status_lbl.pack(anchor="w", pady=(2, 4))

        self.progress = ttk.Progressbar(body, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(0, 8))

        self.log_box = tk.Text(
            body,
            height=6,
            bg="#090d16",
            fg="#cbd5e1",
            font=("Consolas", 8),
            relief="flat",
            padx=8,
            pady=6,
            state="disabled",
        )
        self.log_box.pack(fill="both", expand=True)

    def refresh_status_and_reqs(self):
        target = Path(self.install_dir_var.get().strip() or str(SOURCE_DIR))
        # Update version banner
        if (target / "app.py").exists():
            vf = target / "version.txt"
            _, reg_v = detect_existing_install()
            file_v = vf.read_text("utf-8").strip() if vf.exists() else ""
            if target.resolve() == SOURCE_DIR.resolve() and reg_v and reg_v != APP_VERSION:
                cur_v = reg_v
            else:
                cur_v = file_v or reg_v or "0.70 or earlier"
            if cur_v == APP_VERSION:
                self.ver_lbl.config(
                    text=f"Existing install detected: v{cur_v}  —  Reinstalling / Repairing v{APP_VERSION} (your data/ folder will be kept)"
                )
                self.btn_install.config(text=f"Reinstall / Repair v{APP_VERSION}")
            else:
                self.ver_lbl.config(
                    text=f"Existing install detected: v{cur_v}  →  Installing / Updating to v{APP_VERSION} (your data/ folder will be kept)"
                )
                self.btn_install.config(text=f"Update to v{APP_VERSION}")
        else:
            self.ver_lbl.config(text=f"Fresh Installation: TCG Grading Vault v{APP_VERSION}")
            self.btn_install.config(text="Install Now")

        for w in self.req_list_frame.winfo_children():
            w.destroy()
        checks, pw_ok = check_system_requirements(target)
        for name, ok, note in checks:
            icon = "✔" if ok else "•"
            color = "#4ade80" if ok else ("#fbbf24" if "Optional" in note or "Will" in note or "Not found" in note else "#f87171")
            row = tk.Frame(self.req_list_frame, bg="#131c31")
            row.pack(fill="x", pady=1)
            tk.Label(row, text=f"{icon} {name}:", font=("Segoe UI", 8, "bold"), fg=color, bg="#131c31").pack(side="left")
            tk.Label(row, text=f" {note}", font=("Segoe UI", 8), fg="#94a3b8", bg="#131c31").pack(side="left")

    def browse_folder(self):
        chosen = filedialog.askdirectory(
            initialdir=self.install_dir_var.get() or str(SOURCE_DIR),
            title="Choose Install Location for TCG Grading Vault",
        )
        if chosen:
            chosen_p = Path(chosen)
            if chosen_p.name.lower() not in ("tcg grading vault", "tcg_grader_tracker") and not (chosen_p / "app.py").exists():
                chosen_p = chosen_p / "TCG Grading Vault"
            self.install_dir_var.set(str(chosen_p))
            self.refresh_status_and_reqs()

    def open_target_folder(self):
        target = Path(self.install_dir_var.get().strip() or str(SOURCE_DIR))
        target.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(str(target))

    def restore_data_folder(self):
        chosen = filedialog.askdirectory(
            title="Select your existing 'data' folder (or old TCG Grading Vault folder containing data/)",
        )
        if not chosen:
            return
        src = Path(chosen)
        if (src / "data" / "tcg_tracker.db").exists():
            src_data = src / "data"
        elif (src / "tcg_tracker.db").exists():
            src_data = src
        else:
            messagebox.showwarning("No Database Found", f"Could not find tcg_tracker.db inside:\n{src}")
            return
        target = Path(self.install_dir_var.get().strip() or str(SOURCE_DIR))
        dest_data = target / "data"
        dest_data.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src_data, dest_data, dirs_exist_ok=True)
        self.log(f"Restored database & scans from {src_data} -> {dest_data}")
        messagebox.showinfo("Database Restored", f"Restored your database and photos into:\n{dest_data}")

    def log(self, msg: str):
        def _append():
            self.log_box.configure(state="normal")
            self.log_box.insert("end", msg.rstrip() + "\n")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
        self.root.after(0, _append)

    def set_status(self, msg: str, pct: int | None = None):
        def _upd():
            self.status_lbl.config(text=msg)
            if pct is not None:
                self.progress["value"] = pct
        self.root.after(0, _upd)

    def run_cmd(self, cmd: list[str], cwd: Path) -> int:
        kwargs = dict(
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        if os.name == "nt":
            kwargs["creationflags"] = 0x08000000
        proc = subprocess.Popen(cmd, **kwargs)
        assert proc.stdout is not None
        for line in proc.stdout:
            if line.strip():
                self.log(line.rstrip())
        return proc.wait()

    def copy_app_to_target(self, target_dir: Path):
        import stat
        if target_dir.resolve() == SOURCE_DIR.resolve():
            return
        target_dir.mkdir(parents=True, exist_ok=True)
        self.log(f"Copying app files to {target_dir}...")

        def _force_copy(src_file, dst_file):
            src_p = Path(src_file)
            dst_p = Path(dst_file)
            if dst_p.exists():
                try:
                    os.chmod(dst_p, stat.S_IWRITE | stat.S_IREAD)
                except Exception:
                    pass
            try:
                shutil.copy2(src_p, dst_p)
            except PermissionError:
                if os.name == "nt":
                    subprocess.run(
                        ["powershell", "-NoProfile", "-Command",
                         f'Get-Process python,pythonw -ErrorAction SilentlyContinue | Where-Object {{ $_.Path -like "{target_dir}*" }} | Stop-Process -Force'],
                        capture_output=True,
                        creationflags=0x08000000,
                    )
                    import time as _t
                    _t.sleep(0.6)
                    try:
                        os.chmod(dst_p, stat.S_IWRITE | stat.S_IREAD)
                    except Exception:
                        pass
                    shutil.copy2(src_p, dst_p)
                else:
                    raise

        for item in SOURCE_DIR.iterdir():
            if item.name in (".venv", "__pycache__", ".git"):
                continue
            dest = target_dir / item.name
            if item.name == "data" and dest.exists():
                self.log("Existing data/ folder detected — preserving database and scans!")
                continue
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True, copy_function=_force_copy)
            else:
                _force_copy(item, dest)
        try:
            (target_dir / "version.txt").write_text(f"{APP_VERSION}\n", encoding="utf-8")
        except Exception:
            pass

    def start_install(self):
        if self.running:
            return
        self.running = True
        self.btn_install.configure(state="disabled")
        threading.Thread(target=self._install_worker, daemon=True).start()

    def _install_worker(self):
        try:
            target_dir = Path(self.install_dir_var.get().strip() or str(SOURCE_DIR))
            self.installed_dir = target_dir
            self.set_status("Step 1/5: Preparing install folder...", 10)
            self.copy_app_to_target(target_dir)

            venv_dir = target_dir / ".venv"
            venv_py = venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            req_file = target_dir / "requirements.txt"

            if not venv_py.exists():
                self.set_status("Step 2/5: Creating virtual environment (.venv)...", 22)
                self.log("Creating .venv...")
                rc = self.run_cmd([sys.executable, "-m", "venv", str(venv_dir)], target_dir)
                if rc != 0:
                    raise RuntimeError("Failed to create .venv.")
            else:
                self.set_status("Step 2/5: Using existing .venv...", 25)
                self.log("Using existing .venv...")

            self.set_status("Step 3/5: Installing/updating Python packages...", 42)
            self.log("Installing packages from requirements.txt...")
            self.run_cmd([str(venv_py), "-m", "pip", "install", "--upgrade", "pip"], target_dir)
            rc = self.run_cmd([str(venv_py), "-m", "pip", "install", "-r", str(req_file)], target_dir)
            if rc != 0:
                raise RuntimeError("pip install -r requirements.txt failed.")

            if self.install_pw_var.get():
                self.set_status("Step 4/5: Installing Playwright Chromium browser...", 72)
                self.log("Installing Chromium for Playwright...")
                rc = self.run_cmd([str(venv_py), "-m", "playwright", "install", "chromium"], target_dir)
                if rc != 0:
                    self.log("Warning: Playwright Chromium install returned non-zero; you can retry later.")

            if os.name == "nt":
                self.set_status("Step 5/5: Creating Desktop & Start Menu shortcuts...", 92)
                try:
                    sc_list = create_windows_shortcuts(target_dir, create_desktop=self.create_shortcut_var.get())
                    for sc in sc_list:
                        self.log(f"Created shortcut: {sc}")
                except Exception as exc:
                    self.log(f"Shortcut warning: {exc}")
                ok_reg, info_reg = register_windows_uninstaller(target_dir)
                self.log(info_reg if ok_reg else f"Uninstaller registry note: {info_reg}")

            self.set_status(f"v{APP_VERSION} ready! Click 'Launch TCG Grading Vault' below.", 100)
            self.log("All done!")
            self.root.after(0, self.refresh_status_and_reqs)
            self.root.after(0, lambda: self.btn_launch.configure(state="normal"))
        except Exception as exc:
            self.set_status(f"Setup failed: {exc}", None)
            self.log(f"ERROR: {exc}")
            self.root.after(0, lambda: messagebox.showerror("Setup Error", str(exc)))
        finally:
            self.running = False
            self.root.after(0, lambda: self.btn_install.configure(state="normal"))

    def launch_app(self):
        target_dir = getattr(self, "installed_dir", Path(self.install_dir_var.get().strip() or str(SOURCE_DIR)))
        venv_pythonw = target_dir / ".venv" / ("Scripts/pythonw.exe" if os.name == "nt" else "bin/python")
        venv_python = target_dir / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        exe = venv_pythonw if venv_pythonw.exists() else venv_python
        if exe.exists():
            subprocess.Popen([str(exe), str(target_dir / "run.py")], cwd=str(target_dir))
        elif os.name == "nt" and (target_dir / "start.bat").exists():
            os.startfile(str(target_dir / "start.bat"))
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    InstallerWizard(root)
    root.mainloop()
