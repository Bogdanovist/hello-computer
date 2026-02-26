import Foundation

#if canImport(ApplicationServices)
import ApplicationServices
#endif

// MARK: - Levenshtein Distance

/// Compute the Levenshtein edit distance between two strings.
public func levenshteinDistance(_ a: String, _ b: String) -> Int {
    let aChars = Array(a)
    let bChars = Array(b)
    let m = aChars.count
    let n = bChars.count

    if m == 0 { return n }
    if n == 0 { return m }

    var prev = Array(0...n)
    var curr = [Int](repeating: 0, count: n + 1)

    for i in 1...m {
        curr[0] = i
        for j in 1...n {
            let cost = aChars[i - 1] == bChars[j - 1] ? 0 : 1
            curr[j] = min(
                prev[j] + 1,
                curr[j - 1] + 1,
                prev[j - 1] + cost
            )
        }
        swap(&prev, &curr)
    }

    return prev[n]
}

/// Compute the Levenshtein edit distance ratio: distance / max(len(a), len(b)).
/// Returns 0.0 if both strings are empty.
public func levenshteinRatio(_ a: String, _ b: String) -> Double {
    let maxLen = max(a.count, b.count)
    guard maxLen > 0 else { return 0.0 }
    return Double(levenshteinDistance(a, b)) / Double(maxLen)
}

// MARK: - Types

/// Observation strategy for correction capture.
public enum ObservationStrategy: Equatable {
    case native
    case browserHybrid
}

/// Tracks the state of an active observation session after text injection.
public struct ObservationSession {
    public let injectedText: String
    public let injectionTimestamp: Date
    public let appBundleID: String
    public let axElement: AnyObject  // AXUIElement on macOS
    public let strategy: ObservationStrategy
    public internal(set) var latestValue: String?
    public let windowExpiry: Date
}

/// Emitted when a valid correction is captured within edit distance thresholds.
public struct CorrectionEvent {
    public let injected: String
    public let corrected: String
    public let appBundleID: String
}

// MARK: - CorrectionObserver

/// Observes user corrections to injected text via macOS Accessibility API.
///
/// After text is injected at the cursor, registers an AXObserver for
/// kAXValueChangedNotification on the focused element. Uses debounce logic
/// to wait for the user to finish editing, then computes Levenshtein edit
/// distance to determine if the change qualifies as a correction.
public final class CorrectionObserver {

    // MARK: - Configuration

    /// Duration of the correction observation window in seconds.
    public let correctionWindowSeconds: TimeInterval

    /// Debounce duration — captures final value after this period of inactivity.
    public let debounceSeconds: TimeInterval

    /// Minimum edit distance ratio to count as a correction (below = noise).
    public let minEditRatio: Double

    /// Maximum edit distance ratio to count as a correction (above = rewrite).
    public let maxEditRatio: Double

    // MARK: - Callbacks

    /// Called when a valid correction is captured.
    public var onCorrectionCaptured: ((CorrectionEvent) -> Void)?

    /// Optional logging callback. Messages are content-free (no transcribed text).
    public var logHandler: ((String) -> Void)?

    // MARK: - Internal State

    private var session: ObservationSession?
    private var debounceTimer: DispatchSourceTimer?
    private var windowTimer: DispatchSourceTimer?
    #if canImport(ApplicationServices)
    private var axObserver: AXObserver?
    #endif

    // MARK: - Lifecycle

    public init(
        correctionWindowSeconds: TimeInterval = 30,
        debounceSeconds: TimeInterval = 2,
        minEditRatio: Double = 0.05,
        maxEditRatio: Double = 0.80
    ) {
        self.correctionWindowSeconds = correctionWindowSeconds
        self.debounceSeconds = debounceSeconds
        self.minEditRatio = minEditRatio
        self.maxEditRatio = maxEditRatio
    }

    deinit {
        stopObserving()
    }

    // MARK: - Public API

    /// The currently active observation session, if any.
    public var activeSession: ObservationSession? {
        return session
    }

    /// Begin observing a text field for corrections after injection.
    ///
    /// Cancels any previously active session. On macOS, registers an AXObserver
    /// for kAXValueChangedNotification on the provided element. On other
    /// platforms, sets up the session without AX observation.
    public func startObserving(
        injectedText: String,
        appBundleID: String,
        axElement: AnyObject,
        correctionWindowExpiry: Date
    ) {
        stopObserving()

        session = ObservationSession(
            injectedText: injectedText,
            injectionTimestamp: Date(),
            appBundleID: appBundleID,
            axElement: axElement,
            strategy: .native,
            latestValue: nil,
            windowExpiry: correctionWindowExpiry
        )

        #if canImport(ApplicationServices)
        if !setupAXObserver() {
            logHandler?("Correction observer — AX observer setup failed, observation skipped")
            session = nil
            return
        }
        #else
        logHandler?("Correction observer — ApplicationServices not available, AX observation disabled")
        #endif

        // Start window expiry timer
        let timer = DispatchSource.makeTimerSource(queue: .main)
        let timeUntilExpiry = max(correctionWindowExpiry.timeIntervalSinceNow, 0)
        timer.schedule(deadline: .now() + timeUntilExpiry)
        timer.setEventHandler { [weak self] in
            self?.handleWindowExpiry()
        }
        windowTimer = timer
        timer.resume()

        logHandler?("Correction observer started — native strategy for \(appBundleID)")
    }

    /// Stop all observation: cancel timers, remove AX observer, clear session.
    public func stopObserving() {
        guard session != nil else { return }
        cancelDebounceTimer()
        cancelWindowTimer()
        #if canImport(ApplicationServices)
        removeAXObserver()
        #endif
        session = nil
    }

    // MARK: - Value Update (internal for testability)

    /// Handle a value change in the observed text field.
    /// On macOS, called by the AXObserver callback. Can be called directly in tests.
    func handleValueUpdate(_ newValue: String) {
        guard session != nil else { return }
        session?.latestValue = newValue
        resetDebounceTimer()
    }

    // MARK: - AX Observer Setup

    #if canImport(ApplicationServices)
    private func setupAXObserver() -> Bool {
        guard let currentSession = session,
              let element = currentSession.axElement as? AXUIElement else {
            logHandler?("Correction observer — invalid AX element type")
            return false
        }

        var pid: pid_t = 0
        guard AXUIElementGetPid(element, &pid) == .success else {
            logHandler?("Correction observer — could not get PID for AX element")
            return false
        }

        var observer: AXObserver?
        guard AXObserverCreate(pid, correctionAXCallback, &observer) == .success,
              let newObserver = observer else {
            logHandler?("Correction observer — AXObserver creation failed")
            return false
        }

        let refcon = Unmanaged.passUnretained(self).toOpaque()
        guard AXObserverAddNotification(
            newObserver,
            element,
            kAXValueChangedNotification as CFString,
            refcon
        ) == .success else {
            logHandler?("Correction observer — failed to register for AXValueChanged")
            return false
        }

        CFRunLoopAddSource(
            CFRunLoopGetMain(),
            AXObserverGetRunLoopSource(newObserver),
            .defaultMode
        )

        axObserver = newObserver
        return true
    }

    private func removeAXObserver() {
        guard let observer = axObserver else { return }
        if let element = session?.axElement as? AXUIElement {
            AXObserverRemoveNotification(
                observer,
                element,
                kAXValueChangedNotification as CFString
            )
        }
        CFRunLoopRemoveSource(
            CFRunLoopGetMain(),
            AXObserverGetRunLoopSource(observer),
            .defaultMode
        )
        axObserver = nil
    }

    /// Called by the AX observer C callback when the element's value changes.
    fileprivate func handleAXValueChanged(element: AXUIElement) {
        guard session != nil else { return }

        var value: AnyObject?
        let result = AXUIElementCopyAttributeValue(
            element,
            kAXValueAttribute as CFString,
            &value
        )
        guard result == .success, let stringValue = value as? String else {
            logHandler?("Correction observer — could not read AX value")
            return
        }

        handleValueUpdate(stringValue)
    }
    #endif

    // MARK: - Debounce Timer

    private func resetDebounceTimer() {
        cancelDebounceTimer()
        let timer = DispatchSource.makeTimerSource(queue: .main)
        timer.schedule(deadline: .now() + debounceSeconds)
        timer.setEventHandler { [weak self] in
            self?.handleDebounceExpiry()
        }
        debounceTimer = timer
        timer.resume()
    }

    private func handleDebounceExpiry() {
        guard let currentSession = session,
              let correctedText = currentSession.latestValue else { return }

        let injectedText = currentSession.injectedText
        let ratio = levenshteinRatio(injectedText, correctedText)

        if ratio < minEditRatio {
            logHandler?("Correction discarded — edit ratio \(String(format: "%.3f", ratio)) below minimum \(minEditRatio)")
            return
        }

        if ratio > maxEditRatio {
            logHandler?("Correction discarded — edit ratio \(String(format: "%.3f", ratio)) above maximum \(maxEditRatio)")
            return
        }

        let event = CorrectionEvent(
            injected: injectedText,
            corrected: correctedText,
            appBundleID: currentSession.appBundleID
        )

        logHandler?("Correction captured — edit ratio \(String(format: "%.3f", ratio))")

        // Stop observation before invoking callback so the consumer can start
        // a new observation from within the callback if needed.
        stopObserving()
        onCorrectionCaptured?(event)
    }

    // MARK: - Window Expiry

    private func handleWindowExpiry() {
        logHandler?("Correction window expired — removing observer")
        stopObserving()
    }

    // MARK: - Timer Cleanup

    private func cancelDebounceTimer() {
        debounceTimer?.cancel()
        debounceTimer = nil
    }

    private func cancelWindowTimer() {
        windowTimer?.cancel()
        windowTimer = nil
    }
}

// MARK: - AX Observer C Callback

#if canImport(ApplicationServices)
/// C-compatible callback invoked by the AX observer when element value changes.
private func correctionAXCallback(
    _ observer: AXObserver,
    _ element: AXUIElement,
    _ notification: CFString,
    _ refcon: UnsafeMutableRawPointer?
) {
    guard let refcon = refcon else { return }
    let correctionObserver = Unmanaged<CorrectionObserver>
        .fromOpaque(refcon)
        .takeUnretainedValue()
    correctionObserver.handleAXValueChanged(element: element)
}
#endif
