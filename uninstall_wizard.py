import os
import shutil
import subprocess
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

APP_DIR = Path(__file__).resolve().parent


def remove_windows_shortcuts():
    if os.name != "nt":
        return
    try:
        ps = (
            '$ws = New-Object -ComObject WScript.Shell; '
            'foreach ($f in @($ws.SpecialFolders("Desktop"), $ws.SpecialFolders("Programs"))) { '
            '  $p = Join-Path $f "TCG Grading Vault.lnk"; '
            '  if (Test-Path $p) { Remove-Item -Force $p } '
            '}'
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
            capture_output=True,
            creationflags=0x08000000,
        )
    except Exception:
        pass


def remove_windows_uninstall_entry():
    if os.name != "nt":
        return
    try:
        import winreg
        winreg.DeleteKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Uninstall\TCGGradingVault",
        )
    except Exception:
        pass


def run_uninstall():
    root = tk.Tk()
    root.withdraw()
    confirm = messagebox.askyesno(
        "Uninstall TCG Grading Vault",
        f"Are you sure you want to uninstall TCG Grading Vault from:\n{APP_DIR}?",
    )
    if not confirm:
        root.destroy()
        return

    keep_data = messagebox.askyesno(
        "Keep Your Database & Card Photos?",
        "Do you want to KEEP your 'data' folder (your database, submissions, and uploaded card scans)?\n\n"
        "Click YES to keep your data folder.\n"
        "Click NO to permanently delete everything.",
    )

    remove_windows_shortcuts()
    remove_windows_uninstall_entry()

    if os.name == "nt":
        if keep_data:
            for item in APP_DIR.iterdir():
                if item.name.lower() == "data" or item.name == "uninstall_wizard.py":
                    continue
                try:
                    if item.is_dir():
                        shutil.rmtree(item, ignore_errors=True)
                    else:
                        item.unlink(missing_ok=True)
                except Exception:
                    pass
            messagebox.showinfo(
                "Uninstalled",
                f"TCG Grading Vault has been uninstalled.\n\nYour database and photos were kept in:\n{APP_DIR / 'data'}",
            )
            subprocess.Popen(
                ["cmd", "/c", f'ping 127.0.0.1 -n 2 >nul & del /f /q "{APP_DIR / "uninstall_wizard.py"}"'],
                creationflags=0x08000000,
            )
        else:
            messagebox.showinfo("Uninstalled", "TCG Grading Vault and all local data will now be removed.")
            subprocess.Popen(
                ["cmd", "/c", f'ping 127.0.0.1 -n 2 >nul & rmdir /s /q "{APP_DIR}"'],
                creationflags=0x08000000,
            )
    root.destroy()


if __name__ == "__main__":
    run_uninstall()
