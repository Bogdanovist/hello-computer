import Foundation
import CorrectionObserver
import HotkeyListener

#if canImport(AppKit)
import AppKit
#endif

#if canImport(ApplicationServices)
import ApplicationServices
#endif

// MARK: - Startup

func main() {
    let config = loadConfig()
    log(.info, "VoxDaemon starting — pid=\(ProcessInfo.processInfo.processIdentifier)")
    checkAccessibilityPermission()

    let coordinator = DictationCoordinator(config: config)
    coordinator.start()

    log(.info, "VoxDaemon ready — entering run loop")
    withExtendedLifetime(coordinator) {
        RunLoop.current.run()
    }
}

// MARK: - Configuration

struct VoxConfig {
    let hotkey: String
    let whisperModel: String
    let correctionWindowSeconds: Int
    let blocklistBundleIDs: [String]
    let blocklistTitlePatterns: [String]
}

func loadConfig() -> VoxConfig {
    let configPath = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent(".vox/config.toml")

    // TODO: Parse TOML config file; fall back to defaults
    log(.info, "Loading config from \(configPath.path)")

    return VoxConfig(
        hotkey: "globe",
        whisperModel: "large-v3-turbo.en",
        correctionWindowSeconds: 30,
        blocklistBundleIDs: [
            "com.1password.1password",
            "com.agilebits.onepassword7",
            "com.apple.keychainaccess",
            "com.apple.systempreferences",
            "com.bitwarden.desktop",
            "com.lastpass.LastPass",
        ],
        blocklistTitlePatterns: [
            "password", "credential", "secret", "keychain", "ssh", "gpg",
        ]
    )
}

// MARK: - Accessibility

func checkAccessibilityPermission() {
    log(.info, "Checking Accessibility API permission")
    #if canImport(ApplicationServices)
    let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
    let trusted = AXIsProcessTrustedWithOptions(options)
    if trusted {
        log(.info, "Accessibility API permission granted")
    } else {
        log(.warning, "Accessibility API permission not granted — correction observation will be disabled")
    }
    #endif
}

// MARK: - Dictation Coordinator

/// Coordinates the complete dictation pipeline: hotkey → audio → whisper →
/// IPC transcription → inject response → text injection → correction observation.
///
/// Handles Python not connected (queue + retry every 2s + fallback inject raw after 10s)
/// and rapid successive dictations via FIFO queuing.
final class DictationCoordinator {

    // MARK: - Components

    private let ipcServer: IPCServer
    private let textInjector: TextInjector
    private let correctionObserver: CorrectionObserver
    private let audioCapture: AudioCapture
    private let whisperBridge: WhisperBridge?
    private let hotkeyListener: HotkeyListener

    // MARK: - Pending Dictation Queue

    /// Tracks a transcription awaiting Python's inject response or fallback timeout.
    private struct PendingDictation {
        let id: UUID
        let raw: String
        let appBundleID: String
        let windowTitle: String
        var sentToPython: Bool
    }

    /// FIFO queue of in-flight dictations awaiting inject responses.
    private var pendingQueue: [PendingDictation] = []

    /// Per-dictation fallback timers (10s timeout → inject raw).
    private var fallbackTimers: [UUID: DispatchSourceTimer] = [:]

    /// Retry timer for sending queued transcriptions when Python reconnects (every 2s).
    private var retryTimer: DispatchSourceTimer?

    /// Background queue for whisper.cpp transcription (avoids blocking main run loop).
    private let transcriptionQueue = DispatchQueue(label: "com.vox.transcription", qos: .userInitiated)

    // MARK: - Lifecycle

    init(config: VoxConfig) {
        audioCapture = AudioCapture()
        audioCapture.logHandler = { log(.info, $0) }

        var bridge: WhisperBridge?
        do {
            bridge = try WhisperBridge(modelName: config.whisperModel)
            bridge?.logHandler = { log(.info, $0) }
        } catch {
            log(.error, "Failed to load whisper model: \(error) — transcription disabled")
        }
        whisperBridge = bridge

        textInjector = TextInjector(correctionWindowSeconds: config.correctionWindowSeconds)

        correctionObserver = CorrectionObserver(
            correctionWindowSeconds: TimeInterval(config.correctionWindowSeconds),
            blocklistBundleIDs: Set(config.blocklistBundleIDs),
            blocklistTitlePatterns: config.blocklistTitlePatterns
        )
        correctionObserver.logHandler = { log(.info, $0) }

        ipcServer = IPCServer(socketPath: "/tmp/vox.sock")

        hotkeyListener = HotkeyListener(
            hotkey: .from(config.hotkey),
            maxRecordingDuration: 60
        )
        hotkeyListener.logHandler = { log(.info, $0) }
    }

    /// Start listening for hotkeys and IPC connections.
    func start() {
        wireCallbacks()

        do {
            try ipcServer.start()
        } catch {
            log(.error, "Failed to start IPC server: \(error)")
        }

        hotkeyListener.start()
    }

    // MARK: - Callback Wiring

    private func wireCallbacks() {
        // IPC incoming messages → dispatch to main queue for thread safety
        ipcServer.onMessage = { [weak self] data in
            DispatchQueue.main.async {
                self?.handleIPCMessage(data)
            }
        }

        // Correction captured → send to Python via IPC
        correctionObserver.onCorrectionCaptured = { [weak self] event in
            self?.handleCorrection(event)
        }

        // Hotkey pressed → start audio capture
        hotkeyListener.onRecordingStart = { [weak self] in
            self?.handleRecordingStart()
        }

        // Hotkey released → stop capture, transcribe, send to Python
        hotkeyListener.onRecordingStop = { [weak self] in
            self?.handleRecordingStop()
        }
    }

    // MARK: - Recording Lifecycle

    private func handleRecordingStart() {
        do {
            try audioCapture.startRecording()
        } catch {
            log(.error, "Audio capture failed: \(error)")
        }
    }

    private func handleRecordingStop() {
        guard let audioURL = audioCapture.stopRecording() else {
            log(.warning, "No audio URL after recording stop")
            return
        }

        // Capture frontmost app context now (before background transcription changes focus)
        let appInfo = getFrontmostAppInfo()

        // Transcribe on background queue to avoid blocking main run loop
        transcriptionQueue.async { [weak self] in
            self?.transcribe(audioURL: audioURL, appInfo: appInfo)
        }
    }

    private func transcribe(audioURL: URL, appInfo: (bundleID: String, windowTitle: String)) {
        guard let bridge = whisperBridge else {
            log(.warning, "Whisper not available — cannot transcribe")
            AudioCapture.deleteTempFile(audioURL)
            return
        }

        let rawText: String?
        do {
            // WhisperBridge deletes the audio temp file in its defer block
            rawText = try bridge.transcribe(audioFileURL: audioURL)
        } catch {
            log(.error, "Transcription failed: \(error)")
            return
        }

        guard let raw = rawText, !raw.isEmpty else {
            log(.info, "Whisper produced empty output — skipping")
            return
        }

        // Dispatch back to main queue for state management
        DispatchQueue.main.async { [weak self] in
            self?.handleTranscription(raw: raw, appBundleID: appInfo.bundleID, windowTitle: appInfo.windowTitle)
        }
    }

    // MARK: - Transcription → IPC

    private func handleTranscription(raw: String, appBundleID: String, windowTitle: String) {
        let id = UUID()
        var sentToPython = false

        // Try to send to Python immediately
        if ipcServer.isClientConnected {
            let message = TranscriptionMessage(
                type: .transcription,
                raw: raw,
                appBundleID: appBundleID,
                timestamp: ISO8601DateFormatter().string(from: Date())
            )
            if ipcServer.send(message: message) {
                sentToPython = true
                log(.info, "Transcription sent to Python (\(raw.count) chars)")
            }
        }

        if !sentToPython {
            log(.info, "Python not connected — queuing transcription (\(raw.count) chars)")
        }

        pendingQueue.append(PendingDictation(
            id: id,
            raw: raw,
            appBundleID: appBundleID,
            windowTitle: windowTitle,
            sentToPython: sentToPython
        ))

        // Start 10s fallback timer — inject raw whisper output if no Python response
        let timer = DispatchSource.makeTimerSource(queue: .main)
        timer.schedule(deadline: .now() + 10.0)
        timer.setEventHandler { [weak self] in
            self?.handleFallback(id: id)
        }
        fallbackTimers[id] = timer
        timer.resume()

        // Start retry timer if transcription not yet sent to Python
        if !sentToPython {
            startRetryTimerIfNeeded()
        }
    }

    // MARK: - Python Not Connected Handling

    private func handleFallback(id: UUID) {
        fallbackTimers[id]?.cancel()
        fallbackTimers.removeValue(forKey: id)

        guard let index = pendingQueue.firstIndex(where: { $0.id == id }) else { return }
        let pending = pendingQueue.remove(at: index)

        log(.warning, "Python not available after 10s — injecting raw whisper output (\(pending.raw.count) chars)")
        injectAndObserve(text: pending.raw, appBundleID: pending.appBundleID, windowTitle: pending.windowTitle)

        stopRetryTimerIfIdle()
    }

    private func startRetryTimerIfNeeded() {
        guard retryTimer == nil else { return }
        let timer = DispatchSource.makeTimerSource(queue: .main)
        timer.schedule(deadline: .now() + 2.0, repeating: 2.0)
        timer.setEventHandler { [weak self] in
            self?.retryPendingTranscriptions()
        }
        retryTimer = timer
        timer.resume()
    }

    private func retryPendingTranscriptions() {
        guard ipcServer.isClientConnected else { return }

        for i in pendingQueue.indices {
            guard !pendingQueue[i].sentToPython else { continue }

            let pending = pendingQueue[i]
            let message = TranscriptionMessage(
                type: .transcription,
                raw: pending.raw,
                appBundleID: pending.appBundleID,
                timestamp: ISO8601DateFormatter().string(from: Date())
            )
            if ipcServer.send(message: message) {
                pendingQueue[i].sentToPython = true
                log(.info, "Retry: transcription sent to Python (\(pending.raw.count) chars)")
            }
        }

        stopRetryTimerIfIdle()
    }

    private func stopRetryTimerIfIdle() {
        guard !pendingQueue.contains(where: { !$0.sentToPython }) else { return }
        retryTimer?.cancel()
        retryTimer = nil
    }

    // MARK: - IPC Message Handling

    private func handleIPCMessage(_ data: Data) {
        struct MessageEnvelope: Decodable {
            let type: IPCMessageType
        }

        let decoder = JSONDecoder()
        do {
            let envelope = try decoder.decode(MessageEnvelope.self, from: data)
            switch envelope.type {
            case .inject:
                let message = try decoder.decode(InjectMessage.self, from: data)
                handleInjectMessage(message)
            case .transcription, .correction:
                log(.warning, "IPC received unexpected message type '\(envelope.type.rawValue)' from client")
            }
        } catch {
            log(.warning, "IPC failed to decode message — skipping")
        }
    }

    private func handleInjectMessage(_ message: InjectMessage) {
        log(.info, "IPC received inject (\(message.text.count) chars)")

        // Match to oldest pending transcription that was sent to Python (FIFO)
        guard let index = pendingQueue.firstIndex(where: { $0.sentToPython }) else {
            log(.warning, "Inject received with no pending transcription — injecting with unknown context")
            _ = textInjector.inject(text: message.text, appBundleID: "")
            return
        }

        let pending = pendingQueue.remove(at: index)

        // Cancel the 10s fallback timer for this dictation
        fallbackTimers[pending.id]?.cancel()
        fallbackTimers.removeValue(forKey: pending.id)

        injectAndObserve(text: message.text, appBundleID: pending.appBundleID, windowTitle: pending.windowTitle)
    }

    // MARK: - Text Injection + Correction Observation

    private func injectAndObserve(text: String, appBundleID: String, windowTitle: String) {
        guard let context = textInjector.inject(text: text, appBundleID: appBundleID) else {
            return
        }

        // Start correction observation if AX element is available
        guard let axElement = context.axElementRef else {
            log(.info, "No AX element — correction observation skipped")
            return
        }

        correctionObserver.startObserving(
            injectedText: text,
            appBundleID: appBundleID,
            axElement: axElement,
            correctionWindowExpiry: context.correctionWindowExpiry,
            windowTitle: windowTitle
        )
    }

    // MARK: - Correction → IPC

    private func handleCorrection(_ event: CorrectionEvent) {
        let message = CorrectionMessage(
            type: .correction,
            injected: event.injected,
            corrected: event.corrected,
            appBundleID: event.appBundleID
        )
        if ipcServer.send(message: message) {
            log(.info, "Correction sent to Python")
        } else {
            log(.warning, "Failed to send correction — Python not connected")
        }
    }
}

// MARK: - App Context Helper

/// Get the frontmost application's bundle ID and focused window title.
/// Returns empty strings on non-macOS platforms.
func getFrontmostAppInfo() -> (bundleID: String, windowTitle: String) {
    #if canImport(AppKit)
    guard let frontApp = NSWorkspace.shared.frontmostApplication else {
        return ("", "")
    }
    let bundleID = frontApp.bundleIdentifier ?? ""

    var windowTitle = ""
    #if canImport(ApplicationServices)
    let appElement = AXUIElementCreateApplication(frontApp.processIdentifier)
    var windowValue: AnyObject?
    if AXUIElementCopyAttributeValue(appElement, kAXFocusedWindowAttribute as CFString, &windowValue) == .success {
        // swiftlint:disable:next force_cast
        let windowElement = windowValue as! AXUIElement
        var titleValue: AnyObject?
        if AXUIElementCopyAttributeValue(windowElement, kAXTitleAttribute as CFString, &titleValue) == .success,
           let title = titleValue as? String {
            windowTitle = title
        }
    }
    #endif

    return (bundleID, windowTitle)
    #else
    return ("", "")
    #endif
}

// MARK: - Logging (content-free)

enum LogLevel: String {
    case info = "INFO"
    case warning = "WARN"
    case error = "ERROR"
}

func log(_ level: LogLevel, _ message: String) {
    let timestamp = ISO8601DateFormatter().string(from: Date())
    print("[\(timestamp)] [\(level.rawValue)] \(message)")
}

// MARK: - Entry Point

main()
