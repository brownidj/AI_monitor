import SwiftUI

struct AllowanceOverview: View {
    @EnvironmentObject var model: AppModel
    @State private var showPools = false
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Text("Remaining capacity").font(.title2.bold())
                Spacer()
                Button("Configure free API pools…") { showPools = true }
            }
            Text("Live means provider-reported data, refreshed periodically. Estimated and manual balances are labelled separately. Check each card’s update time.")
                .font(.caption).foregroundStyle(.secondary)
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 260), spacing: 16, alignment: .top)], spacing: 16) {
                ForEach(model.overview.records("cards"), id: \.idValue) { card in
                    AllowanceCard(card: card)
                }
            }
            Text("Token, Codex and configured JetBrains checks run every 5 minutes while monitoring is enabled. Provider reporting can lag. API spending uses your configured cost-check interval.")
                .font(.caption).foregroundStyle(.secondary)
        }.sheet(isPresented: $showPools) { FreePoolsForm() }
    }
}

struct AllowanceCard: View {
    var card: Record
    private var detailLines: [String] { card["details"] as? [String] ?? [] }
    var sourceColor: Color {
        switch card.text("source") { case "live": .green; case "manual": .purple; default: .orange }
    }
    var status: String {
        switch card.text("data_status") {
        case "current": "Up to date"
        case "stale": "Stale — check update time"
        case "waiting_for_reset": "Waiting for reset · recorded balance"
        case "reset_due": "Reset due — confirm balance"
        case "not_configured": "Setup needed"
        case "future_observation": "Check observation date"
        default: "Data unavailable"
        }
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top) {
                Text(card.text("title")).font(.headline)
                Spacer(minLength: 4)
                Text(card.text("source").capitalized).font(.caption.bold())
                    .padding(.horizontal, 8).padding(.vertical, 4)
                    .background(sourceColor.opacity(0.12), in: Capsule()).foregroundStyle(sourceColor)
            }
            Text(card.text("remaining")).font(.system(size: 25, weight: .semibold, design: .rounded))
                .monospacedDigit().textSelection(.enabled)
            Text(card.text("id") == "api_spending" ? "Monthly budget remaining" : "Remaining capacity")
                .font(.caption).foregroundStyle(.secondary)
            if let percent = card.number("used_percent") {
                CapacityUsageBar(usedPercent: NSDecimalNumber(decimal: percent).doubleValue)
            }
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(status).fontWeight(.medium)
                    .foregroundStyle(card.text("data_status") == "current" ? Color.secondary : Color.orange)
                Spacer(minLength: 0)
                Text("Reset: \(card.text("reset_at").isEmpty ? "Unknown" : dateText(card.text("reset_at")))")
                    .multilineTextAlignment(.trailing)
            }.font(.caption).foregroundStyle(.secondary)
            DisclosureGroup("Details") {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Updated: \(card.text("last_updated").isEmpty ? "Never" : dateText(card.text("last_updated")))")
                        .textSelection(.enabled)
                    ForEach(Array(detailLines.enumerated()), id: \.offset) { _, detail in
                        Text(detail).textSelection(.enabled)
                    }
                    ForEach(Array(card.records("windows").enumerated()), id: \.offset) { _, window in
                        Text("\(window.text("label")) resets \(dateText(window.text("resets_at")))")
                    }
                    if !card.text("error").isEmpty {
                        Text(card.text("error")).foregroundStyle(.orange)
                            .textSelection(.enabled)
                    }
                }.font(.caption).foregroundStyle(.secondary).padding(.top, 6)
            }.font(.caption)
        }.padding(18).frame(maxWidth: .infinity, alignment: .topLeading)
            .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 16))
            .overlay(RoundedRectangle(cornerRadius: 16).stroke(.quaternary))
    }
}

struct CapacityUsageBar: View {
    var usedPercent: Double
    private var used: Double { min(100, max(0, usedPercent)) }
    var body: some View {
        VStack(spacing: 5) {
            GeometryReader { geometry in
                ZStack(alignment: .leading) {
                    Rectangle().fill(Color.green)
                    Rectangle().fill(Color.red)
                        .frame(width: geometry.size.width * used / 100)
                }.clipShape(Capsule())
            }.frame(height: 6)
            HStack {
                Text("Used \(used, specifier: "%.0f")%").foregroundStyle(.red)
                Spacer()
                Text("Remaining \(100 - used, specifier: "%.0f")%").foregroundStyle(.green)
            }.font(.caption2).monospacedDigit()
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Capacity")
        .accessibilityValue("\(used, specifier: "%.0f") percent used, \(100 - used, specifier: "%.0f") percent remaining")
    }
}

struct FreePoolsForm: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) var dismiss
    @State private var enrollment = "unconfirmed"
    @State private var allProjects = false
    @State private var projects = ""
    @State private var largeLimit = "1000000"
    @State private var smallLimit = "10000000"
    @State private var largeModels = ""
    @State private var smallModels = ""
    @State private var failure: String?
    @State private var saving = false
    func names(_ value: String) -> [String] {
        value.split(separator: ",").map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty }
    }
    var valid: Bool {
        (Int(largeLimit) ?? 0) > 0 && (Int(smallLimit) ?? 0) > 0 && !names(largeModels).isEmpty && !names(smallModels).isEmpty && (enrollment != "enrolled" || allProjects || !names(projects).isEmpty)
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Daily free API pools").font(.title2.bold())
            Text("Record the offer and sharing scope shown in your OpenAI account. These settings only configure this monitor; they do not enable data sharing or change billing.").foregroundStyle(.secondary)
            Form {
                Picker("Enrollment", selection: $enrollment) {
                    Text("Not confirmed").tag("unconfirmed")
                    Text("I confirmed enrollment").tag("enrolled")
                    Text("Not enrolled").tag("disabled")
                }
                Toggle("Sharing is enabled for all API projects", isOn: $allProjects)
                if !allProjects { TextField("Shared project IDs, comma-separated", text: $projects) }
                TextField("Larger-model daily token allowance", text: $largeLimit)
                TextField("Mini/nano daily token allowance", text: $smallLimit)
                TextField("Eligible larger models, comma-separated", text: $largeModels, axis: .vertical).lineLimit(2...4)
                TextField("Eligible mini/nano models, comma-separated", text: $smallModels, axis: .vertical).lineLimit(2...4)
            }
            Link("Review OpenAI data sharing settings…", destination: URL(string: "https://platform.openai.com/settings/organization/data-controls/sharing")!)
            Text("Sharing can allow inputs and outputs to be used for model improvement. Only include projects you have chosen to share. Estimates can lag and are not a guaranteed free balance. Reset is midnight UTC; tools may still incur charges.")
                .font(.caption).foregroundStyle(.secondary)
            if let failure { Text(failure).foregroundStyle(.red) }
            HStack {
                Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction)
                Spacer()
                Button(saving ? "Saving…" : "Save") {
                    saving = true
                    Task {
                        defer { saving = false }
                        do {
                            let value: Record = ["enrollment": enrollment, "all_projects": allProjects, "project_ids": names(projects), "large_limit": Int(largeLimit)!, "small_limit": Int(smallLimit)!, "large_models": names(largeModels), "small_models": names(smallModels)]
                            _ = try await model.request("/v1/overview/free-pools", method: "PUT", body: value)
                            try await model.reload()
                            dismiss()
                        } catch { failure = error.localizedDescription }
                    }
                }.buttonStyle(.borderedProminent).disabled(!valid || saving)
            }
        }.padding(26).frame(width: 650)
            .onAppear {
                let value = model.freePoolSettings
                enrollment = value.text("enrollment"); allProjects = value.flag("all_projects")
                projects = (value["project_ids"] as? [String] ?? []).joined(separator: ", ")
                largeLimit = value.number("large_limit").map { NSDecimalNumber(decimal: $0).stringValue } ?? largeLimit
                smallLimit = value.number("small_limit").map { NSDecimalNumber(decimal: $0).stringValue } ?? smallLimit
                largeModels = (value["large_models"] as? [String] ?? []).joined(separator: ", ")
                smallModels = (value["small_models"] as? [String] ?? []).joined(separator: ", ")
            }
    }
}
