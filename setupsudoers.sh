#!/usr/bin/env bash
# Installs the sudoers rule the SysAdmin Telegram bot needs, with the right owner, mode and paths.
#
# Usage:  sudo bash ./setup-sudoers.sh <bot-user> [unit.service ...]
#         bash ./setup-sudoers.sh --print <bot-user> [unit.service ...]    (preview only, no root needed)
#
# Grants passwordless sudo for these EXACT commands and nothing else:
#   dnf check-update -q  |  dnf upgrade -y  |  reboot  |  systemctl restart <unit>  (one per unit you list)
# List the same units in RESTART_UNITS in your .env file.
set -euo pipefail

TARGET=/etc/sudoers.d/sysadmin-bot
# Same search path sudo uses, so the paths written here match what sudo will resolve.
SEARCH_PATH=${SUDOERS_BIN_PATH:-/usr/sbin:/usr/bin:/sbin:/bin}

usage() { sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 1; }

PRINT_ONLY=0
if [[ "${1:-}" == "--print" ]]; then PRINT_ONLY=1; shift; fi
[[ $# -ge 1 && "$1" != -* ]] || usage
BOT_USER=$1; shift
UNITS=("$@")

[[ $BOT_USER =~ ^[a-z_][a-z0-9_-]*$ ]] || { echo "Invalid user name: $BOT_USER" >&2; exit 1; }
for u in "${UNITS[@]}"; do
  [[ $u =~ ^[A-Za-z0-9@._:-]+$ ]] || { echo "Invalid unit name: $u" >&2; exit 1; }
done

if [[ $PRINT_ONLY -eq 0 ]]; then
  [[ $EUID -eq 0 ]] || { echo "Run as root:  sudo bash $0 $BOT_USER ${UNITS[*]:-}" >&2; exit 1; }
  id "$BOT_USER" >/dev/null 2>&1 || { echo "No such user: $BOT_USER" >&2; exit 1; }
fi

find_bin() {
  local p
  p=$(PATH="$SEARCH_PATH" command -v "$1") || { echo "Cannot find '$1' in $SEARCH_PATH" >&2; exit 1; }
  echo "$p"
}

DNF=$(find_bin dnf)
REBOOT=$(find_bin reboot)
CMDS=("$DNF check-update -q" "$DNF upgrade -y" "$REBOOT")
if ((${#UNITS[@]})); then
  SYSTEMCTL=$(find_bin systemctl)
  for u in "${UNITS[@]}"; do CMDS+=("$SYSTEMCTL restart $u"); done
fi

build_rule() {
  echo "# Managed by setup-sudoers.sh: the exact commands used by the SysAdmin Telegram bot."
  echo "# Re-run the script to change this file. Do not add wildcards or extra arguments."
  echo "Cmnd_Alias SYSADMIN_BOT = \\"
  local i last=$((${#CMDS[@]} - 1))
  for i in "${!CMDS[@]}"; do
    if ((i < last)); then echo "    ${CMDS[$i]}, \\"; else echo "    ${CMDS[$i]}"; fi
  done
  echo
  echo "$BOT_USER ALL=(root) NOPASSWD: SYSADMIN_BOT"
}

RULE=$(build_rule)

if [[ $PRINT_ONLY -eq 1 ]]; then
  echo "$RULE"
  exit 0
fi

TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT
printf '%s\n' "$RULE" > "$TMP"

# Validate BEFORE installing, so a typo can never break sudo.
if ! visudo -cf "$TMP" >/dev/null; then
  echo "The generated rule failed validation. Nothing was installed." >&2
  exit 1
fi
install -o root -g root -m 0440 "$TMP" "$TARGET"
echo "Installed $TARGET (owner root, mode 0440)."

if ! visudo -c; then
  echo "WARNING: visudo reported problems in other sudoers files (see above). Fix those too." >&2
fi

echo
echo "Checking what '$BOT_USER' can run without a password:"
fail=0
for c in "${CMDS[@]}"; do
  # shellcheck disable=SC2086  # intentional word splitting: command + arguments
  if sudo -u "$BOT_USER" sudo -n -l $c >/dev/null 2>&1; then
    echo "  OK       $c"
  else
    echo "  MISSING  $c"; fail=1
  fi
done

# Regression check: anything broader than the rule above means a leftover rule is still active.
if sudo -u "$BOT_USER" sudo -n -l "$DNF" remove nano >/dev/null 2>&1; then
  echo "  PROBLEM  '$BOT_USER' can run arbitrary dnf commands without a password."
  echo "           Another sudoers rule is too broad. NOPASSWD rules found:"
  grep -rn NOPASSWD /etc/sudoers /etc/sudoers.d/ 2>/dev/null | grep -v "$TARGET" | sed 's/^/             /' || true
  fail=1
else
  echo "  OK       arbitrary dnf commands are refused"
fi

echo
if ((fail)); then
  echo "Some checks failed. Fix the items above, then re-run this script."
else
  echo "All good. The bot's upgrade and reboot buttons will work without a password."
fi
exit "$fail"