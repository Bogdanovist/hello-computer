import Foundation

#if canImport(CoreGraphics)
import CoreGraphics
#endif

#if canImport(ApplicationServices)
import ApplicationServices
#endif

/// Injects text at the active cursor position using CGEvent keystroke injection.
/// Maintains a FIFO queue of InjectionContexts for correction observation.
final class TextInjector {

    // MARK: - Properties

    /// FIFO queue of injection contexts for correction observation.
    private(set) var contextQueue: [InjectionContext] = []

    /// Correction window duration in seconds.
    let correctionWindowSeconds: TimeInterval

    // MARK: - Lifecycle

    init(correctionWindowSeconds: Int = 30) {
        self.correctionWindowSeconds = TimeInterval(correctionWindowSeconds)
    }

    // MARK: - Text Injection

    /// Inject text at the active cursor position via CGEvent keystrokes.
    /// Creates an InjectionContext and appends it to the queue.
    /// Returns the InjectionContext, or nil if text is empty or injection fails.
    @discardableResult
    func inject(text: String, appBundleID: String) -> InjectionContext? {
        guard !text.isEmpty else {
            log(.info, "TextInjector skipping empty text")
            return nil
        }

        // Remove expired contexts before adding a new one
        purgeExpired()

        let now = Date()

        // Perform CGEvent keystroke injection
        let success = injectKeystrokes(text)
        guard success else {
            log(.warning, "TextInjector keystroke injection failed")
            return nil
        }

        // Capture the focused AX element for correction observation
        let axElement = getFocusedElement()

        let context = InjectionContext(
            injectedText: text,
            timestamp: now,
            axElementRef: axElement,
            appBundleID: appBundleID,
            correctionWindowExpiry: now.addingTimeInterval(correctionWindowSeconds)
        )

        contextQueue.append(context)
        log(.info, "TextInjector injected \(text.count) chars — \(contextQueue.count) context(s) in queue")

        return context
    }

    // MARK: - Queue Management

    /// Remove all InjectionContexts whose correction window has expired.
    func purgeExpired() {
        let now = Date()
        let before = contextQueue.count
        contextQueue.removeAll { $0.correctionWindowExpiry < now }
        let removed = before - contextQueue.count
        if removed > 0 {
            log(.info, "TextInjector purged \(removed) expired context(s)")
        }
    }

    // MARK: - Private — CGEvent Keystroke Injection

    /// Type each character in the text using CGEvent keyboard events.
    private func injectKeystrokes(_ text: String) -> Bool {
        #if canImport(CoreGraphics)
        let source = CGEventSource(stateID: .hidSystemState)

        for scalar in text.unicodeScalars {
            var char = UniChar(scalar.value)

            guard let keyDown = CGEvent(keyboardEventSource: source, virtualKey: 0, keyDown: true) else {
                return false
            }
            keyDown.keyboardSetUnicodeString(stringLength: 1, unicodeString: &char)
            keyDown.post(tap: .cghidEventTap)

            guard let keyUp = CGEvent(keyboardEventSource: source, virtualKey: 0, keyDown: false) else {
                return false
            }
            keyUp.keyboardSetUnicodeString(stringLength: 1, unicodeString: &char)
            keyUp.post(tap: .cghidEventTap)
        }

        return true
        #else
        // CGEvent not available on this platform (Linux build environment).
        // Return true so InjectionContext is still created for downstream testing.
        return true
        #endif
    }

    // MARK: - Private — Accessibility

    /// Get the currently focused AX UI element for correction observation.
    private func getFocusedElement() -> AnyObject? {
        #if canImport(ApplicationServices)
        let systemWide = AXUIElementCreateSystemWide()

        var focusedApp: AnyObject?
        let appResult = AXUIElementCopyAttributeValue(
            systemWide,
            kAXFocusedApplicationAttribute as CFString,
            &focusedApp
        )
        guard appResult == .success, let appElement = focusedApp else {
            log(.info, "TextInjector could not get focused application")
            return nil
        }

        // swiftlint:disable:next force_cast
        var focusedElement: AnyObject?
        let elementResult = AXUIElementCopyAttributeValue(
            appElement as! AXUIElement,
            kAXFocusedUIElementAttribute as CFString,
            &focusedElement
        )
        guard elementResult == .success else {
            log(.info, "TextInjector could not get focused UI element")
            return nil
        }

        return focusedElement
        #else
        // AX API not available on this platform.
        return nil
        #endif
    }
}
