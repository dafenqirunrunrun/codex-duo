import AppKit
import Foundation
import Security
import SwiftUI

private enum GatewayKeychain {
    static let service = "local.codex.duo.gateway"
    static let account = "default"

    static func exists() -> Bool {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                    kSecAttrService as String: service,
                                    kSecAttrAccount as String: account]
        return SecItemCopyMatching(query as CFDictionary, nil) == errSecSuccess
    }

    static func save(_ value: String) throws {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                    kSecAttrService as String: service,
                                    kSecAttrAccount as String: account]
        let removed = SecItemDelete(query as CFDictionary)
        guard removed == errSecSuccess || removed == errSecItemNotFound else {
            throw NSError(domain: NSOSStatusErrorDomain, code: Int(removed))
        }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/security")
        process.arguments = ["add-generic-password", "-a", account, "-s", service, "-w"]
        let input = Pipe()
        process.standardInput = input
        process.standardOutput = Pipe()
        process.standardError = Pipe()
        try process.run()
        input.fileHandleForWriting.write(Data("\(value)\n\(value)\n".utf8))
        try input.fileHandleForWriting.close()
        process.waitUntilExit()
        if process.terminationStatus != 0 {
            throw NSError(domain: "Codex Duo", code: Int(process.terminationStatus),
                          userInfo: [NSLocalizedDescriptionKey: "macOS 钥匙串未能保存服务商密钥"])
        }
    }
}

struct GatewayConfig: Decodable {
    let url: String
    let model: String
}

struct ClaudeTokenUsage: Decodable {
    let input: Int
    let output: Int
    let cache: Int
    let total: Int
    let requests: Int
    let date: String?
}

struct ClaudeUsage: Decodable {
    let today: ClaudeTokenUsage
    let week: ClaudeTokenUsage
    let days: [ClaudeTokenUsage]
    let updatedAt: Int
    let lastActivityAt: Int?
    let filesRead: Int
    let unreadableFiles: Int
}

struct SharedProjectConfig: Decodable {
    let path: String
}

struct ProjectSyncResult: Decodable {
    let created: [String]
    let total: Int
    let history: ConversationSyncResult?
    let pid: Int32?
}

struct ConversationSyncIssue: Decodable, Identifiable {
    let title: String
    let reason: String
    var id: String { title + reason }
}

struct ConversationSyncResult: Decodable {
    let updated: Int
    let unchanged: Int
    let pending: [ConversationSyncIssue]
    let errors: [ConversationSyncIssue]
    let reloadProfiles: [String]
}

struct DesktopProject: Decodable, Identifiable {
    let name: String
    let path: String
    let synced: Bool
    var id: String { path }
}

struct WindowUsage: Decodable {
    let label: String
    let remaining: Int
    let resetsAt: Int?
}

struct RecentTask: Decodable, Identifiable {
    let id: String
    let title: String
    let updatedAt: String
}

struct Handoff: Decodable {
    let path: String
    let cwd: String
    let content: String
    let messageCount: Int
    let target: String
    let sourceProfile: String?
}

struct HistoryPoint: Decodable {
    let at: Int
    let fiveHour: Int?
    let weekly: Int?
}

struct Account: Decodable, Identifiable {
    let id: String
    let name: String
    let pid: Int32?
    let loggedIn: Bool
    let usage: [String: WindowUsage]?
    let updatedAt: Int?
    let error: String?
    let recentTasks: [RecentTask]?
    let history: [HistoryPoint]?
    let planType: String?

    var membership: Membership {
        guard loggedIn else { return .unknown }
        return Membership(rawValue: planType?.lowercased() ?? "") ?? .unknown
    }

    var visibleUsageKeys: [String] {
        let keys = ["primary", "secondary"]
        // Pro's weekly window can occupy either API slot.
        return membership == .pro ? keys.filter { usage?[$0]?.label == "每周" } : keys
    }

    var identityCode: String {
        switch id {
        case "primary": return "A"
        case "secondary": return "B"
        default: return id
        }
    }

    var nickname: String {
        let defaults = ["账号 1", "账号 2", "账号1", "账号2", "账号一", "账号二", "主账号", "第二账号"]
        guard defaults.contains(name) else { return name }
        return id == "primary" ? "极光" : id == "secondary" ? "曜石" : "工作区"
    }

    // Keep the workspace identity visible even when tiers or custom names match.
    var displayName: String { "\(identityCode) · \(nickname)" }
}

enum Membership: String {
    case pro, plus, free, unknown

    var symbol: String {
        switch self {
        case .pro: return "crown.fill"
        case .plus: return "sparkles"
        case .free: return "leaf.fill"
        case .unknown: return "person.crop.circle"
        }
    }

    var label: String {
        switch self {
        case .pro: return "PRO MEMBER"
        case .plus: return "PLUS MEMBER"
        case .free: return "FREE EXPLORER"
        case .unknown: return "CODEX ACCOUNT"
        }
    }
}

@MainActor final class DuoModel: ObservableObject {
    @Published var accounts: [Account] = []
    @Published var busy = false
    @Published var refreshing = false
    @Published var message: String?
    @Published var foregroundPID: Int32?
    @Published var handoff: Handoff?
    @Published var claudeTasks: [RecentTask] = []
    @Published var claudeUsage: ClaudeUsage?
    @Published var claudeUsageError: String?
    @Published var refreshingClaudeUsage = false
    @Published var gatewayURL = "https://api.do.top"
    @Published var gatewayModel = "DeepSeek-V4.1-Flash"
    @Published var gatewayKeyConfigured = GatewayKeychain.exists()
    @Published var sharedProjectPath = ""
    @Published var syncingProjects = false
    @Published var desktopProjects: [DesktopProject] = []
    @Published var conversationSyncSummary = ""
    @Published var conversationSyncIssues: [ConversationSyncIssue] = []
    private let helper = Bundle.main.resourcePath! + "/codex_duo.py"

    private func command(_ args: [String], done: @escaping (Result<String, Error>) -> Void) {
        let executable = helper
        DispatchQueue.global(qos: .utility).async {
            let process = Process()
            process.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
            process.arguments = [executable] + args
            let output = Pipe(), errors = Pipe()
            process.standardOutput = output
            process.standardError = errors
            do {
                try process.run()
                let stdout = String(data: output.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
                let stderr = String(data: errors.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
                process.waitUntilExit()
                DispatchQueue.main.async {
                    if process.terminationStatus == 0 { done(.success(stdout.trimmingCharacters(in: .whitespacesAndNewlines))) }
                    else { done(.failure(NSError(domain: "Codex Duo", code: Int(process.terminationStatus), userInfo: [NSLocalizedDescriptionKey: stderr.trimmingCharacters(in: .whitespacesAndNewlines)]))) }
                }
            } catch { DispatchQueue.main.async { done(.failure(error)) } }
        }
    }

    func refresh(fetch: Bool = true) {
        if fetch {
            refreshClaudeUsage()
            refreshClaudeTasks()
        }
        guard !busy && !refreshing else { return }
        refreshing = true
        command(fetch ? ["status", "--refresh"] : ["status"]) { result in
            self.refreshing = false
            switch result {
            case .success(let text):
                if let data = text.data(using: .utf8), let value = try? JSONDecoder().decode([Account].self, from: data) {
                    let newlyLoggedIn = !fetch && value.contains { candidate in
                        candidate.loggedIn && self.accounts.first(where: { $0.id == candidate.id })?.loggedIn == false
                    }
                    self.accounts = value
                    if let front = NSWorkspace.shared.frontmostApplication?.processIdentifier,
                       value.contains(where: { $0.pid == front }) {
                        self.foregroundPID = front
                    }
                    if newlyLoggedIn { self.refresh(fetch: true) }
                } else { self.message = "读取账号状态失败" }
            case .failure(let error): self.message = error.localizedDescription
            }
        }
    }

    func open(_ account: Account) {
        guard !busy else { return }
        busy = true
        message = nil
        command(["open", account.id]) { result in
            self.busy = false
            switch result {
            case .success(let text):
                if let pid = Int32(text) {
                    self.foregroundPID = pid
                    NSRunningApplication(processIdentifier: pid)?.activate(options: [.activateAllWindows])
                }
                self.refresh(fetch: false)
            case .failure(let error): self.message = error.localizedDescription
            }
        }
    }

    func rename(_ account: Account, to newName: String) {
        guard !busy else { return }
        busy = true
        command(["rename", account.id, newName]) { result in
            self.busy = false
            switch result {
            case .success: self.refresh(fetch: false)
            case .failure(let error): self.message = error.localizedDescription
            }
        }
    }

    func prepareHandoff(account: Account, task: RecentTask) {
        guard !busy else { return }
        busy = true
        message = nil
        command(["handoff", account.id, task.id]) { result in
            self.busy = false
            switch result {
            case .success(let text):
                if let data = text.data(using: .utf8), let value = try? JSONDecoder().decode(Handoff.self, from: data) {
                    self.handoff = value
                } else { self.message = "交接包解析失败" }
            case .failure(let error): self.message = error.localizedDescription
            }
        }
    }

    func refreshClaudeTasks() {
        command(["claude-tasks"]) { result in
            switch result {
            case .success(let text):
                if let data = text.data(using: .utf8), let value = try? JSONDecoder().decode([RecentTask].self, from: data) {
                    self.claudeTasks = value
                }
            case .failure(let error): self.message = error.localizedDescription
            }
        }
    }

    func refreshClaudeUsage() {
        guard !refreshingClaudeUsage else { return }
        refreshingClaudeUsage = true
        command(["claude-usage"]) { result in
            self.refreshingClaudeUsage = false
            switch result {
            case .success(let text):
                if let data = text.data(using: .utf8), let value = try? JSONDecoder().decode(ClaudeUsage.self, from: data) {
                    self.claudeUsage = value
                    self.claudeUsageError = nil
                } else { self.claudeUsageError = "本机用量解析失败" }
            case .failure(let error): self.claudeUsageError = error.localizedDescription
            }
        }
    }

    func loadGateway() {
        gatewayKeyConfigured = GatewayKeychain.exists()
        command(["gateway"]) { result in
            if case .success(let text) = result,
               let data = text.data(using: .utf8),
               let value = try? JSONDecoder().decode(GatewayConfig.self, from: data) {
                self.gatewayURL = value.url
                self.gatewayModel = value.model
            }
        }
    }

    func loadSharedProject() {
        command(["shared-project"]) { result in
            if case .success(let text) = result,
               let data = text.data(using: .utf8),
               let value = try? JSONDecoder().decode(SharedProjectConfig.self, from: data) {
                self.sharedProjectPath = value.path
            }
        }
    }

    func selectSharedProject() {
        let panel = NSOpenPanel()
        panel.title = "选择两个账号共用的项目文件夹"
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        guard panel.runModal() == .OK, let path = panel.url?.path else { return }
        guard !busy else { return }
        busy = true
        command(["set-shared-project", path]) { result in
            self.busy = false
            switch result {
            case .success:
                self.loadSharedProject()
                self.message = "已设置共享项目文件夹"
            case .failure(let error): self.message = error.localizedDescription
            }
        }
    }

    func openSharedProject(profile: String) {
        guard !busy else { return }
        busy = true
        message = nil
        command(["launch-shared-project", profile]) { result in
            self.busy = false
            switch result {
            case .success: self.message = "已在共享项目文件夹打开 Codex CLI"
            case .failure(let error): self.message = error.localizedDescription
            }
        }
    }

    func syncDesktopProjects(showMessage: Bool = true) {
        guard !syncingProjects && !busy else { return }
        syncingProjects = true
        let args = showMessage ? ["sync-desktop-workspace", "--refresh-windows"] : ["sync-desktop-workspace"]
        command(args) { result in
            self.syncingProjects = false
            switch result {
            case .success(let text):
                self.loadDesktopProjects()
                if let data = text.data(using: .utf8),
                   let value = try? JSONDecoder().decode(ProjectSyncResult.self, from: data) {
                    if let history = value.history {
                        self.conversationSyncIssues = history.pending + history.errors
                        self.conversationSyncSummary = "本次更新 \(history.updated) 条原会话 · 无变化 \(history.unchanged) 条 · 待处理 \(self.conversationSyncIssues.count) 条"
                        if history.updated > 0 {
                            self.conversationSyncSummary += "。继续使用原来的会话条目即可。"
                        }
                        if showMessage { self.message = self.conversationSyncSummary }
                    }
                }
            case .failure(let error):
                self.conversationSyncSummary = "同步未完成：" + error.localizedDescription
                if showMessage { self.message = error.localizedDescription }
            }
        }
    }

    func loadDesktopProjects() {
        command(["desktop-projects"]) { result in
            if case .success(let text) = result,
               let data = text.data(using: .utf8),
               let value = try? JSONDecoder().decode([DesktopProject].self, from: data) {
                self.desktopProjects = value
            }
        }
    }

    func saveGateway(url: String, model: String, key: String) {
        guard !busy else { return }
        busy = true
        message = nil
        command(["set-gateway", url, model]) { result in
            self.busy = false
            switch result {
            case .success:
                if !key.isEmpty {
                    do { try GatewayKeychain.save(key) }
                    catch { self.message = "密钥保存失败：\(error.localizedDescription)"; return }
                }
                self.gatewayKeyConfigured = GatewayKeychain.exists()
                self.loadGateway()
                self.message = "服务商配置已保存"
            case .failure(let error): self.message = error.localizedDescription
            }
        }
    }

    func prepareClaudeHandoff(task: RecentTask) {
        guard !busy else { return }
        busy = true
        message = nil
        command(["claude-handoff", task.id]) { result in
            self.busy = false
            switch result {
            case .success(let text):
                if let data = text.data(using: .utf8), let value = try? JSONDecoder().decode(Handoff.self, from: data) {
                    self.handoff = value
                } else { self.message = "Claude Code 交接包解析失败" }
            case .failure(let error): self.message = error.localizedDescription
            }
        }
    }

    func openClaudeCLI() {
        guard !busy else { return }
        guard GatewayKeychain.exists() else {
            gatewayKeyConfigured = false
            message = "请先在设置中保存 Claude Code 服务商密钥"
            return
        }
        busy = true
        message = nil
        let cwd = sharedProjectPath.isEmpty ? NSHomeDirectory() : sharedProjectPath
        command(["launch-claude", cwd]) { result in
            self.busy = false
            switch result {
            case .success: self.message = "已打开 Claude Code CLI，使用已配置的第三方模型"
            case .failure(let error): self.message = error.localizedDescription
            }
        }
    }

    func launchHandoff(content: String, codexProfile: String? = nil) {
        guard !busy, let handoff else { return }
        busy = true
        message = nil
        do {
            let url = URL(fileURLWithPath: handoff.path)
            try content.write(to: url, atomically: true, encoding: .utf8)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: handoff.path)
        } catch {
            busy = false
            message = "保存交接包失败：\(error.localizedDescription)"
            return
        }
        let args = handoff.target == "codex"
            ? ["launch-codex-handoff", handoff.path, handoff.cwd, codexProfile ?? "primary"]
            : ["launch-handoff", handoff.path, handoff.cwd]
        command(args) { result in
            self.busy = false
            switch result {
            case .success: self.message = handoff.target == "codex" ? "已在终端打开 Codex CLI" : "已在终端打开 Claude Code"
            case .failure(let error): self.message = error.localizedDescription
            }
        }
    }

    func launchDesktopHandoff(content: String, targetProfile: String) {
        guard !busy, let handoff else { return }
        busy = true
        message = nil
        do {
            let url = URL(fileURLWithPath: handoff.path)
            try content.write(to: url, atomically: true, encoding: .utf8)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: handoff.path)
        } catch {
            busy = false
            message = "保存交接包失败：\(error.localizedDescription)"
            return
        }
        command(["launch-desktop-handoff", handoff.path, handoff.cwd, targetProfile]) { result in
            self.busy = false
            switch result {
            case .success:
                if let account = self.accounts.first(where: { $0.id == targetProfile }) {
                    self.open(account)
                }
                self.message = "已在目标账号创建桌面任务，请在该窗口的最近任务中查看"
            case .failure(let error): self.message = error.localizedDescription
            }
        }
    }

    func hide(_ account: Account) {
        guard let pid = account.pid else { return }
        NSRunningApplication(processIdentifier: pid)?.hide()
        refresh(fetch: false)
    }

    func quit(_ account: Account) {
        guard let pid = account.pid else { return }
        if NSRunningApplication(processIdentifier: pid)?.terminate() != true {
            message = "未能退出 \(account.name)"
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.7) { self.refresh(fetch: false) }
    }

    func hideAll() {
        accounts.forEach { if let pid = $0.pid { NSRunningApplication(processIdentifier: pid)?.hide() } }
        refresh(fetch: false)
    }

    func quitAll() {
        accounts.forEach { if let pid = $0.pid { NSRunningApplication(processIdentifier: pid)?.terminate() } }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.7) { self.refresh(fetch: false) }
    }

    func showOnly(_ account: Account) {
        accounts.filter { $0.id != account.id }.forEach {
            if let pid = $0.pid { NSRunningApplication(processIdentifier: pid)?.hide() }
        }
        open(account)
    }

    func openBoth(tile: Bool = false) {
        guard !busy else { return }
        busy = true
        command(["open", "primary"]) { first in
            switch first {
            case .failure(let error): self.busy = false; self.message = error.localizedDescription
            case .success(let firstText):
                self.command(["open", "secondary"]) { second in
                    self.busy = false
                    switch second {
                    case .failure(let error): self.message = error.localizedDescription
                    case .success(let text):
                        let firstPID = Int32(firstText)
                        let secondPID = Int32(text)
                        if let firstPID { NSRunningApplication(processIdentifier: firstPID)?.unhide() }
                        if let secondPID { NSRunningApplication(processIdentifier: secondPID)?.unhide() }
                        if tile, let firstPID, let secondPID,
                           !WindowArranger.arrange(left: firstPID, right: secondPID) {
                            self.message = "两个窗口已打开。并排排列需要在系统设置中允许 Codex Duo 使用辅助功能。"
                        }
                        if let secondPID { NSRunningApplication(processIdentifier: secondPID)?.activate(options: [.activateAllWindows]) }
                        self.refresh(fetch: false)
                    }
                }
            }
        }
    }
}

@MainActor final class AppDelegate: NSObject, NSApplicationDelegate {
    private let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    private let model = DuoModel()
    private var window: NSWindow?
    private var quotaTimer: Timer?
    private var loginTimer: Timer?
    private var projectTimer: Timer?

    func applicationDidFinishLaunching(_ notification: Notification) {
        model.foregroundPID = NSWorkspace.shared.frontmostApplication?.processIdentifier
        NSApp.applicationIconImage = DuoAppIcon.make()
        statusItem.button?.title = "◇ Duo"
        statusItem.button?.target = self
        statusItem.button?.action = #selector(showWindow)
        let panel = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 790, height: 740),
                             styleMask: [.titled, .closable, .miniaturizable, .resizable],
                             backing: .buffered, defer: false)
        panel.title = "Codex Duo"
        panel.contentView = NSHostingView(rootView: ControlDashboard(model: model))
        panel.center()
        panel.makeKeyAndOrderFront(nil)
        window = panel
        NSApp.activate(ignoringOtherApps: true)
        model.refresh()
        model.loadGateway()
        model.loadSharedProject()
        model.syncDesktopProjects(showMessage: false)
        quotaTimer = Timer.scheduledTimer(withTimeInterval: 30, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.model.refresh() }
        }
        loginTimer = Timer.scheduledTimer(withTimeInterval: 10, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.model.refresh(fetch: false) }
        }
        projectTimer = Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.model.syncDesktopProjects(showMessage: false) }
        }
    }

    @objc private func showWindow() {
        window?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        model.refresh(fetch: false)
    }
}

@main struct CodexDuoApp {
    @MainActor static func main() {
        if CommandLine.arguments.count == 3, CommandLine.arguments[1] == "--export-icon" {
            let image = DuoAppIcon.make()
            if let tiff = image.tiffRepresentation,
               let bitmap = NSBitmapImageRep(data: tiff),
               let png = bitmap.representation(using: .png, properties: [:]) {
                try? png.write(to: URL(fileURLWithPath: CommandLine.arguments[2]))
            }
            return
        }
        let app = NSApplication.shared
        app.setActivationPolicy(.regular)
        let delegate = AppDelegate()
        app.delegate = delegate
        app.run()
    }
}
