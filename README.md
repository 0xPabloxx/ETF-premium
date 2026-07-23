# ETF Premium Monitor · 场内基金溢价监控

Monitor the premium of China-listed NASDAQ-100 / S&P 500 QDII ETFs over their NAV, with automatic alerts (WeChat / Email / macOS) when the premium falls back (buy opportunity) or spikes (risk). Pure Python stdlib, zero dependencies. Also works as a [Claude Code skill](https://code.claude.com/docs/en/skills).

监控 A 股场内纳斯达克100 / 标普500 QDII ETF 相对基金净值的溢价率，溢价回落（买入机会）或冲高（风险）时自动推送微信 / 邮件 / macOS 通知。纯 Python 标准库，零依赖，可兼作 Claude Code skill。

```
== 纳斯达克100 ==
代码         名称          现价     涨跌    净值(T-1)   净值日期      溢价率
sh513100   纳指ETF国泰    2.130  +1.62%    1.9381   2026-07-20   +9.90%
sz159941   纳指ETF       1.589  +2.45%    1.4522   2026-07-20   +9.42%
```

## How it works · 原理

- Realtime price / 场内实时价：`hq.sinajs.cn/list=sh513100`
- Fund NAV (T-1) / 基金净值：`hq.sinajs.cn/list=f_513100`
- Daily K-line / 日K线：`CN_MarketData.getKLineData`
- **Premium 溢价率 = price / NAV(T-1) − 1** — same formula as the exchanges' official premium-risk announcements · 与交易所溢价风险提示公告同口径

## Install · 安装

```bash
# As a Claude Code skill (optional) · 作为 Claude Code skill（可选）
git clone https://github.com/0xPabloxx/ETF-premium.git ~/.claude/skills/etf-premium
cd ~/.claude/skills/etf-premium && ./install.sh

# Or standalone, any directory · 或独立使用，clone 到任意目录即可
```

`install.sh` creates your private `notify.json` and registers a launchd job (macOS) that checks every 10 minutes during A-share trading hours; on Linux it prints an equivalent crontab line.

`install.sh` 会生成私人配置 `notify.json` 并注册 launchd 定时任务（macOS，A 股交易时段内每 10 分钟检查一次）；Linux 会输出等价的 crontab。

## Usage · 用法

```bash
python3 monitor.py                    # premium table · 一次性溢价表
python3 monitor.py --watch 60         # live refresh · 终端常驻刷新
python3 monitor.py --json             # machine-readable · JSON 输出
python3 monitor.py check              # cron entry: check thresholds & push · 定时任务入口
python3 monitor.py test-notify        # test notification channels · 测试通知渠道
python3 monitor.py history sh513100 --days 20
python3 monitor.py backfill           # backfill today's curve from 5-min bars · 用5分钟K线回填当天数据
python3 monitor.py plot [--range 5d]  # watchlist curves from local DB · 全部标的溢价曲线网页
python3 monitor.py plot --code 513500 --range 6m   # one fund, months back · 单基金历史溢价曲线
python3 monitor.py export             # dump DB to CSV · 导出全部数据
```

## Data storage · 数据存储

Samples live in **SQLite** (`premium.db`, stdlib, zero setup) — designed for month/quarter/year horizons: primary key (time, code) dedupes automatically, range queries stay fast for years of data (~110k rows/yr). The cron `check` writes every sample; a legacy `premium_log.csv` is auto-imported on first run; `export` dumps everything back to CSV.

采样数据存 **SQLite**（`premium.db`，标准库自带零配置），面向月/季/年长期积累：主键 (时间,代码) 自动去重，按时间范围查询常年保持索引级速度（每年约 11 万行）。定时任务每次采样入库；旧 CSV 首次运行自动导入；`export` 可整库导出 CSV。

`plot` renders a self-contained interactive HTML chart (light/dark, hover crosshair, data table). Resolution adapts to range: ≤8 trading days → raw 5-min samples; longer → daily closing premium. `--code` mode reconstructs **months of history immediately** from daily K-line + NAV history (Eastmoney), no accumulation needed.

`plot` 生成自包含交互式网页（明暗自适应、悬停十字线、数据表）。分辨率随范围自适应：≤8 个交易日用 5 分钟原始采样，更长自动切日线（每日收盘溢价）。`--code` 单基金模式用日K + 天天基金历史净值**即时回溯数月**，无需等待本地积累。

Watchlist lives in `funds.json` (codes with sh/sz prefix). 监控标的在 `funds.json` 配置（代码带 sh/sz 前缀）。

## Alerts · 预警配置（notify.json）

Thresholds · 阈值：`alert_low` — notify when premium ≤ this (buy opportunity, default 3%) · 溢价回落买入提醒；`alert_high` — notify when ≥ this (risk, default 10%) · 高溢价风险提醒。Each fund+condition alerts at most once per day. 同一基金同一条件每天最多提醒一次。

| Channel 渠道 | Setup 配置 | Notes 说明 |
|---|---|---|
| `macos` | on by default · 默认开启 | Local notification · 本机通知 |
| `pushplus_token` | token from [pushplus.plus](https://www.pushplus.plus/) | **Recommended · 推荐**，free 200 msg/day to WeChat · 免费 200 条/天直达微信 |
| `wecom_webhook` | WeCom group bot webhook · 企业微信群机器人 | Free & unlimited · 免费不限量 |
| `serverchan_sendkey` | SendKey from [sct.ftqq.com](https://sct.ftqq.com/) | Free tier only 5/day · 免费版仅 5 条/天 |
| `email` | Gmail + [app password](https://myaccount.google.com/apppasswords) | Any SMTP works · 任意 SMTP 均可 |

Verify with · 填好后验证：`python3 monitor.py test-notify`

## Caveats · 已知局限

- NAV is T-1: on days after a big overnight US move, this premium metric is systematically biased — cross-check with NASDAQ futures. A true realtime premium needs IOPV (NAV × index futures × FX), planned.
- 净值是 T-1 的：美股隔夜大涨/大跌次日该口径会系统性偏差，需结合纳指期货判断。实时口径需要 IOPV（净值 × 指数期货 × 汇率），待接入。
- Sina endpoints require a `Referer` header and return GBK — already handled. 新浪接口需 Referer 头、GBK 编码，脚本已处理。

## Disclaimer · 免责声明

For information only, not investment advice. 仅供参考，不构成投资建议。
