# 场内基金溢价监控（skill 包）

监控纳斯达克100 / 标普500 场内基金（QDII ETF）相对基金净值的溢价率，低溢价（买入机会）/ 高溢价（风险）自动推送微信、邮件、macOS 通知。数据来自新浪财经，纯 Python 标准库，无依赖。

本目录同时是 Claude Code 的用户级 skill（`~/.claude/skills/etf-premium/`），在任何会话里问「溢价怎么样」即可触发。

## 安装

```bash
git clone https://github.com/<owner>/etf-premium-monitor.git ~/.claude/skills/etf-premium
cd ~/.claude/skills/etf-premium && ./install.sh
```

`install.sh` 会生成 `notify.json`（编辑它填入微信/邮件凭证）并注册 launchd 定时预警；非 macOS 会给出等价的 crontab。不用 Claude Code 也可以，clone 到任意目录直接跑 `monitor.py`，skill 只是可选的会话入口。

## 原理

- 场内实时价：`https://hq.sinajs.cn/list=sh513100`（需 `Referer: finance.sina.com.cn`，GBK）
- 基金净值：同接口 `f_` 前缀（`list=f_513100`），返回 T-1 单位净值和日期
- 日K线：`CN_MarketData.getKLineData?symbol=sh513100&scale=240&datalen=N`
- **溢价率 = 场内现价 / T-1 单位净值 − 1**，与交易所溢价风险提示公告口径一致

## 用法

```bash
python3 monitor.py                    # 一次性溢价表
python3 monitor.py --watch 60         # 终端常驻刷新
python3 monitor.py --json             # JSON 输出
python3 monitor.py check              # 定时任务入口：交易时段内查阈值并推送
python3 monitor.py check --force      # 强制检查（忽略时段/当日去重）
python3 monitor.py test-notify        # 测试所有已配置通知渠道
python3 monitor.py history sh513100 --days 20
```

## 自动预警

launchd 定时任务 `com.etf-premium`（由 `install.sh` 注册）每 10 分钟跑一次 `check`：

- 脚本自带 A 股交易时段判断（9:25–11:35 / 12:55–15:10，Asia/Shanghai），盘外直接跳过
- 同一基金同一条件（低/高溢价）**每天只提醒一次**，不刷屏
- 日志：`~/Library/Logs/etf-premium.log`
- 卸载：`launchctl unload ~/Library/LaunchAgents/com.etf-premium.plist`

## 通知渠道配置（notify.json）

| 渠道 | 填什么 | 说明 |
|---|---|---|
| `macos` | true/false | 本机通知，默认开 |
| `pushplus_token` | [pushplus.plus](https://www.pushplus.plus/) 微信扫码登录后复制 token | **推荐**，免费 200 条/天，消息进微信公众号 |
| `wecom_webhook` | 企业微信建群 → 添加群机器人 → 复制 webhook 地址 | 完全免费，消息在企业微信 |
| `serverchan_sendkey` | [sct.ftqq.com](https://sct.ftqq.com/) 的 SendKey | 免费版仅 5 条/天且只显示标题，不推荐 |
| `email` | Gmail 地址 + [应用专用密码](https://myaccount.google.com/apppasswords)（需开两步验证） | `user`/`app_password` 填发信账号，`to` 已填你的邮箱 |

填好后跑 `python3 monitor.py test-notify` 验证。

阈值：`alert_low`（溢价 ≤ 此值提醒买入机会，默认 3%）、`alert_high`（≥ 此值提醒风险，默认 10%）。

## 已知局限 / TODO

- 净值是 T-1 的：美股隔夜大涨/大跌当天该口径会系统性偏差，需结合纳指期货判断。
  更准的实时溢价需要 IOPV（T-1 净值 × 指数期货实时涨跌 × 汇率），待接入。
- 历史溢价曲线需要净值历史序列，新浪 `f_` 只给最新一期，待找净值历史接口。
