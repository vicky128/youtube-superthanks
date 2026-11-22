#!/usr/bin/env python3
"""
yt_superthanks.py — Aggregate YouTube "Super Thanks" (超级感谢) donations across
all of a channel's videos, or for individual videos.

Donation amounts are NOT exposed by the official YouTube Data API. YouTube's
internal (InnerTube) comment data attaches a paid "chip" to each Super Thanks
comment at:
    commentSurfaceEntityPayload.pdgCommentChip.pdgCommentChipRenderer.chipText.simpleText
e.g. "NT$1,500.00", "MYR 15.00", "€10.00".
yt-dlp fetches this data (handling pagination / tokens) but discards the chip,
so we hook its response parser, collect every chip, de-dupe, parse the localized
currency string, convert to a target currency, and sum.

Super Thanks comments float to the TOP of the "top comments" sort, so scanning
the first ~1-2k comments per video captures essentially all of them cheaply.
Live-stream Super Chat/Stickers are NOT in the comment section and are not counted
(same scope as superthanks.net).

Gross amounts (what viewers paid). Creator nets roughly 70% after YouTube's cut.
"""

import argparse, json, os, re, sys, time, csv, urllib.request

# ---- ensure yt_dlp importable (add Homebrew's bundled site-packages if needed) ----
def _bootstrap():
    try:
        import yt_dlp  # noqa
        return
    except ImportError:
        pass
    import glob
    for pat in ("/opt/homebrew/Cellar/yt-dlp/*/libexec/lib/python*/site-packages",
                "/usr/local/Cellar/yt-dlp/*/libexec/lib/python*/site-packages",
                os.path.expanduser("~/.local/pipx/venvs/yt-dlp/lib/python*/site-packages")):
        for sp in sorted(glob.glob(pat), reverse=True):
            if os.path.isdir(os.path.join(sp, "yt_dlp")):
                sys.path.insert(0, sp)
                try:
                    import yt_dlp  # noqa
                    return
                except ImportError:
                    sys.path.pop(0)
    sys.stderr.write(
        "ERROR: Python module 'yt_dlp' not found. Install it into this Python:\n"
        "   pip3 install -U yt-dlp\n")
    sys.exit(2)

_bootstrap()
import yt_dlp
from yt_dlp.extractor.youtube import _video as ytv

# ---------------------- currency parsing ----------------------
SYMBOL_MAP = [
    ("NT$", "TWD"), ("HK$", "HKD"), ("CA$", "CAD"), ("A$", "AUD"), ("AU$", "AUD"),
    ("MX$", "MXN"), ("NZ$", "NZD"), ("R$", "BRL"), ("US$", "USD"), ("CN¥", "CNY"),
    ("SGD", "SGD"), ("CHF", "CHF"), ("MYR", "MYR"), ("PHP", "PHP"), ("Rp", "IDR"),
    ("₹", "INR"), ("₩", "KRW"), ("₺", "TRY"), ("₱", "PHP"), ("฿", "THB"),
    ("zł", "PLN"), ("Kč", "CZK"), ("kr", "SEK"), ("£", "GBP"), ("€", "EUR"),
    ("¥", "JPY"), ("₪", "ILS"), ("₫", "VND"), ("₦", "NGN"), ("$", "USD"),
]

def parse_amount(text):
    t = (text or "").strip()
    code = None; rest = None
    for sym, iso in SYMBOL_MAP:
        if sym in t:
            code = iso; rest = t.replace(sym, ""); break
    if code is None:
        m = re.match(r"^\s*([A-Za-z]{3})\b", t)
        if m:
            code = m.group(1).upper(); rest = t[m.end():]
        else:
            return None
    num = re.sub(r"[^\d.]", "", rest.replace(",", ""))
    if num.count(".") > 1:
        a, _, b = num.rpartition(".")
        num = a.replace(".", "") + "." + b
    try:
        return (code, float(num))
    except ValueError:
        return None

def fetch_rates():
    """units of X per 1 USD."""
    for url in ("https://open.er-api.com/v6/latest/USD",
                "https://api.exchangerate-api.com/v4/latest/USD"):
        try:
            with urllib.request.urlopen(url, timeout=25) as r:
                d = json.load(r)
            return d.get("rates") or d["conversion_rates"]
        except Exception as e:
            sys.stderr.write(f"[warn] rate source failed ({url}): {e}\n")
    raise SystemExit("Could not fetch exchange rates. Check your connection.")

def to_target(amount, code, rates, target):
    if code not in rates or target not in rates:
        return None
    usd = amount / rates[code]
    return usd * rates[target]

# ---------------------- comment chip scraping ----------------------
class ChipCollector:
    """Hooks yt-dlp's response parser to collect Super Thanks chips."""
    def __init__(self):
        self.chips = {}            # entityKey -> chipText
        self._orig = ytv.YoutubeIE._extract_response
        collector = self
        def patched(ie_self, *a, **kw):
            resp = collector._orig(ie_self, *a, **kw)
            try:
                muts = (resp or {}).get("frameworkUpdates", {}) \
                                   .get("entityBatchUpdate", {}).get("mutations", [])
                for m in muts:
                    surf = (m.get("payload") or {}).get("commentSurfaceEntityPayload")
                    if not surf:
                        continue
                    txt = surf.get("pdgCommentChip", {}).get("pdgCommentChipRenderer", {}) \
                              .get("chipText", {}).get("simpleText")
                    if txt:
                        k = m.get("entityKey")
                        collector.chips.setdefault(k, txt)
            except Exception:
                pass
            return resp
        ytv.YoutubeIE._extract_response = patched
    def reset(self):
        self.chips = {}
    def restore(self):
        ytv.YoutubeIE._extract_response = self._orig


def scan_video(ydl, collector, video_id, max_comments):
    collector.reset()
    maxc = "all" if max_comments in (0, None, "all") else str(max_comments)
    ydl.params["getcomments"] = True
    ydl.params.setdefault("extractor_args", {})["youtube"] = {
        "comment_sort": ["top"],
        "max_comments": [maxc, "all", "0", "0"],
    }
    info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
    if not info:
        return {"id": video_id, "title": None, "chips": [],
                "error": "unavailable (age-restricted/private/removed — try --cookies-from-browser)"}
    return {
        "id": video_id,
        "title": info.get("title"),
        "upload_date": info.get("upload_date"),
        "view_count": info.get("view_count"),
        "comment_count": info.get("comment_count"),
        "chips": list(collector.chips.values()),
    }

# ---------------------- parallel (thread-safe) scraping ----------------------
# Super Thanks float to the top of "top comments", so a modest per-video cap
# plus several worker threads scans a big channel far faster. Each thread keeps
# its own YoutubeDL; chips are routed to a thread-local bucket by a single
# class-level patch installed once.
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

_TLS = threading.local()
_PATCH_INSTALLED = {"done": False}

def _install_threadsafe_patch():
    if _PATCH_INSTALLED["done"]:
        return
    orig = ytv.YoutubeIE._extract_response
    def patched(ie_self, *a, **kw):
        resp = orig(ie_self, *a, **kw)
        try:
            bucket = getattr(_TLS, "chips", None)
            if bucket is not None:
                muts = (resp or {}).get("frameworkUpdates", {}) \
                                   .get("entityBatchUpdate", {}).get("mutations", [])
                for m in muts:
                    surf = (m.get("payload") or {}).get("commentSurfaceEntityPayload")
                    if not surf:
                        continue
                    txt = surf.get("pdgCommentChip", {}).get("pdgCommentChipRenderer", {}) \
                              .get("chipText", {}).get("simpleText")
                    if txt:
                        bucket.setdefault(m.get("entityKey"), txt)
        except Exception:
            pass
        return resp
    ytv.YoutubeIE._extract_response = patched
    _PATCH_INSTALLED["done"] = True

def _thread_ydl(cookie_opts):
    ydl = getattr(_TLS, "ydl", None)
    if ydl is None:
        opts = {"quiet": True, "no_warnings": True, "skip_download": True, "ignoreerrors": True}
        opts.update(cookie_opts or {})
        ydl = yt_dlp.YoutubeDL(opts)
        _TLS.ydl = ydl
    return ydl

def scan_video_tls(video_id, max_comments, cookie_opts=None, title=None):
    """Thread-safe single-video scan (returns raw data incl. chips)."""
    _install_threadsafe_patch()
    ydl = _thread_ydl(cookie_opts)
    maxc = "all" if max_comments in (0, None, "all") else str(max_comments)
    ydl.params["getcomments"] = True
    ydl.params.setdefault("extractor_args", {})["youtube"] = {
        "comment_sort": ["top"], "max_comments": [maxc, "all", "0", "0"],
    }
    _TLS.chips = {}
    try:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
    except Exception as e:
        _TLS.chips = None
        return {"id": video_id, "title": title, "chips": [], "error": str(e)}
    chips = _TLS.chips or {}
    _TLS.chips = None
    if not info:
        return {"id": video_id, "title": title, "chips": [],
                "error": "unavailable (age-restricted/private/removed — try --cookies-from-browser)"}
    return {"id": video_id, "title": info.get("title"), "upload_date": info.get("upload_date"),
            "view_count": info.get("view_count"), "comment_count": info.get("comment_count"),
            "chips": list(chips.values())}

def scan_many(videos, max_comments, cookie_opts, workers, on_result):
    """Scan videos [(id, title), ...] with `workers` threads.
    Calls on_result(data_dict) as each finishes (order not guaranteed)."""
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = {ex.submit(scan_video_tls, vid, max_comments, cookie_opts, title): vid
                for vid, title in videos}
        for fut in as_completed(futs):
            try:
                data = fut.result()
            except Exception as e:
                data = {"id": futs[fut], "title": None, "chips": [], "error": str(e)}
            on_result(data)

# ---------------------- channel enumeration ----------------------
def list_channel_videos(url, tabs, cookie_opts=None):
    """Return list of (video_id, title) for a channel across requested tabs."""
    base = url.rstrip("/")
    # Normalize to a channel base. yt-dlp accepts @handle, /channel/ID, /c/, /user/.
    targets = []
    for tab in tabs:
        if re.search(r"/(videos|shorts|streams)/?$", base):
            targets = [base]; break
        targets.append(f"{base}/{tab}")
    seen = {}
    opts = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist",
            "skip_download": True, "ignoreerrors": True}
    opts.update(cookie_opts or {})
    with yt_dlp.YoutubeDL(opts) as ydl:
        for t in targets:
            try:
                info = ydl.extract_info(t, download=False)
            except Exception as e:
                sys.stderr.write(f"[warn] could not list {t}: {e}\n")
                continue
            for e in (info or {}).get("entries", []) or []:
                if not e:
                    continue
                vid = e.get("id")
                if vid and vid not in seen:
                    seen[vid] = e.get("title")
    return list(seen.items())

# ---------------------- main ----------------------
def main():
    ap = argparse.ArgumentParser(description="Aggregate YouTube Super Thanks donations.")
    ap.add_argument("target", help="Channel URL/@handle, or a single video URL/ID, "
                                    "or path to a .txt file of video IDs/URLs (one per line).")
    ap.add_argument("-c", "--currency", default="USD", help="Target currency (default USD).")
    ap.add_argument("-m", "--max-comments", default="1500",
                    help="Max comments scanned per video (default 1500; 'all' = unlimited = exact). "
                         "Paid comments cluster near the top of 'top' sort, so a cap is MUCH faster "
                         "but can miss a few deep ones (a cap of 1500 captured ~95-100%% in tests). "
                         "Use 'all' for guaranteed-complete totals.")
    ap.add_argument("-w", "--workers", type=int, default=4,
                    help="Parallel worker threads (default 4). Higher = faster but more likely "
                         "to hit YouTube rate limits; 4-8 is a safe range.")
    ap.add_argument("--probe", type=int, default=0, metavar="N",
                    help="FAST ESTIMATE (lossy): first scan only the top N comments of every "
                         "video, then deep-scan only those that showed a donation. Reliable for "
                         "heavily-tipped videos (paid comments float to the top), but UNDERCOUNTS: "
                         "a lone tip on a video with many comments may sit below the top N and be "
                         "missed (tests missed ~3%% of money / several lightly-tipped videos at "
                         "--probe 40). Use for a quick overview; omit it (scan with --max-comments "
                         "or 'all') for exact totals.")
    ap.add_argument("--tabs", default="videos",
                    help="Comma list of channel tabs to include: videos,shorts,streams "
                         "(default: videos).")
    ap.add_argument("--limit", type=int, default=0, help="Only process first N videos (0=all).")
    ap.add_argument("-o", "--out", default="superthanks_report",
                    help="Output basename (writes .csv and .json).")
    ap.add_argument("--cache", default="superthanks_cache.json",
                    help="Resume cache file (skips already-scanned videos).")
    ap.add_argument("--sleep", type=float, default=0.0,
                    help="Seconds between videos (sequential mode / --workers 1 only).")
    ap.add_argument("--cookies-from-browser", default=None,
                    help="Read cookies from a browser (chrome/safari/firefox/edge/brave). "
                         "Needed for age-restricted videos and helps avoid bot checks on long runs.")
    ap.add_argument("--cookies", default=None, help="Path to a cookies.txt file (alternative to above).")
    args = ap.parse_args()

    target = args.target
    maxc = 0 if args.max_comments == "all" else int(args.max_comments)
    tabs = [t.strip() for t in args.tabs.split(",") if t.strip()]

    # Build the work list
    if os.path.isfile(target):
        with open(target) as f:
            ids = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
        videos = [(re.search(r"(?:v=|/)([0-9A-Za-z_-]{11})", x).group(1)
                   if re.search(r"(?:v=|/)([0-9A-Za-z_-]{11})", x) else x, None) for x in ids]
    elif re.search(r"(?:v=|youtu\.be/|/shorts/)([0-9A-Za-z_-]{11})", target) or \
         (re.fullmatch(r"[0-9A-Za-z_-]{11}", target)):
        m = re.search(r"(?:v=|youtu\.be/|/shorts/)([0-9A-Za-z_-]{11})", target)
        vid = m.group(1) if m else target
        videos = [(vid, None)]
    else:
        sys.stderr.write(f"Listing videos for channel: {target} (tabs: {tabs}) ...\n")
        _ck = {}
        if args.cookies_from_browser:
            _ck["cookiesfrombrowser"] = (args.cookies_from_browser, )
        if args.cookies:
            _ck["cookiefile"] = args.cookies
        videos = list_channel_videos(target, tabs, _ck)
        sys.stderr.write(f"Found {len(videos)} videos.\n")

    if args.limit:
        videos = videos[:args.limit]

    # cache
    cache = {}
    if os.path.isfile(args.cache):
        try:
            cache = json.load(open(args.cache))
        except Exception:
            cache = {}

    rates = fetch_rates()
    if args.currency not in rates:
        raise SystemExit(f"Unknown target currency {args.currency!r}. "
                         f"Examples: USD EUR TWD JPY CNY GBP.")

    cookie_opts = {}
    if args.cookies_from_browser:
        cookie_opts["cookiesfrombrowser"] = (args.cookies_from_browser, )
    if args.cookies:
        cookie_opts["cookiefile"] = args.cookies

    def summarize(data):
        """Compute tip_count/total/by_currency in the CURRENT target currency
        from the raw chips. Never cached, so --currency can change between runs."""
        n = 0; cur_tally = {}
        for c in data.get("chips", []):
            p = parse_amount(c)
            if not p: continue
            code, amt = p
            conv = to_target(amt, code, rates, args.currency)
            if conv is None: continue
            n += 1
            cur_tally[code] = cur_tally.get(code, 0) + conv
        data["tip_count"] = n
        data["total"] = round(sum(cur_tally.values()), 2)
        data["by_currency"] = {k: round(v, 2) for k, v in cur_tally.items()}
        return data

    results = []
    lock = threading.Lock()
    done_n = [0]
    total_n = len(videos)
    t0 = time.time()

    # Serve cached videos instantly; only scan the rest.
    todo = []
    for vid, title in videos:
        if vid in cache:
            data = summarize(dict(cache[vid]))
            results.append(data)
            done_n[0] += 1
        else:
            todo.append((vid, title))
    if done_n[0]:
        sys.stderr.write(f"{done_n[0]} videos served from cache.\n")

    def handle(data):
        with lock:
            cache[data["id"]] = {k: data.get(k) for k in
                                 ("id", "title", "upload_date", "view_count",
                                  "comment_count", "chips", "error")}
            json.dump(cache, open(args.cache, "w"), ensure_ascii=False)
            s = summarize(data)
            results.append(s)
            done_n[0] += 1
            i = done_n[0]
            rate = i / max(1e-6, time.time() - t0)
            eta = (total_n - i) / rate if rate else 0
            sys.stderr.write(f"[{i}/{total_n}] {s['id']}  "
                             f"{(s.get('title') or '')[:36]!r}  tips={s['tip_count']}  "
                             f"{args.currency} {s['total']:,.2f}  "
                             f"({rate:.1f} vid/s, ETA {eta/60:.1f}m)\n")

    def run_pass(items, cap, on_each):
        if args.workers > 1:
            scan_many(items, cap, cookie_opts, args.workers, on_each)
        else:
            _install_threadsafe_patch()
            for vid, title in items:
                on_each(scan_video_tls(vid, cap, cookie_opts, title))
                if args.sleep:
                    time.sleep(args.sleep)

    if todo:
        if args.probe and args.probe < (maxc or 10**9):
            # Phase 1: cheap probe of the top N comments of every video.
            deep = []
            def on_probe(data):
                if data.get("chips"):          # donation present -> deep-scan later
                    deep.append((data["id"], data.get("title")))
                else:                           # no donation in top N -> finalize as 0
                    handle(data)
            sys.stderr.write(f"Probe phase: checking top {args.probe} comments of "
                             f"{len(todo)} videos ({args.workers} workers)...\n")
            run_pass(todo, args.probe, on_probe)
            sys.stderr.write(f"Probe done: {len(deep)} videos have donations; "
                             f"deep-scanning those (cap {args.max_comments})...\n")
            run_pass(deep, maxc, handle)
        else:
            run_pass(todo, maxc, handle)

    # ---- aggregate + output ----
    results.sort(key=lambda r: r.get("total", 0), reverse=True)
    grand = round(sum(r.get("total", 0) for r in results), 2)
    grand_tips = sum(r.get("tip_count", 0) for r in results)

    with open(args.out + ".json", "w") as f:
        json.dump({"currency": args.currency, "grand_total": grand,
                   "total_tips": grand_tips, "videos": results}, f,
                  ensure_ascii=False, indent=2)
    with open(args.out + ".csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["video_id", "title", "upload_date", "tip_count",
                    f"total_{args.currency}", "view_count", "url"])
        for r in results:
            w.writerow([r["id"], r.get("title", ""), r.get("upload_date", ""),
                        r.get("tip_count", 0), r.get("total", 0), r.get("view_count", ""),
                        f"https://youtu.be/{r['id']}"])

    print("\n" + "=" * 60)
    print(f"GRAND TOTAL: {args.currency} {grand:,.2f}  (gross; creator keeps ~70%)")
    print(f"Super Thanks tips: {grand_tips} across {len(results)} videos")
    print("=" * 60)
    print("Top videos by donations:")
    for r in results[:15]:
        if r.get("total", 0) <= 0: continue
        print(f"  {args.currency} {r.get('total',0):>10,.2f}  ({r.get('tip_count',0):>3} tips)  "
              f"{(r.get('title') or r['id'])[:50]}")
    print(f"\nWrote {args.out}.csv and {args.out}.json")

if __name__ == "__main__":
    main()
