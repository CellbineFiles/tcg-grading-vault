import io
import os
import re
from pathlib import Path
from typing import Optional
from PIL import Image, ImageDraw, ImageFont

# Palette definitions matching Pokémon Energy Themes
THEMES = {
    "water": {"bg": (9, 19, 36), "card": (19, 33, 56), "accent": (56, 189, 248), "gem": (103, 232, 249)},
    "fire": {"bg": (22, 12, 12), "card": (35, 20, 20), "accent": (239, 68, 68), "gem": (251, 191, 36)},
    "fighting": {"bg": (22, 16, 11), "card": (36, 26, 19), "accent": (234, 88, 12), "gem": (251, 146, 60)},
    "psychic": {"bg": (19, 11, 32), "card": (31, 20, 51), "accent": (168, 85, 247), "gem": (232, 121, 249)},
    "grass": {"bg": (9, 23, 16), "card": (17, 38, 27), "accent": (16, 185, 129), "gem": (74, 222, 128)},
    "lightning": {"bg": (20, 19, 10), "card": (33, 31, 17), "accent": (234, 179, 8), "gem": (253, 224, 71)},
    "dark": {"bg": (9, 11, 16), "card": (19, 23, 32), "accent": (148, 163, 184), "gem": (226, 232, 240)},
    "light": {"bg": (24, 34, 52), "card": (36, 50, 76), "accent": (96, 165, 250), "gem": (191, 219, 254)},
}

COMPANY_COLORS = {
    "PSA": (220, 38, 38),
    "CGC": (37, 99, 235),
    "Beckett": (217, 119, 6),
}


def _load_font(size: int, bold: bool = False):
    candidates = []
    if bold:
        candidates += [
            "C:/Windows/Fonts/segoeuib.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        ]
    else:
        candidates += [
            "C:/Windows/Fonts/segoeui.ttf",
            "C:/Windows/Fonts/arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
        ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _fmt_money(val) -> str:
    num = float(val or 0.0)
    sign = "-" if num < 0 else ""
    return f"{sign}${abs(num):,.2f}"


def _draw_round_rect(draw: ImageDraw.ImageDraw, xy, radius: int, fill, outline=None, width: int = 1):
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def _paste_card_image(canvas: Image.Image, img_path: Optional[Path], box: tuple, radius: int = 16):
    x1, y1, x2, y2 = box
    bw, bh = x2 - x1, y2 - y1
    if not img_path or not img_path.exists():
        return False
    try:
        im = Image.open(img_path).convert("RGBA")
        im.thumbnail((bw, bh), Image.Resampling.LANCZOS)
        px = x1 + (bw - im.width) // 2
        py = y1 + (bh - im.height) // 2

        mask = Image.new("L", im.size, 0)
        mdraw = ImageDraw.Draw(mask)
        mdraw.rounded_rectangle((0, 0, im.width, im.height), radius=radius, fill=255)
        canvas.paste(im, (px, py), mask)
        return True
    except Exception:
        return False


def _draw_stat_box(draw: ImageDraw.ImageDraw, box, label: str, value: str, sub: str, bg_color, accent_color, val_color=(241, 245, 249), right_stat=None):
    x1, y1, x2, y2 = box
    _draw_round_rect(draw, box, 16, fill=bg_color, outline=(148, 163, 184, 60), width=2)
    draw.line([(x1 + 16, y1 + 2), (x2 - 16, y1 + 2)], fill=accent_color, width=4)

    f_lbl = _load_font(16, bold=True)
    f_val = _load_font(44 if not right_stat else 40, bold=True)
    f_sub = _load_font(15 if len(sub or "") > 36 else 17, bold=False)

    draw.text((x1 + 20, y1 + 15), label.upper(), font=f_lbl, fill=(148, 163, 184))
    draw.text((x1 + 20, y1 + 42), value, font=f_val, fill=val_color)
    if sub:
        draw.text((x1 + 20, y1 + 98), sub[:48], font=f_sub, fill=(148, 163, 184))

    if right_stat:
        r_lbl, r_val, r_sub = right_stat
        mid_x = x1 + int((x2 - x1) * 0.54)
        draw.line([(mid_x, y1 + 20), (mid_x, y2 - 18)], fill=(51, 65, 85), width=2)
        draw.text((mid_x + 18, y1 + 15), r_lbl.upper(), font=_load_font(15, bold=True), fill=accent_color)
        draw.text((mid_x + 18, y1 + 42), r_val, font=_load_font(36, bold=True), fill=val_color)
        if r_sub:
            draw.text((mid_x + 18, y1 + 98), r_sub[:22], font=_load_font(17, bold=True), fill=(203, 213, 225))


def generate_card_infographic(card: dict, uploads_dir: Path, theme_name: str = "water") -> bytes:
    theme = THEMES.get(theme_name, THEMES["water"])
    W, H = 1200, 760
    img = Image.new("RGB", (W, H), theme["bg"])
    draw = ImageDraw.Draw(img)

    # Header bar
    _draw_round_rect(draw, (32, 28, W - 32, H - 28), 24, fill=theme["card"], outline=theme["accent"], width=2)

    f_kicker = _load_font(18, bold=True)
    f_title = _load_font(36, bold=True)
    f_sub = _load_font(20, bold=False)
    f_badge = _load_font(20, bold=True)

    company = card.get("submission_company") or "PSA"
    comp_color = COMPANY_COLORS.get(company, theme["accent"])
    _draw_round_rect(draw, (64, 58, 154, 98), 10, fill=comp_color)
    draw.text((84, 66), company, font=f_badge, fill=(255, 255, 255))

    grade_txt = card.get("actual_grade") or card.get("status") or "In Grading"
    _draw_round_rect(draw, (170, 58, 390, 98), 10, fill=(30, 41, 59), outline=theme["gem"], width=2)
    draw.text((188, 66), f"Grade: {grade_txt}"[:18], font=f_badge, fill=theme["gem"])

    draw.text((W - 310, 66), "TCG GRADING VAULT", font=f_kicker, fill=(148, 163, 184))

    # Left: Card Image (Cert scan preferred, else raw front)
    img_rel = card.get("cert_front_image") or card.get("raw_front_image") or ""
    img_path = (uploads_dir / img_rel) if img_rel and not img_rel.startswith("http") else None
    _draw_round_rect(draw, (64, 126, 430, 690), 18, fill=theme["bg"], outline=(71, 85, 105), width=2)
    pasted = _paste_card_image(img, img_path, (78, 140, 416, 676))
    if not pasted:
        f_miss = _load_font(22, bold=True)
        draw.text((155, 395), "No Card Scan", font=f_miss, fill=(148, 163, 184))

    # Right: Title + Metrics
    rx = 465
    card_name = card.get("card_name") or "Pokémon Card"
    if len(card_name) > 30:
        card_name = card_name[:28] + "..."
    draw.text((rx, 128), card_name, font=f_title, fill=(241, 245, 249))

    sub_line = f"{card.get('set_name') or ''} {('#' + card['card_number']) if card.get('card_number') else ''}".strip()
    if not sub_line:
        sub_line = card.get("submission_title") or ""
    if card.get("cert_number"):
        sub_line = f"{sub_line}  •  Cert #{card['cert_number']}".strip(" •")
    draw.text((rx, 178), sub_line[:52], font=f_sub, fill=(148, 163, 184))

    acq = float(card.get("acquisition_cost") or 0)
    grd = float(card.get("grading_cost") or 0)
    shp = float(card.get("shipping_cost") or 0)
    total_cost = acq + grd + shp
    fees = float(card.get("selling_fees") or 0)

    sold_price = card.get("sold_price")
    market_val = card.get("market_value")

    if sold_price is not None:
        val_label = "SOLD PRICE"
        val_num = float(sold_price)
        profit = val_num - total_cost - fees
        profit_label = "REALIZED PROFIT"
        roi = round((profit / (total_cost + fees)) * 100, 1) if (total_cost + fees) > 0 else 0.0
        profit_sub = f"ROI: {roi:+.1f}% (after {_fmt_money(fees)} fees)"
    elif market_val is not None:
        val_label = "EST. MARKET VALUE"
        val_num = float(market_val)
        profit = val_num - total_cost
        profit_label = "EST. UNREALIZED GAIN"
        roi = round((profit / total_cost) * 100, 1) if total_cost > 0 else 0.0
        profit_sub = f"Est. ROI: {roi:+.1f}% ({card.get('market_value_source') or 'Market'})"
    else:
        val_label = "STATUS"
        val_num = None
        profit = None
        profit_label = "EST. GAIN"
        profit_sub = "Add market value or sale price"

    # 2x2 Stat Grid
    _draw_stat_box(
        draw, (rx, 232, rx + 330, 368),
        "UPFRONT COST",
        _fmt_money(total_cost),
        f"Raw {_fmt_money(acq)} + Grade {_fmt_money(grd)} + Ship {_fmt_money(shp)}",
        theme["bg"], theme["accent"]
    )
    _draw_stat_box(
        draw, (rx + 350, 232, W - 64, 368),
        val_label,
        _fmt_money(val_num) if val_num is not None else (card.get("status") or "In Grading"),
        f"Sold {card.get('sold_date')}" if card.get("sold_date") else "Current slab valuation",
        theme["bg"], (59, 130, 246), val_color=(96, 165, 250)
    )

    p_color = (16, 185, 129) if (profit is not None and profit >= 0) else ((239, 68, 68) if profit is not None else (148, 163, 184))
    _draw_stat_box(
        draw, (rx, 388, rx + 330, 524),
        profit_label,
        _fmt_money(profit) if profit is not None else "Pending",
        profit_sub,
        theme["bg"], p_color, val_color=p_color
    )

    preds = [
        f"C:{card['pred_corners']}" if card.get("pred_corners") is not None else None,
        f"E:{card['pred_edges']}" if card.get("pred_edges") is not None else None,
        f"Ct:{card['pred_centering']}" if card.get("pred_centering") is not None else None,
        f"S:{card['pred_surface']}" if card.get("pred_surface") is not None else None,
    ]
    pred_str = " ".join(p for p in preds if p) or "None entered"
    _draw_stat_box(
        draw, (rx + 350, 388, W - 64, 524),
        "FINAL GRADE",
        str(grade_txt),
        f"Predicted: {pred_str}",
        theme["bg"], theme["gem"], val_color=theme["gem"]
    )

    # Footer banner
    _draw_round_rect(draw, (rx, 550, W - 64, 690), 16, fill=theme["bg"], outline=(51, 65, 85), width=2)
    f_foot_lbl = _load_font(16, bold=True)
    f_foot = _load_font(20, bold=False)
    draw.text((rx + 20, 570), "SUBMISSION & NOTES", font=f_foot_lbl, fill=theme["accent"])
    draw.text((rx + 20, 602), f"Submission: {card.get('submission_title') or 'N/A'}"[:58], font=f_foot, fill=(226, 232, 240))
    if card.get("notes"):
        draw.text((rx + 20, 638), f"Notes: {card['notes']}"[:58], font=f_foot, fill=(148, 163, 184))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _us_federal_holidays(year: int) -> set:
    from datetime import date, timedelta
    holidays = set()

    def add_observed(m: int, d: int):
        dt = date(year, m, d)
        if dt.weekday() == 5:  # Sat -> Fri
            dt = dt - timedelta(days=1)
        elif dt.weekday() == 6:  # Sun -> Mon
            dt = dt + timedelta(days=1)
        holidays.add(dt)

    def nth_weekday(m: int, weekday: int, n: int):
        first = date(year, m, 1)
        offset = (weekday - first.weekday()) % 7
        return first + timedelta(days=offset + (n - 1) * 7)

    def last_weekday(m: int, weekday: int):
        if m == 12:
            last = date(year, 12, 31)
        else:
            last = date(year, m + 1, 1) - timedelta(days=1)
        offset = (last.weekday() - weekday) % 7
        return last - timedelta(days=offset)

    add_observed(1, 1)
    holidays.add(nth_weekday(1, 0, 3))
    holidays.add(nth_weekday(2, 0, 3))
    holidays.add(last_weekday(5, 0))
    add_observed(6, 19)
    add_observed(7, 4)
    holidays.add(nth_weekday(9, 0, 1))
    holidays.add(nth_weekday(10, 0, 2))
    add_observed(11, 11)
    holidays.add(nth_weekday(11, 3, 4))
    add_observed(12, 25)
    return holidays


def _calc_grading_days(start_date_str: str, end_date_str: str = ""):
    from datetime import date, timedelta
    s_str = (start_date_str or "").strip()
    e_str = (end_date_str or "").strip()
    if not s_str:
        return None
    try:
        start = date.fromisoformat(s_str[:10])
    except Exception:
        return None
    stopped = False
    if e_str:
        try:
            end = date.fromisoformat(e_str[:10])
            stopped = True
        except Exception:
            end = date.today()
    else:
        end = date.today()
    if start > end:
        return {"biz": 0, "cal": 0, "stopped": stopped, "start": s_str[:10], "end": e_str[:10] if stopped else ""}
    cal = (end - start).days
    hols = set()
    for yr in range(start.year, end.year + 2):
        hols |= _us_federal_holidays(yr)
    biz = 0
    cur = start + timedelta(days=1)
    while cur <= end:
        if cur.weekday() < 5 and cur not in hols:
            biz += 1
        cur += timedelta(days=1)
    return {"biz": biz, "cal": cal, "stopped": stopped, "start": s_str[:10], "end": e_str[:10] if stopped else ""}


def generate_summary_infographic(
    title: str,
    subtitle: str,
    stats: dict,
    breakdown: list,
    preview_paths: list,
    uploads_dir: Path,
    theme_name: str = "water",
    company_badge: str = "",
    grading_started_date: str = "",
    grading_finished_date: str = "",
    featured_label: str = "FEATURED SLABS",
) -> bytes:
    theme = THEMES.get(theme_name, THEMES["water"])
    W, H = 1360, 920
    img = Image.new("RGB", (W, H), theme["bg"])
    draw = ImageDraw.Draw(img)

    _draw_round_rect(draw, (28, 24, W - 28, H - 24), 24, fill=theme["card"], outline=theme["accent"], width=2)

    f_title = _load_font(38, bold=True)
    f_sub = _load_font(20, bold=False)
    f_kicker = _load_font(18, bold=True)

    x_start = 60
    if company_badge:
        comp_color = COMPANY_COLORS.get(company_badge, theme["accent"])
        _draw_round_rect(draw, (60, 54, 152, 94), 10, fill=comp_color)
        draw.text((78, 62), company_badge, font=_load_font(20, bold=True), fill=(255, 255, 255))
        x_start = 170

    draw.text((x_start, 50), title[:38], font=f_title, fill=(241, 245, 249))
    draw.text((60, 102), subtitle[:86], font=f_sub, fill=(148, 163, 184))
    draw.text((W - 290, 58), "TCG GRADING VAULT", font=f_kicker, fill=theme["accent"])

    # 3x2 Stat Cards
    bw = 396
    bh = 138
    gap_x = 25
    gap_y = 20
    top_y = 152

    cards_spec = [
        ("COMPLETED / IN GRADING", f"{stats.get('completed_grading', 0)} / {stats.get('still_grading', 0)}", f"{stats.get('total_cards', 0)} total cards", theme["accent"], (241, 245, 249)),
        ("GEM RATE (10s)", f"{stats.get('gem_rate', 0)}%", f"{stats.get('gem_10_count', 0)} of {stats.get('graded_with_grade_count', 0)} hit 10", theme["gem"], theme["gem"]),
        ("UPFRONT TOTAL COSTS", _fmt_money(stats.get("total_collective_cost", 0)), f"Raw {_fmt_money(stats.get('total_acquisition_cost', 0))} • Grade {_fmt_money(stats.get('total_grading_cost', 0))}", (148, 163, 184), (241, 245, 249)),
        ("TOTAL SOLD REVENUE", _fmt_money(stats.get("total_sold_revenue", 0)), f"{stats.get('sold_cards', 0)} cards sold", (59, 130, 246), (96, 165, 250)),
        ("REALIZED PROFIT (SOLD)", _fmt_money(stats.get("realized_profit", 0)), (f"Realized ROI: {float(stats.get('realized_roi_pct') or 0):+.1f}% • Overall: {float(stats.get('overall_roi_pct') or 0):+.1f}%" if stats.get("overall_roi_pct") is not None or stats.get("realized_roi_pct") is not None else "Sold revenue minus sold card costs"), (16, 185, 129) if float(stats.get("realized_profit", 0)) >= 0 else (239, 68, 68), (16, 185, 129) if float(stats.get("realized_profit", 0)) >= 0 else (239, 68, 68)),
        ("EST. GRADED VALUE (UNSOLD)", _fmt_money(stats.get("total_market_value_unsold", 0)), (f"Unsold Est. ROI: {float(stats.get('unsold_est_roi_pct') if stats.get('unsold_est_roi_pct') is not None else (stats.get('est_roi_pct') or 0)):+.1f}% • Slab market value" if (stats.get("unsold_est_roi_pct") is not None or stats.get("est_roi_pct") is not None) else "Tracked current slab market value"), theme["accent"], theme["accent"]),
    ]

    pristine_item = next((b for b in (breakdown or []) if str(b.get("grade", "")).strip().lower() == "pristine 10" and int(b.get("count", 0)) > 0), None)
    for idx, (lbl, val, sub, ac, vc) in enumerate(cards_spec):
        row = idx // 3
        col = idx % 3
        x1 = 60 + col * (bw + gap_x)
        y1 = top_y + row * (bh + gap_y)
        r_stat = None
        if idx == 1 and pristine_item:
            p_cnt = int(pristine_item.get("count", 0))
            p_pct = pristine_item.get("pct", 0)
            r_stat = ("PRISTINE 10s", f"{p_pct}%", f"{p_cnt} card{'' if p_cnt == 1 else 's'}")
        _draw_stat_box(draw, (x1, y1, x1 + bw, y1 + bh), lbl, val, sub, theme["bg"], ac, val_color=vc, right_stat=r_stat)

    by1 = 472
    by2 = H - 52
    gd = _calc_grading_days(grading_started_date, grading_finished_date)

    if gd is not None:
        # 3-Column Bottom Row: Grade Breakdown (60..476) | Grading Turnaround (496..796) | Featured Slabs (816..1300)
        gb_x1, gb_x2 = 60, 476
        dt_x1, dt_x2 = 496, 796
        fs_x1, fs_x2 = 816, W - 60
    else:
        # 2-Column Bottom Row: Grade Breakdown (60..796) | Featured Slabs (816..1300)
        gb_x1, gb_x2 = 60, 796
        fs_x1, fs_x2 = 816, W - 60

    # Bottom Col 1: Grade Breakdown Bars
    _draw_round_rect(draw, (gb_x1, by1, gb_x2, by2), 18, fill=theme["bg"], outline=(51, 65, 85), width=2)
    draw.text((gb_x1 + 22, by1 + 20), "GRADE BREAKDOWN", font=_load_font(18, bold=True), fill=theme["accent"])

    if breakdown:
        f_g = _load_font(16 if gd else 18, bold=True)
        bar_x1 = gb_x1 + (134 if gd else 180)
        bar_x2 = gb_x2 - (108 if gd else 150)
        bar_w = max(80, bar_x2 - bar_x1)
        bar_y = by1 + 58
        max_rows = min(7, len(breakdown))
        row_step = min(46, max(36, (by2 - bar_y - 16) // max(1, max_rows)))
        for item in breakdown[:max_rows]:
            g_lbl = f"{item['grade']}"[:12 if gd else 14]
            pct = float(item["pct"])
            cnt = item["count"]
            color = theme["gem"] if item.get("is_gem") else theme["accent"]
            draw.text((gb_x1 + 22, bar_y), g_lbl, font=f_g, fill=(241, 245, 249))
            _draw_round_rect(draw, (bar_x1, bar_y + 3, bar_x2, bar_y + 21), 8, fill=(30, 41, 59))
            fill_w = max(10, int(bar_w * (pct / 100.0)))
            _draw_round_rect(draw, (bar_x1, bar_y + 3, bar_x1 + fill_w, bar_y + 21), 8, fill=color)
            draw.text((bar_x2 + 10, bar_y), f"{pct}% ({cnt})", font=f_g, fill=(203, 213, 225))
            bar_y += row_step
    else:
        draw.text((gb_x1 + 22, by1 + 80), "No graded cards recorded yet.", font=_load_font(18), fill=(148, 163, 184))

    # Bottom Col 2 (when grading dates exist): Grading Turnaround Card
    if gd is not None:
        _draw_round_rect(draw, (dt_x1, by1, dt_x2, by2), 18, fill=theme["bg"], outline=theme["accent"], width=2)
        draw.line([(dt_x1 + 18, by1 + 2), (dt_x2 - 18, by1 + 2)], fill=theme["accent"], width=4)
        status_hdr = "GRADING COMPLETED" if gd["stopped"] else "GRADING TURNAROUND"
        draw.text((dt_x1 + 22, by1 + 20), status_hdr, font=_load_font(17, bold=True), fill=theme["accent"])

        # Business Days Box (Accent Highlighted, Hero Number Left + Label Right)
        b_box = (dt_x1 + 18, by1 + 56, dt_x2 - 18, by1 + 196)
        _draw_round_rect(draw, b_box, 14, fill=theme["card"], outline=theme["accent"], width=2)
        biz_str = str(gd["biz"])
        f_biz = _load_font(62 if len(biz_str) <= 2 else 52, bold=True)
        draw.text((dt_x1 + 34, by1 + 88), biz_str, font=f_biz, fill=theme["accent"])
        draw.text((dt_x1 + 124, by1 + 88), f"BUSINESS DAY{'' if gd['biz'] == 1 else 'S'}", font=_load_font(16, bold=True), fill=(241, 245, 249))
        draw.text((dt_x1 + 124, by1 + 114), "Excl. weekends &", font=_load_font(14), fill=(148, 163, 184))
        draw.text((dt_x1 + 124, by1 + 134), "US federal holidays", font=_load_font(14), fill=(148, 163, 184))

        # Calendar Days Box (Hero Number Left + Label Right)
        c_box = (dt_x1 + 18, by1 + 210, dt_x2 - 18, by1 + 340)
        _draw_round_rect(draw, c_box, 14, fill=theme["card"], outline=(51, 65, 85), width=2)
        cal_str = str(gd["cal"])
        f_cal = _load_font(58 if len(cal_str) <= 2 else 48, bold=True)
        draw.text((dt_x1 + 34, by1 + 238), cal_str, font=f_cal, fill=(241, 245, 249))
        draw.text((dt_x1 + 124, by1 + 246), f"CALENDAR DAY{'' if gd['cal'] == 1 else 'S'}", font=_load_font(16, bold=True), fill=(241, 245, 249))
        draw.text((dt_x1 + 124, by1 + 272), "Total elapsed days", font=_load_font(14), fill=(148, 163, 184))

        # Dates footer
        f_dt = _load_font(13, bold=True)
        draw.text((dt_x1 + 18, by2 - 34), f"Start: {gd['start']}", font=f_dt, fill=(203, 213, 225))
        end_lbl = f"End: {gd['end']}" if gd["end"] else "Active"
        draw.text((dt_x2 - 18, by2 - 34), end_lbl, font=f_dt, fill=theme["accent"] if not gd["end"] else (203, 213, 225), anchor="ra")

    # Bottom Right: Maximized Featured Slabs
    _draw_round_rect(draw, (fs_x1, by1, fs_x2, by2), 18, fill=theme["bg"], outline=(51, 65, 85), width=2)
    draw.text((fs_x1 + 22, by1 + 20), featured_label, font=_load_font(18, bold=True), fill=theme["accent"])

    valid_paths = []
    for rel in preview_paths:
        p = uploads_dir / rel if rel and not rel.startswith("http") else None
        if p and p.exists():
            valid_paths.append(p)
        if len(valid_paths) >= 3:
            break

    if valid_paths:
        inner_x1 = fs_x1 + 18
        inner_x2 = fs_x2 - 18
        inner_y1 = by1 + 56
        inner_y2 = by2 - 18
        avail_w = inner_x2 - inner_x1
        n = len(valid_paths)
        gap_s = 14
        slot_w = min(240, (avail_w - gap_s * (n - 1)) // n)
        total_used_w = n * slot_w + (n - 1) * gap_s
        start_x = inner_x1 + (avail_w - total_used_w) // 2
        for i, p in enumerate(valid_paths):
            sx1 = start_x + i * (slot_w + gap_s)
            _paste_card_image(img, p, (sx1, inner_y1, sx1 + slot_w, inner_y2), radius=12)
    else:
        draw.text((fs_x1 + 24, by1 + 140), "Upload raw card photos or\npull slab scans to feature here", font=_load_font(18), fill=(148, 163, 184))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def generate_multi_cards_infographic(cards: list[dict], uploads_dir: Path, theme_name: str = "water", title: str = "Selected Cards Showcase") -> bytes:
    theme = THEMES.get(theme_name, THEMES["water"])
    show_cards = cards[:4]
    n = max(1, len(show_cards))
    W = 1280
    H = 668
    img = Image.new("RGB", (W, H), theme["bg"])
    draw = ImageDraw.Draw(img)

    # Outer themed frame with subtle top accent bar — zero wasted margin
    _draw_round_rect(draw, (16, 16, W - 16, H - 16), 20, fill=theme["card"], outline=theme["accent"], width=2)
    draw.rectangle((36, 18, W - 36, 23), fill=theme["accent"])

    f_title = _load_font(30, bold=True)
    f_sub = _load_font(16, bold=True)
    f_pill = _load_font(14, bold=True)
    f_card = _load_font(20, bold=True)
    f_grade = _load_font(16, bold=True)
    f_val = _load_font(24, bold=True)
    f_profit = _load_font(14, bold=True)

    total_cost = sum(float(c.get("acquisition_cost") or 0) + float(c.get("grading_cost") or 0) + float(c.get("shipping_cost") or 0) for c in show_cards)
    total_val = sum(float(c.get("sold_price") if c.get("status") == "Sold" and c.get("sold_price") is not None else (c.get("market_value") or 0)) for c in show_cards)
    net_profit = total_val - total_cost

    draw.text((38, 34), title[:44], font=f_title, fill=(248, 250, 252))
    draw.text(
        (38, 72),
        f"{len(show_cards)} Card{'s' if len(show_cards) != 1 else ''}  •  Cost {_fmt_money(total_cost)}  •  Value {_fmt_money(total_val)}  •  Net {'+' if net_profit >= 0 else ''}{_fmt_money(net_profit)}",
        font=f_sub,
        fill=theme["gem"],
    )

    # Top-right compact brand badge
    brand_txt = "TCG GRADING VAULT"
    _draw_round_rect(draw, (W - 234, 36, W - 36, 68), 10, fill=theme["bg"], outline=theme["accent"], width=1)
    draw.text((W - 216, 44), brand_txt, font=f_pill, fill=theme["accent"])

    pad_x = 36
    top_y = 108
    bot_pad = 32
    gap = 18
    avail_w = W - 2 * pad_x
    cell_w = (avail_w - gap * (n - 1)) // n
    cell_h = H - top_y - bot_pad

    for idx, c in enumerate(show_cards):
        x1 = pad_x + idx * (cell_w + gap)
        y1 = top_y
        x2 = x1 + cell_w
        y2 = y1 + cell_h
        _draw_round_rect(draw, (x1, y1, x2, y2), 16, fill=theme["bg"], outline=theme["accent"], width=2)

        img_rel = c.get("cert_front_image") or c.get("raw_front_image") or ""
        img_path = (uploads_dir / img_rel) if img_rel and not img_rel.startswith("http") else None
        footer_h = 118
        _paste_card_image(img, img_path, (x1 + 12, y1 + 10, x2 - 12, y2 - footer_h - 6))

        # Divider above card text footer
        div_y = y2 - footer_h
        draw.line((x1 + 14, div_y, x2 - 14, div_y), fill=(51, 65, 85), width=1)

        nm = (c.get("card_name") or "Card")
        max_chars = 26 if n <= 3 else 20
        if len(nm) > max_chars:
            nm = nm[: max_chars - 1] + "…"
        draw.text((x1 + 14, div_y + 10), nm, font=f_card, fill=(248, 250, 252))

        comp = (c.get("submission_company") or "").strip()
        gr = (c.get("actual_grade") or c.get("status") or "").strip()
        badge_txt = f"{comp} {gr}".strip()[:22]
        _draw_round_rect(draw, (x1 + 14, div_y + 40, min(x2 - 14, x1 + 14 + max(110, len(badge_txt) * 10 + 18)), div_y + 68), 8, fill=theme["card"], outline=theme["accent"], width=1)
        draw.text((x1 + 22, div_y + 45), badge_txt, font=f_grade, fill=theme["accent"])

        val = c.get("sold_price") if c.get("status") == "Sold" and c.get("sold_price") is not None else c.get("market_value")
        val_txt = _fmt_money(val) if val is not None else "N/A"
        cost = float(c.get("acquisition_cost") or 0) + float(c.get("grading_cost") or 0) + float(c.get("shipping_cost") or 0)
        draw.text((x1 + 14, div_y + 78), val_txt, font=f_val, fill=theme["gem"])

        if val is not None:
            prof = float(val) - cost
            p_color = (74, 222, 128) if prof >= 0 else (248, 113, 113)
            p_txt = f"{'+' if prof >= 0 else ''}{_fmt_money(prof)}"
            draw.text((x2 - 14 - max(70, len(p_txt) * 9), div_y + 84), p_txt, font=f_profit, fill=p_color)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
