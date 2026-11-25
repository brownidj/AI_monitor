import AppKit
import SwiftUI

final class AppDelegate: NSObject, NSApplicationDelegate {
    var model: AppModel?
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
    func applicationWillTerminate(_ notification: Notification) {
        MainActor.assumeIsolated { model?.quitting = true; model?.stop() }
    }
}

@main struct BillingMonitorApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate
    @StateObject private var model = AppModel()
    @Environment(\.openWindow) private var openWindow
    var body: some Scene {
        WindowGroup("AI Billing Monitor", id: "main") {
            MainView().environmentObject(model).task {
                delegate.model = model
                if !model.connected { await model.start() }
            }
        }.defaultSize(width: 1140, height: 800)
        .commands { CommandGroup(replacing: .newItem) {} }
        MenuBarExtra("AI Billing Monitor", systemImage: "chart.bar.fill") {
            Text("API spending: \(money(model.history.number("converted_total_estimate")))")
            Text(model.fresh ? "Monitoring is up to date" : "Monitoring needs attention")
            Divider()
            Button("Open dashboard") { openWindow(id: "main"); NSApp.activate(ignoringOtherApps: true) }
            Button("Check now") { model.checkNow() }.disabled(model.busy || !model.connected)
            Link("Buy OpenAI credits…", destination: openAIBillingURL)
            Divider()
            Button("Quit AI Billing Monitor") { NSApp.terminate(nil) }.keyboardShortcut("q")
        }
    }
}
