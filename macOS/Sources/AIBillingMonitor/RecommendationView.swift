import SwiftUI

struct RecommendationPanel: View {
    @EnvironmentObject var model: AppModel
    @State private var power = "standard"
    @State private var speed = "conserve"
    @State private var allowSharedAPI = false
    var recommendation: Record { model.overview.object("recommendation") }
    var choice: Record {
        guard model.connected else { return [:] }
        let value = recommendation.object("choice")
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        func parse(_ stamp: String) -> Date? { formatter.date(from: stamp) ?? ISO8601DateFormatter().date(from: stamp) }
        guard let updated = parse(value.text("last_updated")) else { return [:] }
        let reset = parse(value.text("reset_at"))
        let age = Date().timeIntervalSince(updated)
        guard age >= 0, age <= (value.text("source") == "manual" ? 48 * 3600 : 10 * 60), (reset == nil || reset! > Date()) else { return [:] }
        return value
    }
    var changed: Bool {
        let saved = recommendation.object("preferences")
        return power != saved.text("power") || speed != saved.text("speed") || allowSharedAPI != saved.flag("allow_shared_api")
    }
    var body: some View {
        Panel(title: "Recommended for your next task") {
            HStack(alignment: .top, spacing: 24) {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Power required").font(.caption).foregroundStyle(.secondary)
                    Picker("Power required", selection: $power) {
                        Text("Routine edits").tag("routine")
                        Text("Feature coding").tag("standard")
                        Text("Complex work").tag("complex")
                    }.pickerStyle(.segmented).labelsHidden()
                }
                VStack(alignment: .leading, spacing: 6) {
                    Text("Speed preference").font(.caption).foregroundStyle(.secondary)
                    Picker("Speed preference", selection: $speed) {
                        Text("Save allowance").tag("conserve")
                        Text("Faster replies").tag("fast")
                    }.pickerStyle(.segmented).labelsHidden()
                }
            }
            HStack {
                Toggle("Shared API traffic is suitable for this task", isOn: $allowSharedAPI)
                    .font(.callout)
                Spacer()
                Button("Update recommendation") {
                    model.perform {
                        _ = try await model.request("/v1/overview/recommendation", method: "PUT", body: ["power": power, "speed": speed, "allow_shared_api": allowSharedAPI])
                    }
                }.buttonStyle(.borderedProminent).disabled(model.busy || !changed)
            }
            if changed {
                Text("Apply your choices to update the recommendation below.").font(.caption).foregroundStyle(.orange)
            }
            Divider()
            if !choice.isEmpty {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 8) {
                        Label(choice.text("title"), systemImage: "sparkles").font(.title2.bold())
                        Text(choice.text("model_profile")).font(.callout.weight(.medium))
                        Text(choice.text("speed_profile")).font(.callout)
                    }
                    Spacer(minLength: 12)
                    VStack(alignment: .trailing, spacing: 6) {
                        Text(choice.text("remaining")).font(.title3.bold()).monospacedDigit()
                        Text(choice.text("source").capitalized + " source").font(.caption)
                        Text("Updated \(dateText(choice.text("last_updated")))").font(.caption)
                        Text("Reset \(choice.text("reset_at").isEmpty ? "Unknown" : dateText(choice.text("reset_at")))").font(.caption)
                    }.foregroundStyle(.secondary)
                }
            } else {
                Label(recommendation.text("message").isEmpty ? "Refresh usage before choosing an account; the recommendation needs current data." : recommendation.text("message"), systemImage: "arrow.clockwise.circle")
                    .font(.headline).foregroundStyle(.orange)
            }
            DisclosureGroup("Details") {
                VStack(alignment: .leading, spacing: 10) {
                    if !choice.isEmpty {
                        if let reasons = choice["reasons"] as? [String] {
                            ForEach(Array(reasons.enumerated()), id: \.offset) { _, reason in
                                Label(reason, systemImage: "checkmark.circle")
                            }
                        }
                        Text(choice.text("connection")).textSelection(.enabled)
                        if let cautions = choice["cautions"] as? [String] {
                            ForEach(Array(cautions.enumerated()), id: \.offset) { _, caution in
                                Label(caution, systemImage: "exclamationmark.triangle").foregroundStyle(.orange)
                            }
                        }
                        Divider()
                    }
                    ForEach(recommendation.records("alternatives"), id: \.routeID) { option in
                        Text("\(option.text("title")) · \(option.text("remaining")) remaining · \(option.text("source"))")
                    }
                    ForEach(Array(recommendation.records("excluded").enumerated()), id: \.offset) { _, excluded in
                        Text(excluded.text("reason"))
                    }
                    Text(recommendation.text("paid_api_note"))
                    Text(recommendation.text("basis"))
                    Link("Codex allowance and speed guidance", destination: URL(string: "https://learn.chatgpt.com/docs/pricing")!)
                }.font(.caption).foregroundStyle(.secondary).padding(.top, 8)
            }.font(.caption)
        }
        .onAppear {
            let saved = recommendation.object("preferences")
            power = saved.text("power").isEmpty ? "standard" : saved.text("power")
            speed = saved.text("speed").isEmpty ? "conserve" : saved.text("speed")
            allowSharedAPI = saved.flag("allow_shared_api")
        }
    }
}

private extension Dictionary where Key == String, Value == Any {
    var routeID: String { text("route_id") }
}
