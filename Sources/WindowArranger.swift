import AppKit
import ApplicationServices

enum WindowArranger {
    static func arrange(left: pid_t, right: pid_t) -> Bool {
        let prompt = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
        guard AXIsProcessTrustedWithOptions(prompt), let screen = NSScreen.main else { return false }
        let area = screen.visibleFrame
        let gap: CGFloat = 12
        let width = (area.width - gap) / 2
        return move(pid: left, to: CGRect(x: area.minX, y: area.minY, width: width, height: area.height)) &&
               move(pid: right, to: CGRect(x: area.minX + width + gap, y: area.minY, width: width, height: area.height))
    }

    private static func move(pid: pid_t, to frame: CGRect) -> Bool {
        let app = AXUIElementCreateApplication(pid)
        var raw: CFTypeRef?
        guard AXUIElementCopyAttributeValue(app, kAXWindowsAttribute as CFString, &raw) == .success,
              let windows = raw as? [AXUIElement], let window = windows.first else { return false }
        var position = frame.origin
        var size = frame.size
        guard let positionValue = AXValueCreate(.cgPoint, &position),
              let sizeValue = AXValueCreate(.cgSize, &size) else { return false }
        let moved = AXUIElementSetAttributeValue(window, kAXPositionAttribute as CFString, positionValue)
        let resized = AXUIElementSetAttributeValue(window, kAXSizeAttribute as CFString, sizeValue)
        return moved == .success && resized == .success
    }
}
