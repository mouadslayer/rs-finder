#!/usr/bin/env python3
"""
rs_fr_lookup_v9.py

v9: final focused extractor — Brand + MPN only.
- Primary: parse search result cards for a[data-testid="brand-link"] -> span and dd[data-testid="mpn-desktop"] (or dt->dd)
- Fallbacks: aggressive raw search for /web/p/ links (and snippets), product page parse
- Special: If Brand == "RS PRO" and no MPN, use distrelec field as MPN (dd[data-testid="distrelec-desktop"] or dt->dd)
- Accept numeric MPNs when they come from the mpn-desktop field (but avoid returning RS_PN itself)
Output columns: RS_PN, Manufacturer_PN, Brand, Product_URL, Status
"""
import requests, urllib.parse, time, os, csv, re
from pathlib import Path
from bs4 import BeautifulSoup
# ---------------- resource_path helper ----------------
import sys, os

def resource_path(relative_path):
    """
    Get absolute path to resource, works for dev and when packaged by PyInstaller.
    Use it like:
        input_path = resource_path("input.csv")
    """
    base_path = getattr(sys, "_MEIPASS", os.path.abspath("."))
    return os.path.join(base_path, relative_path)
# -----------------------------------------------------

# -------- CONFIG ----------
INPUT_FILE = "input.csv"
OUTPUT_FILE = "output.csv"
FAILED_DIR = Path("failed_pages"); FAILED_DIR.mkdir(exist_ok=True)

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
DELAY = 1.1
SHORT_DELAY = 0.25
MAX_RETRIES = 3
RETRY_BACKOFF = 2.0

# -------- Helpers ----------
def safe_get(url, headers=HEADERS, timeout=15, max_retries=MAX_RETRIES):
    delay = 0.8
    for attempt in range(1, max_retries+1):
        try:
            r = requests.get(url, headers=headers, timeout=timeout)
            return r
        except Exception as e:
            if attempt == max_retries:
                raise
            time.sleep(delay)
            delay *= RETRY_BACKOFF

def save_failed_html(rs_pn, html_text, suffix="page"):
    try:
        fname = FAILED_DIR / f"{rs_pn}_{suffix}.html"
        fname.write_text(html_text, encoding="utf-8")
    except Exception as e:
        print(f"Unable to save failed html for {rs_pn}: {e}")

def norm(t):
    return re.sub(r"\s+", " ", (t or "")).strip()

# -------- heuristics ----------
_rejection_substrings = [
    "contains svhc", "cadmium", "lead", "cas no", "ah", " v ", " volt", "volts",
    "amp", "capacity", "rechargeable", "watt", "battery", "description"
]

def looks_like_brand(s):
    if not s: return False
    s = s.strip()
    return bool(re.search(r"[A-Za-zÀ-ÖØ-öø-ÿ]", s)) and len(s) > 1

def is_valid_mpn_from_field(candidate, rs_pn_hint=None):
    """
    Accept mpn candidate coming from dd[data-testid="mpn-desktop"] / dt->dd etc.
    Rules:
      - must contain at least one alnum char
      - disallow returning the RS_PN itself
      - avoid obvious long sentences or warnings
      - otherwise accept (including pure numeric) if not equal to RS_PN
    """
    if not candidate:
        return False
    s = candidate.strip()
    if len(s.split()) > 6:
        return False
    lower = s.lower()
    for bad in _rejection_substrings:
        if bad in lower:
            return False
    if not re.search(r"[A-Za-z0-9]", s):
        return False
    if rs_pn_hint and s.replace(" ", "").lower() == str(rs_pn_hint).lower():
        # don't return the RS PN as the manufacturer PN
        return False
    return True

def heuristic_mpn_candidate(tok, rs_pn_hint=None):
    """
    Secondary heuristic for tokens scraped from larger strings.
    Requires mixture of letters and digits or punctuation with letters/digits.
    """
    if not tok: return False
    s = tok.strip()
    if len(s.split()) > 4:
        return False
    if ":" in s:
        return False
    if not re.search(r"[A-Za-z0-9]", s):
        return False
    if re.fullmatch(r"\d{2,20}", s):
        # allow pure numeric only if it's not the RS PN
        if rs_pn_hint and s == str(rs_pn_hint):
            return False
        # accept short numeric (e.g., "2777") as MPN if not RS PN
        return True
    has_letter = bool(re.search(r"[A-Za-zÀ-ÖØ-öø-ÿ]", s))
    has_digit = bool(re.search(r"\d", s))
    has_punct = bool(re.search(r"[-_\/\.]", s))
    if (has_letter and has_digit) or (has_punct and (has_letter or has_digit)):
        return True
    if has_letter and len(s) >= 3 and len(s.split())==1:
        return True
    return False

# -------- distrelec extractor helper ----------
def extract_distrelec_from_container(container):
    if not container:
        return ""
    dd = container.find("dd", {"data-testid": "distrelec-desktop"})
    if dd and dd.get_text(strip=True):
        return norm(dd.get_text(" ", strip=True))
    dt = container.find("dt", {"data-testid": "distrelec-desktop"})
    if dt:
        nxt = dt.find_next_sibling("dd")
        if nxt and nxt.get_text(strip=True):
            return norm(nxt.get_text(" ", strip=True))
    # fallback: any dt containing 'distrelec'
    for dt in container.find_all("dt"):
        if "distrelec" in dt.get_text(" ", strip=True).lower():
            nxt = dt.find_next_sibling("dd")
            if nxt and nxt.get_text(strip=True):
                return norm(nxt.get_text(" ", strip=True))
    return ""

# -------- parse card on search page ----------
def parse_search_page_for_fields(search_html, rs_pn):
    soup = BeautifulSoup(search_html, "html.parser")
    anchors = soup.find_all("a", href=True)
    candidates = []
    for a in anchors:
        href = a["href"]
        if "/web/p/" in href:
            full = href if href.startswith("http") else urllib.parse.urljoin("https://fr.rs-online.com", href)
            score = 1 + (10 if str(rs_pn) in href or str(rs_pn) in a.get_text(" ", strip=True) else 0)
            candidates.append((score, a, full))
    if not candidates:
        return None, "", "", "SEARCH_NO_PRODUCT_LINK"
    candidates.sort(key=lambda x: x[0], reverse=True)

    for _, anchor, product_url in candidates:
        container = anchor
        for _ in range(4):
            if container is None: break
            if container.name in ("article","li","div"):
                # brand
                brand = ""
                brand_a = container.find("a", {"data-testid": "brand-link"})
                if brand_a:
                    span = brand_a.find("span")
                    brand = norm(span.get_text(strip=True) if span else brand_a.get_text(" ", strip=True))
                # mpn usual
                mpn = ""
                ddmpn = container.find("dd", {"data-testid": "mpn-desktop"})
                if ddmpn and ddmpn.get_text(strip=True):
                    candidate = norm(ddmpn.get_text(" ", strip=True))
                    if is_valid_mpn_from_field(candidate, rs_pn_hint=rs_pn):
                        mpn = candidate
                else:
                    dtmpn = container.find("dt", {"data-testid": "mpn-desktop"}) or container.find(lambda t: t.name=="dt" and "référence fabricant" in t.get_text(" ", strip=True).lower())
                    if dtmpn:
                        nxt = dtmpn.find_next_sibling("dd")
                        if nxt and nxt.get_text(strip=True):
                            candidate = norm(nxt.get_text(" ", strip=True))
                            if is_valid_mpn_from_field(candidate, rs_pn_hint=rs_pn):
                                mpn = candidate
                # if brand is RS PRO and mpn empty, try distrelec
                if (not mpn) and brand and "rs" in brand.lower() and "pro" in brand.lower():
                    dist = extract_distrelec_from_container(container)
                    if dist and is_valid_mpn_from_field(dist, rs_pn_hint=rs_pn):
                        mpn = dist
                # sanitize if mpn is noisy: try tokens
                if mpn and not is_valid_mpn_from_field(mpn, rs_pn_hint=rs_pn):
                    for tok in re.split(r"[\s,;/]+", mpn):
                        if heuristic_mpn_candidate(tok, rs_pn_hint=rs_pn):
                            mpn = tok; break
                    else:
                        mpn = ""
                # accept if we have brand or mpn
                if (brand and looks_like_brand(brand)) or (mpn and (is_valid_mpn_from_field(mpn, rs_pn) or heuristic_mpn_candidate(mpn, rs_pn))):
                    return product_url, brand or "", mpn or "", "OK(search-card)"
            container = container.parent

        # sibling fallback
        sibling = anchor.find_next_sibling()
        if sibling:
            brand = ""
            brand_a = sibling.find("a", {"data-testid": "brand-link"})
            if brand_a:
                span = brand_a.find("span")
                brand = norm(span.get_text(strip=True) if span else brand_a.get_text(" ", strip=True))
            mpn = ""
            ddmpn = sibling.find("dd", {"data-testid": "mpn-desktop"})
            if ddmpn and ddmpn.get_text(strip=True):
                candidate = norm(ddmpn.get_text(" ", strip=True))
                if is_valid_mpn_from_field(candidate, rs_pn_hint=rs_pn):
                    mpn = candidate
            if (not mpn) and brand and "rs" in brand.lower() and "pro" in brand.lower():
                dist = extract_distrelec_from_container(sibling)
                if dist and is_valid_mpn_from_field(dist, rs_pn_hint=rs_pn):
                    mpn = dist
            if mpn and not is_valid_mpn_from_field(mpn, rs_pn_hint=rs_pn):
                for tok in re.split(r"[\s,;/]+", mpn):
                    if heuristic_mpn_candidate(tok, rs_pn_hint=rs_pn):
                        mpn = tok; break
                else:
                    mpn = ""
            if (brand and looks_like_brand(brand)) or (mpn and (is_valid_mpn_from_field(mpn, rs_pn) or heuristic_mpn_candidate(mpn, rs_pn))):
                return product_url, brand or "", mpn or "", "OK(search-sibling)"
    # none had fields, return top product link for fallback
    return candidates[0][2], "", "", "OK_FOUND_anchor_no_card_fields"

# -------- aggressive raw scan for /web/p/ occurrences (and snippet extraction) ----------
def aggressive_search_scan(search_html, rs_pn):
    raw = search_html
    matches = re.findall(r'(/web/p/(?:[^"\'\s>\\]+))', raw)
    if matches:
        # prefer one with the RS_PN
        for m in matches:
            if str(rs_pn) in m:
                link = urllib.parse.urljoin("https://fr.rs-online.com", m)
                idx = raw.find(m)
                left = max(0, idx-800); right = min(len(raw), idx+800)
                snippet = raw[left:right]
                brand = ""
                mbrand = re.search(r'data-testid=["\']brand-link["\'][^>]*>.*?<span[^>]*>([^<]{1,80})', snippet, flags=re.I|re.S)
                if mbrand: brand = mbrand.group(1).strip()
                mpn = ""
                mmpn = re.search(r'dd[^>]*data-testid=["\']mpn-desktop["\'][^>]*>([^<]{1,80})', snippet, flags=re.I|re.S)
                if not mmpn:
                    mmpn = re.search(r'dt[^>]*data-testid=["\']mpn-desktop["\'][^>]*>[^<]*</dt>\s*<dd[^>]*>([^<]{1,80})', snippet, flags=re.I|re.S)
                if mmpn:
                    cand = mmpn.group(1).strip()
                    if is_valid_mpn_from_field(cand, rs_pn_hint=rs_pn):
                        mpn = cand
                    else:
                        # try tokenization
                        for tok in re.split(r"[\s,;/]+", cand):
                            if heuristic_mpn_candidate(tok, rs_pn_hint=rs_pn):
                                mpn = tok; break
                # If brand suggests RS PRO and mpn missing, try distrelec in snippet
                if (not mpn) and (brand and "rs" in brand.lower() and "pro" in brand.lower() or re.search(r"rs\s*pro", snippet, flags=re.I)):
                    m_dist = re.search(r'(\d{2,4}[-]\d{2,4}[-]?\d{0,4})', snippet)
                    if m_dist:
                        cand = m_dist.group(1).strip()
                        if is_valid_mpn_from_field(cand, rs_pn_hint=rs_pn):
                            mpn = cand
                if brand or mpn:
                    return link, brand, mpn, "OK(raw-snippet)"
        # else return first link
        return urllib.parse.urljoin("https://fr.rs-online.com", matches[0]), "", "", "OK(raw-first)"
    return None, "", "", "NO_RAW_LINKS"

# -------- product page parse (fallback) ----------
def parse_product_page_for_fields(html_text, rs_pn_hint=None):
    soup = BeautifulSoup(html_text, "html.parser")
    brand = ""
    a_brand = soup.find("a", {"data-testid": "brand-link"})
    if a_brand and a_brand.get_text(strip=True):
        span = a_brand.find("span"); brand = norm(span.get_text(strip=True) if span else a_brand.get_text(" ", strip=True))
    if not brand:
        dd_brand = soup.find("dd", {"data-testid": "brand-desktop"})
        if dd_brand:
            sp = dd_brand.find("span"); brand = norm(sp.get_text(strip=True) if sp else dd_brand.get_text(" ", strip=True))
    mpn = ""
    ddmpn = soup.find("dd", {"data-testid": "mpn-desktop"})
    if ddmpn and ddmpn.get_text(strip=True):
        candidate = norm(ddmpn.get_text(" ", strip=True))
        if is_valid_mpn_from_field(candidate, rs_pn_hint=rs_pn_hint):
            mpn = candidate
        else:
            # allow numeric mpn if not RS PN
            if candidate.isdigit() and (not rs_pn_hint or candidate != str(rs_pn_hint)):
                mpn = candidate
    else:
        dtmpn = soup.find("dt", {"data-testid": "mpn-desktop"}) or soup.find(lambda t: t.name=="dt" and "référence fabricant" in t.get_text(" ", strip=True).lower())
        if dtmpn:
            nxt = dtmpn.find_next_sibling("dd")
            if nxt and nxt.get_text(strip=True):
                candidate = norm(nxt.get_text(" ", strip=True))
                if is_valid_mpn_from_field(candidate, rs_pn_hint=rs_pn_hint):
                    mpn = candidate
                else:
                    if candidate.isdigit() and (not rs_pn_hint or candidate != str(rs_pn_hint)):
                        mpn = candidate
    # if brand is RS PRO and mpn empty -> try distrelec field
    if (not mpn) and brand and "rs" in brand.lower() and "pro" in brand.lower():
        dist = extract_distrelec_from_container(soup)
        if dist and is_valid_mpn_from_field(dist, rs_pn_hint=rs_pn_hint):
            mpn = dist
    return mpn or "", brand or ""

# -------- combined search wrapper ----------
def search_rs_for_part_combined(rs_pn):
    base_search = "https://fr.rs-online.com/web/c/?searchTerm="
    url = base_search + urllib.parse.quote_plus(str(rs_pn))
    try:
        r = safe_get(url)
    except Exception as e:
        return None, "", "", f"SEARCH_ERROR:{e}"
    if r.status_code != 200:
        save_failed_html(rs_pn, r.text, suffix="search_http_"+str(r.status_code))
        return None, "", "", f"SEARCH_HTTP_{r.status_code}"
    # 1) search-card parse
    product_link, brand, mpn, status = parse_search_page_for_fields(r.text, rs_pn)
    if product_link and (brand or mpn):
        return product_link, brand, mpn, status
    # 2) aggressive snippet scan
    pl2, b2, m2, st2 = aggressive_search_scan(r.text, rs_pn)
    if pl2:
        if b2 or m2:
            return pl2, b2, m2, st2
        return pl2, "", "", st2
    save_failed_html(rs_pn, r.text, suffix="search_no_link_raw")
    return None, "", "", "SEARCH_NO_PRODUCT_LINK"

# -------- main flow ----------
def fetch_rs_info(rs_pn):
    # try product page first
    direct_url = f"https://fr.rs-online.com/web/p/{rs_pn}/"
    try:
        r = safe_get(direct_url)
    except Exception as e:
        return "", "", "", f"ERROR_DIRECT:{e}"
    if r.status_code == 200:
        mpn, brand = parse_product_page_for_fields(r.text, rs_pn_hint=rs_pn)
        if (mpn and (is_valid_mpn_from_field(mpn, rs_pn) or heuristic_mpn_candidate(mpn, rs_pn))) or (brand and looks_like_brand(brand)):
            return mpn or "", brand or "", direct_url, "OK(direct)"
        else:
            save_failed_html(rs_pn, r.text, suffix="direct_fields_missing")
    time.sleep(SHORT_DELAY)
    product_link, brand_s, mpn_s, status = search_rs_for_part_combined(rs_pn)
    if not product_link:
        return "", "", "", status
    # if search returned fields, accept them (sanitize mpn)
    if (brand_s and looks_like_brand(brand_s)) or (mpn_s and (is_valid_mpn_from_field(mpn_s, rs_pn) or heuristic_mpn_candidate(mpn_s, rs_pn))):
        mpn_clean = mpn_s
        if mpn_clean and not is_valid_mpn_from_field(mpn_clean, rs_pn):
            for tok in re.split(r"[\s,;/]+", mpn_clean):
                if heuristic_mpn_candidate(tok, rs_pn):
                    mpn_clean = tok; break
            else:
                mpn_clean = ""
        return mpn_clean or "", brand_s or "", product_link, f"OK(search:{status})"
    # else follow product link
    try:
        r2 = safe_get(product_link)
    except Exception as e:
        return "", "", product_link, f"ERROR_FETCH_PRODUCT:{e}"
    if r2.status_code != 200:
        save_failed_html(rs_pn, r2.text, suffix="product_http_"+str(r2.status_code))
        return "", "", product_link, f"PRODUCT_HTTP_{r2.status_code}"
    mpn2, brand2 = parse_product_page_for_fields(r2.text, rs_pn_hint=rs_pn)
    if (mpn2 and (is_valid_mpn_from_field(mpn2, rs_pn) or heuristic_mpn_candidate(mpn2, rs_pn))) or (brand2 and looks_like_brand(brand2)):
        return mpn2 or "", brand2 or "", product_link, "OK(search->product)"
    save_failed_html(rs_pn, r2.text, suffix="product_fields_missing_after_search")
    return "", "", product_link, "PRODUCT_PAGE_FIELDS_MISSING"

# -------- resume helper ----------
def load_already_done(output_file):
    done = set()
    if os.path.exists(output_file):
        try:
            import pandas as _pd
            df_done = _pd.read_csv(output_file, dtype=str)
            if "RS_PN" in df_done.columns:
                done = set(df_done["RS_PN"].astype(str).str.strip().tolist())
        except Exception:
            pass
    return done

# -------- main ----------
def main():
    if not os.path.exists(INPUT_FILE):
        print(f"Input file '{INPUT_FILE}' not found. Create CSV named {INPUT_FILE} with header RS_PN.")
        return
    import pandas as pd
    df_in = pd.read_csv(INPUT_FILE, dtype=str).fillna("")
    rs_list = [str(x).strip() for x in df_in["RS_PN"].tolist()]
    already_done = load_already_done(OUTPUT_FILE)
    if already_done:
        print(f"Resuming: {len(already_done)} parts already processed (found in {OUTPUT_FILE}).")
    total = len(rs_list)
    for idx, rs_pn in enumerate(rs_list, start=1):
        if not rs_pn:
            continue
        if rs_pn in already_done:
            print(f"[{idx}/{total}] Skipping {rs_pn} (already done).")
            continue
        print(f"[{idx}/{total}] Looking up {rs_pn} ...")
        try:
            mpn, brand, product_url, status = fetch_rs_info(rs_pn)
        except Exception as e:
            mpn = brand = product_url = ""
            status = f"EXCEPTION:{e}"
        print(f" -> {status} | MPN={mpn or 'N/A'} | Brand={brand or 'N/A'}")
        row = {"RS_PN": rs_pn, "Manufacturer_PN": mpn, "Brand": brand, "Product_URL": product_url, "Status": status}
        header = not os.path.exists(OUTPUT_FILE)
        with open(OUTPUT_FILE, "a", newline='', encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["RS_PN","Manufacturer_PN","Brand","Product_URL","Status"])
            if header:
                writer.writeheader()
            writer.writerow(row)
        time.sleep(DELAY)
    print("\nDone. Results in:", OUTPUT_FILE)
    print("Failed pages (if any) saved in:", FAILED_DIR)

if __name__ == "__main__":
    main()
