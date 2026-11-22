#!/usr/bin/env python3
"""
superthanks_ui.py — Local web UI for scanning YouTube Super Thanks donations
across a whole channel (or a single video).

Run:
    python3 superthanks_ui.py
then open http://127.0.0.1:8750 (opens automatically).

It reuses the scanning logic in yt_superthanks.py (must sit next to this file)
and streams live progress to the browser via Server-Sent Events. Everything
runs locally; no data leaves your machine except the YouTube requests yt-dlp
makes and one exchange-rate lookup.
"""
import json, os, re, sys, threading, urllib.parse, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import yt_superthanks as st  # noqa  (runs its _bootstrap, ensuring yt_dlp is importable)
import yt_dlp

PORT = int(os.environ.get("PORT", "8750"))
SCAN_LOCK = threading.Lock()

VIDEO_RE = re.compile(r"(?:v=|youtu\.be/|/shorts/)([0-9A-Za-z_-]{11})")

INDEX_HTML = r"""<!doctype html>
<html lang="zh"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Super Thanks 打赏统计</title>
<style>
:root{
  --bg:#ffffff; --section:#f5f5f7; --card:#ffffff; --field:#ffffff;
  --txt:#1d1d1f; --muted:#6e6e73; --faint:#86868b;
  --line:#d2d2d7; --line-soft:#e8e8ed;
  --accent:#0071e3; --accent-hover:#0077ed; --accent-soft:#e8f1fd;
  --ok:#248a3d; --err:#d70015;
  --shadow:0 4px 24px rgba(0,0,0,.06); --shadow-sm:0 1px 3px rgba(0,0,0,.05);
  --radius:20px;
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    --bg:#000000; --section:#000000; --card:#1c1c1e; --field:#1c1c1e;
    --txt:#f5f5f7; --muted:#a1a1a6; --faint:#86868b;
    --line:#38383a; --line-soft:#2c2c2e;
    --accent:#0a84ff; --accent-hover:#409cff; --accent-soft:#0a2a4d;
    --ok:#30d158; --err:#ff453a;
    --shadow:0 4px 30px rgba(0,0,0,.5); --shadow-sm:0 1px 3px rgba(0,0,0,.4);
  }
}
*{box-sizing:border-box}
html{-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
body{margin:0;background:var(--bg);color:var(--txt);
font:17px/1.47 -apple-system,BlinkMacSystemFont,"SF Pro Text","SF Pro Display","Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif;}
.wrap{max-width:980px;margin:0 auto;padding:16px}
/* top bar */
.topbar{max-width:980px;margin:0 auto;padding:14px 16px;display:flex;align-items:center;justify-content:space-between;gap:12px}
.brand{font-size:17px;font-weight:600;letter-spacing:-.01em;display:flex;align-items:center;gap:8px}
.brand .dot{width:9px;height:9px;border-radius:50%;background:linear-gradient(135deg,#0071e3,#ff2d55)}
.langsel{position:relative}
.langsel select{-webkit-appearance:none;appearance:none;background:var(--section);border:1px solid var(--line-soft);
color:var(--txt);border-radius:980px;padding:7px 30px 7px 14px;font-size:14px;cursor:pointer;outline:none}
.langsel::after{content:"";position:absolute;right:13px;top:50%;width:7px;height:7px;
border-right:1.6px solid var(--faint);border-bottom:1.6px solid var(--faint);transform:translateY(-70%) rotate(45deg);pointer-events:none}
/* hero */
.hero{text-align:center;padding:40px 16px 44px}
@media (max-width:560px){.stats{grid-template-columns:1fr!important}.hero{padding:26px 16px 24px}.card{padding:20px}}
h1{font-size:clamp(34px,6vw,56px);line-height:1.05;margin:0 0 14px;
font-weight:600;letter-spacing:-.015em}
h1 .grad{background:linear-gradient(90deg,#0071e3,#9b5cff 65%,#ff2d55);
-webkit-background-clip:text;background-clip:text;color:transparent}
.sub{color:var(--muted);margin:0 auto;max-width:600px;font-size:clamp(17px,2.2vw,21px);line-height:1.4}
/* cards */
.card{background:var(--card);border:1px solid var(--line-soft);border-radius:var(--radius);
padding:26px;margin-bottom:20px;box-shadow:var(--shadow)}
label{display:block;font-size:12px;color:var(--faint);margin:0 0 7px;font-weight:500;
letter-spacing:.01em;text-transform:uppercase}
input,select{width:100%;background:var(--field);border:1px solid var(--line);color:var(--txt);
border-radius:12px;padding:13px 14px;font-size:16px;outline:none;
transition:border-color .15s,box-shadow .15s;-webkit-appearance:none;appearance:none}
select{background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='12' height='8' viewBox='0 0 12 8'><path d='M1 1l5 5 5-5' stroke='%2386868b' stroke-width='1.6' fill='none' stroke-linecap='round'/></svg>");
background-repeat:no-repeat;background-position:right 14px center;padding-right:34px}
input:focus,select:focus{border-color:var(--accent);box-shadow:0 0 0 4px var(--accent-soft)}
.row{display:flex;gap:14px;flex-wrap:wrap}
.row>div{flex:1;min-width:130px}
.urlrow{flex:3 1 280px}
/* advanced grid: inputs bottom-align even when labels wrap to different line counts */
.adv{align-items:flex-end}
.adv label{min-height:34px;line-height:1.35}
button{background:var(--accent);border:0;color:#fff;font-weight:400;
border-radius:980px;padding:12px 26px;font-size:17px;cursor:pointer;
transition:background .15s,transform .06s,opacity .15s;line-height:1.2}
button:hover{background:var(--accent-hover)}
button:active{transform:scale(.97)}
button:disabled{opacity:.4;cursor:not-allowed}
.btn-ghost{background:transparent;color:var(--accent);border:1px solid transparent}
.btn-ghost:hover{background:var(--accent-soft)}
details{margin-top:18px;border-top:1px solid var(--line-soft);padding-top:4px}
details summary{cursor:pointer;color:var(--accent);font-size:15px;margin-top:12px;
list-style:none;font-weight:400}
details summary::-webkit-details-marker{display:none}
details summary::before{content:"⌄ ";opacity:.7}
details[open] summary::before{content:"⌃ "}
/* stats */
.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}
.stat{background:var(--section);border-radius:16px;padding:22px 16px;text-align:center}
.stat .v{font-size:30px;font-weight:600;letter-spacing:-.02em}
.stat .v.big{font-size:40px;color:var(--ok)}
.stat .k{font-size:13px;color:var(--muted);margin-top:4px}
/* progress */
.bar{height:6px;background:var(--line-soft);border-radius:999px;overflow:hidden;margin:6px 0 2px}
.bar>i{display:block;height:100%;width:0;border-radius:999px;
background:linear-gradient(90deg,var(--accent),#9b5cff);transition:width .25s ease}
.prog-txt{font-size:15px;color:var(--muted)}
/* chips */
.chips{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px}
.chip{background:var(--section);border-radius:999px;padding:7px 14px;font-size:14px;color:var(--muted)}
.chip b{color:var(--txt);font-weight:600}
/* table */
table{width:100%;border-collapse:collapse;font-size:15px}
th,td{text-align:left;padding:12px 10px;border-bottom:1px solid var(--line-soft)}
th{color:var(--faint);font-weight:500;font-size:12px;cursor:pointer;user-select:none;
text-transform:uppercase;letter-spacing:.02em}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
tbody tr{transition:background .1s}
tbody tr:hover td{background:var(--section)}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.pill{display:inline-block;font-size:12px;color:var(--muted);background:var(--section);
border-radius:999px;padding:2px 9px;margin-left:8px}
.err{color:var(--err)}
.err.pill{background:transparent;border:1px solid var(--err)}
.hide{display:none}
.note{font-size:13px;color:var(--faint);margin-top:12px;line-height:1.5}
.donlist{max-height:380px;overflow:auto;margin-top:10px}
.don{display:flex;justify-content:space-between;gap:14px;padding:11px 2px;border-bottom:1px solid var(--line-soft)}
.don .amt{color:var(--ok);font-weight:600;white-space:nowrap;font-variant-numeric:tabular-nums}
.don .msg{color:var(--muted);font-size:14px}
.flex{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
.flex strong{font-size:19px;font-weight:600;letter-spacing:-.01em}
</style></head>
<body>
<div class="topbar">
  <div class="brand"><span class="dot"></span><span>Super Thanks</span></div>
  <div class="langsel"><select id="lang" aria-label="Language"></select></div>
</div>
<div class="hero">
  <h1 data-i="h1"></h1>
  <p class="sub" data-i="tagline"></p>
</div>
<div class="wrap">

<div class="card">
  <div class="row">
    <div class="urlrow">
      <label data-i="lblUrl"></label>
      <input id="url" placeholder="https://www.youtube.com/@handle ..." data-ip="phUrl">
    </div>
    <div>
      <label data-i="lblCurrency"></label>
      <select id="currency">
        <option>USD</option><option>CNY</option><option>TWD</option><option>JPY</option>
        <option>EUR</option><option>GBP</option><option>HKD</option><option>KRW</option>
        <option>AUD</option><option>CAD</option>
      </select>
    </div>
  </div>
  <details>
    <summary data-i="advanced"></summary>
    <div class="row adv" style="margin-top:10px">
      <div><label data-i="lblLimit"></label><input id="limit" type="number" value="0" min="0"></div>
      <div><label data-i="lblMaxc"></label><input id="maxc" value="1500"></div>
      <div><label data-i="lblWorkers"></label>
        <select id="workers"><option>4</option><option>6</option><option>8</option>
          <option>10</option><option>2</option><option>1</option></select></div>
      <div><label data-i="lblProbe"></label>
        <select id="probe"><option value="0" data-i="optProbeOff"></option>
          <option value="40" data-i="optProbe40"></option><option value="80" data-i="optProbe80"></option></select></div>
      <div><label data-i="lblTabs"></label>
        <select id="tabs"><option value="videos">videos</option>
          <option value="videos,shorts">videos + shorts</option>
          <option value="videos,shorts,streams" data-i="optTabsAll"></option></select></div>
      <div><label data-i="lblCookies"></label>
        <select id="cookies"><option value="" data-i="optCookieNone"></option><option>chrome</option>
          <option>safari</option><option>firefox</option><option>edge</option><option>brave</option></select></div>
    </div>
    <div class="note" data-i="speedNote"></div>
  </details>
  <div style="margin-top:14px" class="flex">
    <button id="go" data-i="btnGo"></button>
    <button id="stop" class="btn-ghost hide" data-i="btnStop"></button>
    <button id="csv" class="btn-ghost hide" data-i="btnCsv"></button>
  </div>
</div>

<div id="progwrap" class="card hide">
  <div class="prog-txt" id="progtxt">准备中…</div>
  <div class="bar"><i id="progbar"></i></div>
</div>

<div id="summary" class="card hide">
  <div class="stats">
    <div class="stat"><div class="v big" id="s_total">—</div><div class="k" id="s_total_k" data-i="statTotal"></div></div>
    <div class="stat"><div class="v" id="s_tips">—</div><div class="k" data-i="statTips"></div></div>
    <div class="stat"><div class="v" id="s_vids">—</div><div class="k" data-i="statVids"></div></div>
  </div>
  <div class="note" data-i="grossNote"></div>
  <div style="margin-top:12px"><label data-i="breakdown"></label><div class="chips" id="breakdown"></div></div>
</div>

<div id="donwrap" class="card hide">
  <div class="flex"><strong id="dontitle" data-i="donTitle"></strong><span class="pill" id="donpill"></span></div>
  <div class="donlist" id="donlist"></div>
</div>

<div id="tablewrap" class="card hide">
  <div class="flex"><strong data-i="rankTitle"></strong><span class="pill" id="tabhint" data-i="tabhint"></span></div>
  <table><thead><tr>
    <th data-k="idx" class="num">#</th>
    <th data-k="title" data-i="thTitle"></th>
    <th data-k="tip_count" class="num" data-i="thTips"></th>
    <th data-k="total" class="num" data-i="thAmount"></th>
    <th data-k="view_count" class="num" data-i="thViews"></th>
  </tr></thead><tbody id="tbody"></tbody></table>
</div>

<script>
const $=id=>document.getElementById(id);
let es=null, rows=[], cur="USD", grand=0, tips=0, totalVids=0, single=false, sortK="total", sortAsc=false;

/* ---------------- i18n ---------------- */
const LANGS={"zh-CN":"简体中文","zh-TW":"繁體中文","en":"English","ja":"日本語","ko":"한국어","es":"Español"};
const T={
 "zh-CN":{h1:()=>`YouTube <span class="grad">Super Thanks</span><br>打赏统计`,
  tagline:"粘贴一个频道链接，统计全站视频的真实打赏总额；也支持单个视频。全程本地运行。",
  phUrl:"https://www.youtube.com/@频道  或  https://www.youtube.com/watch?v=...",
  lblUrl:"频道或视频链接",lblCurrency:"目标货币",advanced:"高级选项",
  lblLimit:"只扫前 N 个视频（0=全部）",lblMaxc:"每个视频扫评论上限（all=不限）",
  lblWorkers:"并行线程（越大越快，易限流）",lblProbe:"快速估算模式（会少算长尾）",
  lblTabs:"频道板块",lblCookies:"读取浏览器 Cookie（年龄限制用）",
  optProbeOff:"关（精确）",optProbe40:"开 · 探测前40条",optProbe80:"开 · 探测前80条",
  optTabsAll:"全部(含直播回放)",optCookieNone:"不用",
  speedNote:"加速提示：打赏评论会浮在「热门」排序最前面，所以 1500 条上限通常已能抓全（想完全精确用 all）、且快得多；并行线程 4-8 较稳（太高易被 YouTube 限流）。可随时停止，已扫结果保留。",
  btnGo:"开始分析",btnStop:"停止",btnCsv:"导出 CSV",
  statTotal:"总打赏（毛额）",statTips:"打赏笔数",statVids:"视频数（有打赏/总）",
  grossNote:"毛额＝观众支付总额；博主实际到手约 70%（YouTube 抽成约 30%）。",
  breakdown:"各货币明细",donTitle:"打赏明细",rankTitle:"各视频排行",tabhint:"点表头可排序",
  thTitle:"标题",thTips:"笔数",thAmount:"金额",thViews:"播放",noTips:"暂无打赏",failPill:"失败",
  progListing:"正在获取视频列表…",progSingle:"分析单个视频…",
  progChannel:n=>`共 ${n} 个视频，开始扫描…`,
  progScan:(d,t,c,a,n)=>`已扫 ${d}/${t} · 累计 ${c} ${a} · ${n} 笔`,
  errPrefix:"出错：",doneMsg:(c,a)=>`完成 ✓ 共 ${c} ${a}`,connEnd:"连接结束",stopped:"已停止（已扫结果保留）"},
 "zh-TW":{h1:()=>`YouTube <span class="grad">Super Thanks</span><br>抖內統計`,
  tagline:"貼上一個頻道連結，統計全站影片的真實抖內總額；也支援單一影片。全程在本機執行。",
  phUrl:"https://www.youtube.com/@頻道  或  https://www.youtube.com/watch?v=...",
  lblUrl:"頻道或影片連結",lblCurrency:"目標貨幣",advanced:"進階選項",
  lblLimit:"只掃前 N 部影片（0=全部）",lblMaxc:"每部影片掃留言上限（all=不限）",
  lblWorkers:"並行執行緒（越大越快，易被限流）",lblProbe:"快速估算模式（會少算長尾）",
  lblTabs:"頻道分頁",lblCookies:"讀取瀏覽器 Cookie（年齡限制用）",
  optProbeOff:"關（精確）",optProbe40:"開 · 探測前40則",optProbe80:"開 · 探測前80則",
  optTabsAll:"全部(含直播回放)",optCookieNone:"不用",
  speedNote:"加速提示：抖內留言會浮在「熱門」排序最前面，所以 1500 則上限通常已能抓全（想完全精確用 all）、且快得多；並行 4-8 較穩（太高易被 YouTube 限流）。可隨時停止，已掃結果保留。",
  btnGo:"開始分析",btnStop:"停止",btnCsv:"匯出 CSV",
  statTotal:"總抖內（毛額）",statTips:"抖內筆數",statVids:"影片數（有抖內/總）",
  grossNote:"毛額＝觀眾支付總額；創作者實際到手約 70%（YouTube 抽成約 30%）。",
  breakdown:"各貨幣明細",donTitle:"抖內明細",rankTitle:"各影片排行",tabhint:"點表頭可排序",
  thTitle:"標題",thTips:"筆數",thAmount:"金額",thViews:"觀看",noTips:"暫無抖內",failPill:"失敗",
  progListing:"正在取得影片清單…",progSingle:"分析單一影片…",
  progChannel:n=>`共 ${n} 部影片，開始掃描…`,
  progScan:(d,t,c,a,n)=>`已掃 ${d}/${t} · 累計 ${c} ${a} · ${n} 筆`,
  errPrefix:"出錯：",doneMsg:(c,a)=>`完成 ✓ 共 ${c} ${a}`,connEnd:"連線結束",stopped:"已停止（已掃結果保留）"},
 "en":{h1:()=>`YouTube <span class="grad">Super Thanks</span><br>Tip Stats`,
  tagline:"Paste a channel link to total up real Super Thanks across all its videos; single videos work too. Everything runs locally.",
  phUrl:"https://www.youtube.com/@channel  or  https://www.youtube.com/watch?v=...",
  lblUrl:"Channel or video link",lblCurrency:"Target currency",advanced:"Advanced options",
  lblLimit:"Only first N videos (0 = all)",lblMaxc:"Max comments per video (all = unlimited)",
  lblWorkers:"Parallel threads (faster, but rate-limits)",lblProbe:"Fast-estimate mode (undercounts the long tail)",
  lblTabs:"Channel tabs",lblCookies:"Read browser cookies (for age-restricted)",
  optProbeOff:"Off (exact)",optProbe40:"On · probe top 40",optProbe80:"On · probe top 80",
  optTabsAll:"All (incl. live replays)",optCookieNone:"None",
  speedNote:"Tip: paid comments float to the top of the ‘top’ sort, so a 1500 cap usually catches them all (use all for exact) and is much faster; 4-8 threads is a safe range (too high triggers YouTube rate limits). Stop anytime — scanned results are kept.",
  btnGo:"Analyze",btnStop:"Stop",btnCsv:"Export CSV",
  statTotal:"Total tips (gross)",statTips:"Tips",statVids:"Videos (with tips / total)",
  grossNote:"Gross = what viewers paid; the creator keeps roughly 70% after YouTube's ~30% cut.",
  breakdown:"By currency",donTitle:"Tip details",rankTitle:"Videos ranked",tabhint:"Click a header to sort",
  thTitle:"Title",thTips:"Tips",thAmount:"Amount",thViews:"Views",noTips:"No tips yet",failPill:"failed",
  progListing:"Fetching the video list…",progSingle:"Analyzing a single video…",
  progChannel:n=>`${n} videos found, scanning…`,
  progScan:(d,t,c,a,n)=>`Scanned ${d}/${t} · ${c} ${a} · ${n} tips`,
  errPrefix:"Error: ",doneMsg:(c,a)=>`Done ✓ ${c} ${a}`,connEnd:"Connection closed",stopped:"Stopped (scanned results kept)"},
 "ja":{h1:()=>`YouTube <span class="grad">Super Thanks</span><br>投げ銭集計`,
  tagline:"チャンネルのリンクを貼ると、全動画の投げ銭合計を集計します。単一動画にも対応。すべてローカルで実行されます。",
  phUrl:"https://www.youtube.com/@channel  または  https://www.youtube.com/watch?v=...",
  lblUrl:"チャンネル／動画のリンク",lblCurrency:"換算する通貨",advanced:"詳細オプション",
  lblLimit:"先頭 N 件のみ（0=全部）",lblMaxc:"動画ごとのコメント上限（all=無制限）",
  lblWorkers:"並列スレッド（速いが制限されやすい）",lblProbe:"高速見積りモード（ロングテールを少なく見積る）",
  lblTabs:"チャンネルのタブ",lblCookies:"ブラウザCookieを読む（年齢制限用）",
  optProbeOff:"オフ（正確）",optProbe40:"オン · 上位40件",optProbe80:"オン · 上位80件",
  optTabsAll:"すべて（配信アーカイブ含む）",optCookieNone:"使わない",
  speedNote:"ヒント：投げ銭コメントは「人気順」の上位に集まるため、1500件の上限でもほぼ取り切れます（完全一致は all）。4-8スレッドが安全（高すぎるとYouTubeに制限されます）。いつでも停止でき、取得済みの結果は保持されます。",
  btnGo:"分析する",btnStop:"停止",btnCsv:"CSV書き出し",
  statTotal:"投げ銭合計（総額）",statTips:"投げ銭件数",statVids:"動画数（投げ銭あり / 全体）",
  grossNote:"総額＝視聴者が支払った合計。YouTubeの約30%の手数料を引き、クリエイターの取り分は約70%です。",
  breakdown:"通貨別",donTitle:"投げ銭の明細",rankTitle:"動画ランキング",tabhint:"見出しをクリックで並べ替え",
  thTitle:"タイトル",thTips:"件数",thAmount:"金額",thViews:"再生",noTips:"投げ銭はまだありません",failPill:"失敗",
  progListing:"動画リストを取得中…",progSingle:"単一動画を分析中…",
  progChannel:n=>`${n} 件の動画、スキャン開始…`,
  progScan:(d,t,c,a,n)=>`${d}/${t} 済 · ${c} ${a} · ${n} 件`,
  errPrefix:"エラー：",doneMsg:(c,a)=>`完了 ✓ ${c} ${a}`,connEnd:"接続を終了しました",stopped:"停止しました（取得済みは保持）"},
 "ko":{h1:()=>`YouTube <span class="grad">Super Thanks</span><br>후원 집계`,
  tagline:"채널 링크를 붙여넣으면 전체 영상의 실제 후원 합계를 집계합니다. 단일 영상도 지원합니다. 모든 처리는 로컬에서 실행됩니다.",
  phUrl:"https://www.youtube.com/@channel  또는  https://www.youtube.com/watch?v=...",
  lblUrl:"채널 또는 영상 링크",lblCurrency:"대상 통화",advanced:"고급 옵션",
  lblLimit:"처음 N개 영상만 (0=전체)",lblMaxc:"영상당 댓글 상한 (all=무제한)",
  lblWorkers:"병렬 스레드 (빠르지만 제한되기 쉬움)",lblProbe:"빠른 추정 모드 (롱테일을 적게 집계)",
  lblTabs:"채널 탭",lblCookies:"브라우저 쿠키 읽기 (연령 제한용)",
  optProbeOff:"끔 (정확)",optProbe40:"켬 · 상위 40개",optProbe80:"켬 · 상위 80개",
  optTabsAll:"전체 (라이브 다시보기 포함)",optCookieNone:"사용 안 함",
  speedNote:"팁: 후원 댓글은 '인기순' 상단에 모이므로 1500개 상한으로도 거의 다 잡힙니다(정확히는 all). 4-8 스레드가 안전합니다(너무 높으면 YouTube 제한). 언제든 멈출 수 있고 수집된 결과는 유지됩니다.",
  btnGo:"분석",btnStop:"중지",btnCsv:"CSV 내보내기",
  statTotal:"총 후원(총액)",statTips:"후원 건수",statVids:"영상 수(후원 있음 / 전체)",
  grossNote:"총액＝시청자가 지불한 합계. YouTube가 약 30%를 가져가고 크리에이터는 약 70%를 받습니다.",
  breakdown:"통화별",donTitle:"후원 내역",rankTitle:"영상 순위",tabhint:"머리글을 클릭해 정렬",
  thTitle:"제목",thTips:"건수",thAmount:"금액",thViews:"조회수",noTips:"아직 후원이 없습니다",failPill:"실패",
  progListing:"영상 목록을 가져오는 중…",progSingle:"단일 영상 분석 중…",
  progChannel:n=>`${n}개 영상, 스캔 시작…`,
  progScan:(d,t,c,a,n)=>`${d}/${t} 완료 · ${c} ${a} · ${n}건`,
  errPrefix:"오류: ",doneMsg:(c,a)=>`완료 ✓ ${c} ${a}`,connEnd:"연결이 종료되었습니다",stopped:"중지됨 (수집된 결과 유지)"},
 "es":{h1:()=>`YouTube <span class="grad">Super Thanks</span><br>Propinas`,
  tagline:"Pega el enlace de un canal para sumar las propinas reales de todos sus vídeos; también funciona con un solo vídeo. Todo se ejecuta localmente.",
  phUrl:"https://www.youtube.com/@canal  o  https://www.youtube.com/watch?v=...",
  lblUrl:"Enlace de canal o vídeo",lblCurrency:"Moneda de destino",advanced:"Opciones avanzadas",
  lblLimit:"Solo los primeros N vídeos (0 = todos)",lblMaxc:"Máx. comentarios por vídeo (all = sin límite)",
  lblWorkers:"Hilos paralelos (más rápido, pero limita)",lblProbe:"Modo estimación rápida (subestima la cola larga)",
  lblTabs:"Pestañas del canal",lblCookies:"Leer cookies del navegador (restringido por edad)",
  optProbeOff:"Apagado (exacto)",optProbe40:"Activo · primeros 40",optProbe80:"Activo · primeros 80",
  optTabsAll:"Todo (incl. directos)",optCookieNone:"Ninguno",
  speedNote:"Consejo: los comentarios de pago suben al principio del orden ‘populares’, así que un tope de 1500 suele capturarlos todos (usa all para exacto) y es mucho más rápido; 4-8 hilos es un rango seguro (demasiados activan límites de YouTube). Puedes parar cuando quieras; los resultados se conservan.",
  btnGo:"Analizar",btnStop:"Parar",btnCsv:"Exportar CSV",
  statTotal:"Propinas totales (bruto)",statTips:"Propinas",statVids:"Vídeos (con propina / total)",
  grossNote:"Bruto = lo que pagaron los espectadores; el creador se queda con ~70% tras el ~30% de YouTube.",
  breakdown:"Por moneda",donTitle:"Detalle de propinas",rankTitle:"Vídeos ordenados",tabhint:"Haz clic en un encabezado para ordenar",
  thTitle:"Título",thTips:"Propinas",thAmount:"Importe",thViews:"Vistas",noTips:"Aún no hay propinas",failPill:"error",
  progListing:"Obteniendo la lista de vídeos…",progSingle:"Analizando un solo vídeo…",
  progChannel:n=>`${n} vídeos encontrados, escaneando…`,
  progScan:(d,t,c,a,n)=>`Escaneados ${d}/${t} · ${c} ${a} · ${n} propinas`,
  errPrefix:"Error: ",doneMsg:(c,a)=>`Listo ✓ ${c} ${a}`,connEnd:"Conexión cerrada",stopped:"Detenido (resultados conservados)"}
};
function detectLang(){
  let saved=null; try{saved=localStorage.getItem("st_lang");}catch(e){}
  if(saved&&T[saved])return saved;
  const prefs=(navigator.languages&&navigator.languages.length)?navigator.languages:[navigator.language||"en"];
  for(const p of prefs){const l=(p||"").toLowerCase();
    if(l.startsWith("zh"))return /tw|hk|hant|mo/.test(l)?"zh-TW":"zh-CN";
    if(l.startsWith("ja"))return "ja"; if(l.startsWith("ko"))return "ko";
    if(l.startsWith("es"))return "es"; if(l.startsWith("en"))return "en";}
  return "en";
}
let lang=detectLang();
const d=()=>T[lang]||T.en;
function applyLang(){
  const t=d(); document.documentElement.lang=lang;
  document.querySelectorAll("[data-i]").forEach(el=>{
    let v=t[el.getAttribute("data-i")]; if(v===undefined)v=T.en[el.getAttribute("data-i")];
    if(typeof v==="function")el.innerHTML=v(); else if(v!==undefined)el.textContent=v;});
  document.querySelectorAll("[data-ip]").forEach(el=>{
    let v=t[el.getAttribute("data-ip")]; if(v!==undefined)el.setAttribute("placeholder",v);});
  const sel=$("lang");
  if(!sel.options.length){for(const c in LANGS){const o=document.createElement("option");o.value=c;o.textContent=LANGS[c];sel.appendChild(o);}}
  sel.value=lang;
  if(rows.length){renderSummary(true);renderTable();if(single&&rows[0])renderDonations(rows[0]);}
}
$("lang").onchange=e=>{lang=e.target.value;try{localStorage.setItem("st_lang",lang);}catch(_){}applyLang();};

function fmt(n){return n.toLocaleString(undefined,{maximumFractionDigits:2,minimumFractionDigits:2});}
function reset(){rows=[];grand=0;tips=0;totalVids=0;single=false;
  $("tbody").innerHTML="";$("breakdown").innerHTML="";$("donlist").innerHTML="";
  ["summary","tablewrap","donwrap"].forEach(x=>$(x).classList.add("hide"));
  $("progwrap").classList.remove("hide");$("csv").classList.add("hide");}

function renderSummary(done){
  $("summary").classList.remove("hide");
  $("s_total").textContent=cur+" "+fmt(grand);
  $("s_tips").textContent=tips;
  const withTips=rows.filter(r=>r.tip_count>0).length;
  $("s_vids").textContent=withTips+" / "+rows.length;
  // breakdown
  const bd={};rows.forEach(r=>{for(const k in (r.by_currency||{}))bd[k]=(bd[k]||0)+r.by_currency[k];});
  $("breakdown").innerHTML=Object.entries(bd).sort((a,b)=>b[1]-a[1])
    .map(([k,v])=>`<span class="chip">${k} <b>${fmt(v)}</b></span>`).join("")||`<span class="chip">${d().noTips}</span>`;
}
function renderTable(){
  $("tablewrap").classList.remove("hide");
  const r=[...rows].sort((a,b)=>{let x=a[sortK],y=b[sortK];
    if(sortK==="title"){x=(x||"");y=(y||"");return sortAsc?x.localeCompare(y):y.localeCompare(x);}
    x=x||0;y=y||0;return sortAsc?x-y:y-x;});
  $("tbody").innerHTML=r.map((v,i)=>`<tr>
    <td class="num">${i+1}</td>
    <td><a href="https://youtu.be/${v.id}" target="_blank">${(v.title||v.id)}</a>${v.error?` <span class="err pill">${d().failPill}</span>`:''}</td>
    <td class="num">${v.tip_count||0}</td>
    <td class="num">${v.total?fmt(v.total):'—'}</td>
    <td class="num">${v.view_count?v.view_count.toLocaleString():'—'}</td></tr>`).join("");
}
function renderDonations(v){
  $("donwrap").classList.remove("hide");
  $("dontitle").textContent=d().donTitle+" · "+(v.title||v.id);
  $("donpill").textContent=(v.chips||[]).length;
  const sorted=(v.donations||v.chips||[]).map(d=> typeof d==='string'?{amount:d,msg:'',author:''}:d);
  $("donlist").innerHTML=sorted.map(d=>`<div class="don">
    <span class="amt">${d.amount}</span>
    <span class="msg">${d.author?('<b style="color:#aeb6c7">'+d.author+'</b> '):''}${(d.msg||'').replace(/</g,'&lt;')}</span></div>`).join("");
}

function startScan(){
  const url=$("url").value.trim(); if(!url){$("url").focus();return;}
  cur=$("currency").value;
  const qs=new URLSearchParams({url,currency:cur,limit:$("limit").value||"0",
    max:$("maxc").value||"1500",tabs:$("tabs").value,cookies:$("cookies").value,
    workers:$("workers").value||"4",probe:$("probe").value||"0"});
  reset();
  $("go").disabled=true;$("stop").classList.remove("hide");
  $("progtxt").textContent=d().progListing;$("progbar").style.width="4%";
  es=new EventSource("/api/scan?"+qs.toString());
  es.onmessage=ev=>{
    const m=JSON.parse(ev.data);
    if(m.type==="meta"){
      single=m.kind==="video";totalVids=m.total;
      $("progtxt").textContent=single?d().progSingle:d().progChannel(m.total);
    }else if(m.type==="video"){
      rows.push(m); if(m.total)grand+=m.total; tips+=m.tip_count||0;
      const done=rows.length,pct=totalVids?Math.round(done/totalVids*100):100;
      $("progbar").style.width=Math.max(pct,4)+"%";
      $("progtxt").textContent=d().progScan(done,totalVids,cur,fmt(grand),tips);
      renderSummary(false);renderTable();
      if(single)renderDonations(m);
    }else if(m.type==="error"){
      $("progtxt").innerHTML=`<span class="err">${d().errPrefix}${m.message}</span>`;
    }else if(m.type==="done"){
      finish(d().doneMsg(cur,fmt(grand)));
    }
  };
  es.onerror=()=>{ if(es){finish(d().connEnd);} };
}
function finish(msg){
  if(es){es.close();es=null;}
  $("go").disabled=false;$("stop").classList.add("hide");
  $("progtxt").textContent=msg;$("progbar").style.width="100%";
  if(rows.length){$("csv").classList.remove("hide");renderSummary(true);}
}
$("go").onclick=startScan;
$("stop").onclick=()=>finish(d().stopped);
$("url").addEventListener("keydown",e=>{if(e.key==="Enter")startScan();});
document.querySelectorAll("th[data-k]").forEach(th=>th.onclick=()=>{
  const k=th.dataset.k; if(k==="idx")return;
  if(sortK===k)sortAsc=!sortAsc;else{sortK=k;sortAsc=false;} renderTable();});
$("csv").onclick=()=>{
  const head=["video_id","title","upload_date","tip_count","total_"+cur,"view_count","url"];
  const lines=[head.join(",")].concat([...rows].sort((a,b)=>(b.total||0)-(a.total||0)).map(r=>
    [r.id,'"'+((r.title||'').replace(/"/g,'""'))+'"',r.upload_date||'',r.tip_count||0,
     r.total||0,r.view_count||'',"https://youtu.be/"+r.id].join(",")));
  const blob=new Blob([lines.join("\n")],{type:"text/csv"});
  const a=document.createElement("a");a.href=URL.createObjectURL(blob);
  a.download="superthanks_report.csv";a.click();
};
applyLang();
</script>
</div></body></html>"""


def parse_qs(path):
    q = urllib.parse.urlparse(path).query
    return {k: v[0] for k, v in urllib.parse.parse_qs(q).items()}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass  # quiet

    def _sse_event(self, obj):
        self.wfile.write(b"data: " + json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n\n")
        self.wfile.flush()

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index"):
            body = INDEX_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/api/scan"):
            return self.handle_scan(parse_qs(self.path))
        self.send_error(404)

    def handle_scan(self, p):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            self._run_scan(p)
        except BrokenPipeError:
            pass
        except Exception as e:
            try:
                self._sse_event({"type": "error", "message": str(e)})
            except Exception:
                pass

    def _run_scan(self, p):
        url = (p.get("url") or "").strip()
        currency = (p.get("currency") or "USD").upper()
        limit = int(p.get("limit") or 0)
        maxc_s = p.get("max") or "1500"
        maxc = 0 if maxc_s == "all" else int(maxc_s)
        tabs = [t for t in (p.get("tabs") or "videos").split(",") if t]
        cookies = p.get("cookies") or ""
        try:
            workers = max(1, min(16, int(p.get("workers") or 4)))
        except ValueError:
            workers = 4
        try:
            probe = int(p.get("probe") or 0)
        except ValueError:
            probe = 0

        rates = st.fetch_rates()
        if currency not in rates:
            self._sse_event({"type": "error", "message": f"未知货币 {currency}"})
            return

        cookie_opts = {}
        if cookies:
            cookie_opts["cookiesfrombrowser"] = (cookies,)

        # Decide: single video vs channel vs file-of-ids
        m = VIDEO_RE.search(url)
        if m or re.fullmatch(r"[0-9A-Za-z_-]{11}", url):
            vid = m.group(1) if m else url
            videos = [(vid, None)]
            kind = "video"
        elif os.path.isfile(url):
            with open(url) as f:
                ids = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
            videos = []
            for x in ids:
                mm = VIDEO_RE.search(x)
                videos.append((mm.group(1) if mm else x, None))
            kind = "channel"
        else:
            self._sse_event({"type": "meta", "kind": "listing", "total": 0})
            videos = st.list_channel_videos(url, tabs, cookie_opts)
            kind = "channel"

        if limit:
            videos = videos[:limit]
        self._sse_event({"type": "meta", "kind": kind, "total": len(videos)})
        if not videos:
            self._sse_event({"type": "error", "message": "没有找到视频，请检查链接。"})
            return

        def make_event(data):
            n = 0
            tally = {}
            for c in data.get("chips", []):
                pr = st.parse_amount(c)
                if not pr:
                    continue
                code, amt = pr
                conv = st.to_target(amt, code, rates, currency)
                if conv is None:
                    continue
                n += 1
                tally[code] = tally.get(code, 0) + conv
            ev = {
                "type": "video", "id": data.get("id"),
                "title": data.get("title"), "upload_date": data.get("upload_date"),
                "view_count": data.get("view_count"),
                "tip_count": n, "total": round(sum(tally.values()), 2),
                "by_currency": {k: round(v, 2) for k, v in tally.items()},
                "error": data.get("error"),
            }
            if kind == "video":
                ev["chips"] = data.get("chips", [])
            return ev

        # scan_many calls on_result from THIS (handler) thread as each future
        # completes, so SSE writes stay single-threaded and safe.
        with SCAN_LOCK:
            if probe and kind == "channel" and probe < (maxc or 10**9):
                deep = []
                def on_probe(data):
                    if data.get("chips"):
                        deep.append((data["id"], data.get("title")))
                    else:
                        self._sse_event(make_event(data))
                st.scan_many(videos, probe, cookie_opts, workers, on_probe)
                st.scan_many(deep, maxc, cookie_opts, workers,
                             lambda data: self._sse_event(make_event(data)))
            else:
                st.scan_many(videos, maxc, cookie_opts, workers,
                             lambda data: self._sse_event(make_event(data)))
        self._sse_event({"type": "done"})


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://127.0.0.1:{PORT}"
    print(f"Super Thanks UI running at {url}  (Ctrl+C to stop)")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
