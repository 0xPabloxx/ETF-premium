---
name: etf-premium
description: >
  纳斯达克100/标普500 场内基金(QDII ETF)溢价监控与预警。当用户问「溢价怎么样」「纳指ETF溢价」
  「标普ETF贵不贵」「看下场内基金溢价」，或要求配置溢价通知/预警/推送（微信、邮件）、
  修改预警阈值、增删监控标的时使用。数据来自新浪财经，脚本与配置都在本 skill 目录内。
---

# 场内基金溢价监控

本 skill 目录自包含全部文件（`~/.claude/skills/etf-premium/`）：

- `monitor.py` — 核心脚本，纯 stdlib
- `funds.json` — 监控标的（代码带 sh/sz 前缀）
- `notify.json` — 预警阈值 + 通知渠道（pushplus/Server酱/企业微信 webhook/Gmail SMTP/macOS 通知）
- `state.json` — check 模式的当日去重状态（自动生成）

下文 `$DIR` 指本 SKILL.md 所在目录。

## 用法

- **查询当前溢价**：运行 `python3 $DIR/monitor.py --json`，解读时说明：哪只溢价最高/最低、
  是否高溢价（>5% 提示申购套利砸盘风险）、口径是相对 T-1 净值（非实时 IOPV）。
- **查历史走势**：`python3 $DIR/monitor.py history <code> --days N`
- **修改阈值/通知渠道**：编辑 `$DIR/notify.json`（`alert_low` 溢价回落买入提醒、
  `alert_high` 高溢价风险提醒）；改完用 `python3 $DIR/monitor.py test-notify` 验证渠道。
- **增删标的**：编辑 `$DIR/funds.json`。
- **自动预警**：launchd 定时任务 `com.etf-premium`（`$DIR/install.sh` 注册）每 10 分钟跑
  `monitor.py check`（脚本自带交易时段判断 + 当日去重）。
  排查：日志在 `~/Library/Logs/etf-premium.log`；重装直接重跑 `install.sh`。

## 注意

- 溢价率 = 场内现价 / T-1 单位净值 - 1，与交易所「溢价风险提示」口径一致；
  美股隔夜大涨/大跌当天该口径会失真，提醒用户结合纳指期货判断。
- 新浪接口需要 Referer 头，脚本已处理；行情返回 GBK 编码。
- 微信推送免费额度：pushplus 200条/天；Server酱免费版仅 5条/天且只显示标题；
  企业微信群机器人 webhook 免费不限量但消息在企业微信里。
