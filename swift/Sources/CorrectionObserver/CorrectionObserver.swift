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
///
/// Supports two observation strategies:
/// - **Native**: AXValueChanged notifications only (most apps)
/// - **Browser hybrid**: AXValueChanged + 500ms polling fallback (browsers)
///
/// Apps on the blocklist are never observed (zero AX reads, zero polling).
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

    /// App bundle IDs where observation is blocked entirely.
    public let blocklistBundleIDs: Set<String>

    /// Window title substrings (case-insensitive) that block observation.
    public let blocklistTitlePatterns: [String]

    // MARK: - Browser Detection

    /// Known browser bundle IDs that use the hybrid observation strategy.
    public static let knownBrowserBundleIDs: Set<String> = [
        "com.google.Chrome",
        "com.apple.Safari",
        "company.thebrowser.Browser",  // Arc
        "org.mozilla.firefox",
        "com.brave.Browser",
        "com.microsoft.edgemac",
    ]

    // MARK: - Callbacks

    /// Called when a valid correction is captured.
    public var onCorrectionCaptured: ((CorrectionEvent) -> Void)?

    /// Optional logging callback. Messages are content-free (no transcribed text).
    public var logHandler: ((String) -> Void)?

    // MARK: - Internal State

    private var session: ObservationSession?
    private var debounceTimer: DispatchSourceTimer?
    private var windowTimer: DispatchSourceTimer?
    private var pollingTimer: DispatchSourceTimer?
    private var modeDecisionTimer: DispatchSourceTimer?
    private var axValueChangedReceived = false
    private var lastPolledValue: String?
    #if canImport(ApplicationServices)
    private var axObserver: AXObserver?
    #endif

    // MARK: - Lifecycle

    public init(
        correctionWindowSeconds: TimeInterval = 30,
        debounceSeconds: TimeInterval = 2,
        minEditRatio: Double = 0.05,
        maxEditRatio: Double = 0.80,
        blocklistBundleIDs: Set<String> = [],
        blocklistTitlePatterns: [String] = []
    ) {
        self.correctionWindowSeconds = correctionWindowSeconds
        self.debounceSeconds = debounceSeconds
        self.minEditRatio = minEditRatio
        self.maxEditRatio = maxEditRatio
        self.blocklistBundleIDs = blocklistBundleIDs
        self.blocklistTitlePatterns = blocklistTitlePatterns
    }

    deinit {
        stopObserving()
    }

    // MARK: - Public API

    /// The currently active observation session, if any.
    public var activeSession: ObservationSession? {
        return session
    }

    /// Check whether observation is allowed for a given app and window.
    ///
    /// Returns `false` if the app bundle ID is in the blocklist or if the
    /// window title matches any blocklist pattern (case-insensitive).
    /// When this returns `false`: zero AX reads, zero polling, zero content logging.
    public func shouldObserve(appBundleID: String, windowTitle: String) -> Bool {
        if blocklistBundleIDs.contains(appBundleID) {
            return false
        }
        for pattern in blocklistTitlePatterns {
            if windowTitle.localizedCaseInsensitiveContains(pattern) {
                return false
            }
        }
        return true
    }

    /// Determine the observation strategy for an app based on its bundle ID.
    /// Browsers use `.browserHybrid`; all other apps use `.native`.
    public static func selectStrategy(appBundleID: String) -> ObservationStrategy {
        if knownBrowserBundleIDs.contains(appBundleID) {
            return .browserHybrid
        }
        return .native
    }

    /// Begin observing a text field for corrections after injection.
    ///
    /// Checks the blocklist first — if the app or window title is blocked,
    /// no observation is started. Otherwise, selects the strategy (native
    /// or browser hybrid) based on the app bundle ID.
    ///
    /// Cancels any previously active session. On macOS, registers an AXObserver
    /// for kAXValueChangedNotification on the provided element. For browsers,
    /// additionally starts a 500ms polling timer with a 5s mode decision window.
    /// On other platforms, sets up the session without AX observation.
    public func startObserving(
        injectedText: String,
        appBundleID: String,
        axElement: AnyObject,
        correctionWindowExpiry: Date,
        windowTitle: String = ""
    ) {
        // Blocklist check — zero AX reads, zero polling, zero content logging
        if !shouldObserve(appBundleID: appBundleID, windowTitle: windowTitle) {
            logHandler?("Correction observer — app blocklisted, observation skipped")
            return
        }

        stopObserving()

        let strategy = CorrectionObserver.selectStrategy(appBundleID: appBundleID)

        session = ObservationSession(
            injectedText: injectedText,
            injectionTimestamp: Date(),
            appBundleID: appBundleID,
            axElement: axElement,
            strategy: strategy,
            latestValue: nil,
            windowExpiry: correctionWindowExpiry
        )

        axValueChangedReceived = false
        lastPolledValue = nil

        #if canImport(ApplicationServices)
        if !setupAXObserver() {
            logHandler?("Correction observer — AX observer setup failed, observation skipped")
            session = nil
            return
        }

        // Browser hybrid: start polling + mode decision timer
        if strategy == .browserHybrid {
            startPollingTimer()
            startModeDecisionTimer()
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

        let strategyName = strategy == .browserHybrid ? "browser hybrid" : "native"
        logHandler?("Correction observer started — \(strategyName) strategy for \(appBundleID)")
    }

    /// Stop all observation: cancel timers, remove AX observer, clear session.
    public func stopObserving() {
        guard session != nil else { return }
        cancelDebounceTimer()
        cancelWindowTimer()
        cancelPollingTimer()
        cancelModeDecisionTimer()
        #if canImport(ApplicationServices)
        removeAXObserver()
        #endif
        session = nil
        axValueChangedReceived = false
        lastPolledValue = nil
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
        guard let currentSession = session else { return }

        // In browser hybrid mode, first AXValueChanged cancels polling
        if currentSession.strategy == .browserHybrid && !axValueChangedReceived {
            axValueChangedReceived = true
            cancelPollingTimer()
            cancelModeDecisionTimer()
            logHandler?("Correction observer — AXValueChanged received in browser, switching to native strategy")
        }

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

    // MARK: - Browser Hybrid Polling

    #if canImport(ApplicationServices)
    /// Start a 500ms repeating polling timer that reads the AX value of the
    /// focused element. Used in browser hybrid strategy as a fallback when
    /// AXValueChanged notifications may not fire reliably.
    private func startPollingTimer() {
        let timer = DispatchSource.makeTimerSource(queue: .main)
        timer.schedule(deadline: .now() + 0.5, repeating: 0.5)
        timer.setEventHandler { [weak self] in
            self?.pollAXValue()
        }
        pollingTimer = timer
        timer.resume()
    }

    /// Read the current AX value of the observed element. If the value has
    /// changed since the last poll, trigger a value update (which resets
    /// the debounce timer). If the value is unreadable, cancel observation
    /// gracefully — dictation still works, only learning is degraded.
    private func pollAXValue() {
        guard let currentSession = session,
              let element = currentSession.axElement as? AXUIElement else { return }

        var value: AnyObject?
        let result = AXUIElementCopyAttributeValue(
            element,
            kAXValueAttribute as CFString,
            &value
        )

        if result != .success {
            logHandler?("AX tree does not expose text content for \(currentSession.appBundleID). Correction capture skipped.")
            stopObserving()
            return
        }

        guard let stringValue = value as? String else { return }

        // Only trigger update if value actually changed since last poll
        let baseline = lastPolledValue ?? currentSession.injectedText
        if stringValue != baseline {
            lastPolledValue = stringValue
            handleValueUpdate(stringValue)
        }
    }

    /// Start the 5-second mode decision timer for browser hybrid strategy.
    /// If no AXValueChanged fires within 5s, remove the AX observer and
    /// continue with polling-only for the remainder of the correction window.
    private func startModeDecisionTimer() {
        let timer = DispatchSource.makeTimerSource(queue: .main)
        timer.schedule(deadline: .now() + 5.0)
        timer.setEventHandler { [weak self] in
            self?.handleModeDecision()
        }
        modeDecisionTimer = timer
        timer.resume()
    }

    private func handleModeDecision() {
        guard session != nil, !axValueChangedReceived else { return }
        // No AXValueChanged after 5s — remove AX observer, continue polling
        removeAXObserver()
        logHandler?("Correction observer — no AXValueChanged after 5s, continuing with polling only")
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

    private func cancelPollingTimer() {
        pollingTimer?.cancel()
        pollingTimer = nil
    }

    private func cancelModeDecisionTimer() {
        modeDecisionTimer?.cancel()
        modeDecisionTimer = nil
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
