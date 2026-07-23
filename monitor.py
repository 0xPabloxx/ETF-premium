#!/usr/bin/env python3
"""纳斯达克100 / 标普500 场内基金溢价监控。

数据源（新浪财经）：
  实时行情  https://hq.sinajs.cn/list=sh513100        (场内价格)
  基金净值  https://hq.sinajs.cn/list=f_513100        (T-1 单位净值)
  K线数据   https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData

溢价率 = 场内现价 / T-1单位净值 - 1（即交易所溢价风险提示所用口径）。

用法：
  python3 monitor.py                     # 一次性输出溢价表
  python3 monitor.py --watch 60          # 每 60 秒刷新
  python3 monitor.py --json              # JSON 输出（供程序/skill 消费）
  python3 monitor.py check               # 供定时任务：交易时段内检查阈值并推送通知
  python3 monitor.py check --force       # 忽略交易时段/去重，强制检查
  python3 monitor.py test-notify         # 向所有已配置渠道发测试消息
  python3 monitor.py history sh513100    # 近 20 日日K

通知配置见 notify.json（低溢价=买入机会提醒，高溢价=风险提醒）。
"""

import argparse
import json
import smtplib
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from email.header import Header
from email.mime.text import MIMEText
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
HQ_URL = "https://hq.sinajs.cn/list="
KLINE_URL = (
    "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "CN_MarketData.getKLineData?symbol={symbol}&scale={scale}&ma=no&datalen={datalen}"
)
HEADERS = {
    "Referer": "https://finance.sina.com.cn",
    "User-Agent": "Mozilla/5.0",
}
FUNDS_FILE = BASE / "funds.json"
NOTIFY_FILE = BASE / "notify.json"
STATE_FILE = BASE / "state.json"
CSV_FILE = BASE / "premium_log.csv"
CHART_FILE = BASE / "premium_chart.html"
CSV_HEADER = "time,code,name,price,nav,nav_date,premium_pct"
TZ = ZoneInfo("Asia/Shanghai")


def http_get(url: str, encoding: str = "gbk") -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.read().decode(encoding, errors="replace")


def http_post(url: str, data: dict, as_json: bool = True) -> str:
    if as_json:
        body = json.dumps(data).encode("utf-8")
        headers = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
    else:
        body = urllib.parse.urlencode(data).encode("utf-8")
        headers = {"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "Mozilla/5.0"}
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode("utf-8", errors="replace")


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 数据抓取

def fetch_all(codes: list) -> list:
    """codes 形如 sh513100；一次请求同时取行情和净值。"""
    nav_codes = ["f_" + c[2:] for c in codes]
    raw = http_get(HQ_URL + ",".join(codes + nav_codes))
    fields = {}
    for line in raw.strip().splitlines():
        if '="' not in line:
            continue
        key, _, val = line.partition('="')
        fields[key.replace("var hq_str_", "")] = val.rstrip('";').split(",")

    rows = []
    for code in codes:
        q = fields.get(code)
        n = fields.get("f_" + code[2:])
        if not q or len(q) < 32 or not n or len(n) < 5:
            rows.append({"code": code, "error": "no data"})
            continue
        price = float(q[3])
        prev_close = float(q[2])
        nav = float(n[1])
        if price == 0 or nav == 0:  # 停牌或未开盘
            rows.append({"code": code, "error": "price/nav is 0"})
            continue
        rows.append({
            "code": code,
            "name": q[0],
            "price": price,
            "change_pct": (price / prev_close - 1) * 100 if prev_close else None,
            "nav": nav,
            "nav_date": n[4],
            "premium_pct": (price / nav - 1) * 100,
            "quote_time": f"{q[30]} {q[31]}",
        })
    return rows


# ---------------------------------------------------------------- 通知渠道

def notify_macos(title: str, message: str) -> None:
    if sys.platform == "darwin":
        script = f'display notification "{message}" with title "{title}"'
        subprocess.run(["osascript", "-e", script], capture_output=True)


def notify_pushplus(token: str, title: str, message: str) -> None:
    http_post("https://www.pushplus.plus/send",
              {"token": token, "title": title, "content": message.replace("\n", "<br>")})


def notify_serverchan(sendkey: str, title: str, message: str) -> None:
    http_post(f"https://sctapi.ftqq.com/{sendkey}.send",
              {"title": title, "desp": message}, as_json=False)


def notify_wecom(webhook: str, title: str, message: str) -> None:
    http_post(webhook, {"msgtype": "text", "text": {"content": f"{title}\n{message}"}})


def notify_email(cfg: dict, title: str, message: str) -> None:
    msg = MIMEText(message, "plain", "utf-8")
    msg["Subject"] = Header(title, "utf-8")
    msg["From"] = cfg["user"]
    msg["To"] = cfg["to"]
    with smtplib.SMTP_SSL(cfg.get("smtp_host", "smtp.gmail.com"), cfg.get("smtp_port", 465), timeout=20) as s:
        s.login(cfg["user"], cfg["app_password"])
        s.sendmail(cfg["user"], [cfg["to"]], msg.as_string())


def send_all(cfg: dict, title: str, message: str) -> list:
    """向所有已配置渠道推送，返回 (渠道, 是否成功, 错误) 列表。"""
    ch = cfg.get("channels", {})
    results = []
    tasks = []
    if ch.get("macos"):
        tasks.append(("macos", lambda: notify_macos(title, message)))
    if ch.get("pushplus_token"):
        tasks.append(("pushplus", lambda: notify_pushplus(ch["pushplus_token"], title, message)))
    if ch.get("serverchan_sendkey"):
        tasks.append(("serverchan", lambda: notify_serverchan(ch["serverchan_sendkey"], title, message)))
    if ch.get("wecom_webhook"):
        tasks.append(("wecom", lambda: notify_wecom(ch["wecom_webhook"], title, message)))
    email = ch.get("email") or {}
    if email.get("user") and email.get("app_password") and email.get("to"):
        tasks.append(("email", lambda: notify_email(email, title, message)))
    for name, fn in tasks:
        try:
            fn()
            results.append((name, True, ""))
        except Exception as e:
            results.append((name, False, str(e)))
    return results


# ---------------------------------------------------------------- 数据落盘

def record_rows(rows: list) -> int:
    """把快照追加到 premium_log.csv，按 (时间,代码) 去重，返回新增行数。"""
    existing = set()
    if CSV_FILE.exists():
        for line in CSV_FILE.read_text(encoding="utf-8").splitlines()[1:]:
            parts = line.split(",")
            if len(parts) >= 2:
                existing.add((parts[0], parts[1]))
    else:
        CSV_FILE.write_text(CSV_HEADER + "\n", encoding="utf-8")

    added = []
    for r in rows:
        if "error" in r:
            continue
        key = (r["quote_time"], r["code"])
        if key in existing:
            continue
        existing.add(key)
        added.append(
            f"{r['quote_time']},{r['code']},{r['name']},{r['price']},"
            f"{r['nav']},{r['nav_date']},{r['premium_pct']:.4f}"
        )
    if added:
        with CSV_FILE.open("a", encoding="utf-8") as f:
            f.write("\n".join(added) + "\n")
    return len(added)


def cmd_backfill() -> None:
    """用 5 分钟 K 线回填当天的溢价曲线（净值取当前已公布的最新净值）。"""
    today = f"{datetime.now(TZ):%F}"
    groups = load_watchlist()
    codes = [c for lst in groups.values() for c in lst]
    live = {r["code"]: r for r in fetch_all(codes) if "error" not in r}
    rows = []
    for code in codes:
        info = live.get(code)
        if not info or info["nav_date"] >= today:
            continue  # 无净值或净值口径对不上，跳过
        try:
            bars = json.loads(http_get(
                KLINE_URL.format(symbol=code, scale=5, datalen=60), encoding="utf-8"))
        except Exception as e:
            print(f"{code} K线获取失败: {e}")
            continue
        for bar in bars:
            if not bar["day"].startswith(today):
                continue
            close = float(bar["close"])
            rows.append({
                "quote_time": bar["day"],
                "code": code,
                "name": info["name"],
                "price": close,
                "nav": info["nav"],
                "nav_date": info["nav_date"],
                "premium_pct": (close / info["nav"] - 1) * 100,
            })
    n = record_rows(rows)
    print(f"回填 {today}：新增 {n} 条记录 -> {CSV_FILE}")


# ---------------------------------------------------------------- check（定时任务入口）

def in_trading_hours(now: datetime) -> bool:
    if now.weekday() >= 5:
        return False
    hm = now.hour * 100 + now.minute
    return 925 <= hm <= 1135 or 1255 <= hm <= 1510


def cmd_check(args) -> None:
    cfg = load_json(NOTIFY_FILE, {})
    now = datetime.now(TZ)
    if not args.force and not in_trading_hours(now):
        print(f"[{now:%F %T}] 非交易时段，跳过")
        return

    low = cfg.get("alert_low", 3.0)
    high = cfg.get("alert_high", 10.0)
    groups = load_watchlist()
    codes = [c for lst in groups.values() for c in lst]
    rows = [r for r in fetch_all(codes) if "error" not in r]
    record_rows(rows)  # 顺带落盘，供 plot 画溢价曲线

    state = load_json(STATE_FILE, {})
    today = f"{now:%F}"
    hits = []
    for r in rows:
        p = r["premium_pct"]
        cond = "low" if p <= low else "high" if p >= high else None
        if not cond:
            continue
        key = f"{today}|{r['code']}|{cond}"
        if not args.force and state.get(key):
            continue  # 今天该条件已提醒过
        state[key] = f"{now:%T}"
        tag = "📉 溢价回落(机会)" if cond == "low" else "📈 高溢价(风险)"
        hits.append(
            f"{tag} {r['name']} {r['code'][2:]}  溢价 {p:+.2f}%  "
            f"价 {r['price']:.3f} / 净值 {r['nav']:.4f}({r['nav_date']})"
        )

    if not hits:
        print(f"[{now:%F %T}] 无触发（阈值: ≤{low}% 或 ≥{high}%）")
        return

    # 只保留今天的去重状态，避免文件无限增长
    state = {k: v for k, v in state.items() if k.startswith(today)}
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    title = f"ETF溢价提醒 {len(hits)}条"
    message = "\n".join(hits)
    print(f"[{now:%F %T}] 触发 {len(hits)} 条:\n{message}")
    for name, ok, err in send_all(cfg, title, message):
        print(f"  -> {name}: {'ok' if ok else 'FAIL ' + err}")


def cmd_test_notify() -> None:
    cfg = load_json(NOTIFY_FILE, {})
    results = send_all(cfg, "ETF溢价监控 测试",
                       f"测试消息 {datetime.now(TZ):%F %T}\n收到即说明该渠道配置成功。")
    if not results:
        print("没有任何已配置的渠道，请编辑 notify.json")
    for name, ok, err in results:
        print(f"{name}: {'ok' if ok else 'FAIL ' + err}")


# ---------------------------------------------------------------- 溢价曲线

CHART_TEMPLATE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>场内基金溢价率走势</title>
<style>
.viz-root{color-scheme:light;
  --surface-1:#fcfcfb;--page:#f9f9f7;--text-primary:#0b0b0b;--text-secondary:#52514e;
  --muted:#898781;--grid:#e1e0d9;--axis:#c3c2b7;--border:rgba(11,11,11,.10);
  --s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--s4:#eda100;--s5:#e87ba4;--s6:#008300;}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])) .viz-root{color-scheme:dark;
  --surface-1:#1a1a19;--page:#0d0d0d;--text-primary:#fff;--text-secondary:#c3c2b7;
  --muted:#898781;--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);
  --s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500;--s5:#d55181;--s6:#008300;}}
:root[data-theme="dark"] .viz-root{color-scheme:dark;
  --surface-1:#1a1a19;--page:#0d0d0d;--text-primary:#fff;--text-secondary:#c3c2b7;
  --muted:#898781;--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);
  --s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500;--s5:#d55181;--s6:#008300;}
body{margin:0}
.viz-root{background:var(--page);min-height:100vh;padding:24px 16px;
  font-family:system-ui,-apple-system,"Segoe UI",sans-serif;color:var(--text-primary)}
.wrap{max-width:960px;margin:0 auto}
h1{font-size:1.15rem;margin:0 0 2px}
.sub{color:var(--muted);font-size:.8rem;margin:0 0 20px}
.card{background:var(--surface-1);border:1px solid var(--border);border-radius:10px;
  padding:16px;margin-bottom:20px}
h2{font-size:.95rem;margin:0 0 10px}
.legend{display:flex;flex-wrap:wrap;gap:4px 14px;margin-bottom:8px}
.legend span{display:inline-flex;align-items:center;gap:5px;font-size:.75rem;color:var(--text-secondary)}
.chip{width:8px;height:8px;border-radius:2px;display:inline-block}
.plot{position:relative}
svg{display:block;width:100%;height:auto}
.tip{position:absolute;pointer-events:none;background:var(--surface-1);border:1px solid var(--border);
  border-radius:6px;padding:6px 9px;font-size:.72rem;box-shadow:0 2px 8px rgba(0,0,0,.12);
  display:none;z-index:2;white-space:nowrap}
.tip b{display:block;margin-bottom:3px;color:var(--text-secondary);font-weight:600}
.tip div{display:flex;align-items:center;gap:5px;line-height:1.5}
.tip .v{margin-left:auto;padding-left:10px;font-variant-numeric:tabular-nums}
details{margin-top:8px}summary{font-size:.75rem;color:var(--muted);cursor:pointer}
table{border-collapse:collapse;font-size:.75rem;margin-top:8px;width:100%}
th,td{text-align:right;padding:3px 8px;border-bottom:1px solid var(--grid);
  font-variant-numeric:tabular-nums;color:var(--text-secondary)}
th:first-child,td:first-child{text-align:left}
th{color:var(--muted);font-weight:600}
</style></head><body><div class="viz-root"><div class="wrap">
<h1>场内基金溢价率走势</h1>
<p class="sub">溢价率 = 场内价 / 最新公布净值 − 1 · 生成于 __GENERATED__</p>
<div id="charts"></div>
</div></div>
<script>
const DATA = __DATA__;
const COLORS = ["--s1","--s2","--s3","--s4","--s5","--s6"];
const css = v => getComputedStyle(document.querySelector(".viz-root")).getPropertyValue(v).trim();
const short = t => t.slice(5, 16);

function build(group, gi) {
  const card = document.createElement("div"); card.className = "card";
  card.innerHTML = `<h2>${group.name}</h2>`;
  const legend = document.createElement("div"); legend.className = "legend";
  group.series.forEach((s, i) => {
    legend.insertAdjacentHTML("beforeend",
      `<span><i class="chip" style="background:var(${COLORS[i]})"></i>${s.name} ${s.code.slice(2)}</span>`);
  });
  card.appendChild(legend);
  const plot = document.createElement("div"); plot.className = "plot";
  card.appendChild(plot);

  // 数据表（可访问性兜底）
  const rows = group.series.map((s, i) => {
    const vs = s.points.map(p => p[1]);
    const f = x => x.toFixed(2) + "%";
    return `<tr><td><i class="chip" style="background:var(${COLORS[i]})"></i> ${s.name} ${s.code.slice(2)}</td>` +
      `<td>${f(vs[vs.length-1])}</td><td>${f(vs.reduce((a,b)=>a+b,0)/vs.length)}</td>` +
      `<td>${f(Math.min(...vs))}</td><td>${f(Math.max(...vs))}</td><td>${vs.length}</td></tr>`;
  }).join("");
  card.insertAdjacentHTML("beforeend",
    `<details><summary>数据表</summary><table><tr><th>基金</th><th>最新</th><th>均值</th><th>最低</th><th>最高</th><th>样本</th></tr>${rows}</table></details>`);
  document.getElementById("charts").appendChild(card);

  const render = () => {
    const W = plot.clientWidth, H = Math.max(220, Math.min(320, W * 0.34));
    const M = {t: 10, r: 14, b: 24, l: 44};
    const xs = [...new Set(group.series.flatMap(s => s.points.map(p => p[0])))].sort();
    const xi = new Map(xs.map((t, i) => [t, i]));
    const all = group.series.flatMap(s => s.points.map(p => p[1]));
    let y0 = Math.min(...all), y1 = Math.max(...all);
    const pad = Math.max((y1 - y0) * 0.1, 0.1); y0 -= pad; y1 += pad;
    const X = i => M.l + (xs.length < 2 ? 0.5 : i / (xs.length - 1)) * (W - M.l - M.r);
    const Y = v => M.t + (1 - (v - y0) / (y1 - y0)) * (H - M.t - M.b);
    let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${group.name}溢价率走势">`;
    const step = niceStep((y1 - y0) / 4);
    for (let v = Math.ceil(y0 / step) * step; v <= y1; v += step) {
      svg += `<line x1="${M.l}" x2="${W-M.r}" y1="${Y(v)}" y2="${Y(v)}" stroke="${css('--grid')}"/>` +
             `<text x="${M.l-6}" y="${Y(v)+3}" text-anchor="end" font-size="10" fill="${css('--muted')}">${v>0?"+":""}${+v.toFixed(1)}%</text>`;
    }
    const nT = Math.min(6, xs.length);
    for (let k = 0; k < nT; k++) {
      const i = Math.round(k * (xs.length - 1) / Math.max(nT - 1, 1));
      const anchor = k === nT - 1 ? "end" : k === 0 ? "start" : "middle";
      svg += `<text x="${X(i)}" y="${H-8}" text-anchor="${anchor}" font-size="10" fill="${css('--muted')}">${short(xs[i])}</text>`;
    }
    svg += `<line x1="${M.l}" x2="${W-M.r}" y1="${H-M.b}" y2="${H-M.b}" stroke="${css('--axis')}"/>`;
    group.series.forEach((s, i) => {
      const d = s.points.map((p, j) => `${j ? "L" : "M"}${X(xi.get(p[0])).toFixed(1)},${Y(p[1]).toFixed(1)}`).join("");
      svg += `<path d="${d}" fill="none" stroke="var(${COLORS[i]})" stroke-width="2" stroke-linejoin="round"/>`;
    });
    svg += `<line id="ch${gi}" y1="${M.t}" y2="${H-M.b}" stroke="${css('--axis')}" stroke-dasharray="3,3" visibility="hidden"/></svg>`;
    plot.innerHTML = svg + `<div class="tip" id="tip${gi}"></div>`;

    const el = plot.querySelector("svg"), tip = plot.querySelector(".tip"), cross = plot.querySelector(`#ch${gi}`);
    el.addEventListener("mousemove", e => {
      const r = el.getBoundingClientRect();
      const px = (e.clientX - r.left) * W / r.width;
      const i = Math.max(0, Math.min(xs.length - 1,
        Math.round((px - M.l) / (W - M.l - M.r) * (xs.length - 1))));
      cross.setAttribute("x1", X(i)); cross.setAttribute("x2", X(i));
      cross.setAttribute("visibility", "visible");
      const items = group.series
        .map((s, si) => ({s, si, v: (s.points.find(p => p[0] === xs[i]) || [])[1]}))
        .filter(o => o.v !== undefined).sort((a, b) => b.v - a.v)
        .map(o => `<div><i class="chip" style="background:var(${COLORS[o.si]})"></i>${o.s.name}<span class="v">${o.v>0?"+":""}${o.v.toFixed(2)}%</span></div>`);
      tip.innerHTML = `<b>${xs[i].slice(5)}</b>` + items.join("");
      tip.style.display = "block";
      const tw = tip.offsetWidth, lx = e.clientX - r.left;
      tip.style.left = (lx + tw + 24 > r.width ? lx - tw - 12 : lx + 12) + "px";
      tip.style.top = Math.max(0, e.clientY - r.top - 10) + "px";
    });
    el.addEventListener("mouseleave", () => { tip.style.display = "none"; cross.setAttribute("visibility", "hidden"); });
  };
  render();
  new ResizeObserver(render).observe(plot);
}
function niceStep(raw) {
  const p = Math.pow(10, Math.floor(Math.log10(raw)));
  for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= raw) return m * p;
  return 10 * p;
}
DATA.groups.forEach(build);
</script></body></html>
"""


def cmd_plot() -> None:
    if not CSV_FILE.exists():
        sys.exit("还没有数据：先跑 `monitor.py backfill` 或等定时任务积累 premium_log.csv")
    by_code = {}
    names = {}
    for line in CSV_FILE.read_text(encoding="utf-8").splitlines()[1:]:
        p = line.split(",")
        if len(p) < 7:
            continue
        t, code, name, prem = p[0], p[1], p[2], float(p[6])
        by_code.setdefault(code, {})[t] = prem  # 同一时间戳取最后一条
        names[code] = name
    groups = []
    for gname, codes in load_watchlist().items():
        series = []
        for code in codes:
            pts = sorted(by_code.get(code, {}).items())
            if pts:
                series.append({"code": code, "name": names[code], "points": pts})
        if series:
            groups.append({"name": gname, "series": series})
    if not groups:
        sys.exit("premium_log.csv 里没有监控清单内的数据")
    payload = {"groups": groups}
    html = CHART_TEMPLATE.replace("__DATA__", json.dumps(payload, ensure_ascii=False)) \
                         .replace("__GENERATED__", f"{datetime.now(TZ):%F %H:%M}")
    CHART_FILE.write_text(html, encoding="utf-8")
    total = sum(len(s["points"]) for g in groups for s in g["series"])
    print(f"已生成 {CHART_FILE}（{total} 个数据点）")


# ---------------------------------------------------------------- 表格 / watch

def load_watchlist() -> dict:
    return json.loads(FUNDS_FILE.read_text(encoding="utf-8"))


def render_table(groups: dict, rows_by_code: dict) -> str:
    lines = []
    header = f"{'代码':10} {'名称':14} {'现价':>7} {'涨跌':>7} {'净值(T-1)':>9} {'净值日期':>10} {'溢价率':>8}"
    for group, codes in groups.items():
        lines.append(f"\n== {group} ==")
        lines.append(header)
        group_rows = [rows_by_code[c] for c in codes if c in rows_by_code]
        for r in sorted(group_rows, key=lambda x: x.get("premium_pct") or -999, reverse=True):
            if "error" in r:
                lines.append(f"{r['code']:10} 获取失败: {r['error']}")
                continue
            lines.append(
                f"{r['code']:10} {r['name'][:7]:8} {r['price']:>7.3f} "
                f"{r['change_pct']:>+6.2f}% {r['nav']:>9.4f} {r['nav_date']:>10} "
                f"{r['premium_pct']:>+7.2f}%"
            )
    return "\n".join(lines)


def run_once(args) -> None:
    groups = load_watchlist()
    codes = [c for lst in groups.values() for c in lst]
    rows = fetch_all(codes)
    rows_by_code = {r["code"]: r for r in rows}

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        ok = [r for r in rows if "error" not in r]
        stamp = ok[0]["quote_time"] if ok else time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"场内基金溢价监控  行情时间: {stamp}")
        print(render_table(groups, rows_by_code))


def cmd_history(args) -> None:
    data = json.loads(http_get(
        KLINE_URL.format(symbol=args.code, scale=240, datalen=args.days), encoding="utf-8"))
    print(f"{args.code} 近 {len(data)} 个交易日：")
    prev = None
    for bar in data:
        close = float(bar["close"])
        chg = f"{(close / prev - 1) * 100:+.2f}%" if prev else "     -"
        print(f"{bar['day']}  收 {close:>7.3f}  {chg}  量 {int(bar['volume']):>12,}")
        prev = close


def main() -> None:
    parser = argparse.ArgumentParser(description="场内基金溢价监控")
    parser.add_argument("--watch", type=int, metavar="SEC", help="每 SEC 秒刷新")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    sub = parser.add_subparsers(dest="cmd")
    chk = sub.add_parser("check", help="检查阈值并推送通知（供定时任务）")
    chk.add_argument("--force", action="store_true", help="忽略交易时段与当日去重")
    sub.add_parser("test-notify", help="向已配置渠道发测试消息")
    sub.add_parser("backfill", help="用 5 分钟K线回填当天溢价数据")
    sub.add_parser("plot", help="从 premium_log.csv 生成溢价曲线 HTML")
    hist = sub.add_parser("history", help="查看日K历史")
    hist.add_argument("code", help="如 sh513100")
    hist.add_argument("--days", type=int, default=20)
    args = parser.parse_args()

    if args.cmd == "history":
        cmd_history(args)
    elif args.cmd == "check":
        cmd_check(args)
    elif args.cmd == "test-notify":
        cmd_test_notify()
    elif args.cmd == "backfill":
        cmd_backfill()
    elif args.cmd == "plot":
        cmd_plot()
    elif args.watch:
        while True:
            print("\033[2J\033[H", end="")
            try:
                run_once(args)
            except Exception as e:
                print(f"抓取失败: {e}", file=sys.stderr)
            time.sleep(args.watch)
    else:
        run_once(args)


if __name__ == "__main__":
    main()
