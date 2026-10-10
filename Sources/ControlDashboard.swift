import AppKit
import SwiftUI

private let canvas = Color(nsColor: .windowBackgroundColor)
private let panelColor = Color(nsColor: .controlBackgroundColor)
private let accent = Color.accentColor

private struct DuoMark: View {
    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 11)
                .fill(LinearGradient(colors: [.blue, .indigo], startPoint: .topLeading, endPoint: .bottomTrailing))
            RoundedRectangle(cornerRadius: 4)
                .fill(.white.opacity(0.96))
                .frame(width: 17, height: 17)
                .offset(x: -6, y: -5)
            RoundedRectangle(cornerRadius: 4)
                .fill(.white.opacity(0.72))
                .frame(width: 17, height: 17)
                .offset(x: 6, y: 5)
            Circle().fill(.orange).frame(width: 7, height: 7).offset(x: 10, y: -9)
            Circle().fill(.mint).frame(width: 6, height: 6).offset(x: -10, y: 10)
        }
        .frame(width: 42, height: 42)
        .shadow(color: .blue.opacity(0.16), radius: 7, y: 3)
    }
}

private struct StateBadge: View {
    let title: String
    let color: Color
    var body: some View {
        HStack(spacing: 5) {
            Circle().fill(color).frame(width: 7, height: 7)
            Text(title).font(.caption.weight(.medium))
        }
        .padding(.horizontal, 9).padding(.vertical, 5)
        .background(color.opacity(0.14), in: Capsule())
    }
}

private struct LimitRow: View {
    let window: WindowUsage
    let color: Color
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(window.label).foregroundStyle(.secondary)
                Spacer()
                Text("剩余 \(window.remaining)%").monospacedDigit().fontWeight(.semibold)
            }
            GeometryReader { geometry in
                ZStack(alignment: .leading) {
                    Capsule().fill(color.opacity(0.12))
                    Capsule().fill(window.remaining <= 20 ? Color.red : color)
                        .frame(width: geometry.size.width * CGFloat(max(0, min(100, window.remaining))) / 100)
                }
            }
            .frame(height: 6)
            .accessibilityLabel("\(window.label)额度")
            .accessibilityValue("剩余 \(window.remaining)%")
            if let reset = window.resetsAt {
                Text("重置：\(Date(timeIntervalSince1970: TimeInterval(reset)).formatted(date: .abbreviated, time: .shortened))")
                    .font(.caption2).foregroundStyle(.secondary)
            }
        }.font(.caption)
    }
}

private extension Membership {
    var color: Color {
        switch self {
        case .pro: return Color(red: 0.88, green: 0.72, blue: 0.42)
        case .plus: return Color(red: 0.43, green: 0.35, blue: 0.80)
        case .free: return Color(red: 0.36, green: 0.47, blue: 0.52)
        case .unknown: return .gray
        }
    }

    var surface: [Color] {
        switch self {
        case .pro: return [Color(red: 0.19, green: 0.18, blue: 0.16), Color(red: 0.09, green: 0.10, blue: 0.12)]
        case .plus: return [Color(red: 0.95, green: 0.92, blue: 1), Color(red: 0.90, green: 0.95, blue: 1)]
        case .free, .unknown: return [Color(white: 0.98), Color(red: 0.90, green: 0.93, blue: 0.94)]
        }
    }
}

private struct MembershipEmblem: View {
    let membership: Membership
    var body: some View {
        Image(systemName: membership.symbol)
            .font(.system(size: 20, weight: .medium))
            .foregroundStyle(membership.color)
            .frame(width: 44, height: 44)
            .background(
                LinearGradient(colors: [membership.color.opacity(0.23), membership.color.opacity(0.04)],
                               startPoint: .topLeading, endPoint: .bottomTrailing),
                in: RoundedRectangle(cornerRadius: 14))
            .overlay(RoundedRectangle(cornerRadius: 14).stroke(membership.color.opacity(0.35)))
            .accessibilityHidden(true)
    }
}

private extension Account {
    var identityColor: Color {
        id == "primary" ? Color(red: 0.26, green: 0.57, blue: 0.96) : Color(red: 0.24, green: 0.72, blue: 0.61)
    }
}

private struct AccountIdentityBadge: View {
    let account: Account
    var body: some View {
        Text(account.identityCode)
            .font(.system(size: 13, weight: .bold, design: .rounded))
            .foregroundStyle(account.identityColor)
            .frame(minWidth: 30, minHeight: 30)
            .background(account.identityColor.opacity(0.13), in: RoundedRectangle(cornerRadius: 9))
            .overlay(RoundedRectangle(cornerRadius: 9).stroke(account.identityColor.opacity(0.35)))
            .accessibilityLabel("固定账号标识 \(account.identityCode)")
            .help("固定工作区标识，改名和订阅变化后保持不变")
    }
}

private struct ControlAccountCard: View {
    let account: Account
    let foreground: Bool
    @ObservedObject var model: DuoModel
    let requestQuit: (Account) -> Void
    private var accountColor: Color { account.membership.color }

    var body: some View {
        VStack(alignment: .leading, spacing: 11) {
            HStack(spacing: 11) {
                MembershipEmblem(membership: account.membership)
                VStack(alignment: .leading, spacing: 5) {
                    Text(account.membership.label)
                        .font(.system(size: 9, weight: .bold, design: .rounded)).tracking(2)
                        .foregroundStyle(accountColor)
                    Text(account.nickname).font(.system(size: 18, weight: .semibold))
                        .lineLimit(1).minimumScaleFactor(0.75)
                        .help(account.displayName)
                }
                Spacer(minLength: 0)
                AccountIdentityBadge(account: account)
            }
            HStack {
                Text(account.id == "primary" ? "A  /  主工作区" : "B  /  第二工作区")
                    .font(.system(size: 10, weight: .medium)).foregroundStyle(.secondary)
                Spacer()
                StateBadge(title: account.pid == nil ? "未打开" : foreground ? "当前窗口" : "实例运行",
                           color: account.pid == nil ? .gray : foreground ? .green : accountColor)
            }
            Divider()
            if account.loggedIn {
                if let usage = account.usage, account.visibleUsageKeys.contains(where: { usage[$0] != nil }) {
                    ForEach(account.visibleUsageKeys, id: \.self) { key in
                        if let window = usage[key] { LimitRow(window: window, color: accountColor) }
                    }
                } else {
                    ContentUnavailableView("等待额度数据", systemImage: "chart.bar", description: Text("登录完成后自动刷新"))
                        .frame(height: 100)
                }
            } else {
                VStack(alignment: .leading, spacing: 6) {
                    Image(systemName: "person.crop.circle.badge.questionmark").font(.title2)
                    Text("尚未登录").font(.headline)
                    Text("点击下方按钮，在 Codex 窗口完成网页登录。")
                        .font(.caption).foregroundStyle(.secondary)
                }.frame(maxWidth: .infinity, minHeight: 100, alignment: .leading)
            }
            if let error = account.error {
                Label("额度更新失败：\(error)", systemImage: "exclamationmark.triangle")
                    .font(.caption2).foregroundStyle(.orange).lineLimit(2)
            }
            Spacer(minLength: 0)
            HStack(spacing: 9) {
                Button { model.open(account) } label: {
                    Text(account.pid == nil ? "打开 Codex" : "切换到窗口")
                        .font(.callout.weight(.semibold))
                        .foregroundStyle(account.membership == .pro ? Color.black.opacity(0.85) : .white)
                        .padding(.horizontal, 13).padding(.vertical, 7)
                        .background(accountColor, in: RoundedRectangle(cornerRadius: 8))
                }
                .buttonStyle(.plain).disabled(model.busy).opacity(model.busy ? 0.5 : 1)
                Menu {
                    Button("只显示此账号") { model.showOnly(account) }
                    Button("隐藏此窗口") { model.hide(account) }.disabled(account.pid == nil)
                    Divider()
                    Button("退出此账号实例…", role: .destructive) { requestQuit(account) }.disabled(account.pid == nil)
                } label: { Image(systemName: "ellipsis").frame(width: 24) }
                    .menuStyle(.borderlessButton)
            }
            HStack {
                Text(account.pid == nil ? "实例未运行" : "隐藏窗口不会停止后台任务")
                Spacer()
                if let updated = account.updatedAt {
                    Text(Date(timeIntervalSince1970: TimeInterval(updated)).formatted(date: .omitted, time: .shortened))
                }
            }.font(.caption2).foregroundStyle(.secondary)
        }
        .padding(18)
        .frame(maxWidth: .infinity, minHeight: 340, maxHeight: 340, alignment: .topLeading)
        .background {
            ZStack(alignment: .topTrailing) {
                LinearGradient(colors: account.membership.surface, startPoint: .topLeading, endPoint: .bottomTrailing)
                Circle().stroke(accountColor.opacity(0.10), lineWidth: 28)
                    .frame(width: 170, height: 170).offset(x: 62, y: -95)
                Image(systemName: account.membership.symbol)
                    .font(.system(size: 86, weight: .ultraLight))
                    .rotationEffect(.degrees(-16)).foregroundStyle(accountColor.opacity(0.07))
                    .offset(x: 10, y: 18)
            }
            .clipShape(RoundedRectangle(cornerRadius: 20))
        }
        .overlay(RoundedRectangle(cornerRadius: 20)
            .stroke(LinearGradient(colors: [accountColor.opacity(foreground ? 0.8 : 0.45), accountColor.opacity(0.08), accountColor.opacity(0.25)],
                                   startPoint: .topLeading, endPoint: .bottomTrailing)))
        .shadow(color: accountColor.opacity(0.10), radius: 12, y: 5)
        .environment(\.colorScheme, account.membership == .pro ? .dark : .light)
    }
}

private struct MiniChart: View {
    let points: [HistoryPoint]
    let value: (HistoryPoint) -> Int?
    let color: Color

    var body: some View {
        GeometryReader { geometry in
            let numbers = points.compactMap(value)
            if numbers.count > 1 {
                Path { path in
                    for (index, number) in numbers.enumerated() {
                        let x = geometry.size.width * CGFloat(index) / CGFloat(numbers.count - 1)
                        let y = geometry.size.height * (1 - CGFloat(number) / 100)
                        if index == 0 { path.move(to: CGPoint(x: x, y: y)) }
                        else { path.addLine(to: CGPoint(x: x, y: y)) }
                    }
                }.stroke(color, style: StrokeStyle(lineWidth: 2.5, lineCap: .round, lineJoin: .round))
            } else {
                Text("积累两次额度记录后显示曲线")
                    .font(.caption).foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .frame(height: 110)
        .background(Color.primary.opacity(0.035), in: RoundedRectangle(cornerRadius: 8))
    }
}

private struct HistoryCard: View {
    let account: Account
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 7) {
                Image(systemName: account.membership.symbol).foregroundStyle(account.membership.color)
                Text(account.displayName).font(.headline)
            }
            if account.membership != .pro {
                HStack {
                    Circle().fill(accent).frame(width: 7, height: 7)
                    Text("5 小时剩余")
                    Spacer()
                    if let value = account.history?.last?.fiveHour { Text("\(value)%").monospacedDigit() }
                }.font(.caption)
                MiniChart(points: account.history ?? [], value: { $0.fiveHour }, color: account.membership.color)
            }
            HStack {
                Circle().fill(.green).frame(width: 7, height: 7)
                Text("每周剩余")
                Spacer()
                if let value = account.history?.last?.weekly { Text("\(value)%").monospacedDigit() }
            }.font(.caption)
            MiniChart(points: account.history ?? [], value: { $0.weekly }, color: .green)
        }
        .padding(18).frame(maxWidth: .infinity, alignment: .leading)
        .background(panelColor, in: RoundedRectangle(cornerRadius: 15))
        .overlay(RoundedRectangle(cornerRadius: 15).stroke(Color.primary.opacity(0.07)))
    }
}

private struct RenameRow: View {
    let account: Account
    @ObservedObject var model: DuoModel
    @State private var name: String = ""
    var body: some View {
        HStack {
            AccountIdentityBadge(account: account)
            Text("账号昵称").frame(width: 70, alignment: .leading)
            TextField(account.nickname, text: $name).textFieldStyle(.roundedBorder)
            Button("保存") {
                model.rename(account, to: name.isEmpty ? account.name : name)
            }.disabled(model.busy)
        }
        .onAppear { name = account.nickname }
    }
}

struct ControlDashboard: View {
    @ObservedObject var model: DuoModel
    @State private var tab = 0
    @State private var quitTarget: Account?
    @State private var quitEverything = false
    @State private var handoffText = ""
    @State private var gatewayURL = "https://api.do.top"
    @State private var gatewayModel = "DeepSeek-V4.1-Flash"
    @State private var gatewayKey = ""

    private var runningCount: Int { model.accounts.filter { $0.pid != nil }.count }
    var body: some View {
        VStack(alignment: .leading, spacing: 17) {
            header
            Picker("页面", selection: $tab) {
                Text("概览").tag(0)
                Text("最近任务").tag(1)
                Text("任务交接").tag(2)
                Text("用量趋势").tag(3)
                Text("设置").tag(4)
                Text("共享项目").tag(5)
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            content
            if let message = model.message {
                Label(message, systemImage: "exclamationmark.circle")
                    .font(.caption).foregroundStyle(.orange).lineLimit(2)
            }
            HStack {
                Text("额度 / 本机用量每 30 秒刷新 · 窗口状态每 10 秒更新")
                Spacer()
                Text("Codex Duo · 本地运行")
            }.font(.caption2).foregroundStyle(.tertiary)
        }
        .padding(22)
        .frame(width: 790, height: 740, alignment: .top)
        .background(canvas)
        .confirmationDialog("退出 \(quitTarget?.displayName ?? "") 的 Codex 实例？", isPresented: Binding(
            get: { quitTarget != nil }, set: { if !$0 { quitTarget = nil } }
        ), titleVisibility: .visible) {
            Button("退出实例", role: .destructive) {
                if let target = quitTarget { model.quit(target) }
                quitTarget = nil
            }
            Button("取消", role: .cancel) { quitTarget = nil }
        } message: { Text("这会关闭该账号的 Codex 进程，正在执行的任务可能中断。") }
        .confirmationDialog("退出两个 Codex 实例？", isPresented: $quitEverything, titleVisibility: .visible) {
            Button("退出两个实例", role: .destructive) { model.quitAll() }
            Button("取消", role: .cancel) { }
        } message: { Text("正在执行的任务可能中断。Codex Duo 控制台本身会保持打开。") }
        .onChange(of: model.handoff?.path) { _, _ in handoffText = model.handoff?.content ?? "" }
        .onChange(of: tab) { _, value in
            if value == 1 || value == 2 { model.refreshClaudeTasks() }
            if value == 3 { model.refreshClaudeUsage() }
            if value == 5 { model.loadDesktopProjects() }
        }
        .onChange(of: model.gatewayURL) { _, value in gatewayURL = value }
        .onChange(of: model.gatewayModel) { _, value in gatewayModel = value }
    }

    private var header: some View {
        HStack(spacing: 13) {
            DuoMark()
            VStack(alignment: .leading, spacing: 3) {
                Text("Codex Duo").font(.title2.weight(.semibold))
                Text("桌面与终端，一处掌握").font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            Button { model.refresh() } label: {
                Label(model.refreshing ? "更新中" : "刷新全部", systemImage: "arrow.clockwise")
            }.buttonStyle(.bordered).disabled(model.busy || model.refreshing)
        }
    }

    private var controls: some View {
        HStack(spacing: 10) {
            Button { model.openBoth() } label: { Label("打开两个", systemImage: "macwindow.on.rectangle") }
            Button { model.openBoth(tile: true) } label: { Label("并排显示", systemImage: "rectangle.split.2x1") }
            Button { model.hideAll() } label: { Label("隐藏全部", systemImage: "eye.slash") }
                .disabled(runningCount == 0)
            Menu {
                ForEach(model.accounts) { account in
                    Button("只显示 \(account.displayName)") { model.showOnly(account) }
                }
            } label: { Label("只显示一个", systemImage: "rectangle.on.rectangle") }
            Spacer()
            Button(role: .destructive) { quitEverything = true } label: {
                Label("退出两个实例", systemImage: "power")
            }.disabled(runningCount == 0)
        }.buttonStyle(.bordered).disabled(model.busy)
    }

    private func sectionHeading(_ title: String, subtitle: String, icon: String, color: Color, divided: Bool = false) -> some View {
        VStack(alignment: .leading, spacing: 20) {
            if divided {
                Rectangle().fill(LinearGradient(colors: [.clear, color.opacity(0.4), .clear],
                                                startPoint: .leading, endPoint: .trailing)).frame(height: 1)
                    .padding(.top, 6)
            }
            HStack(spacing: 9) {
                Image(systemName: icon).foregroundStyle(color)
                    .frame(width: 28, height: 28)
                    .background(color.opacity(0.10), in: RoundedRectangle(cornerRadius: 8))
                Text(title).font(.headline)
                Text(subtitle).font(.caption).foregroundStyle(.secondary)
                Spacer()
            }
        }
    }

    private var codexHeading: some View {
        HStack {
            sectionHeading("Codex", subtitle: "桌面账号 · 订阅额度", icon: "macwindow.on.rectangle", color: .blue)
            StateBadge(title: "\(runningCount)/2 运行", color: runningCount == 2 ? .green : .orange)
        }
    }

    private var claudeHeading: some View {
        sectionHeading("Claude Code", subtitle: "终端 · 本机 Token 消耗", icon: "terminal.fill",
                       color: Color(red: 0.83, green: 0.48, blue: 0.35), divided: true)
    }

    private func taskRow(_ task: RecentTask, color: Color) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: "text.bubble").foregroundStyle(color)
            VStack(alignment: .leading, spacing: 3) {
                Text(task.title).lineLimit(2)
                Text(taskDate(task.updatedAt)).font(.caption2).foregroundStyle(.secondary)
            }
            Spacer(minLength: 0)
        }.font(.caption).padding(.vertical, 5)
    }

    private func taskCard(_ title: String, tasks: [RecentTask], color: Color) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Circle().fill(color).frame(width: 7, height: 7)
                Text(title).font(.headline)
                Spacer()
                Text("\(tasks.count) 项").font(.caption).foregroundStyle(.secondary)
            }
            if tasks.isEmpty {
                Text("暂无本地任务记录").font(.caption).foregroundStyle(.secondary).padding(.vertical, 10)
            } else {
                ForEach(Array(tasks.prefix(5))) { task in taskRow(task, color: color) }
                if tasks.count > 5 {
                    DisclosureGroup("更多任务（\(tasks.count - 5)）") {
                        ForEach(Array(tasks.dropFirst(5))) { task in taskRow(task, color: color) }
                    }.font(.caption)
                }
            }
        }
        .padding(16).frame(maxWidth: .infinity, alignment: .topLeading)
        .background(panelColor, in: RoundedRectangle(cornerRadius: 16))
        .overlay(RoundedRectangle(cornerRadius: 16).stroke(color.opacity(0.18)))
    }

    @ViewBuilder private var content: some View {
        if tab == 0 {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    codexHeading
                    controls
                    if model.accounts.isEmpty {
                        ProgressView("正在读取账号状态…").frame(maxWidth: .infinity, minHeight: 160)
                    } else {
                        HStack(alignment: .top, spacing: 14) {
                            ForEach(model.accounts) { account in
                                ControlAccountCard(account: account, foreground: account.pid != nil && account.pid == model.foregroundPID,
                                                   model: model, requestQuit: { quitTarget = $0 })
                            }
                        }
                    }
                    claudeHeading
                    ClaudeUsagePanel(model: model, configure: { tab = 4 })
                }.padding(.bottom, 8)
            }.frame(maxHeight: .infinity)
        } else if tab == 1 {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    sectionHeading("Codex", subtitle: "两个账号的最近任务", icon: "macwindow.on.rectangle", color: .blue)
                    HStack(alignment: .top, spacing: 14) {
                        ForEach(model.accounts) { account in
                            taskCard(account.displayName, tasks: account.recentTasks ?? [], color: account.identityColor)
                        }
                    }
                    claudeHeading
                    taskCard("Claude Code · 本机任务", tasks: model.claudeTasks, color: Color(red: 0.83, green: 0.48, blue: 0.35))
                }.padding(.bottom, 8)
            }.frame(maxHeight: .infinity)
        } else if tab == 2 {
            handoffContent
        } else if tab == 3 {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    sectionHeading("Codex", subtitle: "额度剩余比例", icon: "chart.xyaxis.line", color: .blue)
                    HStack(alignment: .top, spacing: 14) {
                        ForEach(model.accounts) { HistoryCard(account: $0) }
                    }
                    claudeHeading
                    ClaudeTokenHistory(model: model)
                }.padding(.bottom, 8)
            }.frame(maxHeight: .infinity)
        } else if tab == 4 {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    Text("账号显示名称").font(.headline)
                    ForEach(model.accounts) { account in RenameRow(account: account, model: model) }
                    Divider()
                    Text("Claude Code 服务商").font(.headline)
                    HStack {
                        Text("接口根地址").frame(width: 120, alignment: .leading)
                        TextField("https://…", text: $gatewayURL).textFieldStyle(.roundedBorder)
                    }
                    HStack {
                        Text("模型 ID").frame(width: 120, alignment: .leading)
                        TextField("模型名称", text: $gatewayModel).textFieldStyle(.roundedBorder)
                    }
                    HStack {
                        Text("服务商密钥").frame(width: 120, alignment: .leading)
                        SecureField(model.gatewayKeyConfigured ? "已保存；留空则沿用" : "粘贴服务商密钥", text: $gatewayKey)
                            .textFieldStyle(.roundedBorder)
                    }
                    HStack {
                        Button("保存服务商配置") {
                            model.saveGateway(url: gatewayURL, model: gatewayModel, key: gatewayKey)
                            gatewayKey = ""
                        }.disabled(model.busy || (!model.gatewayKeyConfigured && gatewayKey.isEmpty))
                        if model.gatewayKeyConfigured {
                            Label("密钥已保存在 macOS 钥匙串", systemImage: "checkmark.shield")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    Text("交接到 Claude Code 时仅该终端会话使用此配置。请确认服务商提供 Anthropic Messages API 兼容接口。")
                        .font(.caption).foregroundStyle(.secondary)
                    Divider()
                    Label("额度每 30 秒自动刷新；登录状态和窗口状态每 10 秒检查。", systemImage: "arrow.clockwise")
                    Label("隐藏窗口会保留 Codex 实例；退出实例可能中断正在执行的任务。", systemImage: "info.circle")
                    Button("退出 Codex Duo 控制台") { NSApp.terminate(nil) }
                        .buttonStyle(.bordered)
                }
                .padding(20).frame(maxWidth: .infinity, alignment: .topLeading)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(panelColor, in: RoundedRectangle(cornerRadius: 15))
        } else {
            sharedProjectContent
        }
    }

    private var sharedProjectContent: some View {
        ScrollView {
        VStack(alignment: .leading, spacing: 18) {
            Label("两个 Codex 桌面窗口，共用项目", systemImage: "folder.badge.person.crop")
                .font(.headline)
            Text("一键同步两边的项目和会话，以最新完成的对话为准，更新原来的会话条目。")
                .font(.subheadline).foregroundStyle(.secondary)
            HStack {
                Button {
                    model.syncDesktopProjects()
                } label: {
                    Label(model.syncingProjects ? "同步中…" : "同步会话与项目", systemImage: "arrow.triangle.2.circlepath")
                }.disabled(model.syncingProjects || model.busy)
            }.buttonStyle(.borderedProminent)
            Text("较新内容覆盖旧版本，并自动刷新需要更新的窗口。任务正在执行时，完成后自动同步；旧版本会先备份。")
                .font(.caption).foregroundStyle(.secondary)
            if !model.conversationSyncSummary.isEmpty {
                Text(model.conversationSyncSummary).font(.caption).textSelection(.enabled)
            }
            ForEach(model.conversationSyncIssues) { issue in
                VStack(alignment: .leading, spacing: 3) {
                    Text(issue.title).font(.caption.weight(.medium)).lineLimit(1)
                    Text(issue.reason).font(.caption2).foregroundStyle(.orange)
                }
            }
            if model.desktopProjects.isEmpty {
                Text("主账号暂无本地项目").font(.caption).foregroundStyle(.secondary)
            } else {
                ScrollView {
                    VStack(spacing: 6) {
                        ForEach(model.desktopProjects) { project in
                            HStack(spacing: 10) {
                                Image(systemName: "folder.fill").foregroundStyle(.blue)
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(project.name).font(.subheadline.weight(.medium))
                                    Text(project.path).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                                }
                                Spacer()
                                StateBadge(title: project.synced ? "两边可见" : "待同步", color: project.synced ? .green : .orange)
                            }
                            .padding(.horizontal, 12).padding(.vertical, 8)
                            .background(Color.primary.opacity(0.04), in: RoundedRectangle(cornerRadius: 8))
                        }
                    }
                }
                .frame(maxHeight: 150)
            }
            Divider()
            Text("下方 CLI 入口用于需要在终端操作项目的情况。桌面项目同步不依赖它。")
                .font(.caption).foregroundStyle(.secondary)
            Text("选择本机项目文件夹后，两个账号的 Codex CLI 都从此目录启动。保存的代码和未提交修改会立即对双方可见。")
                .font(.subheadline).foregroundStyle(.secondary)
            HStack {
                Image(systemName: "folder.fill").foregroundStyle(.blue)
                Text(model.sharedProjectPath.isEmpty ? "尚未选择项目文件夹" : model.sharedProjectPath)
                    .lineLimit(2).textSelection(.enabled)
                Spacer()
                Button(model.sharedProjectPath.isEmpty ? "选择文件夹" : "更换文件夹") {
                    model.selectSharedProject()
                }.disabled(model.busy)
            }
            .padding(14)
            .background(Color.primary.opacity(0.045), in: RoundedRectangle(cornerRadius: 10))
            HStack(spacing: 12) {
                ForEach(model.accounts) { account in
                    Button {
                        model.openSharedProject(profile: account.id)
                    } label: {
                        Label("用 \(account.displayName) 打开", systemImage: "terminal")
                            .frame(maxWidth: .infinity)
                    }
                    .disabled(model.busy || model.sharedProjectPath.isEmpty || !account.loggedIn)
                }
            }.buttonStyle(.borderedProminent)
            Text("项目目录已共用。接收对话更新后可从原会话继续，也可以使用任务交接创建独立任务。")
                .font(.caption).foregroundStyle(.secondary)
            Text("同时开发时请让两个任务负责不同文件；修改同一文件可能互相覆盖。需要完全隔离的并行修改时，使用 Git 分支或 worktree。")
                .font(.caption).foregroundStyle(.secondary)
            Spacer()
        }
        .padding(20)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        }
        .background(panelColor, in: RoundedRectangle(cornerRadius: 15))
    }

    private var handoffContent: some View {
        HStack(alignment: .top, spacing: 14) {
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    Label("选择来源任务", systemImage: "arrow.left.arrow.right")
                        .font(.headline)
                    Text("Codex 与 Claude Code 可以轮流接手。建议等当前步骤结束后再生成交接包。")
                        .font(.caption).foregroundStyle(.secondary)
                    ForEach(model.accounts) { account in
                        Text("Codex · \(account.displayName)").font(.caption.weight(.semibold)).foregroundStyle(account.membership.color)
                            .padding(.top, 8)
                        if let tasks = account.recentTasks, !tasks.isEmpty {
                            ForEach(tasks) { task in
                                Button {
                                    model.prepareHandoff(account: account, task: task)
                                } label: {
                                    HStack {
                                        Text(task.title).lineLimit(2).multilineTextAlignment(.leading)
                                        Spacer(minLength: 4)
                                        Image(systemName: "chevron.right")
                                    }
                                    .font(.caption)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                }
                                .buttonStyle(.borderless)
                                .disabled(model.busy)
                                Divider()
                            }
                        } else {
                            Text("暂无本地任务").font(.caption).foregroundStyle(.tertiary)
                        }
                    }
                    Divider()
                    HStack {
                        Text("Claude Code").font(.caption.weight(.semibold)).foregroundStyle(.purple)
                        Spacer()
                        Button("刷新") { model.refreshClaudeTasks() }.font(.caption2)
                    }
                    if model.claudeTasks.isEmpty {
                        Text("暂无本地 Claude Code CLI 任务")
                            .font(.caption).foregroundStyle(.tertiary)
                    } else {
                        ForEach(model.claudeTasks) { task in
                            Button { model.prepareClaudeHandoff(task: task) } label: {
                                HStack {
                                    Text(task.title).lineLimit(2).multilineTextAlignment(.leading)
                                    Spacer(minLength: 4)
                                    Image(systemName: "chevron.right")
                                }
                                .font(.caption)
                                .frame(maxWidth: .infinity, alignment: .leading)
                            }
                            .buttonStyle(.borderless)
                            .disabled(model.busy)
                            Divider()
                        }
                    }
                }
                .padding(17)
            }
            .frame(width: 245)
            .background(panelColor, in: RoundedRectangle(cornerRadius: 15))

            VStack(alignment: .leading, spacing: 10) {
                if let handoff = model.handoff {
                    HStack {
                        Text("交接包预览").font(.headline)
                        Spacer()
                        Text("\(handoff.messageCount) 条对话")
                            .font(.caption2).foregroundStyle(.secondary)
                    }
                    Text(handoff.cwd).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                    TextEditor(text: $handoffText)
                        .font(.system(size: 11, design: .monospaced))
                        .scrollContentBackground(.hidden)
                        .padding(6)
                        .background(Color.primary.opacity(0.04), in: RoundedRectangle(cornerRadius: 8))
                    HStack {
                        Button("在访达中查看") {
                            NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: handoff.path)])
                        }
                        Spacer()
                        if handoff.target == "codex" {
                            Menu {
                                ForEach(model.accounts) { account in
                                    Button("用 \(account.displayName) 继续") {
                                        model.launchHandoff(content: handoffText, codexProfile: account.id)
                                    }
                                    .disabled(!account.loggedIn)
                                }
                            } label: {
                                Label("打开 Codex CLI 继续", systemImage: "terminal")
                            }
                            .disabled(model.busy || handoffText.isEmpty)
                        } else {
                            if let source = handoff.sourceProfile,
                               let target = model.accounts.first(where: { $0.id != source }) {
                                Button {
                                    model.launchDesktopHandoff(content: handoffText, targetProfile: target.id)
                                } label: {
                                    Label("用 \(target.name) 桌面窗口继续", systemImage: "macwindow.on.rectangle")
                                }
                                .buttonStyle(.borderedProminent)
                                .disabled(model.busy || !target.loggedIn || handoffText.isEmpty)
                            }
                            Button {
                                model.launchHandoff(content: handoffText)
                            } label: {
                                Label("打开 Claude Code 继续", systemImage: "terminal")
                            }
                            .buttonStyle(.bordered)
                            .disabled(model.busy || handoffText.isEmpty)
                        }
                    }
                } else {
                    ContentUnavailableView("选择任务生成交接包", systemImage: "arrow.left.arrow.right",
                                           description: Text("交接包保存在本机；目标 CLI 将从原工作目录启动。"))
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            }
            .padding(17)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(panelColor, in: RoundedRectangle(cornerRadius: 15))
        }
        .frame(maxHeight: .infinity)
    }

    private func taskDate(_ raw: String) -> String {
        let parser = ISO8601DateFormatter()
        parser.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = parser.date(from: raw) {
            return date.formatted(date: .abbreviated, time: .shortened)
        }
        return raw
    }
}
