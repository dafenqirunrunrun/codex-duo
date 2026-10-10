import AppKit
import SwiftUI
import Charts

private let copper = Color(red: 0.83, green: 0.48, blue: 0.35)

private func tokenText(_ value: Int) -> String {
    if value >= 1_000_000 { return String(format: "%.2fM", Double(value) / 1_000_000) }
    if value >= 10_000 { return String(format: "%.1fK", Double(value) / 1_000) }
    return value.formatted()
}

private struct TokenMetric: View {
    let title: String
    let usage: ClaudeTokenUsage?
    var warm = false
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(title).font(.caption).foregroundStyle(.secondary)
                Spacer()
                Text("TOKENS").font(.system(size: 8, weight: .bold)).tracking(1.5).foregroundStyle(copper)
            }
            HStack(alignment: .firstTextBaseline) {
                Text(usage.map { tokenText($0.total) } ?? "—")
                    .font(.system(size: 28, weight: .semibold, design: .rounded)).monospacedDigit()
                Spacer()
                Text(usage.map { "\($0.requests) 次请求" } ?? "等待读取").font(.caption).foregroundStyle(.secondary)
            }
            GeometryReader { geometry in
                let total = Double(max(usage?.total ?? 0, 1))
                HStack(spacing: 0) {
                    copper.frame(width: geometry.size.width * Double(usage?.input ?? 0) / total)
                    Color.indigo.frame(width: geometry.size.width * Double(usage?.output ?? 0) / total)
                    Color.teal.frame(width: geometry.size.width * Double(usage?.cache ?? 0) / total)
                }
                .background(Color.primary.opacity(0.06)).clipShape(Capsule())
            }.frame(height: 5).accessibilityHidden(true)
            HStack(spacing: 10) {
                Text("输入 \(tokenText(usage?.input ?? 0))").foregroundStyle(copper)
                Text("输出 \(tokenText(usage?.output ?? 0))").foregroundStyle(.indigo)
                Text("缓存 \(tokenText(usage?.cache ?? 0))").foregroundStyle(.teal)
            }.font(.system(size: 10)).lineLimit(1).minimumScaleFactor(0.8)
        }
        .padding(16).frame(maxWidth: .infinity, alignment: .leading)
        .background {
            if warm {
                RoundedRectangle(cornerRadius: 16).fill(LinearGradient(
                    colors: [Color(red: 0.99, green: 0.95, blue: 0.89), Color(red: 0.91, green: 0.77, blue: 0.66)],
                    startPoint: .topLeading, endPoint: .bottomTrailing))
            } else {
                RoundedRectangle(cornerRadius: 16).fill(Color(nsColor: .controlBackgroundColor))
            }
        }
        .overlay(RoundedRectangle(cornerRadius: 16).stroke(copper.opacity(0.22)))
        .environment(\.colorScheme, warm ? .light : .dark)
    }
}

struct ClaudeUsagePanel: View {
    @ObservedObject var model: DuoModel
    let configure: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(alignment: .top, spacing: 14) {
                Image(systemName: "terminal.fill").font(.system(size: 27))
                    .foregroundStyle(copper).frame(width: 54, height: 54)
                    .background(copper.opacity(0.13), in: RoundedRectangle(cornerRadius: 16))
                VStack(alignment: .leading, spacing: 7) {
                    Text("CLAUDE CODE / CLI").font(.system(size: 10, weight: .bold)).tracking(2).foregroundStyle(copper)
                    Text(model.gatewayModel).font(.title3.weight(.semibold)).lineLimit(1)
                    Text(URL(string: model.gatewayURL)?.host ?? model.gatewayURL)
                        .font(.caption).foregroundStyle(.secondary)
                }
                Spacer(minLength: 8)
                VStack(alignment: .trailing, spacing: 10) {
                    Button {
                        if model.gatewayKeyConfigured { model.openClaudeCLI() } else { configure() }
                    } label: {
                        Label(model.gatewayKeyConfigured ? "打开终端" : "配置服务商", systemImage: "arrow.up.right")
                            .font(.callout.weight(.semibold)).foregroundStyle(.white)
                            .padding(.horizontal, 14).padding(.vertical, 9)
                            .background(copper, in: RoundedRectangle(cornerRadius: 9))
                    }.buttonStyle(.plain).disabled(model.busy)
                    Button("服务商设置", action: configure).buttonStyle(.borderless).font(.caption)
                }
            }
            .padding(18)
            .background(LinearGradient(colors: [copper.opacity(0.13), Color(nsColor: .controlBackgroundColor)],
                                       startPoint: .topLeading, endPoint: .bottomTrailing), in: RoundedRectangle(cornerRadius: 19))
            .overlay(RoundedRectangle(cornerRadius: 19).stroke(copper.opacity(0.32)))

            HStack(spacing: 14) {
                TokenMetric(title: "今日消耗", usage: model.claudeUsage?.today, warm: true)
                TokenMetric(title: "近 7 天消耗", usage: model.claudeUsage?.week, warm: true)
            }

            VStack(alignment: .leading, spacing: 4) {
                let directory = model.sharedProjectPath.isEmpty ? NSHomeDirectory() : model.sharedProjectPath
                Label("启动目录：\(URL(fileURLWithPath: directory).lastPathComponent)", systemImage: "folder")
                    .help(directory)
                Text("统计本机全部 CC 会话（含子任务及其他服务商）；色条表示 Token 构成。")
                if let usage = model.claudeUsage {
                    Text("\(usage.filesRead) 份会话记录 · 更新于 \(Date(timeIntervalSince1970: TimeInterval(usage.updatedAt)).formatted(date: .omitted, time: .shortened))")
                    if usage.unreadableFiles > 0 { Text("\(usage.unreadableFiles) 份记录读取失败，统计可能不完整").foregroundStyle(.orange) }
                }
                if let error = model.claudeUsageError { Text("更新失败：\(error)；当前保留上次数据").foregroundStyle(.orange) }
            }.font(.caption2).foregroundStyle(.secondary)
        }
    }
}

struct ClaudeTokenHistory: View {
    @ObservedObject var model: DuoModel

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 14) {
                TokenMetric(title: "今日消耗", usage: model.claudeUsage?.today, warm: true)
                TokenMetric(title: "近 7 天消耗", usage: model.claudeUsage?.week, warm: true)
            }
            VStack(alignment: .leading, spacing: 14) {
                HStack {
                    Text("每日 Token 消耗").font(.headline)
                    Spacer()
                    Text("近 7 天 · 本地记录").font(.caption).foregroundStyle(.secondary)
                }
                if let usage = model.claudeUsage {
                    Chart {
                        ForEach(usage.days, id: \.date) { day in
                            AreaMark(x: .value("日期", String((day.date ?? "").suffix(5))), y: .value("Tokens", day.total))
                                .foregroundStyle(LinearGradient(colors: [copper.opacity(0.3), copper.opacity(0.02)], startPoint: .top, endPoint: .bottom))
                            LineMark(x: .value("日期", String((day.date ?? "").suffix(5))), y: .value("Tokens", day.total))
                                .foregroundStyle(copper).lineStyle(StrokeStyle(lineWidth: 2.5))
                            PointMark(x: .value("日期", String((day.date ?? "").suffix(5))), y: .value("Tokens", day.total))
                                .foregroundStyle(copper).symbolSize(28)
                                .accessibilityLabel("\(day.date ?? "")")
                                .accessibilityValue("\(day.total) Tokens，\(day.requests) 次请求")
                        }
                    }
                    .chartYScale(domain: 0...max(usage.days.map(\.total).max() ?? 0, 1))
                    .frame(height: 180)
                    .accessibilityElement(children: .ignore)
                    .accessibilityLabel("近七天 Claude Code Token 消耗曲线")
                    .accessibilityValue(usage.days.map { "\($0.date ?? "")：\($0.total) Tokens" }.joined(separator: "；"))
                    if usage.week.total == 0 {
                        Text("暂无本地 Token 消耗记录").font(.caption).foregroundStyle(.secondary)
                    }
                } else {
                    ProgressView("正在读取本机用量…").frame(maxWidth: .infinity, minHeight: 180)
                }
                Text("输入 + 输出 + 缓存 Token；统计本机全部 CC 会话，非服务商余额。")
                    .font(.caption2).foregroundStyle(.secondary)
                if let error = model.claudeUsageError { Text("更新失败：\(error)").font(.caption).foregroundStyle(.orange) }
            }
            .padding(18)
            .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 17))
            .overlay(RoundedRectangle(cornerRadius: 17).stroke(copper.opacity(0.2)))
        }
    }
}
