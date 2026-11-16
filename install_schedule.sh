#!/usr/bin/env bash
# Install a systemd user timer that runs run_weekly.sh every Monday 08:00.
# Persistent=true: if the laptop was off/asleep then, it runs at next login/boot.
#   ./install_schedule.sh            install & enable
#   ./install_schedule.sh "Mon 07:30" custom time (systemd OnCalendar syntax)
#   ./install_schedule.sh --remove   uninstall
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"
WHEN="${1:-Mon *-*-* 08:00:00}"

if [[ "${1:-}" == "--remove" ]]; then
  systemctl --user disable --now pmo-report.timer 2>/dev/null || true
  rm -f "$UNIT_DIR"/pmo-report.{service,timer}
  systemctl --user daemon-reload
  echo "Schedule removed."; exit 0
fi

mkdir -p "$UNIT_DIR"
cat > "$UNIT_DIR/pmo-report.service" <<UNIT
[Unit]
Description=PMO weekly portfolio status report
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
ExecStart=$DIR/run_weekly.sh
UNIT

cat > "$UNIT_DIR/pmo-report.timer" <<UNIT
[Unit]
Description=Run PMO status report weekly

[Timer]
OnCalendar=$WHEN
Persistent=true
RandomizedDelaySec=2min

[Install]
WantedBy=timers.target
UNIT

systemctl --user daemon-reload
systemctl --user enable --now pmo-report.timer
echo "Scheduled. Next run:"
systemctl --user list-timers pmo-report.timer --no-pager
echo
echo "Logs:     journalctl --user -u pmo-report.service"
echo "Run now:  systemctl --user start pmo-report.service"
