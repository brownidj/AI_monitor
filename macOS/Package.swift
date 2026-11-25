// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "AIBillingMonitor",
    platforms: [.macOS(.v14)],
    products: [.executable(name: "AIBillingMonitor", targets: ["AIBillingMonitor"])],
    targets: [.executableTarget(name: "AIBillingMonitor")]
)
