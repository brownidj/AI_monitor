import AppKit

let image = NSImage(size: NSSize(width: 1024, height: 1024))
image.lockFocus()
let background = NSBezierPath(roundedRect: NSRect(x: 40, y: 40, width: 944, height: 944), xRadius: 210, yRadius: 210)
NSGradient(starting: NSColor(calibratedRed: 0.06, green: 0.24, blue: 0.22, alpha: 1), ending: NSColor(calibratedRed: 0.10, green: 0.62, blue: 0.43, alpha: 1))!.draw(in: background, angle: 45)
NSColor.white.withAlphaComponent(0.92).setFill()
for (index, height) in [200, 340, 510].enumerated() {
    NSBezierPath(roundedRect: NSRect(x: 230 + index * 200, y: 250, width: 135, height: height), xRadius: 35, yRadius: 35).fill()
}
image.unlockFocus()
let bitmap = NSBitmapImageRep(data: image.tiffRepresentation!)!
try bitmap.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
