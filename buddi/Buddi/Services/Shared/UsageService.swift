import Combine
import Foundation
import Security

// MARK: - Models

struct UsageData: Equatable, Codable {
    var fiveHour: QuotaPeriod?
    var sevenDay: QuotaPeriod?

    static let empty = UsageData()
}

struct QuotaPeriod: Equatable, Codable {
    let utilization: Double     // 0-100
    let resetsAt: Date?
}

// MARK: - Usage Service

@MainActor
final class UsageService: ObservableObject {
    static let shared = UsageService()

    @Published private(set) var usage = UsageData.empty
    @Published private(set) var isAvailable = false

    private var pollTimer: Timer?
    private var pollTask: Task<Void, Never>?
    private let baseInterval: TimeInterval = 300
    private var currentInterval: TimeInterval = 300
    private var backoffCount = 0
    private var consecutiveFailures = 0

    private static let isoFormatterFrac: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()

    private static let isoFormatterBasic = ISO8601DateFormatter()

    private init() {}

    func startPolling() {
        loadCache()
        // If already polling, only kick off a fresh fetch if the last one is stale (>5 min old)
        guard pollTimer == nil else {
            if let cached = loadCachedUsage(), Date().timeIntervalSince(cached.fetchedAt) > baseInterval {
                poll()
            }
            return
        }
        poll()
    }

    func stopPolling() {
        pollTimer?.invalidate()
        pollTimer = nil
        pollTask?.cancel()
        pollTask = nil
        backoffCount = 0
        consecutiveFailures = 0
        currentInterval = baseInterval
    }

    private func scheduleNextPoll() {
        pollTimer?.invalidate()
        let interval = max(currentInterval, 1)
        pollTimer = Timer.scheduledTimer(withTimeInterval: interval, repeats: false) { [weak self] _ in
            Task { @MainActor [weak self] in
                self?.poll()
            }
        }
    }

    private func poll() {
        pollTask = Task {
            guard let token = Self.readOAuthToken() else {
                isAvailable = false
                scheduleNextPoll()
                return
            }

            if !isAvailable { isAvailable = true }

            do {
                let result = try await Self.fetchUsage(token: token)
                usage = result
                consecutiveFailures = 0
                backoffCount = 0
                currentInterval = baseInterval
                saveCache()
            } catch let error as URLError where error.code.rawValue == 429 {
                backoffCount += 1
                currentInterval = min(1800, baseInterval * pow(2.0, Double(backoffCount)))
            } catch {
                consecutiveFailures += 1
                if consecutiveFailures > 5 && usage.fiveHour == nil && usage.sevenDay == nil {
                    isAvailable = false
                }
                // Always restore to base interval on any failure path so we never loop fast
                currentInterval = baseInterval
            }
            scheduleNextPoll()
        }
    }

    // MARK: - Cache

    private static let cacheURL: URL = {
        let appSupport = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
        let dir = appSupport.appendingPathComponent("Buddi", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir.appendingPathComponent("usage-cache.json")
    }()

    private struct CachedUsage: Codable {
        let usage: UsageData
        let fetchedAt: Date
    }

    private func loadCachedUsage() -> CachedUsage? {
        guard let data = try? Data(contentsOf: Self.cacheURL) else { return nil }
        return try? JSONDecoder().decode(CachedUsage.self, from: data)
    }

    private func loadCache() {
        guard let cached = loadCachedUsage() else { return }
        usage = cached.usage
        isAvailable = true
        // If cached data is stale, use a short interval so the first poll fires quickly
        // without creating a tight loop (minimum is clamped to 1s in scheduleNextPoll)
        if Date().timeIntervalSince(cached.fetchedAt) > baseInterval {
            currentInterval = 2
        }
    }

    private func saveCache() {
        let cached = CachedUsage(usage: usage, fetchedAt: Date())
        guard let data = try? JSONEncoder().encode(cached) else { return }
        try? data.write(to: Self.cacheURL, options: .atomic)
    }

    // MARK: - Keychain

    private static func readOAuthToken() -> String? {
        // Primary: /usr/bin/security CLI (avoids ACL dialog)
        if let json = readKeychainViaCLI(),
           let token = extractToken(from: json) {
            return token
        }

        // Fallback: Security.framework
        if let json = readKeychainViaFramework(),
           let token = extractToken(from: json) {
            return token
        }

        return nil
    }

    private static func readKeychainViaCLI() -> [String: Any]? {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/security")
        process.arguments = ["find-generic-password", "-s", "Claude Code-credentials", "-w"]

        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = FileHandle.nullDevice

        do { try process.run() } catch { return nil }
        process.waitUntilExit()

        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        guard !data.isEmpty,
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return nil
        }
        return json
    }

    private static func readKeychainViaFramework() -> [String: Any]? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: "Claude Code-credentials",
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
            kSecUseAuthenticationUI as String: kSecUseAuthenticationUISkip
        ]

        var result: AnyObject?
        let status = SecItemCopyMatching(query as CFDictionary, &result)

        guard status == errSecSuccess,
              let data = result as? Data,
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return nil
        }
        return json
    }

    private static func extractToken(from json: [String: Any]) -> String? {
        guard let oauth = json["claudeAiOauth"] as? [String: Any],
              let rawToken = oauth["accessToken"] as? String else { return nil }
        let token = rawToken.trimmingCharacters(in: .whitespacesAndNewlines)
        return token.isEmpty ? nil : token
    }

    // MARK: - API Call

    private static func fetchUsage(token: String) async throws -> UsageData {
        var request = URLRequest(url: URL(string: "https://api.anthropic.com/api/oauth/usage")!)
        request.httpMethod = "GET"
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("oauth-2025-04-20", forHTTPHeaderField: "anthropic-beta")
        request.setValue("claude-code/2.1", forHTTPHeaderField: "User-Agent")
        request.timeoutInterval = 15

        let (data, response) = try await URLSession.shared.data(for: request)

        guard let http = response as? HTTPURLResponse else {
            throw URLError(.badServerResponse)
        }

        guard http.statusCode == 200 else {
            throw URLError(.init(rawValue: http.statusCode))
        }

        guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw URLError(.cannotParseResponse)
        }

        return UsageData(
            fiveHour: parseQuotaPeriod(json["five_hour"]),
            sevenDay: parseQuotaPeriod(json["seven_day"])
        )
    }

    private static func parseQuotaPeriod(_ value: Any?) -> QuotaPeriod? {
        guard let dict = value as? [String: Any],
              let utilization = dict["utilization"] as? Double else { return nil }

        var resetsAt: Date?
        if let dateStr = dict["resets_at"] as? String {
            resetsAt = isoFormatterFrac.date(from: dateStr) ?? isoFormatterBasic.date(from: dateStr)
        }

        return QuotaPeriod(utilization: utilization, resetsAt: resetsAt)
    }
}
