//
//  TerminalStubs.swift
//  Buddi
//
//  Stub types for the embedded terminal feature.
//  These will be replaced by full implementations in sibling PRs (Units 1-5).
//

import Foundation

// MARK: - EmbeddedTerminalController Stub

/// Launch mode for an embedded terminal session.
enum EmbeddedTerminalMode {
    /// Attach to an existing tmux or cmux session.
    case attach
    /// Spawn Claude directly in a new PTY (no multiplexer required).
    case directPTY
}

/// Controls a single embedded PTY session.
///
/// This is a stub — the full implementation lives in the `embedded-terminal` sibling PR.
@MainActor
final class EmbeddedTerminalController {
    let mode: EmbeddedTerminalMode
    let sessionId: String?

    /// PID of the child process once launched.
    private(set) var childPID: Int?

    init(mode: EmbeddedTerminalMode, sessionId: String?) {
        self.mode = mode
        self.sessionId = sessionId
    }

    /// Start the Claude process directly in a PTY.
    func launchDirect(claudePath: String, workingDirectory: String) {
        // Stub: full PTY launch implemented in the embedded-terminal PR.
    }
}

// MARK: - TerminalControllerStore Stub

/// Registry that maps child PIDs to their EmbeddedTerminalController.
///
/// This is a stub — the full implementation lives in the `embedded-terminal` sibling PR.
@MainActor
final class TerminalControllerStore {
    static let shared = TerminalControllerStore()

    private var controllers: [Int: EmbeddedTerminalController] = [:]

    private init() {}

    func register(_ controller: EmbeddedTerminalController, pid: Int) {
        controllers[pid] = controller
    }

    func controller(forPID pid: Int) -> EmbeddedTerminalController? {
        controllers[pid]
    }
}
