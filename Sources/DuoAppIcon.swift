import AppKit

enum DuoAppIcon {
    static func make() -> NSImage {
        NSImage(size: NSSize(width: 128, height: 128), flipped: false) { _ in
            let base = NSBezierPath(roundedRect: NSRect(x: 9, y: 9, width: 110, height: 110), xRadius: 27, yRadius: 27)
            NSGradient(colors: [.systemBlue, .systemIndigo])?.draw(in: base, angle: -45)

            NSColor.white.withAlphaComponent(0.95).setFill()
            NSBezierPath(roundedRect: NSRect(x: 30, y: 56, width: 46, height: 46), xRadius: 11, yRadius: 11).fill()
            NSColor.white.withAlphaComponent(0.76).setFill()
            NSBezierPath(roundedRect: NSRect(x: 53, y: 31, width: 46, height: 46), xRadius: 11, yRadius: 11).fill()

            NSColor.systemOrange.setFill()
            NSBezierPath(ovalIn: NSRect(x: 88, y: 84, width: 15, height: 15)).fill()
            NSColor.systemMint.setFill()
            NSBezierPath(ovalIn: NSRect(x: 25, y: 28, width: 12, height: 12)).fill()
            return true
        }
    }
}
