import io
import json
import csv
import re
import shutil
import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus, urljoin

import openpyxl
import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageOps
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from infographic import generate_card_infographic, generate_multi_cards_infographic, generate_summary_infographic

BASE_DIR = Path(__file__).resolve().parent


def _resolve_data_dir() -> Path:
    local_data = BASE_DIR / "data"
    local_db = local_data / "tcg_tracker.db"
    parent_data = BASE_DIR.parent / "data"
    parent_db = parent_data / "tcg_tracker.db"
    if parent_db.exists():
        if not local_db.exists():
            return parent_data
        try:
            c_loc = sqlite3.connect(local_db).execute("SELECT COUNT(*) FROM submissions").fetchone()[0]
            c_par = sqlite3.connect(parent_db).execute("SELECT COUNT(*) FROM submissions").fetchone()[0]
            if c_loc == 0 and c_par > 0:
                return parent_data
        except Exception:
            pass
    return local_data


DATA_DIR = _resolve_data_dir()
UPLOADS_DIR = DATA_DIR / "uploads"
RAW_UPLOADS_DIR = UPLOADS_DIR / "raw"
CERT_UPLOADS_DIR = UPLOADS_DIR / "certs"
BRAND_UPLOADS_DIR = UPLOADS_DIR / "brand"
SHARED_SUBS_DIR = DATA_DIR / "shared_submissions"
STATIC_DIR = BASE_DIR / "static"
DB_PATH = DATA_DIR / "tcg_tracker.db"

for d in (DATA_DIR, UPLOADS_DIR, RAW_UPLOADS_DIR, CERT_UPLOADS_DIR, BRAND_UPLOADS_DIR, SHARED_SUBS_DIR, STATIC_DIR):
    d.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Local TCG Grading Tracker", version="3.4.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def no_cache_static(request: Request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn


def init_db() -> None:
    conn = get_db()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                company TEXT NOT NULL CHECK(company IN ('PSA', 'CGC', 'Beckett', 'TAG')),
                submission_number TEXT DEFAULT '',
                submitted_date TEXT DEFAULT '',
                status TEXT DEFAULT 'In Grading',
                submit_channel TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                submission_id INTEGER NOT NULL,
                card_name TEXT NOT NULL,
                card_year TEXT DEFAULT '',
                set_name TEXT DEFAULT '',
                card_number TEXT DEFAULT '',
                acquisition_cost REAL DEFAULT 0.0,
                grading_cost REAL DEFAULT 0.0,
                shipping_cost REAL DEFAULT 0.0,
                pred_corners REAL,
                pred_edges REAL,
                pred_centering REAL,
                pred_surface REAL,
                raw_front_image TEXT DEFAULT '',
                raw_back_image TEXT DEFAULT '',
                cert_number TEXT DEFAULT '',
                cert_front_image TEXT DEFAULT '',
                cert_back_image TEXT DEFAULT '',
                cert_lookup_url TEXT DEFAULT '',
                actual_grade TEXT DEFAULT '',
                actual_subgrades TEXT DEFAULT '',
                status TEXT DEFAULT 'In Grading',
                fanatics_url TEXT DEFAULT '',
                sold_price REAL,
                selling_fees REAL DEFAULT 0.0,
                sold_date TEXT DEFAULT '',
                initial_graded_value REAL,
                market_value REAL,
                market_value_source TEXT DEFAULT '',
                pricecharting_url TEXT DEFAULT '',
                psa_images_zip_url TEXT DEFAULT '',
                ebay_order_number TEXT DEFAULT '',
                ebay_item_number TEXT DEFAULT '',
                ebay_shipping_collected REAL DEFAULT 0.0,
                ebay_tax_collected REAL DEFAULT 0.0,
                market_value_updated_at TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (submission_id) REFERENCES submissions(id) ON DELETE CASCADE
            );
            """
        )
        existing_cols = {r[1] for r in conn.execute("PRAGMA table_info(cards)").fetchall()}
        migrations = {
            "card_year": "ALTER TABLE cards ADD COLUMN card_year TEXT DEFAULT ''",
            "initial_graded_value": "ALTER TABLE cards ADD COLUMN initial_graded_value REAL",
            "market_value": "ALTER TABLE cards ADD COLUMN market_value REAL",
            "market_value_source": "ALTER TABLE cards ADD COLUMN market_value_source TEXT DEFAULT ''",
            "pricecharting_url": "ALTER TABLE cards ADD COLUMN pricecharting_url TEXT DEFAULT ''",
            "psa_images_zip_url": "ALTER TABLE cards ADD COLUMN psa_images_zip_url TEXT DEFAULT ''",
            "ebay_order_number": "ALTER TABLE cards ADD COLUMN ebay_order_number TEXT DEFAULT ''",
            "ebay_item_number": "ALTER TABLE cards ADD COLUMN ebay_item_number TEXT DEFAULT ''",
            "ebay_shipping_collected": "ALTER TABLE cards ADD COLUMN ebay_shipping_collected REAL DEFAULT 0.0",
            "ebay_tax_collected": "ALTER TABLE cards ADD COLUMN ebay_tax_collected REAL DEFAULT 0.0",
            "market_value_updated_at": "ALTER TABLE cards ADD COLUMN market_value_updated_at TEXT DEFAULT ''",
            "curated_date": "ALTER TABLE cards ADD COLUMN curated_date TEXT DEFAULT ''",
            "pred_overall": "ALTER TABLE cards ADD COLUMN pred_overall TEXT DEFAULT ''",
        }
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT DEFAULT ''
            );
            """
        )
        for col, sql in migrations.items():
            if col not in existing_cols:
                conn.execute(sql)
        sub_sql_row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='submissions'").fetchone()
        if sub_sql_row and "'TAG'" not in (sub_sql_row["sql"] or ""):
            conn.execute("PRAGMA foreign_keys = OFF;")
            conn.execute("""
                CREATE TABLE submissions_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    company TEXT NOT NULL CHECK(company IN ('PSA', 'CGC', 'Beckett', 'TAG')),
                    submission_number TEXT DEFAULT '',
                    submitted_date TEXT DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'In Grading',
                    notes TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)
            conn.execute("INSERT INTO submissions_new SELECT * FROM submissions;")
            conn.execute("DROP TABLE submissions;")
            conn.execute("ALTER TABLE submissions_new RENAME TO submissions;")
            conn.execute("PRAGMA foreign_keys = ON;")
        sub_cols = {r[1] for r in conn.execute("PRAGMA table_info(submissions)").fetchall()}
        if "submit_channel" not in sub_cols:
            conn.execute("ALTER TABLE submissions ADD COLUMN submit_channel TEXT DEFAULT ''")
        conn.execute("UPDATE cards SET status = 'Unsold' WHERE status = 'Graded'")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cards_sub_id ON cards(submission_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cards_cert ON cards(cert_number);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cards_status ON cards(status);")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS auctions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                platform TEXT DEFAULT 'eBay',
                title TEXT DEFAULT '',
                image_url TEXT DEFAULT '',
                current_bid REAL DEFAULT 0.0,
                bid_count INTEGER DEFAULT 0,
                time_left TEXT DEFAULT '',
                status TEXT DEFAULT 'Active',
                cost REAL DEFAULT 0.0,
                final_price REAL,
                linked_card_id INTEGER,
                last_refreshed TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS imported_bundles (
                bundle_uid TEXT PRIMARY KEY,
                source_name TEXT DEFAULT '',
                submission_id INTEGER,
                imported_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


init_db()
try:
    _c = sqlite3.connect(DB_PATH)
    _sc = _c.execute("SELECT COUNT(*) FROM submissions").fetchone()[0]
    _cc = _c.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
    _c.close()
    print(f"[TCG Vault] Database: {DB_PATH} ({_sc} submissions, {_cc} cards)")
except Exception as _e:
    print(f"[TCG Vault] Database check warning: {_e}")


def copy_local_upload(rel_path: str) -> str:
    if not rel_path or rel_path.startswith("http"):
        return rel_path or ""
    src = UPLOADS_DIR / rel_path
    if not src.exists() or not src.is_file():
        return ""
    ext = src.suffix or ".webp"
    new_name = f"{uuid.uuid4().hex}{ext}"
    dest = src.parent / new_name
    shutil.copy2(src, dest)
    return f"{src.parent.name}/{new_name}"


def save_upload_file(upload: Optional[UploadFile], subdir: Path) -> str:
    if not upload or not upload.filename:
        return ""
    content = upload.file.read()
    if not content:
        return ""
    ext = Path(upload.filename).suffix.lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}:
        ext = ".jpg"

    # For raw card uploads, re-encode at full original resolution to optimized WebP (~75% smaller)
    if subdir == RAW_UPLOADS_DIR:
        try:
            im = Image.open(io.BytesIO(content))
            im = ImageOps.exif_transpose(im)
            if im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGB")
            filename = f"{uuid.uuid4().hex}.webp"
            dest = subdir / filename
            im.save(dest, format="WEBP", quality=85, method=6)
            return f"{subdir.name}/{filename}"
        except Exception:
            pass

    filename = f"{uuid.uuid4().hex}{ext}"
    dest = subdir / filename
    dest.write_bytes(content)
    return f"{subdir.name}/{filename}"


def delete_upload_if_local(rel_path: str) -> None:
    if not rel_path or rel_path.startswith("http"):
        return
    target = UPLOADS_DIR / rel_path
    if target.exists() and target.is_file():
        try:
            target.unlink()
        except OSError:
            pass


def extract_cert_number(raw_cert: str) -> str:
    s = str(raw_cert or "").strip()
    if not s:
        return ""
    if s.startswith("http://") or s.startswith("https://"):
        low = s.lower()
        if any(d in low for d in ("psacard.com", "cgc", "beckett.com", "taggrading.com")):
            m = re.search(r"(\d{5,})", s)
            return m.group(1) if m else ""
        return ""
    return s


def build_cert_url(company: str, cert_number: str) -> str:
    c = extract_cert_number(cert_number)
    if not c:
        return ""
    if company == "PSA":
        return f"https://www.psacard.com/cert/{c}"
    if company == "CGC":
        return f"https://www.cgcgrading.com/certlookup/cards/{c}/"
    if company == "Beckett":
        return f"https://www.beckett.com/grading/card-lookup?item_id={c}&item_type=BGS"
    if company == "TAG":
        return f"https://my.taggrading.com/card/{c}"
    return ""


def is_valid_grade(grade_str: str) -> bool:
    g = (grade_str or "").strip()
    return bool(g) and g.lower() not in ("not yet graded", "none", "n/a", "-")


def is_gem_grade(grade_str: str) -> bool:
    g = (grade_str or "").strip()
    return bool(re.search(r"\b10\b", g))


def grade_sort_key(grade_str: str) -> tuple:
    g = (grade_str or "").strip().lower()
    if "black" in g and "10" in g:
        return (10.5, g)
    if "pristine" in g and "10" in g:
        return (10.3, g)
    m = re.search(r"(\d+(?:\.\d+)?)", g)
    num = float(m.group(1)) if m else 0.0
    return (num, g)


def compute_grade_metrics(cards_list: list) -> dict:
    counts = {}
    graded_total = 0
    gem_10_count = 0

    for c in cards_list:
        g = (c.get("actual_grade") or "").strip()
        if not is_valid_grade(g):
            continue
        if g.lower() == "pristine 10":
            g = "Pristine 10"
        graded_total += 1
        if is_gem_grade(g):
            gem_10_count += 1
        counts[g] = counts.get(g, 0) + 1

    gem_rate = round((gem_10_count / graded_total) * 100, 1) if graded_total > 0 else 0.0
    breakdown = []
    for g, cnt in sorted(counts.items(), key=lambda kv: grade_sort_key(kv[0]), reverse=True):
        breakdown.append({
            "grade": g,
            "count": cnt,
            "pct": round((cnt / graded_total) * 100, 1) if graded_total > 0 else 0.0,
            "is_gem": is_gem_grade(g),
        })

    return {
        "graded_with_grade_count": graded_total,
        "gem_10_count": gem_10_count,
        "gem_rate": gem_rate,
        "grade_breakdown": breakdown,
    }


def fetch_html_with_playwright(url: str, visible: bool = True, max_wait_s: int = 45, window_bounds: Optional[tuple] = None, wait_for_login: bool = False) -> tuple:
    """
    Uses a visible local Edge/Chrome browser via Playwright with a persistent
    local profile (data/pw_profile) so Cloudflare verification cookies are saved.
    """
    pw_profile = str((DATA_DIR / "pw_profile").resolve())
    pop_w, pop_h, pop_x, pop_y = 960, 760, None, None
    if window_bounds and all(v is not None for v in window_bounds):
        wx, wy, ww, wh = [int(v) for v in window_bounds]
        pop_w = min(980, max(680, ww - 80))
        pop_h = min(760, max(560, wh - 90))
        pop_x = wx + max(20, (ww - pop_w) // 2)
        pop_y = wy + max(20, (wh - pop_h) // 2)
    extra_args = ["--disable-blink-features=AutomationControlled"]
    if not visible:
        extra_args.append("--headless=new")
    if visible:
        extra_args.append(f"--window-size={pop_w},{pop_h}")
        if pop_x is not None and pop_y is not None:
            extra_args.append(f"--window-position={pop_x},{pop_y}")
    helper_code = f"""
import json, sys, time
from playwright.sync_api import sync_playwright

url = {json.dumps(url)}
visible = {repr(visible)}
max_wait_s = {int(max_wait_s)}
wait_for_login = {repr(wait_for_login)}
profile_dir = {json.dumps(pw_profile)}
res = {{"url": url, "html": "", "error": ""}}

with sync_playwright() as p:
    ctx = None
    errs = []
    for channel in ("chrome", "msedge", None):
        try:
            kwargs = {{
                "headless": (not visible),
                "args": {json.dumps(extra_args)},
                "viewport": {{"width": {max(600, pop_w - 24)}, "height": {max(480, pop_h - 90)}}},
            }}
            if channel:
                kwargs["channel"] = channel
            ctx = p.chromium.launch_persistent_context(profile_dir, **kwargs)
            break
        except Exception as e:
            errs.append(str(e))
    if not ctx:
        res["error"] = "; ".join(errs[:2])
        print(json.dumps(res))
        sys.exit(0)
    try:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {{get: () => undefined}});")
        page.goto(url, wait_until="domcontentloaded", timeout=25000)

        deadline = time.time() + max_wait_s
        while time.time() < deadline:
            title = (page.title() or "").lower()
            body_txt = ""
            try:
                body_txt = page.inner_text("body")[:500].lower()
            except Exception:
                pass
            cur_u = (page.url or "").lower()
            is_cf = ("just a moment" in title or "verify you are human" in body_txt or "checking your browser" in body_txt)
            is_login_gate = wait_for_login and (
                any(k in cur_u for k in ("/login", "/signin", "/sign-in", "/auth"))
                or any(k in body_txt for k in ("page cannot be found", "page not found", "sign in to", "log in to"))
            )
            if not is_cf and not is_login_gate:
                try:
                    page.wait_for_load_state("networkidle", timeout=4500)
                except Exception:
                    pass
                page.wait_for_timeout(2000)
                break
            page.wait_for_timeout(1000)

        res["url"] = page.url
        res["html"] = page.content()
    except Exception as e:
        res["error"] = str(e)
    finally:
        ctx.close()

print(json.dumps(res))
"""
    try:
        proc = subprocess.run(
            [sys.executable, "-c", helper_code],
            capture_output=True,
            text=True,
            timeout=max_wait_s + 20,
        )
        if proc.returncode != 0:
            raw_err = (proc.stderr or "").strip()
            if "No module named 'playwright'" in raw_err:
                return url, "", "Playwright is not installed in .venv (run: pip install playwright && playwright install chromium)."
            return url, "", (raw_err.splitlines()[-1] if raw_err else "Playwright subprocess failed")[:220]
        lines = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
        if not lines:
            return url, "", "Playwright returned empty output."
        data = json.loads(lines[-1])
        return data.get("url", url), data.get("html", ""), data.get("error", "")
    except Exception as exc:
        return url, "", f"Browser fallback unavailable: {exc}"


def fetch_cgc_highres_with_playwright(url: str) -> tuple:
    """
    CGC cert pages show a low-res thumbnail until clicked.
    Opens CGC cert page headless in Playwright, clicks the slab image(s) to trigger
    the high-res lightbox/modal, and captures the full-resolution image URLs + HTML.
    """
    pw_profile = str((DATA_DIR / "pw_profile").resolve())
    helper_code = f"""
import json, sys, time
from playwright.sync_api import sync_playwright

url = {json.dumps(url)}
profile_dir = {json.dumps(pw_profile)}
res = {{"url": url, "html": "", "highres_urls": [], "error": ""}}

with sync_playwright() as p:
    ctx = None
    for ch in ("msedge", "chrome", None):
        try:
            kw = {{"headless": True, "viewport": {{"width": 1280, "height": 900}}}}
            if ch: kw["channel"] = ch
            ctx = p.chromium.launch_persistent_context(profile_dir, **kw)
            break
        except Exception:
            pass
    if not ctx:
        print(json.dumps(res))
        sys.exit(0)
    try:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        seen_hi = []
        def on_resp(resp):
            try:
                ct = (resp.headers.get("content-type") or "").lower()
                if "image/" in ct and resp.status == 200:
                    clen = int(resp.headers.get("content-length") or "0")
                    if clen > 45000 and resp.url not in seen_hi:
                        seen_hi.append(resp.url)
            except Exception:
                pass
        page.on("response", on_resp)
        page.goto(url, wait_until="networkidle", timeout=20000)
        page.wait_for_timeout(1200)
        res["html"] = page.content()

        # Click any slab/cert thumbnail images to trigger CGC's high-res modal
        imgs = page.locator("img").all()
        for im in imgs[:8]:
            try:
                src = (im.get_attribute("src") or "").lower()
                if any(bad in src for bad in ("logo", "icon", "svg", "avatar", "banner")):
                    continue
                if im.is_visible(timeout=400):
                    im.click(timeout=1500)
                    page.wait_for_timeout(1200)
                    for m_im in page.locator("img").all():
                        ms = m_im.get_attribute("src") or ""
                        if ms and ms.startswith("http") and not any(b in ms.lower() for b in ("logo", "icon", "banner")):
                            if ms not in seen_hi:
                                seen_hi.append(ms)
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(300)
            except Exception:
                pass

        res["url"] = page.url
        res["highres_urls"] = seen_hi
    except Exception as e:
        res["error"] = str(e)
    finally:
        ctx.close()

print(json.dumps(res))
"""
    try:
        proc = subprocess.run([sys.executable, "-c", helper_code], capture_output=True, text=True, timeout=35)
        lines = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
        if not lines:
            return url, "", []
        data = json.loads(lines[-1])
        return data.get("url", url), data.get("html", ""), data.get("highres_urls", [])
    except Exception:
        return url, "", []


def upgrade_cert_image_candidates(img_url: str) -> list:
    from urllib.parse import urlparse, parse_qs, unquote
    u = (img_url or "").strip()
    if not u:
        return []
    out = []

    # 0. Unwrap Next.js /_next/image?url=...&w=384&q=75 proxy URLs (used by CGC)
    base_targets = []
    try:
        parsed_u = urlparse(u)
        qs = parse_qs(parsed_u.query)
        if "url" in qs and qs["url"]:
            inner = unquote(qs["url"][0]).strip()
            if inner.startswith("http"):
                # Strip TN_ thumbnail prefix (e.g. TN_CAR6260059-022_OBV.jpg -> CAR6260059-022_OBV.jpg)
                no_tn = re.sub(r"/(?:TN_|tn_)", "/", inner)
                if no_tn != inner:
                    base_targets.append(no_tn)
                base_targets.append(inner)
    except Exception:
        pass

    no_tn_u = re.sub(r"/(?:TN_|tn_)", "/", u)
    if no_tn_u != u:
        base_targets.append(no_tn_u)
    base_targets.append(u)

    for tgt in base_targets:
        if tgt not in out:
            out.append(tgt)

    # 1. Strip common CDN resize query params like ?width=300 or &w=400 on direct URLs
    no_q = re.sub(r"([?&])(?:w|width|h|height|size|quality|q)=\d+&?", r"\1", base_targets[0], flags=re.I).rstrip("?&")
    if no_q not in out and "/_next/image" not in no_q:
        out.insert(0, no_q)
    # 2. Replace /small/, /medium/, /thumb/, /thumbnails/ with /large/ or /original/
    for folder in ("/large/", "/original/", "/full/"):
        u_dir = re.sub(r"/(?:small|med|medium|thumb|thumbs|thumbnail|thumbnails)/", folder, no_q, flags=re.I)
        if u_dir not in out:
            out.append(u_dir)
    # 3. Replace filename suffixes like _small, _med, _thumb, -thumb, _300
    u_suf = re.sub(r"([_-])(?:small|sm|med|medium|thumb|thumbnail)(?=\.[a-z]+(?:$|\?))", "", no_q, flags=re.I)
    if u_suf not in out:
        out.append(u_suf)
    u_suf_lg = re.sub(r"([_-])(?:small|sm|med|medium|thumb|thumbnail)(?=\.[a-z]+(?:$|\?))", r"\1large", no_q, flags=re.I)
    if u_suf_lg not in out:
        out.append(u_suf_lg)
    if no_q not in out:
        out.append(no_q)
    if u not in out:
        out.append(u)
    return out


def upgrade_cert_image_url(img_url: str) -> str:
    cands = upgrade_cert_image_candidates(img_url)
    return cands[0] if cands else (img_url or "").strip()


def download_image_to_certs(img_url: str, cert_number: str, side: str) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        )
    }
    resp = None
    best_bytes = 0
    for cand_url in upgrade_cert_image_candidates(img_url):
        try:
            r_cand = requests.get(cand_url, headers=headers, timeout=12)
            if r_cand.status_code == 200 and len(r_cand.content) > best_bytes:
                try:
                    with Image.open(io.BytesIO(r_cand.content)) as im_chk:
                        w, h = im_chk.size
                    if w >= 120 and h >= 120:
                        resp = r_cand
                        best_bytes = len(r_cand.content)
                        break
                except Exception:
                    pass
        except Exception:
            continue
    if resp is None:
        resp = requests.get(img_url, headers=headers, timeout=12)
        resp.raise_for_status()
    ctype = resp.headers.get("Content-Type", "").lower()
    ext = ".jpg"
    if "png" in ctype or img_url.lower().endswith(".png"):
        ext = ".png"
    elif "webp" in ctype or img_url.lower().endswith(".webp"):
        ext = ".webp"
    safe_cert = re.sub(r"[^a-zA-Z0-9_-]", "", cert_number)[:32] or "cert"
    filename = f"{safe_cert}_{side}_{uuid.uuid4().hex[:8]}{ext}"
    if side == "back":
        try:
            with Image.open(io.BytesIO(resp.content)) as im:
                w, h = im.size
                if w > h * 1.15:
                    raise ValueError(f"Rejected landscape non-slab image ({w}x{h}) for cert back")
        except ValueError:
            raise
        except Exception:
            pass
    dest = CERT_UPLOADS_DIR / filename
    dest.write_bytes(resp.content)
    return f"certs/{filename}"


SET_ABBREVIATIONS = {
    "por": "Perfect Order",
    "cz": "Crown Zenith",
    "crz": "Crown Zenith",
    "svi": "Scarlet & Violet",
    "pal": "Paldea Evolved",
    "obf": "Obsidian Flames",
    "mew": "Pokemon 151",
    "par": "Paradox Rift",
    "paf": "Paldean Fates",
    "tef": "Temporal Forces",
    "twm": "Twilight Masquerade",
    "sfa": "Shrouded Fable",
    "scr": "Stellar Crown",
    "ssp": "Surging Sparks",
    "pre": "Prismatic Evolutions",
    "jtg": "Journey Together",
    "dri": "Destined Rivals",
    "sit": "Silver Tempest",
    "st": "Silver Tempest",
    "lor": "Lost Origin",
    "asr": "Astral Radiance",
    "brs": "Brilliant Stars",
    "fst": "Fusion Strike",
    "evs": "Evolving Skies",
    "cre": "Chilling Reign",
    "bst": "Battle Styles",
    "swsh": "Sword & Shield",
    "mep": "Mega Evolution Promo",
}


def expand_set_name(raw_set: str, card_name_hint: str = "") -> str:
    s = (raw_set or "").strip()
    if s.lower() in SET_ABBREVIATIONS:
        return SET_ABBREVIATIONS[s.lower()]
    for src in (s, card_name_hint or ""):
        for tok in re.findall(r"\b([A-Za-z]{2,5})\b", src):
            if tok.lower() in SET_ABBREVIATIONS:
                if not s or s.lower() == tok.lower():
                    return SET_ABBREVIATIONS[tok.lower()]
    return s


def parse_cert_details_from_html(html: str, company: str, base_url: str, cert_clean: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    # 1. Extract images
    candidates = []
    for meta in soup.find_all("meta", attrs={"property": re.compile(r"og:image", re.I)}):
        content = (meta.get("content") or "").strip()
        if content:
            candidates.append(("og", urljoin(base_url, content)))

    def _in_related_section(tag) -> bool:
        for parent in tag.parents:
            p_attr = " ".join([
                parent.get("id") or "",
                " ".join(parent.get("class") or []),
                parent.get("aria-label") or "",
                parent.get("data-testid") or "",
            ]).lower()
            if any(k in p_attr for k in ("similar", "related", "recommend", "also-like", "suggested", "more-from", "other-lot")):
                return True
        return False

    for img in soup.find_all("img"):
        if _in_related_section(img):
            continue
        parent_a = img.find_parent("a", href=True)
        parent_href = (parent_a.get("href") or "").strip() if parent_a else ""
        if parent_href and not re.search(r"\.(?:jpg|jpeg|png|webp)(?:\?|$)", parent_href, re.I):
            parent_href = ""
        src = (
            parent_href
            or img.get("data-zoom-image")
            or img.get("data-highres")
            or img.get("data-full")
            or img.get("data-large")
            or img.get("data-lg-src")
            or img.get("data-original")
            or img.get("data-image")
            or img.get("src")
            or img.get("data-src")
            or ""
        ).strip()
        srcset = (img.get("srcset") or "").strip()
        if srcset:
            best_ss_url = ""
            best_ss_w = -1
            for part in srcset.split(","):
                toks = part.strip().split()
                if toks:
                    w_val = 0
                    if len(toks) >= 2 and toks[1].endswith("w") and toks[1][:-1].isdigit():
                        w_val = int(toks[1][:-1])
                    if w_val >= best_ss_w:
                        best_ss_w = w_val
                        best_ss_url = toks[0]
            if best_ss_url:
                src = best_ss_url
        if not src or src.startswith("data:"):
            continue
        full_src = urljoin(base_url, src)
        alt = (img.get("alt") or "").lower()
        cls = " ".join(img.get("class") or []).lower()
        combined = f"{full_src.lower()} {alt} {cls}"
        if any(bad in combined for bad in ["logo", "icon", "favicon", "avatar", "banner", "spinner", "placeholder", "mobile_v1", "foundation", "promo", "hero", "social", "marketing", "wp-content"]):
            continue
        candidates.append((combined, full_src))

    next_script = soup.find("script", id="__NEXT_DATA__")
    if next_script and next_script.string:
        raw_json = next_script.string
        for m in re.findall(r'https?://[^"\'\s<>]+\.(?:jpg|jpeg|png|webp)(?:\?[^"\'\s<>]*)?', raw_json, re.I):
            low = m.lower()
            if any(bad in low for bad in ["logo", "icon", "favicon", "avatar", "ctfassets", "mobile_v1", "foundation", "banner", "promo", "hero", "marketing"]):
                continue
            candidates.append((low, m))

    for m in re.findall(r'https?://[^"\'\s<>]+(?:cloudfront|amazonaws|psacard|cgc|beckett)[^"\'\s<>]*\.(?:jpg|jpeg|png|webp)', html, re.I):
        low = m.lower()
        if any(bad in low for bad in ["logo", "icon", "favicon", "avatar", "banner", "mobile_v1", "foundation", "promo", "hero", "marketing"]):
            continue
        candidates.append((low, m))

    front_img_url = ""
    back_img_url = ""
    for meta_str, url in candidates:
        low = f"{meta_str} {url.lower()}"
        if not front_img_url and ("front" in low or "obverse" in low or "_obv" in low or "_f" in low or cert_clean.lower() in url.lower()):
            front_img_url = url
        elif not back_img_url and ("back" in low or "reverse" in low or "_rev" in low or "_b" in low):
            back_img_url = url
    slab_urls = []
    for meta_str, url in candidates:
        if url not in slab_urls and (meta_str == "og" or any(k in url.lower() for k in ["cert", "slab", "cloudfront", "amazonaws", "images"])):
            slab_urls.append(url)
    if not front_img_url and slab_urls:
        front_img_url = slab_urls[0]
    is_marketplace = any(d in (base_url or "").lower() for d in ("fanaticscollect", "ebay.", "goldin.", "ha.com", "pwcc"))
    if not back_img_url and len(slab_urls) >= 2:
        front_host = front_img_url.split("/")[2] if front_img_url.startswith("http") else ""
        front_dir = front_img_url.rsplit("/", 1)[0] if "/" in front_img_url else ""
        for u in slab_urls:
            if u == front_img_url:
                continue
            u_low = u.lower()
            if any(b in u_low for b in ["foundation", "banner", "promo", "og-", "marketing", "default", "share"]):
                continue
            u_host = u.split("/")[2] if u.startswith("http") else ""
            if front_host and u_host and u_host != front_host and cert_clean.lower() not in u_low:
                continue
            if is_marketplace:
                u_dir = u.rsplit("/", 1)[0] if "/" in u else ""
                has_back_hint = any(k in u_low for k in ("back", "reverse", "_b.", "-b.", "/b."))
                same_item_dir = bool(front_dir and u_dir == front_dir and not front_dir.endswith("/slabs"))
                if not has_back_hint and not same_item_dir:
                    continue
            back_img_url = u
            break

    # 2. Extract official label-value pairs (Year, Brand/Set, Card Number, Subject/Card Name, Grade)
    kv = {}
    for tr in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
        if len(cells) >= 2:
            kv[cells[0].lower().rstrip(":")] = cells[1].strip()

    for dl in soup.find_all("dl"):
        dts = dl.find_all("dt")
        dds = dl.find_all("dd")
        for dt, dd in zip(dts, dds):
            k = dt.get_text(" ", strip=True).lower().rstrip(":")
            v = dd.get_text(" ", strip=True)
            if k and v and k not in kv:
                kv[k] = v

    for key_pat, kv_key in [
        (r'"Year"\s*:\s*"([^"]+)"', "year"),
        (r'"Brand"\s*:\s*"([^"]+)"', "brand"),
        (r'"SetName"\s*:\s*"([^"]+)"', "set"),
        (r'"Subject"\s*:\s*"([^"]+)"', "subject"),
        (r'"CardNumber"\s*:\s*"([^"]+)"', "card number"),
        (r'"GradeDescription"\s*:\s*"([^"]+)"', "grade"),
    ]:
        if kv_key not in kv:
            mj = re.search(key_pat, html, re.I)
            if mj and mj.group(1).strip():
                kv[kv_key] = mj.group(1).strip()

    for idx, ln in enumerate(lines[:-1]):
        low_ln = ln.lower().rstrip(":")
        if low_ln in ("year", "brand", "set", "series", "subject", "player", "card", "card name", "card number", "card #", "grade", "variety/pedigree", "category"):
            if low_ln not in kv and len(lines[idx + 1]) < 120:
                kv[low_ln] = lines[idx + 1]

    official_year = kv.get("year", "")
    official_set = kv.get("brand") or kv.get("set") or kv.get("series") or ""
    official_name = kv.get("subject") or kv.get("card name") or kv.get("player") or kv.get("card") or ""
    official_number = kv.get("card number") or kv.get("card #") or ""
    official_grade = kv.get("grade") or ""

    if not official_grade:
        grade_match = re.search(
            r"(?:Grade|PSA|CGC|BGS)\s*[:#]?\s*(Pristine\s*10|Gem\s*Mint\s*10|10|9\.5|9|8\.5|8|7\.5|7|6\.5|6|5\.5|5|4\.5|4|3\.5|3|2\.5|2|1\.5|1)",
            text,
            re.I,
        )
        if grade_match:
            official_grade = grade_match.group(1).strip()

    # Clean numeric grade if PSA says "GEM MT 10" -> "10" or keep "Pristine 10"
    if official_grade:
        if "pristine" in official_grade.lower() and "10" in official_grade:
            official_grade = "Pristine 10"
        else:
            m_g = re.search(r"(10|9\.5|9|8\.5|8|7\.5|7|6\.5|6|5\.5|5|4\.5|4|3\.5|3|2\.5|2|1\.5|1)\b", official_grade)
            if m_g:
                official_grade = m_g.group(1)

    return {
        "front_img_url": front_img_url,
        "back_img_url": back_img_url,
        "official_name": official_name,
        "official_year": official_year,
        "official_set": expand_set_name(official_set, official_name),
        "official_number": official_number,
        "detected_grade": official_grade,
    }


def fetch_cert_scans_from_web(company: str, cert_number: str, window_bounds: Optional[tuple] = None, headless_only: bool = False) -> dict:
    cert_clean = extract_cert_number(cert_number)
    if not cert_clean:
        return {
            "success": False,
            "message": "Please enter a cert number first.",
            "lookup_url": "",
            "front_image": "",
            "back_image": "",
        }

    urls_to_try = [build_cert_url(company, cert_clean)]
    if company == "CGC":
        urls_to_try.append(f"https://www.cgccards.com/certlookup/{cert_clean}")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    parsed = {}
    error_reason = ""
    primary_url = urls_to_try[0]
    needs_browser = False

    for url in urls_to_try:
        try:
            resp = requests.get(url, headers=headers, timeout=10, allow_redirects=True)
            if resp.status_code in (403, 429):
                needs_browser = True
                continue
            if resp.status_code >= 400:
                error_reason = f"{company} returned HTTP {resp.status_code} for cert {cert_clean}."
                continue

            info = parse_cert_details_from_html(resp.text, company, resp.url, cert_clean)
            if info["front_img_url"] or info["official_name"]:
                parsed = info
                primary_url = resp.url
                break
            else:
                needs_browser = True
        except requests.RequestException as exc:
            error_reason = f"Network error reaching {company}: {exc}"

    # CGC shows low-res thumbnails until clicked: capture clicked high-res images if available
    if company == "CGC" and (not parsed.get("front_img_url") or not parsed.get("back_img_url")):
        cgc_url, cgc_html, hi_urls = fetch_cgc_highres_with_playwright(primary_url)
        if cgc_html:
            info = parse_cert_details_from_html(cgc_html, company, cgc_url, cert_clean)
            if info.get("official_name") or info.get("front_img_url"):
                parsed = info
                primary_url = cgc_url
        if hi_urls:
            # Prefer largest / clicked high-res URLs
            parsed["front_img_url"] = hi_urls[0]
            if len(hi_urls) >= 2:
                parsed["back_img_url"] = hi_urls[1]

    # Visible browser Playwright fallback for Cloudflare-protected PSA / Beckett / CGC pages
    if not parsed.get("front_img_url") and needs_browser:
        if company == "Beckett" and not headless_only:
            pw_url, pw_html, pw_err = fetch_html_with_playwright(primary_url, visible=True, max_wait_s=60, window_bounds=window_bounds)
        else:
            pw_url, pw_html, pw_err = fetch_html_with_playwright(primary_url, visible=False, max_wait_s=10, window_bounds=window_bounds)
            if (not pw_html or "just a moment" in pw_html.lower() or "verify you are human" in pw_html.lower()) and not headless_only:
                pw_url, pw_html, pw_err = fetch_html_with_playwright(primary_url, visible=True, max_wait_s=50, window_bounds=window_bounds)
        if pw_html and "just a moment" not in pw_html.lower():
            info = parse_cert_details_from_html(pw_html, company, pw_url, cert_clean)
            if info["front_img_url"] or info["official_name"] or info["detected_grade"]:
                parsed = info
                primary_url = pw_url
        elif pw_err:
            error_reason = pw_err

    saved_front = ""
    saved_back = ""
    if parsed.get("front_img_url"):
        try:
            saved_front = download_image_to_certs(parsed["front_img_url"], cert_clean, "front")
        except Exception:
            saved_front = parsed["front_img_url"]
    if parsed.get("back_img_url"):
        try:
            saved_back = download_image_to_certs(parsed["back_img_url"], cert_clean, "back")
        except Exception:
            saved_back = parsed["back_img_url"]

    has_any = bool(saved_front or saved_back or parsed.get("official_name") or parsed.get("detected_grade"))
    if has_any:
        return {
            "success": True,
            "message": f"Pulled cert data & scans from {company}!",
            "lookup_url": primary_url,
            "front_image": saved_front,
            "back_image": saved_back,
            "detected_grade": parsed.get("detected_grade", ""),
            "official_name": parsed.get("official_name", ""),
            "official_year": parsed.get("official_year", ""),
            "official_set": parsed.get("official_set", ""),
            "official_number": parsed.get("official_number", ""),
        }

    return {
        "success": False,
        "message": error_reason or (
            f"{company} required manual verification. Click 'Open Official Cert ↗', right-click the slab photo -> Copy Image, "
            "then click Cert Front/Back here and press Ctrl+V."
        ),
        "lookup_url": primary_url,
        "front_image": "",
        "back_image": "",
        "detected_grade": "",
        "official_name": "",
        "official_year": "",
        "official_set": "",
        "official_number": "",
    }


def score_pricecharting_candidate(href: str, link_text: str, card_name: str, set_name: str, card_number: str) -> int:
    score = 0
    combined = f"{href} {link_text}".lower()
    if "/game/" not in href.lower():
        return -1000

    # Match card number strongly (e.g., 160 from 160/159)
    num_token = ""
    if card_number:
        num_token = card_number.split("/")[0].strip().lstrip("0").lower()
    else:
        m = re.search(r"\b(\d{1,4})\s*/\s*\d{1,4}\b", card_name or "")
        if m:
            num_token = m.group(1).lstrip("0").lower()

    if num_token and re.search(rf"(?<!\d)0*{re.escape(num_token)}(?!\d)", combined):
        score += 50

    # Match language preference
    is_jp = any(k in (card_name + " " + set_name).lower() for k in ("(jp)", "japanese", "jpn"))
    has_jp_in_url = "japanese" in combined
    if is_jp == has_jp_in_url:
        score += 20
    else:
        score -= 25

    # Match set words
    for word in re.findall(r"[a-z0-9]{3,}", (set_name or "").lower()):
        if word in ("pokemon", "card", "tcg", "the", "and"):
            continue
        if word in combined:
            score += 15

    # Match card name words
    clean_name = re.sub(r"\([^)]*\)", "", card_name or "").lower()
    for word in re.findall(r"[a-z]{3,}", clean_name):
        if word in combined:
            score += 12

    return score


def fetch_graded_market_value(card_name: str, set_name: str, card_number: str, grade: str, company: str) -> dict:
    """
    Searches PriceCharting using official card name, set, and card number,
    scores search results to pick the exact card, and reads the exact
    Company + Grade row from PriceCharting's Full Price Guide table.
    """
    clean_name = re.sub(r"\([^)]*\)", "", card_name or "").strip()
    num_part = card_number.split("/")[0].strip() if card_number else ""
    expanded_set = expand_set_name(set_name, card_name)
    set_part = expanded_set.split("(")[0].strip() if expanded_set else ""
    query = " ".join(p for p in [clean_name, num_part, set_part] if p)
    search_url = f"https://www.pricecharting.com/search-products?q={quote_plus(query)}&type=prices"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        )
    }

    html = ""
    final_url = search_url
    try:
        r = requests.get(search_url, headers=headers, timeout=12, allow_redirects=True)
        if r.status_code == 200:
            html = r.text
            final_url = r.url
        elif r.status_code in (403, 429):
            final_url, html, _ = fetch_html_with_playwright(search_url, visible=False, max_wait_s=10)
    except requests.RequestException:
        pass

    if not html:
        return {
            "success": False,
            "price": None,
            "source_url": search_url,
            "message": "Could not reach PriceCharting right now.",
        }

    soup = BeautifulSoup(html, "html.parser")

    if "/search-products" in final_url:
        best_href = None
        best_score = -999
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/game/" not in href:
                continue
            sc = score_pricecharting_candidate(href, a.get_text(" ", strip=True), card_name, set_name, card_number)
            if sc > best_score:
                best_score = sc
                best_href = urljoin(final_url, href)
        if best_href:
            try:
                r2 = requests.get(best_href, headers=headers, timeout=12)
                if r2.status_code == 200:
                    soup = BeautifulSoup(r2.text, "html.parser")
                    final_url = r2.url
            except requests.RequestException:
                pass

    # Parse PriceCharting's exact Full Price Guide rows (e.g. "PSA 10", "CGC 10", "CGC 10 Pristine", "BGS 10", "Grade 9.5", "Grade 9", "Ungraded")
    exact_prices = {}
    for tr in soup.find_all("tr"):
        cells = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
        if len(cells) == 2:
            key = cells[0].strip().lower()
            m_price = re.search(r"^\s*\$([\d,]+(?:\.\d{2})?)", cells[1])
            if m_price:
                exact_prices[key] = float(m_price.group(1).replace(",", ""))

    g_low = (grade or "").strip().lower()
    comp_low = (company or "").strip().lower()

    # Build strict priority keys based on Grading Company + Grade
    priority_keys = []
    if "black" in g_low and "10" in g_low:
        priority_keys = ["bgs 10 black", "bgs 10", "psa 10"]
    elif "pristine" in g_low and "10" in g_low:
        if "cgc" in comp_low:
            priority_keys = ["cgc 10 pristine", "cgc 10", "psa 10"]
        else:
            priority_keys = ["bgs 10", "cgc 10 pristine", "psa 10"]
    elif "10" in g_low:
        if "cgc" in comp_low:
            priority_keys = ["cgc 10", "psa 10"]
        elif "beckett" in comp_low or "bgs" in comp_low:
            priority_keys = ["bgs 10", "psa 10"]
        elif "tag" in comp_low:
            priority_keys = ["tag 10", "psa 10", "cgc 10"]
        else:
            priority_keys = ["psa 10", "cgc 10", "bgs 10"]
    elif "9.5" in g_low:
        priority_keys = ["grade 9.5", "bgs 9.5", "cgc 9.5"]
    elif "9" in g_low:
        priority_keys = ["grade 9", "psa 9"]
    elif "8.5" in g_low or "8" in g_low:
        priority_keys = ["grade 8", "psa 8"]
    elif "7.5" in g_low or "7" in g_low:
        priority_keys = ["grade 7", "psa 7"]
    elif "6" in g_low:
        priority_keys = ["grade 6"]
    elif "5" in g_low:
        priority_keys = ["grade 5"]
    else:
        priority_keys = ["ungraded"]

    chosen_price = None
    matched_label = ""
    for pk in priority_keys:
        if pk in exact_prices:
            chosen_price = exact_prices[pk]
            matched_label = pk.upper()
            break

    pc_set = ""
    pc_num = ""
    title_tag = soup.find("title")
    if title_tag and "|" in title_tag.get_text():
        parts = [pt.strip() for pt in title_tag.get_text().split("|")]
        m_num = re.search(r"#([A-Za-z0-9/\-]+)", parts[0])
        if m_num:
            pc_num = m_num.group(1).strip()
        if len(parts) >= 2:
            pc_set = re.sub(r"^(?:Pokemon|Magic|One Piece|Lorcana|Dragon Ball|Yu-Gi-Oh!?)\s+", "", parts[1], flags=re.I).strip()

    if chosen_price is not None:
        return {
            "success": True,
            "price": round(chosen_price, 2),
            "matched_label": matched_label,
            "pricecharting_set": pc_set,
            "pricecharting_number": pc_num,
            "source_url": final_url,
            "message": f"Pulled PriceCharting ({matched_label}): ${chosen_price:,.2f}",
        }

    return {
        "success": False,
        "price": None,
        "source_url": final_url,
        "message": "Could not match that exact grade on PriceCharting. Open the link or enter Market Value manually.",
    }


def resolve_card_status(
    requested_status: str,
    sold_price: Optional[float],
    cert_number: str,
    actual_grade: str,
    listing_url: str,
    explicit_save: bool = False,
) -> str:
    st = (requested_status or "In Grading").strip()
    if st == "Graded":
        st = "Unsold"
    if explicit_save:
        if st == "Sold":
            return "Sold" if sold_price is not None else "Unsold"
        if st in ("Not Submitted", "In Grading", "Unsold", "Hold", "Listed"):
            return st
    if sold_price is not None:
        return "Sold"
    if st == "Sold":
        return "Unsold"
    if st == "Not Submitted":
        return "Not Submitted"
    if st == "In Grading" and (listing_url.strip() or cert_number.strip() or is_valid_grade(actual_grade)):
        return "Unsold"
    return st


class SubmissionCreate(BaseModel):
    title: str
    company: str
    submission_number: str = ""
    submitted_date: str = ""
    status: str = "In Grading"
    submit_channel: str = ""
    notes: str = ""


class SubmissionUpdate(BaseModel):
    title: Optional[str] = None
    company: Optional[str] = None
    submission_number: Optional[str] = None
    submitted_date: Optional[str] = None
    status: Optional[str] = None
    submit_channel: Optional[str] = None
    notes: Optional[str] = None


class CertFetchRequest(BaseModel):
    cert_number: Optional[str] = None
    company: Optional[str] = None
    win_x: Optional[int] = None
    win_y: Optional[int] = None
    win_w: Optional[int] = None
    win_h: Optional[int] = None
    background: bool = False


class OpenLocalPathRequest(BaseModel):
    path: str
    mode: str = "folder"


class ScanUrlUploadRequest(BaseModel):
    side: str = "cert_front"
    image_url: str


class ExtractLinkRequest(BaseModel):
    url: str
    company: str = ""


def parse_opt_float(val: Optional[str]) -> Optional[float]:
    if val is None or str(val).strip() == "":
        return None
    try:
        return float(val)
    except ValueError:
        return None


def parse_float(val: Optional[str], default: float = 0.0) -> float:
    res = parse_opt_float(val)
    return res if res is not None else default


@app.get("/api/stats")
def get_stats():
    conn = get_db()
    try:
        rows = [
            dict(r)
            for r in conn.execute(
                """
                SELECT c.*, s.company AS submission_company, s.title AS submission_title, COALESCE(s.submit_channel, '') AS submit_channel
                FROM cards c
                JOIN submissions s ON s.id = c.submission_id
                """
            ).fetchall()
        ]
        sub_count = conn.execute("SELECT COUNT(*) FROM submissions WHERE COALESCE(submit_channel, '') != 'eBay'").fetchone()[0]

        grading_rows = [r for r in rows if (r.get("submit_channel") or "") != "eBay"]
        total_cards = len(grading_rows)
        completed_count = 0
        grading_count = 0
        not_submitted_count = 0
        sold_count = 0
        not_shipped_subs = conn.execute("SELECT COUNT(*) FROM submissions WHERE status = 'Not Shipped'").fetchone()[0]

        total_acq = 0.0
        total_grading = 0.0
        total_shipping = 0.0
        total_sold_revenue = 0.0
        sold_cards_cost = 0.0
        total_selling_fees = 0.0
        total_market_value_unsold = 0.0
        total_est_profit_unsold = 0.0
        unsold_with_mv_cost = 0.0
        total_card_values = 0.0

        top_card = None
        best_profit = None

        for r in rows:
            status = (r["status"] or "In Grading").strip()
            is_ebay_row = (r.get("submit_channel") or "") == "eBay"
            if not is_ebay_row:
                if status == "Not Submitted":
                    not_submitted_count += 1
                elif status == "In Grading":
                    grading_count += 1
                else:
                    completed_count += 1

            acq = float(r["acquisition_cost"] or 0.0)
            grd = float(r["grading_cost"] or 0.0)
            shp = float(r["shipping_cost"] or 0.0)
            fees = float(r["selling_fees"] or 0.0)

            total_acq += acq
            total_grading += grd
            total_shipping += shp

            if status != "Sold":
                held_val = r.get("market_value") if r.get("market_value") is not None else r.get("initial_graded_value")
                is_graded_card = bool((r.get("actual_grade") or "").strip()) or (status not in ("In Grading", "Not Submitted"))
                if is_graded_card and held_val is not None:
                    total_card_values += float(held_val)
                elif held_val is not None:
                    total_card_values += float(held_val)
                else:
                    total_card_values += acq
                if held_val is not None:
                    total_market_value_unsold += float(held_val)
                    total_est_profit_unsold += float(held_val) - (acq + grd + shp)
                    unsold_with_mv_cost += (acq + grd + shp)

                # Unsold held slab profit uses initial_graded_value, or market_value if initial isn't set
                init_val = r.get("initial_graded_value") if r.get("initial_graded_value") is not None else r.get("market_value")
                if init_val is not None:
                    card_cost = acq + grd + shp
                    card_profit = float(init_val) - card_cost
                    if best_profit is None or card_profit > best_profit:
                        best_profit = card_profit
                        roi_pct = round((card_profit / card_cost) * 100, 1) if card_cost > 0 else 0.0
                        top_card = {
                            "id": r["id"],
                            "submission_id": r["submission_id"],
                            "card_name": r["card_name"],
                            "submission_company": r["submission_company"],
                            "submission_title": r["submission_title"],
                            "actual_grade": r["actual_grade"],
                            "cert_number": r["cert_number"],
                            "total_cost": round(card_cost, 2),
                            "sold_price": round(float(init_val), 2),
                            "profit": round(card_profit, 2),
                            "roi_pct": roi_pct,
                            "is_sold": False,
                            "image": r["cert_front_image"] or r["raw_front_image"] or "",
                            "has_cert_scan": bool(r["cert_front_image"]),
                        }

            if status == "Sold" and r["sold_price"] is not None:
                sold_count += 1
                sp = float(r["sold_price"])
                total_sold_revenue += sp
                total_selling_fees += fees
                card_cost = acq + grd + shp + fees
                sold_cards_cost += card_cost
                card_profit = sp - card_cost

                if best_profit is None or card_profit > best_profit:
                    best_profit = card_profit
                    roi_pct = round((card_profit / card_cost) * 100, 1) if card_cost > 0 else 0.0
                    top_card = {
                        "id": r["id"],
                        "submission_id": r["submission_id"],
                        "card_name": r["card_name"],
                        "submission_company": r["submission_company"],
                        "submission_title": r["submission_title"],
                        "actual_grade": r["actual_grade"],
                        "cert_number": r["cert_number"],
                        "total_cost": round(card_cost, 2),
                        "sold_price": round(sp, 2),
                        "profit": round(card_profit, 2),
                        "roi_pct": roi_pct,
                        "is_sold": True,
                        "image": r["cert_front_image"] or r["raw_front_image"] or "",
                        "has_cert_scan": bool(r["cert_front_image"]),
                    }

        total_collective_cost = total_acq + total_grading + total_shipping
        realized_profit = total_sold_revenue - sold_cards_cost
        net_overall = total_sold_revenue - total_collective_cost - total_selling_fees
        realized_roi_pct = round((realized_profit / sold_cards_cost) * 100, 1) if sold_cards_cost > 0 else 0.0
        unsold_est_roi_pct = round((total_est_profit_unsold / unsold_with_mv_cost) * 100, 1) if unsold_with_mv_cost > 0 else 0.0
        portfolio_value = (total_sold_revenue - total_selling_fees) + total_market_value_unsold
        overall_roi_pct = round(((portfolio_value - total_collective_cost) / total_collective_cost) * 100, 1) if total_collective_cost > 0 else 0.0
        gem_metrics = compute_grade_metrics(grading_rows)

        return {
            "total_submissions": sub_count,
            "total_cards": total_cards,
            "completed_grading": completed_count,
            "still_grading": grading_count,
            "not_submitted_cards": not_submitted_count,
            "not_shipped_subs": not_shipped_subs,
            "sold_cards": sold_count,
            "unsold_est_roi_pct": unsold_est_roi_pct,
            "total_acquisition_cost": round(total_acq, 2),
            "total_grading_cost": round(total_grading, 2),
            "total_shipping_cost": round(total_shipping, 2),
            "total_collective_cost": round(total_collective_cost, 2),
            "total_sold_revenue": round(total_sold_revenue, 2),
            "total_market_value_unsold": round(total_market_value_unsold, 2),
            "total_card_values": round(total_card_values, 2),
            "total_est_profit_unsold": round(total_est_profit_unsold, 2),
            "realized_roi_pct": realized_roi_pct,
            "overall_roi_pct": overall_roi_pct,
            "realized_profit": round(realized_profit, 2),
            "net_overall": round(net_overall, 2),
            "top_profitable_card": top_card,
            **gem_metrics,
        }
    finally:
        conn.close()


def _import_submission_bundle_bytes(raw_zip: bytes, source_name: str, conn) -> dict | None:
    import zipfile as _zf
    with _zf.ZipFile(io.BytesIO(raw_zip)) as zf:
        if "submission.json" not in zf.namelist():
            return None
        meta = json.loads(zf.read("submission.json").decode("utf-8"))
        return _insert_bundle_meta(meta, source_name, conn, zf=zf, folder_path=None)


def _import_submission_bundle_folder(folder: Path, conn) -> dict | None:
    sub_json = folder / "submission.json"
    if not sub_json.exists():
        return None
    meta = json.loads(sub_json.read_text("utf-8"))
    return _insert_bundle_meta(meta, folder.name, conn, zf=None, folder_path=folder)


def _insert_bundle_meta(meta: dict, source_name: str, conn, zf=None, folder_path: Path | None = None) -> dict | None:
    sub_obj = meta.get("submission") or {}
    cards_arr = meta.get("cards") or []
    bundle_uid = (meta.get("bundle_uid") or "").strip()
    if not bundle_uid:
        bundle_uid = f"{sub_obj.get('title','')}_{sub_obj.get('submitted_date','')}_{len(cards_arr)}"
    already = conn.execute("SELECT submission_id FROM imported_bundles WHERE bundle_uid = ?", (bundle_uid,)).fetchone()
    if already:
        return None

    ts = now_iso()
    cur = conn.execute(
        """
        INSERT INTO submissions (title, company, submission_number, submitted_date, status, submit_channel, notes, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            sub_obj.get("title") or source_name or "Imported Submission",
            sub_obj.get("company") or "PSA",
            sub_obj.get("submission_number") or "",
            sub_obj.get("submitted_date") or datetime.now().strftime("%Y-%m-%d"),
            sub_obj.get("status") or "In Grading",
            sub_obj.get("submit_channel") or "",
            sub_obj.get("notes") or f"Imported from bundle ({source_name})",
            ts,
            ts,
        ),
    )
    new_sub_id = cur.lastrowid

    def _copy_img(rel_path: str) -> str:
        rel = (rel_path or "").strip()
        if not rel or rel.startswith("http"):
            return rel
        src_rel = f"uploads/{rel}"
        img_bytes = None
        if zf is not None and src_rel in zf.namelist():
            img_bytes = zf.read(src_rel)
        elif folder_path is not None and (folder_path / src_rel).exists():
            img_bytes = (folder_path / src_rel).read_bytes()
        if img_bytes is None:
            return ""
        dest = UPLOADS_DIR / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            stem = dest.stem
            ext = dest.suffix
            new_name = f"{stem}_{uuid.uuid4().hex[:6]}{ext}"
            dest = dest.parent / new_name
            rel = str(Path(rel).parent / new_name).replace("\\", "/")
        dest.write_bytes(img_bytes)
        return rel

    for c in cards_arr:
        conn.execute(
            """
            INSERT INTO cards (
                submission_id, card_name, card_year, set_name, card_number,
                fanatics_url, acquisition_cost, grading_cost, shipping_cost,
                pred_overall, pred_corners, pred_edges, pred_centering, pred_surface,
                raw_front_image, raw_back_image, cert_number, cert_lookup_url,
                cert_front_image, cert_back_image, actual_grade, status,
                sold_price, selling_fees, sold_date, initial_graded_value,
                market_value, market_value_source, pricecharting_url,
                psa_images_zip_url, curated_date, notes, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_sub_id,
                c.get("card_name") or "Card",
                c.get("card_year") or "",
                c.get("set_name") or "",
                c.get("card_number") or "",
                c.get("fanatics_url") or "",
                float(c.get("acquisition_cost") or 0),
                float(c.get("grading_cost") or 0),
                float(c.get("shipping_cost") or 0),
                c.get("pred_overall"),
                c.get("pred_corners"),
                c.get("pred_edges"),
                c.get("pred_centering"),
                c.get("pred_surface"),
                _copy_img(c.get("raw_front_image") or ""),
                _copy_img(c.get("raw_back_image") or ""),
                c.get("cert_number") or "",
                c.get("cert_lookup_url") or "",
                _copy_img(c.get("cert_front_image") or ""),
                _copy_img(c.get("cert_back_image") or ""),
                c.get("actual_grade") or "",
                c.get("status") or "In Grading",
                c.get("sold_price"),
                float(c.get("selling_fees") or 0),
                c.get("sold_date") or "",
                c.get("initial_graded_value"),
                c.get("market_value"),
                c.get("market_value_source") or "",
                c.get("pricecharting_url") or "",
                c.get("psa_images_zip_url") or "",
                c.get("curated_date") or "",
                c.get("notes") or "",
                ts,
                ts,
            ),
        )

    conn.execute(
        "INSERT OR IGNORE INTO imported_bundles (bundle_uid, source_name, submission_id, imported_at) VALUES (?, ?, ?, ?)",
        (bundle_uid, source_name, new_sub_id, ts),
    )
    conn.commit()
    return {"submission_id": new_sub_id, "title": sub_obj.get("title") or source_name, "cards_count": len(cards_arr)}


def sync_shared_submissions_folder(conn) -> list[dict]:
    SHARED_SUBS_DIR.mkdir(parents=True, exist_ok=True)
    imported = []
    for entry in sorted(SHARED_SUBS_DIR.iterdir()):
        try:
            if entry.is_file() and entry.suffix.lower() == ".zip":
                res = _import_submission_bundle_bytes(entry.read_bytes(), entry.name, conn)
                if res:
                    imported.append(res)
            elif entry.is_dir() and (entry / "submission.json").exists():
                res = _import_submission_bundle_folder(entry, conn)
                if res:
                    imported.append(res)
        except Exception:
            continue
    return imported


@app.get("/api/submissions/{sub_id}/export-bundle")
def export_submission_bundle(sub_id: int, save_to_shared: bool = False):
    import zipfile as _zf
    sub = get_submission(sub_id)
    cards = sub.get("cards", [])
    bundle_uid = f"sub_{sub_id}_{uuid.uuid4().hex[:10]}"
    meta = {
        "bundle_uid": bundle_uid,
        "exported_at": now_iso(),
        "submission": {
            "title": sub.get("title") or "",
            "company": sub.get("company") or "PSA",
            "submission_number": sub.get("submission_number") or "",
            "submitted_date": sub.get("submitted_date") or "",
            "status": sub.get("status") or "In Grading",
            "submit_channel": sub.get("submit_channel") or "",
            "notes": sub.get("notes") or "",
        },
        "cards": cards,
    }
    buf = io.BytesIO()
    with _zf.ZipFile(buf, "w", _zf.ZIP_DEFLATED) as zf:
        zf.writestr("submission.json", json.dumps(meta, indent=2))
        seen_files = set()
        for c in cards:
            for col in ("raw_front_image", "raw_back_image", "cert_front_image", "cert_back_image"):
                rel = (c.get(col) or "").strip()
                if rel and not rel.startswith("http") and rel not in seen_files:
                    fp = UPLOADS_DIR / rel
                    if fp.exists() and fp.is_file():
                        zf.write(fp, f"uploads/{rel}")
                        seen_files.add(rel)
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", sub.get("title") or f"sub_{sub_id}").strip("_") or f"sub_{sub_id}"
    filename = f"{slug}_sub.zip"
    zip_bytes = buf.getvalue()
    if save_to_shared:
        SHARED_SUBS_DIR.mkdir(parents=True, exist_ok=True)
        (SHARED_SUBS_DIR / filename).write_bytes(zip_bytes)
        conn = get_db()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO imported_bundles (bundle_uid, source_name, submission_id, imported_at) VALUES (?, ?, ?, ?)",
                (bundle_uid, filename, sub_id, now_iso()),
            )
            conn.commit()
        finally:
            conn.close()
        return {"saved": True, "filename": filename, "path": str(SHARED_SUBS_DIR / filename)}
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/shared-submissions")
def list_shared_submissions():
    SHARED_SUBS_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    for entry in sorted(SHARED_SUBS_DIR.iterdir()):
        if entry.is_file() and entry.suffix.lower() == ".zip":
            items.append({"name": entry.name, "type": "zip"})
        elif entry.is_dir() and (entry / "submission.json").exists():
            items.append({"name": entry.name, "type": "folder"})
    return {"folder": str(SHARED_SUBS_DIR), "items": items}


@app.post("/api/submissions/import-bundle")
async def import_submission_bundle(files: list[UploadFile] = File(...)):
    SHARED_SUBS_DIR.mkdir(parents=True, exist_ok=True)
    conn = get_db()
    imported = []
    skipped = 0
    try:
        for f in files:
            raw = await f.read()
            fn = f.filename or f"sub_{uuid.uuid4().hex[:6]}.zip"
            dest = SHARED_SUBS_DIR / fn
            if not dest.exists():
                dest.write_bytes(raw)
            res = _import_submission_bundle_bytes(raw, fn, conn)
            if res:
                imported.append(res)
            else:
                skipped += 1
        return {
            "imported_count": len(imported),
            "skipped_count": skipped,
            "imported": imported,
            "message": f"Imported {len(imported)} submission(s)" + (f" ({skipped} already in database or invalid)." if skipped else "!"),
        }
    finally:
        conn.close()


@app.get("/api/submissions")
def list_submissions(sort: str = "newest", company: str = "ALL", status: str = "ALL"):
    conn = get_db()
    try:
        sync_shared_submissions_folder(conn)
        subs = conn.execute("SELECT * FROM submissions").fetchall()
        cards = conn.execute("SELECT * FROM cards").fetchall()

        cards_by_sub = {}
        for c in cards:
            cards_by_sub.setdefault(c["submission_id"], []).append(dict(c))

        result = []
        for s in subs:
            s_dict = dict(s)
            if company != "ALL" and s_dict["company"] != company:
                continue
            if status != "ALL" and s_dict["status"] != status:
                continue

            sub_cards = cards_by_sub.get(s_dict["id"], [])
            card_count = len(sub_cards)
            not_submitted = sum(1 for c in sub_cards if c["status"] == "Not Submitted")
            completed = sum(1 for c in sub_cards if c["status"] not in ("In Grading", "Not Submitted"))
            grading = sum(1 for c in sub_cards if c["status"] == "In Grading")
            sold = sum(1 for c in sub_cards if c["status"] == "Sold")

            total_cost = sum(
                float(c["acquisition_cost"] or 0) + float(c["grading_cost"] or 0) + float(c["shipping_cost"] or 0)
                for c in sub_cards
            )
            total_sold = sum(
                float(c["sold_price"]) for c in sub_cards if c["status"] == "Sold" and c["sold_price"] is not None
            )
            sold_cost = sum(
                float(c["acquisition_cost"] or 0)
                + float(c["grading_cost"] or 0)
                + float(c["shipping_cost"] or 0)
                + float(c["selling_fees"] or 0)
                for c in sub_cards
                if c["status"] == "Sold" and c["sold_price"] is not None
            )
            realized_profit = total_sold - sold_cost
            gem_metrics = compute_grade_metrics(sub_cards)

            def _thumb_val_key(c_item: dict) -> float:
                for k in ("market_value", "sold_price", "initial_graded_value"):
                    if c_item.get(k) is not None:
                        try:
                            return float(c_item[k])
                        except (TypeError, ValueError):
                            pass
                return -1.0

            preview_images = []
            for c in sorted(sub_cards, key=_thumb_val_key, reverse=True):
                img = c.get("cert_front_image") or c.get("raw_front_image")
                if img:
                    preview_images.append(img)
                if len(preview_images) >= 35:
                    break

            s_dict.update(
                {
                    "card_count": card_count,
                    "completed_count": completed,
                    "grading_count": grading,
                    "not_submitted_count": not_submitted,
                    "sold_count": sold,
                    "total_cost": round(total_cost, 2),
                    "total_sold": round(total_sold, 2),
                    "realized_profit": round(realized_profit, 2),
                    "preview_images": preview_images,
                    "gem_rate": gem_metrics["gem_rate"],
                    "gem_10_count": gem_metrics["gem_10_count"],
                    "graded_with_grade_count": gem_metrics["graded_with_grade_count"],
                }
            )
            result.append(s_dict)

        if sort == "oldest":
            result.sort(key=lambda x: (x["submitted_date"] or "9999-99-99", x["id"]))
        elif sort == "company":
            result.sort(key=lambda x: (x["company"], x["submitted_date"] or "0000-00-00"))
        elif sort == "cost_desc":
            result.sort(key=lambda x: x["total_cost"], reverse=True)
        elif sort == "profit_desc":
            result.sort(key=lambda x: x["realized_profit"], reverse=True)
        elif sort == "cards_desc":
            result.sort(key=lambda x: x["card_count"], reverse=True)
        elif sort == "gem_desc":
            result.sort(key=lambda x: x["gem_rate"], reverse=True)
        else:
            result.sort(key=lambda x: (x["submitted_date"] or "0000-00-00", x["id"]), reverse=True)

        return result
    finally:
        conn.close()


@app.post("/api/submissions")
def create_submission(payload: SubmissionCreate):
    if payload.company not in ("PSA", "CGC", "Beckett", "TAG"):
        raise HTTPException(status_code=400, detail="Company must be PSA, CGC, or Beckett.")
    title = payload.title.strip() or f"{payload.company} Submission"
    ts = now_iso()
    conn = get_db()
    try:
        cur = conn.execute(
            """
            INSERT INTO submissions (title, company, submission_number, submitted_date, status, submit_channel, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                title,
                payload.company,
                payload.submission_number.strip(),
                payload.submitted_date.strip() or datetime.now().strftime("%Y-%m-%d"),
                payload.status.strip() or "In Grading",
                (payload.submit_channel or "").strip(),
                payload.notes.strip(),
                ts,
                ts,
            ),
        )
        conn.commit()
        sub_id = cur.lastrowid
        row = conn.execute("SELECT * FROM submissions WHERE id = ?", (sub_id,)).fetchone()
        return dict(row)
    finally:
        conn.close()


@app.get("/api/submissions/{sub_id}")
def get_submission(sub_id: int):
    conn = get_db()
    try:
        sub = conn.execute("SELECT * FROM submissions WHERE id = ?", (sub_id,)).fetchone()
        if not sub:
            raise HTTPException(status_code=404, detail="Submission not found.")
        cards = conn.execute(
            "SELECT * FROM cards WHERE submission_id = ? ORDER BY id ASC", (sub_id,)
        ).fetchall()

        sub_cards = [dict(c) for c in cards]
        market_val_total = 0.0
        est_profit_unsold_total = 0.0
        for c in sub_cards:
            cost = float(c["acquisition_cost"] or 0) + float(c["grading_cost"] or 0) + float(c["shipping_cost"] or 0)
            c["total_cost"] = round(cost, 2)
            if c["status"] != "Sold" and c.get("market_value") is not None:
                market_val_total += float(c["market_value"])
            if c["status"] == "Sold" and c["sold_price"] is not None:
                c["profit"] = round(float(c["sold_price"]) - cost - float(c["selling_fees"] or 0), 2)
                c["est_profit"] = None
            else:
                c["profit"] = None
                est_val = c.get("market_value") if c.get("market_value") is not None else c.get("initial_graded_value")
                if est_val is not None:
                    c["est_profit"] = round(float(est_val) - cost, 2)
                    est_profit_unsold_total += c["est_profit"]
                else:
                    c["est_profit"] = None

        sold_cost_sub = sum(
            (c["total_cost"] + float(c.get("selling_fees") or 0))
            for c in sub_cards if c["status"] == "Sold" and c.get("sold_price") is not None
        )
        sold_profit_sub = sum(float(c["profit"]) for c in sub_cards if c.get("profit") is not None)
        unsold_with_est_cost = sum(
            c["total_cost"] for c in sub_cards if c["status"] != "Sold" and c.get("est_profit") is not None
        )
        total_sub_cost = sum(c["total_cost"] for c in sub_cards)
        total_sub_sold_net = sum(
            (float(c["sold_price"]) - float(c.get("selling_fees") or 0))
            for c in sub_cards if c["status"] == "Sold" and c.get("sold_price") is not None
        )
        realized_roi_pct = round((sold_profit_sub / sold_cost_sub) * 100, 1) if sold_cost_sub > 0 else None
        est_roi_pct = round((est_profit_unsold_total / unsold_with_est_cost) * 100, 1) if unsold_with_est_cost > 0 else None
        overall_roi_pct = round(((total_sub_sold_net + market_val_total - total_sub_cost) / total_sub_cost) * 100, 1) if total_sub_cost > 0 else None

        s_dict = dict(sub)
        s_dict["cards"] = sub_cards
        s_dict["total_market_value_unsold"] = round(market_val_total, 2)
        s_dict["total_est_profit_unsold"] = round(est_profit_unsold_total, 2)
        s_dict["realized_roi_pct"] = realized_roi_pct
        s_dict["est_roi_pct"] = est_roi_pct
        s_dict["overall_roi_pct"] = overall_roi_pct
        s_dict.update(compute_grade_metrics(sub_cards))
        return s_dict
    finally:
        conn.close()


@app.put("/api/submissions/{sub_id}")
def update_submission(sub_id: int, payload: SubmissionUpdate):
    conn = get_db()
    try:
        existing = conn.execute("SELECT * FROM submissions WHERE id = ?", (sub_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Submission not found.")

        company = payload.company if payload.company is not None else existing["company"]
        if company not in ("PSA", "CGC", "Beckett", "TAG"):
            raise HTTPException(status_code=400, detail="Company must be PSA, CGC, or Beckett.")

        conn.execute(
            """
            UPDATE submissions
            SET title = ?, company = ?, submission_number = ?, submitted_date = ?, status = ?, submit_channel = ?, notes = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                payload.title.strip() if payload.title is not None else existing["title"],
                company,
                payload.submission_number.strip() if payload.submission_number is not None else existing["submission_number"],
                payload.submitted_date.strip() if payload.submitted_date is not None else existing["submitted_date"],
                payload.status.strip() if payload.status is not None else existing["status"],
                payload.submit_channel.strip() if payload.submit_channel is not None else (existing["submit_channel"] if "submit_channel" in existing.keys() else ""),
                payload.notes.strip() if payload.notes is not None else existing["notes"],
                now_iso(),
                sub_id,
            ),
        )
        conn.commit()
        updated = conn.execute("SELECT * FROM submissions WHERE id = ?", (sub_id,)).fetchone()
        return dict(updated)
    finally:
        conn.close()


@app.delete("/api/submissions/{sub_id}")
def delete_submission(sub_id: int):
    conn = get_db()
    try:
        cards = conn.execute("SELECT raw_front_image, raw_back_image, cert_front_image, cert_back_image FROM cards WHERE submission_id = ?", (sub_id,)).fetchall()
        for c in cards:
            for col in ("raw_front_image", "raw_back_image", "cert_front_image", "cert_back_image"):
                delete_upload_if_local(c[col])
        conn.execute("DELETE FROM submissions WHERE id = ?", (sub_id,))
        conn.commit()
        return {"deleted": True}
    finally:
        conn.close()


@app.post("/api/submissions/{sub_id}/cards")
def add_card_to_submission(
    sub_id: int,
    card_name: str = Form(""),
    card_year: str = Form(""),
    set_name: str = Form(""),
    card_number: str = Form(""),
    acquisition_cost: str = Form("0"),
    grading_cost: str = Form("0"),
    shipping_cost: str = Form("0"),
    pred_corners: str = Form(""),
    pred_edges: str = Form(""),
    pred_centering: str = Form(""),
    pred_surface: str = Form(""),
    cert_number: str = Form(""),
    fanatics_url: str = Form(""),
    pred_overall: str = Form(""),
    notes: str = Form(""),
    quantity: int = Form(1),
    raw_front: Optional[UploadFile] = File(None),
    raw_back: Optional[UploadFile] = File(None),
):
    conn = get_db()
    try:
        sub = conn.execute("SELECT * FROM submissions WHERE id = ?", (sub_id,)).fetchone()
        if not sub:
            raise HTTPException(status_code=404, detail="Submission not found.")

        raw_front_path = save_upload_file(raw_front, RAW_UPLOADS_DIR)
        raw_back_path = save_upload_file(raw_back, RAW_UPLOADS_DIR)
        use_name = card_name.strip()
        use_year = card_year.strip()
        use_set = set_name.strip()
        use_num = card_number.strip()
        use_grade = ""
        cert_front_path = ""
        cert_back_path = ""
        cert_clean = extract_cert_number(cert_number)
        link_clean = fanatics_url.strip()

        if not use_name and not link_clean and not cert_clean:
            raise HTTPException(status_code=400, detail="Enter a Card Name, Graded Card Link, or Cert #.")

        if link_clean and (not use_name or not cert_clean):
            try:
                ext = extract_info_from_graded_url(link_clean, sub["company"])
                if not use_name and ext.get("card_name"):
                    use_name = ext["card_name"].strip()
                if not use_year and ext.get("card_year"):
                    use_year = ext["card_year"].strip()
                if not use_set and ext.get("set_name"):
                    use_set = ext["set_name"].strip()
                if not use_num and ext.get("card_number"):
                    use_num = ext["card_number"].strip()
                if not cert_clean and ext.get("cert_number"):
                    cert_clean = extract_cert_number(ext["cert_number"])
                if ext.get("actual_grade"):
                    use_grade = ext["actual_grade"].strip()
                if ext.get("front_img_url"):
                    cert_front_path = download_remote_image(ext["front_img_url"], CERT_UPLOADS_DIR)
                if ext.get("back_img_url"):
                    cert_back_path = download_remote_image(ext["back_img_url"], CERT_UPLOADS_DIR)
            except Exception:
                pass

        if cert_clean and (not card_name.strip() or not use_name):
            try:
                c_res = fetch_cert_scans_from_web(sub["company"], cert_clean)
                if not use_name and c_res.get("official_name"):
                    use_name = c_res["official_name"].strip()
                if not use_year and c_res.get("official_year"):
                    use_year = c_res["official_year"].strip()
                if not use_set and c_res.get("official_set"):
                    use_set = c_res["official_set"].strip()
                if not use_num and c_res.get("official_number"):
                    use_num = c_res["official_number"].strip()
                if not use_grade and c_res.get("detected_grade"):
                    use_grade = c_res["detected_grade"].strip()
                if not cert_front_path and c_res.get("front_image"):
                    cert_front_path = c_res["front_image"]
                if not cert_back_path and c_res.get("back_image"):
                    cert_back_path = c_res["back_image"]
            except Exception:
                pass

        if not use_name:
            use_name = f"{sub['company']} Cert #{cert_clean}" if cert_clean else "Graded Card"

        cert_url = build_cert_url(sub["company"], cert_clean) if cert_clean else ""
        default_st = "Not Submitted" if (sub["status"] or "") == "Not Shipped" else "In Grading"
        card_status = resolve_card_status(default_st, None, cert_clean, use_grade, link_clean)
        ts = now_iso()

        qty = max(1, min(200, int(quantity or 1)))
        first_card_id = None
        for idx in range(qty):
            rf = raw_front_path if idx == 0 else copy_local_upload(raw_front_path)
            rb = raw_back_path if idx == 0 else copy_local_upload(raw_back_path)
            cf = cert_front_path if idx == 0 else copy_local_upload(cert_front_path)
            cb = cert_back_path if idx == 0 else copy_local_upload(cert_back_path)
            cur = conn.execute(
                """
                INSERT INTO cards (
                    submission_id, card_name, card_year, set_name, card_number,
                    acquisition_cost, grading_cost, shipping_cost,
                    pred_corners, pred_edges, pred_centering, pred_surface, pred_overall,
                    raw_front_image, raw_back_image,
                    cert_number, cert_front_image, cert_back_image, cert_lookup_url,
                    actual_grade, status, fanatics_url, notes,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sub_id,
                    use_name,
                    use_year,
                    use_set,
                    use_num,
                    parse_float(acquisition_cost, 0.0),
                    parse_float(grading_cost, 0.0),
                    parse_float(shipping_cost, 0.0),
                    parse_opt_float(pred_corners),
                    parse_opt_float(pred_edges),
                    parse_opt_float(pred_centering),
                    parse_opt_float(pred_surface),
                    pred_overall.strip(),
                    rf,
                    rb,
                    cert_clean,
                    cf,
                    cb,
                    cert_url,
                    use_grade,
                    card_status,
                    link_clean,
                    notes.strip(),
                    ts,
                    ts,
                ),
            )
            if first_card_id is None:
                first_card_id = cur.lastrowid
        conn.commit()
        row = conn.execute("SELECT * FROM cards WHERE id = ?", (first_card_id,)).fetchone()
        res = dict(row)
        res["added_count"] = qty
        return res
    finally:
        conn.close()


@app.get("/api/cards")
def list_all_cards(q: str = ""):
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT c.*, s.company AS submission_company, s.title AS submission_title, s.submitted_date AS submission_date
            FROM cards c
            JOIN submissions s ON s.id = c.submission_id
            ORDER BY c.id DESC
            """
        ).fetchall()
        out = []
        q_low = (q or "").strip().lower()
        for r in rows:
            c = dict(r)
            cost = float(c["acquisition_cost"] or 0) + float(c["grading_cost"] or 0) + float(c["shipping_cost"] or 0)
            c["total_cost"] = round(cost, 2)
            if c["status"] == "Sold" and c["sold_price"] is not None:
                c["profit"] = round(float(c["sold_price"]) - cost - float(c["selling_fees"] or 0), 2)
                c["est_profit"] = None
            else:
                c["profit"] = None
                est_val = c.get("market_value") if c.get("market_value") is not None else c.get("initial_graded_value")
                c["est_profit"] = round(float(est_val) - cost, 2) if est_val is not None else None
            if q_low:
                hay = " ".join(str(c.get(k) or "") for k in (
                    "card_name", "set_name", "card_number", "card_year", "actual_grade",
                    "cert_number", "status", "submission_title", "submission_company", "curated_date", "notes"
                )).lower()
                if q_low not in hay:
                    continue
            out.append(c)
        return out
    finally:
        conn.close()


@app.get("/api/cards/{card_id}")
def get_card(card_id: int):
    conn = get_db()
    try:
        card = conn.execute(
            """
            SELECT c.*, s.company AS submission_company, s.title AS submission_title
            FROM cards c
            JOIN submissions s ON s.id = c.submission_id
            WHERE c.id = ?
            """,
            (card_id,),
        ).fetchone()
        if not card:
            raise HTTPException(status_code=404, detail="Card not found.")
        c_dict = dict(card)
        cost = float(c_dict["acquisition_cost"] or 0) + float(c_dict["grading_cost"] or 0) + float(c_dict["shipping_cost"] or 0)
        c_dict["total_cost"] = round(cost, 2)
        if c_dict["status"] == "Sold" and c_dict["sold_price"] is not None:
            c_dict["profit"] = round(float(c_dict["sold_price"]) - cost - float(c_dict["selling_fees"] or 0), 2)
            c_dict["est_profit"] = None
        else:
            c_dict["profit"] = None
            est_val = c_dict.get("market_value") if c_dict.get("market_value") is not None else c_dict.get("initial_graded_value")
            c_dict["est_profit"] = round(float(est_val) - cost, 2) if est_val is not None else None
        return c_dict
    finally:
        conn.close()


@app.put("/api/cards/{card_id}")
def update_card(
    card_id: int,
    card_name: str = Form(...),
    card_year: str = Form(""),
    set_name: str = Form(""),
    card_number: str = Form(""),
    acquisition_cost: str = Form("0"),
    grading_cost: str = Form("0"),
    shipping_cost: str = Form("0"),
    pred_corners: str = Form(""),
    pred_edges: str = Form(""),
    pred_centering: str = Form(""),
    pred_surface: str = Form(""),
    cert_number: str = Form(""),
    actual_grade: str = Form(""),
    actual_subgrades: str = Form(""),
    status: str = Form("In Grading"),
    fanatics_url: str = Form(""),
    sold_price: str = Form(""),
    selling_fees: str = Form("0"),
    sold_date: str = Form(""),
    initial_graded_value: str = Form(""),
    market_value: str = Form(""),
    pred_overall: str = Form(""),
    curated_date: str = Form(""),
    notes: str = Form(""),
    pricecharting_url: str = Form(""),
    cert_front_url_manual: str = Form(""),
    cert_back_url_manual: str = Form(""),
    raw_front: Optional[UploadFile] = File(None),
    raw_back: Optional[UploadFile] = File(None),
    cert_front: Optional[UploadFile] = File(None),
    cert_back: Optional[UploadFile] = File(None),
):
    conn = get_db()
    try:
        existing = conn.execute(
            """
            SELECT c.*, s.company AS submission_company
            FROM cards c JOIN submissions s ON s.id = c.submission_id
            WHERE c.id = ?
            """,
            (card_id,),
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Card not found.")

        raw_front_path = existing["raw_front_image"]
        if raw_front and raw_front.filename:
            delete_upload_if_local(raw_front_path)
            raw_front_path = save_upload_file(raw_front, RAW_UPLOADS_DIR)

        raw_back_path = existing["raw_back_image"]
        if raw_back and raw_back.filename:
            delete_upload_if_local(raw_back_path)
            raw_back_path = save_upload_file(raw_back, RAW_UPLOADS_DIR)

        cert_front_path = existing["cert_front_image"]
        if cert_front and cert_front.filename:
            delete_upload_if_local(cert_front_path)
            cert_front_path = save_upload_file(cert_front, CERT_UPLOADS_DIR)
        elif cert_front_url_manual.strip():
            cert_front_path = cert_front_url_manual.strip()

        cert_back_path = existing["cert_back_image"]
        if cert_back and cert_back.filename:
            delete_upload_if_local(cert_back_path)
            cert_back_path = save_upload_file(cert_back, CERT_UPLOADS_DIR)
        elif cert_back_url_manual.strip():
            cert_back_path = cert_back_url_manual.strip()

        cert_num_clean = extract_cert_number(cert_number)
        cert_changed = bool(cert_num_clean) and cert_num_clean != (existing["cert_number"] or "").strip()
        cert_url = build_cert_url(existing["submission_company"], cert_number) if cert_num_clean else ""
        parsed_sold = parse_opt_float(sold_price)
        parsed_init_val = parse_opt_float(initial_graded_value)
        parsed_mkt = parse_opt_float(market_value)
        mkt_source = existing["market_value_source"]
        mkt_updated = existing["market_value_updated_at"]

        use_name = card_name.strip()
        use_year = card_year.strip()
        use_set = set_name.strip()
        use_num = card_number.strip()
        use_grade = actual_grade.strip()

        # Only auto-pull market value (no browser popup) when cert_number is newly updated and market_value is empty
        if cert_changed and parsed_mkt is None:
            val_res = fetch_graded_market_value(use_name, use_set, use_num, use_grade, existing["submission_company"])
            if val_res.get("success") and val_res.get("price") is not None:
                parsed_mkt = val_res["price"]
                mkt_source = "PriceCharting"
                mkt_updated = now_iso()

        final_status = resolve_card_status(status, parsed_sold, cert_num_clean, use_grade, fanatics_url, explicit_save=True)
        if final_status != "Sold" and status.strip() != "Sold":
            parsed_sold = None

        conn.execute(
            """
            UPDATE cards
            SET card_name = ?, card_year = ?, set_name = ?, card_number = ?,
                acquisition_cost = ?, grading_cost = ?, shipping_cost = ?,
                pred_corners = ?, pred_edges = ?, pred_centering = ?, pred_surface = ?, pred_overall = ?,
                raw_front_image = ?, raw_back_image = ?,
                cert_number = ?, cert_front_image = ?, cert_back_image = ?, cert_lookup_url = ?,
                actual_grade = ?, actual_subgrades = ?, status = ?,
                fanatics_url = ?, sold_price = ?, selling_fees = ?, sold_date = ?,
                initial_graded_value = ?, market_value = ?, market_value_source = ?, market_value_updated_at = ?,
                pricecharting_url = ?, curated_date = ?, notes = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                use_name,
                use_year,
                use_set,
                use_num,
                parse_float(acquisition_cost, 0.0),
                parse_float(grading_cost, 0.0),
                parse_float(shipping_cost, 0.0),
                parse_opt_float(pred_corners),
                parse_opt_float(pred_edges),
                parse_opt_float(pred_centering),
                parse_opt_float(pred_surface),
                pred_overall.strip(),
                raw_front_path,
                raw_back_path,
                cert_num_clean,
                cert_front_path,
                cert_back_path,
                cert_url,
                use_grade,
                actual_subgrades.strip(),
                final_status,
                fanatics_url.strip(),
                parsed_sold,
                parse_float(selling_fees, 0.0),
                sold_date.strip(),
                parsed_init_val,
                parsed_mkt,
                mkt_source,
                mkt_updated,
                pricecharting_url.strip(),
                curated_date.strip(),
                notes.strip(),
                now_iso(),
                card_id,
            ),
        )
        conn.commit()
        return get_card(card_id)
    finally:
        conn.close()


class PullFromLinkRequest(BaseModel):
    url: str
    win_x: Optional[int] = None
    win_y: Optional[int] = None
    win_w: Optional[int] = None
    win_h: Optional[int] = None
    force_replace: bool = False


@app.post("/api/open-browser-session")
def open_browser_session(req: PullFromLinkRequest):
    url = (req.url or "").strip() or "https://www.fanaticscollect.com/"
    pw_profile = str((DATA_DIR / "pw_profile").resolve())
    pop_w, pop_h, pop_x, pop_y = 1000, 780, None, None
    if req.win_w and req.win_h:
        wx, wy, ww, wh = int(req.win_x or 0), int(req.win_y or 0), int(req.win_w), int(req.win_h)
        pop_w = min(1020, max(700, ww - 60))
        pop_h = min(800, max(580, wh - 80))
        pop_x = wx + max(20, (ww - pop_w) // 2)
        pop_y = wy + max(20, (wh - pop_h) // 2)
    extra_args = ["--disable-blink-features=AutomationControlled", f"--window-size={pop_w},{pop_h}"]
    if pop_x is not None and pop_y is not None:
        extra_args.append(f"--window-position={pop_x},{pop_y}")
    helper = f"""
import time
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    ctx = None
    for ch in ("msedge", "chrome", None):
        try:
            kw = {{"headless": False, "args": {json.dumps(extra_args)}, "viewport": {{"width": {pop_w - 24}, "height": {pop_h - 90}}}}}
            if ch: kw["channel"] = ch
            ctx = p.chromium.launch_persistent_context({json.dumps(pw_profile)}, **kw)
            break
        except Exception:
            pass
    if ctx:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto({json.dumps(url)}, wait_until="domcontentloaded", timeout=25000)
        while len(ctx.pages) > 0:
            try:
                if ctx.pages[0].is_closed():
                    break
            except Exception:
                break
            time.sleep(0.5)
        try:
            ctx.close()
        except Exception:
            pass
"""
    try:
        subprocess.Popen([sys.executable, "-c", helper])
        return {"opened": True, "message": "Opened local Vault browser! Sign into Fanatics Collect, then close that window when finished."}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not open browser session: {exc}")


def download_psa_zip_scans(zip_url: str, cert_number: str) -> tuple[str, str]:
    u = (zip_url or "").strip()
    if not u or not (u.startswith("http://") or u.startswith("https://")):
        return "", ""
    import zipfile as _zf
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        )
    }
    resp = requests.get(u, headers=headers, timeout=15)
    resp.raise_for_status()
    img_entries: list[tuple[str, bytes]] = []
    with _zf.ZipFile(io.BytesIO(resp.content)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            fn = info.filename
            if "__MACOSX" in fn or Path(fn).name.startswith("."):
                continue
            ext = Path(fn).suffix.lower()
            if ext in (".jpg", ".jpeg", ".png", ".webp"):
                img_entries.append((fn, zf.read(info)))
    if not img_entries:
        return "", ""

    front_bytes = None
    front_ext = ".jpg"
    back_bytes = None
    back_ext = ".jpg"

    if len(img_entries) == 1:
        front_bytes = img_entries[0][1]
        front_ext = Path(img_entries[0][0]).suffix.lower() or ".jpg"
    else:
        # Check explicit front/back filenames first
        front_cands = [e for e in img_entries if any(k in e[0].lower() for k in ("front", "_f.", "-f.", "obverse"))]
        back_cands = [e for e in img_entries if any(k in e[0].lower() for k in ("back", "_b.", "-b.", "reverse", "rear"))]
        if front_cands and back_cands:
            front_bytes = front_cands[0][1]
            front_ext = Path(front_cands[0][0]).suffix.lower() or ".jpg"
            back_bytes = back_cands[0][1]
            back_ext = Path(back_cands[0][0]).suffix.lower() or ".jpg"
        else:
            # Sort by name so _01 / _1 is usually front, _02 / _2 is back, or score back
            img_entries.sort(key=lambda x: x[0].lower())
            s0 = score_pokemon_card_back(img_entries[0][1], img_entries[0][0])
            s1 = score_pokemon_card_back(img_entries[1][1], img_entries[1][0])
            if s0 > s1 + 8.0:
                back_bytes, front_bytes = img_entries[0][1], img_entries[1][1]
                back_ext = Path(img_entries[0][0]).suffix.lower() or ".jpg"
                front_ext = Path(img_entries[1][0]).suffix.lower() or ".jpg"
            else:
                front_bytes, back_bytes = img_entries[0][1], img_entries[1][1]
                front_ext = Path(img_entries[0][0]).suffix.lower() or ".jpg"
                back_ext = Path(img_entries[1][0]).suffix.lower() or ".jpg"

    safe_cert = re.sub(r"[^a-zA-Z0-9_-]", "", cert_number or "cert")[:32] or "cert"
    saved_front = ""
    saved_back = ""
    if front_bytes:
        fn_f = f"{safe_cert}_front_{uuid.uuid4().hex[:8]}{front_ext}"
        (CERT_UPLOADS_DIR / fn_f).write_bytes(front_bytes)
        saved_front = f"certs/{fn_f}"
    if back_bytes:
        fn_b = f"{safe_cert}_back_{uuid.uuid4().hex[:8]}{back_ext}"
        (CERT_UPLOADS_DIR / fn_b).write_bytes(back_bytes)
        saved_back = f"certs/{fn_b}"
    return saved_front, saved_back


@app.post("/api/cards/{card_id}/fetch-cert")
def fetch_card_cert(card_id: int, payload: CertFetchRequest):
    conn = get_db()
    try:
        card = conn.execute(
            """
            SELECT c.*, s.company AS submission_company
            FROM cards c JOIN submissions s ON s.id = c.submission_id
            WHERE c.id = ?
            """,
            (card_id,),
        ).fetchone()
        if not card:
            raise HTTPException(status_code=404, detail="Card not found.")

        raw_input_cert = (payload.cert_number if payload.cert_number is not None else card["cert_number"] or "").strip()
        if raw_input_cert.startswith("http://") or raw_input_cert.startswith("https://"):
            if not any(d in raw_input_cert.lower() for d in ("psacard.com", "cgc", "beckett.com", "taggrading.com")):
                return pull_card_from_link(
                    card_id,
                    PullFromLinkRequest(
                        url=raw_input_cert,
                        win_x=payload.win_x,
                        win_y=payload.win_y,
                        win_w=payload.win_w,
                        win_h=payload.win_h,
                    ),
                )
        cert_num = extract_cert_number(raw_input_cert)
        cert_changed = bool(cert_num) and cert_num != (card["cert_number"] or "").strip()
        company = payload.company or card["submission_company"]

        # Fast-path: if card has a direct PSA CloudFront Images zip URL, download & extract directly (no Playwright)
        zip_url = (card["psa_images_zip_url"] or "").strip() if "psa_images_zip_url" in card.keys() else ""
        if zip_url and not cert_changed:
            try:
                zf_front, zf_back = download_psa_zip_scans(zip_url, cert_num)
                if zf_front or zf_back:
                    new_front = zf_front or card["cert_front_image"]
                    new_back = zf_back or card["cert_back_image"]
                    conn.execute(
                        "UPDATE cards SET cert_front_image = ?, cert_back_image = ?, updated_at = ? WHERE id = ?",
                        (new_front, new_back, now_iso(), card_id),
                    )
                    conn.commit()
                    return {
                        "success": True,
                        "message": "Pulled HQ slab scans from PSA order zip!",
                        "lookup_url": card["cert_lookup_url"] or build_cert_url(company, cert_num),
                        "front_image": new_front,
                        "back_image": new_back,
                        "card": get_card(card_id),
                    }
            except Exception:
                pass

        bounds = (payload.win_x, payload.win_y, payload.win_w, payload.win_h) if payload.win_w and payload.win_h else None
        res = fetch_cert_scans_from_web(company, cert_num, window_bounds=bounds, headless_only=bool(payload.background))

        new_front = res["front_image"] or card["cert_front_image"]
        new_back = res["back_image"] or card["cert_back_image"]
        new_name = res.get("official_name") or card["card_name"]
        new_year = res.get("official_year") or card["card_year"] or ""
        new_set = res.get("official_set") or card["set_name"] or ""
        new_num = res.get("official_number") or card["card_number"] or ""
        new_grade = res.get("detected_grade") or card["actual_grade"] or ""
        new_status = resolve_card_status(card["status"], card["sold_price"], cert_num, new_grade, card["fanatics_url"])

        new_mkt = card["market_value"]
        new_mkt_src = card["market_value_source"]
        new_mkt_at = card["market_value_updated_at"]
        if cert_changed and new_mkt is None:
            val_res = fetch_graded_market_value(new_name, new_set, new_num, new_grade, company)
            if val_res.get("success") and val_res.get("price") is not None:
                new_mkt = val_res["price"]
                new_mkt_src = "PriceCharting"
                new_mkt_at = now_iso()

        conn.execute(
            """
            UPDATE cards
            SET card_name = ?, card_year = ?, set_name = ?, card_number = ?,
                cert_number = ?, cert_lookup_url = ?, cert_front_image = ?, cert_back_image = ?,
                actual_grade = ?, status = ?, market_value = ?, market_value_source = ?,
                market_value_updated_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                new_name,
                new_year,
                new_set,
                new_num,
                cert_num,
                res["lookup_url"],
                new_front,
                new_back,
                new_grade,
                new_status,
                new_mkt,
                new_mkt_src,
                new_mkt_at,
                now_iso(),
                card_id,
            ),
        )
        conn.commit()
        res["card"] = get_card(card_id)
        return res
    finally:
        conn.close()


@app.post("/api/cards/{card_id}/upload-scan")
def upload_single_scan(
    card_id: int,
    side: str = Form("front"),
    file: UploadFile = File(...),
):
    conn = get_db()
    try:
        card = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
        if not card:
            raise HTTPException(status_code=404, detail="Card not found.")
        col_map = {
            "raw_front": ("raw_front_image", RAW_UPLOADS_DIR),
            "raw_back": ("raw_back_image", RAW_UPLOADS_DIR),
            "front": ("cert_front_image", CERT_UPLOADS_DIR),
            "cert_front": ("cert_front_image", CERT_UPLOADS_DIR),
            "back": ("cert_back_image", CERT_UPLOADS_DIR),
            "cert_back": ("cert_back_image", CERT_UPLOADS_DIR),
        }
        col, target_dir = col_map.get(side, ("cert_front_image", CERT_UPLOADS_DIR))
        delete_upload_if_local(card[col])
        saved = save_upload_file(file, target_dir)
        conn.execute(f"UPDATE cards SET {col} = ?, updated_at = ? WHERE id = ?", (saved, now_iso(), card_id))
        conn.commit()
        return {"card": get_card(card_id)}
    finally:
        conn.close()


@app.post("/api/cards/{card_id}/upload-scan-url")
def upload_scan_from_url(card_id: int, req: ScanUrlUploadRequest):
    img_url = (req.image_url or "").strip()
    if not img_url:
        raise HTTPException(status_code=400, detail="No image URL provided.")

    conn = get_db()
    try:
        card = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
        if not card:
            raise HTTPException(status_code=404, detail="Card not found.")

        col_map = {
            "raw_front": ("raw_front_image", RAW_UPLOADS_DIR),
            "raw_back": ("raw_back_image", RAW_UPLOADS_DIR),
            "front": ("cert_front_image", CERT_UPLOADS_DIR),
            "cert_front": ("cert_front_image", CERT_UPLOADS_DIR),
            "back": ("cert_back_image", CERT_UPLOADS_DIR),
            "cert_back": ("cert_back_image", CERT_UPLOADS_DIR),
        }
        col, target_dir = col_map.get(req.side, ("cert_front_image", CERT_UPLOADS_DIR))

        raw_bytes = None
        ext = ".jpg"
        if img_url.startswith("data:image/"):
            import base64
            header, b64 = img_url.split(",", 1)
            raw_bytes = base64.b64decode(b64)
            if "png" in header:
                ext = ".png"
            elif "webp" in header:
                ext = ".webp"
        elif img_url.startswith("http://") or img_url.startswith("https://"):
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0.0.0 Safari/537.36"
                ),
                "Referer": img_url,
            }
            resp = requests.get(img_url, headers=headers, timeout=12)
            resp.raise_for_status()
            raw_bytes = resp.content
            ctype = resp.headers.get("Content-Type", "").lower()
            if "png" in ctype or img_url.lower().endswith(".png"):
                ext = ".png"
            elif "webp" in ctype or img_url.lower().endswith(".webp"):
                ext = ".webp"
        else:
            raise HTTPException(status_code=400, detail="Unsupported image URL format.")

        delete_upload_if_local(card[col])
        if target_dir == RAW_UPLOADS_DIR:
            filename = f"{uuid.uuid4().hex}.webp"
            dest = target_dir / filename
            try:
                with Image.open(io.BytesIO(raw_bytes)) as im:
                    im = ImageOps.exif_transpose(im)
                    if im.mode not in ("RGB", "RGBA"):
                        im = im.convert("RGB")
                    im.save(dest, format="WEBP", quality=85, method=6)
                saved = f"raw/{filename}"
            except Exception:
                fallback_name = f"{uuid.uuid4().hex}{ext}"
                (target_dir / fallback_name).write_bytes(raw_bytes)
                saved = f"raw/{fallback_name}"
        else:
            filename = f"{uuid.uuid4().hex}{ext}"
            (target_dir / filename).write_bytes(raw_bytes)
            saved = f"certs/{filename}"

        conn.execute(f"UPDATE cards SET {col} = ?, updated_at = ? WHERE id = ?", (saved, now_iso(), card_id))
        conn.commit()
        return {"card": get_card(card_id)}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not download dragged image: {exc}")
    finally:
        conn.close()


def extract_info_from_graded_url(url: str, company_hint: str = "") -> dict:
    u = (url or "").strip()
    out = {
        "cert_number": "",
        "detected_company": company_hint,
        "actual_grade": "",
        "card_name": "",
        "card_year": "",
        "set_name": "",
        "card_number": "",
        "source": "url",
    }
    if not u:
        return out

    low = u.lower()
    if "psacard.com" in low:
        out["detected_company"] = "PSA"
        m = re.search(r"/cert/(\d{5,12})", u, re.I)
        if m:
            out["cert_number"] = m.group(1)
    elif "cgc" in low:
        out["detected_company"] = "CGC"
        m = re.search(r"/(\d{7,12})(?:[/?#]|$)", u)
        if m:
            out["cert_number"] = m.group(1)
    elif "beckett.com" in low:
        out["detected_company"] = "Beckett"
        m = re.search(r"item_id=(\d{5,12})", u, re.I)
        if m:
            out["cert_number"] = m.group(1)
    elif "taggrading.com" in low:
        out["detected_company"] = "TAG"
        m = re.search(r"/card/([A-Za-z0-9]{5,16})", u, re.I)
        if m:
            out["cert_number"] = m.group(1)

    if not out["cert_number"]:
        m_cert = re.search(r"(?:cert(?:ificate)?[-_#/=]?|item_id=)(\d{6,12})", u, re.I)
        if m_cert:
            out["cert_number"] = m_cert.group(1)

    # Fetch page HTML silently (no popup) to extract cert #, grade, year, set, or card title
    if u.startswith("http://") or u.startswith("https://"):
        try:
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            }
            resp = requests.get(u, headers=headers, timeout=8, allow_redirects=True)
            if resp.status_code == 200 and "just a moment" not in resp.text.lower():
                soup = BeautifulSoup(resp.text, "html.parser")
                title_txt = soup.title.get_text(" ", strip=True) if soup.title else ""
                meta_desc = ""
                md = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
                if md and md.get("content"):
                    meta_desc = md["content"].strip()
                page_txt = soup.get_text(" ", strip=True)
                combined_txt = f"{title_txt} {meta_desc} {page_txt[:4000]}"

                if not out["cert_number"]:
                    m_pcert = re.search(r"(?:Cert(?:ificate)?\s*(?:#|No\.?|Number)?\s*[:#]?\s*)(\d{6,12})", combined_txt, re.I)
                    if m_pcert:
                        out["cert_number"] = m_pcert.group(1)

                if not out["actual_grade"]:
                    m_g = re.search(r"(PSA|CGC|BGS|Beckett)\s*(Pristine\s*10|Black\s*Label\s*10|Gem\s*Mint\s*10|10|9\.5|9|8\.5|8|7\.5|7|6\.5|6|5\.5|5|4\.5|4|3\.5|3|2\.5|2|1\.5|1)", combined_txt, re.I)
                    if m_g:
                        if not out["detected_company"]:
                            out["detected_company"] = "Beckett" if m_g.group(1).lower() in ("bgs", "beckett") else m_g.group(1).upper()
                        g_raw = m_g.group(2).strip()
                        if "pristine" in g_raw.lower():
                            out["actual_grade"] = "Pristine 10"
                        elif "black" in g_raw.lower():
                            out["actual_grade"] = "Black Label 10"
                        else:
                            m_num = re.search(r"(10|9\.5|9|8\.5|8|7\.5|7|6\.5|6|5\.5|5|4\.5|4|3\.5|3|2\.5|2|1\.5|1)", g_raw)
                            out["actual_grade"] = m_num.group(1) if m_num else g_raw

                if not out["card_year"]:
                    m_yr = re.search(r"(199\d|20[0-3]\d)", title_txt or meta_desc)
                    if m_yr:
                        out["card_year"] = m_yr.group(1)

                if not out["card_number"]:
                    m_cn = re.search(r"#(\d{1,4}(?:/\d{1,4})?)", title_txt or meta_desc)
                    if m_cn:
                        out["card_number"] = m_cn.group(1)

                if out["detected_company"] in ("PSA", "CGC", "Beckett", "TAG") and out["cert_number"]:
                    parsed = parse_cert_details_from_html(resp.text, out["detected_company"], resp.url, out["cert_number"])
                    out["card_name"] = parsed.get("official_name") or out["card_name"]
                    out["card_year"] = parsed.get("official_year") or out["card_year"]
                    out["set_name"] = parsed.get("official_set") or out["set_name"]
                    out["card_number"] = parsed.get("official_number") or out["card_number"]
                    out["actual_grade"] = parsed.get("detected_grade") or out["actual_grade"]
                out["source"] = "page"
        except Exception:
            pass
    return out


@app.post("/api/extract-link-info")
def extract_link_info(req: ExtractLinkRequest):
    return extract_info_from_graded_url(req.url, req.company)


def parse_marketplace_html(html: str, page_url: str, company_hint: str = "") -> dict:
    soup = BeautifulSoup(html, "html.parser")
    og_title_tag = soup.find("meta", attrs={"property": "og:title"})
    h1_tag = soup.find("h1")
    title_txt = (
        (og_title_tag.get("content") if og_title_tag else "")
        or (h1_tag.get_text(" ", strip=True) if h1_tag else "")
        or (soup.title.get_text(" ", strip=True) if soup.title else "")
    ).strip()
    page_txt = soup.get_text(" ", strip=True)
    combined = f"{title_txt} {page_txt[:6000]}"

    out = {
        "cert_number": "",
        "detected_company": company_hint,
        "actual_grade": "",
        "card_name": "",
        "card_year": "",
        "set_name": "",
        "card_number": "",
        "front_img_url": "",
        "back_img_url": "",
    }

    for pat in [
        r'"(?:certNumber|certificationNumber|cert_number|certificateNumber|psaCert|cgcCert)"\s*:\s*"?(\d{6,12})"?',
        r"(?:Cert(?:ification|ificate)?\s*(?:#|No\.?|Number)?\s*[:#]?\s*)(\d{6,12})",
        r"(?:Serial\s*(?:#|No\.?)?\s*[:#]?\s*)(\d{6,12})",
    ]:
        m = re.search(pat, html if pat.startswith('"') else combined, re.I)
        if m:
            out["cert_number"] = m.group(1)
            break

    m_g = re.search(
        r"\b(PSA|CGC|BGS|Beckett|TAG)\s*(Pristine\s*10|Black\s*Label\s*10|Gem\s*Mint\s*10|10|9\.5|9|8\.5|8|7\.5|7|6\.5|6|5\.5|5|4\.5|4|3\.5|3|2\.5|2|1\.5|1)\b",
        combined,
        re.I,
    )
    if m_g:
        comp_raw = m_g.group(1).upper()
        out["detected_company"] = "Beckett" if comp_raw in ("BGS", "BECKETT") else comp_raw
        g_raw = m_g.group(2).strip()
        if "pristine" in g_raw.lower():
            out["actual_grade"] = "Pristine 10"
        elif "black" in g_raw.lower():
            out["actual_grade"] = "Black Label 10"
        else:
            m_num = re.search(r"(10|9\.5|9|8\.5|8|7\.5|7|6\.5|6|5\.5|5|4\.5|4|3\.5|3|2\.5|2|1\.5|1)", g_raw)
            out["actual_grade"] = m_num.group(1) if m_num else g_raw

    if title_txt:
        m_yr = re.search(r"\b(199\d|20[0-3]\d)\b", title_txt)
        if m_yr:
            out["card_year"] = m_yr.group(1)
        m_cn = re.search(r"#([A-Za-z0-9/\-]+)", title_txt)
        if m_cn:
            out["card_number"] = m_cn.group(1)
        clean_title = re.sub(r"\s*\|\s*.*$", "", title_txt)
        clean_title = re.sub(r"\b(?:PSA|CGC|BGS|Beckett)\s*(?:Pristine|Gem\s*Mint|Black\s*Label)?\s*(?:10|9\.5|9|8\.5|8|7\.5|7|6\.5|6|5\.5|5|4\.5|4|3\.5|3|2\.5|2|1\.5|1)\b", "", clean_title, flags=re.I)
        clean_title = re.sub(r"\s+", " ", clean_title).strip(" -|•")
        if clean_title and len(clean_title) <= 120:
            out["card_name"] = clean_title
            out["set_name"] = expand_set_name("", clean_title)

    cert_parsed = parse_cert_details_from_html(html, out["detected_company"] or company_hint, page_url, out["cert_number"] or "slab")
    out["front_img_url"] = cert_parsed.get("front_img_url") or ""
    out["back_img_url"] = cert_parsed.get("back_img_url") or ""
    out["card_name"] = cert_parsed.get("official_name") or out["card_name"]
    out["card_year"] = cert_parsed.get("official_year") or out["card_year"]
    out["set_name"] = cert_parsed.get("official_set") or out["set_name"]
    out["card_number"] = cert_parsed.get("official_number") or out["card_number"]
    out["actual_grade"] = cert_parsed.get("detected_grade") or out["actual_grade"]
    return out


@app.post("/api/cards/{card_id}/pull-from-link")
def pull_card_from_link(card_id: int, req: PullFromLinkRequest):
    url = (req.url or "").strip()
    if not url or not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(status_code=400, detail="Please enter a valid http(s) link.")

    conn = get_db()
    try:
        card = conn.execute(
            """
            SELECT c.*, s.company AS submission_company
            FROM cards c JOIN submissions s ON s.id = c.submission_id
            WHERE c.id = ?
            """,
            (card_id,),
        ).fetchone()
        if not card:
            raise HTTPException(status_code=404, detail="Card not found.")

        bounds = (req.win_x, req.win_y, req.win_w, req.win_h) if req.win_w and req.win_h else None
        info = extract_info_from_graded_url(url, card["submission_company"])
        front_url = ""
        back_url = ""

        if not info.get("cert_number") or not info.get("card_name"):
            pw_url, pw_html, _ = fetch_html_with_playwright(url, visible=True, max_wait_s=90, window_bounds=bounds, wait_for_login=True)
            if pw_html and "just a moment" not in pw_html.lower():
                parsed_pw = parse_marketplace_html(pw_html, pw_url, info.get("detected_company") or card["submission_company"])
                for k in ("cert_number", "detected_company", "actual_grade", "card_name", "card_year", "set_name", "card_number"):
                    if parsed_pw.get(k) and not info.get(k):
                        info[k] = parsed_pw[k]
                front_url = parsed_pw.get("front_img_url") or ""
                back_url = parsed_pw.get("back_img_url") or ""

        cert_num = info.get("cert_number") or card["cert_number"] or ""
        company = info.get("detected_company") or card["submission_company"]

        saved_front = ""
        saved_back = ""
        if front_url:
            try:
                saved_front = download_image_to_certs(front_url, cert_num or f"link_{card_id}", "front")
            except Exception:
                saved_front = ""
        if back_url:
            try:
                saved_back = download_image_to_certs(back_url, cert_num or f"link_{card_id}", "back")
            except Exception:
                saved_back = ""

        cert_res = {}
        if cert_num and (not saved_front or not info.get("card_name")):
            cert_res = fetch_cert_scans_from_web(company, cert_num, window_bounds=bounds)

        new_front = cert_res.get("front_image") or saved_front or card["cert_front_image"]
        new_back = cert_res.get("back_image") or saved_back or card["cert_back_image"]
        new_name = cert_res.get("official_name") or info.get("card_name") or card["card_name"]
        new_year = cert_res.get("official_year") or info.get("card_year") or card["card_year"] or ""
        new_set = cert_res.get("official_set") or info.get("set_name") or card["set_name"] or ""
        new_num = cert_res.get("official_number") or info.get("card_number") or card["card_number"] or ""
        new_grade = cert_res.get("detected_grade") or info.get("actual_grade") or card["actual_grade"] or ""
        lookup_url = cert_res.get("lookup_url") or (build_cert_url(company, cert_num) if cert_num else card["cert_lookup_url"])
        new_status = resolve_card_status(card["status"], card["sold_price"], cert_num, new_grade, url)

        conn.execute(
            """
            UPDATE cards
            SET card_name = ?, card_year = ?, set_name = ?, card_number = ?,
                cert_number = ?, cert_lookup_url = ?, cert_front_image = ?, cert_back_image = ?,
                actual_grade = ?, status = ?, fanatics_url = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                new_name,
                new_year,
                new_set,
                new_num,
                cert_num,
                lookup_url,
                new_front,
                new_back,
                new_grade,
                new_status,
                url,
                now_iso(),
                card_id,
            ),
        )
        conn.commit()
        updated = get_card(card_id)
        got_anything = bool(saved_front or cert_res.get("success") or info.get("card_name") or info.get("actual_grade") or cert_num)
        msg = cert_res.get("message") if cert_res.get("success") else (
            "Pulled card data & images from link!" if got_anything else "Saved link, but could not auto-read details from that page."
        )
        return {"success": got_anything, "message": msg, "card": updated}
    finally:
        conn.close()


def score_pokemon_card_back(img_bytes: bytes, filename: str = "") -> float:
    """
    Scores how likely an image is the BACK of a Pokémon card (vs the front artwork).
    English backs have a royal blue border/swirl + central red/white Pokéball.
    Japanese backs have deep navy/indigo + warm gold border/emblem.
    """
    score = 0.0
    fn = (filename or "").lower()
    if any(k in fn for k in ("back", "_b.", "-b.", "rear")):
        score += 15.0
    elif any(k in fn for k in ("front", "_f.", "-f.")):
        score -= 15.0

    try:
        with Image.open(io.BytesIO(img_bytes)) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im = im.resize((96, 134), Image.Resampling.BILINEAR)
            px = im.load()
            w, h = im.size
            total = w * h
            blue_px = 0
            border_blue = 0
            border_total = 0
            gold_px = 0
            center_red = 0
            center_white = 0
            center_total = 0

            for y in range(h):
                in_border_y = y < 18 or y >= h - 18
                in_center_y = 42 <= y <= 92
                for x in range(w):
                    r, g, b = px[x, y]
                    is_border = in_border_y or x < 14 or x >= w - 14
                    is_blue = (b > r + 18 and b > g + 6 and b > 65)
                    is_navy = (b > r + 10 and b > g and 35 <= b <= 150)
                    is_gold = (r > 140 and g > 105 and b < 95 and r > b + 45)
                    if is_blue:
                        blue_px += 1
                    if is_gold:
                        gold_px += 1
                    if is_border:
                        border_total += 1
                        if is_blue or is_navy:
                            border_blue += 1
                    if in_center_y and 26 <= x <= 70:
                        center_total += 1
                        if r > 145 and g < 95 and b < 95:
                            center_red += 1
                        elif r > 195 and g > 195 and b > 195:
                            center_white += 1

            blue_ratio = blue_px / max(1, total)
            border_blue_ratio = border_blue / max(1, border_total)
            gold_ratio = gold_px / max(1, total)
            center_ball_ratio = (center_red + center_white) / max(1, center_total)

            score += blue_ratio * 100.0
            score += border_blue_ratio * 80.0
            if center_red > 15 and center_white > 15:
                score += center_ball_ratio * 35.0
            if border_blue_ratio > 0.35 and gold_ratio > 0.08:
                score += 20.0
    except Exception:
        pass
    return score


def classify_raw_pair(bytes_a: bytes, name_a: str, bytes_b: bytes, name_b: str) -> tuple:
    score_a = score_pokemon_card_back(bytes_a, name_a)
    score_b = score_pokemon_card_back(bytes_b, name_b)
    if score_b >= score_a:
        return (0, 1)  # 0=front, 1=back
    return (1, 0)      # 1=front, 0=back


@app.post("/api/classify-raw-pair")
async def classify_raw_pair_endpoint(files: list[UploadFile] = File(...)):
    if len(files) != 2:
        raise HTTPException(status_code=400, detail="Please drop exactly 2 images (front & back).")
    b0 = await files[0].read()
    b1 = await files[1].read()
    front_idx, back_idx = classify_raw_pair(b0, files[0].filename or "", b1, files[1].filename or "")
    return {"front_index": front_idx, "back_index": back_idx}


@app.post("/api/cards/{card_id}/upload-raw-pair")
async def upload_raw_pair(card_id: int, files: list[UploadFile] = File(...)):
    if len(files) != 2:
        raise HTTPException(status_code=400, detail="Please drop exactly 2 images (front & back).")
    b0 = await files[0].read()
    b1 = await files[1].read()
    front_idx, back_idx = classify_raw_pair(b0, files[0].filename or "", b1, files[1].filename or "")
    raw_items = [(b0, files[0].filename or "img1.png"), (b1, files[1].filename or "img2.png")]
    front_bytes, front_name = raw_items[front_idx]
    back_bytes, back_name = raw_items[back_idx]

    def _save_raw_bytes(raw_bytes: bytes) -> str:
        filename = f"{uuid.uuid4().hex}.webp"
        dest = RAW_UPLOADS_DIR / filename
        try:
            with Image.open(io.BytesIO(raw_bytes)) as img:
                img = ImageOps.exif_transpose(img)
                if img.mode not in ("RGB", "RGBA"):
                    img = img.convert("RGB")
                img.save(dest, format="WEBP", quality=85, method=6)
            return f"raw/{filename}"
        except Exception:
            fallback_name = f"{uuid.uuid4().hex}.png"
            (RAW_UPLOADS_DIR / fallback_name).write_bytes(raw_bytes)
            return f"raw/{fallback_name}"

    conn = get_db()
    try:
        card = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
        if not card:
            raise HTTPException(status_code=404, detail="Card not found.")
        delete_upload_if_local(card["raw_front_image"])
        delete_upload_if_local(card["raw_back_image"])
        saved_front = _save_raw_bytes(front_bytes)
        saved_back = _save_raw_bytes(back_bytes)
        conn.execute(
            "UPDATE cards SET raw_front_image = ?, raw_back_image = ?, updated_at = ? WHERE id = ?",
            (saved_front, saved_back, now_iso(), card_id),
        )
        conn.commit()
        return {
            "card": get_card(card_id),
            "front_filename": front_name,
            "back_filename": back_name,
        }
    finally:
        conn.close()


@app.post("/api/cards/{card_id}/swap-raw")
def swap_raw_images(card_id: int):
    conn = get_db()
    try:
        card = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
        if not card:
            raise HTTPException(status_code=404, detail="Card not found.")
        conn.execute(
            "UPDATE cards SET raw_front_image = ?, raw_back_image = ?, updated_at = ? WHERE id = ?",
            (card["raw_back_image"], card["raw_front_image"], now_iso(), card_id),
        )
        conn.commit()
        return {"card": get_card(card_id)}
    finally:
        conn.close()


@app.post("/api/open-local-path")
def open_local_path(req: OpenLocalPathRequest):
    rel = (req.path or "").strip().lstrip("/")
    if not rel or rel.startswith("http://") or rel.startswith("https://"):
        raise HTTPException(status_code=400, detail="Not a local file path.")
    base_dir = UPLOADS_DIR.resolve()
    target = (base_dir / rel).resolve()
    if base_dir not in target.parents or not target.exists():
        raise HTTPException(status_code=404, detail="Local file not found.")
    try:
        if sys.platform.startswith("win"):
            if req.mode == "folder":
                subprocess.Popen(["explorer", "/select,", str(target)])
            else:
                try:
                    os.startfile(str(target))
                except Exception:
                    try:
                        subprocess.Popen(["cmd", "/c", "start", "", str(target)])
                    except Exception:
                        subprocess.Popen(["explorer", str(target)])
        elif sys.platform == "darwin":
            if req.mode == "folder":
                subprocess.Popen(["open", "-R", str(target)])
            else:
                subprocess.Popen(["open", str(target)])
        else:
            open_target = target.parent if req.mode == "folder" else target
            subprocess.Popen(["xdg-open", str(open_target)])
        return {"opened": True, "path": str(target)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not open local path: {exc}")


@app.post("/api/cards/{card_id}/fetch-value")
def fetch_card_value(card_id: int):
    conn = get_db()
    try:
        card = conn.execute(
            """
            SELECT c.*, s.company AS submission_company
            FROM cards c JOIN submissions s ON s.id = c.submission_id
            WHERE c.id = ?
            """,
            (card_id,),
        ).fetchone()
        if not card:
            raise HTTPException(status_code=404, detail="Card not found.")

        res = fetch_graded_market_value(
            card["card_name"],
            card["set_name"],
            card["card_number"],
            card["actual_grade"],
            card["submission_company"],
        )
        if res.get("success") and res.get("price") is not None:
            new_set = card["set_name"] or ""
            if res.get("pricecharting_set") and (not new_set.strip() or len(new_set.strip()) <= 5):
                new_set = res["pricecharting_set"]
            new_num = card["card_number"] or ""
            if res.get("pricecharting_number") and not new_num.strip():
                new_num = res["pricecharting_number"]
            conn.execute(
                """
                UPDATE cards
                SET set_name = ?, card_number = ?, market_value = ?, market_value_source = ?, market_value_updated_at = ?, pricecharting_url = COALESCE(NULLIF(?, ''), pricecharting_url), updated_at = ?
                WHERE id = ?
                """,
                (new_set, new_num, res["price"], "PriceCharting", now_iso(), res.get("source_url") or "", now_iso(), card_id),
            )
            conn.commit()
        res["card"] = get_card(card_id)
        return res
    finally:
        conn.close()


class PriceChartingUrlRequest(BaseModel):
    url: str


def fetch_tcgplayer_product_data(tcg_url: str) -> dict:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/html, */*",
    }
    m_pid = re.search(r"tcgplayer\.com/product/(\d+)", tcg_url, re.I)
    price = None
    name = ""
    set_name = ""
    card_num = ""
    if m_pid:
        pid = m_pid.group(1)
        try:
            rp = requests.get(f"https://mpapi.tcgplayer.com/v2/product/{pid}/pricepoints", headers=headers, timeout=10)
            if rp.status_code == 200:
                pts = rp.json()
                if isinstance(pts, list) and pts:
                    normal = next((p for p in pts if (p.get("printingType") or "").lower() == "normal" and p.get("marketPrice") is not None), None)
                    best = normal or next((p for p in pts if p.get("marketPrice") is not None), None) or next((p for p in pts if p.get("listedMedianPrice") is not None), None)
                    if best:
                        raw_p = best.get("marketPrice") if best.get("marketPrice") is not None else best.get("listedMedianPrice")
                        if raw_p is not None:
                            price = round(float(raw_p), 2)
        except Exception:
            pass
        try:
            rd = requests.get(f"https://mpapi.tcgplayer.com/v2/product/{pid}/details", headers=headers, timeout=10)
            if rd.status_code == 200:
                det = rd.json()
                name = (det.get("productName") or "").strip()
                set_name = (det.get("setName") or "").strip()
                custom = det.get("customAttributes") or {}
                card_num = str(custom.get("number") or "").strip()
        except Exception:
            pass

    if price is None:
        html = ""
        try:
            r = requests.get(tcg_url, headers=headers, timeout=12, allow_redirects=True)
            if r.status_code == 200:
                html = r.text
        except requests.RequestException:
            pass
        if not html or ("marketprice" not in html.lower() and "price-points" not in html.lower()):
            try:
                _, pw_html, _ = fetch_html_with_playwright(tcg_url, visible=False, max_wait_s=12)
                if pw_html:
                    html = pw_html
            except Exception:
                pass
        if html:
            m_mp = re.search(r'"marketPrice"\s*:\s*([\d.]+)', html)
            if not m_mp:
                m_mp = re.search(r'Market\s+Price[^\$]{0,80}\$([\d,]+(?:\.\d{2})?)', html, re.I)
            if m_mp:
                price = round(float(m_mp.group(1).replace(",", "")), 2)
            if not name:
                soup = BeautifulSoup(html, "html.parser")
                h1 = soup.find("h1")
                if h1:
                    name = h1.get_text(" ", strip=True)
    return {"price": price, "name": name, "set_name": set_name, "card_number": card_num}


@app.post("/api/cards/{card_id}/fetch-value-url")
def fetch_card_value_from_url(card_id: int, req: PriceChartingUrlRequest):
    pc_url = (req.url or "").strip()
    if not pc_url or not (pc_url.startswith("http://") or pc_url.startswith("https://")):
        raise HTTPException(status_code=400, detail="Please enter a valid PriceCharting or TCGplayer URL.")

    conn = get_db()
    try:
        card = conn.execute(
            """
            SELECT c.*, s.company AS submission_company
            FROM cards c JOIN submissions s ON s.id = c.submission_id
            WHERE c.id = ?
            """,
            (card_id,),
        ).fetchone()
        if not card:
            raise HTTPException(status_code=404, detail="Card not found.")

        if "tcgplayer.com" in pc_url.lower():
            tcg_data = fetch_tcgplayer_product_data(pc_url)
            chosen_price = tcg_data.get("price")
            tcg_name = tcg_data.get("name") or card["card_name"]
            tcg_set = tcg_data.get("set_name") or card["set_name"] or ""
            tcg_num = tcg_data.get("card_number") or card["card_number"] or ""
            new_mkt = round(chosen_price, 2) if chosen_price is not None else card["market_value"]
            new_src = "TCGplayer" if chosen_price is not None else card["market_value_source"]
            new_at = now_iso() if chosen_price is not None else card["market_value_updated_at"]
            conn.execute(
                """
                UPDATE cards
                SET card_name = ?, card_number = ?, set_name = ?,
                    market_value = ?, market_value_source = ?, market_value_updated_at = ?, pricecharting_url = ?, updated_at = ?
                WHERE id = ?
                """,
                (tcg_name, tcg_num, tcg_set, new_mkt, new_src, new_at, pc_url, now_iso(), card_id),
            )
            conn.commit()
            msg = (
                f"Updated from TCGplayer: ${chosen_price:,.2f}"
                if chosen_price is not None
                else "Saved TCGplayer link (could not auto-read Market Price — enter manually if needed)."
            )
            return {
                "success": chosen_price is not None,
                "price": new_mkt,
                "source_url": pc_url,
                "message": msg,
                "card": get_card(card_id),
            }

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            )
        }
        html = ""
        final_url = pc_url
        try:
            r = requests.get(pc_url, headers=headers, timeout=12, allow_redirects=True)
            if r.status_code == 200:
                html = r.text
                final_url = r.url
            elif r.status_code in (403, 429):
                final_url, html, _ = fetch_html_with_playwright(pc_url, visible=False, max_wait_s=12)
        except requests.RequestException:
            pass

        if not html:
            return {"success": False, "source_url": pc_url, "message": "Could not reach that PriceCharting link.", "card": get_card(card_id)}

        soup = BeautifulSoup(html, "html.parser")
        exact_prices = {}
        for tr in soup.find_all("tr"):
            cells = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
            if len(cells) == 2:
                key = cells[0].strip().lower()
                m_price = re.search(r"^\s*\$([\d,]+(?:\.\d{2})?)", cells[1])
                if m_price:
                    exact_prices[key] = float(m_price.group(1).replace(",", ""))

        g_low = (card["actual_grade"] or "").strip().lower()
        comp_low = (card["submission_company"] or "").strip().lower()
        priority_keys = ["ungraded"]
        if "black" in g_low and "10" in g_low:
            priority_keys = ["bgs 10 black", "bgs 10", "psa 10"]
        elif "pristine" in g_low and "10" in g_low:
            priority_keys = ["cgc 10 pristine", "cgc 10", "psa 10"] if "cgc" in comp_low else ["bgs 10", "cgc 10 pristine", "psa 10"]
        elif "10" in g_low:
            if "cgc" in comp_low:
                priority_keys = ["cgc 10", "psa 10"]
            elif "beckett" in comp_low or "bgs" in comp_low:
                priority_keys = ["bgs 10", "psa 10"]
            else:
                priority_keys = ["psa 10", "cgc 10", "bgs 10"]
        elif "9.5" in g_low:
            priority_keys = ["grade 9.5", "bgs 9.5", "cgc 9.5"]
        elif "9" in g_low:
            priority_keys = ["grade 9", "psa 9"]
        elif "8.5" in g_low or "8" in g_low:
            priority_keys = ["grade 8", "psa 8"]
        elif "7.5" in g_low or "7" in g_low:
            priority_keys = ["grade 7", "psa 7"]

        chosen_price = None
        matched_label = ""
        for pk in priority_keys:
            if pk in exact_prices:
                chosen_price = exact_prices[pk]
                matched_label = pk.upper()
                break

        pc_name = card["card_name"]
        pc_num = card["card_number"] or ""
        pc_set = card["set_name"] or ""
        title_tag = soup.find("title")
        if title_tag and "|" in title_tag.get_text():
            parts = [pt.strip() for pt in title_tag.get_text().split("|")]
            part0 = parts[0]
            m_num = re.search(r"#([A-Za-z0-9/\-]+)", part0)
            if m_num:
                pc_num = m_num.group(1).strip()
            clean_p0 = re.sub(r"\s*#[A-Za-z0-9/\-]+.*$", "", part0)
            clean_p0 = re.sub(r"\s+Prices\s*$", "", clean_p0, flags=re.I).strip()
            if clean_p0:
                pc_name = clean_p0
            if len(parts) >= 2 and "pokemon" in parts[1].lower():
                pc_set = re.sub(r"^Pokemon\s+", "", parts[1], flags=re.I).strip()

        new_mkt = round(chosen_price, 2) if chosen_price is not None else card["market_value"]
        new_src = "PriceCharting" if chosen_price is not None else card["market_value_source"]
        new_at = now_iso() if chosen_price is not None else card["market_value_updated_at"]

        conn.execute(
            """
            UPDATE cards
            SET card_name = ?, card_number = ?, set_name = ?,
                market_value = ?, market_value_source = ?, market_value_updated_at = ?, pricecharting_url = ?, updated_at = ?
            WHERE id = ?
            """,
            (pc_name, pc_num, pc_set, new_mkt, new_src, new_at, final_url, now_iso(), card_id),
        )
        conn.commit()
        msg = (
            f"Updated from PriceCharting ({matched_label}): ${chosen_price:,.2f}"
            if chosen_price is not None
            else "Updated card name, number & set from PriceCharting (grade price not listed)."
        )
        return {
            "success": True,
            "price": new_mkt,
            "source_url": final_url,
            "message": msg,
            "card": get_card(card_id),
        }
    finally:
        conn.close()


@app.get("/api/brand")
def get_brand_settings():
    conn = get_db()
    try:
        rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
        kv = {r["key"]: r["value"] for r in rows}
        return {
            "brand_name": kv.get("brand_name") or "TCG Grading Vault",
            "brand_subtitle": kv.get("brand_subtitle") or "100% Local Storage • TCG Slab & Profit Tracker",
            "brand_icon": kv.get("brand_icon") or "",
        }
    finally:
        conn.close()


@app.post("/api/brand")
def update_brand_settings(
    brand_name: str = Form("TCG Grading Vault"),
    brand_subtitle: str = Form("100% Local Storage • TCG Slab & Profit Tracker"),
    reset_icon: str = Form("false"),
    icon: Optional[UploadFile] = File(None),
):
    conn = get_db()
    try:
        cur_icon_row = conn.execute("SELECT value FROM app_settings WHERE key = 'brand_icon'").fetchone()
        cur_icon = cur_icon_row["value"] if cur_icon_row else ""

        if reset_icon.lower() == "true":
            delete_upload_if_local(cur_icon)
            cur_icon = ""
        elif icon and icon.filename:
            raw_bytes = icon.file.read()
            if raw_bytes:
                delete_upload_if_local(cur_icon)
                filename = f"brand_icon_{uuid.uuid4().hex[:8]}.webp"
                dest = BRAND_UPLOADS_DIR / filename
                try:
                    with Image.open(io.BytesIO(raw_bytes)) as im:
                        im = ImageOps.exif_transpose(im)
                        if im.mode not in ("RGB", "RGBA"):
                            im = im.convert("RGBA")
                        im.thumbnail((256, 256), Image.Resampling.LANCZOS)
                        im.save(dest, format="WEBP", quality=90)
                    cur_icon = f"brand/{filename}"
                except Exception:
                    ext = Path(icon.filename).suffix.lower() or ".png"
                    fb_name = f"brand_icon_{uuid.uuid4().hex[:8]}{ext}"
                    (BRAND_UPLOADS_DIR / fb_name).write_bytes(raw_bytes)
                    cur_icon = f"brand/{fb_name}"

        for k, v in [
            ("brand_name", brand_name.strip() or "TCG Grading Vault"),
            ("brand_subtitle", brand_subtitle.strip() or "100% Local Storage • TCG Slab & Profit Tracker"),
            ("brand_icon", cur_icon),
        ]:
            conn.execute(
                "INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (k, v),
            )
        conn.commit()
        return get_brand_settings()
    finally:
        conn.close()


KNOWN_FULL_SETS = [
    "Prismatic Evolutions", "Surging Sparks", "Stellar Crown", "Shrouded Fable",
    "Twilight Masquerade", "Temporal Forces", "Paldean Fates", "Paradox Rift",
    "Obsidian Flames", "Paldea Evolved", "Scarlet & Violet", "Journey Together",
    "Destined Rivals", "Perfect Order", "Crown Zenith", "Silver Tempest",
    "Lost Origin", "Astral Radiance", "Brilliant Stars", "Fusion Strike",
    "Evolving Skies", "Chilling Reign", "Battle Styles", "Sword & Shield",
    "Hidden Fates", "Shining Fates", "Celebrations", "Pokemon 151", "151",
    "Terastal Festival ex", "Terastal Festival", "VSTAR Universe", "Shiny Treasure ex",
    "Shiny Treasure", "VMAX Climax", "Eevee Heroes", "Base Set", "Jungle", "Fossil",
    "Team Rocket", "Neo Genesis", "Mega Evolution Promo", "Black Star Promo",
    "Ascended Heroes", "Phantasmal Flames", "30th Celebration",
    "Gem Pack Vol. 4", "Gem Pack Vol.4", "Gem Pack Volume 4", "Gem Pack Vol4",
    "Islands Await You", "Scarlet & Violet 151",
]


def parse_fanatics_csv_title(title: str) -> dict:
    t = re.sub(r"\s+", " ", (title or "")).strip()
    out = {"year": "", "language": "", "set_name": "", "card_name": t, "card_number": "", "grader": "", "grade": ""}
    if not t:
        return out

    m_yr = re.search(r"\b(199\d|20[0-3]\d)\b", t)
    if m_yr:
        out["year"] = m_yr.group(1)

    for lang in ("Japanese", "English", "Chinese", "Korean", "German", "French", "Spanish", "Italian", "Portuguese"):
        if re.search(rf"\b{lang}\b", t, re.I):
            out["language"] = lang
            break

    m_cn = re.search(r"#([A-Za-z0-9/\-]+)", t)
    if m_cn:
        out["card_number"] = m_cn.group(1)
    else:
        m_cn2 = re.search(r"\b(\d{1,3}/\d{1,3}|(?:TG|GG|SV|SWSH|SM|XY|BW)\d{1,4})\b", t, re.I)
        if m_cn2:
            out["card_number"] = m_cn2.group(1)

    m_g = re.search(r"\b(PSA|CGC|BGS|Beckett|TAG)\s*(Pristine\s*10|Black\s*Label\s*10|Gem\s*Mint\s*10|10|9\.5|9|8\.5|8|7\.5|7|6\.5|6|5\.5|5|4\.5|4|3\.5|3|2\.5|2|1\.5|1)\b", t, re.I)
    if m_g:
        g_comp = m_g.group(1).upper()
        out["grader"] = "Beckett" if g_comp in ("BGS", "BECKETT") else g_comp
        g_raw = m_g.group(2).strip()
        if "pristine" in g_raw.lower():
            out["grade"] = "Pristine 10"
        elif "black" in g_raw.lower():
            out["grade"] = "Black Label 10"
        else:
            m_n = re.search(r"(10|9\.5|9|8\.5|8|7\.5|7|6\.5|6|5\.5|5|4\.5|4|3\.5|3|2\.5|2|1\.5|1)", g_raw)
            out["grade"] = m_n.group(1) if m_n else g_raw

    out["set_name"] = expand_set_name("", t)
    if not out["set_name"]:
        t_low = t.lower()
        for full_set in sorted(set(KNOWN_FULL_SETS + list(SET_ABBREVIATIONS.values())), key=len, reverse=True):
            if full_set.lower() in t_low:
                out["set_name"] = full_set
                break

    clean = t
    if out["year"]:
        clean = re.sub(rf"^\s*{re.escape(out['year'])}\s+", "", clean)
    clean = re.sub(r"\b(?:Pokemon|TCG|Japanese|English|Chinese|Korean|German|French|Spanish|Italian|Portuguese)\b", "", clean, flags=re.I)
    clean = re.sub(r"\b(?:PSA|CGC|BGS|Beckett)\s*(?:Pristine|Black\s*Label|Gem\s*Mint)?\s*(?:10|9\.5|9|8\.5|8|7\.5|7|6\.5|6|5\.5|5|4\.5|4|3\.5|3|2\.5|2|1\.5|1)\b.*$", "", clean, flags=re.I)
    clean = re.sub(r"\s+", " ", clean).strip(" -|•")
    if out["language"] and out["language"].lower() != "english" and out["language"].lower() not in clean.lower():
        clean = f"{clean} ({out['language']})"
    if clean:
        out["card_name"] = clean
    return out


@app.post("/api/submissions/{sub_id}/import-fanatics-csv")
async def import_fanatics_csv(sub_id: int, file: UploadFile = File(...)):
    import csv as py_csv
    conn = get_db()
    try:
        sub = conn.execute("SELECT * FROM submissions WHERE id = ?", (sub_id,)).fetchone()
        if not sub:
            raise HTTPException(status_code=404, detail="Submission not found.")

        raw_bytes = await file.read()
        text = raw_bytes.decode("utf-8-sig", errors="replace")
        reader = py_csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            raise HTTPException(status_code=400, detail="CSV file appears empty.")

        existing_cards = [dict(r) for r in conn.execute("SELECT * FROM cards WHERE submission_id = ?", (sub_id,)).fetchall()]
        matched_ids = set()
        updated_count = 0
        created_count = 0
        total_rows = 0

        for raw_row in reader:
            row = {re.sub(r"[^a-z0-9]", "", (k or "").lower()): (v or "").strip() for k, v in raw_row.items() if k is not None}
            title = row.get("title", "") or row.get("card", "") or row.get("name", "") or row.get("cardname", "")
            grade_col = row.get("grade", "") or row.get("finalgrade", "")
            cert_raw = (
                row.get("certnumber", "") or row.get("certno", "") or row.get("cert", "")
                or row.get("certificatenumber", "") or row.get("certificationnumber", "") or row.get("certid", "")
            )
            cert_col = extract_cert_number(cert_raw)
            curated_col = row.get("curateddate", "") or row.get("datecurated", "") or row.get("curated", "")
            mkt_raw = row.get("marketvalue", "") or row.get("estimatedvalue", "") or row.get("value", "")
            link_col = row.get("url", "") or row.get("link", "") or row.get("itemurl", "") or row.get("listingurl", "")
            if not any([title, grade_col, cert_col, curated_col, mkt_raw]):
                continue
            total_rows += 1

            parsed = parse_fanatics_csv_title(title)
            final_grade = grade_col or parsed["grade"]
            if "pristine" in title.lower() and "10" in (final_grade or title):
                final_grade = "Pristine 10"
            elif "black label" in title.lower() and "10" in (final_grade or title):
                final_grade = "Black Label 10"
            elif final_grade and "pristine" in final_grade.lower() and "10" in final_grade:
                final_grade = "Pristine 10"
            elif final_grade:
                mg = re.search(r"(10|9\.5|9|8\.5|8|7\.5|7|6\.5|6|5\.5|5|4\.5|4|3\.5|3|2\.5|2|1\.5|1)", final_grade)
                if mg and "black" not in final_grade.lower():
                    final_grade = mg.group(1)

            mkt_val = None
            if mkt_raw:
                cleaned_m = re.sub(r"[^0-9.]", "", mkt_raw)
                if cleaned_m:
                    try:
                        mkt_val = round(float(cleaned_m), 2)
                    except ValueError:
                        mkt_val = None

            curated_norm = curated_col
            if curated_col:
                for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d", "%b %d, %Y"):
                    try:
                        curated_norm = datetime.strptime(curated_col.strip(), fmt).strftime("%Y-%m-%d")
                        break
                    except ValueError:
                        pass

            best_card = None
            best_score = -1
            row_tokens = set(re.findall(r"[a-z0-9]{3,}", title.lower()))
            for c in existing_cards:
                if c["id"] in matched_ids:
                    continue
                if cert_col and (c.get("cert_number") or "").strip() == cert_col:
                    best_card = c
                    best_score = 1000
                    break
                sc = 0
                c_num = (c.get("card_number") or "").split("/")[0].lstrip("0").lower()
                p_num = (parsed["card_number"] or "").split("/")[0].lstrip("0").lower()
                if c_num and p_num and c_num == p_num:
                    sc += 40
                c_tokens = set(re.findall(r"[a-z0-9]{3,}", f"{c.get('card_name','')} {c.get('set_name','')}".lower()))
                sc += len(row_tokens & c_tokens) * 15
                c_name_low = (c.get("card_name") or "").strip().lower()
                if len(c_name_low) >= 4 and c_name_low in title.lower():
                    sc += 25
                if cert_col and not (c.get("cert_number") or "").strip():
                    sc += 15
                if sc > best_score and sc >= 20:
                    best_score = sc
                    best_card = c

            company = parsed["grader"] or sub["company"]
            cert_url = build_cert_url(company, cert_col) if cert_col else ""

            if best_card:
                matched_ids.add(best_card["id"])
                new_status = resolve_card_status(
                    best_card["status"], best_card["sold_price"], cert_col or best_card["cert_number"], final_grade or best_card["actual_grade"], best_card["fanatics_url"]
                )
                conn.execute(
                    """
                    UPDATE cards
                    SET card_name = ?, card_year = ?, set_name = ?, card_number = ?,
                        actual_grade = ?, cert_number = ?, cert_lookup_url = ?,
                        fanatics_url = COALESCE(NULLIF(?, ''), fanatics_url),
                        curated_date = ?, market_value = COALESCE(?, market_value),
                        market_value_source = CASE WHEN ? IS NOT NULL THEN 'Fanatics CSV' ELSE market_value_source END,
                        market_value_updated_at = CASE WHEN ? IS NOT NULL THEN ? ELSE market_value_updated_at END,
                        status = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        parsed["card_name"] or best_card["card_name"],
                        parsed["year"] or best_card["card_year"] or "",
                        parsed["set_name"] or best_card["set_name"] or "",
                        parsed["card_number"] or best_card["card_number"] or "",
                        final_grade or best_card["actual_grade"] or "",
                        cert_col or best_card["cert_number"] or "",
                        cert_url or best_card["cert_lookup_url"] or "",
                        link_col,
                        curated_norm or best_card.get("curated_date") or "",
                        mkt_val,
                        mkt_val,
                        mkt_val,
                        now_iso(),
                        new_status,
                        now_iso(),
                        best_card["id"],
                    ),
                )
                updated_count += 1
            else:
                ts = now_iso()
                new_status = resolve_card_status("In Grading", None, cert_col, final_grade, "")
                conn.execute(
                    """
                    INSERT INTO cards (
                        submission_id, card_name, card_year, set_name, card_number,
                        cert_number, cert_lookup_url, fanatics_url, actual_grade, curated_date,
                        market_value, market_value_source, market_value_updated_at,
                        status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        sub_id,
                        parsed["card_name"] or title or "Imported Card",
                        parsed["year"],
                        parsed["set_name"],
                        parsed["card_number"],
                        cert_col,
                        cert_url,
                        link_col,
                        final_grade,
                        curated_norm,
                        mkt_val,
                        "Fanatics CSV" if mkt_val is not None else "",
                        ts if mkt_val is not None else "",
                        new_status,
                        ts,
                        ts,
                    ),
                )
                created_count += 1

        conn.commit()
        return {
            "updated_count": updated_count,
            "created_count": created_count,
            "total_rows": total_rows,
            "message": f"Fanatics CSV imported: {updated_count} cards updated, {created_count} added.",
        }
    finally:
        conn.close()


def _extract_text_from_any_doc(raw_bytes: bytes, filename: str) -> str:
    ext = Path(filename or "").suffix.lower()
    if ext == ".pdf":
        pdf_text = ""
        had_reader = False
        try:
            from pypdf import PdfReader
            had_reader = True
            reader = PdfReader(io.BytesIO(raw_bytes))
            pdf_text = "\n".join((page.extract_text() or "") for page in reader.pages)
        except ImportError:
            pass
        except Exception:
            pass
        if not pdf_text.strip():
            try:
                import pdfplumber
                had_reader = True
                with pdfplumber.open(io.BytesIO(raw_bytes)) as pdf:
                    pdf_text = "\n".join((p.extract_text() or "") for p in pdf.pages)
            except ImportError:
                pass
            except Exception:
                pass
        if not had_reader:
            raise HTTPException(status_code=400, detail="PDF support requires pypdf or pdfplumber. Run: pip install pypdf pdfplumber")
        if not pdf_text.strip():
            raise HTTPException(status_code=400, detail="No readable text found in that PDF (it may be a scanned image PDF).")
        return pdf_text
    if ext in (".xlsx", ".xlsm") and openpyxl is not None:
        wb = openpyxl.load_workbook(io.BytesIO(raw_bytes), data_only=True)
        lines = []
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                vals = [str(c).strip() for c in row if c is not None and str(c).strip()]
                if vals:
                    lines.append(" | ".join(vals))
        return "\n".join(lines)
    if ext == ".docx":
        try:
            import zipfile as _zf, xml.etree.ElementTree as _ET
            with _zf.ZipFile(io.BytesIO(raw_bytes)) as z:
                xml_bytes = z.read("word/document.xml")
            root = _ET.fromstring(xml_bytes)
            return "\n".join(el.text.strip() for el in root.iter() if el.text and el.text.strip())
        except Exception:
            pass
    if ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"):
        try:
            import pytesseract
            im = ImageOps.exif_transpose(Image.open(io.BytesIO(raw_bytes)))
            im = ImageOps.grayscale(im)
            return pytesseract.image_to_string(im) or ""
        except Exception:
            return ""
    return raw_bytes.decode("utf-8-sig", errors="ignore")


def _normalize_psa_grade_text(grade_str: str) -> str:
    g = (grade_str or "").strip()
    if not g:
        return ""
    m = re.search(r"(\d+(?:\.\d+)?)", g)
    return m.group(1) if m else g


def _parse_card_description_line(desc: str) -> dict:
    raw = re.sub(r"\s+", " ", (desc or "").strip())
    raw = re.sub(r"^\$?\d+\.\d{2}\s+", "", raw).strip()
    grade = ""
    m_gr = re.search(r"[-–]\s*(PSA|BGS|CGC|TAG)\s+(\d+(?:\.\d+)?(?:\s+PRISTINE|\s+BLACK\s+LABEL)?)\s*$", raw, re.I)
    if m_gr:
        grade = m_gr.group(2).strip()
        raw = raw[:m_gr.start()].strip()
    year = ""
    m_yr = re.match(r"^(19\d{2}|20\d{2})\s+(.+)$", raw)
    if m_yr:
        year = m_yr.group(1)
        raw = m_yr.group(2).strip()
    card_num = ""
    set_name = ""
    card_name = raw
    # Split around card number token (e.g., "#160 Pikachu SEC" or "038 CHARMANDER FIRST PARTNER...")
    m_split = re.search(r"^(.*?)\s+(?:#)?(\d{1,4}(?:/\d{1,4})?)\s+(.+)$", raw)
    if m_split:
        prefix, card_num, suffix = m_split.group(1).strip(), m_split.group(2).strip(), m_split.group(3).strip()
        set_name = re.sub(r"^(?:POKEMON|ONE PIECE|LORCANA|MTG|DRAGON BALL)\s+(?:TCG\s+)?", "", prefix, flags=re.I).strip()
        # Clean GameStop trailing collection noise if all-caps
        clean_suf = re.sub(r"\b(?:FIRST PARTNER ILLUSTRATION COLLECTION SERIES \d+|ILLUSTRATION RARE|SPECIAL ILLUSTRATION RARE|CELEBRATIONS ELITE TRAINER BOX|FULL ART/)\b", "", suffix, flags=re.I).strip(" -/")
        if clean_suf:
            card_name = clean_suf.title() if suffix.isupper() else clean_suf
        else:
            card_name = suffix.title() if suffix.isupper() else suffix
    return {"card_name": card_name, "card_year": year, "set_name": set_name, "card_number": card_num, "actual_grade": grade}


def _parse_psa_submission_form_pdf(text: str, raw_bytes: bytes = b"") -> dict | None:
    """
    Parses a mailed-in PSA Submission Form PDF (e.g., psasub_copy.pdf).
    - Deduplicates PSA Copy #1 vs PSA Copy #2 / Customer Copy.
    - Extracts Submission #, Service Level / Grading Fee, and Insured Return Shipping.
    - Handles optional SPEC # and "TCG CARDS" columns between LN#/QTY and YEAR/DESCRIPTION.
    - Extracts clean Pokemon card_name and card_number (when present), expanding Qty > 1.
    """
    low_all = text.lower()
    is_psa_sub = (
        ("psa copy" in low_all or "psa submission form" in low_all or "submission" in low_all)
        and any(k in low_all for k in ("declared value", "max ins. value", "service level fee", "grading fee", "insured return shipping", "return shipping", "psa copy #"))
    )
    if not is_psa_sub:
        return None

    # Truncate ONLY at explicit Copy #2 / Customer Copy (after Copy #1)
    lines_raw = [ln.strip() for ln in text.splitlines() if ln.strip()]
    dedup_lines: list[str] = []
    seen_copy1 = False
    for ln in lines_raw:
        low_ln = ln.lower()
        if re.search(r"\bpsa\s*copy\s*#?\s*1\b", low_ln):
            seen_copy1 = True
        if re.search(r"\bpsa\s*copy\s*#?\s*2\b|\bcopy\s*#\s*2\b", low_ln):
            break
        if seen_copy1 and re.search(r"\bcustomer\s*copy\b", low_ln):
            break
        dedup_lines.append(ln)

    sub_num = ""
    total_grading = 0.0
    total_shipping = 0.0
    for i, ln in enumerate(dedup_lines):
        low_ln = ln.lower()
        if not sub_num:
            m_sub = re.search(r"submission\s*(?:#|no\.?|number)?\s*[:#]?\s*(\d{6,12})", ln, re.I)
            if m_sub:
                sub_num = m_sub.group(1)
            elif low_ln in ("submission #", "submission#") and i + 1 < len(dedup_lines) and re.fullmatch(r"\d{6,12}", dedup_lines[i + 1]):
                sub_num = dedup_lines[i + 1]
        m_money = re.findall(r"\$\s*(\d+(?:,\d{3})*(?:\.\d{2})?)", ln)
        if not m_money:
            continue
        amt = float(m_money[-1].replace(",", ""))
        if any(k in low_ln for k in ("service level fee", "grading fee", "grading fees", "total grading", "service fee", "grading subtotal")):
            if amt > total_grading:
                total_grading = amt
        elif any(k in low_ln for k in ("insured return shipping", "return shipping", "shipping & handling", "shipping and handling", "shipping fee", "shipping cost", "postage", "shipping")):
            if "free" not in low_ln and "carrier" not in low_ln and amt > total_shipping:
                total_shipping = amt

    def _clean_psa_row_to_cards(desc_text: str, qty: int = 1) -> list[dict]:
        # Strip trailing "$1" / "$1.00" max ins / declared value
        clean_desc = re.sub(r"\s+\$\s*\d+(?:,\d{3})*(?:\.\d{2})?\s*$", "", desc_text).strip()
        # Fix PDF wrap concatenation like "024ALOMOMOLA" or "FLARE065" -> "024 ALOMOMOLA", "FLARE 065"
        clean_desc = re.sub(r"(\d{2,4})([A-Z]{2,})", r"\1 \2", clean_desc)
        clean_desc = re.sub(r"([A-Z]{3,})(\d{2,4})\b", r"\1 \2", clean_desc)
        clean_desc = re.sub(r"\s+", " ", clean_desc).strip()
        if len(clean_desc) < 5:
            return []

        # Extract year
        c_yr = ""
        m_yr = re.match(r"^(19\d{2}|20\d{2})\s+(.+)$", clean_desc)
        body = clean_desc
        if m_yr:
            c_yr = m_yr.group(1)
            body = m_yr.group(2).strip()

        c_num = ""
        c_name = body
        # Match card number token (e.g., "036", "GG05", "GG30", "TG01", "SV012", "123/198") followed by Pokemon name
        m_split = re.search(r"^(.*?)\s+(?:#)?((?:GG|TG|SV|SWSH|SM|XY|BW)?\d{1,4}(?:/\d{1,4})?)\s+(.+)$", body, re.I)
        if m_split:
            c_num = m_split.group(2).strip().upper()
            raw_after = m_split.group(3).strip()
            # Clean "FULL ART/LAPRAS" -> "Lapras", and GameStop/box noise
            raw_after = re.sub(r"^FULL\s+ART/", "", raw_after, flags=re.I).strip()
            raw_after = re.sub(r"\b(?:FIRST PARTNER ILLUSTRATION COLLECTION SERIES \d+|TECH STICKER COLLECTION|MEGA KANGASKHAN EX BOX|TEAM ROCKET'S MEWTWO EX BOX|CROWN ZENITH TIN)\b", "", raw_after, flags=re.I).strip(" -/")
            c_name = raw_after.title() if raw_after.isupper() else raw_after
        else:
            # No card number (e.g., "POKEMON MY FIRST BATTLE-BULBASAUR DECK BASIC GRASS ENERGY")
            m_deck = re.search(r"\bDECK\s+(.+)$", body, re.I)
            if m_deck:
                nm = m_deck.group(1).strip()
                c_name = nm.title() if nm.isupper() else nm
            else:
                parsed = _parse_card_description_line(clean_desc)
                c_name = parsed["card_name"] or body
                c_num = parsed["card_number"]

        out = []
        for _ in range(max(1, min(50, qty))):
            out.append({
                "card_name": c_name,
                "card_year": c_yr,
                "set_name": "",
                "card_number": c_num,
                "actual_grade": "",
                "cert_number": "",
                "psa_images_zip_url": "",
                "market_value": None,
                "curated_date": "",
            })
        return out

    # Parse text rows: "<LN#> <QTY> [<SPEC#>] [TCG CARDS] <YEAR> <DESCRIPTION...>"
    # Multi-line descriptions wrap onto next lines until "$<num>" or next "<LN#> <QTY> ..."
    skip_prefixes = (
        "psa submission", "psa copy", "psa item list", "customer #", "submission #",
        "form expires", "ln#", "grand total", "i have read", "form. if items",
        "authentication, grading", "system.", "return shipping", "payment details",
        "order details", "submission summary", "psa use only",
    )
    row_start_re = re.compile(
        r"^(\d{1,3})\s+(\d{1,2})\s+(?:[A-Z0-9]{6,14}\s+)?(?:TCG\s+CARDS\s+)?((?:19|20)\d{2}\b.*)$",
        re.I,
    )
    merged_rows: list[tuple[int, int, str]] = []
    cur_ln = 0
    cur_qty = 1
    cur_buf: list[str] = []
    seen_ln_nums: set[int] = set()

    for ln in dedup_lines:
        low_ln = ln.lower()
        if any(low_ln.startswith(sp) for sp in skip_prefixes):
            if cur_buf:
                merged_rows.append((cur_ln, cur_qty, " ".join(cur_buf)))
                cur_buf = []
            continue
        m_row = row_start_re.match(ln)
        if m_row:
            ln_num = int(m_row.group(1))
            if ln_num == 1 and len(seen_ln_nums) >= 3:
                break
            seen_ln_nums.add(ln_num)
            if cur_buf:
                merged_rows.append((cur_ln, cur_qty, " ".join(cur_buf)))
            cur_ln = ln_num
            cur_qty = int(m_row.group(2))
            cur_buf = [m_row.group(3).strip()]
            if re.search(r"\$\s*\d+(?:,\d{3})*(?:\.\d{2})?\s*$", ln):
                merged_rows.append((cur_ln, cur_qty, " ".join(cur_buf)))
                cur_buf = []
        elif cur_buf:
            cur_buf.append(ln)
            if re.search(r"\$\s*\d+(?:,\d{3})*(?:\.\d{2})?\s*$", ln):
                merged_rows.append((cur_ln, cur_qty, " ".join(cur_buf)))
                cur_buf = []

    if cur_buf:
        merged_rows.append((cur_ln, cur_qty, " ".join(cur_buf)))

    cards_out: list[dict] = []
    for _, qty_v, row_txt in merged_rows:
        cards_out.extend(_clean_psa_row_to_cards(row_txt, qty_v))

    if not cards_out:
        return None
    return {
        "format_detected": "PSA Submission Form PDF",
        "cards": cards_out,
        "has_rich_data": True,
        "submission_number": sub_num,
        "total_grading_fee": round(total_grading, 2),
        "total_shipping_cost": round(total_shipping, 2),
    }


def parse_smart_submission_doc(raw_bytes: bytes, filename: str, company: str) -> dict:
    text = _extract_text_from_any_doc(raw_bytes, filename)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    # 0. Guard against eBay Paid & Shipped CSVs
    if any("Order Number" in ln and "Item Title" in ln and "Sold For" in ln for ln in lines[:6]):
        raise HTTPException(
            status_code=400,
            detail="This is an eBay Paid & Shipped CSV — please use the '🧾 eBay Sales' button in the top bar instead.",
        )

    # 0b. Check for Fanatics Collect CSV ("Title" and "CertNumber")
    if lines and "title" in lines[0].lower() and "certnumber" in lines[0].lower():
        reader = csv.DictReader(io.StringIO(text))
        cards_out = []
        for row in reader:
            cert = extract_cert_number(row.get("CertNumber") or "")
            title = (row.get("Title") or "").strip()
            grade_raw = (row.get("Grade") or "").strip()
            m_gr = re.search(r"(\d+(?:\.\d+)?)", grade_raw)
            grade = m_gr.group(1) if m_gr else grade_raw
            cur_date = (row.get("Curated Date") or "").strip()
            mv_raw = (row.get("Market Value") or "").strip()
            mv = parse_opt_float(mv_raw)
            if not cert and not title:
                continue
            parsed = _parse_card_description_line(title)
            cards_out.append({
                "card_name": parsed["card_name"] or title or f"{company} Cert #{cert}",
                "card_year": parsed["card_year"],
                "set_name": "",
                "card_number": parsed["card_number"],
                "actual_grade": grade or parsed["actual_grade"],
                "cert_number": cert,
                "psa_images_zip_url": "",
                "market_value": mv,
                "curated_date": cur_date,
            })
        if cards_out:
            return {"format_detected": "Fanatics Collect CSV", "cards": cards_out, "has_rich_data": True}

    # 1. Check for PSA Order CSV ("Cert #" and "Description")
    if lines and ("cert #" in lines[0].lower() or "cert#" in lines[0].lower()) and "description" in lines[0].lower():
        reader = csv.DictReader(io.StringIO(text))
        cards_out = []
        for row in reader:
            cert = extract_cert_number(row.get("Cert #") or row.get("Cert#") or "")
            desc = (row.get("Description") or "").strip()
            grade = _normalize_psa_grade_text(row.get("Grade") or "")
            img_zip = (row.get("Images") or "").strip()
            if not cert and not desc:
                continue
            parsed = _parse_card_description_line(desc)
            cards_out.append({
                "card_name": parsed["card_name"] or f"{company} Cert #{cert}",
                "card_year": parsed["card_year"],
                "set_name": "",
                "card_number": parsed["card_number"],
                "actual_grade": grade or parsed["actual_grade"],
                "cert_number": cert,
                "psa_images_zip_url": img_zip,
                "market_value": None,
                "curated_date": "",
            })
        if cards_out:
            return {"format_detected": "PSA Order CSV", "cards": cards_out, "has_rich_data": True}

    # 1a. Check for PSA Submission Form PDF (mailed-in submission with Copy #1/#2 + fees)
    if Path(filename or "").suffix.lower() == ".pdf":
        psa_form = _parse_psa_submission_form_pdf(text, raw_bytes)
        if psa_form:
            return psa_form

    # 1b. Check for Beckett (BGS) single-line OR multi-line PDF Card Listing
    bgs_cards = []
    # First: single-line tab/space-separated ("20644075\t2023 Pokemon ...\t9.5")
    for ln in lines:
        m_bgs = re.match(r"^(\d{6,12})[\t ]+(.{10,}?)[\t ]+(\d+(?:\.\d+)?(?:\s*(?:BLACK|PRISTINE))?)\s*$", ln, re.I)
        if m_bgs:
            cert_b, title_b, grade_b = m_bgs.group(1).strip(), m_bgs.group(2).strip(), m_bgs.group(3).strip()
            parsed = _parse_card_description_line(title_b)
            bgs_cards.append({
                "card_name": parsed["card_name"] or title_b,
                "card_year": parsed["card_year"],
                "set_name": "",
                "card_number": parsed["card_number"],
                "actual_grade": grade_b or parsed["actual_grade"],
                "cert_number": cert_b,
                "psa_images_zip_url": "",
                "market_value": None,
                "curated_date": "",
            })
    if bgs_cards:
        return {"format_detected": "Beckett (BGS) Card Listing", "cards": bgs_cards, "has_rich_data": True}

    # Second: multi-line Beckett PDF ("Card Listing" -> "Graded Serial No. Title Final Grade Actions")
    if any("graded serial no" in ln.lower() for ln in lines):
        in_listing = False
        cur_cert = ""
        cur_desc_parts = []
        for ln in lines:
            low = ln.lower().strip()
            if "graded serial no" in low:
                in_listing = True
                continue
            if not in_listing:
                continue
            if low.startswith(("submit", "logout", "beckett.com", "yes, sign", "sign up")):
                break
            if low in ("view pop report", "actions"):
                continue
            m_start = re.match(r"^(\d{6,12})\s+(.+)$", ln)
            if m_start and not cur_cert:
                cur_cert = m_start.group(1).strip()
                cur_desc_parts = [m_start.group(2).strip()]
                continue
            if cur_cert:
                if re.fullmatch(r"\d+(?:\.\d+)?", ln.strip()):
                    desc_full = " ".join(cur_desc_parts).strip()
                    parsed = _parse_card_description_line(desc_full)
                    bgs_cards.append({
                        "card_name": parsed["card_name"] or desc_full,
                        "card_year": parsed["card_year"],
                        "set_name": "",
                        "card_number": parsed["card_number"],
                        "actual_grade": ln.strip(),
                        "cert_number": cur_cert,
                        "psa_images_zip_url": "",
                        "market_value": None,
                        "curated_date": "",
                    })
                    cur_cert = ""
                    cur_desc_parts = []
                else:
                    cur_desc_parts.append(ln.strip())
        if bgs_cards:
            return {"format_detected": "Beckett (BGS) PDF Order", "cards": bgs_cards, "has_rich_data": True}

    # 2. Check for GameStop pasted text or printed PDF (multi-line Card Name followed by "<cert>" or "<cert> *Up to...")
    gamestop_cards = []
    buf_lines = []
    stop_prefixes = ("card name", "cert.", "cert #", "trade in", "*up to", "*trade estimates", "graded cards", "order:", "order status", "order date", "hi,", "dashboard", "store:", "approved for", "cookie", "sign up")
    for ln in lines:
        low_ln = ln.lower().strip()
        m_gs = re.match(r"^(\d{7,12})(?:\s*\*Up\s*to.*)?$", ln, re.I)
        if m_gs:
            cert_g = m_gs.group(1)
            desc = " ".join(buf_lines).strip()
            buf_lines = []
            if len(desc) > 10:
                parsed = _parse_card_description_line(desc)
                gamestop_cards.append({
                    "card_name": parsed["card_name"],
                    "card_year": parsed["card_year"],
                    "set_name": "",
                    "card_number": parsed["card_number"],
                    "actual_grade": parsed["actual_grade"],
                    "cert_number": cert_g,
                    "psa_images_zip_url": "",
                    "market_value": None,
                    "curated_date": "",
                })
        elif any(low_ln.startswith(p) for p in stop_prefixes) or re.fullmatch(r"\$?\d+\.\d{2}", ln.strip()):
            if any(low_ln.startswith(p) for p in stop_prefixes):
                buf_lines = []
        else:
            buf_lines.append(ln)
            if len(buf_lines) > 6:
                buf_lines = buf_lines[-6:]
    if gamestop_cards:
        return {"format_detected": "GameStop / BGS Text or PDF", "cards": gamestop_cards, "has_rich_data": True}

    # 3. Fallback: Cert-Only List (.txt, .csv, .xlsx, .docx, .pdf)
    certs = extract_certs_from_doc(raw_bytes, filename)
    cards_out = [{
        "card_name": f"{company} Cert #{c}",
        "card_year": "",
        "set_name": "",
        "card_number": "",
        "actual_grade": "",
        "cert_number": c,
        "psa_images_zip_url": "",
        "market_value": None,
        "curated_date": "",
    } for c in certs]
    return {"format_detected": "Cert # List", "cards": cards_out, "has_rich_data": False}


@app.post("/api/submissions/smart-import")
async def smart_import_submission(
    file: UploadFile = File(...),
    title: str = Form(""),
    company: str = Form(""),
    submitted_date: str = Form(""),
    not_shipped: str = Form(""),
    submit_channel: str = Form(""),
):
    if company not in ("PSA", "CGC", "Beckett", "TAG"):
        raise HTTPException(status_code=400, detail="Please select a grading company (PSA, CGC, Beckett, or TAG) first.")
    raw_bytes = await file.read()
    parsed = parse_smart_submission_doc(raw_bytes, file.filename or "import.txt", company)
    cards_data = parsed["cards"]
    if not cards_data:
        raise HTTPException(status_code=400, detail="No cards or cert numbers found in that file.")

    sub_title = title.strip() or Path(file.filename or f"{company} Import").stem.replace("_", " ").strip()
    sub_date = submitted_date.strip() or datetime.now().strftime("%Y-%m-%d")
    sub_status = "Not Shipped" if not_shipped.strip().lower() in ("true", "1", "yes") else "Completed"
    if parsed["format_detected"] == "PSA Submission Form PDF" and sub_status == "Completed" and not_shipped.strip() == "":
        sub_status = "In Grading"
    sub_num = (parsed.get("submission_number") or "").strip()
    n_cards = max(1, len(cards_data))
    per_card_grading = round(float(parsed.get("total_grading_fee") or 0.0) / n_cards, 2)
    per_card_shipping = round(float(parsed.get("total_shipping_cost") or 0.0) / n_cards, 2)
    ts = now_iso()

    conn = get_db()
    created_cards = []
    try:
        cur = conn.execute(
            """
            INSERT INTO submissions (title, company, submission_number, submitted_date, status, submit_channel, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (sub_title, company, sub_num, sub_date, sub_status, submit_channel.strip(), f"Imported via {parsed['format_detected']}", ts, ts),
        )
        sub_id = cur.lastrowid
        for c in cards_data:
            cert_clean = extract_cert_number(c["cert_number"]) or c["cert_number"]
            c_url = build_cert_url(company, cert_clean) if cert_clean else ""
            c_status = "Not Submitted" if sub_status == "Not Shipped" else ("In Grading" if sub_status == "In Grading" else "Unsold")
            c_cur = conn.execute(
                """
                INSERT INTO cards (
                    submission_id, card_name, card_year, set_name, card_number,
                    cert_number, cert_lookup_url, actual_grade, psa_images_zip_url,
                    grading_cost, shipping_cost,
                    market_value, curated_date, status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sub_id,
                    c["card_name"],
                    c["card_year"],
                    c["set_name"],
                    c["card_number"],
                    cert_clean,
                    c_url,
                    c["actual_grade"],
                    c["psa_images_zip_url"],
                    per_card_grading,
                    per_card_shipping,
                    c.get("market_value"),
                    c.get("curated_date") or "",
                    c_status,
                    ts,
                    ts,
                ),
            )
            created_cards.append({
                "id": c_cur.lastrowid,
                "cert_number": cert_clean,
                "psa_images_zip_url": c["psa_images_zip_url"],
            })
        conn.commit()
        return {
            "submission_id": sub_id,
            "format_detected": parsed["format_detected"],
            "has_rich_data": parsed["has_rich_data"],
            "cards": created_cards,
            "total": len(created_cards),
        }
    finally:
        conn.close()


def _parse_money_str(val: str) -> float:
    s = re.sub(r"[^\d.\-]", "", (val or "").strip())
    try:
        return round(float(s), 2) if s else 0.0
    except ValueError:
        return 0.0


def _normalize_ebay_date(val: str) -> str:
    s = (val or "").strip()
    if not s:
        return ""
    for fmt in ("%b-%d-%y", "%b-%d-%Y", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return s


def parse_ebay_paid_shipped_csv(raw_bytes: bytes) -> list[dict]:
    text = raw_bytes.decode("utf-8-sig", errors="ignore")
    lines = text.splitlines()
    # Find actual header line containing "Order Number" and "Item Title"
    hdr_idx = -1
    for i, ln in enumerate(lines):
        if "Order Number" in ln and "Item Title" in ln and "Sold For" in ln:
            hdr_idx = i
            break
    if hdr_idx == -1:
        return []
    reader = csv.DictReader(io.StringIO("\n".join(lines[hdr_idx:])))
    items = []
    for row in reader:
        item_num = (row.get("Item Number") or "").strip()
        title = (row.get("Item Title") or "").strip()
        order_num = (row.get("Order Number") or "").strip()
        # Skip blank lines, footer lines, and combined-order summary rows (where Item Number is blank)
        if not item_num or not title or not order_num:
            continue
        sold_for = _parse_money_str(row.get("Sold For") or "")
        ship_chg = _parse_money_str(row.get("Shipping And Handling") or "")
        ebay_tax = _parse_money_str(row.get("eBay Collected Tax") or "")
        sale_date = _normalize_ebay_date(row.get("Sale Date") or row.get("Paid On Date") or "")
        qty = int(float(row.get("Quantity") or 1) or 1)
        m_cert = re.search(r"\b(?:PSA|CGC|BGS|TAG)\s*(?:#)?\s*(\d{6,12})\b", title, re.I)
        items.append({
            "order_number": order_num,
            "item_number": item_num,
            "item_title": title,
            "sold_for": sold_for,
            "shipping_collected": ship_chg,
            "ebay_tax": ebay_tax,
            "sale_date": sale_date,
            "quantity": qty,
            "detected_cert": m_cert.group(1) if m_cert else "",
        })
    return items


@app.post("/api/ebay/import-sales")
async def import_ebay_sales(file: UploadFile = File(...), preview_only: str = Form("true")):
    raw_bytes = await file.read()
    items = parse_ebay_paid_shipped_csv(raw_bytes)
    if not items:
        raise HTTPException(status_code=400, detail="No valid eBay item sales found in that CSV.")

    conn = get_db()
    matched_details = []
    unmatched = []
    try:
        all_cards = [
            dict(r)
            for r in conn.execute(
                "SELECT c.*, s.title AS submission_title, s.company AS submission_company FROM cards c JOIN submissions s ON s.id = c.submission_id WHERE c.status != 'Sold'"
            ).fetchall()
        ]
        _STOP_TOKENS = {
            "pokemon", "pokémon", "one", "piece", "dragon", "ball", "magic", "lorcana",
            "yugioh", "yu-gi-oh", "card", "cards", "tcg", "promo", "black", "star",
            "japanese", "english", "holo", "reverse", "foil", "vmax", "vstar", "gx", "ex",
            "the", "and", "for", "with", "from", "first", "battle", "deck", "series",
            "collection", "illustration", "edition", "set", "box", "pack", "etb", "tin",
            "alt", "art", "full", "secret", "rare", "ultra", "hyper", "shiny", "dark",
            "mega", "pop", "gem", "mint", "psa", "cgc", "bgs", "tag", "graded", "slab",
        }

        def _card_cost(c: dict) -> float:
            return round(
                float(c.get("acquisition_cost") or 0)
                + float(c.get("grading_cost") or 0)
                + float(c.get("shipping_cost") or 0),
                2,
            )

        def _card_img(c: dict) -> str:
            return (c.get("cert_front_image") or c.get("raw_front_image") or "").strip()

        def _extract_numeric_grade(g_str: str) -> str:
            s = (g_str or "").strip()
            if not s:
                return ""
            m = re.search(r"\b(10|9\.5|9|8\.5|8|7\.5|7|6\.5|6|5\.5|5|4\.5|4|3\.5|3|2\.5|2|1\.5|1)\b", s)
            return m.group(1) if m else ""

        def _parse_ebay_title_meta(title_str: str) -> dict:
            t_low = (title_str or "").lower()
            comp = ""
            m_comp = re.search(r"\b(psa|cgc|bgs|beckett|tag)\b", t_low)
            if m_comp:
                c_raw = m_comp.group(1).upper()
                comp = "Beckett" if c_raw in ("BGS", "BECKETT") else c_raw
            grade = ""
            m_gr = re.search(
                r"\b(?:psa|cgc|bgs|beckett|tag)\s*(?:gem\s*mt|gem\s*mint|pristine|black\s*label|mint|nm-mt|nm)?\s*(10|9\.5|9|8\.5|8|7\.5|7|6\.5|6|5\.5|5|4\.5|4|3\.5|3|2\.5|2|1\.5|1)\b",
                t_low,
            )
            if m_gr:
                grade = m_gr.group(1)
            yr = ""
            m_yr = re.search(r"\b(199\d|20[0-3]\d)\b", t_low)
            if m_yr:
                yr = m_yr.group(1)
            # Clean title for card-number and token matching so "PSA 10", "POP 8", and year never match card #
            clean_t = re.sub(
                r"\b(?:psa|cgc|bgs|beckett|tag)\s*(?:gem\s*mt|gem\s*mint|pristine|black\s*label|mint|nm-mt|nm)?\s*(?:10|9\.5|9|8\.5|8|7\.5|7|6\.5|6|5\.5|5|4\.5|4|3\.5|3|2\.5|2|1\.5|1)?\b",
                " ",
                t_low,
            )
            clean_t = re.sub(r"\|\s*pop\s*\d+.*$", " ", clean_t)
            clean_t = re.sub(r"\bpop\s*\d+\b", " ", clean_t)
            if yr:
                clean_t = re.sub(rf"\b{re.escape(yr)}\b", " ", clean_t)
            lang = "english"
            if re.search(r"\b(japanese|jpn|jp)\b", clean_t):
                lang = "japanese"
            elif re.search(r"\b(chinese|chs|cht)\b", clean_t):
                lang = "chinese"
            elif re.search(r"\b(korean|kor)\b", clean_t):
                lang = "korean"
            return {"company": comp, "grade": grade, "year": yr, "clean_title": clean_t, "lang": lang}

        used_ids = set()
        for it in items:
            candidates = []
            match_reason = "Title + Grade + # Match"
            if it["detected_cert"]:
                candidates = [
                    c for c in all_cards
                    if c["id"] not in used_ids and (c.get("cert_number") or "").strip() == it["detected_cert"]
                ]
                if candidates:
                    match_reason = "Cert Match"
            if not candidates:
                t_low = it["item_title"].lower()
                is_graded_title = bool(re.search(r"\b(psa|cgc|bgs|beckett|tag|slab|graded)\b", t_low))
                if is_graded_title:
                    meta = _parse_ebay_title_meta(it["item_title"])
                    clean_t = meta["clean_title"]
                    scored_cands: list[tuple[float, dict]] = []
                    for c in all_cards:
                        if c["id"] in used_ids:
                            continue
                        # Never match a card with a different explicit cert number
                        c_cert = (c.get("cert_number") or "").strip()
                        if it["detected_cert"] and c_cert and c_cert != it["detected_cert"]:
                            continue
                        # 1. Strict Grading Company match when both are known
                        c_comp = (c.get("submission_company") or "").strip()
                        if meta["company"] and c_comp and meta["company"] != c_comp:
                            continue
                        # 2. Strict Numeric Grade match when both are known (never match PSA 9 to PSA 10)
                        c_grade = _extract_numeric_grade(c.get("actual_grade") or "")
                        if meta["grade"] and c_grade and meta["grade"] != c_grade:
                            continue
                        # 3. Strict 4-digit Year match when both are known (never match 2023 to 1999)
                        c_year = (c.get("card_year") or "").strip()
                        if meta["year"] and c_year and meta["year"] != c_year:
                            continue

                        cname = (c.get("card_name") or "").strip().lower()
                        cnum = (c.get("card_number") or "").strip().lower()
                        tokens = [
                            w for w in re.findall(r"[a-z0-9]+", cname)
                            if len(w) >= 4 and w not in _STOP_TOKENS and not w.isdigit()
                        ]
                        if not tokens:
                            continue
                        # 4. Require strong token overlap against cleaned eBay title
                        hits = sum(1 for w in tokens if re.search(rf"\b{re.escape(w)}\b", clean_t))
                        needed = len(tokens) if len(tokens) <= 2 else max(2, len(tokens) - 1)
                        if hits < needed:
                            continue
                        # 5. Strict Card Number match against cleaned title (never matches PSA 10!)
                        if cnum:
                            num_part = cnum.split("/")[0].strip()
                            num_no_zero = num_part.lstrip("0") or num_part
                            if not re.search(rf"(?:^|[^0-9])0*{re.escape(num_no_zero)}(?:$|[^0-9])", clean_t):
                                continue
                        elif len(tokens) <= 2:
                            # Without a card number, require ALL tokens and matching year
                            if hits < len(tokens) or not (meta["year"] and c_year and meta["year"] == c_year):
                                continue

                        # 6. Language & major variant consistency checks
                        c_lang = "english"
                        if re.search(r"\b(japanese|jpn|jp)\b", cname):
                            c_lang = "japanese"
                        elif re.search(r"\b(chinese|chs|cht)\b", cname):
                            c_lang = "chinese"
                        elif re.search(r"\b(korean|kor)\b", cname):
                            c_lang = "korean"
                        if meta["lang"] != c_lang:
                            continue
                        if ("jumbo" in clean_t) != ("jumbo" in cname):
                            continue
                        if ("master ball" in clean_t) != ("master ball" in cname):
                            continue
                        if ("poke ball" in clean_t or "pokeball" in clean_t) != ("poke ball" in cname or "pokeball" in cname):
                            continue

                        score = float(hits * 10)
                        if cnum:
                            score += 15.0
                        if meta["grade"] and c_grade and meta["grade"] == c_grade:
                            score += 10.0
                        if meta["year"] and c_year and meta["year"] == c_year:
                            score += 10.0
                        if ("reverse" in clean_t) == ("reverse" in cname):
                            score += 5.0
                        scored_cands.append((score, c))
                    scored_cands.sort(key=lambda x: x[0], reverse=True)
                    candidates = [x[1] for x in scored_cands]
            if candidates:
                hit = candidates[0]
                used_ids.add(hit["id"])
                matched_details.append({
                    "card_id": hit["id"],
                    "card_name": hit["card_name"],
                    "card_number": hit.get("card_number") or "",
                    "set_name": hit.get("set_name") or "",
                    "submission_title": hit["submission_title"],
                    "submission_company": hit.get("submission_company") or "",
                    "cert_number": hit.get("cert_number") or "",
                    "actual_grade": hit.get("actual_grade") or "",
                    "image_url": _card_img(hit),
                    "total_cost": _card_cost(hit),
                    "match_reason": match_reason,
                    "candidates": [
                        {
                            "card_id": cand["id"],
                            "card_name": cand["card_name"],
                            "card_number": cand.get("card_number") or "",
                            "set_name": cand.get("set_name") or "",
                            "submission_title": cand["submission_title"],
                            "submission_company": cand.get("submission_company") or "",
                            "cert_number": cand.get("cert_number") or "",
                            "actual_grade": cand.get("actual_grade") or "",
                            "image_url": _card_img(cand),
                            "total_cost": _card_cost(cand),
                        }
                        for cand in candidates
                    ],
                    "item_title": it["item_title"],
                    "sold_for": it["sold_for"],
                    "sale_date": it["sale_date"],
                    "order_number": it["order_number"],
                    "item_number": it["item_number"],
                    "shipping_collected": it["shipping_collected"],
                    "ebay_tax": it["ebay_tax"],
                })
            else:
                unmatched.append(it)

        gross_sales = round(sum(x["sold_for"] for x in items), 2)
        total_ship = round(sum(x["shipping_collected"] for x in items), 2)
        total_tax = round(sum(x["ebay_tax"] for x in items), 2)
        return {
            "total_items": len(items),
            "matched_count": len(matched_details),
            "matched": matched_details,
            "unmatched_count": len(unmatched),
            "unmatched": unmatched,
            "items": items,
            "gross_sales": gross_sales,
            "total_shipping_collected": total_ship,
            "total_ebay_tax": total_tax,
            "message": f"Found {len(items)} eBay sales: {len(matched_details)} match existing vault cards, {len(unmatched)} unmatched.",
        }
    finally:
        conn.close()


class EbayConfirmRequest(BaseModel):
    matched_updates: list[dict] = []
    unmatched_items: list[dict] = []
    create_unmatched_sub: bool = False
    sub_title: str = ""


@app.post("/api/ebay/confirm-sales")
def confirm_ebay_sales(req: EbayConfirmRequest):
    conn = get_db()
    updated_count = 0
    created_sub_id = None
    created_cards = 0
    ts = now_iso()
    try:
        for m in req.matched_updates:
            cid = m.get("card_id")
            if not cid:
                continue
            cur = conn.execute(
                """
                UPDATE cards
                SET status = 'Sold', sold_price = ?, sold_date = ?,
                    ebay_order_number = ?, ebay_item_number = ?,
                    ebay_shipping_collected = ?, ebay_tax_collected = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    float(m.get("sold_for") or 0),
                    m.get("sale_date") or "",
                    m.get("order_number") or "",
                    m.get("item_number") or "",
                    float(m.get("shipping_collected") or 0),
                    float(m.get("ebay_tax") or 0),
                    ts,
                    int(cid),
                ),
            )
            updated_count += cur.rowcount

        if req.create_unmatched_sub and req.unmatched_items:
            stitle = (req.sub_title or "").strip() or f"eBay Sales • {datetime.now().strftime('%Y-%m-%d')}"
            scur = conn.execute(
                """
                INSERT INTO submissions (title, company, submission_number, submitted_date, status, submit_channel, notes, created_at, updated_at)
                VALUES (?, 'PSA', '', ?, 'Completed', 'eBay', 'Imported from eBay Paid & Shipped CSV (Zero PII)', ?, ?)
                """,
                (stitle, datetime.now().strftime("%Y-%m-%d"), ts, ts),
            )
            created_sub_id = scur.lastrowid
            for it in req.unmatched_items:
                conn.execute(
                    """
                    INSERT INTO cards (
                        submission_id, card_name, cert_number, status, sold_price, sold_date,
                        ebay_order_number, ebay_item_number, ebay_shipping_collected, ebay_tax_collected,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, 'Sold', ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        created_sub_id,
                        it.get("item_title") or "eBay Sale",
                        it.get("detected_cert") or "",
                        float(it.get("sold_for") or 0),
                        it.get("sale_date") or "",
                        it.get("order_number") or "",
                        it.get("item_number") or "",
                        float(it.get("shipping_collected") or 0),
                        float(it.get("ebay_tax") or 0),
                        ts,
                        ts,
                    ),
                )
                created_cards += 1
        conn.commit()
        return {
            "updated_count": updated_count,
            "created_submission_id": created_sub_id,
            "created_cards_count": created_cards,
            "message": f"Marked {updated_count} vault card(s) Sold" + (f" and created eBay Sales sub with {created_cards} item(s)." if created_sub_id else "."),
        }
    finally:
        conn.close()


def extract_certs_from_doc(raw_bytes: bytes, filename: str) -> list[str]:
    ext = Path(filename or "").suffix.lower()
    raw_tokens: list[str] = []
    if ext in (".xlsx", ".xlsm") and openpyxl is not None:
        wb = openpyxl.load_workbook(io.BytesIO(raw_bytes), data_only=True)
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                for cell in row:
                    if cell is not None:
                        raw_tokens.append(str(cell).strip())
    elif ext == ".docx":
        try:
            import zipfile as _zf, xml.etree.ElementTree as _ET
            with _zf.ZipFile(io.BytesIO(raw_bytes)) as z:
                xml_bytes = z.read("word/document.xml")
            root = _ET.fromstring(xml_bytes)
            for el in root.iter():
                if el.text and el.text.strip():
                    raw_tokens.append(el.text.strip())
        except Exception:
            pass
    elif ext == ".pdf":
        txt = _extract_text_from_any_doc(raw_bytes, filename)
        for line in txt.splitlines():
            for part in re.split(r"[,;\t]+", line):
                if part.strip():
                    raw_tokens.append(part.strip())
    else:
        txt = raw_bytes.decode("utf-8", errors="ignore")
        for line in txt.splitlines():
            for part in re.split(r"[,;\t]+", line):
                if part.strip():
                    raw_tokens.append(part.strip())

    certs: list[str] = []
    seen: set[str] = set()
    skip_words = {"cert", "certs", "certnumber", "cert_number", "cert#", "certificate", "serial", "number", "id"}
    for tok in raw_tokens:
        low = tok.lower().strip()
        if not low or low in skip_words:
            continue
        # Match standalone cert-like tokens (digits or alphanumeric 5-16 chars with at least 4 digits)
        for m in re.findall(r"\b[A-Za-z0-9]{5,18}\b", tok):
            if sum(ch.isdigit() for ch in m) >= 4 and m not in seen:
                seen.add(m)
                certs.append(m)
    return certs


@app.post("/api/submissions/init-cert-list")
async def init_submission_from_cert_list(
    file: UploadFile = File(...),
    title: str = Form(""),
    company: str = Form(""),
    submitted_date: str = Form(""),
    not_shipped: str = Form(""),
    submit_channel: str = Form(""),
):
    if company not in ("PSA", "CGC", "Beckett", "TAG"):
        raise HTTPException(status_code=400, detail="Please select a grading company (PSA, CGC, Beckett, or TAG) first.")
    raw_bytes = await file.read()
    certs = extract_certs_from_doc(raw_bytes, file.filename or "certs.txt")
    if not certs:
        raise HTTPException(status_code=400, detail="No valid cert numbers found in that file.")

    sub_title = title.strip() or Path(file.filename or f"{company} Cert Import").stem.replace("_", " ").strip()
    sub_date = submitted_date.strip() or datetime.now().strftime("%Y-%m-%d")
    sub_status = "Not Shipped" if not_shipped.strip().lower() in ("true", "1", "yes") else "Completed"
    ts = now_iso()

    conn = get_db()
    created_cards = []
    try:
        cur = conn.execute(
            """
            INSERT INTO submissions (title, company, submission_number, submitted_date, status, submit_channel, notes, created_at, updated_at)
            VALUES (?, ?, '', ?, ?, ?, 'Created from Cert List', ?, ?)
            """,
            (sub_title, company, sub_date, sub_status, submit_channel.strip(), ts, ts),
        )
        sub_id = cur.lastrowid

        for cert in certs:
            cert_clean = extract_cert_number(cert) or cert
            c_name = f"{company} Cert #{cert_clean}"
            c_url = build_cert_url(company, cert_clean)
            c_status = "Not Submitted" if sub_status == "Not Shipped" else "Unsold"
            c_cur = conn.execute(
                """
                INSERT INTO cards (
                    submission_id, card_name, cert_number, cert_lookup_url, status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (sub_id, c_name, cert_clean, c_url, c_status, ts, ts),
            )
            created_cards.append({"id": c_cur.lastrowid, "cert_number": cert_clean})
        conn.commit()
        return {
            "submission_id": sub_id,
            "cards": created_cards,
            "total": len(created_cards),
        }
    finally:
        conn.close()


@app.post("/api/submissions/import-cert-list")
async def create_submission_from_cert_list(
    file: UploadFile = File(...),
    title: str = Form(""),
    company: str = Form(""),
    submitted_date: str = Form(""),
    not_shipped: str = Form(""),
    submit_channel: str = Form(""),
):
    if company not in ("PSA", "CGC", "Beckett", "TAG"):
        raise HTTPException(status_code=400, detail="Please select a grading company (PSA, CGC, Beckett, or TAG) first.")
    raw_bytes = await file.read()
    certs = extract_certs_from_doc(raw_bytes, file.filename or "certs.txt")
    if not certs:
        raise HTTPException(status_code=400, detail="No valid cert numbers found in that file.")

    sub_title = title.strip() or Path(file.filename or f"{company} Cert Import").stem.replace("_", " ").strip()
    sub_date = submitted_date.strip() or datetime.now().strftime("%Y-%m-%d")
    sub_status = "Not Shipped" if not_shipped.strip().lower() in ("true", "1", "yes") else "Completed"
    ts = now_iso()

    conn = get_db()
    pulled = 0
    try:
        cur = conn.execute(
            """
            INSERT INTO submissions (title, company, submission_number, submitted_date, status, submit_channel, notes, created_at, updated_at)
            VALUES (?, ?, '', ?, ?, ?, 'Created from Cert List', ?, ?)
            """,
            (sub_title, company, sub_date, sub_status, submit_channel.strip(), ts, ts),
        )
        sub_id = cur.lastrowid

        for cert in certs:
            cert_clean = extract_cert_number(cert) or cert
            c_name = f"{company} Cert #{cert_clean}"
            c_year = ""
            c_set = ""
            c_num = ""
            c_grade = ""
            c_front = ""
            c_back = ""
            c_url = build_cert_url(company, cert_clean)
            try:
                res = fetch_cert_scans_from_web(company, cert_clean)
                if res.get("official_name"):
                    c_name = res["official_name"].strip()
                if res.get("official_year"):
                    c_year = res["official_year"].strip()
                if res.get("official_set"):
                    c_set = res["official_set"].strip()
                if res.get("official_number"):
                    c_num = res["official_number"].strip()
                if res.get("detected_grade"):
                    c_grade = res["detected_grade"].strip()
                if res.get("front_image"):
                    c_front = res["front_image"]
                if res.get("back_image"):
                    c_back = res["back_image"]
                if res.get("lookup_url"):
                    c_url = res["lookup_url"]
                if res.get("success") or res.get("official_name"):
                    pulled += 1
            except Exception:
                pass

            c_status = "Not Submitted" if sub_status == "Not Shipped" else resolve_card_status("In Grading", None, cert_clean, c_grade, "")
            conn.execute(
                """
                INSERT INTO cards (
                    submission_id, card_name, card_year, set_name, card_number,
                    cert_number, cert_front_image, cert_back_image, cert_lookup_url,
                    actual_grade, status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (sub_id, c_name, c_year, c_set, c_num, cert_clean, c_front, c_back, c_url, c_grade, c_status, ts, ts),
            )
        conn.commit()
        return {
            "submission_id": sub_id,
            "created_count": len(certs),
            "pulled_count": pulled,
            "message": f"Created {len(certs)} cards from cert list (pulled official data for {pulled}).",
        }
    finally:
        conn.close()


@app.post("/api/submissions/import-fanatics-csv")
async def create_submission_from_fanatics_csv(
    file: UploadFile = File(...),
    title: str = Form(""),
    company: str = Form(""),
    submitted_date: str = Form(""),
):
    raw_bytes = await file.read()
    sub_title = title.strip() or Path(file.filename or "Fanatics Submission").stem.replace("_", " ").strip()
    sub_date = submitted_date.strip() or datetime.now().strftime("%Y-%m-%d")
    ts = now_iso()
    conn = get_db()
    try:
        if company not in ("PSA", "CGC", "Beckett", "TAG"):
            raise HTTPException(status_code=400, detail="Please select a grading company (PSA, CGC, or Beckett) first.")
        cur = conn.execute(
            """
            INSERT INTO submissions (title, company, submission_number, submitted_date, status, notes, created_at, updated_at)
            VALUES (?, ?, '', ?, 'Completed', 'Created from Fanatics CSV', ?, ?)
            """,
            (sub_title, company, sub_date, ts, ts),
        )
        conn.commit()
        sub_id = cur.lastrowid
    finally:
        conn.close()

    # Reuse import_fanatics_csv with a fresh UploadFile wrapper
    import tempfile as _tf
    with _tf.SpooledTemporaryFile() as sp:
        sp.write(raw_bytes)
        sp.seek(0)
        up = UploadFile(filename=file.filename or "fanatics.csv", file=sp)
        res = await import_fanatics_csv(sub_id, up)
    res["submission_id"] = sub_id
    return res


UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def _extract_fanatics_collection_items(obj, out_items: list):
    if isinstance(obj, list):
        for el in obj:
            _extract_fanatics_collection_items(el, out_items)
        return
    if not isinstance(obj, dict):
        return

    raw_id = str(obj.get("id") or obj.get("itemId") or obj.get("collectibleId") or obj.get("vaultItemId") or "").strip()
    raw_url = str(obj.get("url") or obj.get("href") or obj.get("slug") or "").strip()
    uuid_val = raw_id if UUID_RE.match(raw_id) else ""
    if not uuid_val and "/collection/i/" in raw_url:
        m_u = re.search(r"/collection/i/([0-9a-f-]{36})", raw_url, re.I)
        if m_u:
            uuid_val = m_u.group(1)

    cert_raw = str(
        obj.get("certNumber") or obj.get("certificationNumber") or obj.get("cert_number")
        or obj.get("certNo") or obj.get("cert") or obj.get("serialNumber") or ""
    ).strip()
    cert_clean = extract_cert_number(cert_raw) if cert_raw else ""

    # Extract high-res front & back image URLs from common Fanatics GraphQL structures
    front_img = ""
    back_img = ""
    for fk in ("frontImageUrl", "frontImage", "obverseImageUrl", "imageUrl", "primaryImageUrl", "image"):
        v = obj.get(fk)
        if isinstance(v, dict):
            v = v.get("url") or v.get("large") or v.get("original") or v.get("src") or ""
        if isinstance(v, str) and v.startswith("http"):
            front_img = v
            break
    for bk in ("backImageUrl", "backImage", "reverseImageUrl", "secondaryImageUrl"):
        v = obj.get(bk)
        if isinstance(v, dict):
            v = v.get("url") or v.get("large") or v.get("original") or v.get("src") or ""
        if isinstance(v, str) and v.startswith("http"):
            back_img = v
            break

    for list_key in ("images", "media", "assets", "photos"):
        arr = obj.get(list_key)
        if isinstance(arr, list) and arr:
            urls = []
            for item in arr:
                if isinstance(item, str) and item.startswith("http"):
                    urls.append(item)
                elif isinstance(item, dict):
                    u = item.get("url") or item.get("largeUrl") or item.get("originalUrl") or item.get("src") or ""
                    if isinstance(u, str) and u.startswith("http"):
                        urls.append(u)
            if urls and not front_img:
                front_img = urls[0]
            if len(urls) >= 2 and not back_img:
                back_img = urls[1]

    title = str(obj.get("title") or obj.get("name") or obj.get("cardName") or "").strip()
    grade = str(obj.get("grade") or obj.get("gradeValue") or obj.get("gradeLabel") or "").strip()

    if (uuid_val or front_img) and (cert_clean or title):
        item_url = f"https://www.fanaticscollect.com/collection/i/{uuid_val}" if uuid_val else (
            urljoin("https://www.fanaticscollect.com", raw_url) if raw_url.startswith("/") else raw_url
        )
        out_items.append({
            "uuid": uuid_val,
            "item_url": item_url,
            "cert_number": cert_clean,
            "title": title,
            "grade": grade,
            "front_img_url": front_img,
            "back_img_url": back_img,
        })

    for v in obj.values():
        if isinstance(v, (dict, list)):
            _extract_fanatics_collection_items(v, out_items)


def fetch_fanatics_collection_via_session(url: str, window_bounds: Optional[tuple] = None) -> tuple:
    pw_profile = str((DATA_DIR / "pw_profile").resolve())
    pop_w, pop_h, pop_x, pop_y = 1040, 800, None, None
    if window_bounds and all(v is not None for v in window_bounds):
        wx, wy, ww, wh = [int(v) for v in window_bounds]
        pop_w = min(1060, max(700, ww - 60))
        pop_h = min(800, max(580, wh - 80))
        pop_x = wx + max(20, (ww - pop_w) // 2)
        pop_y = wy + max(20, (wh - pop_h) // 2)
    extra_args = ["--disable-blink-features=AutomationControlled", f"--window-size={pop_w},{pop_h}"]
    if pop_x is not None and pop_y is not None:
        extra_args.append(f"--window-position={pop_x},{pop_y}")

    helper_code = f"""
import json, sys, time
from playwright.sync_api import sync_playwright

url = {json.dumps(url)}
profile_dir = {json.dumps(pw_profile)}
res = {{"url": url, "json_payloads": [], "dom_links": [], "error": ""}}

with sync_playwright() as p:
    ctx = None
    for ch in ("msedge", "chrome", None):
        try:
            kw = {{"headless": False, "args": {json.dumps(extra_args)}, "viewport": {{"width": {pop_w - 24}, "height": {pop_h - 90}}}}}
            if ch: kw["channel"] = ch
            ctx = p.chromium.launch_persistent_context(profile_dir, **kw)
            break
        except Exception:
            pass
    if not ctx:
        res["error"] = "Playwright browser unavailable."
        print(json.dumps(res))
        sys.exit(0)
    try:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {{get: () => undefined}});")

        captured_gql = []
        def on_response(resp):
            try:
                ct = (resp.headers.get("content-type") or "").lower()
                if "json" in ct and resp.status == 200:
                    txt = resp.text()
                    if len(txt) > 100 and any(k in txt.lower() for k in ("cert", "collection", "collectible", "image", "grade")):
                        res["json_payloads"].append(json.loads(txt))
                        req = resp.request
                        if req.method == "POST" and ("graphql" in resp.url.lower() or "api" in resp.url.lower()):
                            try:
                                post_d = req.post_data or ""
                                if any(k in post_d for k in ("limit", "first", "pageSize", "perPage")):
                                    captured_gql.append((resp.url, dict(req.headers), post_d))
                            except Exception:
                                pass
            except Exception:
                pass

        page.on("response", on_response)
        page.goto(url, wait_until="domcontentloaded", timeout=25000)

        deadline = time.time() + 90
        while time.time() < deadline:
            cur_u = (page.url or "").lower()
            title = (page.title() or "").lower()
            body_txt = ""
            try:
                body_txt = page.inner_text("body")[:500].lower()
            except Exception:
                pass
            is_cf = ("just a moment" in title or "verify you are human" in body_txt)
            is_login = any(k in cur_u for k in ("/login", "/signin", "/sign-in", "/auth")) or any(
                k in body_txt for k in ("page cannot be found", "page not found", "sign in to", "log in to")
            )
            if not is_cf and not is_login:
                try:
                    page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    pass
                page.wait_for_timeout(2000)
                break
            time.sleep(1.0)

        # Single-request GraphQL replay with larger limit (250) so all 60+ items arrive in 1 call (zero page-clicking!)
        if captured_gql:
            g_url, g_hdrs, g_body = captured_gql[-1]
            try:
                parsed_body = json.loads(g_body)
                vars_obj = parsed_body.get("variables") if isinstance(parsed_body, dict) else None
                if isinstance(vars_obj, dict):
                    changed = False
                    for lk in ("limit", "first", "pageSize", "perPage"):
                        if lk in vars_obj and isinstance(vars_obj[lk], int) and vars_obj[lk] < 250:
                            vars_obj[lk] = 250
                            changed = True
                    if changed:
                        js_replay = "async ([u, b]) => {{ const r = await fetch(u, {{ method: 'POST', headers: {{'content-type': 'application/json'}}, credentials: 'include', body: JSON.stringify(b) }}); return await r.json(); }}"
                        replay_res = page.evaluate(js_replay, [g_url, parsed_body])
                        if isinstance(replay_res, dict):
                            res["json_payloads"].append(replay_res)
                            # If server capped page size (e.g. 50) and returned endCursor or total > 50, fetch page 2 via cursor/page
                            try:
                                r_txt = json.dumps(replay_res)
                                m_cur = re.search(r'"endCursor"\s*:\s*"([^"]+)"', r_txt)
                                if m_cur and '"hasNextPage"\s*:\s*true' in r_txt:
                                    vars_obj["after"] = m_cur.group(1)
                                    pg2 = page.evaluate(js_replay, [g_url, parsed_body])
                                    if isinstance(pg2, dict):
                                        res["json_payloads"].append(pg2)
                                elif "page" in vars_obj and isinstance(vars_obj["page"], int):
                                    vars_obj["page"] = vars_obj["page"] + 1
                                    pg2 = page.evaluate(js_replay, [g_url, parsed_body])
                                    if isinstance(pg2, dict):
                                        res["json_payloads"].append(pg2)
                            except Exception:
                                pass
            except Exception:
                pass

        # Also collect any rendered /collection/i/<uuid> links from the DOM
        try:
            js_dom = "() => Array.from(document.querySelectorAll('a[href*=\"/collection/i/\"]')).map(a => ({{ href: a.href, text: (a.innerText || '').trim(), img: a.querySelector('img')?.src || '' }}))"
            dom_links = page.evaluate(js_dom)
            res["dom_links"] = dom_links or []
        except Exception:
            pass

        res["url"] = page.url
        res["html"] = page.content()
    except Exception as e:
        res["error"] = str(e)
    finally:
        ctx.close()

print(json.dumps(res))
"""
    try:
        proc = subprocess.run(
            [sys.executable, "-c", helper_code],
            capture_output=True,
            text=True,
            timeout=max_wait_s + 20,
        )
        if proc.returncode != 0:
            raw_err = (proc.stderr or "").strip()
            if "No module named 'playwright'" in raw_err:
                return url, "", "Playwright is not installed in .venv (run: pip install playwright && playwright install chromium)."
            return url, "", (raw_err.splitlines()[-1] if raw_err else "Playwright subprocess failed")[:220]
        lines = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
        if not lines:
            return url, "", "Playwright returned empty output."
        data = json.loads(lines[-1])
        return data.get("url", url), data.get("html", ""), data.get("error", "")
    except Exception as exc:
        return url, "", f"Browser fallback unavailable: {exc}"


def fetch_cgc_highres_with_playwright(url: str) -> tuple:
    """
    CGC cert pages show a low-res thumbnail until clicked.
    Opens CGC cert page headless in Playwright, clicks the slab image(s) to trigger
    the high-res lightbox/modal, and captures the full-resolution image URLs + HTML.
    """
    pw_profile = str((DATA_DIR / "pw_profile").resolve())
    helper_code = f"""
import json, sys, time
from playwright.sync_api import sync_playwright

url = {json.dumps(url)}
profile_dir = {json.dumps(pw_profile)}
res = {{"url": url, "html": "", "highres_urls": [], "error": ""}}

with sync_playwright() as p:
    ctx = None
    for ch in ("msedge", "chrome", None):
        try:
            kw = {{"headless": True, "viewport": {{"width": 1280, "height": 900}}}}
            if ch: kw["channel"] = ch
            ctx = p.chromium.launch_persistent_context(profile_dir, **kw)
            break
        except Exception:
            pass
    if not ctx:
        print(json.dumps(res))
        sys.exit(0)
    try:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        seen_hi = []
        def on_resp(resp):
            try:
                ct = (resp.headers.get("content-type") or "").lower()
                if "image/" in ct and resp.status == 200:
                    clen = int(resp.headers.get("content-length") or "0")
                    if clen > 45000 and resp.url not in seen_hi:
                        seen_hi.append(resp.url)
            except Exception:
                pass
        page.on("response", on_resp)
        page.goto(url, wait_until="networkidle", timeout=20000)
        page.wait_for_timeout(1200)
        res["html"] = page.content()

        # Click any slab/cert thumbnail images to trigger CGC's high-res modal
        imgs = page.locator("img").all()
        for im in imgs[:8]:
            try:
                src = (im.get_attribute("src") or "").lower()
                if any(bad in src for bad in ("logo", "icon", "svg", "avatar", "banner")):
                    continue
                if im.is_visible(timeout=400):
                    im.click(timeout=1500)
                    page.wait_for_timeout(1200)
                    for m_im in page.locator("img").all():
                        ms = m_im.get_attribute("src") or ""
                        if ms and ms.startswith("http") and not any(b in ms.lower() for b in ("logo", "icon", "banner")):
                            if ms not in seen_hi:
                                seen_hi.append(ms)
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(300)
            except Exception:
                pass

        res["url"] = page.url
        res["highres_urls"] = seen_hi
    except Exception as e:
        res["error"] = str(e)
    finally:
        ctx.close()

print(json.dumps(res))
"""
    try:
        proc = subprocess.run([sys.executable, "-c", helper_code], capture_output=True, text=True, timeout=35)
        lines = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
        if not lines:
            return url, "", []
        data = json.loads(lines[-1])
        return data.get("url", url), data.get("html", ""), data.get("highres_urls", [])
    except Exception:
        return url, "", []


def upgrade_cert_image_candidates(img_url: str) -> list:
    from urllib.parse import urlparse, parse_qs, unquote
    u = (img_url or "").strip()
    if not u:
        return []
    out = []

    base_targets = []
    try:
        parsed_u = urlparse(u)
        qs = parse_qs(parsed_u.query)
        if "url" in qs and qs["url"]:
            inner = unquote(qs["url"][0]).strip()
            if inner.startswith("http"):
                no_tn = re.sub(r"/(?:TN_|tn_)", "/", inner)
                if no_tn != inner:
                    base_targets.append(no_tn)
                base_targets.append(inner)
    except Exception:
        pass

    no_tn_u = re.sub(r"/(?:TN_|tn_)", "/", u)
    if no_tn_u != u:
        base_targets.append(no_tn_u)
    base_targets.append(u)

    for tgt in base_targets:
        if tgt not in out:
            out.append(tgt)

    no_q = re.sub(r"([?&])(?:w|width|h|height|size|quality|q)=\d+&?", r"\1", base_targets[0], flags=re.I).rstrip("?&")
    if no_q not in out and "/_next/image" not in no_q:
        out.insert(0, no_q)
    # 2. Replace /small/, /medium/, /thumb/, /thumbnails/ with /large/ or /original/
    for folder in ("/large/", "/original/", "/full/"):
        u_dir = re.sub(r"/(?:small|med|medium|thumb|thumbs|thumbnail|thumbnails)/", folder, no_q, flags=re.I)
        if u_dir not in out:
            out.append(u_dir)
    # 3. Replace filename suffixes like _small, _med, _thumb, -thumb, _300
    u_suf = re.sub(r"([_-])(?:small|sm|med|medium|thumb|thumbnail)(?=\.[a-z]+(?:$|\?))", "", no_q, flags=re.I)
    if u_suf not in out:
        out.append(u_suf)
    u_suf_lg = re.sub(r"([_-])(?:small|sm|med|medium|thumb|thumbnail)(?=\.[a-z]+(?:$|\?))", r"\1large", no_q, flags=re.I)
    if u_suf_lg not in out:
        out.append(u_suf_lg)
    if no_q not in out:
        out.append(no_q)
    if u not in out:
        out.append(u)
    return out


def upgrade_cert_image_url(img_url: str) -> str:
    cands = upgrade_cert_image_candidates(img_url)
    return cands[0] if cands else (img_url or "").strip()


def download_image_to_certs(img_url: str, cert_number: str, side: str) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        )
    }
    resp = None
    best_bytes = 0
    for cand_url in upgrade_cert_image_candidates(img_url):
        try:
            r_cand = requests.get(cand_url, headers=headers, timeout=12)
            if r_cand.status_code == 200 and len(r_cand.content) > best_bytes:
                try:
                    with Image.open(io.BytesIO(r_cand.content)) as im_chk:
                        w, h = im_chk.size
                    if w >= 120 and h >= 120:
                        resp = r_cand
                        best_bytes = len(r_cand.content)
                        break
                except Exception:
                    pass
        except Exception:
            continue
    if resp is None:
        resp = requests.get(img_url, headers=headers, timeout=12)
        resp.raise_for_status()
    ctype = resp.headers.get("Content-Type", "").lower()
    ext = ".jpg"
    if "png" in ctype or img_url.lower().endswith(".png"):
        ext = ".png"
    elif "webp" in ctype or img_url.lower().endswith(".webp"):
        ext = ".webp"
    safe_cert = re.sub(r"[^a-zA-Z0-9_-]", "", cert_number)[:32] or "cert"
    filename = f"{safe_cert}_{side}_{uuid.uuid4().hex[:8]}{ext}"
    if side == "back":
        try:
            with Image.open(io.BytesIO(resp.content)) as im:
                w, h = im.size
                if w > h * 1.15:
                    raise ValueError(f"Rejected landscape non-slab image ({w}x{h}) for cert back")
        except ValueError:
            raise
        except Exception:
            pass
    dest = CERT_UPLOADS_DIR / filename
    dest.write_bytes(resp.content)
    return f"certs/{filename}"


SET_ABBREVIATIONS = {
    "por": "Perfect Order",
    "cz": "Crown Zenith",
    "crz": "Crown Zenith",
    "svi": "Scarlet & Violet",
    "pal": "Paldea Evolved",
    "obf": "Obsidian Flames",
    "mew": "Pokemon 151",
    "par": "Paradox Rift",
    "paf": "Paldean Fates",
    "tef": "Temporal Forces",
    "twm": "Twilight Masquerade",
    "sfa": "Shrouded Fable",
    "scr": "Stellar Crown",
    "ssp": "Surging Sparks",
    "pre": "Prismatic Evolutions",
    "jtg": "Journey Together",
    "dri": "Destined Rivals",
    "sit": "Silver Tempest",
    "st": "Silver Tempest",
    "lor": "Lost Origin",
    "asr": "Astral Radiance",
    "brs": "Brilliant Stars",
    "fst": "Fusion Strike",
    "evs": "Evolving Skies",
    "cre": "Chilling Reign",
    "bst": "Battle Styles",
    "swsh": "Sword & Shield",
    "mep": "Mega Evolution Promo",
}


def expand_set_name(raw_set: str, card_name_hint: str = "") -> str:
    s = (raw_set or "").strip()
    if s.lower() in SET_ABBREVIATIONS:
        return SET_ABBREVIATIONS[s.lower()]
    for src in (s, card_name_hint or ""):
        for tok in re.findall(r"\b([A-Za-z]{2,5})\b", src):
            if tok.lower() in SET_ABBREVIATIONS:
                if not s or s.lower() == tok.lower():
                    return SET_ABBREVIATIONS[tok.lower()]
    return s


def parse_cert_details_from_html(html: str, company: str, base_url: str, cert_clean: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    # 1. Extract images
    candidates = []
    for meta in soup.find_all("meta", attrs={"property": re.compile(r"og:image", re.I)}):
        content = (meta.get("content") or "").strip()
        if content:
            candidates.append(("og", urljoin(base_url, content)))

    def _in_related_section(tag) -> bool:
        for parent in tag.parents:
            p_attr = " ".join([
                parent.get("id") or "",
                " ".join(parent.get("class") or []),
                parent.get("aria-label") or "",
                parent.get("data-testid") or "",
            ]).lower()
            if any(k in p_attr for k in ("similar", "related", "recommend", "also-like", "suggested", "more-from", "other-lot")):
                return True
        return False

    for img in soup.find_all("img"):
        if _in_related_section(img):
            continue
        parent_a = img.find_parent("a", href=True)
        parent_href = (parent_a.get("href") or "").strip() if parent_a else ""
        if parent_href and not re.search(r"\.(?:jpg|jpeg|png|webp)(?:\?|$)", parent_href, re.I):
            parent_href = ""
        src = (
            parent_href
            or img.get("data-zoom-image")
            or img.get("data-highres")
            or img.get("data-full")
            or img.get("data-large")
            or img.get("data-lg-src")
            or img.get("data-original")
            or img.get("data-image")
            or img.get("src")
            or img.get("data-src")
            or ""
        ).strip()
        srcset = (img.get("srcset") or "").strip()
        if srcset:
            best_ss_url = ""
            best_ss_w = -1
            for part in srcset.split(","):
                toks = part.strip().split()
                if toks:
                    w_val = 0
                    if len(toks) >= 2 and toks[1].endswith("w") and toks[1][:-1].isdigit():
                        w_val = int(toks[1][:-1])
                    if w_val >= best_ss_w:
                        best_ss_w = w_val
                        best_ss_url = toks[0]
            if best_ss_url:
                src = best_ss_url
        if not src or src.startswith("data:"):
            continue
        full_src = urljoin(base_url, src)
        alt = (img.get("alt") or "").lower()
        cls = " ".join(img.get("class") or []).lower()
        combined = f"{full_src.lower()} {alt} {cls}"
        if any(bad in combined for bad in ["logo", "icon", "favicon", "avatar", "banner", "spinner", "placeholder", "mobile_v1", "foundation", "promo", "hero", "social", "marketing", "wp-content"]):
            continue
        candidates.append((combined, full_src))

    next_script = soup.find("script", id="__NEXT_DATA__")
    if next_script and next_script.string:
        raw_json = next_script.string
        for m in re.findall(r'https?://[^"\'\s<>]+\.(?:jpg|jpeg|png|webp)(?:\?[^"\'\s<>]*)?', raw_json, re.I):
            low = m.lower()
            if any(bad in low for bad in ["logo", "icon", "favicon", "avatar", "ctfassets", "mobile_v1", "foundation", "banner", "promo", "hero", "marketing"]):
                continue
            candidates.append((low, m))

    for m in re.findall(r'https?://[^"\'\s<>]+(?:cloudfront|amazonaws|psacard|cgc|beckett)[^"\'\s<>]*\.(?:jpg|jpeg|png|webp)', html, re.I):
        low = m.lower()
        if any(bad in low for bad in ["logo", "icon", "favicon", "avatar", "banner", "mobile_v1", "foundation", "promo", "hero", "marketing"]):
            continue
        candidates.append((low, m))

    front_img_url = ""
    back_img_url = ""
    for meta_str, url in candidates:
        low = f"{meta_str} {url.lower()}"
        if not front_img_url and ("front" in low or "obverse" in low or "_f" in low or cert_clean.lower() in url.lower()):
            front_img_url = url
        elif not back_img_url and ("back" in low or "reverse" in low or "_b" in low):
            back_img_url = url
    slab_urls = []
    for meta_str, url in candidates:
        if url not in slab_urls and (meta_str == "og" or any(k in url.lower() for k in ["cert", "slab", "cloudfront", "amazonaws", "images"])):
            slab_urls.append(url)
    if not front_img_url and slab_urls:
        front_img_url = slab_urls[0]
    is_marketplace = any(d in (base_url or "").lower() for d in ("fanaticscollect", "ebay.", "goldin.", "ha.com", "pwcc"))
    if not back_img_url and len(slab_urls) >= 2:
        front_host = front_img_url.split("/")[2] if front_img_url.startswith("http") else ""
        front_dir = front_img_url.rsplit("/", 1)[0] if "/" in front_img_url else ""
        for u in slab_urls:
            if u == front_img_url:
                continue
            u_low = u.lower()
            if any(b in u_low for b in ["foundation", "banner", "promo", "og-", "marketing", "default", "share"]):
                continue
            u_host = u.split("/")[2] if u.startswith("http") else ""
            if front_host and u_host and u_host != front_host and cert_clean.lower() not in u_low:
                continue
            if is_marketplace:
                u_dir = u.rsplit("/", 1)[0] if "/" in u else ""
                has_back_hint = any(k in u_low for k in ("back", "reverse", "_b.", "-b.", "/b."))
                same_item_dir = bool(front_dir and u_dir == front_dir and not front_dir.endswith("/slabs"))
                if not has_back_hint and not same_item_dir:
                    continue
            back_img_url = u
            break

    # 2. Extract official label-value pairs (Year, Brand/Set, Card Number, Subject/Card Name, Grade)
    kv = {}
    for tr in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
        if len(cells) >= 2:
            kv[cells[0].lower().rstrip(":")] = cells[1].strip()

    for dl in soup.find_all("dl"):
        dts = dl.find_all("dt")
        dds = dl.find_all("dd")
        for dt, dd in zip(dts, dds):
            k = dt.get_text(" ", strip=True).lower().rstrip(":")
            v = dd.get_text(" ", strip=True)
            if k and v and k not in kv:
                kv[k] = v

    for key_pat, kv_key in [
        (r'"Year"\s*:\s*"([^"]+)"', "year"),
        (r'"Brand"\s*:\s*"([^"]+)"', "brand"),
        (r'"SetName"\s*:\s*"([^"]+)"', "set"),
        (r'"Subject"\s*:\s*"([^"]+)"', "subject"),
        (r'"CardNumber"\s*:\s*"([^"]+)"', "card number"),
        (r'"GradeDescription"\s*:\s*"([^"]+)"', "grade"),
    ]:
        if kv_key not in kv:
            mj = re.search(key_pat, html, re.I)
            if mj and mj.group(1).strip():
                kv[kv_key] = mj.group(1).strip()

    for idx, ln in enumerate(lines[:-1]):
        low_ln = ln.lower().rstrip(":")
        if low_ln in ("year", "brand", "set", "series", "subject", "player", "card", "card name", "card number", "card #", "grade", "variety/pedigree", "category"):
            if low_ln not in kv and len(lines[idx + 1]) < 120:
                kv[low_ln] = lines[idx + 1]

    official_year = kv.get("year", "")
    official_set = kv.get("brand") or kv.get("set") or kv.get("series") or ""
    official_name = kv.get("subject") or kv.get("card name") or kv.get("player") or kv.get("card") or ""
    official_number = kv.get("card number") or kv.get("card #") or ""
    official_grade = kv.get("grade") or ""

    if not official_grade:
        grade_match = re.search(
            r"(?:Grade|PSA|CGC|BGS)\s*[:#]?\s*(Pristine\s*10|Gem\s*Mint\s*10|10|9\.5|9|8\.5|8|7\.5|7|6\.5|6|5\.5|5|4\.5|4|3\.5|3|2\.5|2|1\.5|1)",
            text,
            re.I,
        )
        if grade_match:
            official_grade = grade_match.group(1).strip()

    # Clean numeric grade if PSA says "GEM MT 10" -> "10" or keep "Pristine 10"
    if official_grade:
        if "pristine" in official_grade.lower() and "10" in official_grade:
            official_grade = "Pristine 10"
        else:
            m_g = re.search(r"(10|9\.5|9|8\.5|8|7\.5|7|6\.5|6|5\.5|5|4\.5|4|3\.5|3|2\.5|2|1\.5|1)\b", official_grade)
            if m_g:
                official_grade = m_g.group(1)

    return {
        "front_img_url": front_img_url,
        "back_img_url": back_img_url,
        "official_name": official_name,
        "official_year": official_year,
        "official_set": expand_set_name(official_set, official_name),
        "official_number": official_number,
        "detected_grade": official_grade,
    }


def fetch_cert_scans_from_web(company: str, cert_number: str, window_bounds: Optional[tuple] = None, headless_only: bool = False) -> dict:
    cert_clean = extract_cert_number(cert_number)
    if not cert_clean:
        return {
            "success": False,
            "message": "Please enter a cert number first.",
            "lookup_url": "",
            "front_image": "",
            "back_image": "",
        }

    urls_to_try = [build_cert_url(company, cert_clean)]
    if company == "CGC":
        urls_to_try.append(f"https://www.cgccards.com/certlookup/{cert_clean}")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    parsed = {}
    error_reason = ""
    primary_url = urls_to_try[0]
    needs_browser = False

    for url in urls_to_try:
        try:
            resp = requests.get(url, headers=headers, timeout=10, allow_redirects=True)
            if resp.status_code in (403, 429):
                needs_browser = True
                continue
            if resp.status_code >= 400:
                error_reason = f"{company} returned HTTP {resp.status_code} for cert {cert_clean}."
                continue

            info = parse_cert_details_from_html(resp.text, company, resp.url, cert_clean)
            if info["front_img_url"] or info["official_name"]:
                parsed = info
                primary_url = resp.url
                break
            else:
                needs_browser = True
        except requests.RequestException as exc:
            error_reason = f"Network error reaching {company}: {exc}"

    # CGC shows low-res thumbnails until clicked: capture clicked high-res images if available
    if company == "CGC" and (not parsed.get("front_img_url") or "thumb" in (parsed.get("front_img_url") or "").lower() or "small" in (parsed.get("front_img_url") or "").lower() or not parsed.get("back_img_url")):
        cgc_url, cgc_html, hi_urls = fetch_cgc_highres_with_playwright(primary_url)
        if cgc_html:
            info = parse_cert_details_from_html(cgc_html, company, cgc_url, cert_clean)
            if info.get("official_name") or info.get("front_img_url"):
                parsed = info
                primary_url = cgc_url
        if hi_urls:
            # Prefer largest / clicked high-res URLs
            parsed["front_img_url"] = hi_urls[0]
            if len(hi_urls) >= 2:
                parsed["back_img_url"] = hi_urls[1]

    # Visible browser Playwright fallback for Cloudflare-protected PSA / Beckett / CGC pages
    if not parsed.get("front_img_url") and needs_browser:
        if company == "Beckett" and not headless_only:
            pw_url, pw_html, pw_err = fetch_html_with_playwright(primary_url, visible=True, max_wait_s=60, window_bounds=window_bounds)
        else:
            pw_url, pw_html, pw_err = fetch_html_with_playwright(primary_url, visible=False, max_wait_s=10, window_bounds=window_bounds)
            if (not pw_html or "just a moment" in pw_html.lower() or "verify you are human" in pw_html.lower()) and not headless_only:
                pw_url, pw_html, pw_err = fetch_html_with_playwright(primary_url, visible=True, max_wait_s=50, window_bounds=window_bounds)
        if pw_html and "just a moment" not in pw_html.lower():
            info = parse_cert_details_from_html(pw_html, company, pw_url, cert_clean)
            if info["front_img_url"] or info["official_name"] or info["detected_grade"]:
                parsed = info
                primary_url = pw_url
        elif pw_err:
            error_reason = pw_err

    saved_front = ""
    saved_back = ""
    if parsed.get("front_img_url"):
        try:
            saved_front = download_image_to_certs(parsed["front_img_url"], cert_clean, "front")
        except Exception:
            saved_front = parsed["front_img_url"]
    if parsed.get("back_img_url"):
        try:
            saved_back = download_image_to_certs(parsed["back_img_url"], cert_clean, "back")
        except Exception:
            saved_back = parsed["back_img_url"]

    has_any = bool(saved_front or saved_back or parsed.get("official_name") or parsed.get("detected_grade"))
    if has_any:
        return {
            "success": True,
            "message": f"Pulled cert data & scans from {company}!",
            "lookup_url": primary_url,
            "front_image": saved_front,
            "back_image": saved_back,
            "detected_grade": parsed.get("detected_grade", ""),
            "official_name": parsed.get("official_name", ""),
            "official_year": parsed.get("official_year", ""),
            "official_set": parsed.get("official_set", ""),
            "official_number": parsed.get("official_number", ""),
        }

    return {
        "success": False,
        "message": error_reason or (
            f"{company} required manual verification. Click 'Open Official Cert ↗', right-click the slab photo -> Copy Image, "
            "then click Cert Front/Back here and press Ctrl+V."
        ),
        "lookup_url": primary_url,
        "front_image": "",
        "back_image": "",
        "detected_grade": "",
        "official_name": "",
        "official_year": "",
        "official_set": "",
        "official_number": "",
    }


def score_pricecharting_candidate(href: str, link_text: str, card_name: str, set_name: str, card_number: str) -> int:
    score = 0
    combined = f"{href} {link_text}".lower()
    if "/game/" not in href.lower():
        return -1000

    # Match card number strongly (e.g., 160 from 160/159)
    num_token = ""
    if card_number:
        num_token = card_number.split("/")[0].strip().lstrip("0").lower()
    else:
        m = re.search(r"\b(\d{1,4})\s*/\s*\d{1,4}\b", card_name or "")
        if m:
            num_token = m.group(1).lstrip("0").lower()

    if num_token and re.search(rf"(?<!\d)0*{re.escape(num_token)}(?!\d)", combined):
        score += 50

    # Match language preference
    is_jp = any(k in (card_name + " " + set_name).lower() for k in ("(jp)", "japanese", "jpn"))
    has_jp_in_url = "japanese" in combined
    if is_jp == has_jp_in_url:
        score += 20
    else:
        score -= 25

    # Match set words
    for word in re.findall(r"[a-z0-9]{3,}", (set_name or "").lower()):
        if word in ("pokemon", "card", "tcg", "the", "and"):
            continue
        if word in combined:
            score += 15

    # Match card name words
    clean_name = re.sub(r"\([^)]*\)", "", card_name or "").lower()
    for word in re.findall(r"[a-z]{3,}", clean_name):
        if word in combined:
            score += 12

    return score


def fetch_graded_market_value(card_name: str, set_name: str, card_number: str, grade: str, company: str) -> dict:
    """
    Searches PriceCharting using official card name, set, and card number,
    scores search results to pick the exact card, and reads the exact
    Company + Grade row from PriceCharting's Full Price Guide table.
    """
    clean_name = re.sub(r"\([^)]*\)", "", card_name or "").strip()
    num_part = card_number.split("/")[0].strip() if card_number else ""
    expanded_set = expand_set_name(set_name, card_name)
    set_part = expanded_set.split("(")[0].strip() if expanded_set else ""
    query = " ".join(p for p in [clean_name, num_part, set_part] if p)
    search_url = f"https://www.pricecharting.com/search-products?q={quote_plus(query)}&type=prices"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        )
    }

    html = ""
    final_url = search_url
    try:
        r = requests.get(search_url, headers=headers, timeout=12, allow_redirects=True)
        if r.status_code == 200:
            html = r.text
            final_url = r.url
        elif r.status_code in (403, 429):
            final_url, html, _ = fetch_html_with_playwright(search_url, visible=False, max_wait_s=10)
    except requests.RequestException:
        pass

    if not html:
        return {
            "success": False,
            "price": None,
            "source_url": search_url,
            "message": "Could not reach PriceCharting right now.",
        }

    soup = BeautifulSoup(html, "html.parser")

    if "/search-products" in final_url:
        best_href = None
        best_score = -999
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/game/" not in href:
                continue
            sc = score_pricecharting_candidate(href, a.get_text(" ", strip=True), card_name, set_name, card_number)
            if sc > best_score:
                best_score = sc
                best_href = urljoin(final_url, href)
        if best_href:
            try:
                r2 = requests.get(best_href, headers=headers, timeout=12)
                if r2.status_code == 200:
                    soup = BeautifulSoup(r2.text, "html.parser")
                    final_url = r2.url
            except requests.RequestException:
                pass

    # Parse PriceCharting's exact Full Price Guide rows (e.g. "PSA 10", "CGC 10", "CGC 10 Pristine", "BGS 10", "Grade 9.5", "Grade 9", "Ungraded")
    exact_prices = {}
    for tr in soup.find_all("tr"):
        cells = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
        if len(cells) == 2:
            key = cells[0].strip().lower()
            m_price = re.search(r"^\s*\$([\d,]+(?:\.\d{2})?)", cells[1])
            if m_price:
                exact_prices[key] = float(m_price.group(1).replace(",", ""))

    g_low = (grade or "").strip().lower()
    comp_low = (company or "").strip().lower()

    # Build strict priority keys based on Grading Company + Grade
    priority_keys = []
    if "black" in g_low and "10" in g_low:
        priority_keys = ["bgs 10 black", "bgs 10", "psa 10"]
    elif "pristine" in g_low and "10" in g_low:
        if "cgc" in comp_low:
            priority_keys = ["cgc 10 pristine", "cgc 10", "psa 10"]
        else:
            priority_keys = ["bgs 10", "cgc 10 pristine", "psa 10"]
    elif "10" in g_low:
        if "cgc" in comp_low:
            priority_keys = ["cgc 10", "psa 10"]
        elif "beckett" in comp_low or "bgs" in comp_low:
            priority_keys = ["bgs 10", "psa 10"]
        elif "tag" in comp_low:
            priority_keys = ["tag 10", "psa 10", "cgc 10"]
        else:
            priority_keys = ["psa 10", "cgc 10", "bgs 10"]
    elif "9.5" in g_low:
        priority_keys = ["grade 9.5", "bgs 9.5", "cgc 9.5"]
    elif "9" in g_low:
        priority_keys = ["grade 9", "psa 9"]
    elif "8.5" in g_low or "8" in g_low:
        priority_keys = ["grade 8", "psa 8"]
    elif "7.5" in g_low or "7" in g_low:
        priority_keys = ["grade 7", "psa 7"]
    elif "6" in g_low:
        priority_keys = ["grade 6"]
    elif "5" in g_low:
        priority_keys = ["grade 5"]
    else:
        priority_keys = ["ungraded"]

    chosen_price = None
    matched_label = ""
    for pk in priority_keys:
        if pk in exact_prices:
            chosen_price = exact_prices[pk]
            matched_label = pk.upper()
            break

    pc_set = ""
    pc_num = ""
    title_tag = soup.find("title")
    if title_tag and "|" in title_tag.get_text():
        parts = [pt.strip() for pt in title_tag.get_text().split("|")]
        m_num = re.search(r"#([A-Za-z0-9/\-]+)", parts[0])
        if m_num:
            pc_num = m_num.group(1).strip()
        if len(parts) >= 2:
            pc_set = re.sub(r"^(?:Pokemon|Magic|One Piece|Lorcana|Dragon Ball|Yu-Gi-Oh!?)\s+", "", parts[1], flags=re.I).strip()

    if chosen_price is not None:
        return {
            "success": True,
            "price": round(chosen_price, 2),
            "matched_label": matched_label,
            "pricecharting_set": pc_set,
            "pricecharting_number": pc_num,
            "source_url": final_url,
            "message": f"Pulled PriceCharting ({matched_label}): ${chosen_price:,.2f}",
        }

    return {
        "success": False,
        "price": None,
        "source_url": final_url,
        "message": "Could not match that exact grade on PriceCharting. Open the link or enter Market Value manually.",
    }


def resolve_card_status(
    requested_status: str,
    sold_price: Optional[float],
    cert_number: str,
    actual_grade: str,
    listing_url: str,
    explicit_save: bool = False,
) -> str:
    st = (requested_status or "In Grading").strip()
    if st == "Graded":
        st = "Unsold"
    if explicit_save:
        if st == "Sold":
            return "Sold" if sold_price is not None else "Unsold"
        if st in ("Not Submitted", "In Grading", "Unsold", "Hold", "Listed"):
            return st
    if sold_price is not None:
        return "Sold"
    if st == "Sold":
        return "Unsold"
    if st == "Not Submitted":
        return "Not Submitted"
    if st == "In Grading" and (listing_url.strip() or cert_number.strip() or is_valid_grade(actual_grade)):
        return "Unsold"
    return st


class SubmissionCreate(BaseModel):
    title: str
    company: str
    submission_number: str = ""
    submitted_date: str = ""
    status: str = "In Grading"
    submit_channel: str = ""
    notes: str = ""


class SubmissionUpdate(BaseModel):
    title: Optional[str] = None
    company: Optional[str] = None
    submission_number: Optional[str] = None
    submitted_date: Optional[str] = None
    status: Optional[str] = None
    submit_channel: Optional[str] = None
    notes: Optional[str] = None


class CertFetchRequest(BaseModel):
    cert_number: Optional[str] = None
    company: Optional[str] = None
    win_x: Optional[int] = None
    win_y: Optional[int] = None
    win_w: Optional[int] = None
    win_h: Optional[int] = None
    background: bool = False


class OpenLocalPathRequest(BaseModel):
    path: str
    mode: str = "folder"


class ScanUrlUploadRequest(BaseModel):
    side: str = "cert_front"
    image_url: str


class ExtractLinkRequest(BaseModel):
    url: str
    company: str = ""


def parse_opt_float(val: Optional[str]) -> Optional[float]:
    if val is None or str(val).strip() == "":
        return None
    try:
        return float(val)
    except ValueError:
        return None


def parse_float(val: Optional[str], default: float = 0.0) -> float:
    res = parse_opt_float(val)
    return res if res is not None else default


@app.get("/api/stats")
def get_stats():
    conn = get_db()
    try:
        rows = [
            dict(r)
            for r in conn.execute(
                """
                SELECT c.*, s.company AS submission_company, s.title AS submission_title, COALESCE(s.submit_channel, '') AS submit_channel
                FROM cards c
                JOIN submissions s ON s.id = c.submission_id
                """
            ).fetchall()
        ]
        sub_count = conn.execute("SELECT COUNT(*) FROM submissions WHERE COALESCE(submit_channel, '') != 'eBay'").fetchone()[0]

        grading_rows = [r for r in rows if (r.get("submit_channel") or "") != "eBay"]
        total_cards = len(grading_rows)
        completed_count = 0
        grading_count = 0
        not_submitted_count = 0
        sold_count = 0
        not_shipped_subs = conn.execute("SELECT COUNT(*) FROM submissions WHERE status = 'Not Shipped'").fetchone()[0]

        total_acq = 0.0
        total_grading = 0.0
        total_shipping = 0.0
        total_sold_revenue = 0.0
        sold_cards_cost = 0.0
        total_selling_fees = 0.0
        total_market_value_unsold = 0.0
        total_est_profit_unsold = 0.0
        unsold_with_mv_cost = 0.0
        total_card_values = 0.0

        top_card = None
        best_profit = None

        for r in rows:
            status = (r["status"] or "In Grading").strip()
            is_ebay_row = (r.get("submit_channel") or "") == "eBay"
            if not is_ebay_row:
                if status == "Not Submitted":
                    not_submitted_count += 1
                elif status == "In Grading":
                    grading_count += 1
                else:
                    completed_count += 1

            acq = float(r["acquisition_cost"] or 0.0)
            grd = float(r["grading_cost"] or 0.0)
            shp = float(r["shipping_cost"] or 0.0)
            fees = float(r["selling_fees"] or 0.0)

            total_acq += acq
            total_grading += grd
            total_shipping += shp

            if status != "Sold":
                held_val = r.get("market_value") if r.get("market_value") is not None else r.get("initial_graded_value")
                is_graded_card = bool((r.get("actual_grade") or "").strip()) or (status not in ("In Grading", "Not Submitted"))
                if is_graded_card and held_val is not None:
                    total_card_values += float(held_val)
                elif held_val is not None:
                    total_card_values += float(held_val)
                else:
                    total_card_values += acq
                if held_val is not None:
                    total_market_value_unsold += float(held_val)
                    total_est_profit_unsold += float(held_val) - (acq + grd + shp)
                    unsold_with_mv_cost += (acq + grd + shp)

                # Unsold held slab profit uses initial_graded_value, or market_value if initial isn't set
                init_val = r.get("initial_graded_value") if r.get("initial_graded_value") is not None else r.get("market_value")
                if init_val is not None:
                    card_cost = acq + grd + shp
                    card_profit = float(init_val) - card_cost
                    if best_profit is None or card_profit > best_profit:
                        best_profit = card_profit
                        roi_pct = round((card_profit / card_cost) * 100, 1) if card_cost > 0 else 0.0
                        top_card = {
                            "id": r["id"],
                            "submission_id": r["submission_id"],
                            "card_name": r["card_name"],
                            "submission_company": r["submission_company"],
                            "submission_title": r["submission_title"],
                            "actual_grade": r["actual_grade"],
                            "cert_number": r["cert_number"],
                            "total_cost": round(card_cost, 2),
                            "sold_price": round(float(init_val), 2),
                            "profit": round(card_profit, 2),
                            "roi_pct": roi_pct,
                            "is_sold": False,
                            "image": r["cert_front_image"] or r["raw_front_image"] or "",
                            "has_cert_scan": bool(r["cert_front_image"]),
                        }

            if status == "Sold" and r["sold_price"] is not None:
                sold_count += 1
                sp = float(r["sold_price"])
                total_sold_revenue += sp
                total_selling_fees += fees
                card_cost = acq + grd + shp + fees
                sold_cards_cost += card_cost
                card_profit = sp - card_cost

                if best_profit is None or card_profit > best_profit:
                    best_profit = card_profit
                    roi_pct = round((card_profit / card_cost) * 100, 1) if card_cost > 0 else 0.0
                    top_card = {
                        "id": r["id"],
                        "submission_id": r["submission_id"],
                        "card_name": r["card_name"],
                        "submission_company": r["submission_company"],
                        "submission_title": r["submission_title"],
                        "actual_grade": r["actual_grade"],
                        "cert_number": r["cert_number"],
                        "total_cost": round(card_cost, 2),
                        "sold_price": round(sp, 2),
                        "profit": round(card_profit, 2),
                        "roi_pct": roi_pct,
                        "is_sold": True,
                        "image": r["cert_front_image"] or r["raw_front_image"] or "",
                        "has_cert_scan": bool(r["cert_front_image"]),
                    }

        total_collective_cost = total_acq + total_grading + total_shipping
        realized_profit = total_sold_revenue - sold_cards_cost
        net_overall = total_sold_revenue - total_collective_cost - total_selling_fees
        realized_roi_pct = round((realized_profit / sold_cards_cost) * 100, 1) if sold_cards_cost > 0 else 0.0
        unsold_est_roi_pct = round((total_est_profit_unsold / unsold_with_mv_cost) * 100, 1) if unsold_with_mv_cost > 0 else 0.0
        portfolio_value = (total_sold_revenue - total_selling_fees) + total_market_value_unsold
        overall_roi_pct = round(((portfolio_value - total_collective_cost) / total_collective_cost) * 100, 1) if total_collective_cost > 0 else 0.0
        gem_metrics = compute_grade_metrics(grading_rows)

        return {
            "total_submissions": sub_count,
            "total_cards": total_cards,
            "completed_grading": completed_count,
            "still_grading": grading_count,
            "not_submitted_cards": not_submitted_count,
            "not_shipped_subs": not_shipped_subs,
            "sold_cards": sold_count,
            "unsold_est_roi_pct": unsold_est_roi_pct,
            "total_acquisition_cost": round(total_acq, 2),
            "total_grading_cost": round(total_grading, 2),
            "total_shipping_cost": round(total_shipping, 2),
            "total_collective_cost": round(total_collective_cost, 2),
            "total_sold_revenue": round(total_sold_revenue, 2),
            "total_market_value_unsold": round(total_market_value_unsold, 2),
            "total_card_values": round(total_card_values, 2),
            "total_est_profit_unsold": round(total_est_profit_unsold, 2),
            "realized_roi_pct": realized_roi_pct,
            "overall_roi_pct": overall_roi_pct,
            "realized_profit": round(realized_profit, 2),
            "net_overall": round(net_overall, 2),
            "top_profitable_card": top_card,
            **gem_metrics,
        }
    finally:
        conn.close()


@app.get("/api/submissions")
def list_submissions(sort: str = "newest", company: str = "ALL", status: str = "ALL"):
    conn = get_db()
    try:
        subs = conn.execute("SELECT * FROM submissions").fetchall()
        cards = conn.execute("SELECT * FROM cards").fetchall()

        cards_by_sub = {}
        for c in cards:
            cards_by_sub.setdefault(c["submission_id"], []).append(dict(c))

        result = []
        for s in subs:
            s_dict = dict(s)
            if company != "ALL" and s_dict["company"] != company:
                continue
            if status != "ALL" and s_dict["status"] != status:
                continue

            sub_cards = cards_by_sub.get(s_dict["id"], [])
            card_count = len(sub_cards)
            not_submitted = sum(1 for c in sub_cards if c["status"] == "Not Submitted")
            completed = sum(1 for c in sub_cards if c["status"] not in ("In Grading", "Not Submitted"))
            grading = sum(1 for c in sub_cards if c["status"] == "In Grading")
            sold = sum(1 for c in sub_cards if c["status"] == "Sold")

            total_cost = sum(
                float(c["acquisition_cost"] or 0) + float(c["grading_cost"] or 0) + float(c["shipping_cost"] or 0)
                for c in sub_cards
            )
            total_sold = sum(
                float(c["sold_price"]) for c in sub_cards if c["status"] == "Sold" and c["sold_price"] is not None
            )
            sold_cost = sum(
                float(c["acquisition_cost"] or 0)
                + float(c["grading_cost"] or 0)
                + float(c["shipping_cost"] or 0)
                + float(c["selling_fees"] or 0)
                for c in sub_cards
                if c["status"] == "Sold" and c["sold_price"] is not None
            )
            realized_profit = total_sold - sold_cost
            gem_metrics = compute_grade_metrics(sub_cards)

            def _thumb_val_key(c_item: dict) -> float:
                for k in ("market_value", "sold_price", "initial_graded_value"):
                    if c_item.get(k) is not None:
                        try:
                            return float(c_item[k])
                        except (TypeError, ValueError):
                            pass
                return -1.0

            preview_images = []
            for c in sorted(sub_cards, key=_thumb_val_key, reverse=True):
                img = c.get("cert_front_image") or c.get("raw_front_image")
                if img:
                    preview_images.append(img)
                if len(preview_images) >= 35:
                    break

            s_dict.update(
                {
                    "card_count": card_count,
                    "completed_count": completed,
                    "grading_count": grading,
                    "not_submitted_count": not_submitted,
                    "sold_count": sold,
                    "total_cost": round(total_cost, 2),
                    "total_sold": round(total_sold, 2),
                    "realized_profit": round(realized_profit, 2),
                    "preview_images": preview_images,
                    "gem_rate": gem_metrics["gem_rate"],
                    "gem_10_count": gem_metrics["gem_10_count"],
                    "graded_with_grade_count": gem_metrics["graded_with_grade_count"],
                }
            )
            result.append(s_dict)

        if sort == "oldest":
            result.sort(key=lambda x: (x["submitted_date"] or "9999-99-99", x["id"]))
        elif sort == "company":
            result.sort(key=lambda x: (x["company"], x["submitted_date"] or "0000-00-00"))
        elif sort == "cost_desc":
            result.sort(key=lambda x: x["total_cost"], reverse=True)
        elif sort == "profit_desc":
            result.sort(key=lambda x: x["realized_profit"], reverse=True)
        elif sort == "cards_desc":
            result.sort(key=lambda x: x["card_count"], reverse=True)
        elif sort == "gem_desc":
            result.sort(key=lambda x: x["gem_rate"], reverse=True)
        else:
            result.sort(key=lambda x: (x["submitted_date"] or "0000-00-00", x["id"]), reverse=True)

        return result
    finally:
        conn.close()


@app.post("/api/submissions")
def create_submission(payload: SubmissionCreate):
    if payload.company not in ("PSA", "CGC", "Beckett", "TAG"):
        raise HTTPException(status_code=400, detail="Company must be PSA, CGC, or Beckett.")
    title = payload.title.strip() or f"{payload.company} Submission"
    ts = now_iso()
    conn = get_db()
    try:
        cur = conn.execute(
            """
            INSERT INTO submissions (title, company, submission_number, submitted_date, status, submit_channel, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                title,
                payload.company,
                payload.submission_number.strip(),
                payload.submitted_date.strip() or datetime.now().strftime("%Y-%m-%d"),
                payload.status.strip() or "In Grading",
                (payload.submit_channel or "").strip(),
                payload.notes.strip(),
                ts,
                ts,
            ),
        )
        conn.commit()
        sub_id = cur.lastrowid
        row = conn.execute("SELECT * FROM submissions WHERE id = ?", (sub_id,)).fetchone()
        return dict(row)
    finally:
        conn.close()


@app.get("/api/submissions/{sub_id}")
def get_submission(sub_id: int):
    conn = get_db()
    try:
        sub = conn.execute("SELECT * FROM submissions WHERE id = ?", (sub_id,)).fetchone()
        if not sub:
            raise HTTPException(status_code=404, detail="Submission not found.")
        cards = conn.execute(
            "SELECT * FROM cards WHERE submission_id = ? ORDER BY id ASC", (sub_id,)
        ).fetchall()

        sub_cards = [dict(c) for c in cards]
        market_val_total = 0.0
        est_profit_unsold_total = 0.0
        for c in sub_cards:
            cost = float(c["acquisition_cost"] or 0) + float(c["grading_cost"] or 0) + float(c["shipping_cost"] or 0)
            c["total_cost"] = round(cost, 2)
            if c["status"] != "Sold" and c.get("market_value") is not None:
                market_val_total += float(c["market_value"])
            if c["status"] == "Sold" and c["sold_price"] is not None:
                c["profit"] = round(float(c["sold_price"]) - cost - float(c["selling_fees"] or 0), 2)
                c["est_profit"] = None
            else:
                c["profit"] = None
                est_val = c.get("market_value") if c.get("market_value") is not None else c.get("initial_graded_value")
                if est_val is not None:
                    c["est_profit"] = round(float(est_val) - cost, 2)
                    est_profit_unsold_total += c["est_profit"]
                else:
                    c["est_profit"] = None

        sold_cost_sub = sum(
            (c["total_cost"] + float(c.get("selling_fees") or 0))
            for c in sub_cards if c["status"] == "Sold" and c.get("sold_price") is not None
        )
        sold_profit_sub = sum(float(c["profit"]) for c in sub_cards if c.get("profit") is not None)
        unsold_with_est_cost = sum(
            c["total_cost"] for c in sub_cards if c["status"] != "Sold" and c.get("est_profit") is not None
        )
        total_sub_cost = sum(c["total_cost"] for c in sub_cards)
        total_sub_sold_net = sum(
            (float(c["sold_price"]) - float(c.get("selling_fees") or 0))
            for c in sub_cards if c["status"] == "Sold" and c.get("sold_price") is not None
        )
        realized_roi_pct = round((sold_profit_sub / sold_cost_sub) * 100, 1) if sold_cost_sub > 0 else None
        est_roi_pct = round((est_profit_unsold_total / unsold_with_est_cost) * 100, 1) if unsold_with_est_cost > 0 else None
        overall_roi_pct = round(((total_sub_sold_net + market_val_total - total_sub_cost) / total_sub_cost) * 100, 1) if total_sub_cost > 0 else None

        s_dict = dict(sub)
        s_dict["cards"] = sub_cards
        s_dict["total_market_value_unsold"] = round(market_val_total, 2)
        s_dict["total_est_profit_unsold"] = round(est_profit_unsold_total, 2)
        s_dict["realized_roi_pct"] = realized_roi_pct
        s_dict["est_roi_pct"] = est_roi_pct
        s_dict["overall_roi_pct"] = overall_roi_pct
        s_dict.update(compute_grade_metrics(sub_cards))
        return s_dict
    finally:
        conn.close()


@app.put("/api/submissions/{sub_id}")
def update_submission(sub_id: int, payload: SubmissionUpdate):
    conn = get_db()
    try:
        existing = conn.execute("SELECT * FROM submissions WHERE id = ?", (sub_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Submission not found.")

        company = payload.company if payload.company is not None else existing["company"]
        if company not in ("PSA", "CGC", "Beckett", "TAG"):
            raise HTTPException(status_code=400, detail="Company must be PSA, CGC, or Beckett.")

        conn.execute(
            """
            UPDATE submissions
            SET title = ?, company = ?, submission_number = ?, submitted_date = ?, status = ?, submit_channel = ?, notes = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                payload.title.strip() if payload.title is not None else existing["title"],
                company,
                payload.submission_number.strip() if payload.submission_number is not None else existing["submission_number"],
                payload.submitted_date.strip() if payload.submitted_date is not None else existing["submitted_date"],
                payload.status.strip() if payload.status is not None else existing["status"],
                payload.submit_channel.strip() if payload.submit_channel is not None else (existing["submit_channel"] if "submit_channel" in existing.keys() else ""),
                payload.notes.strip() if payload.notes is not None else existing["notes"],
                now_iso(),
                sub_id,
            ),
        )
        conn.commit()
        updated = conn.execute("SELECT * FROM submissions WHERE id = ?", (sub_id,)).fetchone()
        return dict(updated)
    finally:
        conn.close()


@app.delete("/api/submissions/{sub_id}")
def delete_submission(sub_id: int):
    conn = get_db()
    try:
        cards = conn.execute("SELECT raw_front_image, raw_back_image, cert_front_image, cert_back_image FROM cards WHERE submission_id = ?", (sub_id,)).fetchall()
        for c in cards:
            for col in ("raw_front_image", "raw_back_image", "cert_front_image", "cert_back_image"):
                delete_upload_if_local(c[col])
        conn.execute("DELETE FROM submissions WHERE id = ?", (sub_id,))
        conn.commit()
        return {"deleted": True}
    finally:
        conn.close()


@app.post("/api/submissions/{sub_id}/cards")
def add_card_to_submission(
    sub_id: int,
    card_name: str = Form(""),
    card_year: str = Form(""),
    set_name: str = Form(""),
    card_number: str = Form(""),
    acquisition_cost: str = Form("0"),
    grading_cost: str = Form("0"),
    shipping_cost: str = Form("0"),
    pred_corners: str = Form(""),
    pred_edges: str = Form(""),
    pred_centering: str = Form(""),
    pred_surface: str = Form(""),
    cert_number: str = Form(""),
    fanatics_url: str = Form(""),
    pred_overall: str = Form(""),
    notes: str = Form(""),
    quantity: int = Form(1),
    raw_front: Optional[UploadFile] = File(None),
    raw_back: Optional[UploadFile] = File(None),
):
    conn = get_db()
    try:
        sub = conn.execute("SELECT * FROM submissions WHERE id = ?", (sub_id,)).fetchone()
        if not sub:
            raise HTTPException(status_code=404, detail="Submission not found.")

        raw_front_path = save_upload_file(raw_front, RAW_UPLOADS_DIR)
        raw_back_path = save_upload_file(raw_back, RAW_UPLOADS_DIR)
        use_name = card_name.strip()
        use_year = card_year.strip()
        use_set = set_name.strip()
        use_num = card_number.strip()
        use_grade = ""
        cert_front_path = ""
        cert_back_path = ""
        cert_clean = extract_cert_number(cert_number)
        link_clean = fanatics_url.strip()

        if not use_name and not link_clean and not cert_clean:
            raise HTTPException(status_code=400, detail="Enter a Card Name, Graded Card Link, or Cert #.")

        if link_clean and (not use_name or not cert_clean):
            try:
                ext = extract_info_from_graded_url(link_clean, sub["company"])
                if not use_name and ext.get("card_name"):
                    use_name = ext["card_name"].strip()
                if not use_year and ext.get("card_year"):
                    use_year = ext["card_year"].strip()
                if not use_set and ext.get("set_name"):
                    use_set = ext["set_name"].strip()
                if not use_num and ext.get("card_number"):
                    use_num = ext["card_number"].strip()
                if not cert_clean and ext.get("cert_number"):
                    cert_clean = extract_cert_number(ext["cert_number"])
                if ext.get("actual_grade"):
                    use_grade = ext["actual_grade"].strip()
                if ext.get("front_img_url"):
                    cert_front_path = download_remote_image(ext["front_img_url"], CERT_UPLOADS_DIR)
                if ext.get("back_img_url"):
                    cert_back_path = download_remote_image(ext["back_img_url"], CERT_UPLOADS_DIR)
            except Exception:
                pass

        if cert_clean and (not card_name.strip() or not use_name):
            try:
                c_res = fetch_cert_scans_from_web(sub["company"], cert_clean)
                if not use_name and c_res.get("official_name"):
                    use_name = c_res["official_name"].strip()
                if not use_year and c_res.get("official_year"):
                    use_year = c_res["official_year"].strip()
                if not use_set and c_res.get("official_set"):
                    use_set = c_res["official_set"].strip()
                if not use_num and c_res.get("official_number"):
                    use_num = c_res["official_number"].strip()
                if not use_grade and c_res.get("detected_grade"):
                    use_grade = c_res["detected_grade"].strip()
                if not cert_front_path and c_res.get("front_image"):
                    cert_front_path = c_res["front_image"]
                if not cert_back_path and c_res.get("back_image"):
                    cert_back_path = c_res["back_image"]
            except Exception:
                pass

        if not use_name:
            use_name = f"{sub['company']} Cert #{cert_clean}" if cert_clean else "Graded Card"

        cert_url = build_cert_url(sub["company"], cert_clean) if cert_clean else ""
        default_st = "Not Submitted" if (sub["status"] or "") == "Not Shipped" else "In Grading"
        card_status = resolve_card_status(default_st, None, cert_clean, use_grade, link_clean)
        ts = now_iso()

        qty = max(1, min(200, int(quantity or 1)))
        first_card_id = None
        for idx in range(qty):
            rf = raw_front_path if idx == 0 else copy_local_upload(raw_front_path)
            rb = raw_back_path if idx == 0 else copy_local_upload(raw_back_path)
            cf = cert_front_path if idx == 0 else copy_local_upload(cert_front_path)
            cb = cert_back_path if idx == 0 else copy_local_upload(cert_back_path)
            cur = conn.execute(
                """
                INSERT INTO cards (
                    submission_id, card_name, card_year, set_name, card_number,
                    acquisition_cost, grading_cost, shipping_cost,
                    pred_corners, pred_edges, pred_centering, pred_surface, pred_overall,
                    raw_front_image, raw_back_image,
                    cert_number, cert_front_image, cert_back_image, cert_lookup_url,
                    actual_grade, status, fanatics_url, notes,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sub_id,
                    use_name,
                    use_year,
                    use_set,
                    use_num,
                    parse_float(acquisition_cost, 0.0),
                    parse_float(grading_cost, 0.0),
                    parse_float(shipping_cost, 0.0),
                    parse_opt_float(pred_corners),
                    parse_opt_float(pred_edges),
                    parse_opt_float(pred_centering),
                    parse_opt_float(pred_surface),
                    pred_overall.strip(),
                    rf,
                    rb,
                    cert_clean,
                    cf,
                    cb,
                    cert_url,
                    use_grade,
                    card_status,
                    link_clean,
                    notes.strip(),
                    ts,
                    ts,
                ),
            )
            if first_card_id is None:
                first_card_id = cur.lastrowid
        conn.commit()
        row = conn.execute("SELECT * FROM cards WHERE id = ?", (first_card_id,)).fetchone()
        res = dict(row)
        res["added_count"] = qty
        return res
    finally:
        conn.close()


@app.get("/api/cards/{card_id}")
def get_card(card_id: int):
    conn = get_db()
    try:
        card = conn.execute(
            """
            SELECT c.*, s.company AS submission_company, s.title AS submission_title
            FROM cards c
            JOIN submissions s ON s.id = c.submission_id
            WHERE c.id = ?
            """,
            (card_id,),
        ).fetchone()
        if not card:
            raise HTTPException(status_code=404, detail="Card not found.")
        c_dict = dict(card)
        cost = float(c_dict["acquisition_cost"] or 0) + float(c_dict["grading_cost"] or 0) + float(c_dict["shipping_cost"] or 0)
        c_dict["total_cost"] = round(cost, 2)
        if c_dict["status"] == "Sold" and c_dict["sold_price"] is not None:
            c_dict["profit"] = round(float(c_dict["sold_price"]) - cost - float(c_dict["selling_fees"] or 0), 2)
            c_dict["est_profit"] = None
        else:
            c_dict["profit"] = None
            est_val = c_dict.get("market_value") if c_dict.get("market_value") is not None else c_dict.get("initial_graded_value")
            c_dict["est_profit"] = round(float(est_val) - cost, 2) if est_val is not None else None
        return c_dict
    finally:
        conn.close()


@app.put("/api/cards/{card_id}")
def update_card(
    card_id: int,
    card_name: str = Form(...),
    card_year: str = Form(""),
    set_name: str = Form(""),
    card_number: str = Form(""),
    acquisition_cost: str = Form("0"),
    grading_cost: str = Form("0"),
    shipping_cost: str = Form("0"),
    pred_corners: str = Form(""),
    pred_edges: str = Form(""),
    pred_centering: str = Form(""),
    pred_surface: str = Form(""),
    cert_number: str = Form(""),
    actual_grade: str = Form(""),
    actual_subgrades: str = Form(""),
    status: str = Form("In Grading"),
    fanatics_url: str = Form(""),
    sold_price: str = Form(""),
    selling_fees: str = Form("0"),
    sold_date: str = Form(""),
    initial_graded_value: str = Form(""),
    market_value: str = Form(""),
    pred_overall: str = Form(""),
    curated_date: str = Form(""),
    notes: str = Form(""),
    pricecharting_url: str = Form(""),
    cert_front_url_manual: str = Form(""),
    cert_back_url_manual: str = Form(""),
    raw_front: Optional[UploadFile] = File(None),
    raw_back: Optional[UploadFile] = File(None),
    cert_front: Optional[UploadFile] = File(None),
    cert_back: Optional[UploadFile] = File(None),
):
    conn = get_db()
    try:
        existing = conn.execute(
            """
            SELECT c.*, s.company AS submission_company
            FROM cards c JOIN submissions s ON s.id = c.submission_id
            WHERE c.id = ?
            """,
            (card_id,),
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Card not found.")

        raw_front_path = existing["raw_front_image"]
        if raw_front and raw_front.filename:
            delete_upload_if_local(raw_front_path)
            raw_front_path = save_upload_file(raw_front, RAW_UPLOADS_DIR)

        raw_back_path = existing["raw_back_image"]
        if raw_back and raw_back.filename:
            delete_upload_if_local(raw_back_path)
            raw_back_path = save_upload_file(raw_back, RAW_UPLOADS_DIR)

        cert_front_path = existing["cert_front_image"]
        if cert_front and cert_front.filename:
            delete_upload_if_local(cert_front_path)
            cert_front_path = save_upload_file(cert_front, CERT_UPLOADS_DIR)
        elif cert_front_url_manual.strip():
            cert_front_path = cert_front_url_manual.strip()

        cert_back_path = existing["cert_back_image"]
        if cert_back and cert_back.filename:
            delete_upload_if_local(cert_back_path)
            cert_back_path = save_upload_file(cert_back, CERT_UPLOADS_DIR)
        elif cert_back_url_manual.strip():
            cert_back_path = cert_back_url_manual.strip()

        cert_num_clean = extract_cert_number(cert_number)
        cert_changed = bool(cert_num_clean) and cert_num_clean != (existing["cert_number"] or "").strip()
        cert_url = build_cert_url(existing["submission_company"], cert_number) if cert_num_clean else ""
        parsed_sold = parse_opt_float(sold_price)
        parsed_init_val = parse_opt_float(initial_graded_value)
        parsed_mkt = parse_opt_float(market_value)
        mkt_source = existing["market_value_source"]
        mkt_updated = existing["market_value_updated_at"]

        use_name = card_name.strip()
        use_year = card_year.strip()
        use_set = set_name.strip()
        use_num = card_number.strip()
        use_grade = actual_grade.strip()

        # Only auto-pull market value (no browser popup) when cert_number is newly updated and market_value is empty
        if cert_changed and parsed_mkt is None:
            val_res = fetch_graded_market_value(use_name, use_set, use_num, use_grade, existing["submission_company"])
            if val_res.get("success") and val_res.get("price") is not None:
                parsed_mkt = val_res["price"]
                mkt_source = "PriceCharting"
                mkt_updated = now_iso()

        final_status = resolve_card_status(status, parsed_sold, cert_num_clean, use_grade, fanatics_url, explicit_save=True)
        if final_status != "Sold" and status.strip() != "Sold":
            parsed_sold = None

        conn.execute(
            """
            UPDATE cards
            SET card_name = ?, card_year = ?, set_name = ?, card_number = ?,
                acquisition_cost = ?, grading_cost = ?, shipping_cost = ?,
                pred_corners = ?, pred_edges = ?, pred_centering = ?, pred_surface = ?, pred_overall = ?,
                raw_front_image = ?, raw_back_image = ?,
                cert_number = ?, cert_front_image = ?, cert_back_image = ?, cert_lookup_url = ?,
                actual_grade = ?, actual_subgrades = ?, status = ?,
                fanatics_url = ?, sold_price = ?, selling_fees = ?, sold_date = ?,
                initial_graded_value = ?, market_value = ?, market_value_source = ?, market_value_updated_at = ?,
                pricecharting_url = ?, curated_date = ?, notes = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                use_name,
                use_year,
                use_set,
                use_num,
                parse_float(acquisition_cost, 0.0),
                parse_float(grading_cost, 0.0),
                parse_float(shipping_cost, 0.0),
                parse_opt_float(pred_corners),
                parse_opt_float(pred_edges),
                parse_opt_float(pred_centering),
                parse_opt_float(pred_surface),
                pred_overall.strip(),
                raw_front_path,
                raw_back_path,
                cert_num_clean,
                cert_front_path,
                cert_back_path,
                cert_url,
                use_grade,
                actual_subgrades.strip(),
                final_status,
                fanatics_url.strip(),
                parsed_sold,
                parse_float(selling_fees, 0.0),
                sold_date.strip(),
                parsed_init_val,
                parsed_mkt,
                mkt_source,
                mkt_updated,
                pricecharting_url.strip(),
                curated_date.strip(),
                notes.strip(),
                now_iso(),
                card_id,
            ),
        )
        conn.commit()
        return get_card(card_id)
    finally:
        conn.close()


class PullFromLinkRequest(BaseModel):
    url: str
    win_x: Optional[int] = None
    win_y: Optional[int] = None
    win_w: Optional[int] = None
    win_h: Optional[int] = None


@app.post("/api/open-browser-session")
def open_browser_session(req: PullFromLinkRequest):
    url = (req.url or "").strip() or "https://www.fanaticscollect.com/"
    pw_profile = str((DATA_DIR / "pw_profile").resolve())
    pop_w, pop_h, pop_x, pop_y = 1000, 780, None, None
    if req.win_w and req.win_h:
        wx, wy, ww, wh = int(req.win_x or 0), int(req.win_y or 0), int(req.win_w), int(req.win_h)
        pop_w = min(1020, max(700, ww - 60))
        pop_h = min(800, max(580, wh - 80))
        pop_x = wx + max(20, (ww - pop_w) // 2)
        pop_y = wy + max(20, (wh - pop_h) // 2)
    extra_args = ["--disable-blink-features=AutomationControlled", f"--window-size={pop_w},{pop_h}"]
    if pop_x is not None and pop_y is not None:
        extra_args.append(f"--window-position={pop_x},{pop_y}")
    helper = f"""
import time
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    ctx = None
    for ch in ("msedge", "chrome", None):
        try:
            kw = {{"headless": False, "args": {json.dumps(extra_args)}, "viewport": {{"width": {pop_w - 24}, "height": {pop_h - 90}}}}}
            if ch: kw["channel"] = ch
            ctx = p.chromium.launch_persistent_context({json.dumps(pw_profile)}, **kw)
            break
        except Exception:
            pass
    if ctx:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto({json.dumps(url)}, wait_until="domcontentloaded", timeout=25000)
        while len(ctx.pages) > 0:
            try:
                if ctx.pages[0].is_closed():
                    break
            except Exception:
                break
            time.sleep(0.5)
        try:
            ctx.close()
        except Exception:
            pass
"""
    try:
        subprocess.Popen([sys.executable, "-c", helper])
        return {"opened": True, "message": "Opened local Vault browser! Sign into Fanatics Collect, then close that window when finished."}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not open browser session: {exc}")


@app.post("/api/cards/{card_id}/fetch-cert")
def fetch_card_cert(card_id: int, payload: CertFetchRequest):
    conn = get_db()
    try:
        card = conn.execute(
            """
            SELECT c.*, s.company AS submission_company
            FROM cards c JOIN submissions s ON s.id = c.submission_id
            WHERE c.id = ?
            """,
            (card_id,),
        ).fetchone()
        if not card:
            raise HTTPException(status_code=404, detail="Card not found.")

        raw_input_cert = (payload.cert_number if payload.cert_number is not None else card["cert_number"] or "").strip()
        if raw_input_cert.startswith("http://") or raw_input_cert.startswith("https://"):
            if not any(d in raw_input_cert.lower() for d in ("psacard.com", "cgc", "beckett.com", "taggrading.com")):
                return pull_card_from_link(
                    card_id,
                    PullFromLinkRequest(
                        url=raw_input_cert,
                        win_x=payload.win_x,
                        win_y=payload.win_y,
                        win_w=payload.win_w,
                        win_h=payload.win_h,
                    ),
                )
        cert_num = extract_cert_number(raw_input_cert)
        cert_changed = bool(cert_num) and cert_num != (card["cert_number"] or "").strip()
        company = payload.company or card["submission_company"]

        # Fast-path: if card has a direct PSA CloudFront Images zip URL, download & extract directly (no Playwright)
        zip_url = (card["psa_images_zip_url"] or "").strip() if "psa_images_zip_url" in card.keys() else ""
        if zip_url and not cert_changed:
            try:
                zf_front, zf_back = download_psa_zip_scans(zip_url, cert_num)
                if zf_front or zf_back:
                    new_front = zf_front or card["cert_front_image"]
                    new_back = zf_back or card["cert_back_image"]
                    conn.execute(
                        "UPDATE cards SET cert_front_image = ?, cert_back_image = ?, updated_at = ? WHERE id = ?",
                        (new_front, new_back, now_iso(), card_id),
                    )
                    conn.commit()
                    return {
                        "success": True,
                        "message": "Pulled HQ slab scans from PSA order zip!",
                        "lookup_url": card["cert_lookup_url"] or build_cert_url(company, cert_num),
                        "front_image": new_front,
                        "back_image": new_back,
                        "card": get_card(card_id),
                    }
            except Exception:
                pass

        bounds = (payload.win_x, payload.win_y, payload.win_w, payload.win_h) if payload.win_w and payload.win_h else None
        res = fetch_cert_scans_from_web(company, cert_num, window_bounds=bounds, headless_only=bool(payload.background))

        new_front = res["front_image"] or card["cert_front_image"]
        new_back = res["back_image"] or card["cert_back_image"]
        new_name = res.get("official_name") or card["card_name"]
        new_year = res.get("official_year") or card["card_year"] or ""
        new_set = res.get("official_set") or card["set_name"] or ""
        new_num = res.get("official_number") or card["card_number"] or ""
        new_grade = res.get("detected_grade") or card["actual_grade"] or ""
        new_status = resolve_card_status(card["status"], card["sold_price"], cert_num, new_grade, card["fanatics_url"])

        new_mkt = card["market_value"]
        new_mkt_src = card["market_value_source"]
        new_mkt_at = card["market_value_updated_at"]
        if cert_changed and new_mkt is None:
            val_res = fetch_graded_market_value(new_name, new_set, new_num, new_grade, company)
            if val_res.get("success") and val_res.get("price") is not None:
                new_mkt = val_res["price"]
                new_mkt_src = "PriceCharting"
                new_mkt_at = now_iso()

        conn.execute(
            """
            UPDATE cards
            SET card_name = ?, card_year = ?, set_name = ?, card_number = ?,
                cert_number = ?, cert_lookup_url = ?, cert_front_image = ?, cert_back_image = ?,
                actual_grade = ?, status = ?, market_value = ?, market_value_source = ?,
                market_value_updated_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                new_name,
                new_year,
                new_set,
                new_num,
                cert_num,
                res["lookup_url"],
                new_front,
                new_back,
                new_grade,
                new_status,
                new_mkt,
                new_mkt_src,
                new_mkt_at,
                now_iso(),
                card_id,
            ),
        )
        conn.commit()
        res["card"] = get_card(card_id)
        return res
    finally:
        conn.close()


@app.post("/api/cards/{card_id}/upload-scan")
def upload_single_scan(
    card_id: int,
    side: str = Form("front"),
    file: UploadFile = File(...),
):
    conn = get_db()
    try:
        card = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
        if not card:
            raise HTTPException(status_code=404, detail="Card not found.")
        col_map = {
            "raw_front": ("raw_front_image", RAW_UPLOADS_DIR),
            "raw_back": ("raw_back_image", RAW_UPLOADS_DIR),
            "front": ("cert_front_image", CERT_UPLOADS_DIR),
            "cert_front": ("cert_front_image", CERT_UPLOADS_DIR),
            "back": ("cert_back_image", CERT_UPLOADS_DIR),
            "cert_back": ("cert_back_image", CERT_UPLOADS_DIR),
        }
        col, target_dir = col_map.get(side, ("cert_front_image", CERT_UPLOADS_DIR))
        delete_upload_if_local(card[col])
        saved = save_upload_file(file, target_dir)
        conn.execute(f"UPDATE cards SET {col} = ?, updated_at = ? WHERE id = ?", (saved, now_iso(), card_id))
        conn.commit()
        return {"card": get_card(card_id)}
    finally:
        conn.close()


@app.post("/api/cards/{card_id}/upload-scan-url")
def upload_scan_from_url(card_id: int, req: ScanUrlUploadRequest):
    img_url = (req.image_url or "").strip()
    if not img_url:
        raise HTTPException(status_code=400, detail="No image URL provided.")

    conn = get_db()
    try:
        card = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
        if not card:
            raise HTTPException(status_code=404, detail="Card not found.")

        col_map = {
            "raw_front": ("raw_front_image", RAW_UPLOADS_DIR),
            "raw_back": ("raw_back_image", RAW_UPLOADS_DIR),
            "front": ("cert_front_image", CERT_UPLOADS_DIR),
            "cert_front": ("cert_front_image", CERT_UPLOADS_DIR),
            "back": ("cert_back_image", CERT_UPLOADS_DIR),
            "cert_back": ("cert_back_image", CERT_UPLOADS_DIR),
        }
        col, target_dir = col_map.get(req.side, ("cert_front_image", CERT_UPLOADS_DIR))

        raw_bytes = None
        ext = ".jpg"
        if img_url.startswith("data:image/"):
            import base64
            header, b64 = img_url.split(",", 1)
            raw_bytes = base64.b64decode(b64)
            if "png" in header:
                ext = ".png"
            elif "webp" in header:
                ext = ".webp"
        elif img_url.startswith("http://") or img_url.startswith("https://"):
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0.0.0 Safari/537.36"
                ),
                "Referer": img_url,
            }
            resp = requests.get(img_url, headers=headers, timeout=12)
            resp.raise_for_status()
            raw_bytes = resp.content
            ctype = resp.headers.get("Content-Type", "").lower()
            if "png" in ctype or img_url.lower().endswith(".png"):
                ext = ".png"
            elif "webp" in ctype or img_url.lower().endswith(".webp"):
                ext = ".webp"
        else:
            raise HTTPException(status_code=400, detail="Unsupported image URL format.")

        delete_upload_if_local(card[col])
        if target_dir == RAW_UPLOADS_DIR:
            filename = f"{uuid.uuid4().hex}.webp"
            dest = target_dir / filename
            try:
                with Image.open(io.BytesIO(raw_bytes)) as im:
                    im = ImageOps.exif_transpose(im)
                    if im.mode not in ("RGB", "RGBA"):
                        im = im.convert("RGB")
                    im.save(dest, format="WEBP", quality=85, method=6)
                saved = f"raw/{filename}"
            except Exception:
                fallback_name = f"{uuid.uuid4().hex}{ext}"
                (target_dir / fallback_name).write_bytes(raw_bytes)
                saved = f"raw/{fallback_name}"
        else:
            filename = f"{uuid.uuid4().hex}{ext}"
            (target_dir / filename).write_bytes(raw_bytes)
            saved = f"certs/{filename}"

        conn.execute(f"UPDATE cards SET {col} = ?, updated_at = ? WHERE id = ?", (saved, now_iso(), card_id))
        conn.commit()
        return {"card": get_card(card_id)}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not download dragged image: {exc}")
    finally:
        conn.close()


def extract_info_from_graded_url(url: str, company_hint: str = "") -> dict:
    u = (url or "").strip()
    out = {
        "cert_number": "",
        "detected_company": company_hint,
        "actual_grade": "",
        "card_name": "",
        "card_year": "",
        "set_name": "",
        "card_number": "",
        "source": "url",
    }
    if not u:
        return out

    low = u.lower()
    if "psacard.com" in low:
        out["detected_company"] = "PSA"
        m = re.search(r"/cert/(\d{5,12})", u, re.I)
        if m:
            out["cert_number"] = m.group(1)
    elif "cgc" in low:
        out["detected_company"] = "CGC"
        m = re.search(r"/(\d{7,12})(?:[/?#]|$)", u)
        if m:
            out["cert_number"] = m.group(1)
    elif "beckett.com" in low:
        out["detected_company"] = "Beckett"
        m = re.search(r"item_id=(\d{5,12})", u, re.I)
        if m:
            out["cert_number"] = m.group(1)

    if not out["cert_number"]:
        m_cert = re.search(r"(?:cert(?:ificate)?[-_#/=]?|item_id=)(\d{6,12})", u, re.I)
        if m_cert:
            out["cert_number"] = m_cert.group(1)

    # Fetch page HTML silently (no popup) to extract cert #, grade, year, set, or card title
    if u.startswith("http://") or u.startswith("https://"):
        try:
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            }
            resp = requests.get(u, headers=headers, timeout=8, allow_redirects=True)
            if resp.status_code == 200 and "just a moment" not in resp.text.lower():
                soup = BeautifulSoup(resp.text, "html.parser")
                title_txt = soup.title.get_text(" ", strip=True) if soup.title else ""
                meta_desc = ""
                md = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
                if md and md.get("content"):
                    meta_desc = md["content"].strip()
                page_txt = soup.get_text(" ", strip=True)
                combined_txt = f"{title_txt} {meta_desc} {page_txt[:4000]}"

                if not out["cert_number"]:
                    m_pcert = re.search(r"(?:Cert(?:ificate)?\s*(?:#|No\.?|Number)?\s*[:#]?\s*)(\d{6,12})", combined_txt, re.I)
                    if m_pcert:
                        out["cert_number"] = m_pcert.group(1)

                if not out["actual_grade"]:
                    m_g = re.search(r"(PSA|CGC|BGS|Beckett)\s*(Pristine\s*10|Black\s*Label\s*10|Gem\s*Mint\s*10|10|9\.5|9|8\.5|8|7\.5|7|6\.5|6|5\.5|5|4\.5|4|3\.5|3|2\.5|2|1\.5|1)", combined_txt, re.I)
                    if m_g:
                        if not out["detected_company"]:
                            out["detected_company"] = "Beckett" if m_g.group(1).lower() in ("bgs", "beckett") else m_g.group(1).upper()
                        g_raw = m_g.group(2).strip()
                        if "pristine" in g_raw.lower():
                            out["actual_grade"] = "Pristine 10"
                        elif "black" in g_raw.lower():
                            out["actual_grade"] = "Black Label 10"
                        else:
                            m_num = re.search(r"(10|9\.5|9|8\.5|8|7\.5|7|6\.5|6|5\.5|5|4\.5|4|3\.5|3|2\.5|2|1\.5|1)", g_raw)
                            out["actual_grade"] = m_num.group(1) if m_num else g_raw

                if not out["card_year"]:
                    m_yr = re.search(r"(199\d|20[0-3]\d)", title_txt or meta_desc)
                    if m_yr:
                        out["card_year"] = m_yr.group(1)

                if not out["card_number"]:
                    m_cn = re.search(r"#(\d{1,4}(?:/\d{1,4})?)", title_txt or meta_desc)
                    if m_cn:
                        out["card_number"] = m_cn.group(1)

                if out["detected_company"] in ("PSA", "CGC", "Beckett", "TAG") and out["cert_number"]:
                    parsed = parse_cert_details_from_html(resp.text, out["detected_company"], resp.url, out["cert_number"])
                    out["card_name"] = parsed.get("official_name") or out["card_name"]
                    out["card_year"] = parsed.get("official_year") or out["card_year"]
                    out["set_name"] = parsed.get("official_set") or out["set_name"]
                    out["card_number"] = parsed.get("official_number") or out["card_number"]
                    out["actual_grade"] = parsed.get("detected_grade") or out["actual_grade"]
                out["source"] = "page"
        except Exception:
            pass
    return out


@app.post("/api/extract-link-info")
def extract_link_info(req: ExtractLinkRequest):
    return extract_info_from_graded_url(req.url, req.company)


def parse_marketplace_html(html: str, page_url: str, company_hint: str = "") -> dict:
    soup = BeautifulSoup(html, "html.parser")
    og_title_tag = soup.find("meta", attrs={"property": "og:title"})
    h1_tag = soup.find("h1")
    title_txt = (
        (og_title_tag.get("content") if og_title_tag else "")
        or (h1_tag.get_text(" ", strip=True) if h1_tag else "")
        or (soup.title.get_text(" ", strip=True) if soup.title else "")
    ).strip()
    page_txt = soup.get_text(" ", strip=True)
    combined = f"{title_txt} {page_txt[:6000]}"

    out = {
        "cert_number": "",
        "detected_company": company_hint,
        "actual_grade": "",
        "card_name": "",
        "card_year": "",
        "set_name": "",
        "card_number": "",
        "front_img_url": "",
        "back_img_url": "",
    }

    for pat in [
        r'"(?:certNumber|certificationNumber|cert_number|certificateNumber|psaCert|cgcCert)"\s*:\s*"?(\d{6,12})"?',
        r"(?:Cert(?:ification|ificate)?\s*(?:#|No\.?|Number)?\s*[:#]?\s*)(\d{6,12})",
        r"(?:Serial\s*(?:#|No\.?)?\s*[:#]?\s*)(\d{6,12})",
    ]:
        m = re.search(pat, html if pat.startswith('"') else combined, re.I)
        if m:
            out["cert_number"] = m.group(1)
            break

    m_g = re.search(
        r"\b(PSA|CGC|BGS|Beckett|TAG)\s*(Pristine\s*10|Black\s*Label\s*10|Gem\s*Mint\s*10|10|9\.5|9|8\.5|8|7\.5|7|6\.5|6|5\.5|5|4\.5|4|3\.5|3|2\.5|2|1\.5|1)\b",
        combined,
        re.I,
    )
    if m_g:
        comp_raw = m_g.group(1).upper()
        out["detected_company"] = "Beckett" if comp_raw in ("BGS", "BECKETT") else comp_raw
        g_raw = m_g.group(2).strip()
        if "pristine" in g_raw.lower():
            out["actual_grade"] = "Pristine 10"
        elif "black" in g_raw.lower():
            out["actual_grade"] = "Black Label 10"
        else:
            m_num = re.search(r"(10|9\.5|9|8\.5|8|7\.5|7|6\.5|6|5\.5|5|4\.5|4|3\.5|3|2\.5|2|1\.5|1)", g_raw)
            out["actual_grade"] = m_num.group(1) if m_num else g_raw

    if title_txt:
        m_yr = re.search(r"\b(199\d|20[0-3]\d)\b", title_txt)
        if m_yr:
            out["card_year"] = m_yr.group(1)
        m_cn = re.search(r"#([A-Za-z0-9/\-]+)", title_txt)
        if m_cn:
            out["card_number"] = m_cn.group(1)
        clean_title = re.sub(r"\s*\|\s*.*$", "", title_txt)
        clean_title = re.sub(r"\b(?:PSA|CGC|BGS|Beckett)\s*(?:Pristine|Gem\s*Mint|Black\s*Label)?\s*(?:10|9\.5|9|8\.5|8|7\.5|7|6\.5|6|5\.5|5|4\.5|4|3\.5|3|2\.5|2|1\.5|1)\b", "", clean_title, flags=re.I)
        clean_title = re.sub(r"\s+", " ", clean_title).strip(" -|•")
        if clean_title and len(clean_title) <= 120:
            out["card_name"] = clean_title
            out["set_name"] = expand_set_name("", clean_title)

    cert_parsed = parse_cert_details_from_html(html, out["detected_company"] or company_hint, page_url, out["cert_number"] or "slab")
    out["front_img_url"] = cert_parsed.get("front_img_url") or ""
    out["back_img_url"] = cert_parsed.get("back_img_url") or ""
    out["card_name"] = cert_parsed.get("official_name") or out["card_name"]
    out["card_year"] = cert_parsed.get("official_year") or out["card_year"]
    out["set_name"] = cert_parsed.get("official_set") or out["set_name"]
    out["card_number"] = cert_parsed.get("official_number") or out["card_number"]
    out["actual_grade"] = cert_parsed.get("detected_grade") or out["actual_grade"]
    return out


@app.post("/api/cards/{card_id}/pull-from-link")
def pull_card_from_link(card_id: int, req: PullFromLinkRequest):
    url = (req.url or "").strip()
    if not url or not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(status_code=400, detail="Please enter a valid http(s) link.")

    conn = get_db()
    try:
        card = conn.execute(
            """
            SELECT c.*, s.company AS submission_company
            FROM cards c JOIN submissions s ON s.id = c.submission_id
            WHERE c.id = ?
            """,
            (card_id,),
        ).fetchone()
        if not card:
            raise HTTPException(status_code=404, detail="Card not found.")

        bounds = (req.win_x, req.win_y, req.win_w, req.win_h) if req.win_w and req.win_h else None
        info = extract_info_from_graded_url(url, card["submission_company"])
        front_url = ""
        back_url = ""

        if not info.get("cert_number") or not info.get("card_name"):
            pw_url, pw_html, _ = fetch_html_with_playwright(url, visible=True, max_wait_s=90, window_bounds=bounds, wait_for_login=True)
            if pw_html and "just a moment" not in pw_html.lower():
                parsed_pw = parse_marketplace_html(pw_html, pw_url, info.get("detected_company") or card["submission_company"])
                for k in ("cert_number", "detected_company", "actual_grade", "card_name", "card_year", "set_name", "card_number"):
                    if parsed_pw.get(k) and not info.get(k):
                        info[k] = parsed_pw[k]
                front_url = parsed_pw.get("front_img_url") or ""
                back_url = parsed_pw.get("back_img_url") or ""

        cert_num = info.get("cert_number") or card["cert_number"] or ""
        company = info.get("detected_company") or card["submission_company"]

        saved_front = ""
        saved_back = ""
        if front_url:
            try:
                saved_front = download_image_to_certs(front_url, cert_num or f"link_{card_id}", "front")
            except Exception:
                saved_front = ""
        if back_url:
            try:
                saved_back = download_image_to_certs(back_url, cert_num or f"link_{card_id}", "back")
            except Exception:
                saved_back = ""

        cert_res = {}
        if cert_num and (not saved_front or not info.get("card_name")):
            cert_res = fetch_cert_scans_from_web(company, cert_num, window_bounds=bounds)

        new_front = cert_res.get("front_image") or saved_front or card["cert_front_image"]
        new_back = cert_res.get("back_image") or saved_back or card["cert_back_image"]
        new_name = cert_res.get("official_name") or info.get("card_name") or card["card_name"]
        new_year = cert_res.get("official_year") or info.get("card_year") or card["card_year"] or ""
        new_set = cert_res.get("official_set") or info.get("set_name") or card["set_name"] or ""
        new_num = cert_res.get("official_number") or info.get("card_number") or card["card_number"] or ""
        new_grade = cert_res.get("detected_grade") or info.get("actual_grade") or card["actual_grade"] or ""
        lookup_url = cert_res.get("lookup_url") or (build_cert_url(company, cert_num) if cert_num else card["cert_lookup_url"])
        new_status = resolve_card_status(card["status"], card["sold_price"], cert_num, new_grade, url)

        conn.execute(
            """
            UPDATE cards
            SET card_name = ?, card_year = ?, set_name = ?, card_number = ?,
                cert_number = ?, cert_lookup_url = ?, cert_front_image = ?, cert_back_image = ?,
                actual_grade = ?, status = ?, fanatics_url = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                new_name,
                new_year,
                new_set,
                new_num,
                cert_num,
                lookup_url,
                new_front,
                new_back,
                new_grade,
                new_status,
                url,
                now_iso(),
                card_id,
            ),
        )
        conn.commit()
        updated = get_card(card_id)
        got_anything = bool(saved_front or cert_res.get("success") or info.get("card_name") or info.get("actual_grade") or cert_num)
        msg = cert_res.get("message") if cert_res.get("success") else (
            "Pulled card data & images from link!" if got_anything else "Saved link, but could not auto-read details from that page."
        )
        return {"success": got_anything, "message": msg, "card": updated}
    finally:
        conn.close()


def score_pokemon_card_back(img_bytes: bytes, filename: str = "") -> float:
    """
    Scores how likely an image is the BACK of a Pokémon card (vs the front artwork).
    English backs have a royal blue border/swirl + central red/white Pokéball.
    Japanese backs have deep navy/indigo + warm gold border/emblem.
    """
    score = 0.0
    fn = (filename or "").lower()
    if any(k in fn for k in ("back", "_b.", "-b.", "rear")):
        score += 15.0
    elif any(k in fn for k in ("front", "_f.", "-f.")):
        score -= 15.0

    try:
        with Image.open(io.BytesIO(img_bytes)) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im = im.resize((96, 134), Image.Resampling.BILINEAR)
            px = im.load()
            w, h = im.size
            total = w * h
            blue_px = 0
            border_blue = 0
            border_total = 0
            gold_px = 0
            center_red = 0
            center_white = 0
            center_total = 0

            for y in range(h):
                in_border_y = y < 18 or y >= h - 18
                in_center_y = 42 <= y <= 92
                for x in range(w):
                    r, g, b = px[x, y]
                    is_border = in_border_y or x < 14 or x >= w - 14
                    is_blue = (b > r + 18 and b > g + 6 and b > 65)
                    is_navy = (b > r + 10 and b > g and 35 <= b <= 150)
                    is_gold = (r > 140 and g > 105 and b < 95 and r > b + 45)
                    if is_blue:
                        blue_px += 1
                    if is_gold:
                        gold_px += 1
                    if is_border:
                        border_total += 1
                        if is_blue or is_navy:
                            border_blue += 1
                    if in_center_y and 26 <= x <= 70:
                        center_total += 1
                        if r > 145 and g < 95 and b < 95:
                            center_red += 1
                        elif r > 195 and g > 195 and b > 195:
                            center_white += 1

            blue_ratio = blue_px / max(1, total)
            border_blue_ratio = border_blue / max(1, border_total)
            gold_ratio = gold_px / max(1, total)
            center_ball_ratio = (center_red + center_white) / max(1, center_total)

            score += blue_ratio * 100.0
            score += border_blue_ratio * 80.0
            if center_red > 15 and center_white > 15:
                score += center_ball_ratio * 35.0
            if border_blue_ratio > 0.35 and gold_ratio > 0.08:
                score += 20.0
    except Exception:
        pass
    return score


def classify_raw_pair(bytes_a: bytes, name_a: str, bytes_b: bytes, name_b: str) -> tuple:
    score_a = score_pokemon_card_back(bytes_a, name_a)
    score_b = score_pokemon_card_back(bytes_b, name_b)
    if score_b >= score_a:
        return (0, 1)  # 0=front, 1=back
    return (1, 0)      # 1=front, 0=back


@app.post("/api/classify-raw-pair")
async def classify_raw_pair_endpoint(files: list[UploadFile] = File(...)):
    if len(files) != 2:
        raise HTTPException(status_code=400, detail="Please drop exactly 2 images (front & back).")
    b0 = await files[0].read()
    b1 = await files[1].read()
    front_idx, back_idx = classify_raw_pair(b0, files[0].filename or "", b1, files[1].filename or "")
    return {"front_index": front_idx, "back_index": back_idx}


@app.post("/api/cards/{card_id}/upload-raw-pair")
async def upload_raw_pair(card_id: int, files: list[UploadFile] = File(...)):
    if len(files) != 2:
        raise HTTPException(status_code=400, detail="Please drop exactly 2 images (front & back).")
    b0 = await files[0].read()
    b1 = await files[1].read()
    front_idx, back_idx = classify_raw_pair(b0, files[0].filename or "", b1, files[1].filename or "")
    raw_items = [(b0, files[0].filename or "img1.png"), (b1, files[1].filename or "img2.png")]
    front_bytes, front_name = raw_items[front_idx]
    back_bytes, back_name = raw_items[back_idx]

    def _save_raw_bytes(raw_bytes: bytes) -> str:
        filename = f"{uuid.uuid4().hex}.webp"
        dest = RAW_UPLOADS_DIR / filename
        try:
            with Image.open(io.BytesIO(raw_bytes)) as img:
                img = ImageOps.exif_transpose(img)
                if img.mode not in ("RGB", "RGBA"):
                    img = img.convert("RGB")
                img.save(dest, format="WEBP", quality=85, method=6)
            return f"raw/{filename}"
        except Exception:
            fallback_name = f"{uuid.uuid4().hex}.png"
            (RAW_UPLOADS_DIR / fallback_name).write_bytes(raw_bytes)
            return f"raw/{fallback_name}"

    conn = get_db()
    try:
        card = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
        if not card:
            raise HTTPException(status_code=404, detail="Card not found.")
        delete_upload_if_local(card["raw_front_image"])
        delete_upload_if_local(card["raw_back_image"])
        saved_front = _save_raw_bytes(front_bytes)
        saved_back = _save_raw_bytes(back_bytes)
        conn.execute(
            "UPDATE cards SET raw_front_image = ?, raw_back_image = ?, updated_at = ? WHERE id = ?",
            (saved_front, saved_back, now_iso(), card_id),
        )
        conn.commit()
        return {
            "card": get_card(card_id),
            "front_filename": front_name,
            "back_filename": back_name,
        }
    finally:
        conn.close()


@app.post("/api/cards/{card_id}/swap-raw")
def swap_raw_images(card_id: int):
    conn = get_db()
    try:
        card = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
        if not card:
            raise HTTPException(status_code=404, detail="Card not found.")
        conn.execute(
            "UPDATE cards SET raw_front_image = ?, raw_back_image = ?, updated_at = ? WHERE id = ?",
            (card["raw_back_image"], card["raw_front_image"], now_iso(), card_id),
        )
        conn.commit()
        return {"card": get_card(card_id)}
    finally:
        conn.close()


@app.post("/api/open-local-path")
def open_local_path(req: OpenLocalPathRequest):
    rel = (req.path or "").strip().lstrip("/")
    if not rel or rel.startswith("http://") or rel.startswith("https://"):
        raise HTTPException(status_code=400, detail="Not a local file path.")
    base_dir = UPLOADS_DIR.resolve()
    target = (base_dir / rel).resolve()
    if base_dir not in target.parents or not target.exists():
        raise HTTPException(status_code=404, detail="Local file not found.")
    try:
        if sys.platform.startswith("win"):
            if req.mode == "folder":
                subprocess.Popen(["explorer", "/select,", str(target)])
            else:
                try:
                    os.startfile(str(target))
                except Exception:
                    try:
                        subprocess.Popen(["cmd", "/c", "start", "", str(target)])
                    except Exception:
                        subprocess.Popen(["explorer", str(target)])
        elif sys.platform == "darwin":
            if req.mode == "folder":
                subprocess.Popen(["open", "-R", str(target)])
            else:
                subprocess.Popen(["open", str(target)])
        else:
            open_target = target.parent if req.mode == "folder" else target
            subprocess.Popen(["xdg-open", str(open_target)])
        return {"opened": True, "path": str(target)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not open local path: {exc}")


@app.post("/api/cards/{card_id}/fetch-value")
def fetch_card_value(card_id: int):
    conn = get_db()
    try:
        card = conn.execute(
            """
            SELECT c.*, s.company AS submission_company
            FROM cards c JOIN submissions s ON s.id = c.submission_id
            WHERE c.id = ?
            """,
            (card_id,),
        ).fetchone()
        if not card:
            raise HTTPException(status_code=404, detail="Card not found.")

        res = fetch_graded_market_value(
            card["card_name"],
            card["set_name"],
            card["card_number"],
            card["actual_grade"],
            card["submission_company"],
        )
        if res.get("success") and res.get("price") is not None:
            new_set = card["set_name"] or ""
            if res.get("pricecharting_set") and (not new_set.strip() or len(new_set.strip()) <= 5):
                new_set = res["pricecharting_set"]
            new_num = card["card_number"] or ""
            if res.get("pricecharting_number") and not new_num.strip():
                new_num = res["pricecharting_number"]
            conn.execute(
                """
                UPDATE cards
                SET set_name = ?, card_number = ?, market_value = ?, market_value_source = ?, market_value_updated_at = ?, pricecharting_url = COALESCE(NULLIF(?, ''), pricecharting_url), updated_at = ?
                WHERE id = ?
                """,
                (new_set, new_num, res["price"], "PriceCharting", now_iso(), res.get("source_url") or "", now_iso(), card_id),
            )
            conn.commit()
        res["card"] = get_card(card_id)
        return res
    finally:
        conn.close()


class PriceChartingUrlRequest(BaseModel):
    url: str


def fetch_tcgplayer_product_data(tcg_url: str) -> dict:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/html, */*",
    }
    m_pid = re.search(r"tcgplayer\.com/product/(\d+)", tcg_url, re.I)
    price = None
    name = ""
    set_name = ""
    card_num = ""
    if m_pid:
        pid = m_pid.group(1)
        try:
            rp = requests.get(f"https://mpapi.tcgplayer.com/v2/product/{pid}/pricepoints", headers=headers, timeout=10)
            if rp.status_code == 200:
                pts = rp.json()
                if isinstance(pts, list) and pts:
                    normal = next((p for p in pts if (p.get("printingType") or "").lower() == "normal" and p.get("marketPrice") is not None), None)
                    best = normal or next((p for p in pts if p.get("marketPrice") is not None), None) or next((p for p in pts if p.get("listedMedianPrice") is not None), None)
                    if best:
                        raw_p = best.get("marketPrice") if best.get("marketPrice") is not None else best.get("listedMedianPrice")
                        if raw_p is not None:
                            price = round(float(raw_p), 2)
        except Exception:
            pass
        try:
            rd = requests.get(f"https://mpapi.tcgplayer.com/v2/product/{pid}/details", headers=headers, timeout=10)
            if rd.status_code == 200:
                det = rd.json()
                name = (det.get("productName") or "").strip()
                set_name = (det.get("setName") or "").strip()
                custom = det.get("customAttributes") or {}
                card_num = str(custom.get("number") or "").strip()
        except Exception:
            pass

    if price is None:
        html = ""
        try:
            r = requests.get(tcg_url, headers=headers, timeout=12, allow_redirects=True)
            if r.status_code == 200:
                html = r.text
        except requests.RequestException:
            pass
        if not html or ("marketprice" not in html.lower() and "price-points" not in html.lower()):
            try:
                _, pw_html, _ = fetch_html_with_playwright(tcg_url, visible=False, max_wait_s=12)
                if pw_html:
                    html = pw_html
            except Exception:
                pass
        if html:
            m_mp = re.search(r'"marketPrice"\s*:\s*([\d.]+)', html)
            if not m_mp:
                m_mp = re.search(r'Market\s+Price[^\$]{0,80}\$([\d,]+(?:\.\d{2})?)', html, re.I)
            if m_mp:
                price = round(float(m_mp.group(1).replace(",", "")), 2)
            if not name:
                soup = BeautifulSoup(html, "html.parser")
                h1 = soup.find("h1")
                if h1:
                    name = h1.get_text(" ", strip=True)
    return {"price": price, "name": name, "set_name": set_name, "card_number": card_num}


@app.post("/api/cards/{card_id}/fetch-value-url")
def fetch_card_value_from_url(card_id: int, req: PriceChartingUrlRequest):
    pc_url = (req.url or "").strip()
    if not pc_url or not (pc_url.startswith("http://") or pc_url.startswith("https://")):
        raise HTTPException(status_code=400, detail="Please enter a valid PriceCharting or TCGplayer URL.")

    conn = get_db()
    try:
        card = conn.execute(
            """
            SELECT c.*, s.company AS submission_company
            FROM cards c JOIN submissions s ON s.id = c.submission_id
            WHERE c.id = ?
            """,
            (card_id,),
        ).fetchone()
        if not card:
            raise HTTPException(status_code=404, detail="Card not found.")

        if "tcgplayer.com" in pc_url.lower():
            tcg_data = fetch_tcgplayer_product_data(pc_url)
            chosen_price = tcg_data.get("price")
            tcg_name = tcg_data.get("name") or card["card_name"]
            tcg_set = tcg_data.get("set_name") or card["set_name"] or ""
            tcg_num = tcg_data.get("card_number") or card["card_number"] or ""
            new_mkt = round(chosen_price, 2) if chosen_price is not None else card["market_value"]
            new_src = "TCGplayer" if chosen_price is not None else card["market_value_source"]
            new_at = now_iso() if chosen_price is not None else card["market_value_updated_at"]
            conn.execute(
                """
                UPDATE cards
                SET card_name = ?, card_number = ?, set_name = ?,
                    market_value = ?, market_value_source = ?, market_value_updated_at = ?, pricecharting_url = ?, updated_at = ?
                WHERE id = ?
                """,
                (tcg_name, tcg_num, tcg_set, new_mkt, new_src, new_at, pc_url, now_iso(), card_id),
            )
            conn.commit()
            msg = (
                f"Updated from TCGplayer: ${chosen_price:,.2f}"
                if chosen_price is not None
                else "Saved TCGplayer link (could not auto-read Market Price — enter manually if needed)."
            )
            return {
                "success": chosen_price is not None,
                "price": new_mkt,
                "source_url": pc_url,
                "message": msg,
                "card": get_card(card_id),
            }

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            )
        }
        html = ""
        final_url = pc_url
        try:
            r = requests.get(pc_url, headers=headers, timeout=12, allow_redirects=True)
            if r.status_code == 200:
                html = r.text
                final_url = r.url
            elif r.status_code in (403, 429):
                final_url, html, _ = fetch_html_with_playwright(pc_url, visible=False, max_wait_s=12)
        except requests.RequestException:
            pass

        if not html:
            return {"success": False, "source_url": pc_url, "message": "Could not reach that PriceCharting link.", "card": get_card(card_id)}

        soup = BeautifulSoup(html, "html.parser")
        exact_prices = {}
        for tr in soup.find_all("tr"):
            cells = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
            if len(cells) == 2:
                key = cells[0].strip().lower()
                m_price = re.search(r"^\s*\$([\d,]+(?:\.\d{2})?)", cells[1])
                if m_price:
                    exact_prices[key] = float(m_price.group(1).replace(",", ""))

        g_low = (card["actual_grade"] or "").strip().lower()
        comp_low = (card["submission_company"] or "").strip().lower()
        priority_keys = ["ungraded"]
        if "black" in g_low and "10" in g_low:
            priority_keys = ["bgs 10 black", "bgs 10", "psa 10"]
        elif "pristine" in g_low and "10" in g_low:
            priority_keys = ["cgc 10 pristine", "cgc 10", "psa 10"] if "cgc" in comp_low else ["bgs 10", "cgc 10 pristine", "psa 10"]
        elif "10" in g_low:
            if "cgc" in comp_low:
                priority_keys = ["cgc 10", "psa 10"]
            elif "beckett" in comp_low or "bgs" in comp_low:
                priority_keys = ["bgs 10", "psa 10"]
            else:
                priority_keys = ["psa 10", "cgc 10", "bgs 10"]
        elif "9.5" in g_low:
            priority_keys = ["grade 9.5", "bgs 9.5", "cgc 9.5"]
        elif "9" in g_low:
            priority_keys = ["grade 9", "psa 9"]
        elif "8.5" in g_low or "8" in g_low:
            priority_keys = ["grade 8", "psa 8"]
        elif "7.5" in g_low or "7" in g_low:
            priority_keys = ["grade 7", "psa 7"]

        chosen_price = None
        matched_label = ""
        for pk in priority_keys:
            if pk in exact_prices:
                chosen_price = exact_prices[pk]
                matched_label = pk.upper()
                break

        pc_name = card["card_name"]
        pc_num = card["card_number"] or ""
        pc_set = card["set_name"] or ""
        title_tag = soup.find("title")
        if title_tag and "|" in title_tag.get_text():
            parts = [pt.strip() for pt in title_tag.get_text().split("|")]
            part0 = parts[0]
            m_num = re.search(r"#([A-Za-z0-9/\-]+)", part0)
            if m_num:
                pc_num = m_num.group(1).strip()
            clean_p0 = re.sub(r"\s*#[A-Za-z0-9/\-]+.*$", "", part0)
            clean_p0 = re.sub(r"\s+Prices\s*$", "", clean_p0, flags=re.I).strip()
            if clean_p0:
                pc_name = clean_p0
            if len(parts) >= 2 and "pokemon" in parts[1].lower():
                pc_set = re.sub(r"^Pokemon\s+", "", parts[1], flags=re.I).strip()

        new_mkt = round(chosen_price, 2) if chosen_price is not None else card["market_value"]
        new_src = "PriceCharting" if chosen_price is not None else card["market_value_source"]
        new_at = now_iso() if chosen_price is not None else card["market_value_updated_at"]

        conn.execute(
            """
            UPDATE cards
            SET card_name = ?, card_number = ?, set_name = ?,
                market_value = ?, market_value_source = ?, market_value_updated_at = ?, pricecharting_url = ?, updated_at = ?
            WHERE id = ?
            """,
            (pc_name, pc_num, pc_set, new_mkt, new_src, new_at, final_url, now_iso(), card_id),
        )
        conn.commit()
        msg = (
            f"Updated from PriceCharting ({matched_label}): ${chosen_price:,.2f}"
            if chosen_price is not None
            else "Updated card name, number & set from PriceCharting (grade price not listed)."
        )
        return {
            "success": True,
            "price": new_mkt,
            "source_url": final_url,
            "message": msg,
            "card": get_card(card_id),
        }
    finally:
        conn.close()


@app.get("/api/brand")
def get_brand_settings():
    conn = get_db()
    try:
        rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
        kv = {r["key"]: r["value"] for r in rows}
        return {
            "brand_name": kv.get("brand_name") or "TCG Grading Vault",
            "brand_subtitle": kv.get("brand_subtitle") or "100% Local Storage • TCG Slab & Profit Tracker",
            "brand_icon": kv.get("brand_icon") or "",
        }
    finally:
        conn.close()


@app.post("/api/brand")
def update_brand_settings(
    brand_name: str = Form("TCG Grading Vault"),
    brand_subtitle: str = Form("100% Local Storage • TCG Slab & Profit Tracker"),
    reset_icon: str = Form("false"),
    icon: Optional[UploadFile] = File(None),
):
    conn = get_db()
    try:
        cur_icon_row = conn.execute("SELECT value FROM app_settings WHERE key = 'brand_icon'").fetchone()
        cur_icon = cur_icon_row["value"] if cur_icon_row else ""

        if reset_icon.lower() == "true":
            delete_upload_if_local(cur_icon)
            cur_icon = ""
        elif icon and icon.filename:
            raw_bytes = icon.file.read()
            if raw_bytes:
                delete_upload_if_local(cur_icon)
                filename = f"brand_icon_{uuid.uuid4().hex[:8]}.webp"
                dest = BRAND_UPLOADS_DIR / filename
                try:
                    with Image.open(io.BytesIO(raw_bytes)) as im:
                        im = ImageOps.exif_transpose(im)
                        if im.mode not in ("RGB", "RGBA"):
                            im = im.convert("RGBA")
                        im.thumbnail((256, 256), Image.Resampling.LANCZOS)
                        im.save(dest, format="WEBP", quality=90)
                    cur_icon = f"brand/{filename}"
                except Exception:
                    ext = Path(icon.filename).suffix.lower() or ".png"
                    fb_name = f"brand_icon_{uuid.uuid4().hex[:8]}{ext}"
                    (BRAND_UPLOADS_DIR / fb_name).write_bytes(raw_bytes)
                    cur_icon = f"brand/{fb_name}"

        for k, v in [
            ("brand_name", brand_name.strip() or "TCG Grading Vault"),
            ("brand_subtitle", brand_subtitle.strip() or "100% Local Storage • TCG Slab & Profit Tracker"),
            ("brand_icon", cur_icon),
        ]:
            conn.execute(
                "INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (k, v),
            )
        conn.commit()
        return get_brand_settings()
    finally:
        conn.close()


KNOWN_FULL_SETS = [
    "Prismatic Evolutions", "Surging Sparks", "Stellar Crown", "Shrouded Fable",
    "Twilight Masquerade", "Temporal Forces", "Paldean Fates", "Paradox Rift",
    "Obsidian Flames", "Paldea Evolved", "Scarlet & Violet", "Journey Together",
    "Destined Rivals", "Perfect Order", "Crown Zenith", "Silver Tempest",
    "Lost Origin", "Astral Radiance", "Brilliant Stars", "Fusion Strike",
    "Evolving Skies", "Chilling Reign", "Battle Styles", "Sword & Shield",
    "Hidden Fates", "Shining Fates", "Celebrations", "Pokemon 151", "151",
    "Terastal Festival ex", "Terastal Festival", "VSTAR Universe", "Shiny Treasure ex",
    "Shiny Treasure", "VMAX Climax", "Eevee Heroes", "Base Set", "Jungle", "Fossil",
    "Team Rocket", "Neo Genesis", "Mega Evolution Promo", "Black Star Promo",
    "Ascended Heroes", "Phantasmal Flames", "30th Celebration",
    "Gem Pack Vol. 4", "Gem Pack Vol.4", "Gem Pack Volume 4", "Gem Pack Vol4",
    "Islands Await You", "Scarlet & Violet 151",
]


def parse_fanatics_csv_title(title: str) -> dict:
    t = re.sub(r"\s+", " ", (title or "")).strip()
    out = {"year": "", "language": "", "set_name": "", "card_name": t, "card_number": "", "grader": "", "grade": ""}
    if not t:
        return out

    m_yr = re.search(r"\b(199\d|20[0-3]\d)\b", t)
    if m_yr:
        out["year"] = m_yr.group(1)

    for lang in ("Japanese", "English", "Chinese", "Korean", "German", "French", "Spanish", "Italian", "Portuguese"):
        if re.search(rf"\b{lang}\b", t, re.I):
            out["language"] = lang
            break

    m_cn = re.search(r"#([A-Za-z0-9/\-]+)", t)
    if m_cn:
        out["card_number"] = m_cn.group(1)
    else:
        m_cn2 = re.search(r"\b(\d{1,3}/\d{1,3}|(?:TG|GG|SV|SWSH|SM|XY|BW)\d{1,4})\b", t, re.I)
        if m_cn2:
            out["card_number"] = m_cn2.group(1)

    m_g = re.search(r"\b(PSA|CGC|BGS|Beckett|TAG)\s*(Pristine\s*10|Black\s*Label\s*10|Gem\s*Mint\s*10|10|9\.5|9|8\.5|8|7\.5|7|6\.5|6|5\.5|5|4\.5|4|3\.5|3|2\.5|2|1\.5|1)\b", t, re.I)
    if m_g:
        g_comp = m_g.group(1).upper()
        out["grader"] = "Beckett" if g_comp in ("BGS", "BECKETT") else g_comp
        g_raw = m_g.group(2).strip()
        if "pristine" in g_raw.lower():
            out["grade"] = "Pristine 10"
        elif "black" in g_raw.lower():
            out["grade"] = "Black Label 10"
        else:
            m_n = re.search(r"(10|9\.5|9|8\.5|8|7\.5|7|6\.5|6|5\.5|5|4\.5|4|3\.5|3|2\.5|2|1\.5|1)", g_raw)
            out["grade"] = m_n.group(1) if m_n else g_raw

    out["set_name"] = expand_set_name("", t)
    if not out["set_name"]:
        t_low = t.lower()
        for full_set in sorted(set(KNOWN_FULL_SETS + list(SET_ABBREVIATIONS.values())), key=len, reverse=True):
            if full_set.lower() in t_low:
                out["set_name"] = full_set
                break

    clean = t
    if out["year"]:
        clean = re.sub(rf"^\s*{re.escape(out['year'])}\s+", "", clean)
    clean = re.sub(r"\b(?:Pokemon|TCG|Japanese|English|Chinese|Korean|German|French|Spanish|Italian|Portuguese)\b", "", clean, flags=re.I)
    clean = re.sub(r"\b(?:PSA|CGC|BGS|Beckett)\s*(?:Pristine|Black\s*Label|Gem\s*Mint)?\s*(?:10|9\.5|9|8\.5|8|7\.5|7|6\.5|6|5\.5|5|4\.5|4|3\.5|3|2\.5|2|1\.5|1)\b.*$", "", clean, flags=re.I)
    clean = re.sub(r"\s+", " ", clean).strip(" -|•")
    if out["language"] and out["language"].lower() != "english" and out["language"].lower() not in clean.lower():
        clean = f"{clean} ({out['language']})"
    if clean:
        out["card_name"] = clean
    return out


@app.post("/api/submissions/{sub_id}/import-fanatics-csv")
async def import_fanatics_csv(sub_id: int, file: UploadFile = File(...)):
    import csv as py_csv
    conn = get_db()
    try:
        sub = conn.execute("SELECT * FROM submissions WHERE id = ?", (sub_id,)).fetchone()
        if not sub:
            raise HTTPException(status_code=404, detail="Submission not found.")

        raw_bytes = await file.read()
        text = raw_bytes.decode("utf-8-sig", errors="replace")
        reader = py_csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            raise HTTPException(status_code=400, detail="CSV file appears empty.")

        existing_cards = [dict(r) for r in conn.execute("SELECT * FROM cards WHERE submission_id = ?", (sub_id,)).fetchall()]
        matched_ids = set()
        updated_count = 0
        created_count = 0
        total_rows = 0

        for raw_row in reader:
            row = {re.sub(r"[^a-z0-9]", "", (k or "").lower()): (v or "").strip() for k, v in raw_row.items() if k is not None}
            title = row.get("title", "") or row.get("card", "") or row.get("name", "") or row.get("cardname", "")
            grade_col = row.get("grade", "") or row.get("finalgrade", "")
            cert_raw = (
                row.get("certnumber", "") or row.get("certno", "") or row.get("cert", "")
                or row.get("certificatenumber", "") or row.get("certificationnumber", "") or row.get("certid", "")
            )
            cert_col = extract_cert_number(cert_raw)
            curated_col = row.get("curateddate", "") or row.get("datecurated", "") or row.get("curated", "")
            mkt_raw = row.get("marketvalue", "") or row.get("estimatedvalue", "") or row.get("value", "")
            link_col = row.get("url", "") or row.get("link", "") or row.get("itemurl", "") or row.get("listingurl", "")
            if not any([title, grade_col, cert_col, curated_col, mkt_raw]):
                continue
            total_rows += 1

            parsed = parse_fanatics_csv_title(title)
            final_grade = grade_col or parsed["grade"]
            if "pristine" in title.lower() and "10" in (final_grade or title):
                final_grade = "Pristine 10"
            elif "black label" in title.lower() and "10" in (final_grade or title):
                final_grade = "Black Label 10"
            elif final_grade and "pristine" in final_grade.lower() and "10" in final_grade:
                final_grade = "Pristine 10"
            elif final_grade:
                mg = re.search(r"(10|9\.5|9|8\.5|8|7\.5|7|6\.5|6|5\.5|5|4\.5|4|3\.5|3|2\.5|2|1\.5|1)", final_grade)
                if mg and "black" not in final_grade.lower():
                    final_grade = mg.group(1)

            mkt_val = None
            if mkt_raw:
                cleaned_m = re.sub(r"[^0-9.]", "", mkt_raw)
                if cleaned_m:
                    try:
                        mkt_val = round(float(cleaned_m), 2)
                    except ValueError:
                        mkt_val = None

            curated_norm = curated_col
            if curated_col:
                for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d", "%b %d, %Y"):
                    try:
                        curated_norm = datetime.strptime(curated_col.strip(), fmt).strftime("%Y-%m-%d")
                        break
                    except ValueError:
                        pass

            best_card = None
            best_score = -1
            row_tokens = set(re.findall(r"[a-z0-9]{3,}", title.lower()))
            for c in existing_cards:
                if c["id"] in matched_ids:
                    continue
                if cert_col and (c.get("cert_number") or "").strip() == cert_col:
                    best_card = c
                    best_score = 1000
                    break
                sc = 0
                c_num = (c.get("card_number") or "").split("/")[0].lstrip("0").lower()
                p_num = (parsed["card_number"] or "").split("/")[0].lstrip("0").lower()
                if c_num and p_num and c_num == p_num:
                    sc += 40
                c_tokens = set(re.findall(r"[a-z0-9]{3,}", f"{c.get('card_name','')} {c.get('set_name','')}".lower()))
                sc += len(row_tokens & c_tokens) * 15
                c_name_low = (c.get("card_name") or "").strip().lower()
                if len(c_name_low) >= 4 and c_name_low in title.lower():
                    sc += 25
                if cert_col and not (c.get("cert_number") or "").strip():
                    sc += 15
                if sc > best_score and sc >= 20:
                    best_score = sc
                    best_card = c

            company = parsed["grader"] or sub["company"]
            cert_url = build_cert_url(company, cert_col) if cert_col else ""

            if best_card:
                matched_ids.add(best_card["id"])
                new_status = resolve_card_status(
                    best_card["status"], best_card["sold_price"], cert_col or best_card["cert_number"], final_grade or best_card["actual_grade"], best_card["fanatics_url"]
                )
                conn.execute(
                    """
                    UPDATE cards
                    SET card_name = ?, card_year = ?, set_name = ?, card_number = ?,
                        actual_grade = ?, cert_number = ?, cert_lookup_url = ?,
                        fanatics_url = COALESCE(NULLIF(?, ''), fanatics_url),
                        curated_date = ?, market_value = COALESCE(?, market_value),
                        market_value_source = CASE WHEN ? IS NOT NULL THEN 'Fanatics CSV' ELSE market_value_source END,
                        market_value_updated_at = CASE WHEN ? IS NOT NULL THEN ? ELSE market_value_updated_at END,
                        status = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        parsed["card_name"] or best_card["card_name"],
                        parsed["year"] or best_card["card_year"] or "",
                        parsed["set_name"] or best_card["set_name"] or "",
                        parsed["card_number"] or best_card["card_number"] or "",
                        final_grade or best_card["actual_grade"] or "",
                        cert_col or best_card["cert_number"] or "",
                        cert_url or best_card["cert_lookup_url"] or "",
                        link_col,
                        curated_norm or best_card.get("curated_date") or "",
                        mkt_val,
                        mkt_val,
                        mkt_val,
                        now_iso(),
                        new_status,
                        now_iso(),
                        best_card["id"],
                    ),
                )
                updated_count += 1
            else:
                ts = now_iso()
                new_status = resolve_card_status("In Grading", None, cert_col, final_grade, "")
                conn.execute(
                    """
                    INSERT INTO cards (
                        submission_id, card_name, card_year, set_name, card_number,
                        cert_number, cert_lookup_url, fanatics_url, actual_grade, curated_date,
                        market_value, market_value_source, market_value_updated_at,
                        status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        sub_id,
                        parsed["card_name"] or title or "Imported Card",
                        parsed["year"],
                        parsed["set_name"],
                        parsed["card_number"],
                        cert_col,
                        cert_url,
                        link_col,
                        final_grade,
                        curated_norm,
                        mkt_val,
                        "Fanatics CSV" if mkt_val is not None else "",
                        ts if mkt_val is not None else "",
                        new_status,
                        ts,
                        ts,
                    ),
                )
                created_count += 1

        conn.commit()
        return {
            "updated_count": updated_count,
            "created_count": created_count,
            "total_rows": total_rows,
            "message": f"Fanatics CSV imported: {updated_count} cards updated, {created_count} added.",
        }
    finally:
        conn.close()


@app.post("/api/submissions/import-fanatics-csv")
async def create_submission_from_fanatics_csv(
    file: UploadFile = File(...),
    title: str = Form(""),
    company: str = Form(""),
    submitted_date: str = Form(""),
):
    raw_bytes = await file.read()
    sub_title = title.strip() or Path(file.filename or "Fanatics Submission").stem.replace("_", " ").strip()
    sub_date = submitted_date.strip() or datetime.now().strftime("%Y-%m-%d")
    ts = now_iso()
    conn = get_db()
    try:
        if company not in ("PSA", "CGC", "Beckett", "TAG"):
            raise HTTPException(status_code=400, detail="Please select a grading company (PSA, CGC, or Beckett) first.")
        cur = conn.execute(
            """
            INSERT INTO submissions (title, company, submission_number, submitted_date, status, notes, created_at, updated_at)
            VALUES (?, ?, '', ?, 'Completed', 'Created from Fanatics CSV', ?, ?)
            """,
            (sub_title, company, sub_date, ts, ts),
        )
        conn.commit()
        sub_id = cur.lastrowid
    finally:
        conn.close()

    # Reuse import_fanatics_csv with a fresh UploadFile wrapper
    import tempfile as _tf
    with _tf.SpooledTemporaryFile() as sp:
        sp.write(raw_bytes)
        sp.seek(0)
        up = UploadFile(filename=file.filename or "fanatics.csv", file=sp)
        res = await import_fanatics_csv(sub_id, up)
    res["submission_id"] = sub_id
    return res


UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def _extract_fanatics_collection_items(obj, out_items: list):
    if isinstance(obj, list):
        for el in obj:
            _extract_fanatics_collection_items(el, out_items)
        return
    if not isinstance(obj, dict):
        return

    raw_id = str(obj.get("id") or obj.get("itemId") or obj.get("collectibleId") or obj.get("vaultItemId") or "").strip()
    raw_url = str(obj.get("url") or obj.get("href") or obj.get("slug") or "").strip()
    uuid_val = raw_id if UUID_RE.match(raw_id) else ""
    if not uuid_val and "/collection/i/" in raw_url:
        m_u = re.search(r"/collection/i/([0-9a-f-]{36})", raw_url, re.I)
        if m_u:
            uuid_val = m_u.group(1)

    cert_raw = str(
        obj.get("certNumber") or obj.get("certificationNumber") or obj.get("cert_number")
        or obj.get("certNo") or obj.get("cert") or obj.get("serialNumber") or ""
    ).strip()
    cert_clean = extract_cert_number(cert_raw) if cert_raw else ""

    # Extract high-res front & back image URLs from common Fanatics GraphQL structures
    front_img = ""
    back_img = ""
    for fk in ("frontImageUrl", "frontImage", "obverseImageUrl", "imageUrl", "primaryImageUrl", "image"):
        v = obj.get(fk)
        if isinstance(v, dict):
            v = v.get("url") or v.get("large") or v.get("original") or v.get("src") or ""
        if isinstance(v, str) and v.startswith("http"):
            front_img = v
            break
    for bk in ("backImageUrl", "backImage", "reverseImageUrl", "secondaryImageUrl"):
        v = obj.get(bk)
        if isinstance(v, dict):
            v = v.get("url") or v.get("large") or v.get("original") or v.get("src") or ""
        if isinstance(v, str) and v.startswith("http"):
            back_img = v
            break

    for list_key in ("images", "media", "assets", "photos"):
        arr = obj.get(list_key)
        if isinstance(arr, list) and arr:
            urls = []
            for item in arr:
                if isinstance(item, str) and item.startswith("http"):
                    urls.append(item)
                elif isinstance(item, dict):
                    u = item.get("url") or item.get("largeUrl") or item.get("originalUrl") or item.get("src") or ""
                    if isinstance(u, str) and u.startswith("http"):
                        urls.append(u)
            if urls and not front_img:
                front_img = urls[0]
            if len(urls) >= 2 and not back_img:
                back_img = urls[1]

    title = str(obj.get("title") or obj.get("name") or obj.get("cardName") or "").strip()
    grade = str(obj.get("grade") or obj.get("gradeValue") or obj.get("gradeLabel") or "").strip()

    if (uuid_val or front_img) and (cert_clean or title):
        item_url = f"https://www.fanaticscollect.com/collection/i/{uuid_val}" if uuid_val else (
            urljoin("https://www.fanaticscollect.com", raw_url) if raw_url.startswith("/") else raw_url
        )
        out_items.append({
            "uuid": uuid_val,
            "item_url": item_url,
            "cert_number": cert_clean,
            "title": title,
            "grade": grade,
            "front_img_url": front_img,
            "back_img_url": back_img,
        })

    for v in obj.values():
        if isinstance(v, (dict, list)):
            _extract_fanatics_collection_items(v, out_items)


def fetch_fanatics_collection_via_session(url: str, window_bounds: Optional[tuple] = None) -> tuple:
    pw_profile = str((DATA_DIR / "pw_profile").resolve())
    pop_w, pop_h, pop_x, pop_y = 1040, 800, None, None
    if window_bounds and all(v is not None for v in window_bounds):
        wx, wy, ww, wh = [int(v) for v in window_bounds]
        pop_w = min(1060, max(700, ww - 60))
        pop_h = min(800, max(580, wh - 80))
        pop_x = wx + max(20, (ww - pop_w) // 2)
        pop_y = wy + max(20, (wh - pop_h) // 2)
    extra_args = ["--disable-blink-features=AutomationControlled", f"--window-size={pop_w},{pop_h}"]
    if pop_x is not None and pop_y is not None:
        extra_args.append(f"--window-position={pop_x},{pop_y}")

    helper_code = f"""
import json, sys, time
from playwright.sync_api import sync_playwright

url = {json.dumps(url)}
profile_dir = {json.dumps(pw_profile)}
res = {{"url": url, "json_payloads": [], "dom_links": [], "error": ""}}

with sync_playwright() as p:
    ctx = None
    for ch in ("msedge", "chrome", None):
        try:
            kw = {{"headless": False, "args": {json.dumps(extra_args)}, "viewport": {{"width": {pop_w - 24}, "height": {pop_h - 90}}}}}
            if ch: kw["channel"] = ch
            ctx = p.chromium.launch_persistent_context(profile_dir, **kw)
            break
        except Exception:
            pass
    if not ctx:
        res["error"] = "Playwright browser unavailable."
        print(json.dumps(res))
        sys.exit(0)
    try:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {{get: () => undefined}});")

        captured_gql = []
        def on_response(resp):
            try:
                ct = (resp.headers.get("content-type") or "").lower()
                if "json" in ct and resp.status == 200:
                    txt = resp.text()
                    if len(txt) > 100 and any(k in txt.lower() for k in ("cert", "collection", "collectible", "image", "grade")):
                        res["json_payloads"].append(json.loads(txt))
                        req = resp.request
                        if req.method == "POST" and ("graphql" in resp.url.lower() or "api" in resp.url.lower()):
                            try:
                                post_d = req.post_data or ""
                                if any(k in post_d for k in ("limit", "first", "pageSize", "perPage")):
                                    captured_gql.append((resp.url, dict(req.headers), post_d))
                            except Exception:
                                pass
            except Exception:
                pass

        page.on("response", on_response)
        page.goto(url, wait_until="domcontentloaded", timeout=25000)

        deadline = time.time() + 90
        while time.time() < deadline:
            cur_u = (page.url or "").lower()
            title = (page.title() or "").lower()
            body_txt = ""
            try:
                body_txt = page.inner_text("body")[:500].lower()
            except Exception:
                pass
            is_cf = ("just a moment" in title or "verify you are human" in body_txt)
            is_login = any(k in cur_u for k in ("/login", "/signin", "/sign-in", "/auth")) or any(
                k in body_txt for k in ("page cannot be found", "page not found", "sign in to", "log in to")
            )
            if not is_cf and not is_login:
                try:
                    page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    pass
                page.wait_for_timeout(2000)
                break
            time.sleep(1.0)

        # Single-request GraphQL replay with larger limit (250) so all 60+ items arrive in 1 call (zero page-clicking!)
        if captured_gql:
            g_url, g_hdrs, g_body = captured_gql[-1]
            try:
                parsed_body = json.loads(g_body)
                vars_obj = parsed_body.get("variables") if isinstance(parsed_body, dict) else None
                if isinstance(vars_obj, dict):
                    changed = False
                    for lk in ("limit", "first", "pageSize", "perPage"):
                        if lk in vars_obj and isinstance(vars_obj[lk], int) and vars_obj[lk] < 250:
                            vars_obj[lk] = 250
                            changed = True
                    if changed:
                        js_replay = "async ([u, b]) => {{ const r = await fetch(u, {{ method: 'POST', headers: {{'content-type': 'application/json'}}, credentials: 'include', body: JSON.stringify(b) }}); return await r.json(); }}"
                        replay_res = page.evaluate(js_replay, [g_url, parsed_body])
                        if isinstance(replay_res, dict):
                            res["json_payloads"].append(replay_res)
                            # If server capped page size (e.g. 50) and returned endCursor or total > 50, fetch page 2 via cursor/page
                            try:
                                r_txt = json.dumps(replay_res)
                                m_cur = re.search(r'"endCursor"\s*:\s*"([^"]+)"', r_txt)
                                if m_cur and '"hasNextPage"\s*:\s*true' in r_txt:
                                    vars_obj["after"] = m_cur.group(1)
                                    pg2 = page.evaluate(js_replay, [g_url, parsed_body])
                                    if isinstance(pg2, dict):
                                        res["json_payloads"].append(pg2)
                                elif "page" in vars_obj and isinstance(vars_obj["page"], int):
                                    vars_obj["page"] = vars_obj["page"] + 1
                                    pg2 = page.evaluate(js_replay, [g_url, parsed_body])
                                    if isinstance(pg2, dict):
                                        res["json_payloads"].append(pg2)
                            except Exception:
                                pass
            except Exception:
                pass

        # Also collect any rendered /collection/i/<uuid> links from the DOM
        try:
            js_dom = "() => Array.from(document.querySelectorAll('a[href*=\"/collection/i/\"]')).map(a => ({{ href: a.href, text: (a.innerText || '').trim(), img: a.querySelector('img')?.src || '' }}))"
            dom_links = page.evaluate(js_dom)
            res["dom_links"] = dom_links or []
        except Exception:
            pass

        res["url"] = page.url
    except Exception as e:
        res["error"] = str(e)
    finally:
        ctx.close()

print(json.dumps(res))
"""
    try:
        proc = subprocess.run([sys.executable, "-c", helper_code], capture_output=True, text=True, timeout=115)
        lines = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
        if not lines:
            return [], url, "Playwright returned empty output."
        data = json.loads(lines[-1])
        items = []
        for p in data.get("json_payloads", []):
            _extract_fanatics_collection_items(p, items)
        seen_urls = {it["item_url"] for it in items if it.get("item_url")}
        for dl in data.get("dom_links", []):
            href = (dl.get("href") or "").strip()
            if href and href not in seen_urls:
                seen_urls.add(href)
                m_u = re.search(r"/collection/i/([0-9a-f-]{36})", href, re.I)
                items.append({
                    "uuid": m_u.group(1) if m_u else "",
                    "item_url": href,
                    "cert_number": "",
                    "title": dl.get("text") or "",
                    "grade": "",
                    "front_img_url": dl.get("img") or "",
                    "back_img_url": "",
                })
        return items, data.get("url", url), data.get("error", "")
    except Exception as exc:
        return [], url, str(exc)


@app.post("/api/submissions/{sub_id}/sync-fanatics-collection")
def sync_fanatics_collection(sub_id: int, req: PullFromLinkRequest):
    url = (req.url or "").strip() or "https://www.fanaticscollect.com/collection"
    conn = get_db()
    try:
        sub = conn.execute("SELECT * FROM submissions WHERE id = ?", (sub_id,)).fetchone()
        if not sub:
            raise HTTPException(status_code=404, detail="Submission not found.")
        existing_cards = [dict(r) for r in conn.execute("SELECT * FROM cards WHERE submission_id = ?", (sub_id,)).fetchall()]
    finally:
        conn.close()

    bounds = (req.win_x, req.win_y, req.win_w, req.win_h) if req.win_w and req.win_h else None
    items, final_url, err = fetch_fanatics_collection_via_session(url, window_bounds=bounds)
    if not items:
        return {"success": False, "matched_count": 0, "message": err or "No Fanatics collection items captured. Make sure you're logged in on that page."}

    # Deduplicate items by cert_number or item_url
    by_cert = {}
    fallback_items = []
    for it in items:
        cnum = (it.get("cert_number") or "").strip()
        if cnum:
            prev = by_cert.get(cnum)
            if not prev or (not prev.get("front_img_url") and it.get("front_img_url")) or (not prev.get("item_url") and it.get("item_url")):
                by_cert[cnum] = it
        else:
            fallback_items.append(it)

    matched_count = 0
    photos_downloaded = 0
    conn = get_db()
    try:
        for c in existing_cards:
            c_cert = (c.get("cert_number") or "").strip()
            match = by_cert.get(c_cert) if c_cert else None
            if not match and fallback_items:
                c_num = (c.get("card_number") or "").split("/")[0].lstrip("0").lower()
                c_name_toks = set(re.findall(r"[a-z0-9]{3,}", (c.get("card_name") or "").lower()))
                for fit in fallback_items:
                    t_low = (fit.get("title") or "").lower()
                    if c_num and re.search(rf"#{re.escape(c_num)}\b", t_low) and len(c_name_toks & set(re.findall(r"[a-z0-9]{3,}", t_low))) >= 1:
                        match = fit
                        fallback_items.remove(fit)
                        break
            if not match:
                continue

            new_url = match.get("item_url") or c.get("fanatics_url") or ""
            new_front = "" if req.force_replace else (c.get("cert_front_image") or "")
            new_back = "" if req.force_replace else (c.get("cert_back_image") or "")
            if match.get("front_img_url") and (req.force_replace or not new_front):
                try:
                    dl_f = download_image_to_certs(match["front_img_url"], c_cert or f"fan_{c['id']}", "front")
                    if dl_f:
                        if req.force_replace:
                            delete_upload_if_local(c.get("cert_front_image") or "")
                        new_front = dl_f
                        photos_downloaded += 1
                except Exception:
                    new_front = c.get("cert_front_image") or ""
            if match.get("back_img_url") and (req.force_replace or not new_back):
                try:
                    dl_b = download_image_to_certs(match["back_img_url"], c_cert or f"fan_{c['id']}", "back")
                    if dl_b:
                        if req.force_replace:
                            delete_upload_if_local(c.get("cert_back_image") or "")
                        new_back = dl_b
                except Exception:
                    new_back = c.get("cert_back_image") or ""

            conn.execute(
                """
                UPDATE cards
                SET fanatics_url = COALESCE(NULLIF(?, ''), fanatics_url),
                    cert_front_image = COALESCE(NULLIF(?, ''), cert_front_image),
                    cert_back_image = COALESCE(NULLIF(?, ''), cert_back_image),
                    updated_at = ?
                WHERE id = ?
                """,
                (new_url, new_front, new_back, now_iso(), c["id"]),
            )
            matched_count += 1
        conn.commit()
    finally:
        conn.close()

    return {
        "success": matched_count > 0,
        "captured_items": len(by_cert) + len(fallback_items),
        "matched_count": matched_count,
        "photos_downloaded": photos_downloaded,
        "message": f"Captured {len(by_cert) + len(fallback_items)} Fanatics items — linked {matched_count} cards & downloaded {photos_downloaded} high-res scans!",
    }


class MoveCardRequest(BaseModel):
    target_submission_id: int


@app.post("/api/cards/{card_id}/move")
def move_card_to_submission(card_id: int, req: MoveCardRequest):
    conn = get_db()
    try:
        card = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
        if not card:
            raise HTTPException(status_code=404, detail="Card not found.")
        target_sub = conn.execute("SELECT * FROM submissions WHERE id = ?", (req.target_submission_id,)).fetchone()
        if not target_sub:
            raise HTTPException(status_code=404, detail="Target submission not found.")
        conn.execute(
            "UPDATE cards SET submission_id = ?, updated_at = ? WHERE id = ?",
            (req.target_submission_id, now_iso(), card_id),
        )
        conn.commit()
        return {"moved": True, "card": get_card(card_id), "target_submission": dict(target_sub)}
    finally:
        conn.close()


class BulkMoveRequest(BaseModel):
    card_ids: list[int]
    target_submission_id: int


class BulkDeleteRequest(BaseModel):
    card_ids: list[int]


class BulkDuplicateRequest(BaseModel):
    card_ids: list[int]
    copies: int = 1


class BulkStatusRequest(BaseModel):
    card_ids: list[int]
    status: str


class BulkEditRequest(BaseModel):
    card_ids: list[int]
    set_name: Optional[str] = None
    acquisition_cost: Optional[float] = None
    grading_cost: Optional[float] = None
    shipping_cost: Optional[float] = None
    curated_date: Optional[str] = None


@app.post("/api/cards/bulk-move")
def bulk_move_cards(req: BulkMoveRequest):
    if not req.card_ids:
        return {"moved_count": 0}
    conn = get_db()
    try:
        target_sub = conn.execute("SELECT * FROM submissions WHERE id = ?", (req.target_submission_id,)).fetchone()
        if not target_sub:
            raise HTTPException(status_code=404, detail="Target submission not found.")
        placeholders = ",".join("?" for _ in req.card_ids)
        cur = conn.execute(
            f"UPDATE cards SET submission_id = ?, updated_at = ? WHERE id IN ({placeholders})",
            [req.target_submission_id, now_iso(), *req.card_ids],
        )
        conn.commit()
        return {"moved_count": cur.rowcount, "target_submission": dict(target_sub)}
    finally:
        conn.close()


@app.post("/api/cards/bulk-duplicate")
def bulk_duplicate_cards(req: BulkDuplicateRequest):
    if not req.card_ids:
        return {"duplicated_count": 0, "new_card_ids": []}
    copies = max(1, min(50, int(req.copies or 1)))
    conn = get_db()
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(cards)").fetchall() if r[1] != "id"]
        placeholders = ",".join("?" for _ in req.card_ids)
        rows = conn.execute(
            f"SELECT * FROM cards WHERE id IN ({placeholders})",
            req.card_ids,
        ).fetchall()
        row_map = {r["id"]: dict(r) for r in rows}
        ordered_rows = [row_map[cid] for cid in req.card_ids if cid in row_map]
        ts = now_iso()
        new_ids = []
        col_list_sql = ", ".join(cols)
        val_placeholders = ", ".join("?" for _ in cols)
        for src in ordered_rows:
            for _ in range(copies):
                vals = []
                for c in cols:
                    if c in ("raw_front_image", "raw_back_image", "cert_front_image", "cert_back_image"):
                        vals.append(copy_local_upload(src.get(c) or ""))
                    elif c in ("created_at", "updated_at"):
                        vals.append(ts)
                    else:
                        vals.append(src.get(c))
                cur = conn.execute(f"INSERT INTO cards ({col_list_sql}) VALUES ({val_placeholders})", vals)
                new_ids.append(cur.lastrowid)
        conn.commit()
        return {"duplicated_count": len(new_ids), "new_card_ids": new_ids}
    finally:
        conn.close()


@app.post("/api/cards/bulk-status")
def bulk_update_card_status(req: BulkStatusRequest):
    if not req.card_ids:
        return {"updated_count": 0}
    st = (req.status or "").strip()
    allowed = ("Not Submitted", "In Grading", "Unsold", "Hold", "Listed")
    if st not in allowed:
        raise HTTPException(status_code=400, detail=f"Status must be one of {', '.join(allowed)}.")
    conn = get_db()
    try:
        placeholders = ",".join("?" for _ in req.card_ids)
        cur = conn.execute(
            f"UPDATE cards SET status = ?, sold_price = NULL, updated_at = ? WHERE id IN ({placeholders})",
            [st, now_iso(), *req.card_ids],
        )
        conn.commit()
        return {"updated_count": cur.rowcount, "status": st}
    finally:
        conn.close()


@app.post("/api/cards/bulk-edit")
def bulk_edit_cards(req: BulkEditRequest):
    if not req.card_ids:
        return {"updated_count": 0}
    updates = []
    params = []
    if req.set_name is not None and req.set_name.strip() != "":
        updates.append("set_name = ?")
        params.append(req.set_name.strip())
    if req.acquisition_cost is not None:
        updates.append("acquisition_cost = ?")
        params.append(float(req.acquisition_cost))
    if req.grading_cost is not None:
        updates.append("grading_cost = ?")
        params.append(float(req.grading_cost))
    if req.shipping_cost is not None:
        updates.append("shipping_cost = ?")
        params.append(float(req.shipping_cost))
    if req.curated_date is not None and req.curated_date.strip() != "":
        updates.append("curated_date = ?")
        params.append(req.curated_date.strip())
    if not updates:
        return {"updated_count": 0}
    updates.append("updated_at = ?")
    params.append(now_iso())
    placeholders = ",".join("?" for _ in req.card_ids)
    params.extend(req.card_ids)
    conn = get_db()
    try:
        cur = conn.execute(
            f"UPDATE cards SET {', '.join(updates)} WHERE id IN ({placeholders})",
            params,
        )
        conn.commit()
        return {"updated_count": cur.rowcount}
    finally:
        conn.close()


@app.post("/api/cards/bulk-delete")
def bulk_delete_cards(req: BulkDeleteRequest):
    if not req.card_ids:
        return {"deleted_count": 0}
    conn = get_db()
    try:
        placeholders = ",".join("?" for _ in req.card_ids)
        rows = conn.execute(
            f"SELECT raw_front_image, raw_back_image, cert_front_image, cert_back_image FROM cards WHERE id IN ({placeholders})",
            req.card_ids,
        ).fetchall()
        for r in rows:
            for col in ("raw_front_image", "raw_back_image", "cert_front_image", "cert_back_image"):
                delete_upload_if_local(r[col])
        cur = conn.execute(f"DELETE FROM cards WHERE id IN ({placeholders})", req.card_ids)
        conn.commit()
        return {"deleted_count": cur.rowcount}
    finally:
        conn.close()


@app.delete("/api/cards/{card_id}")
def delete_card(card_id: int):
    conn = get_db()
    try:
        card = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
        if not card:
            raise HTTPException(status_code=404, detail="Card not found.")
        for col in ("raw_front_image", "raw_back_image", "cert_front_image", "cert_back_image"):
            delete_upload_if_local(card[col])
        conn.execute("DELETE FROM cards WHERE id = ?", (card_id,))
        conn.commit()
        return {"deleted": True}
    finally:
        conn.close()


@app.get("/api/infographic/card/{card_id}")
def infographic_card(card_id: int, theme: str = "water"):
    card = get_card(card_id)
    png_bytes = generate_card_infographic(card, UPLOADS_DIR, theme_name=theme)
    return Response(content=png_bytes, media_type="image/png")


@app.get("/api/infographic/submission/{sub_id}")
def infographic_submission(sub_id: int, theme: str = "water"):
    sub = get_submission(sub_id)
    cards = sub.get("cards", [])
    total_acq = sum(float(c.get("acquisition_cost") or 0) for c in cards)
    total_grd = sum(float(c.get("grading_cost") or 0) for c in cards)
    total_shp = sum(float(c.get("shipping_cost") or 0) for c in cards)
    total_sold = sum(float(c["sold_price"]) for c in cards if c["status"] == "Sold" and c["sold_price"] is not None)
    realized_profit = sum(float(c["profit"]) for c in cards if c.get("profit") is not None)

    stats = {
        "total_cards": len(cards),
        "completed_grading": sum(1 for c in cards if c["status"] != "In Grading"),
        "still_grading": sum(1 for c in cards if c["status"] == "In Grading"),
        "sold_cards": sum(1 for c in cards if c["status"] == "Sold"),
        "total_acquisition_cost": round(total_acq, 2),
        "total_grading_cost": round(total_grd, 2),
        "total_collective_cost": round(total_acq + total_grd + total_shp, 2),
        "total_sold_revenue": round(total_sold, 2),
        "realized_profit": round(realized_profit, 2),
        "total_market_value_unsold": sub.get("total_market_value_unsold", 0.0),
        "realized_roi_pct": sub.get("realized_roi_pct"),
        "est_roi_pct": sub.get("est_roi_pct"),
        "overall_roi_pct": sub.get("overall_roi_pct"),
        "gem_rate": sub.get("gem_rate", 0.0),
        "gem_10_count": sub.get("gem_10_count", 0),
        "graded_with_grade_count": sub.get("graded_with_grade_count", 0),
    }
    previews = [c["cert_front_image"] for c in cards if c.get("cert_front_image")]
    subtitle = f"Submitted {sub.get('submitted_date') or 'N/A'}  •  Status: {sub.get('status')}"
    png_bytes = generate_summary_infographic(
        sub["title"],
        subtitle,
        stats,
        sub.get("grade_breakdown", []),
        previews,
        UPLOADS_DIR,
        theme_name=theme,
        company_badge=sub["company"],
    )
    return Response(content=png_bytes, media_type="image/png")


@app.get("/api/infographic/overall")
def infographic_overall(theme: str = "water"):
    stats = get_stats()
    conn = get_db()
    try:
        cards = [dict(r) for r in conn.execute("SELECT * FROM cards").fetchall()]
    finally:
        conn.close()
    previews = [c["cert_front_image"] for c in cards if c.get("cert_front_image")]
    subtitle = f"All Combined History  •  {stats['total_submissions']} Submissions  •  {stats['total_cards']} Cards Tracked"
    png_bytes = generate_summary_infographic(
        "Complete Grading History",
        subtitle,
        stats,
        stats.get("grade_breakdown", []),
        previews,
        UPLOADS_DIR,
        theme_name=theme,
    )
    return Response(content=png_bytes, media_type="image/png")


def infer_company_from_sheet(sheet_name: str, default_fanatics_company: str = "CGC") -> str:
    low = sheet_name.strip().lower()
    if "psa" in low:
        return "PSA"
    if "bgs" in low or "beckett" in low:
        return "Beckett"
    if "cgc" in low:
        return "CGC"
    if "fanatics" in low:
        return default_fanatics_company if default_fanatics_company in ("PSA", "CGC", "Beckett") else "CGC"
    return "PSA"


def format_excel_date(val) -> str:
    if val is None:
        return ""
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%d")
    s = str(val).strip()
    if not s or s.lower() == "none":
        return ""
    return s[:10]


@app.post("/api/import-excel")
def import_excel(
    file: UploadFile = File(...),
    fanatics_company: str = Form("CGC"),
):
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(status_code=400, detail="Please upload a valid .xlsx Excel file.")

    raw_bytes = file.file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    try:
        wb = openpyxl.load_workbook(io.BytesIO(raw_bytes), data_only=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not read Excel workbook: {exc}")

    conn = get_db()
    submissions_created = 0
    cards_imported = 0

    try:
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            if ws.max_row < 2:
                continue

            headers = {}
            for c in range(1, ws.max_column + 1):
                h = str(ws.cell(1, c).value or "").strip().lower()
                if h:
                    headers[h] = c

            if "card" not in headers:
                continue

            company = infer_company_from_sheet(sheet_name, fanatics_company)
            grouped_rows = {}

            for r in range(2, ws.max_row + 1):
                card_cell = ws.cell(r, headers["card"])
                card_name = str(card_cell.value or "").strip()
                if not card_name or card_name.upper() == "TOTAL":
                    continue

                card_link = card_cell.hyperlink.target if card_cell.hyperlink else ""

                def col_val(key):
                    idx = headers.get(key)
                    return ws.cell(r, idx).value if idx else None

                def col_link(key):
                    idx = headers.get(key)
                    if not idx:
                        return ""
                    cell = ws.cell(r, idx)
                    return cell.hyperlink.target if cell.hyperlink else ""

                shipped_date = format_excel_date(col_val("date shipped"))
                batch_key = shipped_date or "Unscheduled"

                grade_raw = str(col_val("grade received") or "").strip()
                actual_grade = "" if grade_raw.lower() in ("", "not yet graded", "none") else grade_raw

                raw_cost = parse_float(str(col_val("raw cost") or 0), 0.0)
                ship_cost = parse_float(str(col_val("shipping") or 0), 0.0)
                grade_cost = parse_float(str(col_val("grading") or 0), 0.0)
                sold_val = parse_opt_float(str(col_val("sold")) if col_val("sold") is not None else None)

                cert_cell_val = str(col_val("cert") or "").strip()
                cert_hl = col_link("cert")
                cert_raw = cert_hl or cert_cell_val
                cert_num = extract_cert_number(cert_raw)
                cert_url = cert_raw if cert_raw.startswith("http") else build_cert_url(company, cert_num)

                listing_url = card_link or col_link("sold")
                if not listing_url and cert_raw.startswith("http") and "fanatics" in cert_raw.lower():
                    listing_url = cert_raw

                date_finished = format_excel_date(col_val("date grading finished"))
                date_received = format_excel_date(col_val("date received"))
                note_parts = []
                if date_received:
                    note_parts.append(f"Received: {date_received}")
                if date_finished:
                    note_parts.append(f"Grading Finished: {date_finished}")

                card_status = resolve_card_status("In Grading", sold_val, cert_num, actual_grade, listing_url)

                grouped_rows.setdefault(batch_key, []).append({
                    "card_name": card_name,
                    "acquisition_cost": raw_cost,
                    "shipping_cost": ship_cost,
                    "grading_cost": grade_cost,
                    "sold_price": sold_val,
                    "actual_grade": actual_grade,
                    "cert_number": cert_num,
                    "cert_lookup_url": cert_url,
                    "fanatics_url": listing_url,
                    "status": card_status,
                    "notes": " | ".join(note_parts),
                })

            for batch_date, items in grouped_rows.items():
                if not items:
                    continue
                done_cnt = sum(1 for it in items if it["status"] != "In Grading")
                if done_cnt == 0:
                    sub_status = "In Grading"
                elif done_cnt == len(items):
                    sub_status = "Completed"
                else:
                    sub_status = "Partially Graded"

                sub_title = f"{sheet_name} • {batch_date}"
                ts = now_iso()
                cur = conn.execute(
                    """
                    INSERT INTO submissions (title, company, submission_number, submitted_date, status, notes, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        sub_title,
                        company,
                        "",
                        "" if batch_date == "Unscheduled" else batch_date,
                        sub_status,
                        f"Imported from {file.filename} ({sheet_name})",
                        ts,
                        ts,
                    ),
                )
                sub_id = cur.lastrowid
                submissions_created += 1

                for it in items:
                    conn.execute(
                        """
                        INSERT INTO cards (
                            submission_id, card_name, acquisition_cost, grading_cost, shipping_cost,
                            cert_number, cert_lookup_url, actual_grade, status, fanatics_url,
                            sold_price, notes, created_at, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            sub_id,
                            it["card_name"],
                            it["acquisition_cost"],
                            it["grading_cost"],
                            it["shipping_cost"],
                            it["cert_number"],
                            it["cert_lookup_url"],
                            it["actual_grade"],
                            it["status"],
                            it["fanatics_url"],
                            it["sold_price"],
                            it["notes"],
                            ts,
                            ts,
                        ),
                    )
                    cards_imported += 1

        conn.commit()
        return {
            "submissions_created": submissions_created,
            "cards_imported": cards_imported,
            "message": f"Imported {cards_imported} cards across {submissions_created} submissions.",
        }
    finally:
        conn.close()


app.mount("/uploads", StaticFiles(directory=str(UPLOADS_DIR)), name="uploads")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")





class OpenExternalRequest(BaseModel):
    url: str


@app.post("/api/open-external")
def open_external_in_default_browser(req: OpenExternalRequest):
    import webbrowser
    u = (req.url or "").strip()
    if not u.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Valid http(s) URL required.")
    webbrowser.open(u)
    return {"ok": True, "url": u}


@app.post("/api/shutdown")
def shutdown_server():
    def _exit_soon():
        time.sleep(0.35)
        os._exit(0)
    threading.Thread(target=_exit_soon, daemon=True).start()
    return {"ok": True, "message": "TCG Grading Vault server is stopping."}


@app.get("/api/check-updates")
def check_github_updates():
    ver_file = BASE_DIR / "version.txt"
    cur_ver = ver_file.read_text("utf-8").strip() if ver_file.exists() else "0.80"
    def _ver_tuple(v: str):
        nums = [int(x) for x in re.findall(r"\d+", v or "")]
        return tuple(nums) if nums else (0,)
    try:
        r = requests.get(
            "https://api.github.com/repos/CellbineFiles/tcg-grading-vault/releases",
            headers={"Accept": "application/vnd.github+json", "User-Agent": "TCG-Grading-Vault"},
            timeout=8,
        )
        if r.status_code != 200:
            return {"ok": False, "current_version": cur_ver, "message": f"GitHub returned HTTP {r.status_code}."}
        releases = r.json() or []
        if not releases:
            return {"ok": True, "current_version": cur_ver, "update_available": False, "message": "No releases published on GitHub yet."}
        latest = releases[0]
        tag = (latest.get("tag_name") or "").lstrip("vV").strip()
        assets = latest.get("assets") or []
        dl_url = assets[0].get("browser_download_url") if assets else (latest.get("zipball_url") or latest.get("html_url"))
        is_newer = _ver_tuple(tag) > _ver_tuple(cur_ver)
        return {
            "ok": True,
            "current_version": cur_ver,
            "latest_version": tag or latest.get("name") or "",
            "update_available": is_newer,
            "release_url": latest.get("html_url") or "https://github.com/CellbineFiles/tcg-grading-vault/releases",
            "download_url": dl_url or "",
            "release_title": latest.get("name") or f"v{tag}",
            "message": f"Update available: v{tag}!" if is_newer else f"You're up to date (v{cur_ver}).",
        }
    except Exception as exc:
        return {"ok": False, "current_version": cur_ver, "message": f"Could not reach GitHub: {exc}"}


def _render_auction_snapshot_html(u: str, info: dict) -> str:
    import html as _html
    title = _html.escape(str(info.get("title") or u))
    platform = _html.escape(str(info.get("platform") or ("Fanatics" if "fanaticscollect" in u.lower() else "eBay")))
    status = _html.escape(str(info.get("status") or "Active"))
    time_left = _html.escape(str(info.get("time_left") or ("ENDED" if status == "Ended" else "Live")))
    bid_count = int(info.get("bid_count") or 0)
    val = info.get("final_price") if info.get("final_price") is not None else (info.get("current_bid") or 0.0)
    val_str = f"${float(val):,.2f}"
    img_url = str(info.get("image_url") or "").strip()
    img_block = (
        f'<img src="{_html.escape(img_url)}" alt="{title}" style="max-width:260px;max-height:360px;object-fit:contain;border-radius:10px;border:1px solid #334155;background:#0b1220;" />'
        if img_url else '<div style="width:220px;height:300px;display:flex;align-items:center;justify-content:center;border:1px dashed #334155;border-radius:10px;color:#94a3b8;">No Image</div>'
    )
    safe_u = _html.escape(u)
    return f"""<!doctype html><html><body style="margin:0;background:#0f172a;color:#f8fafc;font-family:system-ui,-apple-system,sans-serif;display:flex;align-items:center;justify-content:center;min-height:94vh;padding:20px;">
      <div style="max-width:560px;width:100%;background:#131c31;border:1px solid #334155;border-radius:16px;padding:24px;display:flex;gap:20px;align-items:center;flex-wrap:wrap;justify-content:center;">
        <div>{img_block}</div>
        <div style="flex:1;min-width:220px;">
          <div style="display:flex;gap:8px;align-items:center;margin-bottom:8px;">
            <span style="background:#0284c7;color:#fff;font-size:11px;font-weight:800;padding:3px 9px;border-radius:999px;">{platform}</span>
            <span style="color:#94a3b8;font-size:12px;">{status} • {time_left} • {bid_count} bids</span>
          </div>
          <h2 style="margin:0 0 12px;font-size:18px;line-height:1.3;">{title}</h2>
          <div style="background:#0b1220;border:1px solid #334155;border-radius:10px;padding:12px 16px;margin-bottom:14px;">
            <div style="font-size:11px;font-weight:700;color:#94a3b8;text-transform:uppercase;">{"Final Bid" if status == "Ended" else "Current Bid"}</div>
            <div style="font-size:28px;font-weight:900;color:#38bdf8;">{val_str}</div>
          </div>
          <a href="{safe_u}" target="_blank" rel="noopener noreferrer" style="display:inline-block;padding:11px 20px;background:#0284c7;color:#fff;text-decoration:none;border-radius:10px;font-weight:700;font-size:14px;">↗ Open Live Listing in Browser</a>
          <p style="color:#64748b;font-size:12px;margin:10px 0 0;line-height:1.4;">Opens directly in your default browser with your active eBay / Fanatics Collect login.</p>
        </div>
      </div>
    </body></html>"""


@app.get("/api/proxy-preview")
def proxy_preview_page(url: str, inline_nav: int = 0):
    from urllib.parse import urljoin, quote
    u = (url or "").strip()
    if not u.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Valid http(s) URL required.")
    if "ebay.com/itm/" in u.lower():
        m_itm = re.search(r"(https?://[^/]+/itm/(?:[^/?#]+/)?\d+)", u, re.I)
        if m_itm:
            u = m_itm.group(1)

    # Check if this URL is a tracked auction in our DB first
    db_auc = None
    try:
        conn = get_db()
        try:
            row = conn.execute("SELECT * FROM auctions WHERE url = ? OR url LIKE ? LIMIT 1", (u, f"{u}%")).fetchone()
            if row:
                db_auc = dict(row)
        finally:
            conn.close()
    except Exception:
        pass

    is_auction_site = any(h in u.lower() for h in ("ebay.com", "fanaticscollect.com", "pwccmarketplace.com"))
    if is_auction_site and not inline_nav:
        info = db_auc or _scrape_auction_url(u)
        return Response(content=_render_auction_snapshot_html(u, info), media_type="text/html")

    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        }
        resp = requests.get(u, headers=headers, timeout=12, allow_redirects=True)
        html = resp.text
        final_url = resp.url
        if any(k in html.lower() for k in ("error page", "pardon our interruption", "just a moment", "verify you are human")):
            try:
                pw_u, pw_html, _ = fetch_html_with_playwright(u, visible=False, max_wait_s=12)
                if pw_html and "error page" not in pw_html.lower():
                    html = pw_html
                    final_url = pw_u or final_url
            except Exception:
                pass
        low_h = html.lower()
        if any(k in low_h for k in ("error page", "something went wrong on our end", "pardon our interruption", "just a moment", "verify you are human")):
            info = db_auc or _scrape_auction_url(u)
            return Response(content=_render_auction_snapshot_html(u, info), media_type="text/html")

        html = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.I | re.S)
        if inline_nav:
            soup = BeautifulSoup(html, "html.parser")
            for b_tag in soup.find_all("base"):
                b_tag.decompose()
            for tag_name, attr in (("img", "src"), ("link", "href"), ("source", "src")):
                for el in soup.find_all(tag_name):
                    val = (el.get(attr) or "").strip()
                    if val and not val.startswith(("data:", "http://", "https://", "blob:")):
                        el[attr] = urljoin(final_url, val)
            for a_tag in soup.find_all("a", href=True):
                raw_href = (a_tag.get("href") or "").strip()
                if not raw_href or raw_href.startswith(("#", "javascript:", "mailto:")):
                    continue
                abs_href = urljoin(final_url, raw_href)
                if abs_href.startswith(("http://", "https://")):
                    a_tag["href"] = f"/api/proxy-preview?inline_nav=1&url={quote(abs_href, safe='')}"
                    if a_tag.has_attr("target"):
                        del a_tag["target"]
            for f_tag in soup.find_all("form"):
                act = (f_tag.get("action") or "").strip()
                if act and not act.startswith(("http://", "https://")):
                    f_tag["action"] = urljoin(final_url, act)
                f_tag["target"] = "_blank"
            nav_script = soup.new_tag("script")
            nav_script.string = f'try{{window.parent.postMessage({{type:"inapp-nav",url:{json.dumps(final_url)}}},"*");}}catch(e){{}}'
            (soup.body or soup).append(nav_script)
            html = str(soup)
        else:
            base_tag = f'<base href="{final_url}" target="_blank" />'
            if "<head" in html.lower():
                html = re.sub(r"<head[^>]*>", lambda m: m.group(0) + base_tag, html, count=1, flags=re.I)
            else:
                html = base_tag + html
        return Response(content=html, media_type="text/html")
    except Exception as exc:
        return Response(
            content=f"<html><body style='background:#0f172a;color:#e2e8f0;font-family:sans-serif;padding:24px;'><h3>Could not load inline preview</h3><p>{exc}</p><p><a href='{u}' target='_blank' style='color:#38bdf8;'>Open in external browser ↗</a></p></body></html>",
            media_type="text/html",
        )

class MultiCardInfographicRequest(BaseModel):
    card_ids: list[int] = []
    theme: str = "water"
    title: str = "Selected Cards Showcase"


@app.post("/api/infographic/cards")
def infographic_multi_cards(req: MultiCardInfographicRequest):
    if not req.card_ids:
        raise HTTPException(status_code=400, detail="Select at least one card.")
    conn = get_db()
    try:
        placeholders = ",".join("?" for _ in req.card_ids)
        rows = [
            dict(r)
            for r in conn.execute(
                f"""
                SELECT c.*, s.company AS submission_company, s.title AS submission_title
                FROM cards c JOIN submissions s ON s.id = c.submission_id
                WHERE c.id IN ({placeholders})
                """,
                req.card_ids,
            ).fetchall()
        ]
        png_bytes = generate_multi_cards_infographic(rows[:4], UPLOADS_DIR, theme_name=req.theme, title=req.title)
        return Response(content=png_bytes, media_type="image/png")
    finally:
        conn.close()


def _scrape_auction_url(url: str) -> dict:
    u = (url or "").strip()
    low = u.lower()
    platform = "Fanatics" if ("fanaticscollect" in low or "pwcc" in low) else "eBay"
    out = {
        "platform": platform,
        "title": u,
        "image_url": "",
        "current_bid": 0.0,
        "bid_count": 0,
        "time_left": "",
        "status": "Active",
    }
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }
    if "ebay.com/itm/" in low:
        m_itm = re.search(r"(https?://[^/]+/itm/(?:[^/?#]+/)?\d+)", u, re.I)
        if m_itm:
            u = m_itm.group(1)
    try:
        resp = requests.get(u, headers=headers, timeout=12, allow_redirects=True)
        html = resp.text
        if any(k in html.lower() for k in ("error page", "pardon our interruption", "just a moment", "verify you are human")) or (
            platform == "eBay" and "og:title" not in html.lower()
        ):
            try:
                _, pw_html, _ = fetch_html_with_playwright(u, visible=False, max_wait_s=12)
                if pw_html and "error page" not in pw_html.lower():
                    html = pw_html
            except Exception:
                pass
        soup = BeautifulSoup(html, "html.parser")
        og_t = soup.find("meta", attrs={"property": "og:title"})
        h1 = soup.find("h1")
        t_txt = ((og_t.get("content") if og_t else "") or (h1.get_text(" ", strip=True) if h1 else "") or (soup.title.get_text(" ", strip=True) if soup.title else "")).strip()
        t_txt = re.sub(r"\s*\|\s*eBay\s*$", "", t_txt, flags=re.I).strip()
        if t_txt:
            out["title"] = t_txt

        og_img = soup.find("meta", attrs={"property": "og:image"})
        if og_img and og_img.get("content"):
            out["image_url"] = og_img["content"].strip()

        page_txt = soup.get_text(" ", strip=True)
        # Current bid / price
        for pat in [
            r'"currentBid"\s*:\s*\{[^}]*"value"\s*:\s*"?(\d+(?:\.\d{1,2})?)"?',
            r'"price"\s*:\s*"?(\d+(?:\.\d{1,2})?)"?',
            r"(?:Current bid|Winning bid|US)\s*\$\s*(\d+(?:,\d{3})*(?:\.\d{2})?)",
            r"\$\s*(\d+(?:,\d{3})*(?:\.\d{2})?)",
        ]:
            m = re.search(pat, html if pat.startswith('"') else page_txt, re.I)
            if m:
                out["current_bid"] = float(m.group(1).replace(",", ""))
                break

        # Bid count
        m_bids = re.search(r"\b(\d{1,4})\s*bids?\b", page_txt, re.I) or re.search(r'"bidCount"\s*:\s*(\d+)', html, re.I)
        if m_bids:
            out["bid_count"] = int(m_bids.group(1))

        # Ended vs Time left
        if re.search(r"\b(bidding has ended|auction ended|sold for|winning bid)\b", page_txt, re.I):
            out["status"] = "Ended"
            out["time_left"] = "ENDED"
        else:
            m_tl = re.search(r"(?:Ends in|Time left)[:\s]*([0-9]+[dhms\s0-9]*)", page_txt, re.I)
            if m_tl:
                out["time_left"] = m_tl.group(1).strip()[:24]
    except Exception:
        pass
    return out


class AuctionCreateRequest(BaseModel):
    urls: list[str] = []
    cost: float = 0.0


class AuctionUpdateRequest(BaseModel):
    title: Optional[str] = None
    image_url: Optional[str] = None
    current_bid: Optional[float] = None
    bid_count: Optional[int] = None
    time_left: Optional[str] = None
    status: Optional[str] = None
    cost: Optional[float] = None
    final_price: Optional[float] = None


class AuctionLinkCardRequest(BaseModel):
    card_id: int
    selling_fees: float = 0.0


@app.get("/api/auctions")
def list_auctions():
    conn = get_db()
    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM auctions ORDER BY id DESC").fetchall()]
        return rows
    finally:
        conn.close()


@app.post("/api/auctions")
def add_auctions(req: AuctionCreateRequest):
    urls = [u.strip() for u in req.urls if u and u.strip().startswith(("http://", "https://"))]
    if not urls:
        raise HTTPException(status_code=400, detail="Enter at least one valid http(s) auction URL.")
    conn = get_db()
    created = []
    ts = now_iso()
    try:
        for u in urls:
            info = _scrape_auction_url(u)
            cur = conn.execute(
                """
                INSERT INTO auctions (url, platform, title, image_url, current_bid, bid_count, time_left, status, cost, last_refreshed, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    u,
                    info["platform"],
                    info["title"],
                    info["image_url"],
                    info["current_bid"],
                    info["bid_count"],
                    info["time_left"],
                    info["status"],
                    float(req.cost or 0.0),
                    ts,
                    ts,
                ),
            )
            created.append(dict(conn.execute("SELECT * FROM auctions WHERE id = ?", (cur.lastrowid,)).fetchone()))
        conn.commit()
        return {"created": created, "count": len(created)}
    finally:
        conn.close()


@app.post("/api/auctions/refresh")
def refresh_all_auctions():
    conn = get_db()
    ts = now_iso()
    try:
        rows = conn.execute("SELECT * FROM auctions").fetchall()
        updated = 0
        for r in rows:
            info = _scrape_auction_url(r["url"])
            conn.execute(
                """
                UPDATE auctions
                SET title = COALESCE(NULLIF(?, ''), title),
                    image_url = COALESCE(NULLIF(?, ''), image_url),
                    current_bid = ?,
                    bid_count = ?,
                    time_left = ?,
                    status = ?,
                    last_refreshed = ?
                WHERE id = ?
                """,
                (
                    info["title"] if info["title"] != r["url"] else "",
                    info["image_url"],
                    info["current_bid"] if info["current_bid"] > 0 else r["current_bid"],
                    info["bid_count"] if info["bid_count"] > 0 else r["bid_count"],
                    info["time_left"] or r["time_left"],
                    info["status"],
                    ts,
                    r["id"],
                ),
            )
            updated += 1
        conn.commit()
        return {"updated": updated, "auctions": [dict(x) for x in conn.execute("SELECT * FROM auctions ORDER BY id DESC").fetchall()]}
    finally:
        conn.close()


@app.post("/api/auctions/{auction_id}/refresh")
def refresh_single_auction(auction_id: int):
    conn = get_db()
    try:
        r = conn.execute("SELECT * FROM auctions WHERE id = ?", (auction_id,)).fetchone()
        if not r:
            raise HTTPException(status_code=404, detail="Auction not found.")
        info = _scrape_auction_url(r["url"])
        ts = now_iso()
        conn.execute(
            """
            UPDATE auctions
            SET title = COALESCE(NULLIF(?, ''), title),
                image_url = COALESCE(NULLIF(?, ''), image_url),
                current_bid = ?,
                bid_count = ?,
                time_left = ?,
                status = ?,
                last_refreshed = ?
            WHERE id = ?
            """,
            (
                info["title"] if info["title"] != r["url"] else "",
                info["image_url"],
                info["current_bid"] if info["current_bid"] > 0 else r["current_bid"],
                info["bid_count"] if info["bid_count"] > 0 else r["bid_count"],
                info["time_left"] or r["time_left"],
                info["status"],
                ts,
                auction_id,
            ),
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM auctions WHERE id = ?", (auction_id,)).fetchone())
    finally:
        conn.close()


@app.put("/api/auctions/{auction_id}")
def update_auction(auction_id: int, req: AuctionUpdateRequest):
    conn = get_db()
    try:
        r = conn.execute("SELECT * FROM auctions WHERE id = ?", (auction_id,)).fetchone()
        if not r:
            raise HTTPException(status_code=404, detail="Auction not found.")
        fields = []
        vals = []
        for k in ("title", "image_url", "current_bid", "bid_count", "time_left", "status", "cost", "final_price"):
            v = getattr(req, k)
            if v is not None:
                fields.append(f"{k} = ?")
                vals.append(v)
        if fields:
            vals.append(auction_id)
            conn.execute(f"UPDATE auctions SET {', '.join(fields)} WHERE id = ?", vals)
            conn.commit()
        return dict(conn.execute("SELECT * FROM auctions WHERE id = ?", (auction_id,)).fetchone())
    finally:
        conn.close()


@app.delete("/api/auctions")
def clear_all_auctions():
    conn = get_db()
    try:
        conn.execute("DELETE FROM auctions")
        conn.commit()
        return {"cleared": True}
    finally:
        conn.close()


@app.delete("/api/auctions/{auction_id}")
def delete_auction(auction_id: int):
    conn = get_db()
    try:
        conn.execute("DELETE FROM auctions WHERE id = ?", (auction_id,))
        conn.commit()
        return {"deleted": True}
    finally:
        conn.close()


@app.post("/api/auctions/{auction_id}/link-card")
def link_auction_to_vault_card(auction_id: int, req: AuctionLinkCardRequest):
    conn = get_db()
    try:
        auc = conn.execute("SELECT * FROM auctions WHERE id = ?", (auction_id,)).fetchone()
        if not auc:
            raise HTTPException(status_code=404, detail="Auction not found.")
        card = conn.execute("SELECT * FROM cards WHERE id = ?", (req.card_id,)).fetchone()
        if not card:
            raise HTTPException(status_code=404, detail="Card not found.")
        sale_price = float(auc["final_price"] if auc["final_price"] is not None else auc["current_bid"] or 0.0)
        ts = now_iso()
        conn.execute(
            """
            UPDATE cards
            SET status = 'Sold', sold_price = ?, selling_fees = ?, sold_date = ?, updated_at = ?
            WHERE id = ?
            """,
            (sale_price, float(req.selling_fees or 0.0), datetime.now().strftime("%Y-%m-%d"), ts, req.card_id),
        )
        conn.execute(
            "UPDATE auctions SET linked_card_id = ?, status = 'Ended', final_price = ? WHERE id = ?",
            (req.card_id, sale_price, auction_id),
        )
        conn.commit()
        return {"linked": True, "card": get_card(req.card_id), "sold_price": sale_price}
    finally:
        conn.close()


@app.get("/api/auctions/export-html")
def export_auctions_html_report():
    conn = get_db()
    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM auctions ORDER BY id DESC").fetchall()]
        total_items = len(rows)
        ended_rows = [r for r in rows if (r.get("status") or "") == "Ended"]
        total_cost = sum(float(r.get("cost") or 0) for r in rows)
        combined_bids = sum(float(r.get("final_price") if r.get("final_price") is not None else (r.get("current_bid") or 0)) for r in rows)
        sold_total = sum(float(r.get("final_price") if r.get("final_price") is not None else (r.get("current_bid") or 0)) for r in ended_rows)
        cards_html = []
        for r in rows:
            val = float(r.get("final_price") if r.get("final_price") is not None else (r.get("current_bid") or 0))
            img_tag = f'<img src="{r["image_url"]}" alt="item" style="width:100%;height:190px;object-fit:contain;border-radius:8px;" />' if r.get("image_url") else '<div style="height:190px;display:flex;align-items:center;justify-content:center;color:#94a3b8;">No Image</div>'
            cards_html.append(f"""
            <div style="background:#131c31;border:1px solid #334155;border-radius:12px;padding:14px;display:flex;flex-direction:column;gap:8px;">
              <div style="font-weight:700;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{r.get("title") or "Auction"}</div>
              <div style="font-size:12px;color:#94a3b8;">{r.get("platform")} • {r.get("bid_count") or 0} bids • {r.get("time_left") or r.get("status")}</div>
              {img_tag}
              <div style="font-size:20px;font-weight:800;color:#10b981;text-align:center;">${val:,.2f}</div>
              <div style="font-size:12px;color:#cbd5e1;text-align:center;">Cost: ${float(r.get("cost") or 0):,.2f} • <a href="{r.get("url")}" target="_blank" style="color:#38bdf8;">View Listing ↗</a></div>
            </div>""")
        doc = f"""<!doctype html><html><head><meta charset="utf-8"><title>Live Auction Monitor Report</title></head>
        <body style="background:#0f172a;color:#f8fafc;font-family:system-ui,sans-serif;padding:24px;max-width:1280px;margin:0 auto;">
        <h1 style="margin:0 0 8px;">TCG Grading Vault — Auction Monitor Report</h1>
        <p style="color:#94a3b8;">Items: <strong>{total_items}</strong> • Ended/Sold: <strong>{len(ended_rows)}</strong> ($<strong>{sold_total:,.2f}</strong>) • Total Cost: <strong>${total_cost:,.2f}</strong> • Combined Bids: <strong>${combined_bids:,.2f}</strong></p>
        <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:16px;margin-top:18px;">{''.join(cards_html)}</div>
        </body></html>"""
        return Response(
            content=doc,
            media_type="text/html",
            headers={"Content-Disposition": 'attachment; filename="auction_monitor_report.html"'},
        )
    finally:
        conn.close()


@app.get("/")
def serve_index():
    return FileResponse(str(STATIC_DIR / "index.html"))
