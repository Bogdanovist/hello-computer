import Foundation

// MARK: - IPC Message Types

/// Message types for the JSON-over-newline IPC protocol between Swift and Python.
enum IPCMessageType: String, Codable {
    case transcription  // Swift → Python: raw whisper transcript + context
    case inject         // Python → Swift: cleaned text to type
    case correction     // Swift → Python: observed user correction
}

// MARK: - IPC Messages

/// Swift → Python: raw transcript from whisper.cpp with app context.
struct TranscriptionMessage: Codable {
    let type: IPCMessageType
    let raw: String
    let appBundleID: String
    let timestamp: String

    enum CodingKeys: String, CodingKey {
        case type
        case raw
        case appBundleID = "app_bundle_id"
        case timestamp
    }
}

/// Python → Swift: cleaned text to inject at cursor.
struct InjectMessage: Codable {
    let type: IPCMessageType
    let text: String
}

/// Swift → Python: observed correction after user edits injected text.
struct CorrectionMessage: Codable {
    let type: IPCMessageType
    let injected: String
    let corrected: String
    let appBundleID: String

    enum CodingKeys: String, CodingKey {
        case type
        case injected
        case corrected
        case appBundleID = "app_bundle_id"
    }
}

// MARK: - Injection Context

/// Tracks an injected text segment for correction observation.
/// Maintained in a FIFO queue; expired entries removed on each new injection.
struct InjectionContext {
    let injectedText: String
    let timestamp: Date
    let axElementRef: AnyObject?  // AXUIElement on macOS
    let appBundleID: String
    let correctionWindowExpiry: Date  // timestamp + correction_window_seconds
}
