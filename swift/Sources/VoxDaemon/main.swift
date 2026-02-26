import Foundation
import CorrectionObserver
import HotkeyListener

// MARK: - Startup

func main() {
    // 1. Load configuration
    let config = loadConfig()

    // 2. Log startup (content-free — no user text in logs)
    log(.info, "VoxDaemon starting — pid=\(ProcessInfo.processInfo.processIdentifier)")

    // 3. Check Accessibility API permission
    checkAccessibilityPermission()

    // 4. Start IPC server on Unix domain socket
    let ipcServer = startIPCServer(socketPath: "/tmp/vox.sock")

    // 5. Register hotkey listener
    let hotkeyListener = registerHotkeyListener(hotkey: config.hotkey)

    // 6. Enter run loop
    log(.info, "VoxDaemon ready — entering run loop")
    RunLoop.current.run()
}

// MARK: - Configuration

struct VoxConfig {
    let hotkey: String
    let whisperModel: String
    let correctionWindowSeconds: Int
    let ollamaHost: String
    let ollamaPort: Int
    let blocklist: [String]
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
        ollamaHost: "127.0.0.1",
        ollamaPort: 11434,
        blocklist: []
    )
}

// MARK: - Accessibility

func checkAccessibilityPermission() {
    // AXIsProcessTrusted() requires macOS Accessibility API
    // If not trusted, show one-time notification with instructions
    log(.info, "Checking Accessibility API permission")
    // TODO: Call AXIsProcessTrusted() and prompt if false
}

// MARK: - IPC Server (stub)

class IPCServerHandle {
    let socketPath: String
    init(socketPath: String) {
        self.socketPath = socketPath
    }
}

func startIPCServer(socketPath: String) -> IPCServerHandle {
    log(.info, "Starting IPC server at \(socketPath)")
    // TODO: Create Unix domain socket, listen for Python client
    return IPCServerHandle(socketPath: socketPath)
}

// MARK: - Hotkey Listener (stub)

class HotkeyListenerHandle {
    let hotkey: String
    init(hotkey: String) {
        self.hotkey = hotkey
    }
}

func registerHotkeyListener(hotkey: String) -> HotkeyListenerHandle {
    log(.info, "Registering hotkey listener for '\(hotkey)'")
    // TODO: Use NSEvent.addGlobalMonitorForEvents to detect hotkey
    return HotkeyListenerHandle(hotkey: hotkey)
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
