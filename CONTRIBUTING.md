# Contributing to Buddi

Thanks for your interest in contributing to Buddi! Please read through this guide before submitting issues or pull requests.

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code.

## Issue Reporting

- Search [existing issues](https://github.com/talkvalue/Buddi/issues) before creating a new one.
- Use the provided issue templates (bug report or feature request).
- For bugs, include your macOS version, device model (notch vs external display), and steps to reproduce.
- Issues without clear reproduction steps may be closed after 7 days of inactivity.

## Pull Requests

- Open an issue first to discuss significant changes before starting work.
- Fork the repo and create a branch from `main`.
- Keep PRs focused -- one feature or fix per PR.
- Follow the existing code style and project structure.
- Test your changes on a macOS device with a notch if possible.
- Fill out the PR template completely.

### Development Setup

1. **Requirements**: macOS 15.0+ (Sequoia), Xcode 16+
2. Clone the repo: `git clone https://github.com/talkvalue/Buddi.git`
3. Open `buddi.xcodeproj` in Xcode
4. Build and run (Cmd+R)

### Commit Messages

Use clear, descriptive commit messages:

- `fix: resolve crash when switching audio source`
- `feat: add keyboard shortcut for shelf toggle`
- `docs: update installation instructions`

## AI Tool Policy

AI-assisted contributions are welcome, but you are responsible for reviewing and testing all generated code before submitting. PRs that appear to be unreviewed AI output will be closed with the `ai-slop` label.

## Questions?

Open a [discussion](https://github.com/talkvalue/Buddi/discussions) or ask in an issue -- we're happy to help.
