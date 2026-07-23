---
name: etf-premium
description: >
  纳斯达克100/标普500 场内基金(QDII ETF)溢价监控与预警。当用户问「溢价怎么样」「纳指ETF溢价」
  「标普ETF贵不贵」「看下场内基金溢价」「现在买哪只」，或要求配置溢价通知/预警/推送、
  修改预警阈值、增删监控标的、看溢价曲线时使用。数据来自新浪财经/天天基金，
  脚本与配置都在本 skill 目录内。
---

# 场内基金溢价监控

本 skill 目录自包含全部文件，`$DIR` 指本 SKILL.md 所在目录。核心脚本 `monitor.py` 纯 stdlib。

**先看 `$DIR/LOCAL.md`（如存在）**：那里是使用者的私人配置背景（实际持仓、买入习惯、
个性化信号解读），不入 git；本文件与仓库其余部分保持通用，个人相关内容一律写进 LOCAL.md。

## 用法

- **查询当前溢价**：`python3 $DIR/monitor.py --json`。解读时说明：哪只溢价最高/最低、
  是否高溢价（>5% 提示申购套利砸盘风险）、口径是相对最新公布净值（QDII 滞后 1-2 天，
  非实时 IOPV，券商 App 显示的实时估值口径会不同）。
- **买哪只（相对低估信号）**：`python3 $DIR/monitor.py pick`（备选池在 notify.json 的
  `pick`）。score = 自身偏离 + 相对同组价差偏离，最负 = 相对被低估；≤ -1 视为明显买入窗口。
  解读时强调这是相对信号，不代表绝对便宜。
- **溢价曲线**：定时任务每 5 分钟采样入 SQLite（`$DIR/premium.db`）。
  - 全部标的：`python3 $DIR/monitor.py plot [--range 1d|5d|1m|3m|6m|1y|all] [--out 路径]`
  - 单基金历史（可即时回溯数月）：`python3 $DIR/monitor.py plot --code 513500 --range 6m`
  - 输出 HTML 用 `open` 打开给用户看；当天数据有缺口先跑 `backfill`；`export` 导出 CSV。
- **修改阈值/通知渠道/备选池**：编辑 `$DIR/notify.json`；改完 `test-notify` 验证。
  支持：全局与分组阈值（`group_overrides`）、动态低点（`dynamic_low`：近 N 日新低触发
  + 月末兜底）、pick 池与分数阈值、多通知渠道（macOS 横幅/弹窗、pushplus、
  Server酱、企业微信、SMTP 邮件）。
- **增删标的**：编辑 `$DIR/funds.json`。
- **自动预警**：launchd 任务 `com.etf-premium`（`$DIR/install.sh` 注册）每 5 分钟跑
  `check`（交易时段判断 + 当月去重）。日志 `~/Library/Logs/etf-premium.log`；重装重跑 install.sh。

## 注意

- 溢价率 = 场内现价 / 最新公布净值 - 1，与交易所「溢价风险提示」口径一致；
  美股隔夜大涨/大跌后该口径失真，提醒用户结合纳指期货判断。
- 新浪接口需 Referer 头、GBK 编码；天天基金历史净值每页最多 20 条需翻页——脚本均已处理。
- 修改本目录代码后：个人化需求写 LOCAL.md / notify.json（不入库）；
  通用功能才提交推送到 GitHub（远程 origin）。
