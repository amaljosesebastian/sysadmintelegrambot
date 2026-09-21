"""Command policy for the SysAdmin Telegram bot.

classify_command(command) returns (decision, reason):

  ALLOW    read-only command from the allowlist. Runs immediately, without a shell.
  CONFIRM  anything else. Runs only after you tap "Run it" in Telegram.
  DENY     never runs, even with approval.

IMPORTANT: this is a guardrail against mistakes (typos, a small model going off the
rails). It is NOT a security boundary. A blocklist can never be complete. The real
boundary is the OS: run the bot as an unprivileged user and give it only the exact
sudoers rules listed in the README.
"""
import os
import re
import shlex

ALLOW = "allow"
CONFIRM = "confirm"
DENY = "deny"

_UNIT = r"[\w@.:-]+"
_NAME = r"[\w.-]+"

# ---------------------------------------------------------------------------
# 1. Read-only commands that may run without asking.
#    Matched against the whole command (shlex-joined), so extra flags or a different
#    argument order simply fall through to CONFIRM instead of running unreviewed.
# ---------------------------------------------------------------------------
READ_ONLY_PATTERNS = [re.compile(p) for p in (
    # basic host info
    r"uptime", r"whoami", r"date", r"nproc", r"lscpu", r"who", r"last -n \d{1,3}",
    r"uname(?: -[amrsn]+)?",
    r"hostnamectl(?: status)?",
    r"free(?: -[hmgbw]+)?",
    r"df(?: -[hHTi]+)*(?: /[\w./-]*)?",
    r"du(?: -[sh]+)+ /[\w./-]*",
    r"lsblk(?: -[fp]+)?",
    r"ps(?: (?:aux|auxf|-ef))?",
    r"top -bn1",
    r"cat /etc/os-release",
    r"cat /proc/(?:meminfo|cpuinfo|loadavg|uptime)",
    r"ls(?: -[lahtR1]+)*(?: /[\w./-]*)?",
    r"tail -n \d{1,4} /var/log/(?!.*\.\.)[\w./-]+",
    # network
    r"ss(?: -[tulpna46]+)*",
    r"ip(?: -[46br]+)? (?:a|addr|address|route|r|link|l|neigh|n)(?: show)?",
    r"ping -c [1-5] " + _NAME,
    r"tailscale status", r"tailscale ip(?: -[46])?",
    # systemd (read-only verbs only)
    r"systemctl (?:status|is-active|is-enabled|is-failed|show) " + _UNIT + r"(?: -n \d{1,3})?(?: --no-pager)?",
    r"systemctl list-(?:units|timers|unit-files)(?: --(?:type=\w+|state=\w+|failed|all|no-pager))*",
    r"systemctl --failed(?: --no-pager)?",
    r"systemctl is-system-running",
    r"journalctl(?: -u " + _UNIT + r")? -n \d{1,4}(?: -p [a-z0-9]+)?(?: --no-pager)?",
    # containers
    r"docker (?:ps|images)(?: -a)?",
    r"docker logs(?: --tail \d{1,4})? " + _NAME,
    r"docker stats --no-stream",
    r"docker system df",
    r"ollama (?:list|ps)",
    # packages (read-only)
    r"dnf check-update(?: -q)?",
    r"dnf (?:list (?:installed|updates|available)|repolist)",
    r"dnf info [\w.+-]+",
    r"rpm -q [\w.+-]+",
)]

# ---------------------------------------------------------------------------
# 2. sudo: only these exact command lines, nothing else. Must mirror sudoers.
# ---------------------------------------------------------------------------
_BASE_SUDO = ("sudo dnf check-update -q", "sudo dnf upgrade -y", "sudo reboot")


def allowed_sudo_commands(restart_units=()) -> set:
    """Exact sudo command lines the bot may use (restart_units come from RESTART_UNITS in .env)."""
    cmds = set(_BASE_SUDO)
    for unit in restart_units:
        if re.fullmatch(_UNIT, unit):
            cmds.add(f"sudo systemctl restart {unit}")
    return cmds


# ---------------------------------------------------------------------------
# 3. Hard denials: never run, even with approval.
# ---------------------------------------------------------------------------
_PROTECTED = (r"(?:/|/\*|~|~/|~/\*|\$HOME|\$HOME/\*|"
              r"/(?:bin|boot|dev|etc|home|lib|lib64|opt|proc|root|run|sbin|srv|sys|usr|var)(?:/\*?)?)")
_RECURSIVE = r"(?=.*\s(?:-\w*[rR]\w*|--recursive)(?:\s|$))"
_ON_PROTECTED = r"(?=.*\s" + _PROTECTED + r"(?:\s|$))"
_POWER = "power operations are only available through /reboot"

# Matched per simple command (after splitting on ; & | ( ) and unwrapping env/nice/etc.)
_DENY = [(re.compile(p, re.I), why) for p, why in (
    (r"^rm\b" + _RECURSIVE + _ON_PROTECTED, "recursive delete of a system or home directory"),
    (r"^find\s+(?:/|~|\$HOME)(?:\s|$).*\s-delete\b", "find -delete starting at a system root"),
    (r"^(?:chmod|chown|chgrp)\b" + _RECURSIVE + _ON_PROTECTED, "recursive permission change on a system or home path"),
    (r"^mkfs(?:\.\w+)?\b", "creating a filesystem destroys data"),
    (r"^(?:wipefs|fdisk|sfdisk|sgdisk|gdisk|cfdisk|parted|shred|blkdiscard|cryptsetup|lvremove|vgremove|pvremove|mdadm)\b",
     "disk partitioning and wiping tools are blocked"),
    (r"^dd\b.*\bof=/dev/", "writing directly to a device"),
    (r"^(?:shutdown|poweroff|halt|reboot|telinit)\b", _POWER),
    (r"^init\s+[06]\b", _POWER),
    (r"^systemctl\s+(?:poweroff|halt|reboot|kexec|suspend|hibernate|hybrid-sleep)\b", _POWER),
    (r"^systemctl\s+(?:stop|disable|mask|kill)\s+(?:sshd|ssh|NetworkManager|systemd-networkd|firewalld)(?:\.service)?\b",
     "could lock you out of the server"),
    (r"^(?:ifdown\b|ip\s+link\s+set\b.*\bdown\b|nmcli\b.*\b(?:down|delete|disconnect)\b)", "could take the network down"),
    (r"^(?:iptables\s+(?:-F|--flush)\b|nft\s+flush\b|firewall-cmd\b.*--panic-on|ufw\s+(?:disable|reset)\b)",
     "flushing or disabling the firewall"),
    (r"^(?:passwd|chpasswd|usermod|useradd|userdel|groupadd|groupdel|groupmod|visudo|su|chsh|pkexec|setcap|setfacl)\b",
     "account and privilege management is not available through the bot"),
    (r"^(?:setenforce\s+0|grubby|grub2-\w+|dracut)\b", "boot and SELinux changes are blocked"),
    (r"^kill\s+(?:-\S+\s+)*(?:--\s+)?-?1\s*$", "signals PID 1 or every process"),
    (r"^docker\s+(?:run|create|exec)\b(?=.*(?:--privileged|--pid[= ]host|--userns[= ]host|\s-v\s*/:|\s--volume[= ]/:|\bsource=/(?:,|\s|$)))",
     "privileged or host-mounting container (equivalent to root on the host)"),
    (r"^docker\s+(?:volume\s+(?:rm|prune)\b|system\s+prune\b.*--volumes)", "deletes Docker volumes (data loss)"),
    (r"\b(?:drop\s+(?:database|table|schema)|truncate\s+table)\b", "destructive SQL statement"),
)]

# Matched against the raw command line (these involve operators the tokenizer strips).
_RAW_DENY = [(re.compile(p, re.I), why) for p, why in (
    (r":\s*\(\s*\)\s*\{", "fork bomb"),
    (r">\s*/dev/(?:sd|nvme|vd|hd|xvd|mmcblk|dm-|mapper/)", "writing directly to a disk device"),
    (r"(?:>|\btee\b)[^|;&]*/etc/(?:passwd|shadow|group|sudoers|fstab|ssh/)", "modifying authentication or boot configuration"),
    (r"\b(?:curl|wget)\b.*\|\s*(?:sudo\s+)?(?:ba|z|da|k)?sh\b", "piping a download into a shell"),
    (r"\b(?:base64|xxd)\b.*\|\s*(?:sudo\s+)?(?:ba|z|da|k)?sh\b", "piping decoded data into a shell"),
)]

_WRAPPERS = {"env", "nice", "nohup", "time", "command", "exec", "setsid", "stdbuf", "ionice", "timeout", "chrt", "xargs"}
_SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "fish"}
_UNSAFE_FOR_ALLOW = re.compile(r"[;&|<>`\\\n\r$*?\[\]{}~()]")


def _normalize(command: str) -> str:
    # Collapse spaces/tabs but keep newlines: they act as command separators in a shell.
    return re.sub(r"[ \t]+", " ", command.strip())


def _segments(command: str):
    """Split a command line into simple commands (argv lists).

    Splits on ; & | < > ( ) newlines, backticks and $(...). Over-approximates on purpose,
    so anything a shell might run as a separate command is inspected. Returns None if the
    command cannot be parsed (e.g. unbalanced quotes).
    """
    text = command.replace("`", ";").replace("$(", ";").replace("\n", ";").replace("\r", ";")
    try:
        lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = ""  # a '#' inside a word is NOT a comment in bash; don't let it hide the rest
        tokens = list(lexer)
    except ValueError:
        return None
    segments, current = [], []
    for tok in tokens:
        if set(tok) <= set(";&|<>()"):
            if current:
                segments.append(current)
            current = []
        else:
            current.append(tok)
    if current:
        segments.append(current)
    return segments


def _unwrap(argv):
    """Drop leading wrappers (env, nice -n 10, VAR=x, timeout 5 ...) to find the real program."""
    i = 0
    while i < len(argv):
        tok = argv[i]
        if (os.path.basename(tok) in _WRAPPERS or tok.startswith("-")
                or re.fullmatch(r"[A-Za-z_]\w*=.*", tok) or re.fullmatch(r"\d+[smhd]?", tok)):
            i += 1
        else:
            break
    return argv[i:]


def _deny_reason(command: str, depth: int = 0):
    for pattern, why in _RAW_DENY:
        if pattern.search(command):
            return why
    segments = _segments(command)
    if segments is None:
        return "could not parse the command (check your quoting)"
    for argv in segments:
        argv = _unwrap(argv)
        if not argv:
            continue
        prog = os.path.basename(argv[0])
        seg = " ".join([prog] + argv[1:])
        for pattern, why in _DENY:
            if pattern.search(seg):
                return why
        if depth < 3 and prog in _SHELLS:  # bash -c '...' : inspect the inner command too
            for i, arg in enumerate(argv[1:], 1):
                if re.fullmatch(r"-\w*c\w*", arg) and i + 1 < len(argv):
                    inner = _deny_reason(argv[i + 1], depth + 1)
                    if inner:
                        return inner
    return None


def simple_argv(command: str):
    """argv for a plain command (no operators, globs, variables, substitution), else None.
    Plain commands are executed WITHOUT a shell, so what was checked is exactly what runs."""
    if _UNSAFE_FOR_ALLOW.search(command):
        return None
    try:
        argv = shlex.split(command)
    except ValueError:
        return None
    return argv or None


def classify_command(command: str, restart_units=()):
    """Return (ALLOW | CONFIRM | DENY, human-readable reason)."""
    cmd = _normalize(command)
    if not cmd:
        return DENY, "empty command"

    if re.search(r"\bsudo\b", cmd):
        if cmd not in allowed_sudo_commands(restart_units):
            return DENY, "sudo is limited to a fixed list of commands (see README)"
        if cmd == "sudo reboot":
            return DENY, _POWER
        if cmd == "sudo dnf check-update -q":
            return ALLOW, "read-only update check"
        return CONFIRM, "this changes the system"

    why = _deny_reason(cmd)
    if why:
        return DENY, why

    argv = simple_argv(cmd)
    if argv and any(p.fullmatch(shlex.join(argv)) for p in READ_ONLY_PATTERNS):
        return ALLOW, "read-only command"
    return CONFIRM, "not on the read-only list"