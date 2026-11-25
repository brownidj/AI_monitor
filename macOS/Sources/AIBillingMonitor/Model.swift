import AppKit
import Foundation
import ServiceManagement
import SwiftUI

typealias Record = [String: Any]
let openAIBillingURL = URL(string: "https://platform.openai.com/settings/organization/billing/overview")!
let jetBrainsAccountURL = URL(string: "https://account.jetbrains.com/")!
extension Dictionary where Key == String, Value == Any {
    func text(_ key: String) -> String { self[key] as? String ?? "" }
    func flag(_ key: String) -> Bool { self[key] as? Bool ?? false }
    func object(_ key: String) -> Record { self[key] as? Record ?? [:] }
    func records(_ key: String) -> [Record] { self[key] as? [Record] ?? [] }
    func number(_ key: String) -> Decimal? {
        if let value = self[key] as? String { return Decimal(string: value, locale: Locale(identifier: "en_US_POSIX")) }
        if let value = self[key] as? NSNumber { return value.decimalValue }
        return nil
    }
}
func money(_ value: Decimal?, currency: String = "AUD") -> String {
    guard let value else { return "—" }
    let formatter = NumberFormatter()
    formatter.numberStyle = .currency
    formatter.currencyCode = currency
    formatter.locale = Locale(identifier: "en_AU")
    return formatter.string(from: NSDecimalNumber(decimal: value)) ?? "—"
}
func dateText(_ value: String) -> String {
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    if let date = formatter.date(from: value) ?? ISO8601DateFormatter().date(from: value) {
        return date.formatted(date: .abbreviated, time: .shortened)
    }
    return value.isEmpty ? "Not yet" : value
}
struct AppFailure: LocalizedError {
    var message: String
    var errorDescription: String? { message }
}

@MainActor final class AppModel: ObservableObject {
    @Published var connected = false
    @Published var starting = false
    @Published var busy = false
    @Published var error: String?
    @Published var notice: String?
    @Published var status: Record = [:]
    @Published var history: Record = [:]
    @Published var preferences: Record = [:]
    @Published var subscriptions: [Record] = []
    @Published var snapshots: [Record] = []
    @Published var jetbrainsSummary: Record = [:]
    @Published var overview: Record = [:]
    @Published var freePoolSettings: Record = [:]
    @Published var alerts: [Record] = []
    let support = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/AI Billing Monitor")
    private var process: Process?
    private var port = 0
    private var token = ""
    private var refreshTask: Task<Void, Never>?
    private var logHandle: FileHandle?
    var quitting = false
    var fresh: Bool { connected && !status.flag("data_stale") }
    var usdPerAUD: Decimal? { history.object("exchange_rate").number("usd_per_aud") }
    var audBudget: Decimal? {
        guard let budget = preferences.number("monthly_budget") else { return nil }
        if preferences.text("budget_currency") == "AUD" { return budget }
        guard let rate = usdPerAUD, rate > 0 else { return nil }
        return budget / rate
    }
    var usedPercent: Double? {
        guard let budget = audBudget, budget > 0, let spend = history.number("converted_total_estimate") else { return nil }
        return NSDecimalNumber(decimal: spend / budget * 100).doubleValue
    }
    var hasKey: Bool { status.flag("credential_configured") }

    func start() async {
        guard !starting else { return }
        starting = true
        error = nil
        defer { starting = false }
        do {
            try FileManager.default.createDirectory(at: support, withIntermediateDirectories: true)
            let ready = support.appendingPathComponent("ready-\(ProcessInfo.processInfo.processIdentifier).json")
            try? FileManager.default.removeItem(at: ready)
            guard let executable = Bundle.main.resourceURL?.appendingPathComponent("backend/monitor-backend"), FileManager.default.isExecutableFile(atPath: executable.path) else {
                throw AppFailure(message: "The bundled monitoring service is missing. Rebuild the Mac app.")
            }
            let child = Process()
            child.executableURL = executable
            child.arguments = ["--support", support.path, "--ready-file", ready.path, "--parent-pid", String(ProcessInfo.processInfo.processIdentifier)]
            if let source = Bundle.main.object(forInfoDictionaryKey: "MonitorImportDirectory") as? String,
               FileManager.default.fileExists(atPath: source + "/.env") {
                child.arguments! += ["--import-dir", source]
            }
            let log = support.appendingPathComponent("backend.log")
            if !FileManager.default.fileExists(atPath: log.path) { FileManager.default.createFile(atPath: log.path, contents: nil, attributes: [.posixPermissions: 0o600]) }
            let handle = try FileHandle(forWritingTo: log)
            handle.seekToEndOfFile()
            logHandle = handle
            child.standardOutput = handle
            child.standardError = handle
            try child.run()
            process = child
            for _ in 0..<300 {
                if let data = try? Data(contentsOf: ready), let object = try? JSONSerialization.jsonObject(with: data) as? Record {
                    if let failure = object["error"] as? String { throw AppFailure(message: failure) }
                    if let value = object["port"] as? Int, value == 8781,
                       object["pid"] as? Int32 == child.processIdentifier {
                        port = value
                        break
                    }
                }
                guard child.isRunning else { throw AppFailure(message: "The monitoring service stopped during startup. Check the service log in Settings.") }
                try await Task.sleep(nanoseconds: 100_000_000)
            }
            guard port > 0 else { throw AppFailure(message: "The monitoring service took too long to start. Try again.") }
            token = try readEnvironment()["MONITOR_API_TOKEN"] ?? ""
            try await reload()
            refreshTask?.cancel()
            refreshTask = Task { [weak self] in
                while !Task.isCancelled {
                    do { try await Task.sleep(nanoseconds: 30_000_000_000) } catch { return }
                    guard let self, !self.quitting else { return }
                    if self.process?.isRunning != true {
                        self.stop()
                        await self.start()
                        return
                    }
                    do { try await self.reload() } catch { self.error = error.localizedDescription; self.connected = false }
                }
            }
        } catch {
            stop()
            self.error = error.localizedDescription
        }
    }
    func stop() {
        refreshTask?.cancel()
        refreshTask = nil
        if let process, process.isRunning { process.terminate() }
        process = nil
        try? logHandle?.close()
        logHandle = nil
        connected = false
        port = 0
    }
    func request(_ path: String, method: String = "GET", body: Record? = nil) async throws -> Any {
        guard let url = URL(string: "http://127.0.0.1:\(port)\(path)"), port > 0 else { throw AppFailure(message: "The monitoring service is starting.") }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.timeoutInterval = 150
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        if let body {
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let (data, response) = try await URLSession.shared.data(for: request)
        let code = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(code) else {
            if code == 401 { throw AppFailure(message: "The local service could not authenticate. Restart the app.") }
            let json = (try? JSONSerialization.jsonObject(with: data)) as? Record
            throw AppFailure(message: json?.text("detail").isEmpty == false ? json!.text("detail") : "The request could not be completed (\(code)). Check your entries and try again.")
        }
        return data.isEmpty ? [:] : try JSONSerialization.jsonObject(with: data)
    }
    func reload() async throws {
        // Keep a complete last-good snapshot if any request fails.
        let newStatus = try await request("/v1/monitor/status") as? Record ?? [:]
        let newHistory = try await request("/v1/openai/history") as? Record ?? [:]
        let newPreferences = try await request("/v1/monitor/settings") as? Record ?? [:]
        let newSubscriptions = try await request("/v1/subscriptions") as? [Record] ?? []
        let newSnapshots = try await request("/v1/jetbrains/snapshots") as? [Record] ?? []
        let newJetBrainsSummary = try await request("/v1/jetbrains/summary") as? Record ?? [:]
        let newOverview = try await request("/v1/overview") as? Record ?? [:]
        let newFreePoolSettings = try await request("/v1/overview/free-pools") as? Record ?? [:]
        let newAlerts = try await request("/v1/alerts") as? [Record] ?? []
        status = newStatus; history = newHistory; preferences = newPreferences
        subscriptions = newSubscriptions; snapshots = newSnapshots; alerts = newAlerts
        jetbrainsSummary = newJetBrainsSummary
        overview = newOverview; freePoolSettings = newFreePoolSettings
        connected = true
    }
    func perform(_ action: @escaping () async throws -> Void) {
        guard !busy else { return }
        busy = true; error = nil; notice = nil
        Task {
            defer { busy = false }
            do { try await action(); try await reload() } catch { self.error = error.localizedDescription }
        }
    }
    func checkNow() {
        perform {
            _ = try await self.request("/v1/overview/refresh", method: "POST")
            _ = try await self.request("/v1/jetbrains/check", method: "POST")
            let result = try await self.request("/v1/monitor/run", method: "POST") as? Record ?? [:]
            if result.text("status") == "error" { throw AppFailure(message: result.text("message")) }
            self.notice = result.text("message")
        }
    }
    func readEnvironment() throws -> [String: String] {
        let value = try String(contentsOf: support.appendingPathComponent("config.env"), encoding: .utf8)
        var fields: [String: String] = [:]
        for line in value.components(separatedBy: .newlines) where !line.hasPrefix("#") {
            let parts = line.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false)
            if parts.count == 2 { fields[String(parts[0]).trimmingCharacters(in: .whitespaces)] = String(parts[1]).trimmingCharacters(in: .whitespaces).trimmingCharacters(in: CharacterSet(charactersIn: "\"'")) }
        }
        return fields
    }
    func saveKey(_ key: String) async throws {
        let clean = key.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clean.isEmpty, !clean.contains("\n"), !clean.contains("\r") else { throw AppFailure(message: "Enter an OpenAI Admin key.") }
        var environment = try readEnvironment()
        environment["OPENAI_ADMIN_KEY"] = clean
        // Write a temporary file with restrictive permissions before atomically replacing it.
        let temporary = support.appendingPathComponent("config-new.env")
        let data = Data(environment.sorted(by: { $0.key < $1.key }).map { "\($0.key)=\($0.value)" }.joined(separator: "\n").appending("\n").utf8)
        FileManager.default.createFile(atPath: temporary.path, contents: data, attributes: [.posixPermissions: 0o600])
        _ = try FileManager.default.replaceItemAt(support.appendingPathComponent("config.env"), withItemAt: temporary)
        stop()
        await start()
        if !connected { throw AppFailure(message: error ?? "The service could not restart.") }
        notice = "Credential saved on this Mac."
    }
}

extension Dictionary where Key == String, Value == Any {
    var licenceKey: String { self["licence_id"] as? String ?? "unknown" }
}
