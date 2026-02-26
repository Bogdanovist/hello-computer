import Foundation
#if canImport(AppKit)
import AppKit
#endif
#if canImport(AVFoundation)
import AVFoundation
#endif

/// Detects configured hotkey (Globe/Fn by default) and manages audio recording lifecycle.
///
/// Uses NSEvent.addGlobalMonitorForEvents to detect key-down/key-up events.
/// On key-down: checks microphone permission, starts recording via callback.
/// On key-up: stops recording, triggers transcription via callback.
/// Caps recording at a configurable max duration (default 60s).
public final class HotkeyListener {

    // MARK: - Types

    /// Supported hotkey configurations.
    public enum Hotkey: Equatable {
        /// Globe/Fn key (default on Apple Silicon Macs).
        case globe
        /// Specific key code with modifier flags (raw UInt value of NSEvent.ModifierFlags).
        case keyCode(UInt16, modifiers: UInt)

        /// Parse a hotkey from a config string.
        /// Recognises "globe" and "fn"; anything else falls back to globe.
        public static func from(_ string: String) -> Hotkey {
            switch string.lowercased() {
            case "globe", "fn":
                return .globe
            default:
                return .globe
            }
        }
    }

    /// Recording state exposed for external inspection.
    public enum State: Equatable {
        case idle
        case recording
    }

    // MARK: - Callbacks

    /// Called when hotkey is pressed and microphone permission is available — begin audio capture.
    public var onRecordingStart: (() -> Void)?

    /// Called when hotkey is released or max duration reached — stop capture, begin transcription.
    public var onRecordingStop: (() -> Void)?

    /// Optional logging callback. Messages are content-free (no transcribed text).
    public var logHandler: ((String) -> Void)?

    // MARK: - Properties

    /// The hotkey this listener is configured to detect.
    public let hotkey: Hotkey

    /// Maximum recording duration in seconds. Held hotkey is capped at this value.
    public let maxRecordingDuration: TimeInterval

    /// Current recording state.
    public private(set) var state: State = .idle

    #if canImport(AppKit)
    private var globalMonitor: Any?
    #endif
    private var capTimer: DispatchSourceTimer?
    private let queue = DispatchQueue(label: "com.vox.hotkey", qos: .userInteractive)
    private var micPermissionChecked: Bool = false
    private var micPermissionGranted: Bool = false

    // MARK: - Lifecycle

    /// Create a hotkey listener.
    /// - Parameters:
    ///   - hotkey: Which key combination to listen for (default: Globe/Fn).
    ///   - maxRecordingDuration: Cap recording at this many seconds (default: 60).
    public init(hotkey: Hotkey = .globe, maxRecordingDuration: TimeInterval = 60) {
        self.hotkey = hotkey
        self.maxRecordingDuration = maxRecordingDuration
    }

    deinit {
        stop()
    }

    // MARK: - Public Methods

    /// Start listening for global hotkey events.
    /// The app must have Accessibility API permission for global event monitoring.
    public func start() {
        #if canImport(AppKit)
        guard globalMonitor == nil else {
            logHandler?("Hotkey listener already running")
            return
        }

        refreshMicrophoneStatus()

        switch hotkey {
        case .globe:
            // Globe/Fn key triggers flagsChanged events with .function modifier.
            globalMonitor = NSEvent.addGlobalMonitorForEvents(matching: .flagsChanged) {
                [weak self] event in
                self?.handleGlobeFlagsChanged(event)
            }

        case .keyCode(let code, let modifiers):
            let expectedModifiers = NSEvent.ModifierFlags(rawValue: UInt(modifiers))
            globalMonitor = NSEvent.addGlobalMonitorForEvents(
                matching: [.keyDown, .keyUp]
            ) { [weak self] event in
                self?.handleKeyEvent(
                    event,
                    expectedKeyCode: code,
                    expectedModifiers: expectedModifiers
                )
            }
        }

        logHandler?("Hotkey listener started for \(hotkeyDescription)")
        #else
        logHandler?("Hotkey listener requires macOS — AppKit not available")
        #endif
    }

    /// Stop listening for hotkey events and cancel any in-progress recording timer.
    public func stop() {
        #if canImport(AppKit)
        if let monitor = globalMonitor {
            NSEvent.removeMonitor(monitor)
            globalMonitor = nil
        }
        #endif
        cancelCapTimer()
        if state == .recording {
            state = .idle
        }
        logHandler?("Hotkey listener stopped")
    }

    // MARK: - Event Handling

    #if canImport(AppKit)
    private func handleGlobeFlagsChanged(_ event: NSEvent) {
        let fnPressed = event.modifierFlags.contains(.function)

        if fnPressed && state == .idle {
            beginRecording()
        } else if !fnPressed && state == .recording {
            endRecording()
        }
    }

    private func handleKeyEvent(
        _ event: NSEvent,
        expectedKeyCode: UInt16,
        expectedModifiers: NSEvent.ModifierFlags
    ) {
        guard event.keyCode == expectedKeyCode else { return }

        // Compare only the standard modifier keys (ignore caps lock, numpad, etc.)
        let mask: NSEvent.ModifierFlags = [.command, .option, .control, .shift]
        let eventModifiers = event.modifierFlags.intersection(mask)
        let expected = expectedModifiers.intersection(mask)
        guard eventModifiers == expected else { return }

        if event.type == .keyDown && state == .idle {
            beginRecording()
        } else if event.type == .keyUp && state == .recording {
            endRecording()
        }
    }
    #endif

    // MARK: - Recording Lifecycle

    private func beginRecording() {
        // Re-check mic permission on each press (user may grant/revoke between presses)
        refreshMicrophoneStatus()

        guard micPermissionGranted else {
            logHandler?("Microphone permission not granted — requesting access")
            requestMicrophoneAccess()
            return
        }

        state = .recording
        logHandler?("Recording started")

        startCapTimer()
        onRecordingStart?()
    }

    private func endRecording() {
        guard state == .recording else { return }
        cancelCapTimer()
        state = .idle
        logHandler?("Recording stopped")
        onRecordingStop?()
    }

    // MARK: - Recording Cap Timer

    /// Start a timer that caps recording at maxRecordingDuration seconds.
    private func startCapTimer() {
        cancelCapTimer()
        let timer = DispatchSource.makeTimerSource(queue: queue)
        timer.schedule(deadline: .now() + maxRecordingDuration)
        timer.setEventHandler { [weak self] in
            guard let self = self, self.state == .recording else { return }
            self.logHandler?("Recording capped at \(Int(self.maxRecordingDuration))s")
            DispatchQueue.main.async {
                self.endRecording()
            }
        }
        capTimer = timer
        timer.resume()
    }

    private func cancelCapTimer() {
        capTimer?.cancel()
        capTimer = nil
    }

    // MARK: - Microphone Permission

    /// Check current microphone authorization status without prompting.
    private func refreshMicrophoneStatus() {
        #if canImport(AVFoundation)
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized:
            micPermissionGranted = true
            micPermissionChecked = true
        case .notDetermined:
            micPermissionGranted = false
            micPermissionChecked = false
        case .denied, .restricted:
            micPermissionGranted = false
            micPermissionChecked = true
            if !micPermissionChecked {
                logHandler?("Microphone permission denied or restricted")
            }
        @unknown default:
            micPermissionGranted = false
        }
        #else
        micPermissionGranted = true
        micPermissionChecked = true
        #endif
    }

    /// Request microphone access. macOS will show a system alert if not yet determined.
    private func requestMicrophoneAccess() {
        #if canImport(AVFoundation)
        AVCaptureDevice.requestAccess(for: .audio) { [weak self] granted in
            guard let self = self else { return }
            self.micPermissionGranted = granted
            self.micPermissionChecked = true
            if granted {
                self.logHandler?("Microphone permission granted")
            } else {
                self.logHandler?("Microphone permission denied by user")
            }
        }
        #endif
    }

    // MARK: - Helpers

    private var hotkeyDescription: String {
        switch hotkey {
        case .globe:
            return "Globe/Fn"
        case .keyCode(let code, let modifiers):
            return "keyCode=\(code) modifiers=\(modifiers)"
        }
    }
}
