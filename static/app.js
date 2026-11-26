const state = {
  currentSubId: null,
  currentSubCards: [],
  currentSubData: null,
  selectedCardIds: new Set(),
  bulkSelectMode: false,
  subFilterMode: "all",
  subGradeFilter: "ALL",
  allCardsCache: [],
  allCardsGradeFilter: "ALL",
  moveTargetIds: [],
  currentCardId: null,
  currentCardData: null,
  wizardSubId: null,
  wizardCardsAdded: 0,
  lightboxFlipped: false,
  activePasteSide: null,
  currentView: "view-home",
  scrollPositions: {},
  lastCardSnapshot: null,
  lightboxGroup: "raw",
  lightboxZoom: 1,
  lightboxPanX: 0,
  lightboxPanY: 0,
  isPanning: false,
  panMoved: false,
  panStartX: 0,
  panStartY: 0,
};

const $ = (id) => document.getElementById(id);

function getTheme() {
  return localStorage.getItem("tcg_theme") || "water";
}

const DEFAULT_BRAND_SVG = `
  <svg viewBox="0 0 36 36" width="24" height="24" aria-hidden="true">
    <circle cx="18" cy="18" r="15" fill="none" stroke="currentColor" stroke-width="2.4"/>
    <path d="M3 18h10a5 5 0 0 0 10 0h10" fill="none" stroke="currentColor" stroke-width="2.4"/>
    <circle cx="18" cy="18" r="3.2" fill="currentColor"/>
  </svg>
`;

function applyBrandUI(b) {
  if (!b) return;
  if ($("brand-title-text")) $("brand-title-text").textContent = b.brand_name || "TCG Grading Vault";
  if ($("brand-sub-text")) $("brand-sub-text").textContent = b.brand_subtitle || "100% Local Storage • TCG Slab & Profit Tracker";
  document.title = `${b.brand_name || "TCG Grading Vault"} — Local Vault`;
  const box = $("brand-badge-box");
  if (box) {
    if (b.brand_icon) {
      box.innerHTML = `<img src="${resolveImgSrc(b.brand_icon)}" alt="Vault Icon" />`;
    } else {
      box.innerHTML = DEFAULT_BRAND_SVG;
    }
  }
}

async function loadBrandSettings() {
  try {
    const b = await apiJson("/api/brand");
    applyBrandUI(b);
  } catch {
    // ignore
  }
}

function activateInlineRename(titleEl, currentTitle, onSave) {
  if (!titleEl || titleEl.querySelector("input")) return;
  const origText = currentTitle;
  const inp = document.createElement("input");
  inp.type = "text";
  inp.className = "inline-rename-input";
  inp.value = origText;
  titleEl.textContent = "";
  titleEl.appendChild(inp);
  inp.focus();
  inp.select();

  let finished = false;
  const finish = async (save) => {
    if (finished) return;
    finished = true;
    const nextVal = inp.value.trim();
    if (save && nextVal && nextVal !== origText) {
      titleEl.textContent = nextVal;
      await onSave(nextVal);
    } else {
      titleEl.textContent = origText;
    }
  };

  inp.addEventListener("click", (e) => e.stopPropagation());
  inp.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      finish(true);
    } else if (e.key === "Escape") {
      e.preventDefault();
      finish(false);
    }
  });
  inp.addEventListener("blur", () => finish(true));
}

function getSubBorderColors() {
  try {
    const saved = JSON.parse(localStorage.getItem("tcg_sub_border_colors") || "{}");
    return {
      completed: saved.completed || "#10b981",
      partial: saved.partial || "#f59e0b",
      inGrading: saved.inGrading || "#3b82f6",
      notShipped: saved.notShipped || "#a855f7",
      noCompleted: Boolean(saved.noCompleted),
      noPartial: Boolean(saved.noPartial),
      noInGrading: Boolean(saved.noInGrading),
      noNotShipped: Boolean(saved.noNotShipped),
    };
  } catch {
    return {
      completed: "#10b981", partial: "#f59e0b", inGrading: "#3b82f6", notShipped: "#a855f7",
      noCompleted: false, noPartial: false, noInGrading: false, noNotShipped: false,
    };
  }
}

function saveSubBorderColors(cfg) {
  localStorage.setItem("tcg_sub_border_colors", JSON.stringify(cfg));
}

function showMissingCompanyError() {
  const picker = $("company-picker");
  const msg = $("company-error-msg");
  if (picker) {
    picker.classList.add("needs-selection");
    picker.scrollIntoView({ behavior: "smooth", block: "center" });
  }
  if (msg) msg.style.display = "block";
}

function clearMissingCompanyError() {
  const picker = $("company-picker");
  const msg = $("company-error-msg");
  if (picker) picker.classList.remove("needs-selection");
  if (msg) msg.style.display = "none";
}

const SVG_PENCIL = `<svg class="svg-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>`;
const SVG_TRASH = `<svg class="svg-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/></svg>`;

const PAGE_HELP_CONTENT = {
  "view-home": [
    {
      title: "📊 Dashboard Stats, Not Shipped & Portfolio ROI",
      items: [
        "View Completed, In Grading, Not Shipped / Preload cards, Gem Rate (10s %), Collective Costs, Realized Profit, and dedicated Portfolio ROI.",
        "Use the subtle Recalculate Stats button below the cards to refresh totals anytime, and hover over any Collection Pipeline segment for exact counts.",
      ],
    },
    {
      title: "☰ Global List vs. ⊞ Cards & Custom Outlines",
      items: [
        "Toggle ☰ List / ⊞ Cards to switch the entire app between compact single-line rows (with full-width card thumbnails + hover zoom) and card grids.",
        "Use 🎨 Card Colors to customize outline colors for Completed, Partially Graded, In Grading, and Not Shipped—or toggle No outline per status.",
      ],
    },
    {
      title: "🔍 Global Search & 🗂️ All Cards",
      items: [
        "Search any card across all submissions from the Home search bar, or click 🗂️ All Cards to browse and sort the full database.",
      ],
    },
  ],
  "view-wizard": [
    {
      title: "⚡ Unified Submission Import (No Filename Rules)",
      items: [
        "Drop any PSA Order CSV (saves PSA Image Zip links), Fanatics CSV, GameStop/BGS copied text or printed PDF, Excel/Word cert list, or screenshot (OCR).",
        "Leave 'Pull slab scans now' unchecked for instant creation when your file already has card names and grades.",
      ],
    },
    {
      title: "🛒 Separate eBay Sales & Bookkeeping Import",
      items: [
        "Use the eBay card (no grader required) to upload eBay Paid & Shipped CSVs. Buyer PII is 100% stripped; preview matched graded cards before marking Sold and optionally create an eBay Sales sub for unmatched items.",
      ],
    },
    {
      title: "➕ Manual Entry, Quantity & Link-Only",
      items: [
        "Add cards with Quantity (1–200), or paste ONLY a graded card link / Cert # (no card name needed). Check Preload if not shipped yet.",
      ],
    },
  ],
  "view-submission": [
    {
      title: "✨ Pull Missing Scans & Info vs. 🔁 Overwrite All",
      items: [
        "✨ Pull Missing Scans & Info only fetches missing scans/details. 🔁 Overwrite All Cert Scans refreshes HQ scans for every card. 💲 Pull All Market Values updates PriceCharting values across the sub.",
        "All batch pulls show a compact floating progress card at the bottom right (no screen blur) with a Cancel button.",
      ],
    },
    {
      title: "☑️ Bulk Actions & 📤 Share This Sub",
      items: [
        "Toggle ☑️ Bulk Actions to Set Status (e.g. Not Submitted), Bulk Edit (set, fees, curated date), Duplicate, Move, or Delete selected cards.",
        "📤 Share This Sub generates a PNG infographic for this submission only (graded slabs only + ROI).",
      ],
    },
  ],
  "view-card": [
    {
      title: "🏅 Cert Pulls, PSA Zip & BGS Verification",
      items: [
        "Click Pull Cert Scans to fetch official scans (opens a centered browser window when BGS/PSA requires Cloudflare verification). If imported from PSA CSV, click 📦 PSA Images Zip ↗ to download PSA's full image zip.",
      ],
    },
    {
      title: "📈 PriceCharting / TCGplayer Links & Profit vs. Market",
      items: [
        "Click 💲 Pull Market Value or paste a PriceCharting/TCGplayer URL in Manual Pricing Link—the link stays saved with an Open Link ↗ button.",
        "Sold cards display Net Profit, ROI %, and Sold Price vs. Market Value.",
      ],
    },
  ],
  "features": [
    {
      title: "📦 Submissions & Unified Smart Import",
      items: [
        "Track PSA, CGC, Beckett (BGS), and TAG submissions from Not Shipped / Preload through In Grading, Partially Graded, Completed, and Sold.",
        "One-click import from PSA Order CSVs, PSA Submission Form PDFs (auto-deduplicating Copy #1 vs Copy #2 and splitting grading/shipping costs), Fanatics Collect CSVs, GameStop/BGS PDFs or text, Excel/Word cert lists, and screenshots (OCR).",
      ],
    },
    {
      title: "🖼️ High-Res Cert Scans, Raw Photos & Hover Zoom",
      items: [
        "Fast-pull PSA CloudFront order zips in parallel batches, or fetch official PSA, CGC, BGS, and TAG cert scans and metadata automatically.",
        "Upload, drag-and-drop, or paste raw front/back photos and slab scans, inspect them in the 3D flip/zoom Lightbox, and hover any thumbnail across Home, Submissions, and eBay Review for instant zoom.",
      ],
    },
    {
      title: "💲 Market Values, Portfolio ROI & Collectr Export",
      items: [
        "Pull PriceCharting graded values individually or across an entire submission, and track Realized Profit, Unrealized (Unsold/Hold) Profit, Total Card Values (graded value or acquisition cost), and Overall Portfolio ROI.",
        "Export any submission via 📋 Export for Collectr as a TCGplayer/Collectr-compatible CSV or plain-text .txt file.",
      ],
    },
    {
      title: "🧾 eBay Sales & Zero-PII Bookkeeping",
      items: [
        "Import eBay Paid & Shipped CSVs with buyer PII stripped automatically, strict grade/year/card # matching, multi-match submission pickers, profit previews, and a one-click Import Without Marking Sold option.",
        "Download a clean bookkeeping CSV and generate themed PNG stat infographics for your entire vault, a submission, or any individual card.",
      ],
    },
  ],
  "versions": [
    {
      title: "v0.80 — Live Auction Monitor, Streamer Showcase, Multi-Card Share, In-App Tabs & Updates",
      items: [
        "Added Live Auction Monitor (🔨 Auctions) for eBay & Fanatics Collect with 60s auto-refresh, card size slider, split-screen listing viewer, HTML+photo report download, and 1-click Match & Mark Sold into your vault.",
        "Added Streamer Showcase mode & Bulk Actions Share Graphic for multi-card presentations, In-App Tabbed Browser Drawer for cert/price links, GitHub Check for Updates, Home/End + Back to Top navigation, and responsive auto-fit card columns.",
      ],
    },
    {
      title: "v0.70 — Portfolio Card Values, Collectr Export, Windows Setup Wizard & Submission Sharing",
      items: [
        "Added Total Card Values (graded value or acquisition cost), Realized/Unrealized Profit on Home, and Sold Price on card tiles.",
        "Added Collectr CSV/TXT export, Share infographic theme swatches, persistent Bulk Mode, mouse Forward navigation, and Portable Submission Bundle Import/Export (data/shared_submissions/).",
        "Built the 1-click Windows Setup Wizard (setup_windows.bat) with custom install folder, Desktop & Start Menu shortcuts, App Mode auto-shutdown, and Windows Uninstall Programs support.",
      ],
    },
    {
      title: "v0.60 — Dedicated eBay Review Modal, Strict Matching & Fast PSA Zip Pulls",
      items: [
        "Built the full-screen eBay Sales & Bookkeeping Review modal with card thumbnails, hover zoom, multi-match pickers, profit previews, and Import Without Marking Sold.",
        "Added strict numeric grade, 4-digit year, card number, and language matching, 12x parallel PSA CloudFront zip scan pulling, and multi-page PSA Submission Form PDF import.",
      ],
    },
    {
      title: "v0.50 — Unified Smart Import, Global List/Card View & Floating Progress",
      items: [
        "Added Unified Smart Import (Fanatics CSV, PSA CSV, GameStop/BGS PDF/text, OCR), Global List vs. Cards toggle, and floating bottom-right batch progress.",
      ],
    },
    {
      title: "v0.40 — Themes, Custom Card Outlines, Home Search & Bulk Actions",
      items: [
        "Added 8 Pokémon-inspired themes, custom submission outline colors, Home global card search, and Bulk Actions (edit, status, duplicate, move, delete).",
      ],
    },
    {
      title: "v0.10 – v0.30 — Core Local Vault, Cert Fetching, PriceCharting & Infographics",
      items: [
        "Initial local FastAPI + SQLite vault for PSA, CGC, Beckett, and TAG submissions with raw/cert photo slots, 3D Lightbox, PriceCharting valuation, and PNG stat infographics.",
      ],
    },
  ],
};

function renderHelpTab(tabId) {
  const active = PAGE_HELP_CONTENT[tabId] ? tabId : "view-home";
  document.querySelectorAll("#help-tabs .filter-pill").forEach((b) => {
    b.classList.toggle("active", b.dataset.helpTab === active);
  });
  const body = $("help-body");
  if (!body) return;
  const welcomeToggleHtml = active === "versions"
    ? `<label style="display:flex;align-items:center;gap:8px;padding:10px 12px;margin-bottom:12px;border-radius:8px;background:var(--surface-2);border:1px solid var(--border);cursor:pointer;font-size:13px;">
        <input type="checkbox" id="howto-show-welcome-cb" ${localStorage.getItem("tcg_vault_welcome_disabled") !== "1" ? "checked" : ""} />
        <span>Show "What's New" welcome popup when a new update is launched for the first time</span>
      </label>`
    : "";
  body.innerHTML = welcomeToggleHtml + PAGE_HELP_CONTENT[active]
    .map(
      (sec) => `
      <div class="help-card">
        <h4>${escapeHtml(sec.title)}</h4>
        <ul>${sec.items.map((it) => `<li>${escapeHtml(it)}</li>`).join("")}</ul>
      </div>
    `
    )
    .join("");
  const wCb = $("howto-show-welcome-cb");
  if (wCb) {
    wCb.addEventListener("change", () => {
      localStorage.setItem("tcg_vault_welcome_disabled", wCb.checked ? "0" : "1");
      showToast(wCb.checked ? "Welcome popup enabled for future updates." : "Welcome popup disabled.");
    });
  }
}

function applyTheme(themeName) {
  const valid = [
    "light", "dark", "fire", "water", "fighting", "psychic", "grass", "lightning",
    "technology", "christmas", "newyears", "july4", "thanksgiving", "halloween",
  ];
  const chosen = valid.includes(themeName) ? themeName : "water";
  document.documentElement.setAttribute("data-theme", chosen);
  localStorage.setItem("tcg_theme", chosen);
  document.querySelectorAll(".theme-option").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.themeChoice === chosen);
  });
}

function fmtMoney(val) {
  const num = Number(val || 0);
  const sign = num < 0 ? "-" : "";
  return `${sign}$${Math.abs(num).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function resolveImgSrc(path) {
  if (!path) return "";
  if (path.startsWith("http://") || path.startsWith("https://")) return path;
  return `/uploads/${path}`;
}

function detectLinkLabel(url) {
  if (!url) return "Open Link ↗";
  const low = url.toLowerCase();
  if (low.includes("fanaticscollect") || low.includes("pwcc")) return "Fanatics Collect ↗";
  if (low.includes("ebay.")) return "eBay ↗";
  if (low.includes("goldin.")) return "Goldin ↗";
  if (low.includes("ha.com")) return "Heritage ↗";
  if (low.includes("psacard.")) return "PSA Cert ↗";
  if (low.includes("cgccards.") || low.includes("cgcgrading.")) return "CGC Cert ↗";
  if (low.includes("beckett.")) return "Beckett ↗";
  if (low.includes("taggrading.")) return "TAG Cert ↗";
  if (low.includes("tcgplayer.")) return "TCGplayer ↗";
  if (low.includes("alt.xyz")) return "ALT ↗";
  try {
    const host = new URL(url).hostname.replace(/^www\./, "");
    return `${host} ↗`;
  } catch {
    return "Open Link ↗";
  }
}

function showToast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 3400);
}

function switchView(viewId, historyPayload = null, pushHistory = true, scrollMode = "top") {
  if (state.currentView && state.currentView !== viewId) {
    state.scrollPositions[state.currentView] = window.scrollY || 0;
  }
  if (viewId !== "view-auctions" && aucTimerHandle) { clearInterval(aucTimerHandle); aucTimerHandle = null; }
  state.currentView = viewId;
  if ($("thumb-hover-preview")) $("thumb-hover-preview").style.display = "none";
  document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
  $(viewId).classList.add("active");
  $("btn-home-nav").style.display = viewId === "view-home" ? "none" : "inline-flex";
  if ($("btn-share-overall")) $("btn-share-overall").style.display = viewId === "view-home" ? "inline-flex" : "none";
  if ($("tip-share-overall")) $("tip-share-overall").style.display = viewId === "view-home" ? "inline-flex" : "none";
  if (pushHistory && historyPayload) {
    history.pushState(historyPayload, "", historyPayload.hash || "");
  }
  if (scrollMode === "preserve") {
    const curY = state._preserveScrollY ?? window.scrollY ?? 0;
    requestAnimationFrame(() => window.scrollTo(0, curY));
    return;
  }
  if (scrollMode === "restore") {
    const savedY = state.scrollPositions[viewId] || 0;
    requestAnimationFrame(() => window.scrollTo(0, savedY));
    return;
  }
  window.scrollTo({ top: 0, behavior: "smooth" });
}

async function apiJson(url, opts = {}) {
  const res = await fetch(url, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.detail || `Request failed (${res.status})`);
  }
  return data;
}

function setCostInputValue(el, val) {
  if (val === null || val === undefined || val === "" || Number(val) === 0) {
    el.value = "";
  } else {
    el.value = val;
  }
}

function renderTopSlabShowcase(top) {
  const showcaseEl = $("top-slab-showcase");
  const gradePill = $("showcase-grade-pill");
  const body = $("showcase-body");

  if (!top) {
    gradePill.style.display = "none";
    showcaseEl.onclick = null;
    body.innerHTML = `<div class="showcase-empty muted">Mark a graded card as Sold to feature your highest-profit slab here.</div>`;
    return;
  }

  if (top.actual_grade) {
    gradePill.textContent = `${top.submission_company} ${top.actual_grade}`;
    gradePill.style.display = "inline-block";
  } else {
    gradePill.textContent = top.submission_company;
    gradePill.style.display = "inline-block";
  }

  const thumbHtml = top.image
    ? `<img class="showcase-thumb" src="${resolveImgSrc(top.image)}" alt="${escapeHtml(top.card_name)}" />`
    : `<div class="showcase-thumb-placeholder"><strong>${escapeHtml(top.submission_company)} ${escapeHtml(top.actual_grade || "")}</strong><span>Click to open &amp; pull scan</span></div>`;

  body.innerHTML = `
    ${thumbHtml}
    <div class="showcase-info" style="display:flex;flex-direction:column;justify-content:space-between;gap:8px;min-height:210px;padding:6px 0;flex:1;">
      <div>
        <div class="showcase-title" style="font-size:21px;line-height:1.2;">${escapeHtml(top.card_name)}</div>
        <div class="showcase-sub" style="font-size:13px;margin-top:4px;">${escapeHtml(top.submission_title)}${top.cert_number ? ` • Cert #${escapeHtml(top.cert_number)}` : ""}</div>
      </div>
      <div style="background:rgba(16,185,129,0.12);border:1px solid rgba(16,185,129,0.45);border-radius:10px;padding:10px 14px;">
        <div style="font-size:10.5px;font-weight:800;letter-spacing:0.5px;color:var(--muted);">${top.is_sold ? "REALIZED PROFIT" : "ESTIMATED PROFIT"}</div>
        <div class="showcase-profit" style="font-size:30px;">+${fmtMoney(top.profit)}</div>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;font-size:12px;">
        <span class="pill">${top.is_sold ? "Sold" : "Graded Val"}: <strong>${fmtMoney(top.sold_price)}</strong></span>
        <span class="pill">Cost: <strong>${fmtMoney(top.total_cost)}</strong></span>
        <span class="pill pill-gem">ROI: <strong>+${top.roi_pct}%</strong></span>
      </div>
    </div>
  `;
  showcaseEl.onclick = () => openCardDetail(top.id);
}

/* ---------------- SHARE STATS INFOGRAPHIC ---------------- */
async function setInfographicTheme(themeName) {
  const th = themeName || getTheme();
  state._shareTheme = th;
  document.querySelectorAll("[data-share-theme]").forEach((b) => {
    b.classList.toggle("active", b.dataset.shareTheme === th);
  });
  if (state._shareCardIds && state._shareCardIds.length) {
    try {
      const resp = await fetch("/api/infographic/cards", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ card_ids: state._shareCardIds, theme: th, title: state._shareMultiTitle || "Selected Cards Showcase" }),
      });
      const blob = await resp.blob();
      const objUrl = URL.createObjectURL(blob);
      $("share-preview-img").src = objUrl;
      $("btn-download-infographic").href = objUrl;
    } catch (_) {}
    return;
  }
  if (!state._shareBaseUrl) return;
  const sep = state._shareBaseUrl.includes("?") ? "&" : "?";
  const fullUrl = `${state._shareBaseUrl}${sep}theme=${encodeURIComponent(th)}&t=${Date.now()}`;
  $("share-preview-img").src = fullUrl;
  $("btn-download-infographic").href = fullUrl;
}

function openInfographicModal(url, title, filename, cardIds = null) {
  state._shareBaseUrl = url;
  state._shareCardIds = cardIds;
  state._shareMultiTitle = title;
  $("share-modal-title").textContent = title;
  $("btn-download-infographic").download = filename;
  setInfographicTheme(getTheme());
  $("share-modal").style.display = "flex";
}



/* ---------------- v0.80: IN-APP TABS, SHOWCASE, UPDATES, AUCTIONS & SCROLL ---------------- */
const inAppTabs = [];
let activeInAppTabIdx = -1;

function renderInAppTabs() {
  const drawer = $("inapp-tab-drawer");
  const listEl = $("inapp-tabs-list");
  const iframe = $("inapp-tab-iframe");
  const popout = $("inapp-popout-link");
  if (!drawer || !listEl || !iframe) return;
  if (!inAppTabs.length) {
    drawer.style.display = "none";
    iframe.src = "about:blank";
    return;
  }
  drawer.style.display = "flex";
  if (activeInAppTabIdx < 0 || activeInAppTabIdx >= inAppTabs.length) {
    activeInAppTabIdx = inAppTabs.length - 1;
  }
  const cur = inAppTabs[activeInAppTabIdx];
  listEl.innerHTML = inAppTabs.map((t, idx) => `
    <span class="inapp-tab-pill ${idx === activeInAppTabIdx ? "active" : ""}" data-tab-idx="${idx}">
      <span>${escapeHtml(t.label)}</span>
      <button type="button" data-close-tab="${idx}" style="background:none;border:none;color:inherit;cursor:pointer;padding:0;font-size:11px;">✕</button>
    </span>
  `).join("");
  listEl.querySelectorAll("[data-tab-idx]").forEach((el) => {
    el.addEventListener("click", (ev) => {
      if (ev.target.dataset.closeTab !== undefined) return;
      activeInAppTabIdx = Number(el.dataset.tabIdx);
      renderInAppTabs();
    });
  });
  listEl.querySelectorAll("[data-close-tab]").forEach((btn) => {
    btn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const idx = Number(btn.dataset.closeTab);
      inAppTabs.splice(idx, 1);
      if (activeInAppTabIdx >= inAppTabs.length) activeInAppTabIdx = inAppTabs.length - 1;
      renderInAppTabs();
    });
  });
  iframe.src = `/api/proxy-preview?inline_nav=1&url=${encodeURIComponent(cur.url)}`;
  if (popout) popout.href = cur.url;
}

function openInAppTab(url, label = "External View") {
  if (!url || !url.startsWith("http")) return;
  const existingIdx = inAppTabs.findIndex((t) => t.url === url);
  if (existingIdx >= 0) {
    activeInAppTabIdx = existingIdx;
  } else {
    inAppTabs.push({ url, label: label.slice(0, 26) });
    activeInAppTabIdx = inAppTabs.length - 1;
  }
  renderInAppTabs();
}

let showcaseCards = [];
let showcaseIdx = 0;
let showcaseBack = false;
let showcaseGridMode = false;

function renderStreamerShowcase() {
  const stage = $("showcase-stage");
  const counter = $("showcase-counter");
  const navBar = $("showcase-nav-bar");
  const modeBtn = $("btn-showcase-mode-toggle");
  if (!stage || !showcaseCards.length) return;
  if (modeBtn) modeBtn.textContent = showcaseGridMode ? "🎯 Spotlight View" : "⊞ Grid View";
  if (navBar) navBar.style.display = showcaseGridMode ? "none" : "flex";

  if (showcaseGridMode) {
    stage.innerHTML = `<div class="showcase-grid">${showcaseCards.map((c, idx) => {
      const img = showcaseBack ? (c.cert_back_image || c.raw_back_image || c.cert_front_image || c.raw_front_image) : (c.cert_front_image || c.raw_front_image || c.cert_back_image);
      const val = c.status === "Sold" && c.sold_price != null ? c.sold_price : c.market_value;
      return `<div class="panel" style="padding:12px;cursor:pointer;" data-spot-idx="${idx}">
        ${img ? `<img src="${resolveImgSrc(img)}" style="width:100%;height:210px;object-fit:contain;" />` : `<div class="muted" style="height:210px;display:flex;align-items:center;justify-content:center;">No Scan</div>`}
        <div style="font-weight:800;font-size:14px;margin-top:8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${escapeHtml(c.card_name)}</div>
        <div class="muted" style="font-size:12px;">${escapeHtml(c.actual_grade ? "Grade " + c.actual_grade : c.status)} ${c.cert_number ? "• #" + escapeHtml(c.cert_number) : ""}</div>
        <div style="font-size:15px;font-weight:800;color:var(--green,#10b981);margin-top:4px;">${val != null ? fmtMoney(val) : "Cost " + fmtMoney(c.total_cost)}</div>
      </div>`;
    }).join("")}</div>`;
    stage.querySelectorAll("[data-spot-idx]").forEach((el) => {
      el.addEventListener("click", () => {
        showcaseIdx = Number(el.dataset.spotIdx);
        showcaseGridMode = false;
        renderStreamerShowcase();
      });
    });
    return;
  }

  const c = showcaseCards[showcaseIdx];
  if (counter) counter.textContent = `${showcaseIdx + 1} / ${showcaseCards.length}`;
  const img = showcaseBack
    ? (c.cert_back_image || c.raw_back_image || c.cert_front_image || c.raw_front_image)
    : (c.cert_front_image || c.raw_front_image || c.cert_back_image);
  const val = c.status === "Sold" && c.sold_price != null ? c.sold_price : c.market_value;
  const prof = c.profit != null ? c.profit : c.est_profit;
  stage.innerHTML = `
    <div class="showcase-spotlight">
      <div style="text-align:center;">
        ${img ? `<img src="${resolveImgSrc(img)}" alt="${escapeHtml(c.card_name)}" />` : `<div class="muted" style="height:360px;display:flex;align-items:center;justify-content:center;border:1px dashed var(--border);border-radius:12px;">No Scan</div>`}
      </div>
      <div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px;">
          ${c.submission_company ? `<span class="badge badge-${escapeHtml(c.submission_company)}">${escapeHtml(c.submission_company)}</span>` : ""}
          ${c.actual_grade ? `<span class="pill pill-gem" style="font-size:14px;">Grade ${escapeHtml(c.actual_grade)}</span>` : ""}
          ${c.cert_number ? `<span class="pill">Cert #${escapeHtml(c.cert_number)}</span>` : ""}
          <span class="pill">${escapeHtml(c.status)}</span>
        </div>
        <h2 style="font-size:28px;margin:0 0 6px;">${escapeHtml(c.card_name)}</h2>
        <div class="muted" style="font-size:15px;margin-bottom:18px;">${escapeHtml([c.card_year, c.set_name, c.card_number ? "#" + c.card_number : ""].filter(Boolean).join(" • "))}</div>
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;">
          <div class="stat-card"><div class="stat-label">Total Cost</div><div class="stat-value">${fmtMoney(c.total_cost)}</div></div>
          <div class="stat-card stat-blue"><div class="stat-label">${c.status === "Sold" ? "Sold Price" : "Market Value"}</div><div class="stat-value accent-blue">${val != null ? fmtMoney(val) : "—"}</div></div>
          <div class="stat-card stat-profit"><div class="stat-label">${c.status === "Sold" ? "Realized Profit" : "Est. Profit"}</div><div class="stat-value ${prof != null && prof < 0 ? "profit-neg" : "profit-pos"}">${prof != null ? fmtMoney(prof) : "—"}</div></div>
        </div>
      </div>
    </div>`;
}

function openStreamerShowcase(cards, title = "Card Spotlight") {
  if (!cards || !cards.length) { showToast("Select at least one card first."); return; }
  showcaseCards = cards;
  showcaseIdx = 0;
  showcaseBack = false;
  showcaseGridMode = cards.length > 1;
  $("showcase-modal-title").textContent = title;
  renderStreamerShowcase();
  $("showcase-modal").style.display = "flex";
}

let auctionsCache = [];
let selectedAuctionId = null;
let aucCountdown = 60;
let aucTimerHandle = null;
let linkTargetAuction = null;

function renderAuctionsGrid() {
  const grid = $("auctions-grid");
  if (!grid) return;
  const totalItems = auctionsCache.length;
  const endedRows = auctionsCache.filter((a) => a.status === "Ended");
  const totalCost = auctionsCache.reduce((s, a) => s + Number(a.cost || 0), 0);
  const combinedBids = auctionsCache.reduce((s, a) => s + Number(a.final_price != null ? a.final_price : (a.current_bid || 0)), 0);
  const soldTotal = endedRows.reduce((s, a) => s + Number(a.final_price != null ? a.final_price : (a.current_bid || 0)), 0);

  if ($("auc-count-sub")) $("auc-count-sub").textContent = `${totalItems} listing${totalItems === 1 ? "" : "s"} tracked`;
  if ($("auc-total-cost")) $("auc-total-cost").textContent = fmtMoney(totalCost);
  if ($("auc-combined-bids")) $("auc-combined-bids").textContent = fmtMoney(combinedBids);
  if ($("auc-sold-summary")) $("auc-sold-summary").textContent = `${endedRows.length} • ${fmtMoney(soldTotal)}`;

  if (!auctionsCache.length) {
    grid.innerHTML = `<div class="panel" style="grid-column:1/-1;text-align:center;padding:30px;">No auctions tracked yet. Click <strong>➕ Add Auction Link(s)</strong> to monitor eBay or Fanatics Collect listings!</div>`;
    return;
  }

  grid.innerHTML = auctionsCache.map((a) => {
    const val = a.final_price != null ? a.final_price : (a.current_bid || 0);
    const isEnded = a.status === "Ended";
    return `
      <div class="auction-card ${a.id === selectedAuctionId ? "is-selected" : ""}" data-auc-id="${a.id}">
        <div class="auction-card-img-col">
          ${a.image_url ? `<img class="auction-card-img" src="${escapeHtml(a.image_url)}" alt="${escapeHtml(a.title)}" />` : `<div class="muted" style="height:190px;display:flex;align-items:center;justify-content:center;">No Image</div>`}
        </div>
        <div class="auction-card-info-col">
          <div>
            <div class="auction-card-title" title="${escapeHtml(a.title)}">${escapeHtml(a.title || a.url)}</div>
            <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-top:5px;">
              <span class="badge ${a.platform === "Fanatics" ? "badge-CGC" : "badge-PSA"}" style="font-size:calc(10px * var(--auc-scale, 1));">${escapeHtml(a.platform)}</span>
              <span class="muted" style="font-size:calc(11px * var(--auc-scale, 1));">${isEnded ? "ENDED" : escapeHtml(a.time_left || "Active")} • ${a.bid_count || 0} bids</span>
            </div>
          </div>
          <div style="font-size:calc(11px * var(--auc-scale, 1));color:#f87171;font-weight:700;text-align:center;">COST ${fmtMoney(a.cost || 0)}</div>
          <div class="auction-bid-box">
            <div style="font-size:calc(9.5px * var(--auc-scale, 1));font-weight:800;color:${isEnded ? "var(--green,#10b981)" : "var(--muted)"};">${isEnded ? "FINAL BID" : "CURRENT BID"}</div>
            <div class="auction-bid-amount">${fmtMoney(val)}</div>
          </div>
          <div style="display:flex;align-items:center;justify-content:space-between;gap:4px;">
            <button type="button" class="btn btn-secondary btn-sm" data-auc-link="${a.id}" title="Match to Vault Card & Mark Sold" style="padding:2px 7px;font-size:11px;flex:1;">🔗 Match Sold</button>
            <button type="button" class="btn btn-secondary btn-sm" data-auc-refresh="${a.id}" title="Refresh Now" style="padding:2px 6px;font-size:11px;">🔄</button>
            <button type="button" class="btn btn-ghost btn-sm" data-auc-del="${a.id}" title="Remove" style="padding:2px 6px;font-size:11px;">✕</button>
          </div>
        </div>
      </div>`;
  }).join("");

  grid.querySelectorAll(".auction-card").forEach((cardEl) => {
    cardEl.addEventListener("click", (ev) => {
      if (ev.target.closest("button")) return;
      const aid = Number(cardEl.dataset.aucId);
      const auc = auctionsCache.find((x) => x.id === aid);
      if (!auc?.url) return;
      openExternalUrl(auc.url);
    });
  });

  grid.querySelectorAll("[data-auc-refresh]").forEach((b) => {
    b.addEventListener("click", async (ev) => {
      ev.stopPropagation();
      const res = await apiJson(`/api/auctions/${b.dataset.aucRefresh}/refresh`, { method: "POST" });
      auctionsCache = auctionsCache.map((x) => (x.id === res.id ? res : x));
      renderAuctionsGrid();
    });
  });

  grid.querySelectorAll("[data-auc-del]").forEach((b) => {
    b.addEventListener("click", async (ev) => {
      ev.stopPropagation();
      const aid = Number(b.dataset.aucDel);
      await apiJson(`/api/auctions/${aid}`, { method: "DELETE" });
      auctionsCache = auctionsCache.filter((x) => x.id !== aid);
      if (selectedAuctionId === aid) selectedAuctionId = null;
      renderAuctionsGrid();
    });
  });

  grid.querySelectorAll("[data-auc-link]").forEach((b) => {
    b.addEventListener("click", (ev) => {
      ev.stopPropagation();
      openLinkAuctionModal(Number(b.dataset.aucLink));
    });
  });
}

async function openLinkAuctionModal(auctionId) {
  const auc = auctionsCache.find((x) => x.id === auctionId);
  if (!auc) return;
  linkTargetAuction = auc;
  const price = auc.final_price != null ? auc.final_price : (auc.current_bid || 0);
  $("link-auc-summary").textContent = `${auc.title} • Sale Price: ${fmtMoney(price)}`;
  $("link-auc-fees").value = "0";
  const allCards = await apiJson("/api/cards");
  const sel = $("link-auc-card-select");
  const renderOpts = (q = "") => {
    const ql = q.trim().toLowerCase();
    const list = allCards.filter((c) => !ql || `${c.card_name} ${c.cert_number} ${c.card_number} ${c.submission_title}`.toLowerCase().includes(ql));
    sel.innerHTML = list.slice(0, 80).map((c) => `<option value="${c.id}">${escapeHtml(c.card_name)} ${c.card_number ? "#" + escapeHtml(c.card_number) : ""} (${escapeHtml(c.submission_title)} • ${escapeHtml(c.actual_grade || c.status)}${c.cert_number ? " • Cert #" + escapeHtml(c.cert_number) : ""})</option>`).join("");
  };
  renderOpts("");
  $("link-auc-search").value = "";
  $("link-auc-search").oninput = (e) => renderOpts(e.target.value);
  $("link-auction-modal").style.display = "flex";
}

async function openAuctionsView(pushHistory = true) {
  switchView("view-auctions", { view: "auctions", hash: "#auctions" }, pushHistory);
  auctionsCache = await apiJson("/api/auctions");
  renderAuctionsGrid();
  if (!aucTimerHandle) {
    aucTimerHandle = setInterval(async () => {
      if (state.currentView !== "view-auctions" || !$("auc-auto-refresh-cb")?.checked) return;
      aucCountdown -= 1;
      if ($("auc-refresh-timer")) $("auc-refresh-timer").textContent = `(${aucCountdown}s)`;
      if (aucCountdown <= 0) {
        aucCountdown = 60;
        try {
          const r = await apiJson("/api/auctions/refresh", { method: "POST" });
          auctionsCache = r.auctions || auctionsCache;
          renderAuctionsGrid();
        } catch (_) {}
      }
    }, 1000);
  }
}

/* ---------------- HOME DASHBOARD ---------------- */
async function loadHome(pushHistory = true, scrollMode = "top") {
  if (scrollMode === "preserve") state._preserveScrollY = window.scrollY || 0;
  switchView("view-home", { view: "home", hash: "#home" }, pushHistory, scrollMode);
  const sort = $("sort-select").value;
  const company = $("filter-company").value;
  const status = $("filter-status").value;

  let stats, subs;
  try {
    [stats, subs] = await Promise.all([
      apiJson("/api/stats"),
      apiJson(`/api/submissions?sort=${encodeURIComponent(sort)}&company=${encodeURIComponent(company)}&status=${encodeURIComponent(status)}`),
    ]);
  } catch (err) {
    showToast(`Failed to load submissions: ${err.message}`);
    const g = $("submissions-grid");
    if (g) g.innerHTML = `<div class="panel" style="grid-column:1/-1;color:#f87171;">Error loading database: ${escapeHtml(err.message)}</div>`;
    return;
  }

  $("stat-completed").textContent = stats.completed_grading;
  $("stat-total-cards").textContent = `${stats.total_cards} total cards tracked`;
  $("stat-grading").textContent = stats.still_grading;
  $("stat-subs-count").textContent = `${stats.total_submissions} submissions`;
  $("stat-gem-rate").textContent = `${Number(stats.gem_rate || 0).toFixed(1)}%`;
  $("stat-gem-sub").textContent = `${stats.gem_10_count || 0} of ${stats.graded_with_grade_count || 0} graded hit 10`;
  $("stat-total-cost").textContent = fmtMoney(stats.total_collective_cost);
  $("stat-cost-breakdown").textContent = `Acq ${fmtMoney(stats.total_acquisition_cost)} • Grade ${fmtMoney(stats.total_grading_cost)} • Ship ${fmtMoney(stats.total_shipping_cost)}`;
  $("stat-total-sold").textContent = fmtMoney(stats.total_sold_revenue);
  $("stat-sold-count").textContent = `${stats.sold_cards} sold • Card Values: ${fmtMoney(stats.total_card_values ?? stats.total_market_value_unsold)}`;

  const profitEl = $("stat-profit");
  profitEl.textContent = fmtMoney(stats.realized_profit);
  profitEl.className = `stat-value ${stats.realized_profit >= 0 ? "profit-pos" : "profit-neg"}`;
  const unEl = $("stat-unrealized-profit");
  if (unEl) {
    const unVal = Number(stats.total_est_profit_unsold || 0);
    unEl.textContent = `Unrealized (Unsold/Hold): ${unVal >= 0 ? "+" : ""}${fmtMoney(unVal)}`;
    unEl.style.color = unVal >= 0 ? "var(--green, #10b981)" : "var(--danger, #f87171)";
  }
  $("stat-net-overall").textContent = `Net cash vs. all costs: ${fmtMoney(stats.net_overall)}`;

  if ($("stat-not-shipped")) {
    $("stat-not-shipped").textContent = stats.not_submitted_cards ?? 0;
    $("stat-not-shipped-sub").textContent = `${stats.not_shipped_subs ?? 0} Not Shipped sub${(stats.not_shipped_subs ?? 0) === 1 ? "" : "s"}`;
  }
  if ($("stat-roi")) {
    const oRoi = Number(stats.overall_roi_pct || 0);
    const rRoi = Number(stats.realized_roi_pct || 0);
    const uRoi = Number(stats.unsold_est_roi_pct || 0);
    $("stat-roi").textContent = `${oRoi >= 0 ? "+" : ""}${oRoi.toFixed(1)}%`;
    $("stat-roi").className = `stat-value ${oRoi >= 0 ? "profit-pos" : "profit-neg"}`;
    $("stat-roi-sub").textContent = `Realized: ${rRoi >= 0 ? "+" : ""}${rRoi.toFixed(1)}% • Unsold Est.: ${uRoi >= 0 ? "+" : ""}${uRoi.toFixed(1)}%`;
  }

  renderTopSlabShowcase(stats.top_profitable_card);

  const progPanel = $("progress-panel");
  if (stats.total_cards > 0) {
    progPanel.style.display = "block";
    const soldCnt = stats.sold_cards;
    const gradedUnsoldCnt = Math.max(0, stats.completed_grading - soldCnt);
    const gradingCnt = stats.still_grading;
    const notSubCnt = stats.not_submitted_cards || 0;
    const total = stats.total_cards;
    const pct = (n) => ((n / total) * 100).toFixed(1);

    $("prog-sold").style.width = `${(soldCnt / total) * 100}%`;
    $("prog-sold").title = `Green — Sold: ${soldCnt} card${soldCnt === 1 ? "" : "s"} (${pct(soldCnt)}%)`;
    $("prog-graded").style.width = `${(gradedUnsoldCnt / total) * 100}%`;
    $("prog-graded").title = `Blue — Unsold / Hold / Listed: ${gradedUnsoldCnt} card${gradedUnsoldCnt === 1 ? "" : "s"} (${pct(gradedUnsoldCnt)}%)`;
    $("prog-grading").style.width = `${(gradingCnt / total) * 100}%`;
    $("prog-grading").title = `Orange — In Grading: ${gradingCnt} card${gradingCnt === 1 ? "" : "s"} (${pct(gradingCnt)}%)`;
    if ($("prog-not-submitted")) {
      $("prog-not-submitted").style.width = `${(notSubCnt / total) * 100}%`;
      $("prog-not-submitted").title = `Purple — Not Submitted / Preload: ${notSubCnt} card${notSubCnt === 1 ? "" : "s"} (${pct(notSubCnt)}%)`;
    }
    $("progress-legend").innerHTML = `
      <span><span class="legend-dot" style="background:#10b981;"></span>${soldCnt} Sold</span>
      <span style="margin-left:10px;"><span class="legend-dot" style="background:var(--primary);"></span>${gradedUnsoldCnt} Unsold / Hold / Listed</span>
      <span style="margin-left:10px;"><span class="legend-dot" style="background:#f59e0b;"></span>${gradingCnt} In Grading</span>
      ${notSubCnt > 0 ? `<span style="margin-left:10px;"><span class="legend-dot" style="background:#a855f7;"></span>${notSubCnt} Not Submitted</span>` : ""}
    `;
  } else {
    progPanel.style.display = "none";
  }

  const grid = $("submissions-grid");
  grid.innerHTML = "";

  const addTile = document.createElement("div");
  addTile.className = "tile tile-add";
  addTile.innerHTML = `
    <div class="tile-add-plus"><svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg></div>
    <div class="tile-add-label">New Submission</div>
    <div class="muted">PSA, CGC, Beckett, or TAG</div>
  `;
  addTile.addEventListener("click", startNewSubmissionWizard);
  grid.appendChild(addTile);

  const borderColors = getSubBorderColors();
  for (const s of subs) {
    const tile = document.createElement("div");
    tile.className = "tile";
    if (s.status === "Completed" && !borderColors.noCompleted) {
      tile.classList.add("has-custom-outline");
      tile.style.setProperty("--tile-outline", borderColors.completed);
    } else if (s.status === "Partially Graded" && !borderColors.noPartial) {
      tile.classList.add("has-custom-outline");
      tile.style.setProperty("--tile-outline", borderColors.partial);
    } else if (s.status === "In Grading" && !borderColors.noInGrading) {
      tile.classList.add("has-custom-outline");
      tile.style.setProperty("--tile-outline", borderColors.inGrading);
    } else if (s.status === "Not Shipped" && !borderColors.noNotShipped) {
      tile.classList.add("has-custom-outline");
      tile.style.setProperty("--tile-outline", borderColors.notShipped);
    }
    const isListMode = (localStorage.getItem("tcg_view_mode") || "cards") === "list";
    const thumbList = s.preview_images;
    const thumbsHtml = thumbList.length
      ? thumbList.map((p) => `<img src="${resolveImgSrc(p)}" alt="Card" class="home-tile-thumb" />`).join("")
      : `<div class="tile-thumbs-empty">No card photos yet</div>`;

    const profitClass = s.realized_profit >= 0 ? "profit-pos" : "profit-neg";
    const gemPill = s.graded_with_grade_count > 0
      ? `<span class="pill pill-gem">Gem ${s.gem_rate}%</span>`
      : "";

    tile.innerHTML = `
      <div>
        <div class="tile-top">
          <span class="badge badge-${s.submit_channel === "eBay" ? "eBay" : s.company}">${s.submit_channel === "eBay" ? "eBay" : s.company}</span>
          <div style="display:flex;gap:6px;align-items:center;">
            ${gemPill}
            <span class="pill">${s.status}</span>
            <button type="button" class="btn-sub-delete-tile btn-sub-rename-tile" data-sub-id="${s.id}" title="Rename Submission">${SVG_PENCIL}</button>
            <button type="button" class="btn-sub-delete-tile btn-sub-del-action" data-sub-id="${s.id}" title="Delete Submission">${SVG_TRASH}</button>
          </div>
        </div>
        <div class="tile-title">${escapeHtml(s.title)}</div>
        <div class="tile-meta">
          ${s.submitted_date || "No date"}${s.submission_number ? ` • #${escapeHtml(s.submission_number)}` : ""}${s.submit_channel ? ` • via ${escapeHtml(s.submit_channel)}` : ""}
        </div>
        <div class="tile-thumbs">${thumbsHtml}</div>
      </div>
      <div class="tile-footer">
        <span><strong>${s.card_count}</strong> cards (${s.not_submitted_count > 0 && s.completed_count === 0 && s.grading_count === 0 ? `${s.not_submitted_count} not submitted` : `${s.completed_count} done / ${s.grading_count} grading${s.not_submitted_count > 0 ? ` / ${s.not_submitted_count} not submitted` : ""}`})</span>
        <span>
          Cost <strong>${fmtMoney(s.total_cost)}</strong>
          ${s.sold_count > 0 ? ` • <span class="${profitClass}">${fmtMoney(s.realized_profit)}</span>` : ""}
        </span>
      </div>
    `;
    const renBtn = tile.querySelector(".btn-sub-rename-tile");
    if (renBtn) {
      renBtn.addEventListener("click", (ev) => {
        ev.stopPropagation();
        const titleEl = tile.querySelector(".tile-title");
        activateInlineRename(titleEl, s.title, async (newTitle) => {
          await apiJson(`/api/submissions/${s.id}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ title: newTitle }),
          });
          s.title = newTitle;
          showToast("Submission renamed!");
        });
      });
    }
    const delBtn = tile.querySelector(".btn-sub-del-action");
    if (delBtn) {
      delBtn.addEventListener("click", async (ev) => {
        ev.stopPropagation();
        if (!confirm(`Delete submission "${s.title}" and all its cards/photos?`)) return;
        await apiJson(`/api/submissions/${s.id}`, { method: "DELETE" });
        showToast("Submission deleted.");
        loadHome(false, "restore");
      });
    }
    tile.querySelectorAll("img.home-tile-thumb").forEach((imgEl) => {
      const previewBox = $("thumb-hover-preview");
      const previewImg = $("thumb-hover-img");
      const posFn = (ev) => {
        if (!previewBox) return;
        const pad = 18;
        const boxW = previewBox.offsetWidth || 320;
        const boxH = previewBox.offsetHeight || 420;
        let left = ev.clientX + pad;
        if (left + boxW > window.innerWidth - 12) left = Math.max(12, ev.clientX - boxW - pad);
        let top = Math.max(12, Math.min(window.innerHeight - boxH - 12, ev.clientY - boxH / 2));
        previewBox.style.left = `${left}px`;
        previewBox.style.top = `${top}px`;
      };
      imgEl.addEventListener("mouseenter", (ev) => {
        if (!previewBox || !previewImg) return;
        previewImg.src = imgEl.src;
        previewBox.style.display = "block";
        posFn(ev);
      });
      imgEl.addEventListener("mousemove", posFn);
      imgEl.addEventListener("mouseleave", () => { if (previewBox) previewBox.style.display = "none"; });
    });
    tile.addEventListener("click", () => openSubmission(s.id));
    grid.appendChild(tile);
  }
}

/* ---------------- NEW SUBMISSION WIZARD ---------------- */
function startNewSubmissionWizard(pushHistory = true) {
  state.wizardSubId = null;
  state.wizardCardsAdded = 0;
  $("wizard-title").textContent = "New Grading Submission";
  $("wizard-subtitle").textContent = "Step 1: Choose grading company and submission details";
  $("wizard-step-1").style.display = "grid";
  $("wizard-step-2").style.display = "none";
  $("sub-title").value = "";
  $("sub-number").value = "";
  $("sub-date").value = new Date().toISOString().slice(0, 10);
  $("sub-notes").value = "";
  if ($("sub-not-shipped")) $("sub-not-shipped").checked = false;
  if ($("sub-ch-fanatics")) $("sub-ch-fanatics").checked = false;
  if ($("sub-ch-gamestop")) $("sub-ch-gamestop").checked = false;
  if ($("wiz-cert-doc-file")) $("wiz-cert-doc-file").value = "";
  if ($("wiz-auto-pull-certs")) $("wiz-auto-pull-certs").checked = false;
  switchView("view-wizard", { view: "wizard", hash: "#wizard" }, pushHistory);
}

function resetWizardCardForm() {
  $("wizard-card-form").reset();
  if ($("wiz-card-qty")) $("wiz-card-qty").value = "1";
  $("wiz-acq-cost").value = "";
  $("wiz-grade-cost").value = "";
  $("wiz-ship-cost").value = "";
  $("wiz-preview-front").innerHTML = "";
  $("wiz-preview-back").innerHTML = "";
}

function bindImagePreview(inputId, previewId) {
  $(inputId).addEventListener("change", (e) => {
    const file = e.target.files[0];
    const box = $(previewId);
    if (!file) {
      box.innerHTML = "";
      return;
    }
    const url = URL.createObjectURL(file);
    box.innerHTML = `<img src="${url}" alt="Preview" />`;
  });
}

/* ---------------- SUBMISSION DETAIL ---------------- */
async function pullValueForCardInSub(cardId, btnEl) {
  btnEl.disabled = true;
  btnEl.textContent = "Pulling...";
  try {
    const res = await apiJson(`/api/cards/${cardId}/fetch-value`, { method: "POST" });
    showToast(res.message);
    if (res.success) {
      openSubmission(state.currentSubId, false, "preserve");
    }
  } catch (err) {
    showToast(err.message);
  } finally {
    btnEl.disabled = false;
    btnEl.textContent = "💲 Pull Value";
  }
}

function attachHoverPreviewToThumb(thumbEl) {
  if (!thumbEl) return;
  const previewBox = $("thumb-hover-preview");
  const previewImg = $("thumb-hover-img");
  const positionPreview = (ev) => {
    if (!previewBox) return;
    const pad = 18;
    const boxW = previewBox.offsetWidth || 320;
    const boxH = previewBox.offsetHeight || 420;
    let left = ev.clientX + pad;
    if (left + boxW > window.innerWidth - 12) left = Math.max(12, ev.clientX - boxW - pad);
    let top = ev.clientY - boxH / 2;
    top = Math.max(12, Math.min(window.innerHeight - boxH - 12, top));
    previewBox.style.left = `${left}px`;
    previewBox.style.top = `${top}px`;
  };
  thumbEl.addEventListener("mouseenter", (ev) => {
    if (!previewBox || !previewImg) return;
    previewImg.src = thumbEl.src;
    previewBox.style.display = "block";
    positionPreview(ev);
  });
  thumbEl.addEventListener("mousemove", positionPreview);
  thumbEl.addEventListener("mouseleave", () => {
    if (previewBox) previewBox.style.display = "none";
  });
}

function renderReadOnlyCardsList(container, cardsList, emptyMsg) {
  container.innerHTML = "";
  if (!cardsList.length) {
    container.innerHTML = `<div class="muted">${escapeHtml(emptyMsg)}</div>`;
    return;
  }
  for (const c of cardsList) {
    const isGraded = c.status !== "In Grading";
    const thumb = isGraded ? (c.cert_front_image || c.raw_front_image) : (c.raw_front_image || c.cert_front_image);
    const thumbHtml = thumb
      ? `<img class="card-thumb" src="${resolveImgSrc(thumb)}" alt="${escapeHtml(c.card_name)}" />`
      : `<div class="card-thumb-empty">No Photo</div>`;

    const tile = document.createElement("div");
    tile.className = "card-row-tile";
    tile.innerHTML = `
      ${thumbHtml}
      <div class="card-row-info">
        <div class="card-row-title">${escapeHtml(c.card_name)}</div>
        <div class="card-row-sub">
          <span class="badge badge-${c.submission_company}">${escapeHtml(c.submission_company)}</span>
          ${escapeHtml(c.submission_title || "")} • ${escapeHtml(c.set_name || "")}${c.card_number ? ` #${escapeHtml(c.card_number)}` : ""}
        </div>
        <div class="card-row-meta">
          <span class="pill">${escapeHtml(c.status)}</span>
          ${c.actual_grade ? `<span class="pill">Grade: ${escapeHtml(c.actual_grade)}</span>` : ""}
          ${c.cert_number ? `<span class="pill" style="border-color:var(--primary);color:var(--primary);font-weight:700;">Cert #${escapeHtml(c.cert_number)}</span>` : ""}
        </div>
        <div class="card-row-numbers">
          <span>Cost: <strong>${fmtMoney(c.total_cost)}</strong></span>
          ${c.market_value != null && c.status !== "Sold" ? `<span style="color:var(--primary);font-weight:700;">Value: ${fmtMoney(c.market_value)}</span>` : ""}
          ${c.est_profit != null && c.status !== "Sold" ? `<span class="${c.est_profit >= 0 ? "profit-pos" : "profit-neg"}" style="font-weight:700;">Est. Profit: ${fmtMoney(c.est_profit)}</span>` : ""}
          ${c.profit != null ? `<span class="${c.profit >= 0 ? "profit-pos" : "profit-neg"}">Profit: ${fmtMoney(c.profit)}</span>` : ""}
        </div>
      </div>
    `;
    attachHoverPreviewToThumb(tile.querySelector("img.card-thumb"));
    tile.addEventListener("click", () => openCardDetail(c.id));
    container.appendChild(tile);
  }
}

async function openAllCardsView(pushHistory = true, scrollMode = "top") {
  switchView("view-all-cards", { view: "all-cards", hash: "#all-cards" }, pushHistory, scrollMode);
  const cards = await apiJson("/api/cards");
  state.allCardsCache = cards || [];
  state.allCardsGradeFilter = "ALL";
  if ($("all-cards-search")) $("all-cards-search").value = "";
  if ($("all-cards-company")) $("all-cards-company").value = "ALL";
  if ($("all-cards-sort")) $("all-cards-sort").value = "default";
  renderGradeFilterPills($("all-cards-grade-pills"), state.allCardsCache, "ALL", (g) => {
    state.allCardsGradeFilter = g;
    applyAllCardsFilterAndSort();
  });
  applyAllCardsFilterAndSort();
}

function applyAllCardsFilterAndSort() {
  const q = ($("all-cards-search")?.value || "").trim().toLowerCase();
  const comp = $("all-cards-company")?.value || "ALL";
  const sortMode = $("all-cards-sort")?.value || "default";
  const gFilter = state.allCardsGradeFilter || "ALL";

  let list = [...(state.allCardsCache || [])];
  if (comp !== "ALL") list = list.filter((c) => c.submission_company === comp);
  if (gFilter !== "ALL") list = list.filter((c) => (c.actual_grade || "").trim() === gFilter);
  if (q) {
    list = list.filter((c) => {
      const hay = [
        c.card_name, c.set_name, c.card_number, c.card_year, c.actual_grade,
        c.cert_number, c.status, c.submission_title, c.submission_company, c.curated_date, c.notes,
      ].join(" ").toLowerCase();
      return hay.includes(q);
    });
  }

  if (sortMode === "grade_desc") list.sort((a, b) => gradeNumericScore(b.actual_grade) - gradeNumericScore(a.actual_grade));
  else if (sortMode === "name_asc") list.sort((a, b) => String(a.card_name || "").localeCompare(String(b.card_name || "")));
  else if (sortMode === "value_desc") list.sort((a, b) => Number(b.market_value ?? -1) - Number(a.market_value ?? -1));
  else if (sortMode === "profit_desc") {
    const pVal = (c) => (c.profit != null ? Number(c.profit) : (c.est_profit != null ? Number(c.est_profit) : -999999));
    list.sort((a, b) => pVal(b) - pVal(a));
  } else if (sortMode === "cert_asc") list.sort((a, b) => String(a.cert_number || "zzzz").localeCompare(String(b.cert_number || "zzzz")));

  if ($("all-cards-count-meta")) {
    $("all-cards-count-meta").textContent = `Showing ${list.length} of ${state.allCardsCache.length} cards across all submissions (read-only; click any card to open & edit).`;
  }
  renderReadOnlyCardsList($("all-cards-list"), list, "No cards match your filters.");
}

async function openSubmission(subId, pushHistory = true, scrollMode = "top") {
  if (scrollMode === "preserve") state._preserveScrollY = window.scrollY || 0;
  const prevY = window.scrollY || 0;
  state.currentSubId = subId;
  const sub = await apiJson(`/api/submissions/${subId}`);
  switchView("view-submission", { view: "submission", subId, hash: `#submission/${subId}` }, pushHistory, scrollMode);
  if (scrollMode === "preserve") {
    requestAnimationFrame(() => window.scrollTo(0, prevY));
  }

  const badge = $("detail-sub-badge");
  badge.textContent = sub.company;
  badge.className = `badge badge-${sub.company}`;
  $("detail-sub-title").textContent = sub.title;
  $("detail-sub-meta").textContent = `${sub.submitted_date || "No date"}${sub.submission_number ? ` • Order #${sub.submission_number}` : ""}${sub.submit_channel ? ` • via ${sub.submit_channel}` : ""}${sub.notes ? ` • ${sub.notes}` : ""}`;
  $("detail-sub-status").value = sub.status;

  const cards = sub.cards || [];
  const completed = cards.filter((c) => c.status !== "In Grading" && c.status !== "Not Submitted").length;
  const grading = cards.filter((c) => c.status === "In Grading" || c.status === "Not Submitted").length;
  const totalCost = cards.reduce((acc, c) => acc + (c.total_cost || 0), 0);
  const totalSold = cards.filter((c) => c.status === "Sold" && c.sold_price != null).reduce((acc, c) => acc + Number(c.sold_price), 0);
  const totalProfit = cards.filter((c) => c.profit != null).reduce((acc, c) => acc + Number(c.profit), 0);
  const fmtRoi = (v) => (v == null ? "—" : `${v >= 0 ? "+" : ""}${Number(v).toFixed(1)}%`);

  $("detail-sub-stats").innerHTML = `
    <span><strong>${cards.length}</strong> Cards</span>
    <span><strong>${completed}</strong> Completed</span>
    <span><strong>${grading}</strong> Pending / In Grading</span>
    <span>Total Cost: <strong>${fmtMoney(totalCost)}</strong></span>
    <span>Unsold Est. Value: <strong>${fmtMoney(sub.total_market_value_unsold || 0)}</strong></span>
    <span>Sold Revenue: <strong>${fmtMoney(totalSold)}</strong></span>
    <span>Est. Profit (Unsold/Hold): <strong class="${(sub.total_est_profit_unsold || 0) >= 0 ? "profit-pos" : "profit-neg"}">${fmtMoney(sub.total_est_profit_unsold || 0)}</strong></span>
    <span>Realized Profit (Sold): <strong class="${totalProfit >= 0 ? "profit-pos" : "profit-neg"}">${fmtMoney(totalProfit)}</strong></span>
    ${sub.est_roi_pct != null ? `<span>Est. ROI: <strong class="${sub.est_roi_pct >= 0 ? "profit-pos" : "profit-neg"}">${fmtRoi(sub.est_roi_pct)}</strong></span>` : ""}
    ${sub.realized_roi_pct != null ? `<span>Realized ROI: <strong class="${sub.realized_roi_pct >= 0 ? "profit-pos" : "profit-neg"}">${fmtRoi(sub.realized_roi_pct)}</strong></span>` : ""}
    ${sub.overall_roi_pct != null ? `<span>Overall Sub ROI: <strong class="${sub.overall_roi_pct >= 0 ? "profit-pos" : "profit-neg"}">${fmtRoi(sub.overall_roi_pct)}</strong></span>` : ""}
  `;

  const bdPanel = $("detail-grade-breakdown");
  if (sub.graded_with_grade_count > 0) {
    bdPanel.style.display = "block";
    const pillsHtml = (sub.grade_breakdown || [])
      .map(
        (b) =>
          `<span class="grade-pill ${b.is_gem ? "is-gem" : ""}">${escapeHtml(b.grade)}: ${b.pct}% (${b.count})</span>`
      )
      .join("");
    bdPanel.innerHTML = `
      <div class="progress-header" style="margin-bottom:4px;">
        <span class="progress-title">Submission Gem Rate &amp; Grade Breakdown</span>
        <span class="pill pill-gem">Gem Rate (10s): <strong>${sub.gem_rate}%</strong> (${sub.gem_10_count} / ${sub.graded_with_grade_count})</span>
      </div>
      <div class="breakdown-pills">${pillsHtml}</div>
    `;
  } else {
    bdPanel.style.display = "none";
  }

  state.currentSubData = sub;
  state.currentSubCards = cards;
  state.selectedCardIds = new Set();
  if (scrollMode !== "preserve") {
    state.bulkSelectMode = false;
  }
  state.subFilterMode = "all";
  state.subGradeFilter = "ALL";
  if ($("sub-card-search")) $("sub-card-search").value = "";
  renderGradeFilterPills($("sub-grade-pills"), cards, "ALL", (g) => {
    state.subGradeFilter = g;
    applySubCardFilterAndSort();
  });
  if ($("sub-card-sort")) $("sub-card-sort").value = "default";
  document.querySelectorAll("#sub-filter-pills .filter-pill").forEach((p) => p.classList.toggle("active", p.dataset.filter === "all"));
  updateBulkSelectionBar();
  applySubCardFilterAndSort();
}

function updateBulkSelectionBar() {
  const bar = $("sub-bulk-bar");
  const cntEl = $("sub-bulk-count");
  const toggleBtn = $("btn-toggle-bulk-mode");
  if (!bar || !cntEl) return;
  const n = state.selectedCardIds.size;
  bar.style.display = state.bulkSelectMode ? "flex" : "none";
  cntEl.textContent = `${n} card${n === 1 ? "" : "s"} selected`;
  if (toggleBtn) {
    toggleBtn.textContent = state.bulkSelectMode ? "✕ Exit Bulk Mode" : "☑️ Bulk Actions";
  }
}

function gradeNumericScore(g) {
  const s = String(g || "").trim().toLowerCase();
  if (!s) return -1;
  if (s.includes("black") && s.includes("10")) return 10.5;
  if (s.includes("pristine") && s.includes("10")) return 10.3;
  const m = s.match(/(\d+(?:\.\d+)?)/);
  return m ? Number(m[1]) : 0;
}

function renderGradeFilterPills(container, cardsList, activeGrade, onSelect) {
  if (!container) return;
  const grades = Array.from(new Set((cardsList || []).map((c) => (c.actual_grade || "").trim()).filter(Boolean)));
  if (!grades.length) {
    container.style.display = "none";
    container.innerHTML = "";
    return;
  }
  grades.sort((a, b) => gradeNumericScore(b) - gradeNumericScore(a) || a.localeCompare(b));
  container.style.display = "flex";
  container.innerHTML = "";

  const makePill = (label, val) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `filter-pill ${activeGrade === val ? "active" : ""}`;
    btn.textContent = label;
    btn.addEventListener("click", () => {
      container.querySelectorAll(".filter-pill").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      onSelect(val);
    });
    return btn;
  };

  container.appendChild(makePill("All Grades", "ALL"));
  for (const g of grades) {
    container.appendChild(makePill(`Grade ${g}`, g));
  }
}

function applySubCardFilterAndSort() {
  const q = ($("sub-card-search")?.value || "").trim().toLowerCase();
  const sortMode = $("sub-card-sort")?.value || "default";

  let list = [...(state.currentSubCards || [])];
  const gFilter = state.subGradeFilter || "ALL";
  if (gFilter !== "ALL") list = list.filter((c) => (c.actual_grade || "").trim() === gFilter);
  const f = state.subFilterMode || "all";
  if (f === "complete") list = list.filter((c) => c.status !== "In Grading" && c.status !== "Not Submitted");
  else if (f === "not_submitted") list = list.filter((c) => c.status === "Not Submitted");
  else if (f === "in_grading") list = list.filter((c) => c.status === "In Grading");
  else if (f === "unsold") list = list.filter((c) => c.status === "Unsold");
  else if (f === "hold") list = list.filter((c) => c.status === "Hold");
  else if (f === "sold") list = list.filter((c) => c.status === "Sold");
  else if (f === "has_cert") list = list.filter((c) => Boolean((c.cert_number || "").trim()));
  else if (f === "no_cert") list = list.filter((c) => !((c.cert_number || "").trim()));
  else if (f === "no_scan") list = list.filter((c) => !c.cert_front_image);

  if (q) {
    list = list.filter((c) => {
      const hay = [
        c.card_name, c.set_name, c.card_number, c.card_year,
        c.actual_grade, c.cert_number, c.status, c.notes, c.curated_date,
      ].join(" ").toLowerCase();
      return hay.includes(q);
    });
  }

  const statusOrder = { "In Grading": 1, "Unsold": 2, "Hold": 3, "Listed": 4, "Sold": 5 };
  if (sortMode === "status") {
    list.sort((a, b) => (statusOrder[a.status] || 9) - (statusOrder[b.status] || 9) || a.id - b.id);
  } else if (sortMode === "grade_desc") {
    list.sort((a, b) => gradeNumericScore(b.actual_grade) - gradeNumericScore(a.actual_grade));
  } else if (sortMode === "name_asc") {
    list.sort((a, b) => String(a.card_name || "").localeCompare(String(b.card_name || "")));
  } else if (sortMode === "value_desc") {
    list.sort((a, b) => Number(b.market_value ?? -1) - Number(a.market_value ?? -1));
  } else if (sortMode === "profit_desc") {
    const pVal = (c) => (c.profit != null ? Number(c.profit) : (c.est_profit != null ? Number(c.est_profit) : -999999));
    list.sort((a, b) => pVal(b) - pVal(a));
  } else if (sortMode === "cert_asc") {
    list.sort((a, b) => String(a.cert_number || "zzzz").localeCompare(String(b.cert_number || "zzzz")));
  }

  renderSubmissionCards(list);
}

async function openMoveCardModal(cardIds) {
  if (!cardIds || !cardIds.length) return;
  state.moveTargetIds = cardIds;
  $("move-modal-title").textContent = cardIds.length === 1 ? "Move Card to Submission" : `Move ${cardIds.length} Cards to Submission`;
  const sel = $("move-modal-select");
  sel.innerHTML = "";
  const subs = await apiJson("/api/submissions");
  const others = subs.filter((s) => s.id !== state.currentSubId);
  if (!others.length) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "No other submissions available";
    sel.appendChild(opt);
    $("btn-confirm-move-modal").disabled = true;
  } else {
    $("btn-confirm-move-modal").disabled = false;
    for (const s of others) {
      const opt = document.createElement("option");
      opt.value = String(s.id);
      opt.textContent = `[${s.company}] ${s.title} (${s.submitted_date || "No date"})`;
      sel.appendChild(opt);
    }
  }
  $("move-card-modal").style.display = "flex";
}

function renderSubmissionCards(cardsToRender) {
  const list = $("detail-cards-list");
  list.innerHTML = "";

  if (!cardsToRender.length) {
    const q = $("sub-card-search")?.value?.trim();
    list.innerHTML = q
      ? `<div class="muted">No cards match "${escapeHtml(q)}".</div>`
      : `<div class="muted">No cards in this submission yet. Click "+ Add Card to Sub" to add your first card.</div>`;
    return;
  }

  for (const c of cardsToRender) {
    const isGraded = c.status !== "In Grading";
    const thumb = isGraded ? (c.cert_front_image || c.raw_front_image) : (c.raw_front_image || c.cert_front_image);
    let thumbHtml;
    if (thumb) {
      thumbHtml = `<img class="card-thumb" src="${resolveImgSrc(thumb)}" alt="${escapeHtml(c.card_name)}" />`;
    } else if (isGraded && c.cert_number) {
      thumbHtml = `<div class="card-thumb-empty"><strong>Graded ${escapeHtml(c.actual_grade || "")}</strong><span>Open card to pull scan</span></div>`;
    } else {
      thumbHtml = `<div class="card-thumb-empty">No Photo</div>`;
    }

    const predParts = [
      c.pred_corners != null ? `C:${c.pred_corners}` : null,
      c.pred_edges != null ? `E:${c.pred_edges}` : null,
      c.pred_centering != null ? `Ct:${c.pred_centering}` : null,
      c.pred_surface != null ? `S:${c.pred_surface}` : null,
    ].filter(Boolean);

    const linkLabel = detectLinkLabel(c.fanatics_url);

    const tile = document.createElement("div");
    tile.className = "card-row-tile";
    const isChecked = state.selectedCardIds.has(c.id);
    const cbHtml = state.bulkSelectMode
      ? `<input type="checkbox" class="card-select-cb" data-card-id="${c.id}" ${isChecked ? "checked" : ""} title="Select card" />`
      : "";
    tile.innerHTML = `
      ${cbHtml}
      ${thumbHtml}
      <div class="card-row-info">
        <div class="card-row-title">${escapeHtml(c.card_name)}</div>
        <div class="card-row-sub">${escapeHtml(c.set_name || "")}${c.card_number ? ` #${escapeHtml(c.card_number)}` : ""}</div>
        <div class="card-row-meta">
          <span class="pill">${c.status}</span>
          ${c.actual_grade ? `<span class="pill">Grade: ${escapeHtml(c.actual_grade)}</span>` : ""}
          ${c.cert_number ? `<span class="pill" style="border-color:var(--primary);color:var(--primary);font-weight:700;">Cert #${escapeHtml(c.cert_number)}</span>` : ""}
          ${c.curated_date ? `<span class="pill">Curated: ${escapeHtml(c.curated_date)}</span>` : ""}
        </div>
        ${(c.status === "Sold" && c.sold_price != null) || c.market_value != null ? `
          <div class="card-val-highlight">
            <div style="font-size:9.5px;font-weight:800;color:var(--muted);">${c.status === "Sold" ? "SOLD PRICE" : "MARKET VALUE"}</div>
            <div style="font-size:17px;font-weight:900;color:${c.status === "Sold" ? "var(--green, #10b981)" : "var(--primary)"};">${fmtMoney(c.status === "Sold" ? c.sold_price : c.market_value)}</div>
          </div>` : ""}
        <div class="card-row-meta muted">
          <span>Cost: ${fmtMoney(c.total_cost)}</span>
          ${c.market_value != null && c.status !== "Sold" ? `<span style="color:var(--primary);font-weight:700;">Value: ${fmtMoney(c.market_value)}</span>` : ""}
          ${c.est_profit != null && c.status !== "Sold" ? `<span class="${c.est_profit >= 0 ? "profit-pos" : "profit-neg"}" style="font-weight:700;">Est. Profit: ${fmtMoney(c.est_profit)}${c.total_cost > 0 ? ` (${c.est_profit >= 0 ? "+" : ""}${((c.est_profit / c.total_cost) * 100).toFixed(1)}% ROI)` : ""}</span>` : ""}
          ${predParts.length ? `<span>Pred: ${predParts.join(" ")}</span>` : ""}
          ${c.profit != null ? `<span class="${c.profit >= 0 ? "profit-pos" : "profit-neg"}">Profit: ${fmtMoney(c.profit)}${(c.total_cost + (c.selling_fees || 0)) > 0 ? ` (${c.profit >= 0 ? "+" : ""}${((c.profit / (c.total_cost + (c.selling_fees || 0))) * 100).toFixed(1)}% ROI)` : ""}${c.market_value != null && c.sold_price != null ? ` • vs Mkt: ${c.sold_price - c.market_value >= 0 ? "+" : ""}${fmtMoney(c.sold_price - c.market_value)}` : ""}</span>` : ""}
        </div>

      </div>
    `;
    const pullBtn = tile.querySelector(".btn-inline-pull-val");
    if (pullBtn) {
      pullBtn.addEventListener("click", (ev) => {
        ev.stopPropagation();
        pullValueForCardInSub(c.id, pullBtn);
      });
    }
    const thumbEl = tile.querySelector("img.card-thumb");
    if (thumbEl) {
      const previewBox = $("thumb-hover-preview");
      const previewImg = $("thumb-hover-img");
      const positionPreview = (ev) => {
        if (!previewBox) return;
        const pad = 18;
        const boxW = previewBox.offsetWidth || 320;
        const boxH = previewBox.offsetHeight || 420;
        let left = ev.clientX + pad;
        if (left + boxW > window.innerWidth - 12) {
          left = Math.max(12, ev.clientX - boxW - pad);
        }
        let top = ev.clientY - boxH / 2;
        top = Math.max(12, Math.min(window.innerHeight - boxH - 12, top));
        previewBox.style.left = `${left}px`;
        previewBox.style.top = `${top}px`;
      };
      thumbEl.addEventListener("mouseenter", (ev) => {
        if (!previewBox || !previewImg) return;
        previewImg.src = thumbEl.src;
        previewBox.style.display = "block";
        positionPreview(ev);
      });
      thumbEl.addEventListener("mousemove", positionPreview);
      thumbEl.addEventListener("mouseleave", () => {
        if (previewBox) previewBox.style.display = "none";
      });
    }
    const cb = tile.querySelector(".card-select-cb");
    if (cb) {
      cb.addEventListener("click", (ev) => ev.stopPropagation());
      cb.addEventListener("change", (ev) => {
        if (ev.target.checked) state.selectedCardIds.add(c.id);
        else state.selectedCardIds.delete(c.id);
        updateBulkSelectionBar();
      });
    }
    const mvBtn = tile.querySelector(".btn-inline-move-card");
    if (mvBtn) {
      mvBtn.addEventListener("click", (ev) => {
        ev.stopPropagation();
        openMoveCardModal([c.id]);
      });
    }
    const delCardBtn = tile.querySelector(".btn-inline-del-card");
    if (delCardBtn) {
      delCardBtn.addEventListener("click", async (ev) => {
        ev.stopPropagation();
        if (!confirm(`Delete "${c.card_name}"?`)) return;
        await apiJson(`/api/cards/${c.id}`, { method: "DELETE" });
        state.selectedCardIds.delete(c.id);
        showToast("Card deleted.");
        openSubmission(state.currentSubId, false, "preserve");
      });
    }
    if (isChecked) tile.classList.add("is-selected");
    tile.addEventListener("click", () => {
      if (state.bulkSelectMode) {
        const cbEl = tile.querySelector(".card-select-cb");
        if (cbEl) {
          cbEl.checked = !cbEl.checked;
          if (cbEl.checked) {
            state.selectedCardIds.add(c.id);
            tile.classList.add("is-selected");
          } else {
            state.selectedCardIds.delete(c.id);
            tile.classList.remove("is-selected");
          }
          updateBulkSelectionBar();
        }
        return;
      }
      openCardDetail(c.id);
    });
    list.appendChild(tile);
  }
}

/* ---------------- CARD DETAIL / EDIT ---------------- */
function renderPhotoSlot(imgId, emptyId, path) {
  const img = $(imgId);
  const empty = $(emptyId);
  if (path) {
    img.src = resolveImgSrc(path);
    img.style.display = "block";
    empty.style.display = "none";
  } else {
    img.src = "";
    img.style.display = "none";
    empty.style.display = "block";
  }
}

function updateComputedCardMetrics() {
  const acq = Number($("edit-acq-cost").value || 0);
  const grd = Number($("edit-grade-cost").value || 0);
  const shp = Number($("edit-ship-cost").value || 0);
  const fees = Number($("edit-selling-fees").value || 0);
  const soldRaw = $("edit-sold-price").value;
  const initRaw = $("edit-initial-graded-value").value;
  const totalCost = acq + grd + shp;
  $("edit-total-cost-display").textContent = fmtMoney(totalCost);

  const mktRaw = $("edit-market-value").value;
  const unrealEl = $("edit-unrealized-display");
  const hasMkt = mktRaw !== "" && !Number.isNaN(Number(mktRaw));
  const hasInit = initRaw !== "" && !Number.isNaN(Number(initRaw));
  if (hasMkt || hasInit) {
    const refVal = hasMkt ? Number(mktRaw) : Number(initRaw);
    const gain = refVal - totalCost;
    const roiStr = totalCost > 0 ? ` • ${gain >= 0 ? "+" : ""}${((gain / totalCost) * 100).toFixed(1)}% ROI` : "";
    const basis = hasMkt ? "vs. Market Value" : "vs. Time of Grading";
    unrealEl.textContent = `${fmtMoney(gain)} (${basis}${roiStr})`;
    unrealEl.className = `computed-box ${gain >= 0 ? "profit-pos" : "profit-neg"}`;
  } else {
    unrealEl.textContent = "— (Pull Market Value or enter Value at Time of Grading)";
    unrealEl.className = "computed-box";
  }

  const profitEl = $("edit-profit-display");
  if (soldRaw !== "" && !Number.isNaN(Number(soldRaw))) {
    const soldNum = Number(soldRaw);
    const profit = soldNum - totalCost - fees;
    const costWithFees = totalCost + fees;
    const roiStr = costWithFees > 0 ? ` (${profit >= 0 ? "+" : ""}${((profit / costWithFees) * 100).toFixed(1)}% ROI)` : "";
    const vsMkt = hasMkt ? ` • vs Market: ${soldNum - Number(mktRaw) >= 0 ? "+" : ""}${fmtMoney(soldNum - Number(mktRaw))}` : "";
    profitEl.textContent = `${fmtMoney(profit)}${roiStr}${vsMkt}`;
    profitEl.className = `computed-box ${profit >= 0 ? "profit-pos" : "profit-neg"}`;
  } else {
    profitEl.textContent = "— (Unsold)";
    profitEl.className = "computed-box";
  }
}

function autoPromoteStatusIfGraded() {
  if ($("edit-status").value === "In Grading") {
    const hasLink = $("edit-fanatics-url").value.trim().length > 0;
    const hasCert = $("edit-cert-number").value.trim().length > 0;
    const hasGrade = $("edit-actual-grade").value.trim().length > 0;
    if (hasLink || hasCert || hasGrade) {
      $("edit-status").value = "Unsold";
    }
  }
}

function extractDraggedImageUrl(dt) {
  if (!dt) return "";
  const html = dt.getData("text/html") || "";
  if (html) {
    const m = html.match(/<img[^>]+src=["']([^"']+)["']/i);
    if (m && m[1]) return m[1].replace(/&amp;/g, "&");
  }
  const uriList = dt.getData("text/uri-list") || "";
  if (uriList) {
    const first = uriList.split(/\r?\n/).find((ln) => ln.trim() && !ln.trim().startsWith("#"));
    if (first) return first.trim();
  }
  const plain = (dt.getData("text/plain") || "").trim();
  if (plain.startsWith("http://") || plain.startsWith("https://") || plain.startsWith("data:image/")) {
    return plain;
  }
  return "";
}

async function uploadDroppedImageUrl(side, imageUrl) {
  if (!state.currentCardId || !imageUrl) return;
  showToast("Downloading dragged image...");
  try {
    const res = await apiJson(`/api/cards/${state.currentCardId}/upload-scan-url`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ side, image_url: imageUrl }),
    });
    if (res.card) {
      state.currentCardData = res.card;
      renderAllCardPhotos(res.card);
      showToast(`Saved ${side.replace("_", " ")} image!`);
    }
  } catch (err) {
    showToast(err.message);
  }
}

async function pullAllDataFromLink(url) {
  const clean = (url || "").trim();
  if (!clean || (!clean.startsWith("http://") && !clean.startsWith("https://")) || !state.currentCardId) {
    showToast("Enter a valid graded card URL first.");
    return;
  }
  $("cert-fetch-status").textContent = "Pulling card data & cert scans from link...";
  try {
    captureCardFormSnapshot();
    $("btn-undo-autofill").style.display = "inline-flex";
    const res = await apiJson(`/api/cards/${state.currentCardId}/pull-from-link`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url: clean,
        win_x: Math.round(window.screenX || 0),
        win_y: Math.round(window.screenY || 0),
        win_w: Math.round(window.outerWidth || window.innerWidth || 1200),
        win_h: Math.round(window.outerHeight || window.innerHeight || 800),
      }),
    });
    $("cert-fetch-status").textContent = res.message || "Link checked.";
    if (res.card) {
      state.currentCardData = res.card;
      renderAllCardPhotos(res.card);
      if (res.card.card_name) $("edit-card-name").value = res.card.card_name;
      if (res.card.card_year) $("edit-card-year").value = res.card.card_year;
      if (res.card.set_name) $("edit-set-name").value = res.card.set_name;
      if (res.card.card_number) $("edit-card-number").value = res.card.card_number;
      if (res.card.cert_number) $("edit-cert-number").value = res.card.cert_number;
      if (res.card.actual_grade) $("edit-actual-grade").value = res.card.actual_grade;
      if (res.card.status) $("edit-status").value = res.card.status;
      if (res.card.cert_lookup_url) {
        const certLink = $("link-official-cert");
        certLink.href = res.card.cert_lookup_url;
        certLink.style.display = "inline-flex";
      }
      updateComputedCardMetrics();
    }
    showToast(res.success ? "Card data pulled from link!" : "Link saved.");
  } catch (err) {
    $("cert-fetch-status").textContent = err.message;
    showToast(err.message);
  }
}

async function autoExtractFromGradedLink(url, isWizard = false) {
  const clean = (url || "").trim();
  if (!clean || (!clean.startsWith("http://") && !clean.startsWith("https://"))) return;
  try {
    const res = await apiJson("/api/extract-link-info", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: clean, company: isWizard ? ($("sub-company")?.value || "") : (state.currentCardData?.submission_company || "") }),
    });
    if (!res) return;
    if (isWizard) {
      if (res.cert_number && !$("wiz-cert-num").value.trim()) $("wiz-cert-num").value = res.cert_number;
      if (res.card_year && !$("wiz-card-year").value.trim()) $("wiz-card-year").value = res.card_year;
      if (res.set_name && !$("wiz-set-name").value.trim()) $("wiz-set-name").value = res.set_name;
      if (res.card_number && !$("wiz-card-number").value.trim()) $("wiz-card-number").value = res.card_number;
      if (res.cert_number) showToast(`Extracted Cert #${res.cert_number} from link!`);
      return;
    }

    let changed = false;
    if (res.cert_number || res.actual_grade || res.card_year || res.set_name || res.card_number || res.card_name) {
      captureCardFormSnapshot();
      $("btn-undo-autofill").style.display = "inline-flex";
    }
    if (res.cert_number && !$("edit-cert-number").value.trim()) {
      $("edit-cert-number").value = res.cert_number;
      changed = true;
    }
    if (res.actual_grade && !$("edit-actual-grade").value.trim()) {
      $("edit-actual-grade").value = res.actual_grade;
      changed = true;
    }
    if (res.card_year && !$("edit-card-year").value.trim()) {
      $("edit-card-year").value = res.card_year;
      changed = true;
    }
    if (res.set_name && !$("edit-set-name").value.trim()) {
      $("edit-set-name").value = res.set_name;
      changed = true;
    }
    if (res.card_number && !$("edit-card-number").value.trim()) {
      $("edit-card-number").value = res.card_number;
      changed = true;
    }
    if (res.card_name && !$("edit-card-name").value.trim()) {
      $("edit-card-name").value = res.card_name;
      changed = true;
    }
    if (changed) {
      autoPromoteStatusIfGraded();
      const certVal = $("edit-cert-number").value.trim();
      if (certVal) {
        $("cert-fetch-status").textContent = `Extracted Cert #${certVal} from link. Click "Pull Cert Scans" to fetch slab photos & official metadata.`;
        showToast(`Extracted Cert #${certVal} from link!`);
      } else {
        showToast("Extracted card details from link!");
      }
    }
  } catch {
    // Silent fallback if site blocks automated fetch
  }
}

function renderAllCardPhotos(c) {
  renderPhotoSlot("img-raw-front", "empty-raw-front", c.raw_front_image);
  renderPhotoSlot("img-raw-back", "empty-raw-back", c.raw_back_image);
  renderPhotoSlot("img-cert-front", "empty-cert-front", c.cert_front_image);
  renderPhotoSlot("img-cert-back", "empty-cert-back", c.cert_back_image);

  const slotMap = {
    "actions-raw-front": c.raw_front_image,
    "actions-raw-back": c.raw_back_image,
    "actions-cert-front": c.cert_front_image,
    "actions-cert-back": c.cert_back_image,
  };
  Object.entries(slotMap).forEach(([id, path]) => {
    const el = $(id);
    if (!el) return;
    const isLocal = Boolean(path && !path.startsWith("http://") && !path.startsWith("https://"));
    el.classList.toggle("visible", isLocal);
  });

  const swapBtn = $("btn-swap-raw");
  if (swapBtn) {
    swapBtn.style.display = (c.raw_front_image || c.raw_back_image) ? "inline-flex" : "none";
  }
}

async function openLocalPath(relPath, mode = "folder") {
  if (!relPath || relPath.startsWith("http://") || relPath.startsWith("https://")) {
    showToast("Only locally saved files can be opened in your file explorer.");
    return;
  }
  try {
    await apiJson("/api/open-local-path", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: relPath, mode }),
    });
  } catch (err) {
    showToast(err.message);
  }
}

function getCardImageBySide(c, side) {
  if (!c) return "";
  if (side === "raw_front") return c.raw_front_image || "";
  if (side === "raw_back") return c.raw_back_image || "";
  if (side === "cert_front") return c.cert_front_image || "";
  if (side === "cert_back") return c.cert_back_image || "";
  return "";
}

async function uploadRawPair(fileA, fileB) {
  if (!state.currentCardId || !fileA || !fileB) return;
  const fd = new FormData();
  fd.append("files", fileA, fileA.name || "img1.png");
  fd.append("files", fileB, fileB.name || "img2.png");
  const res = await apiJson(`/api/cards/${state.currentCardId}/upload-raw-pair`, {
    method: "POST",
    body: fd,
  });
  if (res.card) {
    state.currentCardData = res.card;
    renderAllCardPhotos(res.card);
    showToast(`Auto-sorted! Front: ${res.front_filename} • Back: ${res.back_filename}`);
  }
}

async function classifyAndAssignWizardRawPair(fileA, fileB) {
  if (!fileA || !fileB) return;
  const fd = new FormData();
  fd.append("files", fileA, fileA.name || "img1.png");
  fd.append("files", fileB, fileB.name || "img2.png");
  try {
    const res = await apiJson("/api/classify-raw-pair", { method: "POST", body: fd });
    const files = [fileA, fileB];
    const frontFile = files[res.front_index];
    const backFile = files[res.back_index];

    const dtFront = new DataTransfer();
    dtFront.items.add(frontFile);
    $("wiz-raw-front").files = dtFront.files;

    const dtBack = new DataTransfer();
    dtBack.items.add(backFile);
    $("wiz-raw-back").files = dtBack.files;

    const renderPrev = (file, prevId) => {
      const box = $(prevId);
      const reader = new FileReader();
      reader.onload = (ev) => {
        box.innerHTML = `<img src="${ev.target.result}" alt="Preview" />`;
      };
      reader.readAsDataURL(file);
    };
    renderPrev(frontFile, "wiz-preview-front");
    renderPrev(backFile, "wiz-preview-back");
    showToast(`Auto-sorted! Front: ${frontFile.name} • Back: ${backFile.name}`);
  } catch (err) {
    showToast(err.message);
  }
}

async function populateMoveSubmissionDropdown(currentSubId) {
  const sel = $("move-card-sub-select");
  if (!sel) return;
  try {
    const subs = await apiJson("/api/submissions");
    sel.innerHTML = "";
    const others = subs.filter((s) => s.id !== currentSubId);
    if (!others.length) {
      const opt = document.createElement("option");
      opt.value = "";
      opt.textContent = "No other subs";
      sel.appendChild(opt);
      $("btn-move-card").disabled = true;
      return;
    }
    $("btn-move-card").disabled = false;
    const ph = document.createElement("option");
    ph.value = "";
    ph.textContent = "Move to submission...";
    sel.appendChild(ph);
    for (const s of others) {
      const opt = document.createElement("option");
      opt.value = String(s.id);
      opt.textContent = `[${s.company}] ${s.title} (${s.submitted_date || "No date"})`;
      sel.appendChild(opt);
    }
  } catch {
    // ignore
  }
}

function captureCardFormSnapshot() {
  state.lastCardSnapshot = {
    card_name: $("edit-card-name").value,
    card_year: $("edit-card-year").value,
    set_name: $("edit-set-name").value,
    card_number: $("edit-card-number").value,
    actual_grade: $("edit-actual-grade").value,
    market_value: $("edit-market-value").value,
    status: $("edit-status").value,
  };
}

async function pullCertScansForCurrentCard(certNum, silent = false) {
  if (!certNum) return;
  if (!silent) $("cert-fetch-status").textContent = "Fetching cert scans from grading company...";
  try {
    const res = await apiJson(`/api/cards/${state.currentCardId}/fetch-cert`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        cert_number: certNum,
        win_x: Math.round(window.screenX || 0),
        win_y: Math.round(window.screenY || 0),
        win_w: Math.round(window.outerWidth || window.innerWidth || 1200),
        win_h: Math.round(window.outerHeight || window.innerHeight || 800),
      }),
    });
    $("cert-fetch-status").textContent = res.message;
    if (res.card) {
      captureCardFormSnapshot();
      $("btn-undo-autofill").style.display = "inline-flex";
      state.currentCardData = res.card;
      renderAllCardPhotos(res.card);
      if (res.card.card_name) $("edit-card-name").value = res.card.card_name;
      if (res.card.card_year) $("edit-card-year").value = res.card.card_year;
      if (res.card.set_name) $("edit-set-name").value = res.card.set_name;
      if (res.card.card_number) $("edit-card-number").value = res.card.card_number;
      if (res.card.actual_grade) $("edit-actual-grade").value = res.card.actual_grade;
      if (res.card.market_value != null) setCostInputValue($("edit-market-value"), res.card.market_value);
      $("edit-status").value = res.card.status;
      updateComputedCardMetrics();
    }
    if (res.lookup_url) {
      const link = $("link-official-cert");
      link.href = res.lookup_url;
      link.style.display = "inline-flex";
    }
    if (!silent || res.success) {
      showToast(res.success ? "Cert scans pulled!" : "Official cert link ready.");
    }
  } catch (err) {
    $("cert-fetch-status").textContent = err.message;
  }
}

async function openCardDetail(cardId, pushHistory = true) {
  state.currentCardId = cardId;
  const c = await apiJson(`/api/cards/${cardId}`);
  state.currentCardData = c;
  state.currentSubId = c.submission_id;
  switchView("view-card", { view: "card", cardId, subId: c.submission_id, hash: `#card/${cardId}` }, pushHistory);

  $("card-detail-heading").textContent = `${c.card_name} (${c.submission_company} • ${c.submission_title})`;

  renderPhotoSlot("img-raw-front", "empty-raw-front", c.raw_front_image);
  renderPhotoSlot("img-raw-back", "empty-raw-back", c.raw_back_image);
  renderPhotoSlot("img-cert-front", "empty-cert-front", c.cert_front_image);
  renderPhotoSlot("img-cert-back", "empty-cert-back", c.cert_back_image);

  $("edit-cert-number").value = c.cert_number || "";
  const certLink = $("link-official-cert");
  if (c.cert_lookup_url) {
    certLink.href = c.cert_lookup_url;
    certLink.style.display = "inline-flex";
  } else {
    certLink.style.display = "none";
  }
  const psaZipLnk = $("link-psa-zip");
  if (psaZipLnk) {
    if (c.psa_images_zip_url) {
      psaZipLnk.href = c.psa_images_zip_url;
      psaZipLnk.style.display = "inline-flex";
    } else {
      psaZipLnk.style.display = "none";
    }
  }
  $("cert-fetch-status").textContent = "";
  if ($("market-fetch-status")) $("market-fetch-status").textContent = "";
  populateMoveSubmissionDropdown(c.submission_id);
  state.lastCardSnapshot = null;
  $("btn-undo-autofill").style.display = "none";

  $("edit-card-name").value = c.card_name || "";
  $("edit-set-name").value = c.set_name || "";
  $("edit-card-year").value = c.card_year || "";
  $("edit-card-number").value = c.card_number || "";
  $("edit-status").value = c.status || "In Grading";
  setCostInputValue($("edit-acq-cost"), c.acquisition_cost);
  setCostInputValue($("edit-grade-cost"), c.grading_cost);
  setCostInputValue($("edit-ship-cost"), c.shipping_cost);
  if ($("edit-pred-overall")) $("edit-pred-overall").value = c.pred_overall || "";
  $("edit-pred-corners").value = c.pred_corners ?? "";
  $("edit-pred-edges").value = c.pred_edges ?? "";
  $("edit-pred-centering").value = c.pred_centering ?? "";
  $("edit-pred-surface").value = c.pred_surface ?? "";
  $("edit-actual-grade").value = c.actual_grade || "";
  $("edit-actual-subgrades").value = c.actual_subgrades || "";
  if ($("edit-curated-date")) $("edit-curated-date").value = c.curated_date || "";
  if ($("edit-pc-url")) $("edit-pc-url").value = c.pricecharting_url || "";
  if ($("link-pc-url")) {
    const pcVal = (c.pricecharting_url || "").trim();
    if (pcVal.startsWith("http://") || pcVal.startsWith("https://")) {
      $("link-pc-url").href = pcVal;
      $("link-pc-url").textContent = pcVal.toLowerCase().includes("tcgplayer") ? "TCGplayer ↗" : "PriceCharting ↗";
      $("link-pc-url").style.display = "inline-flex";
    } else {
      $("link-pc-url").style.display = "none";
    }
  }
  setCostInputValue($("edit-initial-graded-value"), c.initial_graded_value);
  setCostInputValue($("edit-market-value"), c.market_value);
  $("edit-fanatics-url").value = c.fanatics_url || "";
  setCostInputValue($("edit-sold-price"), c.sold_price);
  setCostInputValue($("edit-selling-fees"), c.selling_fees);
  $("edit-sold-date").value = c.sold_date || "";
  $("edit-notes").value = c.notes || "";



  const fanaticsLink = $("link-fanatics");
  if (c.fanatics_url) {
    fanaticsLink.href = c.fanatics_url;
    fanaticsLink.textContent = detectLinkLabel(c.fanatics_url);
    fanaticsLink.style.display = "inline-flex";
  } else {
    fanaticsLink.style.display = "none";
  }

  updateComputedCardMetrics();

  // Cert scans are pulled only when clicking Pull Cert Scans manually.
}

/* ---------------- 3D SPIN / FLIP & ZOOM LIGHTBOX ---------------- */
function getCurrentLightboxPath() {
  const c = state.currentCardData;
  if (!c) return "";
  const side = state.lightboxFlipped ? "back" : "front";
  return state.lightboxGroup === "cert"
    ? (side === "front" ? c.cert_front_image : c.cert_back_image)
    : (side === "front" ? c.raw_front_image : c.raw_back_image);
}

function applyLightboxZoomTransform() {
  const frontImg = $("lightbox-front-img");
  const backImg = $("lightbox-back-img");
  const activeImg = state.lightboxFlipped ? backImg : frontImg;
  const otherImg = state.lightboxFlipped ? frontImg : backImg;
  if (otherImg) otherImg.style.transform = "translate(0px, 0px) scale(1)";
  if (activeImg) {
    activeImg.style.transform = `translate(${state.lightboxPanX}px, ${state.lightboxPanY}px) scale(${state.lightboxZoom})`;
  }
  const scene = $("card-3d-scene");
  if (scene) {
    scene.style.cursor = state.lightboxZoom > 1 ? (state.isPanning ? "grabbing" : "grab") : "pointer";
  }
  if ($("btn-zoom-reset")) {
    $("btn-zoom-reset").textContent = `${Math.round(state.lightboxZoom * 100)}%`;
  }
}

function resetLightboxZoom() {
  state.lightboxZoom = 1;
  state.lightboxPanX = 0;
  state.lightboxPanY = 0;
  state.isPanning = false;
  applyLightboxZoomTransform();
}

function setLightboxZoom(newZoom) {
  const clamped = Math.min(3.5, Math.max(1, Number(newZoom.toFixed(2))));
  state.lightboxZoom = clamped;
  if (clamped === 1) {
    state.lightboxPanX = 0;
    state.lightboxPanY = 0;
  }
  applyLightboxZoomTransform();
}

function updateLightboxLocalButtons() {
  const p = getCurrentLightboxPath();
  const isLocal = Boolean(p && !p.startsWith("http://") && !p.startsWith("https://"));
  if ($("btn-lightbox-open-file")) $("btn-lightbox-open-file").style.display = isLocal ? "inline-flex" : "none";
  if ($("btn-lightbox-open-folder")) $("btn-lightbox-open-folder").style.display = isLocal ? "inline-flex" : "none";
}

function openCardSpinLightbox(group, startSide) {
  const c = state.currentCardData;
  if (!c) return;
  state.lightboxGroup = group;
  const frontPath = group === "cert" ? c.cert_front_image : c.raw_front_image;
  const backPath = group === "cert" ? c.cert_back_image : c.raw_back_image;

  const frontImg = $("lightbox-front-img");
  const backImg = $("lightbox-back-img");
  const frontMiss = $("lightbox-front-missing");
  const backMiss = $("lightbox-back-missing");

  if (frontPath) {
    frontImg.src = resolveImgSrc(frontPath);
    frontImg.style.display = "block";
    frontMiss.style.display = "none";
  } else {
    frontImg.style.display = "none";
    frontMiss.style.display = "block";
  }

  if (backPath) {
    backImg.src = resolveImgSrc(backPath);
    backImg.style.display = "block";
    backMiss.style.display = "none";
  } else {
    backImg.style.display = "none";
    backMiss.style.display = "block";
  }

  state.lightboxFlipped = startSide === "back";
  resetLightboxZoom();
  updateLightboxFlipUI(group === "cert" ? "Cert Scan" : "Raw Photo");
  updateLightboxLocalButtons();
  $("lightbox").style.display = "flex";
}

function updateLightboxFlipUI(prefix = "Card") {
  const inner = $("card-3d-inner");
  if (state.lightboxFlipped) {
    inner.classList.add("is-flipped");
    $("lightbox-side-label").textContent = `${prefix} • Back`;
  } else {
    inner.classList.remove("is-flipped");
    $("lightbox-side-label").textContent = `${prefix} • Front`;
  }
}

function toggleLightboxSpin() {
  state.lightboxFlipped = !state.lightboxFlipped;
  resetLightboxZoom();
  const currentPrefix = $("lightbox-side-label").textContent.split(" • ")[0] || "Card";
  updateLightboxFlipUI(currentPrefix);
  updateLightboxLocalButtons();
}

function escapeHtml(str) {
  return String(str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

async function uploadDroppedCardScan(side, file) {
  if (!state.currentCardId || !file) return;
  const fd = new FormData();
  fd.append("side", side);
  fd.append("file", file, file.name || `${side}.png`);
  const res = await apiJson(`/api/cards/${state.currentCardId}/upload-scan`, { method: "POST", body: fd });
  if (res.card) {
    state.currentCardData = res.card;
    renderAllCardPhotos(res.card);
    showToast(`Uploaded ${side.replace("_", " ")} photo!`);
  }
}

/* ---------------- EVENT WIRING ---------------- */
async function runBatchWithModal({ title, items, cancelLabel = "✕ Cancel", batchSize = 5, workerFn, onCancel }) {
  const modal = $("import-progress-modal");
  const titleEl = $("imp-modal-title");
  const statusEl = $("imp-modal-status");
  const barEl = $("imp-modal-bar");
  const pctEl = $("imp-modal-pct");
  const cancelBtn = $("btn-cancel-import-modal");

  let cancelled = false;
  if (titleEl) titleEl.textContent = title || "Processing...";
  if (statusEl) statusEl.textContent = `Starting (${items.length} item${items.length === 1 ? "" : "s"})...`;
  if (barEl) barEl.style.width = "0%";
  if (pctEl) pctEl.textContent = "0%";
  if (cancelBtn) {
    cancelBtn.disabled = false;
    cancelBtn.textContent = cancelLabel;
    cancelBtn.onclick = async () => {
      cancelled = true;
      cancelBtn.disabled = true;
      if (statusEl) statusEl.textContent = "Stopping...";
      if (onCancel) await onCancel();
    };
  }
  if (modal) modal.style.display = "block";

  let done = 0;
  let successCount = 0;
  const BATCH = Math.max(1, batchSize || 5);
  try {
    for (let i = 0; i < items.length; i += BATCH) {
      if (cancelled) break;
      const chunk = items.slice(i, i + BATCH);
      await Promise.all(chunk.map(async (item) => {
        if (cancelled) return;
        const ok = await workerFn(item);
        if (ok) successCount++;
        done++;
        if (!cancelled) {
          const pct = Math.round((done / Math.max(1, items.length)) * 100);
          if (statusEl) statusEl.textContent = `Processed ${done} of ${items.length}...`;
          if (barEl) barEl.style.width = `${pct}%`;
          if (pctEl) pctEl.textContent = `${pct}%`;
        }
      }));
    }
  } finally {
    if (modal) modal.style.display = "none";
    if (cancelBtn) cancelBtn.onclick = null;
  }
  return { cancelled, done, successCount };
}


function downloadCleanEbayBookkeepingCsv(items) {
  if (!items || !items.length) return;
  const hdr = ["Sale Date", "Order Number", "Item Number", "Item Title", "Qty", "Sold For", "Shipping Collected", "eBay Tax Collected"];
  const rows = items.map((x) => [
    x.sale_date, x.order_number, x.item_number, `"${(x.item_title || "").replace(/"/g, '""')}"`,
    x.quantity, Number(x.sold_for || 0).toFixed(2), Number(x.shipping_collected || 0).toFixed(2), Number(x.ebay_tax || 0).toFixed(2),
  ].join(","));
  const blob = new Blob([[hdr.join(","), ...rows].join("\n")], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `ebay_bookkeeping_clean_${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
}

function openEbayReviewModal(res, onApplied) {
  const modal = $("ebay-modal");
  const bodyEl = $("ebay-import-summary");
  const footerEl = $("ebay-modal-footer");
  const dlBtn = $("btn-download-ebay-bookkeeping");
  const confirmBtn = $("btn-confirm-ebay-apply");
  const cancelBtn = $("btn-cancel-ebay-apply");
  const closeBtn = $("btn-close-ebay");
  const unmatchedCb = $("ebay-create-unmatched-cb");
  const unmatchedLabel = $("ebay-create-unmatched-label");
  const subTitleInput = $("ebay-sub-title-input");
  if (!modal || !bodyEl) return;

  const matched = res.matched || [];
  const unmatched = res.unmatched || [];
  const items = res.items || [];
  const multiCount = matched.filter((m) => (m.candidates || []).length > 1).length;

  if (dlBtn) {
    dlBtn.style.display = items.length ? "inline-flex" : "none";
    dlBtn.onclick = () => downloadCleanEbayBookkeepingCsv(items);
  }
  if (subTitleInput) subTitleInput.value = "";

  const closeEbayModal = () => {
    modal.style.display = "none";
  };
  if (closeBtn) closeBtn.onclick = closeEbayModal;
  if (cancelBtn) cancelBtn.onclick = closeEbayModal;

  const updateFooterSummary = () => {
    const checkedCount = bodyEl.querySelectorAll(".ebay-match-cb:checked").length;
    const uncheckedCount = matched.length - checkedCount;
    const newSubCount = unmatched.length + uncheckedCount;
    if (confirmBtn) {
      confirmBtn.disabled = false;
      confirmBtn.textContent = checkedCount > 0
        ? `✅ Confirm & Mark ${checkedCount} Sold`
        : "✅ Confirm & Apply Updates";
    }
    if (unmatchedLabel) {
      unmatchedLabel.textContent = `Create an "eBay Sales" submission with ${newSubCount} unmatched / unchecked item(s) for ROI & bookkeeping`;
    }
    if (unmatchedCb) {
      unmatchedCb.closest("label").style.display = newSubCount > 0 ? "flex" : "none";
    }
  };

  const renderThumbHtml = (imgUrl, cardName) => {
    const resolved = resolveImgSrc(imgUrl);
    if (resolved) {
      return `<img src="${escapeHtml(resolved)}" alt="${escapeHtml(cardName)}" loading="lazy" onerror="this.outerHTML='<div class=\'ebay-thumb-empty\'>No Scan</div>'" />`;
    }
    return `<div class="ebay-thumb-empty">No Scan</div>`;
  };

  const renderProfitHtml = (soldFor, totalCost) => {
    const cost = Number(totalCost || 0);
    const sold = Number(soldFor || 0);
    if (cost <= 0) {
      return `<span class="muted" style="font-size:12px;">Card Cost: $0.00</span>`;
    }
    const profit = sold - cost;
    const roi = (profit / cost) * 100;
    const color = profit >= 0 ? "var(--gem)" : "var(--danger, #ff5c7a)";
    const sign = profit >= 0 ? "+" : "";
    return `<span style="font-size:12.5px;font-weight:700;color:${color};">Cost ${fmtMoney(cost)} → Profit ${sign}${fmtMoney(profit)} (${sign}${roi.toFixed(0)}% ROI)</span>`;
  };

  const matchRowsHtml = matched.length
    ? matched.map((m, idx) => {
        const cands = m.candidates || [];
        const hasMulti = cands.length > 1;
        const firstCand = cands[0] || m;
        const vaultColHtml = hasMulti
          ? `
            <span class="ebay-cand-label">⚠️ ${cands.length} vault cards match — choose which submission/card to mark Sold:</span>
            <select class="ebay-cand-select" data-idx="${idx}">
              ${cands.map((cd) => {
                const bits = [
                  cd.submission_title || "Submission",
                  cd.card_name || "Card",
                  cd.card_number ? `#${cd.card_number}` : "",
                  cd.actual_grade ? `Grade ${cd.actual_grade}` : "",
                  cd.cert_number ? `Cert #${cd.cert_number}` : "",
                ].filter(Boolean);
                return `<option value="${cd.card_id}">${escapeHtml(bits.join(" • "))}</option>`;
              }).join("")}
            </select>
            <div class="ebay-cand-profit" data-idx="${idx}">${renderProfitHtml(m.sold_for, firstCand.total_cost)}</div>
          `
          : `
            <div style="font-size:14.5px;font-weight:700;color:var(--text);">${escapeHtml(m.card_name)} ${m.card_number ? `<span class="muted">#${escapeHtml(m.card_number)}</span>` : ""}</div>
            <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;font-size:12.5px;">
              <span class="badge badge-${escapeHtml(m.submission_company || 'PSA')}" style="font-size:11px;">${escapeHtml(m.submission_company || "Vault")}</span>
              <span style="font-weight:600;">${escapeHtml(m.submission_title)}</span>
              ${m.actual_grade ? `<span class="badge" style="font-size:11px;">Grade ${escapeHtml(m.actual_grade)}</span>` : ""}
              ${m.cert_number ? `<span class="muted">Cert #${escapeHtml(m.cert_number)}</span>` : ""}
              <span class="badge" style="font-size:10.5px;opacity:0.85;">${escapeHtml(m.match_reason || "Matched")}</span>
            </div>
            <div class="ebay-cand-profit" data-idx="${idx}">${renderProfitHtml(m.sold_for, m.total_cost)}</div>
          `;

        return `
          <div class="ebay-match-row ${hasMulti ? "has-multi" : ""}" data-idx="${idx}" data-multi="${hasMulti ? "1" : "0"}" data-search="${escapeHtml((m.card_name + " " + m.submission_title + " " + m.item_title + " " + (m.cert_number || "")).toLowerCase())}">
            <input type="checkbox" class="ebay-match-cb" data-idx="${idx}" checked style="width:18px;height:18px;cursor:pointer;" />
            <div class="ebay-thumb-wrap" data-idx="${idx}">
              ${renderThumbHtml(firstCand.image_url || m.image_url, firstCand.card_name || m.card_name)}
            </div>
            <div class="ebay-vault-col">
              ${vaultColHtml}
            </div>
            <div class="ebay-sale-col">
              <div style="font-size:11.5px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:0.3px;">Sold on eBay • ${escapeHtml(m.sale_date || "Date N/A")}</div>
              <div style="font-size:13.5px;font-weight:600;line-height:1.35;">${escapeHtml(m.item_title)}</div>
              <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;font-size:13px;margin-top:2px;">
                <strong style="color:var(--gem);font-size:15px;">${fmtMoney(m.sold_for)}</strong>
                ${m.shipping_collected ? `<span class="muted">Ship: ${fmtMoney(m.shipping_collected)}</span>` : ""}
                ${m.ebay_tax ? `<span class="muted">Tax: ${fmtMoney(m.ebay_tax)}</span>` : ""}
              </div>
            </div>
          </div>`;
      }).join("")
    : `<div class="muted" style="padding:24px;text-align:center;font-size:14px;background:var(--surface);border-radius:10px;border:1px solid var(--border);">No existing vault cards matched these eBay titles. You can still create an "eBay Sales" bookkeeping submission below or download the clean CSV.</div>`;

  bodyEl.innerHTML = `
    <div class="ebay-kpi-strip">
      <div class="ebay-kpi-box"><div class="ebay-kpi-label">Total Sales</div><div class="ebay-kpi-val">${res.total_items}</div></div>
      <div class="ebay-kpi-box"><div class="ebay-kpi-label">Matched Vault Cards</div><div class="ebay-kpi-val" style="color:var(--gem);">${matched.length}</div></div>
      <div class="ebay-kpi-box"><div class="ebay-kpi-label">Unmatched Sales</div><div class="ebay-kpi-val">${unmatched.length}</div></div>
      <div class="ebay-kpi-box"><div class="ebay-kpi-label">Gross Sales</div><div class="ebay-kpi-val">${fmtMoney(res.gross_sales)}</div></div>
      <div class="ebay-kpi-box"><div class="ebay-kpi-label">Shipping / eBay Tax</div><div class="ebay-kpi-val" style="font-size:15px;">${fmtMoney(res.total_shipping_collected)} / ${fmtMoney(res.total_ebay_tax)}</div></div>
    </div>
    ${matched.length ? `
    <div class="ebay-toolbar">
      <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
        <input type="text" id="ebay-review-search" placeholder="Search matched cards, subs, or eBay titles..." style="min-width:250px;font-size:13px;padding:7px 11px;" />
        <button type="button" class="filter-pill active" data-ebay-filter="all">All Matched (${matched.length})</button>
        ${multiCount ? `<button type="button" class="filter-pill" data-ebay-filter="multi">⚠️ Multiple Sub Matches (${multiCount})</button>` : ""}
      </div>
      <div style="display:flex;align-items:center;gap:8px;">
        <button type="button" class="btn btn-ghost btn-sm" id="ebay-select-all-btn">Select All</button>
        <button type="button" class="btn btn-ghost btn-sm" id="ebay-deselect-all-btn">Deselect All</button>
      </div>
    </div>` : ""}
    <div class="ebay-match-list">${matchRowsHtml}</div>
  `;

  if (footerEl) footerEl.style.display = "flex";
  modal.style.display = "flex";
  updateFooterSummary();

  // Wire row checkboxes & candidate dropdowns
  bodyEl.querySelectorAll(".ebay-match-cb").forEach((cb) => {
    cb.addEventListener("change", () => {
      const row = bodyEl.querySelector(`.ebay-match-row[data-idx="${cb.dataset.idx}"]`);
      if (row) row.classList.toggle("is-unchecked", !cb.checked);
      updateFooterSummary();
    });
  });

  const bindEbayThumbHover = (imgEl) => {
    if (!imgEl) return;
    const previewBox = $("thumb-hover-preview");
    const previewImg = $("thumb-hover-img");
    const positionPreview = (ev) => {
      if (!previewBox) return;
      const pad = 18;
      const boxW = previewBox.offsetWidth || 320;
      const boxH = previewBox.offsetHeight || 420;
      let left = ev.clientX + pad;
      if (left + boxW > window.innerWidth - 12) {
        left = Math.max(12, ev.clientX - boxW - pad);
      }
      let top = ev.clientY - boxH / 2;
      top = Math.max(12, Math.min(window.innerHeight - boxH - 12, top));
      previewBox.style.left = `${left}px`;
      previewBox.style.top = `${top}px`;
    };
    imgEl.addEventListener("mouseenter", (ev) => {
      if (!previewBox || !previewImg) return;
      previewImg.src = imgEl.src;
      previewBox.style.display = "block";
      positionPreview(ev);
    });
    imgEl.addEventListener("mousemove", positionPreview);
    imgEl.addEventListener("mouseleave", () => {
      if (previewBox) previewBox.style.display = "none";
    });
  };

  bodyEl.querySelectorAll(".ebay-thumb-wrap img").forEach(bindEbayThumbHover);

  bodyEl.querySelectorAll(".ebay-cand-select").forEach((sel) => {
    sel.addEventListener("change", () => {
      const idx = Number(sel.dataset.idx);
      const m = matched[idx];
      const cand = (m?.candidates || []).find((c) => String(c.card_id) === String(sel.value));
      if (!cand) return;
      const thumbWrap = bodyEl.querySelector(`.ebay-thumb-wrap[data-idx="${idx}"]`);
      if (thumbWrap) {
        thumbWrap.innerHTML = renderThumbHtml(cand.image_url, cand.card_name);
        bindEbayThumbHover(thumbWrap.querySelector("img"));
      }
      const profitEl = bodyEl.querySelector(`.ebay-cand-profit[data-idx="${idx}"]`);
      if (profitEl) profitEl.innerHTML = renderProfitHtml(m.sold_for, cand.total_cost);
    });
  });

  // Search & filter pills
  let activeFilter = "all";
  const applyReviewFilter = () => {
    const q = ($("ebay-review-search")?.value || "").trim().toLowerCase();
    bodyEl.querySelectorAll(".ebay-match-row").forEach((row) => {
      const matchesQ = !q || (row.dataset.search || "").includes(q);
      const matchesMulti = activeFilter !== "multi" || row.dataset.multi === "1";
      row.style.display = matchesQ && matchesMulti ? "grid" : "none";
    });
  };
  $("ebay-review-search")?.addEventListener("input", applyReviewFilter);
  bodyEl.querySelectorAll("[data-ebay-filter]").forEach((pill) => {
    pill.addEventListener("click", () => {
      bodyEl.querySelectorAll("[data-ebay-filter]").forEach((p) => p.classList.remove("active"));
      pill.classList.add("active");
      activeFilter = pill.dataset.ebayFilter;
      applyReviewFilter();
    });
  });
  $("ebay-select-all-btn")?.addEventListener("click", () => {
    bodyEl.querySelectorAll(".ebay-match-cb").forEach((cb) => {
      cb.checked = true;
      const row = bodyEl.querySelector(`.ebay-match-row[data-idx="${cb.dataset.idx}"]`);
      if (row) row.classList.remove("is-unchecked");
    });
    updateFooterSummary();
  });
  $("ebay-deselect-all-btn")?.addEventListener("click", () => {
    bodyEl.querySelectorAll(".ebay-match-cb").forEach((cb) => {
      cb.checked = false;
      const row = bodyEl.querySelector(`.ebay-match-row[data-idx="${cb.dataset.idx}"]`);
      if (row) row.classList.add("is-unchecked");
    });
    updateFooterSummary();
  });

  if (confirmBtn) {
    confirmBtn.onclick = async () => {
      confirmBtn.disabled = true;
      confirmBtn.textContent = "Applying...";
      const checkedIdxs = new Set(Array.from(bodyEl.querySelectorAll(".ebay-match-cb:checked")).map((cb) => Number(cb.dataset.idx)));
      const selectedMatched = matched
        .map((m, idx) => {
          if (!checkedIdxs.has(idx)) return null;
          const sel = bodyEl.querySelector(`.ebay-cand-select[data-idx="${idx}"]`);
          return sel ? { ...m, card_id: Number(sel.value) } : m;
        })
        .filter(Boolean);
      const skippedMatchedAsNew = matched.filter((_, idx) => !checkedIdxs.has(idx));
      const allForNewSub = [...unmatched, ...skippedMatchedAsNew];
      const createUnmatched = Boolean(unmatchedCb?.checked) && allForNewSub.length > 0;
      try {
        const cRes = await apiJson("/api/ebay/confirm-sales", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            matched_updates: selectedMatched,
            unmatched_items: allForNewSub,
            create_unmatched_sub: createUnmatched,
            sub_title: subTitleInput ? subTitleInput.value.trim() : "",
          }),
        });
        showToast(cRes.message);
        closeEbayModal();
        if (onApplied) onApplied(cRes);
      } catch (err) {
        showToast(`Confirm failed: ${err.message}`);
        updateFooterSummary();
      }
    };
  }

  const noSoldBtn = $("btn-ebay-import-no-sold");
  if (noSoldBtn) {
    noSoldBtn.disabled = false;
    noSoldBtn.textContent = "📥 Import Without Marking Sold";
    noSoldBtn.onclick = async () => {
      const allItems = res.items || [...matched, ...unmatched];
      if (!allItems.length) return;
      noSoldBtn.disabled = true;
      noSoldBtn.textContent = "Importing...";
      try {
        const cRes = await apiJson("/api/ebay/confirm-sales", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            matched_updates: [],
            unmatched_items: allItems,
            create_unmatched_sub: true,
            sub_title: subTitleInput ? subTitleInput.value.trim() : "",
          }),
        });
        showToast(`Imported ${allItems.length} eBay sales without marking any vault cards Sold.`);
        closeEbayModal();
        if (onApplied) onApplied(cRes);
      } catch (err) {
        showToast(`Import failed: ${err.message}`);
        noSoldBtn.disabled = false;
        noSoldBtn.textContent = "📥 Import Without Marking Sold";
      }
    };
  }
}

function applyViewPrefs() {
  const mode = localStorage.getItem("tcg_view_mode") || "cards";
  const isList = mode === "list";
  const sg = $("submissions-grid");
  if (sg) {
    sg.classList.toggle("list-mode", isList);
    sg.classList.remove("size-sm", "size-md", "size-lg");
  }
  const cl = $("detail-cards-list");
  if (cl) {
    cl.classList.toggle("list-mode", isList);
    cl.classList.remove("size-sm", "size-md", "size-lg");
  }
  if ($("btn-home-view-mode")) $("btn-home-view-mode").textContent = isList ? "⊞ Cards" : "☰ List";
  if ($("btn-sub-view-mode")) $("btn-sub-view-mode").textContent = isList ? "⊞ Cards" : "☰ List";
}

document.addEventListener("DOMContentLoaded", () => {
  applyTheme(getTheme());
  loadBrandSettings();

  // Click outside any modal backdrop to close
  document.querySelectorAll(".modal-backdrop").forEach((backdrop) => {
    backdrop.addEventListener("click", (e) => {
      if (e.target === backdrop) backdrop.style.display = "none";
    });
  });

  // Brand customization modal
  $("btn-edit-brand").addEventListener("click", async (e) => {
    e.stopPropagation();
    try {
      const b = await apiJson("/api/brand");
      $("brand-name-input").value = b.brand_name || "";
      $("brand-subtitle-input").value = b.brand_subtitle || "";
    } catch {}
    $("brand-icon-file").value = "";
    $("brand-modal").style.display = "flex";
  });
  $("btn-close-brand").addEventListener("click", () => ($("brand-modal").style.display = "none"));
  $("brand-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData();
    fd.append("brand_name", $("brand-name-input").value.trim() || "TCG Grading Vault");
    fd.append("brand_subtitle", $("brand-subtitle-input").value.trim());
    fd.append("reset_icon", "false");
    const iconFile = $("brand-icon-file").files[0];
    if (iconFile) fd.append("icon", iconFile);
    const b = await apiJson("/api/brand", { method: "POST", body: fd });
    applyBrandUI(b);
    $("brand-modal").style.display = "none";
    showToast("Vault branding updated!");
  });
  $("btn-reset-brand-icon").addEventListener("click", async () => {
    const fd = new FormData();
    fd.append("brand_name", $("brand-name-input").value.trim() || "TCG Grading Vault");
    fd.append("brand_subtitle", $("brand-subtitle-input").value.trim());
    fd.append("reset_icon", "true");
    const b = await apiJson("/api/brand", { method: "POST", body: fd });
    applyBrandUI(b);
    showToast("Reset to default icon!");
  });

  // Fanatics CSV Import modal inside Submission Detail
  $("btn-open-fanatics-col-sync").addEventListener("click", () => {
    $("fanatics-col-modal").style.display = "flex";
  });
  $("btn-close-fanatics-col").addEventListener("click", () => ($("fanatics-col-modal").style.display = "none"));
  $("btn-cancel-fanatics-col").addEventListener("click", () => ($("fanatics-col-modal").style.display = "none"));
  $("fanatics-col-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!state.currentSubId) return;
    const btn = $("btn-submit-fanatics-col");
    btn.disabled = true;
    btn.textContent = "Syncing...";
    try {
      const res = await apiJson(`/api/submissions/${state.currentSubId}/sync-fanatics-collection`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          url: $("fanatics-col-url").value.trim(),
          win_x: Math.round(window.screenX || 0),
          win_y: Math.round(window.screenY || 0),
          win_w: Math.round(window.outerWidth || window.innerWidth || 1200),
          win_h: Math.round(window.outerHeight || window.innerHeight || 800),
          force_replace: Boolean($("fanatics-col-force")?.checked),
        }),
      });
      $("fanatics-col-modal").style.display = "none";
      showToast(res.message);
      openSubmission(state.currentSubId, false, "preserve");
    } catch (err) {
      showToast(`Sync failed: ${err.message}`);
    } finally {
      btn.disabled = false;
      btn.textContent = "🔗 Sync Links & Photos";
    }
  });

  $("btn-open-fanatics-csv").addEventListener("click", () => {
    $("fanatics-csv-form").reset();
    $("fanatics-csv-modal").style.display = "flex";
  });
  $("btn-close-fanatics-csv").addEventListener("click", () => ($("fanatics-csv-modal").style.display = "none"));
  $("btn-cancel-fanatics-csv").addEventListener("click", () => ($("fanatics-csv-modal").style.display = "none"));
  $("fanatics-csv-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const file = $("fanatics-csv-file").files[0];
    if (!file || !state.currentSubId) return;
    const btn = $("btn-submit-fanatics-csv");
    btn.disabled = true;
    btn.textContent = "Importing...";
    try {
      const fd = new FormData();
      fd.append("file", file);
      const res = await apiJson(`/api/submissions/${state.currentSubId}/import-fanatics-csv`, { method: "POST", body: fd });
      $("fanatics-csv-modal").style.display = "none";
      showToast(res.message);
      openSubmission(state.currentSubId, false, "preserve");
    } catch (err) {
      showToast(`CSV import failed: ${err.message}`);
    } finally {
      btn.disabled = false;
      btn.textContent = "Import & Populate Cards";
    }
  });

  $("nav-home").addEventListener("click", async () => {
    if (state.currentView === "view-card") await saveCurrentCardChanges(true);
    loadHome();
  });
  $("btn-home-nav").addEventListener("click", async () => {
    if (state.currentView === "view-card") await saveCurrentCardChanges(true);
    loadHome();
  });

  let homeSearchTimer = null;
  $("home-card-search").addEventListener("input", (e) => {
    const q = e.target.value.trim();
    clearTimeout(homeSearchTimer);
    if (!q) {
      $("home-card-results").style.display = "none";
      $("submissions-grid").style.display = "grid";
      return;
    }
    homeSearchTimer = setTimeout(async () => {
      const matches = await apiJson(`/api/cards?q=${encodeURIComponent(q)}`);
      $("submissions-grid").style.display = "none";
      const box = $("home-card-results");
      box.style.display = "block";
      box.innerHTML = `<div class="muted" style="margin-bottom:10px;font-size:13px;">Found <strong>${matches.length}</strong> matching card${matches.length === 1 ? "" : "s"} across all submissions:</div><div class="cards-list" id="home-search-cards-list"></div>`;
      renderReadOnlyCardsList($("home-search-cards-list"), matches, `No cards match "${q}".`);
    }, 180);
  });

  $("btn-open-all-cards").addEventListener("click", () => openAllCardsView(true));
  $("btn-all-cards-back").addEventListener("click", () => loadHome(true, "restore"));
  $("all-cards-search").addEventListener("input", applyAllCardsFilterAndSort);
  $("all-cards-company").addEventListener("change", applyAllCardsFilterAndSort);
  $("all-cards-sort").addEventListener("change", applyAllCardsFilterAndSort);

  $("sort-select").addEventListener("change", loadHome);
  $("filter-company").addEventListener("change", loadHome);
  $("filter-status").addEventListener("change", loadHome);

  // Drag-and-drop on all .dropzone file inputs
  document.querySelectorAll(".dropzone").forEach((dz) => {
    const input = $(dz.dataset.input);
    if (!input) return;
    ["dragenter", "dragover"].forEach((ev) => {
      dz.addEventListener(ev, (e) => {
        e.preventDefault();
        dz.classList.add("drag-over");
      });
    });
    ["dragleave", "drop"].forEach((ev) => {
      dz.addEventListener(ev, (e) => {
        e.preventDefault();
        dz.classList.remove("drag-over");
      });
    });
    dz.addEventListener("drop", (e) => {
      const files = e.dataTransfer?.files;
      if (!files || !files.length) return;
      if (files.length >= 2 && (input.id === "wiz-raw-front" || input.id === "wiz-raw-back")) {
        classifyAndAssignWizardRawPair(files[0], files[1]);
        return;
      }
      if (files.length >= 2 && (input.id === "edit-raw-front-file" || input.id === "edit-raw-back-file")) {
        uploadRawPair(files[0], files[1]);
        return;
      }
      const dt = new DataTransfer();
      dt.items.add(files[0]);
      input.files = dt.files;
      input.dispatchEvent(new Event("change", { bubbles: true }));
      showToast(`Attached ${files[0].name}`);
    });
  });

  // Drag-and-drop directly onto Card Detail's 4 photo slots
  document.querySelectorAll(".drop-slot").forEach((slot) => {
    ["dragenter", "dragover"].forEach((ev) => {
      slot.addEventListener(ev, (e) => {
        e.preventDefault();
        slot.classList.add("drag-over");
      });
    });
    ["dragleave", "drop"].forEach((ev) => {
      slot.addEventListener(ev, (e) => {
        e.preventDefault();
        slot.classList.remove("drag-over");
      });
    });
    slot.addEventListener("drop", (e) => {
      const files = e.dataTransfer?.files;
      if (files && files.length) {
        if (files.length >= 2 && slot.dataset.side.startsWith("raw_")) {
          uploadRawPair(files[0], files[1]);
          return;
        }
        uploadDroppedCardScan(slot.dataset.side, files[0]);
        return;
      }
      const draggedUrl = extractDraggedImageUrl(e.dataTransfer);
      if (draggedUrl) {
        uploadDroppedImageUrl(slot.dataset.side, draggedUrl);
      }
    });
  });

  // Share Stats Infographic Buttons
  $("btn-share-overall").addEventListener("click", () => {
    openInfographicModal("/api/infographic/overall", "Share Overall Grading Stats", "grading_stats.png");
  });
  $("btn-share-sub").addEventListener("click", () => {
    openInfographicModal(`/api/infographic/submission/${state.currentSubId}`, "Share Submission Stats", `submission_${state.currentSubId}_stats.png`);
  });
  $("btn-share-card").addEventListener("click", () => {
    openInfographicModal(`/api/infographic/card/${state.currentCardId}`, "Share Card Stats", `card_${state.currentCardId}_stats.png`);
  });
  $("btn-close-share").addEventListener("click", () => ($("share-modal").style.display = "none"));
  document.querySelectorAll("[data-share-theme]").forEach((btn) => {
    btn.addEventListener("click", () => setInfographicTheme(btn.dataset.shareTheme));
  });

  // Collectr Export Modal
  const csvEsc = (v) => {
    const s = String(v ?? "");
    return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const buildCollectrCsvText = () => {
    const sub = state.currentSubData || {};
    const cards = state.currentSubCards || [];
    const headers = [
      "Quantity", "Card Name", "Set Name", "Collector Number", "Variant",
      "Language", "Condition", "Graded", "Grading Company", "Grade",
      "Cert Number", "Purchase Price"
    ];
    const rows = [headers.join(",")];
    for (const c of cards) {
      const nm = (c.card_name || "").trim();
      const nmLow = nm.toLowerCase();
      let variant = "";
      if (nmLow.includes("master ball")) variant = "Master Ball Reverse Holo";
      else if (nmLow.includes("poke ball") || nmLow.includes("pokeball")) variant = "Poke Ball Reverse Holo";
      else if (nmLow.includes("reverse holo") || nmLow.includes("reverse foil")) variant = "Reverse Holofoil";
      else if (nmLow.includes("full art")) variant = "Full Art";
      else if (nmLow.includes("holo")) variant = "Holofoil";

      let lang = "English";
      if (/\b(japanese|jpn|jp)\b/i.test(nm + " " + (c.set_name || ""))) lang = "Japanese";
      else if (/\b(chinese|chs|cht)\b/i.test(nm + " " + (c.set_name || ""))) lang = "Chinese";
      else if (/\b(korean|kor)\b/i.test(nm + " " + (c.set_name || ""))) lang = "Korean";

      const isGraded = Boolean((c.actual_grade || "").trim() || (c.cert_number || "").trim());
      rows.push([
        "1",
        csvEsc(nm),
        csvEsc(c.set_name || ""),
        csvEsc(c.card_number || ""),
        csvEsc(variant),
        csvEsc(lang),
        "Near Mint",
        isGraded ? "Yes" : "No",
        csvEsc(isGraded ? (sub.company || "") : ""),
        csvEsc(c.actual_grade || ""),
        csvEsc(c.cert_number || ""),
        Number(c.total_cost || 0).toFixed(2),
      ].join(","));
    }
    return rows.join("\n");
  };
  if ($("btn-export-collectr")) {
    $("btn-export-collectr").addEventListener("click", () => {
      if (!state.currentSubCards || !state.currentSubCards.length) {
        showToast("No cards in this submission to export.");
        return;
      }
      $("collectr-export-preview").value = buildCollectrCsvText();
      $("collectr-export-modal").style.display = "flex";
    });
  }
  $("btn-close-collectr")?.addEventListener("click", () => ($("collectr-export-modal").style.display = "none"));

  // Export Submission Bundle (.zip)
  $("btn-export-sub-bundle")?.addEventListener("click", () => {
    if (!state.currentSubId) return;
    window.location.href = `/api/submissions/${state.currentSubId}/export-bundle`;
    showToast("Downloading portable submission bundle (.zip)...");
  });

  // Import Submission Bundle Modal (Home)
  $("btn-open-import-bundle")?.addEventListener("click", async () => {
    try {
      const info = await apiJson("/api/shared-submissions");
      if ($("shared-subs-folder-path") && info.folder) {
        $("shared-subs-folder-path").textContent = info.folder;
      }
    } catch (_) {}
    if ($("import-bundle-files")) $("import-bundle-files").value = "";
    $("import-bundle-modal").style.display = "flex";
  });
  $("btn-close-import-bundle")?.addEventListener("click", () => ($("import-bundle-modal").style.display = "none"));
  $("btn-cancel-import-bundle")?.addEventListener("click", () => ($("import-bundle-modal").style.display = "none"));
  $("btn-sync-shared-folder")?.addEventListener("click", async () => {
    $("import-bundle-modal").style.display = "none";
    await loadHome(false, "preserve");
    showToast("Synced data/shared_submissions/ folder!");
  });
  $("btn-confirm-import-bundle")?.addEventListener("click", async () => {
    const fEl = $("import-bundle-files");
    if (!fEl || !fEl.files || !fEl.files.length) {
      showToast("Choose one or more *_sub.zip files first.");
      return;
    }
    const fd = new FormData();
    for (const f of fEl.files) fd.append("files", f);
    try {
      const res = await apiJson("/api/submissions/import-bundle", { method: "POST", body: fd });
      $("import-bundle-modal").style.display = "none";
      showToast(res.message || "Submission(s) imported!");
      await loadHome(false, "preserve");
    } catch (err) {
      showToast(`Import failed: ${err.message}`);
    }
  });
  $("btn-copy-collectr-csv")?.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText($("collectr-export-preview").value);
      showToast("Collectr CSV copied to clipboard!");
    } catch {
      $("collectr-export-preview").select();
      document.execCommand("copy");
      showToast("Collectr CSV copied!");
    }
  });
  const triggerTextDownload = (content, ext, mime) => {
    const slug = (state.currentSubData?.title || "submission").replace(/[^a-z0-9_-]+/gi, "_").replace(/^_+|_+$/g, "") || "submission";
    const blob = new Blob([content], { type: mime });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${slug}_collectr.${ext}`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1500);
  };
  $("btn-download-collectr-csv")?.addEventListener("click", () => {
    triggerTextDownload($("collectr-export-preview").value, "csv", "text/csv;charset=utf-8;");
    showToast("Downloaded Collectr .csv!");
  });
  $("btn-download-collectr-txt")?.addEventListener("click", () => {
    triggerTextDownload($("collectr-export-preview").value, "txt", "text/plain;charset=utf-8;");
    showToast("Downloaded Collectr .txt!");
  });
  $("btn-copy-infographic").addEventListener("click", async () => {
    try {
      const resp = await fetch($("share-preview-img").src);
      const blob = await resp.blob();
      await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
      showToast("Stats infographic copied to clipboard!");
    } catch {
      showToast("Use Download PNG to save the infographic.");
    }
  });

  // Settings / Theme Modal
  $("btn-page-help").addEventListener("click", () => {
    renderHelpTab(state.currentView || "view-home");
    $("help-modal").style.display = "flex";
  });
  $("btn-close-help").addEventListener("click", () => ($("help-modal").style.display = "none"));
  $("btn-done-help").addEventListener("click", () => ($("help-modal").style.display = "none"));
  $("btn-open-about")?.addEventListener("click", () => ($("about-modal").style.display = "flex"));
  $("btn-close-about")?.addEventListener("click", () => ($("about-modal").style.display = "none"));
  $("btn-done-about")?.addEventListener("click", () => ($("about-modal").style.display = "none"));
  
  // v0.80 Wire-ups: Home/End keys, Back to Top, Check Updates, In-App Tabs, Stream Showcase, Bulk Share Graphic, Auctions
  document.addEventListener("keydown", (e) => {
    const tag = (e.target?.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select") return;
    if (e.key === "Home") {
      e.preventDefault();
      window.scrollTo({ top: 0, behavior: "smooth" });
    } else if (e.key === "End") {
      e.preventDefault();
      window.scrollTo({ top: document.documentElement.scrollHeight, behavior: "smooth" });
    } else if ($("showcase-modal")?.style.display === "flex") {
      if (e.key === "ArrowRight" && showcaseIdx < showcaseCards.length - 1) { showcaseIdx++; renderStreamerShowcase(); }
      else if (e.key === "ArrowLeft" && showcaseIdx > 0) { showcaseIdx--; renderStreamerShowcase(); }
      else if (e.key === "Escape") $("showcase-modal").style.display = "none";
    }
  });
  $("btn-back-to-top")?.addEventListener("click", () => window.scrollTo({ top: 0, behavior: "smooth" }));
  $("btn-close-inapp-drawer")?.addEventListener("click", () => { inAppTabs.length = 0; renderInAppTabs(); });

  // Route Card Detail pricing link to In-App Popup; route all other external links to System Default Browser
  document.addEventListener("click", (e) => {
    const a = e.target.closest("a");
    if (!a) return;
    if (a.id === "btn-download-infographic" || a.id === "btn-download-auctions-html" || a.hasAttribute("download")) return;
    const href = (a.getAttribute("href") || "").trim();
    if (!href.startsWith("http://") && !href.startsWith("https://")) return;
    e.preventDefault();
    e.stopPropagation();
    if (a.id === "link-pc-url") {
      openInAppTab(href, a.textContent.trim() || "Pricing");
      window.scrollTo({ top: 0, behavior: "smooth" });
      return;
    }
    openExternalUrl(href);
  });

  $("btn-check-updates")?.addEventListener("click", async () => {
    const box = $("update-check-box");
    const btn = $("btn-check-updates");
    if (!box) return;
    btn.disabled = true;
    box.style.display = "block";
    box.textContent = "Checking GitHub for newer releases...";
    try {
      const res = await apiJson("/api/check-updates");
      if (res.update_available) {
        box.innerHTML = `<strong>${escapeHtml(res.message)}</strong><div style="margin-top:6px;display:flex;gap:8px;flex-wrap:wrap;"><a href="${escapeHtml(res.download_url || res.release_url)}" target="_blank" rel="noopener noreferrer" class="btn btn-primary btn-sm">⬇ Download v${escapeHtml(res.latest_version)}</a><a href="${escapeHtml(res.release_url)}" target="_blank" rel="noopener noreferrer" class="btn btn-secondary btn-sm">Open GitHub Release ↗</a></div>`;
      } else {
        box.innerHTML = `<span>${escapeHtml(res.message)}</span> <a href="https://github.com/CellbineFiles/tcg-grading-vault/releases" target="_blank" rel="noopener noreferrer" style="color:var(--primary);margin-left:6px;">View Releases ↗</a>`;
      }
    } catch (err) {
      box.textContent = `Update check failed: ${err.message}`;
    } finally {
      btn.disabled = false;
    }
  });

  $("btn-bulk-pull-value")?.addEventListener("click", async () => {
    let ids = [...state.selectedCardIds];
    if (!ids.length) return showToast("Select at least one card first.");
    if (ids.length > 4) { showToast("Share Graphic supports up to 4 cards — using the first 4 selected."); ids = ids.slice(0, 4); }
    const btn = $("btn-bulk-pull-value");
    if (btn) btn.disabled = true;
    let ok = 0;
    try {
      for (let i = 0; i < ids.length; i++) {
        if (btn) btn.textContent = `💲 ${i + 1}/${ids.length}...`;
        try {
          const res = await apiJson(`/api/cards/${ids[i]}/pull-value`, { method: "POST" });
          if (res?.success) ok++;
        } catch (_) {}
      }
      showToast(`Pulled market values for ${ok}/${ids.length} selected cards.`);
      openSubmission(state.currentSubId, false, "preserve");
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.textContent = "💲 Pull Value";
      }
    }
  });

  $("btn-bulk-open-pc")?.addEventListener("click", () => {
    const ids = state.selectedCardIds;
    if (!ids.size) return showToast("Select at least one card first.");
    const cards = (state.currentSubCards || []).filter((c) => ids.has(c.id) && c.pricecharting_url);
    if (!cards.length) return showToast("None of the selected cards have a PriceCharting/TCGplayer link yet.");
    cards.forEach((c) => openExternalUrl(c.pricecharting_url));
  });

  $("btn-bulk-open-fanatics")?.addEventListener("click", () => {
    const ids = state.selectedCardIds;
    if (!ids.size) return showToast("Select at least one card first.");
    const cards = (state.currentSubCards || []).filter((c) => ids.has(c.id) && c.fanatics_url);
    if (!cards.length) return showToast("None of the selected cards have a Fanatics / listing link yet.");
    cards.forEach((c) => openExternalUrl(c.fanatics_url));
  });

  $("btn-bulk-showcase")?.addEventListener("click", () => {
    const ids = new Set(Array.from(state.selectedCardIds));
    const cards = (state.currentSubCards || []).filter((c) => ids.has(c.id));
    openStreamerShowcase(cards, `${state.currentSubData?.title || "Submission"} — Stream Showcase`);
  });
  $("btn-card-showcase")?.addEventListener("click", () => {
    if (state.currentCardData) openStreamerShowcase([state.currentCardData], state.currentCardData.card_name);
  });
  $("btn-bulk-share-graphic")?.addEventListener("click", () => {
    const ids = Array.from(state.selectedCardIds);
    if (!ids.length) { showToast("Select one or more cards first."); return; }
    openInfographicModal("", `${state.currentSubData?.title || "Selected"} (${ids.length} Cards)`, "selected_cards_showcase.png", ids);
  });

  $("btn-close-showcase")?.addEventListener("click", () => ($("showcase-modal").style.display = "none"));
  $("btn-showcase-prev")?.addEventListener("click", () => { if (showcaseIdx > 0) { showcaseIdx--; renderStreamerShowcase(); } });
  $("btn-showcase-next")?.addEventListener("click", () => { if (showcaseIdx < showcaseCards.length - 1) { showcaseIdx++; renderStreamerShowcase(); } });
  $("btn-showcase-flip")?.addEventListener("click", () => { showcaseBack = !showcaseBack; renderStreamerShowcase(); });
  $("btn-showcase-mode-toggle")?.addEventListener("click", () => { showcaseGridMode = !showcaseGridMode; renderStreamerShowcase(); });

  // Auctions view wiring
  $("btn-nav-auctions")?.addEventListener("click", () => openAuctionsView(true));
  $("btn-open-add-auctions")?.addEventListener("click", () => { $("add-auctions-urls").value = ""; $("add-auctions-modal").style.display = "flex"; });
  $("btn-close-add-auctions")?.addEventListener("click", () => ($("add-auctions-modal").style.display = "none"));
  $("btn-cancel-add-auctions")?.addEventListener("click", () => ($("add-auctions-modal").style.display = "none"));
  $("btn-submit-add-auctions")?.addEventListener("click", async () => {
    const urls = ($("add-auctions-urls").value || "").split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
    const cost = Number($("add-auctions-cost").value || 0);
    if (!urls.length) { showToast("Paste at least one auction URL."); return; }
    const btn = $("btn-submit-add-auctions");
    btn.disabled = true;
    try {
      const r = await apiJson("/api/auctions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ urls, cost }) });
      $("add-auctions-modal").style.display = "none";
      auctionsCache = await apiJson("/api/auctions");
      renderAuctionsGrid();
      showToast(`Added ${r.count} auction(s)!`);
    } catch (err) {
      showToast(err.message);
    } finally {
      btn.disabled = false;
    }
  });
  $("btn-refresh-all-auctions")?.addEventListener("click", async () => {
    const r = await apiJson("/api/auctions/refresh", { method: "POST" });
    auctionsCache = r.auctions || [];
    aucCountdown = 60;
    renderAuctionsGrid();
    showToast(`Refreshed ${r.updated || 0} auction(s)!`);
  });
  $("sub-card-size-slider")?.addEventListener("input", (e) => {
    const v = Number(e.target.value || 350);
    const listEl = $("detail-cards-list");
    if (listEl) {
      listEl.style.setProperty("--sub-card-w", `${v}px`);
      listEl.style.setProperty("--sub-scale", (v / 350).toFixed(3));
    }
  });
  $("auc-card-size-slider")?.addEventListener("input", (e) => {
    const v = Number(e.target.value || 320);
    const g = $("auctions-grid");
    if (g) {
      g.style.setProperty("--auc-card-w", `${v}px`);
      g.style.setProperty("--auc-scale", (v / 320).toFixed(3));
    }
  });
  $("btn-clear-all-auctions")?.addEventListener("click", async () => {
    if (!auctionsCache.length) return;
    if (!confirm(`Clear all ${auctionsCache.length} tracked auctions?`)) return;
    await apiJson("/api/auctions", { method: "DELETE" });
    auctionsCache = [];
    selectedAuctionId = null;

    renderAuctionsGrid();
    showToast("Cleared all tracked auctions.");
  });
  $("btn-close-auc-viewer")?.addEventListener("click", () => {
    selectedAuctionId = null;

    renderAuctionsGrid();
  });
  $("btn-auc-viewer-link-vault")?.addEventListener("click", () => { if (selectedAuctionId) openLinkAuctionModal(selectedAuctionId); });
  $("btn-close-link-auction")?.addEventListener("click", () => ($("link-auction-modal").style.display = "none"));
  $("btn-cancel-link-auction")?.addEventListener("click", () => ($("link-auction-modal").style.display = "none"));
  $("btn-confirm-link-auction")?.addEventListener("click", async () => {
    const cardId = Number($("link-auc-card-select").value || 0);
    if (!cardId || !linkTargetAuction) return;
    const fees = Number($("link-auc-fees").value || 0);
    const r = await apiJson(`/api/auctions/${linkTargetAuction.id}/link-card`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ card_id: cardId, selling_fees: fees }),
    });
    $("link-auction-modal").style.display = "none";
    auctionsCache = await apiJson("/api/auctions");
    renderAuctionsGrid();
    showToast(`Marked card Sold at ${fmtMoney(r.sold_price)}!`);
  });

$("btn-about-versions")?.addEventListener("click", () => {
    $("about-modal").style.display = "none";
    renderHelpTab("versions");
    $("help-modal").style.display = "flex";
  });
  document.querySelectorAll("#help-tabs .filter-pill").forEach((b) => {
    b.addEventListener("click", () => renderHelpTab(b.dataset.helpTab));
  });

  $("btn-open-settings").addEventListener("click", () => ($("settings-modal").style.display = "flex"));
  $("btn-close-settings").addEventListener("click", () => ($("settings-modal").style.display = "none"));
  document.querySelectorAll(".theme-option").forEach((btn) => {
    btn.addEventListener("click", () => {
      applyTheme(btn.dataset.themeChoice);
      $("settings-modal").style.display = "none";
      showToast(`Theme switched to ${btn.textContent.trim()}!`);
    });
  });

  // Clear default "0" on focus if user hasn't entered a non-zero number
  document.querySelectorAll(".cost-input").forEach((inp) => {
    inp.addEventListener("focus", () => {
      if (inp.value === "0" || inp.value === "0.00") {
        inp.value = "";
      }
    });
  });

  // Excel Import Modal
  let lastEbayItems = [];
  if ($("btn-open-ebay")) {
    $("btn-open-ebay").addEventListener("click", () => ($("ebay-modal").style.display = "flex"));
    $("btn-close-ebay").addEventListener("click", () => ($("ebay-modal").style.display = "none"));
    $("btn-run-ebay-import").addEventListener("click", async () => {
      const f = $("ebay-csv-file").files[0];
      if (!f) { showToast("Choose an eBay Paid & Shipped CSV first."); return; }
      const btn = $("btn-run-ebay-import");
      btn.disabled = true;
      btn.textContent = "Importing...";
      try {
        const fd = new FormData();
        fd.append("file", f);
        fd.append("match_cards", "true");
        const res = await apiJson("/api/ebay/import-sales", { method: "POST", body: fd });
        lastEbayItems = res.items || [];
        $("btn-download-ebay-bookkeeping").style.display = lastEbayItems.length ? "inline-flex" : "none";
        renderEbayPreviewBox($("ebay-import-summary"), res, () => {
          if (state.currentView === "view-home") loadHome(false, "preserve");
        });
      } catch (err) {
        showToast(`eBay import failed: ${err.message}`);
      } finally {
        if ($("ebay-csv-file")) $("ebay-csv-file").value = "";
        btn.disabled = false;
        btn.textContent = "🧾 Import & Match Sales";
      }
    });
    $("btn-download-ebay-bookkeeping").addEventListener("click", () => {
      if (!lastEbayItems.length) return;
      const hdr = ["Sale Date", "Order Number", "Item Number", "Item Title", "Qty", "Sold For", "Shipping Collected", "eBay Tax Collected"];
      const rows = lastEbayItems.map((x) => [
        x.sale_date, x.order_number, x.item_number, `"${(x.item_title || "").replace(/"/g, '""')}"`,
        x.quantity, x.sold_for.toFixed(2), x.shipping_collected.toFixed(2), x.ebay_tax.toFixed(2),
      ].join(","));
      const blob = new Blob([[hdr.join(","), ...rows].join("\n")], { type: "text/csv" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `ebay_bookkeeping_clean_${new Date().toISOString().slice(0, 10)}.csv`;
      a.click();
    });
  }

  $("btn-open-import").addEventListener("click", () => {
    $("import-excel-form").reset();
    $("import-modal").style.display = "flex";
  });
  $("btn-close-import").addEventListener("click", () => ($("import-modal").style.display = "none"));
  $("btn-cancel-import").addEventListener("click", () => ($("import-modal").style.display = "none"));

  $("import-excel-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const file = $("import-file").files[0];
    if (!file) return;
    const btn = $("btn-submit-import");
    btn.disabled = true;
    btn.textContent = "Importing...";
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("fanatics_company", $("import-fanatics-company").value);
      const res = await apiJson("/api/import-excel", { method: "POST", body: fd });
      $("import-modal").style.display = "none";
      showToast(res.message);
      loadHome();
    } catch (err) {
      showToast(`Import failed: ${err.message}`);
    } finally {
      btn.disabled = false;
      btn.textContent = "Import Submissions";
    }
  });

  // Company picker in wizard
  document.querySelectorAll(".company-option").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".company-option").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      $("sub-company").value = btn.dataset.company;
      clearMissingCompanyError();
    });
  });

  $("btn-wizard-cancel").addEventListener("click", () => {
    if (state.wizardSubId) openSubmission(state.wizardSubId);
    else loadHome();
  });

  async function runBatchCertPull(targets, btnEl, defaultLabel) {
    btnEl.disabled = true;
    const allHaveZip = targets.length > 0 && targets.every((c) => Boolean((c.psa_images_zip_url || "").trim()));
    const res = await runBatchWithModal({
      title: allHaveZip ? "Fast-Pulling PSA Zip Scans" : "Pulling Cert Scans",
      items: targets,
      cancelLabel: "✕ Stop Pulling",
      batchSize: allHaveZip ? 12 : 1,
      workerFn: async (c) => {
        try {
          const r = await apiJson(`/api/cards/${c.id}/fetch-cert`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              cert_number: c.cert_number,
              background: false,
              win_x: Math.round(window.screenX || 0),
              win_y: Math.round(window.screenY || 0),
              win_w: Math.round(window.outerWidth || window.innerWidth || 1200),
              win_h: Math.round(window.outerHeight || window.innerHeight || 800),
            }),
          });
          return Boolean(r.success);
        } catch {
          return false;
        }
      },
    });
    btnEl.disabled = false;
    btnEl.textContent = defaultLabel;
    showToast(res.cancelled
      ? `Stopped after ${res.done} cards (pulled ${res.successCount}).`
      : `Finished ${targets.length} cards: pulled HQ data/scans for ${res.successCount}!`);
    openSubmission(state.currentSubId, false, "preserve");
  }

  $("btn-force-repull-all-certs").addEventListener("click", async () => {
    if (!state.currentSubId) return;
    const allWithCert = (state.currentSubCards || []).filter((c) => Boolean((c.cert_number || "").trim()));
    if (!allWithCert.length) {
      showToast("No cards in this submission have a Cert # yet.");
      return;
    }
    if (!confirm(`Re-pull and overwrite cert scans & metadata for ALL ${allWithCert.length} cards with Cert #s? This may take a minute.`)) {
      return;
    }
    await runBatchCertPull(allWithCert, $("btn-force-repull-all-certs"), "🔁 Overwrite All Cert Scans");
  });

  if ($("btn-pull-all-values")) {
    $("btn-pull-all-values").addEventListener("click", async () => {
      const sub = state.currentSubData;
      if (!sub || !sub.cards || !sub.cards.length) {
        showToast("No cards in this submission yet.");
        return;
      }
      const targets = sub.cards;
      if (!confirm(`Pull fresh PriceCharting Market Values for all ${targets.length} card${targets.length === 1 ? "" : "s"} in this submission?\n\nWARNING: This will overwrite any existing Market Values on these cards.`)) {
        return;
      }
      const btn = $("btn-pull-all-values");
      const origTxt = btn.textContent;
      btn.disabled = true;
      const res = await runBatchWithModal({
        title: "Pulling Market Values",
        items: targets,
        cancelLabel: "✕ Stop Pulling",
        workerFn: async (c) => {
          try {
            const r = await apiJson(`/api/cards/${c.id}/fetch-value`, { method: "POST" });
            return Boolean(r.success && r.price != null);
          } catch {
            return false;
          }
        },
      });
      btn.disabled = false;
      btn.textContent = origTxt;
      showToast(res.cancelled
        ? `Stopped after ${res.done} cards (updated ${res.successCount}).`
        : `Updated Market Value for ${res.successCount} of ${targets.length} cards.`);
      openSubmission(state.currentSubId, false, "preserve");
    });
  }

  $("btn-pull-missing-certs").addEventListener("click", async () => {
    if (!state.currentSubId) return;
    let targets = (state.currentSubCards || []).filter(
      (c) => c.cert_number && (!c.cert_front_image || !c.card_year || !c.set_name || !c.card_number)
    );
    if (!targets.length) {
      const allWithCert = (state.currentSubCards || []).filter((c) => Boolean((c.cert_number || "").trim()));
      if (!allWithCert.length) {
        showToast("No cards in this submission have a Cert # yet.");
        return;
      }
      if (!confirm(`All ${allWithCert.length} cards already have scans. Re-pull and overwrite ALL existing cert scans with high-res versions?`)) {
        return;
      }
      targets = allWithCert;
    }
    await runBatchCertPull(targets, $("btn-pull-missing-certs"), "✨ Pull Missing Scans & Info");
  });

  // Home Submission Card Outline Colors Modal
  const syncColorDisableState = () => {
    const pairs = [
      ["color-sub-completed", "no-outline-completed"],
      ["color-sub-partial", "no-outline-partial"],
      ["color-sub-grading", "no-outline-grading"],
      ["color-sub-notshipped", "no-outline-notshipped"],
    ];
    pairs.forEach(([cId, cbId]) => {
      if ($(cId) && $(cbId)) {
        $(cId).disabled = $(cbId).checked;
        $(cId).style.opacity = $(cbId).checked ? "0.35" : "1";
      }
    });
  };
  ["no-outline-completed", "no-outline-partial", "no-outline-grading", "no-outline-notshipped"].forEach((id) => {
    if ($(id)) $(id).addEventListener("change", syncColorDisableState);
  });
  $("btn-sub-colors").addEventListener("click", () => {
    const c = getSubBorderColors();
    $("color-sub-completed").value = c.completed;
    $("color-sub-partial").value = c.partial;
    if ($("color-sub-grading")) $("color-sub-grading").value = c.inGrading;
    if ($("color-sub-notshipped")) $("color-sub-notshipped").value = c.notShipped;
    if ($("no-outline-completed")) $("no-outline-completed").checked = c.noCompleted;
    if ($("no-outline-partial")) $("no-outline-partial").checked = c.noPartial;
    if ($("no-outline-grading")) $("no-outline-grading").checked = c.noInGrading;
    if ($("no-outline-notshipped")) $("no-outline-notshipped").checked = c.noNotShipped;
    syncColorDisableState();
    $("sub-colors-modal").style.display = "flex";
  });
  $("btn-close-sub-colors").addEventListener("click", () => ($("sub-colors-modal").style.display = "none"));
  $("btn-save-sub-colors").addEventListener("click", () => {
    saveSubBorderColors({
      completed: $("color-sub-completed").value,
      partial: $("color-sub-partial").value,
      inGrading: $("color-sub-grading") ? $("color-sub-grading").value : "#3b82f6",
      notShipped: $("color-sub-notshipped") ? $("color-sub-notshipped").value : "#a855f7",
      noCompleted: Boolean($("no-outline-completed")?.checked),
      noPartial: Boolean($("no-outline-partial")?.checked),
      noInGrading: Boolean($("no-outline-grading")?.checked),
      noNotShipped: Boolean($("no-outline-notshipped")?.checked),
    });
    $("sub-colors-modal").style.display = "none";
    showToast("Submission card outline settings updated!");
    loadHome(false, "preserve");
  });
  $("btn-reset-sub-colors").addEventListener("click", () => {
    saveSubBorderColors({
      completed: "#10b981", partial: "#f59e0b", inGrading: "#3b82f6", notShipped: "#a855f7",
      noCompleted: false, noPartial: false, noInGrading: false, noNotShipped: false,
    });
    $("color-sub-completed").value = "#10b981";
    $("color-sub-partial").value = "#f59e0b";
    if ($("color-sub-grading")) $("color-sub-grading").value = "#3b82f6";
    if ($("color-sub-notshipped")) $("color-sub-notshipped").value = "#a855f7";
    ["no-outline-completed", "no-outline-partial", "no-outline-grading", "no-outline-notshipped"].forEach((id) => {
      if ($(id)) $(id).checked = false;
    });
    syncColorDisableState();
    $("sub-colors-modal").style.display = "none";
    showToast("Reset outline colors to defaults!");
    loadHome(false, "preserve");
  });

  if ($("btn-wiz-create-from-certs")) {
    let importCancelled = false;
    let activeImportSubId = null;
    if ($("btn-cancel-import-modal")) {
      $("btn-cancel-import-modal").addEventListener("click", async () => {
        importCancelled = true;
        $("btn-cancel-import-modal").disabled = true;
        $("imp-modal-status").textContent = "Cancelling & removing submission...";
        if (activeImportSubId) {
          try {
            await apiJson(`/api/submissions/${activeImportSubId}`, { method: "DELETE" });
          } catch (_) {}
          activeImportSubId = null;
        }
        $("import-progress-modal").style.display = "none";
        $("btn-cancel-import-modal").disabled = false;
        showToast("Import cancelled and submission removed.");
      });
    }

    $("btn-wiz-create-from-certs").addEventListener("click", async () => {
      const company = $("sub-company").value.trim();
      if (!company) {
        showMissingCompanyError();
        showToast("Please select a Grading Company (PSA, CGC, Beckett, or TAG) at the top first!");
        return;
      }
      const fileEl = $("wiz-cert-doc-file");
      const file = fileEl?.files[0];
      if (!file) {
        showToast("Choose a file (CSV, TXT, PDF, Excel, Word, or Screenshot) first.");
        return;
      }
      const btn = $("btn-wiz-create-from-certs");
      btn.disabled = true;
      btn.textContent = "Importing...";
      importCancelled = false;
      activeImportSubId = null;
      try {
        const fd = new FormData();
        fd.append("file", file);
        fd.append("title", $("sub-title").value.trim());
        fd.append("company", company);
        fd.append("submitted_date", $("sub-date").value);
        fd.append("not_shipped", $("sub-not-shipped")?.checked ? "true" : "false");
        const chChecked = Array.from(document.querySelectorAll(".wiz-channel-cb:checked")).map((cb) => cb.value);
        fd.append("submit_channel", chChecked.join(" / "));

        const initRes = await apiJson("/api/submissions/smart-import", { method: "POST", body: fd });
        activeImportSubId = initRes.submission_id;
        const createdCards = (initRes.cards || []).filter((c) => c.cert_number);
        const autoPull = Boolean($("wiz-auto-pull-certs")?.checked);
        let pulled = 0;

        if (autoPull && createdCards.length) {
          const allZip = createdCards.every((c) => Boolean((c.psa_images_zip_url || "").trim()));
          const bRes = await runBatchWithModal({
            title: `Importing ${initRes.format_detected}`,
            items: createdCards,
            cancelLabel: "✕ Cancel & Remove Sub",
            batchSize: allZip ? 12 : 1,
            workerFn: async (item) => {
              try {
                const cRes = await apiJson(`/api/cards/${item.id}/fetch-cert`, {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({
                    cert_number: item.cert_number,
                    background: false,
                    win_x: Math.round(window.screenX || 0),
                    win_y: Math.round(window.screenY || 0),
                    win_w: Math.round(window.outerWidth || window.innerWidth || 1200),
                    win_h: Math.round(window.outerHeight || window.innerHeight || 800),
                  }),
                });
                return Boolean(cRes.success || (cRes.card && cRes.card.card_name && !cRes.card.card_name.includes("Cert #")));
              } catch {
                return false;
              }
            },
            onCancel: async () => {
              importCancelled = true;
              if (activeImportSubId) {
                try { await apiJson(`/api/submissions/${activeImportSubId}`, { method: "DELETE" }); } catch (_) {}
                activeImportSubId = null;
              }
              showToast("Import cancelled and submission removed.");
            },
          });
          pulled = bRes.successCount;
          if (bRes.cancelled) importCancelled = true;
        }

        if (!importCancelled) {
          showToast(`Imported ${initRes.total} cards via ${initRes.format_detected}${autoPull ? ` (pulled ${pulled} scans)` : ""}.`);
          openSubmission(initRes.submission_id);
        }
      } catch (err) {
        if ($("import-progress-modal")) $("import-progress-modal").style.display = "none";
        showToast(`Import failed: ${err.message}`);
      } finally {
        if (fileEl) fileEl.value = "";
        btn.disabled = false;
        btn.textContent = "⚡ Import & Create Submission";
      }
    });
  }


  let lastWizEbayItems = [];
  if ($("btn-wiz-ebay-import")) {
    $("btn-wiz-ebay-import").addEventListener("click", async () => {
      const fEl = $("wiz-ebay-csv-file");
      const f = fEl?.files[0];
      if (!f) { showToast("Choose an eBay Paid & Shipped CSV first."); return; }
      const btn = $("btn-wiz-ebay-import");
      btn.disabled = true;
      btn.textContent = "Importing...";
      try {
        const fd = new FormData();
        fd.append("file", f);
        fd.append("match_cards", "true");
        const res = await apiJson("/api/ebay/import-sales", { method: "POST", body: fd });
        lastWizEbayItems = res.items || [];
        if ($("btn-wiz-ebay-bookkeeping")) $("btn-wiz-ebay-bookkeeping").style.display = lastWizEbayItems.length ? "inline-flex" : "none";
        openEbayReviewModal(res, (cRes) => {
          if (cRes.created_submission_id) openSubmission(cRes.created_submission_id);
          else loadHome(false, "preserve");
        });
      } catch (err) {
        showToast(`eBay import failed: ${err.message}`);
      } finally {
        if (fEl) fEl.value = "";
        btn.disabled = false;
        btn.textContent = "🧾 Import eBay Sales";
      }
    });
    if ($("btn-wiz-ebay-bookkeeping")) {
      $("btn-wiz-ebay-bookkeeping").addEventListener("click", () => downloadCleanEbayBookkeepingCsv(lastWizEbayItems));
    }
  }

  $("wizard-step-1").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!$("sub-company").value.trim()) {
      showMissingCompanyError();
      showToast("Please select a Grading Company (PSA, CGC, or Beckett) at the top first!");
      return;
    }
    const chChecked = Array.from(document.querySelectorAll(".wiz-channel-cb:checked")).map((cb) => cb.value);
    const payload = {
      company: $("sub-company").value,
      title: $("sub-title").value.trim(),
      submission_number: $("sub-number").value.trim(),
      submitted_date: $("sub-date").value,
      status: $("sub-not-shipped")?.checked ? "Not Shipped" : "In Grading",
      submit_channel: chChecked.join(" / "),
      notes: $("sub-notes").value.trim(),
    };
    const sub = await apiJson("/api/submissions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    state.wizardSubId = sub.id;
    state.wizardCardsAdded = 0;
    $("wizard-step-1").style.display = "none";
    $("wizard-step-2").style.display = "block";
    $("wizard-subtitle").textContent = "Step 2: Add TCG cards and photos to your submission";
    $("wizard-sub-badge").textContent = sub.company;
    $("wizard-sub-badge").className = `badge badge-${sub.company}`;
    $("wizard-sub-name").textContent = sub.title;
    $("wizard-cards-added-pill").textContent = "0 cards added";
    $("btn-wiz-finish-only").style.display = "none";
    resetWizardCardForm();
  });

  bindImagePreview("wiz-raw-front", "wiz-preview-front");
  bindImagePreview("wiz-raw-back", "wiz-preview-back");

  $("wizard-card-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const cname = $("wiz-card-name").value.trim();
    const flink = $("wiz-fanatics-url").value.trim();
    const cnum = $("wiz-cert-num").value.trim();
    if (!cname && !flink && !cnum) {
      showToast("Enter a Card Name, Graded Card Link, or Cert #.");
      $("wiz-card-name").focus();
      return;
    }
    const action = e.submitter?.dataset?.action || "finish";
    const fd = new FormData();
    fd.append("card_name", $("wiz-card-name").value.trim());
    fd.append("card_year", $("wiz-card-year").value.trim());
    fd.append("set_name", $("wiz-set-name").value.trim());
    fd.append("card_number", $("wiz-card-number").value.trim());
    fd.append("fanatics_url", $("wiz-fanatics-url").value.trim());
    fd.append("acquisition_cost", $("wiz-acq-cost").value || "0");
    fd.append("grading_cost", $("wiz-grade-cost").value || "0");
    fd.append("shipping_cost", $("wiz-ship-cost").value || "0");
    fd.append("cert_number", $("wiz-cert-num").value.trim());
    if ($("wiz-pred-overall")) fd.append("pred_overall", $("wiz-pred-overall").value.trim());
    fd.append("pred_corners", $("wiz-pred-corners").value);
    fd.append("pred_edges", $("wiz-pred-edges").value);
    fd.append("pred_centering", $("wiz-pred-centering").value);
    fd.append("pred_surface", $("wiz-pred-surface").value);
    fd.append("notes", $("wiz-card-notes").value.trim());
    const qty = Math.max(1, Math.min(200, parseInt($("wiz-card-qty")?.value || "1", 10) || 1));
    fd.append("quantity", String(qty));

    const frontFile = $("wiz-raw-front").files[0];
    const backFile = $("wiz-raw-back").files[0];
    if (frontFile) fd.append("raw_front", frontFile);
    if (backFile) fd.append("raw_back", backFile);

    await apiJson(`/api/submissions/${state.wizardSubId}/cards`, {
      method: "POST",
      body: fd,
    });

    state.wizardCardsAdded += qty;
    $("wizard-cards-added-pill").textContent = `${state.wizardCardsAdded} card${state.wizardCardsAdded === 1 ? "" : "s"} added`;
    $("btn-wiz-finish-only").style.display = "inline-flex";
    showToast(qty > 1 ? `Added ${qty} copies to submission!` : "Card saved to submission!");

    if (action === "add-more") {
      resetWizardCardForm();
      $("wiz-card-name").focus();
    } else {
      openSubmission(state.wizardSubId);
    }
  });

  $("btn-wiz-finish-only").addEventListener("click", () => {
    openSubmission(state.wizardSubId);
  });

  $("detail-sub-status").addEventListener("change", async (e) => {
    await apiJson(`/api/submissions/${state.currentSubId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: e.target.value }),
    });
    showToast("Submission status updated.");
  });

  $("btn-sub-add-card").addEventListener("click", async () => {
    const sub = await apiJson(`/api/submissions/${state.currentSubId}`);
    state.wizardSubId = sub.id;
    state.wizardCardsAdded = sub.cards.length;
    $("wizard-title").textContent = "Add Cards to Submission";
    $("wizard-subtitle").textContent = "Add another TCG card to this submission";
    $("wizard-step-1").style.display = "none";
    $("wizard-step-2").style.display = "block";
    $("wizard-sub-badge").textContent = sub.company;
    $("wizard-sub-badge").className = `badge badge-${sub.company}`;
    $("wizard-sub-name").textContent = sub.title;
    $("wizard-cards-added-pill").textContent = `${sub.cards.length} cards in sub`;
    $("btn-wiz-finish-only").style.display = "inline-flex";
    resetWizardCardForm();
    switchView("view-wizard");
  });

  $("btn-sub-delete").addEventListener("click", async () => {
    if (!confirm("Delete this submission and all its cards/photos?")) return;
    await apiJson(`/api/submissions/${state.currentSubId}`, { method: "DELETE" });
    showToast("Submission deleted.");
    loadHome();
  });

  $("btn-card-back").addEventListener("click", async () => {
    await saveCurrentCardChanges(true);
    showToast("Card saved.");
    openSubmission(state.currentSubId, true, "restore");
  });

  $("btn-card-delete").addEventListener("click", async () => {
    if (!confirm("Delete this card?")) return;
    await apiJson(`/api/cards/${state.currentCardId}`, { method: "DELETE" });
    showToast("Card deleted.");
    openSubmission(state.currentSubId);
  });

  ["edit-acq-cost", "edit-grade-cost", "edit-ship-cost", "edit-sold-price", "edit-selling-fees", "edit-initial-graded-value", "edit-market-value"].forEach((id) => {
    $(id).addEventListener("input", updateComputedCardMetrics);
  });

  if ($("edit-pc-url")) {
    $("edit-pc-url").addEventListener("input", (e) => {
      const v = (e.target.value || "").trim();
      const lnk = $("link-pc-url");
      if (!lnk) return;
      if (v.startsWith("http://") || v.startsWith("https://")) {
        lnk.href = v;
        lnk.textContent = v.toLowerCase().includes("tcgplayer") ? "TCGplayer ↗" : "PriceCharting ↗";
        lnk.style.display = "inline-flex";
      } else {
        lnk.style.display = "none";
      }
    });
  }
  if ($("edit-status")) {
    $("edit-status").addEventListener("change", (e) => {
      if (e.target.value !== "Sold" && $("edit-sold-price").value !== "") {
        $("edit-sold-price").value = "";
        updateComputedCardMetrics();
      }
    });
  }
  $("edit-fanatics-url").addEventListener("input", (e) => {
    const val = e.target.value.trim();
    const link = $("link-fanatics");
    if (val) {
      link.href = val;
      link.textContent = detectLinkLabel(val);
      link.style.display = "inline-flex";
    } else {
      link.style.display = "none";
    }
    autoPromoteStatusIfGraded();
  });

  $("sub-card-search").addEventListener("input", applySubCardFilterAndSort);
  $("sub-card-sort").addEventListener("change", applySubCardFilterAndSort);

  $("btn-edit-sub-info").addEventListener("click", () => {
    const sub = state.currentSubData;
    if (!sub) return;
    $("edit-sub-title-input").value = sub.title || "";
    $("edit-sub-company-select").value = sub.company || "PSA";
    $("edit-sub-date-input").value = sub.submitted_date || "";
    $("edit-sub-number-input").value = sub.submission_number || "";
    $("edit-sub-status-select").value = sub.status || "In Grading";
    const chVal = (sub.submit_channel || "").toLowerCase();
    if ($("edit-sub-ch-fanatics")) $("edit-sub-ch-fanatics").checked = chVal.includes("fanatics");
    if ($("edit-sub-ch-gamestop")) $("edit-sub-ch-gamestop").checked = chVal.includes("gamestop");
    $("edit-sub-notes-input").value = sub.notes || "";
    $("edit-sub-modal").style.display = "flex";
  });
  document.querySelectorAll(".wiz-channel-cb").forEach((cb) => {
    cb.addEventListener("change", () => {
      if (cb.checked) document.querySelectorAll(".wiz-channel-cb").forEach((o) => { if (o !== cb) o.checked = false; });
    });
  });
  document.querySelectorAll(".edit-channel-cb").forEach((cb) => {
    cb.addEventListener("change", () => {
      if (cb.checked) document.querySelectorAll(".edit-channel-cb").forEach((o) => { if (o !== cb) o.checked = false; });
    });
  });
  $("btn-close-edit-sub").addEventListener("click", () => ($("edit-sub-modal").style.display = "none"));
  $("btn-cancel-edit-sub").addEventListener("click", () => ($("edit-sub-modal").style.display = "none"));
  $("edit-sub-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!state.currentSubId) return;
    const editCh = Array.from(document.querySelectorAll(".edit-channel-cb:checked")).map((cb) => cb.value).join(" / ");
    await apiJson(`/api/submissions/${state.currentSubId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: $("edit-sub-title-input").value.trim(),
        company: $("edit-sub-company-select").value,
        submitted_date: $("edit-sub-date-input").value,
        submission_number: $("edit-sub-number-input").value.trim(),
        status: $("edit-sub-status-select").value,
        submit_channel: editCh,
        notes: $("edit-sub-notes-input").value.trim(),
      }),
    });
    $("edit-sub-modal").style.display = "none";
    showToast("Submission details updated!");
    openSubmission(state.currentSubId, false, "preserve");
  });

  $("btn-edit-sub-title").addEventListener("click", () => {
    if (!state.currentSubId) return;
    const titleEl = $("detail-sub-title");
    const curTitle = titleEl.textContent || "";
    activateInlineRename(titleEl, curTitle, async (newTitle) => {
      await apiJson(`/api/submissions/${state.currentSubId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: newTitle }),
      });
      showToast("Submission renamed!");
    });
  });

  document.querySelectorAll("#sub-filter-pills .filter-pill").forEach((pill) => {
    pill.addEventListener("click", () => {
      state.subFilterMode = pill.dataset.filter || "all";
      document.querySelectorAll("#sub-filter-pills .filter-pill").forEach((p) => p.classList.toggle("active", p === pill));
      applySubCardFilterAndSort();
    });
  });

  $("btn-toggle-bulk-mode").addEventListener("click", () => {
    state.bulkSelectMode = !state.bulkSelectMode;
    if (!state.bulkSelectMode) state.selectedCardIds.clear();
    updateBulkSelectionBar();
    applySubCardFilterAndSort();
  });

  $("btn-bulk-select-all").addEventListener("click", () => {
    document.querySelectorAll(".card-select-cb").forEach((cb) => {
      const cid = Number(cb.dataset.cardId);
      cb.checked = true;
      state.selectedCardIds.add(cid);
    });
    updateBulkSelectionBar();
  });

  $("btn-bulk-clear").addEventListener("click", () => {
    state.bulkSelectMode = false;
    state.selectedCardIds.clear();
    updateBulkSelectionBar();
    applySubCardFilterAndSort();
  });

  if ($("btn-bulk-edit")) {
    $("btn-bulk-edit").addEventListener("click", () => {
      const ids = Array.from(state.selectedCardIds);
      if (!ids.length) return;
      $("bulk-edit-title").textContent = `Bulk Edit ${ids.length} Selected Card${ids.length === 1 ? "" : "s"}`;
      $("bulk-edit-set").value = "";
      $("bulk-edit-acq").value = "";
      $("bulk-edit-grade").value = "";
      $("bulk-edit-ship").value = "";
      $("bulk-edit-curated").value = "";
      $("bulk-edit-modal").style.display = "flex";
    });
    $("btn-close-bulk-edit").addEventListener("click", () => ($("bulk-edit-modal").style.display = "none"));
    $("btn-cancel-bulk-edit").addEventListener("click", () => ($("bulk-edit-modal").style.display = "none"));
    $("bulk-edit-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const ids = Array.from(state.selectedCardIds);
      if (!ids.length) return;
      const payload = { card_ids: ids };
      const setVal = $("bulk-edit-set").value.trim();
      const acqVal = $("bulk-edit-acq").value.trim();
      const grdVal = $("bulk-edit-grade").value.trim();
      const shpVal = $("bulk-edit-ship").value.trim();
      const curVal = $("bulk-edit-curated").value.trim();
      if (setVal !== "") payload.set_name = setVal;
      if (acqVal !== "") payload.acquisition_cost = Number(acqVal);
      if (grdVal !== "") payload.grading_cost = Number(grdVal);
      if (shpVal !== "") payload.shipping_cost = Number(shpVal);
      if (curVal !== "") payload.curated_date = curVal;
      const res = await apiJson("/api/cards/bulk-edit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      $("bulk-edit-modal").style.display = "none";
      state.selectedCardIds.clear();
      showToast(`Updated ${res.updated_count} card${res.updated_count === 1 ? "" : "s"}!`);
      openSubmission(state.currentSubId, false, "preserve");
    });
  }

  if ($("btn-bulk-status")) {
    $("btn-bulk-status").addEventListener("click", async () => {
      const ids = Array.from(state.selectedCardIds);
      if (!ids.length) return;
      const newSt = $("bulk-status-select")?.value || "Not Submitted";
      const res = await apiJson("/api/cards/bulk-status", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ card_ids: ids, status: newSt }),
      });
      state.selectedCardIds.clear();
      showToast(`Updated ${res.updated_count} card${res.updated_count === 1 ? "" : "s"} to "${newSt}"!`);
      openSubmission(state.currentSubId, false, "preserve");
    });
  }

  $("btn-bulk-duplicate").addEventListener("click", async () => {
    const ids = Array.from(state.selectedCardIds);
    if (!ids.length) return;
    const res = await apiJson("/api/cards/bulk-duplicate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ card_ids: ids, copies: 1 }),
    });
    state.selectedCardIds.clear();
    showToast(`Duplicated ${res.duplicated_count} card${res.duplicated_count === 1 ? "" : "s"}!`);
    openSubmission(state.currentSubId, false, "preserve");
  });

  $("btn-bulk-move").addEventListener("click", () => {
    const ids = Array.from(state.selectedCardIds);
    if (ids.length) openMoveCardModal(ids);
  });

  $("btn-bulk-delete").addEventListener("click", async () => {
    const ids = Array.from(state.selectedCardIds);
    if (!ids.length) return;
    if (!confirm(`Delete ${ids.length} selected card${ids.length === 1 ? "" : "s"}?`)) return;
    const res = await apiJson("/api/cards/bulk-delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ card_ids: ids }),
    });
    state.selectedCardIds.clear();
    showToast(`Deleted ${res.deleted_count} cards.`);
    openSubmission(state.currentSubId, false, "preserve");
  });

  $("btn-close-move-modal").addEventListener("click", () => ($("move-card-modal").style.display = "none"));
  $("btn-cancel-move-modal").addEventListener("click", () => ($("move-card-modal").style.display = "none"));
  $("btn-confirm-move-modal").addEventListener("click", async () => {
    const targetId = Number($("move-modal-select").value || 0);
    if (!targetId || !state.moveTargetIds.length) return;
    const res = await apiJson("/api/cards/bulk-move", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ card_ids: state.moveTargetIds, target_submission_id: targetId }),
    });
    $("move-card-modal").style.display = "none";
    state.selectedCardIds.clear();
    showToast(`Moved ${res.moved_count} card${res.moved_count === 1 ? "" : "s"} to ${res.target_submission?.title || "new submission"}!`);
    openSubmission(state.currentSubId, false, "preserve");
  });

  $("btn-pull-from-link").addEventListener("click", () => {
    pullAllDataFromLink($("edit-fanatics-url").value);
  });
  $("btn-site-login").addEventListener("click", async () => {
    const targetUrl = $("edit-fanatics-url").value.trim() || "https://www.fanaticscollect.com/";
    try {
      const res = await apiJson("/api/open-browser-session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          url: targetUrl,
          win_x: Math.round(window.screenX || 0),
          win_y: Math.round(window.screenY || 0),
          win_w: Math.round(window.outerWidth || window.innerWidth || 1200),
          win_h: Math.round(window.outerHeight || window.innerHeight || 800),
        }),
      });
      $("cert-fetch-status").textContent = res.message;
      showToast("Log into Fanatics, then close that browser window.");
    } catch (err) {
      showToast(err.message);
    }
  });
  $("edit-fanatics-url").addEventListener("change", (e) => {
    pullAllDataFromLink(e.target.value);
  });
  $("edit-fanatics-url").addEventListener("paste", () => {
    setTimeout(() => pullAllDataFromLink($("edit-fanatics-url").value), 80);
  });
  $("wiz-fanatics-url").addEventListener("change", (e) => {
    autoExtractFromGradedLink(e.target.value, true);
  });
  $("wiz-fanatics-url").addEventListener("paste", () => {
    setTimeout(() => autoExtractFromGradedLink($("wiz-fanatics-url").value, true), 60);
  });

  $("edit-cert-number").addEventListener("input", autoPromoteStatusIfGraded);
  $("edit-actual-grade").addEventListener("input", autoPromoteStatusIfGraded);

  $("btn-pull-cert").addEventListener("click", () => {
    const certNum = $("edit-cert-number").value.trim();
    if (!certNum) {
      showToast("Enter a cert number first.");
      return;
    }
    pullCertScansForCurrentCard(certNum, false);
  });

  $("btn-pull-value").addEventListener("click", async () => {
    const mktStatus = $("market-fetch-status") || $("cert-fetch-status");
    mktStatus.textContent = "Searching PriceCharting for current graded value...";
    try {
      const res = await apiJson(`/api/cards/${state.currentCardId}/fetch-value`, { method: "POST" });
      mktStatus.innerHTML = res.source_url
        ? `${escapeHtml(res.message)} <a href="${escapeHtml(res.source_url)}" target="_blank" rel="noopener noreferrer" style="color:var(--primary)">View on PriceCharting ↗</a>`
        : escapeHtml(res.message);
      if (res.success && (res.price != null || (res.card && res.card.set_name))) {
        captureCardFormSnapshot();
        $("btn-undo-autofill").style.display = "inline-flex";
      }
      if (res.card) {
        state.currentCardData = res.card;
        if (res.card.set_name) $("edit-set-name").value = res.card.set_name;
        if (res.card.card_number) $("edit-card-number").value = res.card.card_number;
        if (res.card.card_year) $("edit-card-year").value = res.card.card_year;
      }
      if (res.success && res.price != null) {
        $("edit-market-value").value = res.price;
        updateComputedCardMetrics();
        showToast(`Market value updated: ${fmtMoney(res.price)}`);
      } else {
        showToast("Check PriceCharting link or enter value manually.");
      }
    } catch (err) {
      const mktStatus = $("market-fetch-status") || $("cert-fetch-status");
      mktStatus.textContent = err.message;
    }
  });

  $("btn-move-card").addEventListener("click", async () => {
    const targetId = Number($("move-card-sub-select").value || 0);
    if (!targetId || !state.currentCardId) {
      showToast("Choose a destination submission first.");
      return;
    }
    const res = await apiJson(`/api/cards/${state.currentCardId}/move`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target_submission_id: targetId }),
    });
    if (res.card) {
      state.currentSubId = res.card.submission_id;
      $("card-detail-heading").textContent = `${res.card.card_name} (${res.card.submission_company} • ${res.card.submission_title})`;
      populateMoveSubmissionDropdown(res.card.submission_id);
      showToast(`Moved card to ${res.target_submission?.title || "new submission"}!`);
    }
  });

  $("btn-pull-pc-url").addEventListener("click", async () => {
    const pcUrl = $("edit-pc-url").value.trim();
    if (!pcUrl || !state.currentCardId) {
      showToast("Paste a PriceCharting URL first.");
      return;
    }
    const mktStatus = $("market-fetch-status") || $("cert-fetch-status");
    mktStatus.textContent = "Pulling card details & price from PriceCharting link...";
    try {
      captureCardFormSnapshot();
      $("btn-undo-autofill").style.display = "inline-flex";
      const res = await apiJson(`/api/cards/${state.currentCardId}/fetch-value-url`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: pcUrl }),
      });
      mktStatus.innerHTML = res.source_url
        ? `${escapeHtml(res.message)} <a href="${escapeHtml(res.source_url)}" target="_blank" rel="noopener noreferrer" style="color:var(--primary)">View on PriceCharting ↗</a>`
        : escapeHtml(res.message);
      if (res.card) {
        state.currentCardData = res.card;
        if (res.card.card_name) $("edit-card-name").value = res.card.card_name;
        if (res.card.card_number) $("edit-card-number").value = res.card.card_number;
        if (res.card.set_name) $("edit-set-name").value = res.card.set_name;
        if (res.card.market_value != null) setCostInputValue($("edit-market-value"), res.card.market_value);
        if (res.card.pricecharting_url && $("edit-pc-url")) $("edit-pc-url").value = res.card.pricecharting_url;
        if ($("link-pc-url") && res.source_url) {
          $("link-pc-url").href = res.source_url;
          $("link-pc-url").textContent = res.source_url.toLowerCase().includes("tcgplayer") ? "TCGplayer ↗" : "PriceCharting ↗";
          $("link-pc-url").style.display = "inline-flex";
        }
        updateComputedCardMetrics();
      }
      showToast(res.message || "Updated card details from pricing link!");
    } catch (err) {
      mktStatus.textContent = err.message;
      showToast(err.message);
    }
  });

  $("btn-undo-autofill").addEventListener("click", async () => {
    const snap = state.lastCardSnapshot;
    if (!snap) return;
    $("edit-card-name").value = snap.card_name;
    $("edit-card-year").value = snap.card_year;
    $("edit-set-name").value = snap.set_name;
    $("edit-card-number").value = snap.card_number;
    $("edit-actual-grade").value = snap.actual_grade;
    $("edit-market-value").value = snap.market_value;
    $("edit-status").value = snap.status;
    updateComputedCardMetrics();
    await saveCurrentCardChanges(true);
    state.lastCardSnapshot = null;
    $("btn-undo-autofill").style.display = "none";
    $("cert-fetch-status").textContent = "Reverted last auto-filled changes.";
    showToast("Reverted auto-filled changes!");
  });

  // Ctrl+V Paste image directly onto Cert Front / Cert Back slots
  document.querySelectorAll(".paste-target").forEach((slot) => {
    slot.addEventListener("focus", () => (state.activePasteSide = slot.dataset.side));
    slot.addEventListener("click", () => (state.activePasteSide = slot.dataset.side));
  });

  document.addEventListener("paste", async (e) => {
    if (!$("view-card").classList.contains("active") || !state.activePasteSide || !state.currentCardId) return;
    const items = e.clipboardData?.items || [];
    for (const item of items) {
      if (item.type.startsWith("image/")) {
        const blob = item.getAsFile();
        if (!blob) continue;
        await uploadDroppedCardScan(state.activePasteSide, blob);
        break;
      }
    }
  });

  async function saveCurrentCardChanges(silent = false) {
    if (!state.currentCardId || state.currentView !== "view-card") return;
    const cardName = $("edit-card-name").value.trim();
    if (!cardName) return;
    const fd = new FormData();
    fd.append("card_name", cardName);
    fd.append("card_year", $("edit-card-year").value.trim());
    fd.append("set_name", $("edit-set-name").value.trim());
    fd.append("card_number", $("edit-card-number").value.trim());
    fd.append("status", $("edit-status").value);
    fd.append("acquisition_cost", $("edit-acq-cost").value || "0");
    fd.append("grading_cost", $("edit-grade-cost").value || "0");
    fd.append("shipping_cost", $("edit-ship-cost").value || "0");
    if ($("edit-pred-overall")) fd.append("pred_overall", $("edit-pred-overall").value.trim());
    fd.append("pred_corners", $("edit-pred-corners").value);
    fd.append("pred_edges", $("edit-pred-edges").value);
    fd.append("pred_centering", $("edit-pred-centering").value);
    fd.append("pred_surface", $("edit-pred-surface").value);
    fd.append("cert_number", $("edit-cert-number").value.trim());
    fd.append("actual_grade", $("edit-actual-grade").value.trim());
    fd.append("actual_subgrades", $("edit-actual-subgrades").value.trim());
    fd.append("initial_graded_value", $("edit-initial-graded-value").value);
    fd.append("market_value", $("edit-market-value").value);
    fd.append("fanatics_url", $("edit-fanatics-url").value.trim());
    if ($("edit-pc-url")) fd.append("pricecharting_url", $("edit-pc-url").value.trim());
    fd.append("sold_price", $("edit-sold-price").value);
    fd.append("selling_fees", $("edit-selling-fees").value || "0");
    fd.append("sold_date", $("edit-sold-date").value);
    if ($("edit-curated-date")) fd.append("curated_date", $("edit-curated-date").value.trim());
    fd.append("notes", $("edit-notes").value.trim());



    await apiJson(`/api/cards/${state.currentCardId}`, {
      method: "PUT",
      body: fd,
    });
    if (!silent) showToast("Card updated!");
  }

  $("edit-card-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    await saveCurrentCardChanges(false);
    openCardDetail(state.currentCardId, false);
  });

  $("img-raw-front").addEventListener("click", () => openCardSpinLightbox("raw", "front"));
  $("img-raw-back").addEventListener("click", () => openCardSpinLightbox("raw", "back"));
  $("img-cert-front").addEventListener("click", () => openCardSpinLightbox("cert", "front"));
  $("img-cert-back").addEventListener("click", () => openCardSpinLightbox("cert", "back"));

  ["wiz-raw-front", "wiz-raw-back"].forEach((id) => {
    $(id).addEventListener("change", (e) => {
      if (e.target.files && e.target.files.length >= 2) {
        classifyAndAssignWizardRawPair(e.target.files[0], e.target.files[1]);
      }
    });
  });

  $("btn-swap-raw").addEventListener("click", async () => {
    if (!state.currentCardId) return;
    const res = await apiJson(`/api/cards/${state.currentCardId}/swap-raw`, { method: "POST" });
    if (res.card) {
      state.currentCardData = res.card;
      renderAllCardPhotos(res.card);
      showToast("Swapped Raw Front & Back!");
    }
  });

  document.querySelectorAll(".btn-slot-link").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const relPath = getCardImageBySide(state.currentCardData, btn.dataset.side);
      const mode = btn.dataset.action === "open-file" ? "file" : "folder";
      openLocalPath(relPath, mode);
    });
  });

  $("btn-spin-card").addEventListener("click", toggleLightboxSpin);
  $("btn-zoom-in").addEventListener("click", () => setLightboxZoom(state.lightboxZoom + 0.5));
  $("btn-zoom-out").addEventListener("click", () => setLightboxZoom(state.lightboxZoom - 0.5));
  $("btn-zoom-reset").addEventListener("click", resetLightboxZoom);
  $("btn-lightbox-open-file").addEventListener("click", () => openLocalPath(getCurrentLightboxPath(), "file"));
  $("btn-lightbox-open-folder").addEventListener("click", () => openLocalPath(getCurrentLightboxPath(), "folder"));

  const scene = $("card-3d-scene");
  scene.addEventListener("wheel", (e) => {
    if ($("lightbox").style.display !== "flex") return;
    e.preventDefault();
    const delta = e.deltaY < 0 ? 0.25 : -0.25;
    setLightboxZoom(state.lightboxZoom + delta);
  }, { passive: false });

  scene.addEventListener("mousedown", (e) => {
    if (state.lightboxZoom <= 1) return;
    state.isPanning = true;
    state.panMoved = false;
    state.panStartX = e.clientX - state.lightboxPanX;
    state.panStartY = e.clientY - state.lightboxPanY;
    applyLightboxZoomTransform();
  });

  window.addEventListener("mousemove", (e) => {
    if (!state.isPanning) return;
    state.panMoved = true;
    state.lightboxPanX = e.clientX - state.panStartX;
    state.lightboxPanY = e.clientY - state.panStartY;
    applyLightboxZoomTransform();
  });

  window.addEventListener("mouseup", () => {
    if (state.isPanning) {
      state.isPanning = false;
      applyLightboxZoomTransform();
    }
  });

  scene.addEventListener("click", () => {
    if (state.lightboxZoom === 1 && !state.panMoved) {
      toggleLightboxSpin();
    }
    state.panMoved = false;
  });
  $("lightbox-close").addEventListener("click", () => ($("lightbox").style.display = "none"));
  $("lightbox").addEventListener("click", (e) => {
    if (e.target.id === "lightbox") $("lightbox").style.display = "none";
  });
  document.addEventListener("keydown", (e) => {
    if ($("lightbox").style.display === "flex") {
      if (e.code === "Space" || e.code === "ArrowLeft" || e.code === "ArrowRight") {
        e.preventDefault();
        toggleLightboxSpin();
      } else if (e.code === "Escape") {
        $("lightbox").style.display = "none";
      }
    }
  });

  const navigateFromStateOrHash = (st) => {
    let target = st;
    if (!target || !target.view) {
      const h = (window.location.hash || "").toLowerCase();
      const mSub = h.match(/^#submission\/(\d+)/);
      const mCard = h.match(/^#card\/(\d+)/);
      if (mSub) target = { view: "submission", subId: Number(mSub[1]) };
      else if (mCard) target = { view: "card", cardId: Number(mCard[1]) };
      else if (h === "#all-cards") target = { view: "all-cards" };
      else if (h === "#auctions") target = { view: "auctions" };
      else if (h === "#wizard") target = { view: "wizard" };
      else target = { view: "home" };
    }
    if (target.view === "submission" && target.subId) {
      openSubmission(target.subId, false, "restore");
    } else if (target.view === "card" && target.cardId) {
      openCardDetail(target.cardId, false);
    } else if (target.view === "all-cards") {
      openAllCardsView(false, "restore");
    } else if (target.view === "auctions") {
      openAuctionsView(false);
    } else if (target.view === "wizard") {
      startNewSubmissionWizard(false);
    } else {
      loadHome(false, "restore");
    }
  };

  window.addEventListener("popstate", async (e) => {
    if (state.currentView === "view-card") {
      await saveCurrentCardChanges(true);
      showToast("Card saved.");
    }
    navigateFromStateOrHash(e.state);
  });

  if (!history.state) {
    const h = (window.location.hash || "").toLowerCase();
    if (!h || h === "#home") {
      history.replaceState({ view: "home", hash: "#home" }, "", "#home");
    }
  }
  applyViewPrefs();
  const toggleGlobalView = () => {
    const cur = localStorage.getItem("tcg_view_mode") || "cards";
    localStorage.setItem("tcg_view_mode", cur === "list" ? "cards" : "list");
    applyViewPrefs();
    if (state.currentView === "view-home") loadHome(false, "preserve");
  };
  if ($("btn-refresh-stats")) {
    $("btn-refresh-stats").addEventListener("click", async () => {
      await loadHome(false, "preserve");
      showToast("Home stats recalculated!");
    });
  }
  if ($("btn-home-view-mode")) $("btn-home-view-mode").addEventListener("click", toggleGlobalView);
  if ($("btn-sub-view-mode")) $("btn-sub-view-mode").addEventListener("click", toggleGlobalView);
  document.querySelectorAll(".size-btn").forEach((b) => {
    b.addEventListener("click", () => {
      const key = b.dataset.scope === "home" ? "tcg_home_card_size" : "tcg_sub_card_size";
      localStorage.setItem(key, b.dataset.size);
      applyViewPrefs();
    });
  });
  const initHash = (window.location.hash || "").toLowerCase();
  if (initHash && initHash !== "#home") {
    navigateFromStateOrHash(null);
  } else {
    loadHome(false);
  }
});


const CURRENT_APP_VERSION = "0.91";
const CURRENT_UPDATE_BULLETS = [
  "Redesigned Multi-Card Share Graphic (up to 4 cards) with bold, readable stats and zero wasted margin.",
  "Home Card View now lets you scroll left & right through all card photos just like List View.",
  "Redesigned Submission List View with large slab scans and compact, aligned Market Value & Profit info.",
  "Locked horizontal page width so the app never scrolls left or right.",
  "External links open in your system default browser while in-app popups keep navigation inside the popup.",
];

async function openExternalUrl(url) {
  if (!url || !url.startsWith("http")) return;
  try {
    await apiJson("/api/open-external", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
  } catch (_) {
    window.open(url, "_blank", "noopener,noreferrer");
  }
}



window.addEventListener("message", (ev) => {
  if (ev?.data?.type === "inapp-nav" && ev.data.url && activeInAppTabIdx >= 0 && inAppTabs[activeInAppTabIdx]) {
    inAppTabs[activeInAppTabIdx].url = ev.data.url;
    const popout = $("inapp-popout-link");
    if (popout) popout.href = ev.data.url;
  }
});

function maybeShowWelcomeUpdateModal() {
  if (localStorage.getItem("tcg_vault_welcome_disabled") === "1") return;
  if (localStorage.getItem("tcg_vault_last_seen_ver") === CURRENT_APP_VERSION) return;
  const modal = $("welcome-update-modal");
  const list = $("welcome-update-bullets");
  const cb = $("welcome-disable-cb");
  if (!modal || !list) return;
  list.innerHTML = CURRENT_UPDATE_BULLETS.map((b) => `<li>${escapeHtml(b)}</li>`).join("");
  if (cb) cb.checked = false;
  modal.style.display = "flex";
}

function closeWelcomeUpdateModal() {
  const modal = $("welcome-update-modal");
  const cb = $("welcome-disable-cb");
  localStorage.setItem("tcg_vault_last_seen_ver", CURRENT_APP_VERSION);
  if (cb && cb.checked) {
    localStorage.setItem("tcg_vault_welcome_disabled", "1");
  }
  if (modal) modal.style.display = "none";
}

$("btn-close-welcome-modal")?.addEventListener("click", closeWelcomeUpdateModal);
$("btn-welcome-got-it")?.addEventListener("click", closeWelcomeUpdateModal);

$("btn-inapp-copy-link")?.addEventListener("click", async () => {
  const cur = inAppTabs[activeInAppTabIdx];
  if (!cur?.url) return showToast("No webpage open.");
  try {
    await navigator.clipboard.writeText(cur.url);
    showToast("Copied link to clipboard!");
  } catch (_) {
    prompt("Copy this URL:", cur.url);
  }
});

$("btn-inapp-assign-pricing")?.addEventListener("click", () => {
  const cur = inAppTabs[activeInAppTabIdx];
  if (!cur?.url) return showToast("No webpage open.");
  const pcInput = $("edit-pc-url");
  if (!pcInput || $("card-detail-modal")?.style.display === "none") {
    return showToast("Open a card's detail popup first to assign its pricing link.");
  }
  pcInput.value = cur.url;
  const pullBtn = $("btn-pull-from-pc-url");
  if (pullBtn) pullBtn.click();
  showToast("Assigned current webpage as pricing link!");
});

setTimeout(maybeShowWelcomeUpdateModal, 450);


document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && state.bulkSelectMode) {
    const anyModalOpen = [...document.querySelectorAll(".modal-backdrop")].some((m) => m.style.display === "flex");
    if (!anyModalOpen) {
      state.bulkSelectMode = false;
      state.selectedCardIds.clear();
      updateBulkSelectionBar();
      renderCurrentSubCards();
    }
  }
});

document.addEventListener("wheel", (e) => {
  const strip = e.target.closest(".tile-thumbs");
  if (strip && strip.scrollWidth > strip.clientWidth && Math.abs(e.deltaY) > Math.abs(e.deltaX)) {
    e.preventDefault();
    strip.scrollLeft += e.deltaY;
  }
}, { passive: false });
