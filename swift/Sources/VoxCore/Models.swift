import Foundation

// MARK: - IPC Message Types

/// Message types for the JSON-over-newline IPC protocol between Swift and Python.
public enum IPCMessageType: String, Codable {
    case transcription  // Swift → Python: raw whisper transcript + context
    case inject         // Python → Swift: cleaned text to type
    case correction     // Swift → Python: observed user correction
}

// MARK: - IPC Messages

/// Swift → Python: raw transcript from whisper.cpp with app context.
public struct TranscriptionMessage: Codable, Equatable {
    public let type: IPCMessageType
    public let raw: String
    public let appBundleID: String
    public let timestamp: String

    public init(type: IPCMessageType, raw: String, appBundleID: String, timestamp: String) {
        self.type = type
        self.raw = raw
        self.appBundleID = appBundleID
        self.timestamp = timestamp
    }

    enum CodingKeys: String, CodingKey {
        case type
        case raw
        case appBundleID = "app_bundle_id"
        case timestamp
    }
}

/// Python → Swift: cleaned text to inject at cursor.
public struct InjectMessage: Codable, Equatable {
    public let type: IPCMessageType
    public let text: String

    public init(type: IPCMessageType, text: String) {
        self.type = type
        self.text = text
    }
}

/// Swift → Python: observed correction after user edits injected text.
public struct CorrectionMessage: Codable, Equatable {
    public let type: IPCMessageType
    public let injected: String
    public let corrected: String
    public let appBundleID: String

    public init(type: IPCMessageType, injected: String, corrected: String, appBundleID: String) {
        self.type = type
        self.injected = injected
        self.corrected = corrected
        self.appBundleID = appBundleID
    }

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
public struct InjectionContext {
    public let injectedText: String
    public let timestamp: Date
    public let axElementRef: AnyObject?  // AXUIElement on macOS
    public let appBundleID: String
    public let correctionWindowExpiry: Date  // timestamp + correction_window_seconds

    public init(injectedText: String, timestamp: Date, axElementRef: AnyObject?, appBundleID: String, correctionWindowExpiry: Date) {
        self.injectedText = injectedText
        self.timestamp = timestamp
        self.axElementRef = axElementRef
        self.appBundleID = appBundleID
        self.correctionWindowExpiry = correctionWindowExpiry
    }
}
