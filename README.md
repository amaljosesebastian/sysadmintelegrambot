# SysAdmin AI Telegram Bot

A personal AI assistant for Linux server administration that runs in Telegram. It connects natural-language messages to your local **Ollama** instance, runs commands on the host under a safety policy, watches the server in the background, and reports to you when something needs attention.

**Optimized for RHEL-based distributions (Fedora, Rocky Linux, CentOS Stream) that use `dnf`.**

## Features

* **Direct Ollama integration:** No middleman or extra API keys. Talks straight to Ollama's `/api/chat` endpoint.
* **AI chat & troubleshooting:** Ask questions in plain English; the model can propose a command and summarize its output.
* **Command policy:** Every command from `/exec` or the AI model is *read-only* (runs at once), *needs approval* (you tap a button), or *blocked*. See [How commands are handled](#how-commands-are-handled).
* **Autonomous monitoring:** A weekly report every Monday evening, plus health checks at random intervals that message you only when something is wrong. See [Scheduled tasks](#scheduled-tasks).
* **Memory that survives reboots:** Conversation history, notes you save, and alert state live in a local SQLite file. See [How the bot remembers things](#how-the-bot-remembers-things).
* **One-tap updates:** Review pending `dnf` updates and install them from Telegram, with a reboot prompt after a kernel update.
* **Locked to one chat:** The bot ignores every Telegram chat except `ALLOWED_CHAT_ID`.

## Commands

| Command | Description |
|---|---|
| `/start` | Show the command menu |
| `/status` | Uptime, memory and root disk usage |
| `/health` | Run every health check now |
| `/tasks` | Show when the next scheduled tasks will run |
| `/ram`, `/storage`, `/docker`, `/services`, `/logs` | Quick dashboards |
| `/exec <cmd>` | Run a command, subject to the command policy |
| `/update` | Check for `dnf` updates, with upgrade/cancel buttons |
| `/reboot` | Reboot the host, with confirm/cancel buttons |
| `/remember <fact>` | Save a note the bot will know in every future conversation |
| `/notes` | List saved notes |
| `/forget <id>` | Delete a saved note |
| `/reset` | Clear the conversation history (notes are kept) |

Any other text message is sent to the Ollama model.

## Scheduled tasks

These run on their own once the bot is up. They need the job-queue extra (`pip install "python-telegram-bot[job-queue]"`); without it the bot still works and logs a warning.

| Task | When | What it does |
|---|---|---|
| **Weekly report** | Mondays at 19:00 (`REPORT_TIME`) in the server's timezone | Sends pending updates (package names, kernel warning), open health issues and a system snapshot. If updates are pending it includes the **Proceed with Upgrade** button. |
| **Health sweep** | At **random** intervals of 20 to 60 minutes (`HEALTH_MIN_MINUTES` / `HEALTH_MAX_MINUTES`), first run one minute after start | Runs all checks below and messages you **only about changes**. |
| **Boot notice** | 20 seconds after the bot starts | If the whole server rebooted in the last 10 minutes, tells you. This catches unexpected reboots such as power loss. |

**What the health sweep checks**

| Check | Alerts when |
|---|---|
| Docker containers | a container crashed (non-zero exit), is restarting or dead, or is unhealthy. Alerts include a **Restart** button. Also alerts if the Docker daemon isn't responding. |
| systemd | any unit is in the `failed` state |
| Disk | any filesystem is at 85% or more (`DISK_ALERT_PERCENT`) |
| Memory | available memory is below 10% (`MEM_ALERT_PERCENT`) |
| Load | the 15-minute load average is above twice the CPU core count |
| Ollama | the bot can't reach Ollama, so AI chat would fail |

**How alerts behave.** The sweep never repeats itself:

1. A **new** problem is announced once.
2. If it is **still open** after 24 hours you get a single reminder, then again each day.
3. When it clears you get a **Recovered** message.
4. If a check itself can't run (for example Docker is down), existing alerts are left alone rather than wrongly marked as recovered.

Which problems have already been announced is stored in SQLite, so restarting or rebooting the bot does not re-announce known problems.

**Timezone.** The schedule uses the server's timezone, read from `/etc/localtime`. Check yours with `timedatectl`. Override it with `BOT_TIMEZONE` (for example `BOT_TIMEZONE=America/Toronto`). `/tasks` shows the exact next run times.

**Why random intervals?** The delay before each sweep is picked at random. This is what was asked for, but note the trade-off: a fixed interval gives a predictable worst-case time to notice a failure, while random gaps average out to the same coverage. Narrow the range (say 10 to 20 minutes) if you want faster detection.

## How the bot remembers things

**The AI model has no memory of its own.** Ollama treats every request as brand new. Anything the bot "remembers" is stored by the bot's own code in a local SQLite database (`bot_memory.db`) and sent to the model again with each request. The model never writes to memory itself: only the bot's code and your `/remember` command do. That is deliberate, so text from logs or a confused model can't plant permanent "facts".

There are three kinds of memory:

| Memory | Table | What is stored | Used how | Cleared by |
|---|---|---|---|---|
| **Conversation history** (short-term) | `history` | Your messages and the bot's replies, including summaries of command output | The **last 10 messages** are sent with every request so follow-ups make sense. Only the newest 500 per chat are kept. | `/reset` |
| **Notes** (long-term) | `notes` | Facts you save with `/remember`, for example `/remember Caddy reverse-proxies Open WebUI on port 3000` | **All notes** are added to the system prompt on every request, so the bot always knows them, even after `/reset` or a reboot. Max 50 notes of 300 characters. | `/forget <id>` |
| **Alert state** | `alerts` | Which health problems were already announced, and when | Never sent to the model. Used only to avoid repeating alerts. | Automatically, when a problem recovers |

**What happens when you send a message**

1. Your message is saved to `history`.
2. The bot builds the request: system prompt + your notes + the last 10 messages.
3. Ollama replies, and the reply is saved to `history`.
4. After a reboot the same database is read again, so the conversation picks up where it stopped.

**What is not remembered**

* Pending approval buttons live in RAM and expire after 10 minutes. After a restart an old button says it expired.
* Raw command output is not stored, only the model's summary of it.
* Scheduled timers are recalculated at startup (for example "next Monday at 19:00").

**Limits and tips**

* Keep notes short and few. They are sent every time and take space in the model's context window. The bot sets the window to 8192 tokens (`OLLAMA_NUM_CTX`) because Ollama's default is small and silently drops input that doesn't fit.
* A small model such as `llama3.2:3b` has a limited ability to use a long history. If answers drift, `/reset` clears history without touching notes.
* The database can contain command output, so it is created with mode `600`. Keep `bot_memory.db` out of git (add it to `.gitignore`) and out of shared backups.
* Inspect it with `sqlite3 bot_memory.db "SELECT * FROM notes;"`. To wipe everything, stop the bot and delete the file.

## How commands are handled

Commands typed with `/exec`, or proposed by the AI model, pass through `commandpolicy.py`:

| Result | What happens | Examples |
|---|---|---|
| **Read-only** | Runs immediately, without a shell | `df -h`, `free -h`, `docker ps`, `docker logs --tail 50 caddy`, `systemctl status caddy`, `journalctl -u caddy -n 50 --no-pager`, `ss -tulpn`, `ip -br a`, `sudo dnf check-update -q` |
| **Needs approval** | The bot shows the exact command with **Run it / Cancel** buttons. Buttons are single-use and expire after 10 minutes. | `docker restart caddy`, `sudo systemctl restart caddy.service`, `sudo dnf upgrade -y`, anything with pipes, redirects or `;`, anything not on the read-only list |
| **Blocked** | Never runs, even with approval | recursive deletes of system or home paths, `mkfs`/`fdisk`/`dd of=/dev/...`, `shutdown`/`reboot` (use `/reboot`), `sudo` outside the fixed list, account management (`passwd`, `usermod`, ...), stopping `sshd`/network/firewall, privileged or host-mounting containers, `curl ... \| sh`, `DROP DATABASE`, and similar |

* The read-only list is a set of exact patterns in `commandpolicy.py`. A different flag order simply falls through to *needs approval*.
* The AI model handles **one command per reply**. Blocked commands are reported to you and never retried.
* The bot's own background checks and dashboards run fixed, built-in commands and skip this policy.
* Every executed command is logged (`journalctl -u sysadmin-bot`).
* Run `python3 test_command_policy.py` after editing the policy.

## Prerequisites

1. **Python 3.10+**
2. **Telegram bot token**: create one via [@BotFather](https://t.me/BotFather).
3. **Your Telegram chat ID**: get it via [@userinfobot](https://t.me/userinfobot). Use your private chat ID, not a group's (see [Security](#security--safeguards)).
4. **Ollama** running locally with a tool-calling capable model (e.g. `llama3.2:3b` or `llama3.1:8b`).
5. **Passwordless sudo** for a few exact commands (see [Sudo setup](#sudo-setup)).

## Creating your Telegram Bot & Getting Credentials

Before configuring your environment, you need a Telegram Bot Token and your private Chat ID.

### 1. Create a Bot and Get Your Token (`TELEGRAM_TOKEN`)
1. Open Telegram and search for the official **[BotFather](https://t.me/BotFather)**.
2. Send the command `/newbot` and follow the prompts:
   - Provide a friendly display name for your bot (e.g., `My SysAdmin Bot`).
   - Choose a unique username ending in `bot` or `_bot` (e.g., `my_server_sys_bot`).
3. BotFather will provide an HTTP **API Token** (a string like `123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ`). Copy this down for `TELEGRAM_TOKEN` in your `.env`.

### 2. Get Your Private Chat ID (`ALLOWED_CHAT_ID`)
Because your bot manages server infrastructure, it must be locked to your personal account so no one else can interact with it.
1. Search for **[@userinfobot](https://t.me/userinfobot)** on Telegram.
2. Send it any message (like `/start`).
3. The bot will reply with your user details, including your **Id** (a sequence of numbers like `0123456789`). Copy this number for `ALLOWED_CHAT_ID` in your `.env`.

## Installation

1. **Clone the repository:**

```bash
   git clone [https://github.com/amaljosesebastian/sysadmintelegrambot.git](https://github.com/amaljosesebastian/sysadmintelegrambot.git)
   cd sysadmintelegrambot
   ```

2. **Install dependencies:**

   ```bash
   pip install -r requirements.txt
   ```

   `requirements.txt` should contain `python-telegram-bot[job-queue]` (v20 or newer), `python-dotenv` and `requests`. Keep `telegrambridge.py`, `commandpolicy.py` and `setup-sudoers.sh` together in the project folder.

3. **Configure the environment:**

   ```bash
   cp .env.example .env
   chmod 600 .env
   nano .env
   ```

   > The `.env` file holds your bot token and is listed in `.gitignore`, so it should never be committed. Add `bot_memory.db` to `.gitignore` too.

4. **Set up sudo** (see [Sudo setup](#sudo-setup)).

5. **Test it in the foreground:**

   ```bash
   python3 telegrambridge.py
   ```

   Send `/start` to your bot. Then send `/tasks` to confirm the schedule.

## Configuration

| Variable | Required | Default | Description |
|---|---|---|---|
| `TELEGRAM_TOKEN` | Yes | none | Bot token from @BotFather |
| `ALLOWED_CHAT_ID` | Yes | none | The only chat ID the bot will respond to |
| `OLLAMA_URL` | No | `http://127.0.0.1:11434` | Base URL of your Ollama server |
| `DEFAULT_MODEL` | No | `llama3.2:3b` | Ollama model used for chat |
| `OLLAMA_NUM_CTX` | No | `8192` | Context window (tokens) requested from Ollama |
| `RESTART_UNITS` | No | empty | Comma-separated systemd units the bot may restart with approval, e.g. `caddy.service,tailscaled.service`. Pass the same units to `setup-sudoers.sh`. |
| `DB_PATH` | No | `bot_memory.db` next to the script | Where the SQLite file is stored |
| `BOT_TIMEZONE` | No | the server's timezone | Timezone for the weekly report, e.g. `America/Toronto` |
| `REPORT_TIME` | No | `19:00` | Time of the Monday report, 24-hour `HH:MM` |
| `HEALTH_MIN_MINUTES` / `HEALTH_MAX_MINUTES` | No | `20` / `60` | Range for the random delay between health sweeps |
| `DISK_ALERT_PERCENT` | No | `85` | Disk usage that triggers an alert |
| `MEM_ALERT_PERCENT` | No | `10` | Alert when available memory falls below this percent |

## Setting up as a background service

To keep the bot running after you close your SSH session, use the provided systemd unit.

1. Open `sysadmin-bot.service` and set `User=`, `WorkingDirectory=` and `ExecStart=` to match your account and install path. Add `After=network-online.target` and `Wants=network-online.target` so the bot starts once the network is up (needed for the boot notice).
2. Install and start it:

   ```bash
   sudo cp sysadmin-bot.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now sysadmin-bot.service
   ```

3. Follow the logs:

   ```bash
   journalctl -u sysadmin-bot -f
   ```

> Do not set `NoNewPrivileges=yes` in the unit. It stops `sudo` from working at all.

## Sudo setup

The bot runs `sudo` for only these exact commands: `dnf check-update -q`, `dnf upgrade -y`, `reboot`, and `systemctl restart <unit>` for each unit in `RESTART_UNITS`. It calls `sudo -n`, which fails immediately instead of waiting for a password.

**If the upgrade button says a password is required, the sudoers rule is missing or isn't matching.** The usual causes are a wrong file mode, a different command path, or a leftover placeholder user name. Instead of editing by hand, run the installer:

```bash
sudo bash ./setup-sudoers.sh <bot-user> caddy.service tailscaled.service
```

Replace `<bot-user>` with the account the service runs as (the `User=` line in the systemd unit) and list the same units as `RESTART_UNITS`, or none. Preview the rule first with `bash ./setup-sudoers.sh --print <bot-user>`.

The script:

1. finds the real paths of `dnf`, `reboot` and `systemctl` the same way sudo does,
2. writes `/etc/sudoers.d/sysadmin-bot` with **exact** commands only (no wildcards),
3. validates it with `visudo` **before** installing, so a typo can never break sudo,
4. installs it as owner `root`, mode `0440`,
5. tests that the bot user can run each command without a password, **and** that arbitrary `dnf` commands are refused. If they are not refused, another sudoers rule is too broad and the script lists the `NOPASSWD` rules it finds.

Re-run it whenever you change `RESTART_UNITS`. No bot restart is needed after a sudoers change.

**Troubleshooting**

| Symptom | Cause | Fix |
|---|---|---|
| `a password is required` | rule missing, wrong path, or the file mode is wrong | run the script; `sudo visudo -c` should report every file as OK |
| Script says `PROBLEM ... arbitrary dnf commands` | a leftover rule such as `dnf` or `docker` with `NOPASSWD` | remove or tighten the rules the script prints |
| `The "no new privileges" flag is set` | `NoNewPrivileges=yes` in the systemd unit | remove that line, then `sudo systemctl daemon-reload` and restart the service |

The bot now reports a failed upgrade as a failure and explains sudo refusals in the message. Earlier versions said "Upgrade Complete" even when the command had been refused.

Never grant `dnf`, `systemctl`, `journalctl` or `docker` with arbitrary arguments: each one is effectively root access. To read system logs without sudo, add the service account to the `systemd-journal` group (`sudo usermod -aG systemd-journal <bot-user>`).

## Security & Safeguards

* **Zero open ports:** The bot uses Telegram long-polling, so it only makes outbound connections. No inbound ports, port-forwarding or reverse proxy are needed.
* **Chat-ID locked:** Messages, commands and button presses from any chat other than `ALLOWED_CHAT_ID` are ignored. Use your private chat ID: if you use a group's ID, every member of that group can control the server. Enable two-step verification on your Telegram account, because access to that account is effectively shell access as the bot's user.
* **The command policy is a guardrail, not a sandbox.** It catches typos and model mistakes, and approval buttons keep you in the loop. But a blocklist can never be complete, and anyone controlling your Telegram account can tap "Run it" too. The real protection is the operating system: an unprivileged service user and the exact-argument sudoers rule above.
* **Docker group:** membership in the `docker` group is equivalent to root. If the service account is in it, the policy blocks the obvious escapes (`--privileged`, mounting `/`), but treat the account as privileged. The **Restart** button on Docker alerts also relies on this access. Rootless Docker or Podman avoids the problem.
* **Stored history:** `bot_memory.db` keeps past conversations, including summaries of command output, across reboots. Log content can be attacker-influenced (for example web request headers), so a poisoned summary would persist and be fed back to the model. `/reset` clears it, and the policy still applies to anything the model proposes.
* **Least privilege:** run the service as a dedicated unprivileged user, keep the sudoers rule as narrow as shown above, and keep `.env` and `bot_memory.db` at mode `600`.