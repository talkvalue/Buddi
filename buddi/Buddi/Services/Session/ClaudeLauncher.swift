//
//  ClaudeLauncher.swift
//  Buddi
//
//  Launches new Claude CLI sessions inside a tmux window,
//  or directly in an embedded PTY.
//

import Foundation
import os

/// Errors thrown by ClaudeLauncher
enum ClaudeLauncherError: LocalizedError {
    case claudeNotFound
    case tmuxNotFound
    case launchFailed(String)

    var errorDescription: String? {
        switch self {
        case .claudeNotFound:
            return "Claude CLI not found. Install it via npm: npm install -g @anthropic-ai/claude-code"
        case .tmuxNotFound:
            return "tmux not found. Install it via Homebrew: brew install tmux"
        case .launchFailed(let detail):
            return "Failed to launch Claude: \(detail)"
        }
    }
}

/// Finds the claude binary and spawns it in a new tmux window or embedded PTY.
actor ClaudeLauncher {
    static let shared = ClaudeLauncher()

    private static let logger = os.Logger(subsystem: "com.splab.buddi", category: "ClaudeLauncher")

    /// Common install locations for the claude CLI binary
    private let candidatePaths: [String] = [
        "\(NSHomeDirectory())/.npm/bin/claude",
        "\(NSHomeDirectory())/.local/bin/claude",
        "/usr/local/bin/claude",
        "/opt/homebrew/bin/claude",
    ]

    private var cachedClaudePath: String?

    private init() {}

    // MARK: - Public API

    /// Launch a new Claude CLI session in `dir` via tmux.
    ///
    /// - Parameter dir: Working directory for the new window; uses `~` when `nil`.
    /// - Returns: The tmux session name the window was created in (always `"buddi"`).
    func launch(inDirectory dir: String?) async throws -> String {
        guard let claudePath = await findClaudePath() else {
            throw ClaudeLauncherError.claudeNotFound
        }

        guard let tmuxPath = await TmuxPathFinder.shared.getTmuxPath() else {
            throw ClaudeLauncherError.tmuxNotFound
        }

        let workDir = dir ?? NSHomeDirectory()
        let sessionName = "buddi"

        // Check whether the buddi session already exists.
        let hasSession = (try? await ProcessExecutor.shared
            .runWithResult(tmuxPath, arguments: ["has-session", "-t", sessionName])
            .get()) != nil

        if !hasSession {
            // Create a detached session so we have a session to attach windows to.
            let createResult = await ProcessExecutor.shared.runWithResult(
                tmuxPath,
                arguments: ["new-session", "-d", "-s", sessionName, "-c", workDir]
            )
            if case .failure(let error) = createResult {
                Self.logger.error("tmux new-session failed: \(error.localizedDescription, privacy: .public)")
                throw ClaudeLauncherError.launchFailed(error.localizedDescription)
            }
        }

        // Open a new window named "claude" and start the CLI.
        let windowResult = await ProcessExecutor.shared.runWithResult(
            tmuxPath,
            arguments: ["new-window", "-t", sessionName, "-n", "claude", "-c", workDir, claudePath]
        )

        if case .failure(let error) = windowResult {
            Self.logger.error("tmux new-window failed: \(error.localizedDescription, privacy: .public)")
            throw ClaudeLauncherError.launchFailed(error.localizedDescription)
        }

        Self.logger.info("Launched Claude in tmux session '\(sessionName, privacy: .public)', dir: \(workDir, privacy: .public)")
        return sessionName
    }

    /// Launch Claude directly in an embedded PTY (no tmux needed).
    ///
    /// Returns the EmbeddedTerminalController managing the PTY session.
    ///
    /// - Parameter dir: Working directory; uses `~` when `nil`.
    /// - Returns: The controller managing the new PTY session.
    ///
    /// TODO: Wire pending terminal state once Unit 4 (BuddyPanelViewModel.showPendingTerminal) lands.
    func launchInEmbeddedTerminal(inDirectory dir: String?) async throws -> EmbeddedTerminalController {
        guard let claudePath = await findClaudePath() else {
            throw ClaudeLauncherError.claudeNotFound
        }

        let workDir = dir ?? NSHomeDirectory()
        let (controller, pid) = await MainActor.run {
            let c = EmbeddedTerminalController(mode: .directPTY, sessionId: nil)
            c.launchDirect(claudePath: claudePath, workingDirectory: workDir)
            return (c, c.childPID)
        }

        if let pid {
            await TerminalControllerStore.shared.register(controller, pid: pid)
        }

        Self.logger.info("Launched Claude in embedded PTY, dir: \(workDir, privacy: .public)")
        return controller
    }

    /// Resolve the path to the claude CLI binary, caching the result.
    func findClaudePath() async -> String? {
        if let cached = cachedClaudePath {
            return cached
        }

        // Check known static paths first (no subprocess needed).
        for path in candidatePaths where FileManager.default.isExecutableFile(atPath: path) {
            cachedClaudePath = path
            return path
        }

        // Fall back to `which claude` so PATH-based installs are also found.
        if let whichOutput = await ProcessExecutor.shared.runOrNil(
            "/usr/bin/which", arguments: ["claude"]
        ) {
            let path = whichOutput.trimmingCharacters(in: .whitespacesAndNewlines)
            if !path.isEmpty && FileManager.default.isExecutableFile(atPath: path) {
                cachedClaudePath = path
                return path
            }
        }

        return nil
    }
}
