#!/bin/bash
# 一键安装：初始化通知配置 + 注册 launchd 定时预警（macOS）
# 用法：把本目录放到 ~/.claude/skills/etf-premium/ 后执行 ./install.sh
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.etf-premium"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/etf-premium.log"

# 通知配置：不存在才从模板复制，绝不覆盖已有配置
if [ ! -f "$DIR/notify.json" ]; then
  cp "$DIR/notify.example.json" "$DIR/notify.json"
  echo "已生成 notify.json，请编辑填入微信/邮件凭证（macOS 通知默认已开）"
fi

if [ "$(uname)" = "Darwin" ]; then
  mkdir -p "$HOME/Library/LaunchAgents"
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>$DIR/monitor.py</string>
        <string>check</string>
    </array>
    <key>StartInterval</key>
    <integer>300</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$LOG</string>
    <key>StandardErrorPath</key>
    <string>$LOG</string>
</dict>
</plist>
EOF
  UID_N="$(id -u)"
  # 现代方式注册（老式 load 在新版 macOS 上定时器可能不生效），失败再退回 load
  launchctl bootout "gui/$UID_N/$LABEL" 2>/dev/null || true
  launchctl unload "$PLIST" 2>/dev/null || true
  launchctl bootstrap "gui/$UID_N" "$PLIST" 2>/dev/null || launchctl load "$PLIST"
  launchctl kickstart "gui/$UID_N/$LABEL" 2>/dev/null || true
  echo "launchd 定时任务 ${LABEL} 已加载（每 5 分钟检查，日志 ${LOG}）"
  echo "卸载：launchctl unload ${PLIST} && rm ${PLIST}"
else
  echo "非 macOS：请自行添加 crontab，例如："
  echo "*/10 * * * 1-5 /usr/bin/python3 ${DIR}/monitor.py check >> ${LOG} 2>&1"
fi

echo "验证数据抓取： python3 ${DIR}/monitor.py"
echo "验证通知渠道： python3 ${DIR}/monitor.py test-notify"
