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
