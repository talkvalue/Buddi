<div align="center">
  <h1>Buddi</h1>
  <p>A macOS notch companion for <a href="https://docs.anthropic.com/en/docs/claude-code">Claude Code</a>.</p>
</div>

<div align="center">
  <video src="https://github.com/user-attachments/assets/3856b8c7-ca83-4672-b8d2-3c58ac65f2e9" autoplay loop muted playsinline width="100%"></video>
</div>

Anthropic removed /buddy from Claude Code in v2.1.97. Buddi gives your buddy a permanent home in the MacBook notch.

---

## Features

### Claude Code Integration

- **Live session monitoring** — Track multiple concurrent Claude Code sessions in real-time
- **Permission approvals** — Approve or deny tool executions directly from the notch
- **Chat interface** — View full conversation history with markdown rendering, send messages back
- **Usage tracking** — Session and weekly utilization at a glance
- **Multiplexer support** — Works with cmux and tmux sessions
- **Auto-setup** — Hooks install automatically on first launch

### Buddy Characters

18 species — duck, goose, blob, cat, dragon, octopus, owl, penguin, turtle, snail, ghost, axolotl, capybara, cactus, robot, rabbit, mushroom, and chonk. Each buddy has a unique identity generated from your system, with different eyes, hats, rarity levels, and personality stats. They animate through idle, working, reading, sleeping, and error states — matching what Claude Code is doing.

### Notch Utility

- **Music player** — Album art, playback controls, and audio visualizer
- **Calendar** — Upcoming events at a glance
- **Battery** — Status and charging indicator
- **HUD replacement** — Custom volume and brightness overlays
- **File shelf** — Drag-and-drop file staging area
- **Webcam preview** — Quick mirror from the notch
- **Keyboard shortcuts** — Customizable hotkeys

## Requirements

- macOS 15.0+ (Sequoia)
- MacBook with notch (or external display support)
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) installed

## Install

### Homebrew

```bash
brew install --cask talkvalue/buddi/buddi
```

### Manual Download

Download the latest `.dmg` from the [Releases page](https://github.com/talkvalue/Buddi/releases/latest).

## How It Works

```
Claude Code → Hooks → Unix Socket → Buddi → Notch UI
```

Buddi registers hooks with Claude Code on launch. When Claude Code emits events — tool use, thinking, session start/end, permission requests — the hooks forward them to Buddi over a Unix domain socket. The app maps events to buddy animations and UI state. When Claude needs permission, the notch expands with approve/deny buttons.

## Building from Source

1. Clone the repo
2. Open `buddi.xcodeproj` in Xcode 16+
3. Build and run — SPM dependencies resolve automatically

## Acknowledgements

Buddi is built on top of open-source work by others:

- **[boring.notch](https://github.com/TheBoredTeam/boring.notch)** by TheBoredTeam — The notch UI framework that Buddi is built upon. Music player, calendar, battery, HUD replacement, file shelf, and the core notch rendering system all come from boring.notch.
- **[Claude Island](https://github.com/farouqaldori/claude-island)** by farouqaldori — The predecessor to Buddi. Claude Code session monitoring, hook system, buddy characters, and the chat interface originate from Claude Island.

See [NOTICE](NOTICE) for full attribution and licensing details.

## License

This project is licensed under the [GNU General Public License v3.0](LICENSE).

---

<div align="center">

*Built by [TalkValue](https://trytalkvalue.com?utm_source=github&utm_medium=buddi_repo&utm_campaign=buddi) — we build tools for people who build with AI.*

[Event Intelligence Playbook](https://www.linkedin.com/newsletters/event-intelligence-playbook-7432120487045926912/?utm_source=github&utm_medium=buddi_repo&utm_campaign=buddi) · our weekly newsletter on AI + events

© 2026 TalkValue

</div>
