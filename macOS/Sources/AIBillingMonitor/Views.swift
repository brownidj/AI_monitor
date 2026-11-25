import Charts
import ServiceManagement
import SwiftUI

enum Section: String, CaseIterable, Identifiable {
    case overview = "Overview", subscriptions = "Subscriptions", jetbrains = "JetBrains credits", alerts = "Alerts", settings = "Settings"
    var id: String { rawValue }
    var icon: String {
        switch self {
        case .overview: "chart.bar.xaxis"
        case .subscriptions: "calendar"
        case .jetbrains: "sparkles"
        case .alerts: "bell"
        case .settings: "gearshape"
        }
    }
}

struct MainView: View {
    @EnvironmentObject var model: AppModel
    @State private var selection: Section? = .overview
    var body: some View {
        NavigationSplitView {
            List(Section.allCases, selection: $selection) { section in
                Label(section.rawValue, systemImage: section.icon).tag(section)
            }
            .navigationTitle("AI Billing Monitor")
            .safeAreaInset(edge: .bottom) {
                VStack(alignment: .leading, spacing: 6) {
                    Label(model.connected ? (model.status.flag("automatic_checks_enabled") ? "Monitoring on this Mac" : "Monitoring paused") : "Connecting…", systemImage: model.connected ? "circle.fill" : "circle.dotted")
                        .foregroundStyle(model.fresh ? .green : .secondary)
                    Text("Closing this window keeps monitoring running.").foregroundStyle(.secondary)
                }.font(.caption).padding()
            }.navigationSplitViewColumnWidth(min: 195, ideal: 215)
        } detail: {
            VStack(spacing: 0) {
                if let error = model.error {
                    HStack { Image(systemName: "exclamationmark.triangle.fill"); Text(error); Spacer(); Button("Dismiss") { model.error = nil } }
                        .padding(12).background(Color.orange.opacity(0.15))
                }
                if model.starting || (!model.connected && model.status.isEmpty) {
                    VStack(spacing: 20) {
                        if model.starting { ProgressView(); Text("Starting your monitor…") }
                        else { ContentUnavailableView("Monitor is offline", systemImage: "network.slash", description: Text("Your saved data stays on this Mac.")); Button("Try again") { Task { await model.start() } } }
                    }.frame(maxWidth: .infinity, maxHeight: .infinity)
                } else {
                    switch selection ?? .overview {
                    case .overview: OverviewView()
                    case .subscriptions: SubscriptionsView()
                    case .jetbrains: JetBrainsView()
                    case .alerts: AlertsView()
                    case .settings: PreferencesView()
                    }
                }
            }
            .navigationTitle((selection ?? .overview).rawValue)
            .toolbar {
                ToolbarItem {
                    Button { model.checkNow() } label: { Label(model.busy ? "Checking…" : "Check now", systemImage: "arrow.clockwise") }
                        .disabled(model.busy || !model.connected).help("Refresh API costs, token pools, Codex allowance and configured JetBrains balances")
                }
            }
        }
        .frame(minWidth: 960, minHeight: 680)
        .overlay(alignment: .bottom) {
            if let notice = model.notice {
                HStack { Image(systemName: "checkmark.circle.fill").foregroundStyle(.green); Text(notice); Button { model.notice = nil } label: { Image(systemName: "xmark") }.buttonStyle(.plain) }
                    .padding(12).background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12)).padding()
            }
        }
    }
}

struct Panel<Content: View>: View {
    var title: String
    @ViewBuilder var content: Content
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(title).font(.headline)
            content
        }.padding(22).frame(maxWidth: .infinity, alignment: .leading)
            .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 16))
            .overlay(RoundedRectangle(cornerRadius: 16).stroke(.quaternary))
    }
}
struct Metric: View {
    var title: String
    var value: String
    var detail: String
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(.subheadline).foregroundStyle(.secondary)
            Text(value).font(.system(size: 32, weight: .semibold, design: .rounded)).monospacedDigit()
            Text(detail).font(.caption).foregroundStyle(.secondary)
        }.frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct CreditPurchasePanel: View {
    var body: some View {
        Panel(title: "Manual credit purchases") {
            HStack(alignment: .center, spacing: 24) {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Choose when to add OpenAI API credits.").font(.subheadline.weight(.medium))
                    Text("Choose the amount and authorise payment on OpenAI’s website. Your live credit balance and recharge settings are managed there.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Spacer(minLength: 12)
                Link(destination: openAIBillingURL) {
                    Label("Buy OpenAI credits…", systemImage: "arrow.up.right")
                }
                .buttonStyle(.borderedProminent)
                .help("Open OpenAI billing in your browser to review and confirm a manual purchase")
            }
        }
    }
}

struct OverviewView: View {
    @EnvironmentObject var model: AppModel
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                HStack {
                    VStack(alignment: .leading, spacing: 5) {
                        Text("Your AI usage, in view.").font(.largeTitle.bold())
                        Text("Accounts, allowances and spending · Each pool stays separate").foregroundStyle(.secondary)
                    }
                    Spacer()
                    Label(model.fresh ? "API costs up to date" : "API costs need attention", systemImage: model.fresh ? "checkmark.circle.fill" : "exclamationmark.circle")
                        .font(.callout).foregroundStyle(model.fresh ? .green : .orange)
                }
                RecommendationPanel()
                if !model.hasKey {
                    Label("Add your OpenAI Admin key in Settings to collect API spending.", systemImage: "key.fill").padding().frame(maxWidth: .infinity, alignment: .leading).background(.orange.opacity(0.12), in: RoundedRectangle(cornerRadius: 12))
                }
                AllowanceOverview()
                Panel(title: "Monthly API costs") {
                    HStack(alignment: .top, spacing: 30) {
                        Metric(title: "Estimated spend · AUD", value: money(model.history.number("converted_total_estimate")), detail: "Usage costs, separate from card payments")
                        Metric(title: "Monthly reference · AUD", value: money(model.audBudget), detail: "Source: \(money(model.preferences.number("monthly_budget"), currency: model.preferences.text("budget_currency")))")
                        Metric(title: "Budget used", value: model.usedPercent.map { String(format: "%.1f%%", $0) } ?? "—", detail: "Warnings at 50%, 80% and 95%")
                    }
                    if let percent = model.usedPercent { CapacityUsageBar(usedPercent: percent) }
                    if let warning = model.status.object("state")["budget_warning"] as? String { Text(warning).font(.callout).foregroundStyle(.orange) }
                    if let warning = model.history["conversion_warning"] as? String { Text(warning).font(.callout).foregroundStyle(.orange) }
                    Text("AU$ estimates use the RBA rate dated \(model.history.object("exchange_rate").text("rate_date")). Card rates and fees may differ.").font(.caption).foregroundStyle(.secondary)
                }
                CreditPurchasePanel()
                Panel(title: "Daily API spending · estimated AUD") {
                    if let warning = model.history["conversion_warning"] as? String {
                        ContentUnavailableView("AUD conversion unavailable", systemImage: "dollarsign.arrow.circlepath", description: Text(warning))
                    } else if model.history.records("daily").isEmpty {
                        ContentUnavailableView("No collected costs yet", systemImage: "chart.bar", description: Text("Check now to collect your recent history."))
                    } else {
                        Chart(Array(model.history.records("daily").enumerated()), id: \.offset) { _, day in
                            let amount = dailyAUD(day)
                            BarMark(x: .value("Day", String(day.text("day").suffix(2))), y: .value("AUD", amount))
                                .foregroundStyle(Color.green.gradient)
                        }.frame(height: 180).chartYAxis { AxisMarks(position: .leading) }
                        Text("Today's UTC bucket is partial. Provider reporting may be delayed.").font(.caption).foregroundStyle(.secondary)
                    }
                }
                HStack(alignment: .top, spacing: 20) {
                    Panel(title: "Next subscription") {
                        if let next = model.subscriptions.filter({ $0.flag("active") }).sorted(by: { $0.text("next_renewal_date") < $1.text("next_renewal_date") }).first {
                            Text(next.text("plan")).font(.title3.bold())
                            Text("\(next.text("next_renewal_date")) · \(money(next.number("amount"), currency: next.text("currency")))")
                            Text("Manually recorded renewal; payment unconfirmed.").font(.caption).foregroundStyle(.secondary)
                        } else { Text("Add your ChatGPT or JetBrains subscription.").foregroundStyle(.secondary) }
                    }
                    Panel(title: "Monitoring") {
                        Text("Last success: \(dateText(model.status.object("state").text("last_success_at")))")
                        Text("Next check: \(dateText(model.status.object("state").text("next_run_at")))")
                        Text("Keep this app running and your Mac awake for scheduled checks.").font(.caption).foregroundStyle(.secondary)
                    }
                }
            }.padding(28)
        }.background(Color(nsColor: .windowBackgroundColor))
    }
    func dailyAUD(_ day: Record) -> Double {
        let totals = day.object("totals_by_currency")
        var amount = totals.number("AUD") ?? 0
        if let usd = totals.number("USD"), let rate = model.usdPerAUD, rate > 0 { amount += usd / rate }
        return NSDecimalNumber(decimal: amount).doubleValue
    }
}

struct SubscriptionsView: View {
    @EnvironmentObject var model: AppModel
    @State private var editing: Record?
    @State private var showingForm = false
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                HStack { Text("Renewals you want to keep track of.").font(.title2.bold()); Spacer(); Button("Add subscription", systemImage: "plus") { editing = nil; showingForm = true }.buttonStyle(.borderedProminent) }
                Text("Record renewal details from OpenAI, Apple, Google or JetBrains. JetBrains renewals generate reminders; ChatGPT renewal reminders remain previews.").foregroundStyle(.secondary)
                if model.subscriptions.isEmpty { ContentUnavailableView("No subscriptions yet", systemImage: "calendar.badge.plus") }
                ForEach(model.subscriptions, id: \.idValue) { subscription in
                    Panel(title: subscription.text("plan")) {
                        HStack {
                            VStack(alignment: .leading, spacing: 6) {
                                Text("\(subscription.text("provider").capitalized) · \(subscription.text("billing_channel").capitalized)").foregroundStyle(.secondary)
                                Text("Next renewal: \(subscription.text("next_renewal_date"))")
                            }
                            Spacer()
                            Text(money(subscription.number("amount"), currency: subscription.text("currency"))).font(.title2.bold())
                            Button("Edit") { editing = subscription; showingForm = true }
                        }
                    }
                }
            }.padding(28)
        }.sheet(isPresented: $showingForm) { SubscriptionForm(existing: editing) }
    }
}
extension Dictionary where Key == String, Value == Any { var idValue: String { text("id") } }

struct SubscriptionForm: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) var dismiss
    var existing: Record?
    var defaultProvider = "chatgpt"
    @State var provider = "chatgpt"
    @State var plan = ""
    @State var amount = ""
    @State var currency = "AUD"
    @State var channel = "direct"
    @State var cadence = "monthly"
    @State var renewal = Date()
    @State var active = true
    @State var failure: String?
    @State var saving = false
    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text(existing == nil ? "Add subscription" : "Edit subscription").font(.title2.bold())
            Form {
                Picker("Provider", selection: $provider) { Text("ChatGPT").tag("chatgpt"); Text("JetBrains").tag("jetbrains") }
                TextField("Plan name", text: $plan)
                TextField("Price", text: $amount)
                Picker("Currency", selection: $currency) { ForEach(["AUD", "USD", "EUR", "GBP"], id: \.self) { Text($0) } }
                Picker("Billed through", selection: $channel) { ForEach(["direct", "apple", "google", "other"], id: \.self) { Text($0.capitalized) } }
                Picker("Billing cycle", selection: $cadence) { ForEach(["monthly", "annual", "other"], id: \.self) { Text($0.capitalized) } }
                DatePicker("Next renewal", selection: $renewal, displayedComponents: .date)
                Toggle("Active", isOn: $active)
            }.formStyle(.grouped)
            if let failure { Text(failure).foregroundStyle(.orange) }
            HStack {
                if let existing {
                    Button("Delete", role: .destructive) {
                        saving = true
                        Task { do { _ = try await model.request("/v1/subscriptions/\(existing.idValue)", method: "DELETE"); try await model.reload(); dismiss() } catch { failure = error.localizedDescription; saving = false } }
                    }
                }
                Spacer(); Button("Cancel") { dismiss() }
                Button("Save") { save() }.buttonStyle(.borderedProminent).disabled(saving || plan.trimmingCharacters(in: .whitespaces).isEmpty || Decimal(string: amount) == nil)
            }
        }.padding(24).frame(width: 470).onAppear {
            if let existing {
                provider = existing.text("provider"); plan = existing.text("plan"); amount = existing.text("amount"); currency = existing.text("currency")
                channel = existing.text("billing_channel"); cadence = existing.text("cadence"); active = existing.flag("active")
                let formatter = DateFormatter(); formatter.dateFormat = "yyyy-MM-dd"; renewal = formatter.date(from: existing.text("next_renewal_date")) ?? Date()
            } else { provider = defaultProvider }
        }
    }
    func save() {
        saving = true
        let formatter = DateFormatter(); formatter.dateFormat = "yyyy-MM-dd"
        let value: Record = ["provider": provider, "plan": plan, "amount": amount, "currency": currency, "billing_channel": channel, "cadence": cadence, "next_renewal_date": formatter.string(from: renewal), "active": active]
        Task {
            do { _ = try await model.request(existing.map { "/v1/subscriptions/\($0.idValue)" } ?? "/v1/subscriptions", method: existing == nil ? "POST" : "PUT", body: value); try await model.reload(); dismiss() }
            catch { failure = error.localizedDescription; saving = false }
        }
    }
}

struct JetBrainsView: View {
    @EnvironmentObject var model: AppModel
    @State private var licenceID = "V77PXJQ7DS"
    @State private var knowsReset = false
    @State private var allocation = ""
    @State private var remaining = ""
    @State private var topup = "0"
    @State private var reset = Date()
    @State private var observed = Date()
    @State private var hasExpiry = false
    @State private var expiry = Date()
    @State private var showRenewal = false
    @State private var noTopups = false
    @State private var hasResetTime = false
    @State private var deleting: String?
    var valid: Bool {
        guard let total = Decimal(string: allocation), let left = Decimal(string: remaining),
              let extra = Decimal(string: topup) else { return false }
        return total > 0 && left >= 0 && left <= total && extra >= 0
    }
    var statusLabel: String {
        switch model.jetbrainsSummary.text("data_status") {
        case "current": return "Recent manual observation"
        case "stale": return "Balance needs updating"
        case "reset_due": return "Quota reset needs confirmation"
        case "waiting_for_reset": return "Waiting for included credits to reset"
        case "future_observation": return "Check the observation date"
        default: return "Not configured"
        }
    }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                HStack {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("Your JetBrains AI credits.").font(.largeTitle.bold())
                        Text("Personal AI credits · Automatic checks and manual observations").foregroundStyle(.secondary)
                    }
                    Spacer()
                    Link("Open JetBrains Account", destination: jetBrainsAccountURL).buttonStyle(.bordered)
                }
                Text("Configured licences refresh through JetBrains Central CLI every five minutes while monitoring is enabled. Checks pause while Central’s proxy runs. PyCharm’s selected licence is independent; select the matching licence there before using its credits.").foregroundStyle(.secondary)
                Panel(title: "How you use your allowance") {
                    Toggle("No top-ups — wait for included credits to reset", isOn: $noTopups)
                    Text("Use JetBrains AI Assistant while included credits remain; use Air with Codex/ChatGPT when exhausted. The monitor reminds you to check the allowance at reset and about unused credits in the last three days.").foregroundStyle(.secondary)
                    Button("Save JetBrains preference") {
                        model.perform {
                            _ = try await model.request("/v1/jetbrains/settings", method: "PUT", body: ["no_topups": noTopups, "preferred_licence_id": model.jetbrainsSummary.text("preferred_licence_id")])
                            model.notice = "JetBrains preference saved."
                        }
                    }.disabled(model.busy)
                }
                ForEach(model.jetbrainsSummary["licences"] as? [Record] ?? [], id: \.licenceKey) { licence in
                    Panel(title: licence.text("licence_id")) {
                        let latest = licence["latest"] as? Record ?? [:]
                        Text(licence.flag("preferred") ? "Monitor preferred licence" : "Monitored independently")
                        Text("Included: \(latest.text("monthly_remaining")) / \(latest.text("monthly_allocation")) · \(licence.text("data_status"))")
                        Text("Freshness: \(licence.text("freshness")) · \(latest.text("source") == "central_cli" ? "Central CLI" : "Manual observation")")
                        if licence.flag("automatic_monitoring") {
                            Text("Automatic checks configured · every five minutes").font(.caption)
                        }
                        if !licence.text("collection_error").isEmpty {
                            Text(licence.text("collection_error")).foregroundStyle(.orange)
                        }
                        Text("Monthly reset: \(licence.text("next_reset_at").isEmpty ? "Unknown" : dateText(licence.text("next_reset_at")))")
                        Text("Last observation: \(dateText(latest.text("observed_at")))")
                        Text("PyCharm selected licence: unknown. This monitor cannot detect or change it.").font(.caption)
                        Button("Prefer this licence in monitor") {
                            model.perform {
                                _ = try await model.request("/v1/jetbrains/settings", method: "PUT", body: ["no_topups": noTopups, "preferred_licence_id": licence.text("licence_id")])
                            }
                        }
                    }
                }
                Panel(title: "Record a credit balance") {
                    Form {
                        Picker("Licence to record", selection: $licenceID) {
                            ForEach(model.jetbrainsSummary["licences"] as? [Record] ?? [], id: \.licenceKey) { licence in
                                Text(licence.text("licence_id")).tag(licence.text("licence_id"))
                            }
                        }
                        TextField("Included quota allocation", text: $allocation)

                        TextField("Included credits remaining", text: $remaining)
                        TextField("Purchased credits remaining", text: $topup)
                        Toggle("I know the monthly reset date", isOn: $knowsReset)
                        Toggle("I know the reset time", isOn: $hasResetTime)
                        if knowsReset { DatePicker("Next quota reset", selection: $reset, displayedComponents: hasResetTime ? [.date, .hourAndMinute] : [.date]) }
                        DatePicker("Observed at", selection: $observed)
                        Toggle("I have a top-up expiry date", isOn: $hasExpiry)
                        if hasExpiry { DatePicker("Top-up expiry", selection: $expiry, displayedComponents: .date) }
                    }.frame(maxWidth: 580)
                    Text("Choose the licence observed. Recording a balance does not change the monitor preference. For purchases with different expiry dates, record the earliest relevant expiry and check JetBrains Account for the amounts affected.").font(.caption).foregroundStyle(.secondary)
                    Button("Save credit observation") {
                        let formatter = DateFormatter(); formatter.dateFormat = "yyyy-MM-dd"
                        let stamp = ISO8601DateFormatter().string(from: observed)
                        var value: Record = ["monthly_allocation": allocation, "monthly_remaining": remaining, "top_up_remaining": topup, "licence_id": licenceID, "observed_at": stamp]
                        if knowsReset { value["next_reset_date"] = formatter.string(from: reset) }
                        if knowsReset && hasResetTime { value["next_reset_at"] = ISO8601DateFormatter().string(from: reset) }
                        if hasExpiry { value["top_up_expires_on"] = formatter.string(from: expiry) }
                        model.perform {
                            _ = try await model.request("/v1/jetbrains/snapshots", method: "POST", body: value)
                            model.notice = "JetBrains observation saved."
                        }
                    }.buttonStyle(.borderedProminent).disabled(model.busy || !valid)
                }
                Panel(title: "Subscription renewal") {
                    Text("JetBrains subscription reminders run 7, 3 and 1 day before the recorded renewal, with a due-today reminder. Quota reset dates are separate from renewal dates.").foregroundStyle(.secondary)
                    HStack {
                        Button("Add subscription renewal") { showRenewal = true }
                        Link("Manage JetBrains account…", destination: jetBrainsAccountURL)
                    }
                    Text("The annual subscription renewal is separate from the included-credit reset. The app does not submit a payment.").font(.caption).foregroundStyle(.secondary)
                }
                if !model.snapshots.isEmpty {
                    Panel(title: "Recent observations") {
                        ForEach(Array(model.snapshots.prefix(10)), id: \.idValue) { snapshot in
                            HStack {
                                Text(snapshot.text("licence_id") + " · " + dateText(snapshot.text("observed_at"))).frame(width: 170, alignment: .leading)
                                Text("\(snapshot.text("monthly_remaining")) / \(snapshot.text("monthly_allocation")) included · \(snapshot.text("top_up_remaining")) purchased")
                                Spacer()
                                Button("Delete", role: .destructive) { deleting = snapshot.idValue }.disabled(model.busy)
                            }.font(.caption)
                        }
                    }
                }
            }.padding(28)
        }
        .sheet(isPresented: $showRenewal) { SubscriptionForm(existing: nil, defaultProvider: "jetbrains") }
        .confirmationDialog("Delete this credit observation?", isPresented: Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } })) {
            Button("Delete observation", role: .destructive) {
                if let id = deleting { model.perform { _ = try await model.request("/v1/jetbrains/snapshots/\(id)", method: "DELETE") } }
                deleting = nil
            }
        }
        .onAppear {
            noTopups = model.jetbrainsSummary.flag("no_topups")
            allocation = "10"; remaining = ""
        }
    }
}

struct AlertsView: View {
    @EnvironmentObject var model: AppModel
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                Text("Your spending alerts.").font(.largeTitle.bold())
                Text("A stored record of threshold crossings and monitoring events.").foregroundStyle(.secondary)
                if model.alerts.isEmpty { ContentUnavailableView("No alerts", systemImage: "bell.badge.slash", description: Text("New spending alerts will appear here.")) }
                ForEach(model.alerts, id: \.idValue) { alert in
                    Panel(title: alert.text("title")) {
                        Text(alert.text("message")).textSelection(.enabled)
                        HStack {
                            Text(dateText(alert.text("created_at")))
                            Text("· \(alert.text("delivery_state").replacingOccurrences(of: "_", with: " "))")
                            Spacer()
                            if alert["acknowledged_at"] is String { Label("Acknowledged", systemImage: "checkmark") }
                            else { Button("Acknowledge") { model.perform { _ = try await model.request("/v1/alerts/\(alert.idValue)/acknowledge", method: "PATCH") } }.disabled(model.busy) }
                        }.font(.caption).foregroundStyle(.secondary)
                    }
                }
            }.padding(28)
        }
    }
}

struct PreferencesView: View {
    @EnvironmentObject var model: AppModel
    @State private var budget = ""
    @State private var currency = "USD"
    @State private var enabled = true
    @State private var notifications = true
    @State private var minutes = 60
    @State private var key = ""
    @State private var login = SMAppService.mainApp.status == .enabled
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                Panel(title: "Spending warnings") {
                    Form {
                        Toggle("Collect costs automatically", isOn: $enabled)
                        TextField("Monthly budget", text: $budget, prompt: Text("Leave blank to disable threshold alerts"))
                        Picker("Budget's original currency", selection: $currency) { Text("US dollars").tag("USD"); Text("Australian dollars").tag("AUD") }
                        Stepper("Check every \(minutes) minutes", value: $minutes, in: 1...1440)
                        Toggle("Show Mac desktop notifications", isOn: $notifications)
                    }
                    Text("Warnings at 50%, 80% and 95%. Spend and budget are shown in AU$ using a dated exchange rate. This app does not change your OpenAI limits.").font(.caption).foregroundStyle(.secondary)
                    Button("Save monitoring settings") {
                        model.perform {
                            let value: Record = ["enabled": enabled, "interval_minutes": minutes, "monthly_budget": budget.isEmpty ? NSNull() : budget as Any, "budget_currency": currency, "thresholds_percent": [50, 80, 95], "desktop_notifications": notifications]
                            _ = try await model.request("/v1/monitor/settings", method: "PUT", body: value)
                            model.notice = "Monitoring settings saved."
                        }
                    }.buttonStyle(.borderedProminent).disabled(model.busy || (!budget.isEmpty && Decimal(string: budget) == nil))
                }
                CreditPurchasePanel()
                Panel(title: "OpenAI connection") {
                    Label(model.hasKey ? "An Admin key is configured on this Mac" : "Add an OpenAI Admin key", systemImage: model.hasKey ? "checkmark.shield" : "key")
                    SecureField("New or replacement Admin key", text: $key)
                    Button("Save key and reconnect") { model.perform { try await model.saveKey(key); key = "" } }.disabled(model.busy || key.isEmpty)
                    Text("The key stays in your private local configuration and is never included in the app bundle.").font(.caption).foregroundStyle(.secondary)
                }
                Panel(title: "On this Mac") {
                    LabeledContent("Local server", value: "127.0.0.1:8781")
                    Toggle("Open AI Billing Monitor at login", isOn: $login).onChange(of: login) { _, value in
                        do { if value { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() } }
                        catch { model.error = error.localizedDescription; login = SMAppService.mainApp.status == .enabled }
                    }
                    Button("Test desktop notification") {
                        model.perform {
                            let result = try await model.request("/v1/alerts/test", method: "POST") as? Record ?? [:]
                            model.notice = result.text("delivery_state") == "submitted_to_macos" ? "Submitted to macOS. Check Notification Centre if no banner appears." : "Test saved in Alerts. Check its delivery status."
                        }
                    }.disabled(model.busy)
                    Button("Show local data folder") { NSWorkspace.shared.open(model.support) }
                    Button("Open service log") { NSWorkspace.shared.open(model.support.appendingPathComponent("backend.log")) }
                    Text("Closing the dashboard keeps monitoring active in the menu bar. Quitting the app stops monitoring. Your Mac must be awake and connected; phone push is not available yet.").font(.caption).foregroundStyle(.secondary)
                }
            }.padding(28)
        }.onAppear {
            budget = model.preferences.text("monthly_budget"); currency = model.preferences.text("budget_currency")
            enabled = model.preferences.flag("enabled"); notifications = model.preferences.flag("desktop_notifications")
            minutes = model.preferences["interval_minutes"] as? Int ?? 60
        }
    }
}
