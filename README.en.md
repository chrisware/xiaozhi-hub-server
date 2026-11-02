<p align="center">
  <picture>
    <source srcset="hub-docs/images/logo-dark.svg" media="(prefers-color-scheme: dark)">
    <source srcset="hub-docs/images/logo-light.svg" media="(prefers-color-scheme: light)">
    <img src="hub-docs/images/logo-light.svg" alt="xiaozhi-hub-server">
  </picture>
</p>
<p align="center">A voice hub for your AI: call a name, and that agent or model takes the job.</p>
<p align="center">
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/github/license/justhalfbit/xiaozhi-hub-server?style=flat-square" /></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.10-3776ab?style=flat-square" />
  <img alt="Platform" src="https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-64748b?style=flat-square" />
  <a href="https://github.com/justhalfbit/xiaozhi-hub-server/stargazers"><img alt="GitHub Stars" src="https://img.shields.io/github/stars/justhalfbit/xiaozhi-hub-server?style=flat-square" /></a>
  <a href="https://x.com/justhalfbit"><img alt="X (Twitter)" src="https://img.shields.io/badge/X-%40justhalfbit-000000?style=flat-square&logo=x&logoColor=white" /></a>
</p>
<p align="center">
  <a href="README.md">简体中文</a> |
  <a href="README.en.md">English</a>
</p>
<p align="center">
  <a href="hub-docs/guide.md">User guide</a> · <a href="hub-docs/design.md">Design notes</a> (both in Chinese)
</p>

[![Admin console](hub-docs/images/admin.png)](hub-docs/guide.md)

---

A **hub gateway** for Xiaozhi devices (M5Stack StackChan, ESP32 boards, and more). The device listens and speaks; the gateway recognizes and synthesizes speech and hands what you say to the agent or model whose name you call.

- **On your own computer**: let the local Claude Code do the work, or use any LLM API.
- **On a cloud server**: the local Claude Code is not available, so use LLM APIs instead.

```
Xiaozhi device   ⇄   xiaozhi-hub-server                       ⇄   Claude Code / LLM APIs / …
listens, speaks      recognizes, routes by name, synthesizes      thinks and does the work
```

> A community project compatible with the Xiaozhi protocol. Not an official Xiaozhi project.

## Features

**One device, many AIs**

- **Call a name to switch**: say "嗨 Claude" ("Hey Claude") for Claude Code, "嗨 DeepSeek" for DeepSeek; call names are set in the admin console. Each AI keeps its own conversation, and each device remembers the one it used last.
- **Claude Code**: works with the account you signed in to on this computer, for example "turn this week's notes into a checklist".
- **LLM APIs**: Claude, OpenAI, Gemini, DeepSeek, Qwen, Doubao, Zhipu GLM, Kimi, and any OpenAI-compatible API.
- **Grok Bot**: hand tasks to xAI's cloud AI teammates. Sign in with your Cursor account in the admin console, tick the Bots you want, give them call names, then say "嗨 孙悟空, summarize this week's X trends" and the reply is read out. You can stop waiting on a long task; when the Bot finishes in the cloud, the device reports back. This uses the same private endpoints as the desktop app, not an official API, so an app update may break it.
- **More agents**: Codex is planned. All agents go through one session interface, so adding a new one is mostly a matter of implementing it.

**Long tasks, with you all the way**

- **Waits with you**: while Claude Code works on a task for a few minutes, the device stays with you, reports progress now and then, and tells you the result when it's done.
- **Interrupt any time**: while the device is speaking, press its button (on StackChan, tap the face on the screen) or say the wake word, and it stops right away.
- **Pick up where you left off**: reconnects resume automatically; after a longer break, say "接着上次聊" ("continue where we left off") to get back to it (with Claude Code).

**Hears you right**

- Mixed Chinese and English: the default X-ASR runs offline and gets sentences like "帮我跑一下 npm install" right; add misheard words to hotwords and corrections. Doubao streaming ASR is also available.

**Admin console**

- Set up LLMs and API keys, Claude Code permissions, call names, plugins, hotwords and corrections, and the TTS voice in a web page; see online devices and environment checks.

## Language

English is the default in this fork; Chinese stays fully available. One setting — `language: en` (or `zh`) in `data/.config.yaml` — drives everything the hub speaks or prompts:

- The LLM reply language (`{{language}}` in the system prompt), the persona, and the system-prompt time lines (English weekday; the lunar-date line is dropped).
- Canned replies: errors, the "I'm here" call-name reply, the idle goodbye, the chat-history clear confirmation ("clear my chat history" → "yes"), Claude Code/Grok Bot fillers ("Right, let me have a look", "Still working on it") and progress notices, and the spoken timeout/auth/error messages.
- Voice commands: `exit`, `goodbye`, `stop listening` end a session; `stop`, `cancel`, `never mind` halt background work; resume keywords include "continue from last time".
- The default TTS voice becomes `en-NZ-MollyNeural` (New Zealand English); English voices are listed in the admin console.

Anything you set yourself in `data/.config.yaml` wins over the language defaults — e.g. pin `voice: zh-CN-XiaoxiaoNeural` under `TTS.EdgeTTS` and it stays even with `language: en`.

Speech recognition for English needs no extra download: the default **X-ASR** model (`SherpaXASR`, installed by the installer, ~175 MB, runs offline in ~0.5 GB RAM) is a Chinese-English mixed transducer and transcribes English directly, with punctuation and hotwords. It was chosen over SenseVoice (`FunASR`, worse mixed zh/en accuracy and a 2 GB memory gate on this device) and Vosk-small-en (no punctuation). If you want better English accuracy and don't mind the cloud, switch `selected_module.ASR` to `GroqASR` (`whisper-large-v3-turbo`) or `OpenaiASR` — both already exist; add your API key in the admin console. English speech quality on the ThinkSmart mic array still needs a real-device check.

Streaming replies now cut at English periods too (`"The weather is fine. It's 21 degrees."` becomes two speakable segments), so first audio arrives sooner; decimals (`3.5`) and ellipses are never cut.

## Quick start

Before you begin: the computer and the device must be on the same LAN, and git must be installed. To use Claude Code, install it and run `claude` once in a terminal to sign in; without it, the installer uses the Claude API instead, and you fill in the API key in the admin console.

### 1. Install

**macOS / Linux**, in a terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/justhalfbit/xiaozhi-hub-server/main/scripts/bootstrap.sh | bash
```

It installs to `~/xiaozhi-hub-server` and, at the end, asks whether to start the server at login (press Enter to accept). On macOS, missing ffmpeg and opus are installed with [Homebrew](https://brew.sh), so install Homebrew first in that case.

**Windows 10 / 11**, in PowerShell:

```powershell
git clone --depth 1 https://github.com/justhalfbit/xiaozhi-hub-server.git
cd xiaozhi-hub-server
powershell -ExecutionPolicy Bypass -File scripts\install.ps1   # install
powershell -ExecutionPolicy Bypass -File scripts\start.ps1     # start
```

On first start, Windows Firewall asks whether Python may access the network. Allow it on private networks, or the device cannot connect. Windows has no autostart yet; run `start.ps1` once after each boot.

To install without `curl | bash`, speed up slow downloads, or see what gets installed, read [User guide · Install](hub-docs/guide.md#1-安装) (Chinese).

### 2. Set the admin password

Open `http://127.0.0.1:16688/admin` in a browser on this computer and set an admin password on first use.

### 3. Connect the device

When setting up the device's network, enter the OTA address under "Advanced options":

```
http://<your computer's LAN IP>:16688/xiaozhi/ota/
```

For StackChan setup steps and how to talk to the device, see the [user guide](hub-docs/guide.md#3-连接设备) (Chinese).

**On a cloud server**: install the same way, then open the ports and set the public address in the config; see [User guide · Deploy to a cloud server](hub-docs/guide.md#部署到云端服务器) (Chinese).

## Update and uninstall

| | macOS / Linux | Windows |
| --- | --- | --- |
| Update | `bash ~/xiaozhi-hub-server/scripts/update.sh` | Run `git pull` in the project folder, then `install.ps1` again |
| Uninstall | `bash ~/xiaozhi-hub-server/scripts/uninstall.sh` | Run `scripts\uninstall.ps1`; see the [user guide](hub-docs/guide.md#卸载) first |

If you cloned manually, replace `~/xiaozhi-hub-server` with your own directory. For turning autostart on and off and what uninstall removes, see [User guide · Autostart, update and uninstall](hub-docs/guide.md#7-开机自启更新与卸载) (Chinese).

## Security

- On your own computer, use the server only on your LAN. Do not expose its ports to the internet.
- On a cloud server, the device connection and the admin console are on the internet: restrict access with a firewall, and open the admin console soon after starting the server to set a strong admin password.
- By default Claude Code can only read files in the working directory `~/xiaozhi-workspace`. Once you allow writing files or running commands in the admin console, voice commands can do the same, so be careful. Commands run at the Auto level are not limited to the working directory.

## About this repository

This project only uses `main/xiaozhi-server`, `scripts/` and `hub-docs/`. The other directories are unchanged code from the upstream [xiaozhi-esp32-server](https://github.com/xinnan-tech/xiaozhi-esp32-server) and are not used. They are kept so that regular merges from upstream, which keep up with Xiaozhi firmware updates, produce fewer conflicts; the one-line installer does not download them.

To learn what happens from wake word to answer, read the [design notes](hub-docs/design.md) (Chinese) or the "How it works" page in the admin console.

## License

[MIT License](LICENSE). Based on [xinnan-tech/xiaozhi-esp32-server](https://github.com/xinnan-tech/xiaozhi-esp32-server) (MIT). Third-party credits are listed in the [user guide](hub-docs/guide.md#致谢与许可).
