import XCTest
@testable import VoxCore

// MARK: - IPC Message Type Tests

final class IPCMessageTypeTests: XCTestCase {

    func testTranscriptionRawValue() {
        XCTAssertEqual(IPCMessageType.transcription.rawValue, "transcription")
    }

    func testInjectRawValue() {
        XCTAssertEqual(IPCMessageType.inject.rawValue, "inject")
    }

    func testCorrectionRawValue() {
        XCTAssertEqual(IPCMessageType.correction.rawValue, "correction")
    }

    func testMessageTypeRoundTrip() {
        let encoder = JSONEncoder()
        let decoder = JSONDecoder()

        for messageType in [IPCMessageType.transcription, .inject, .correction] {
            let data = try! encoder.encode(messageType)
            let decoded = try! decoder.decode(IPCMessageType.self, from: data)
            XCTAssertEqual(decoded, messageType)
        }
    }
}

// MARK: - TranscriptionMessage Serialization Tests

final class TranscriptionMessageTests: XCTestCase {

    func testRoundTripEncodeDecode() {
        let original = TranscriptionMessage(
            type: .transcription,
            raw: "hello world",
            appBundleID: "com.apple.TextEdit",
            timestamp: "2026-02-26T10:30:00Z"
        )

        let encoder = JSONEncoder()
        let decoder = JSONDecoder()

        let data = try! encoder.encode(original)
        let decoded = try! decoder.decode(TranscriptionMessage.self, from: data)

        XCTAssertEqual(decoded, original)
        XCTAssertEqual(decoded.type, .transcription)
        XCTAssertEqual(decoded.raw, "hello world")
        XCTAssertEqual(decoded.appBundleID, "com.apple.TextEdit")
        XCTAssertEqual(decoded.timestamp, "2026-02-26T10:30:00Z")
    }

    func testSnakeCaseAppBundleIDKey() {
        let message = TranscriptionMessage(
            type: .transcription,
            raw: "test",
            appBundleID: "com.example.app",
            timestamp: "2026-01-01T00:00:00Z"
        )

        let data = try! JSONEncoder().encode(message)
        let json = try! JSONSerialization.jsonObject(with: data) as! [String: Any]

        // Verify the JSON key is snake_case for Python interop
        XCTAssertNotNil(json["app_bundle_id"])
        XCTAssertNil(json["appBundleID"])
        XCTAssertEqual(json["app_bundle_id"] as? String, "com.example.app")
    }

    func testDecodesFromPythonFormat() {
        // Simulate JSON as Python would send it
        let jsonString = """
        {"type":"transcription","raw":"the quick brown fox","app_bundle_id":"com.microsoft.VSCode","timestamp":"2026-02-26T10:30:00Z"}
        """
        let data = jsonString.data(using: .utf8)!

        let message = try! JSONDecoder().decode(TranscriptionMessage.self, from: data)
        XCTAssertEqual(message.type, .transcription)
        XCTAssertEqual(message.raw, "the quick brown fox")
        XCTAssertEqual(message.appBundleID, "com.microsoft.VSCode")
    }

    func testUnicodeContent() {
        let original = TranscriptionMessage(
            type: .transcription,
            raw: "caf\u{00E9} na\u{00EF}ve r\u{00E9}sum\u{00E9}",
            appBundleID: "com.apple.TextEdit",
            timestamp: "2026-02-26T10:30:00Z"
        )

        let data = try! JSONEncoder().encode(original)
        let decoded = try! JSONDecoder().decode(TranscriptionMessage.self, from: data)

        XCTAssertEqual(decoded, original)
    }
}

// MARK: - InjectMessage Serialization Tests

final class InjectMessageTests: XCTestCase {

    func testRoundTripEncodeDecode() {
        let original = InjectMessage(type: .inject, text: "cleaned text to type")

        let data = try! JSONEncoder().encode(original)
        let decoded = try! JSONDecoder().decode(InjectMessage.self, from: data)

        XCTAssertEqual(decoded, original)
        XCTAssertEqual(decoded.type, .inject)
        XCTAssertEqual(decoded.text, "cleaned text to type")
    }

    func testDecodesFromPythonFormat() {
        let jsonString = """
        {"type":"inject","text":"the cleaned text to type"}
        """
        let data = jsonString.data(using: .utf8)!

        let message = try! JSONDecoder().decode(InjectMessage.self, from: data)
        XCTAssertEqual(message.type, .inject)
        XCTAssertEqual(message.text, "the cleaned text to type")
    }

    func testEmptyText() {
        let original = InjectMessage(type: .inject, text: "")

        let data = try! JSONEncoder().encode(original)
        let decoded = try! JSONDecoder().decode(InjectMessage.self, from: data)

        XCTAssertEqual(decoded.text, "")
    }
}

// MARK: - CorrectionMessage Serialization Tests

final class CorrectionMessageTests: XCTestCase {

    func testRoundTripEncodeDecode() {
        let original = CorrectionMessage(
            type: .correction,
            injected: "hello wrold",
            corrected: "hello world",
            appBundleID: "com.apple.TextEdit"
        )

        let data = try! JSONEncoder().encode(original)
        let decoded = try! JSONDecoder().decode(CorrectionMessage.self, from: data)

        XCTAssertEqual(decoded, original)
        XCTAssertEqual(decoded.type, .correction)
        XCTAssertEqual(decoded.injected, "hello wrold")
        XCTAssertEqual(decoded.corrected, "hello world")
        XCTAssertEqual(decoded.appBundleID, "com.apple.TextEdit")
    }

    func testSnakeCaseAppBundleIDKey() {
        let message = CorrectionMessage(
            type: .correction,
            injected: "test",
            corrected: "tested",
            appBundleID: "com.example.app"
        )

        let data = try! JSONEncoder().encode(message)
        let json = try! JSONSerialization.jsonObject(with: data) as! [String: Any]

        XCTAssertNotNil(json["app_bundle_id"])
        XCTAssertNil(json["appBundleID"])
    }

    func testDecodesFromPythonFormat() {
        let jsonString = """
        {"type":"correction","injected":"teh","corrected":"the","app_bundle_id":"com.apple.Notes"}
        """
        let data = jsonString.data(using: .utf8)!

        let message = try! JSONDecoder().decode(CorrectionMessage.self, from: data)
        XCTAssertEqual(message.type, .correction)
        XCTAssertEqual(message.injected, "teh")
        XCTAssertEqual(message.corrected, "the")
        XCTAssertEqual(message.appBundleID, "com.apple.Notes")
    }
}

// MARK: - Injection Context Queue Tests

final class InjectionContextQueueTests: XCTestCase {

    func testInjectAddsToQueue() {
        let injector = TextInjector(correctionWindowSeconds: 30)
        XCTAssertEqual(injector.contextQueue.count, 0)

        injector.inject(text: "hello", appBundleID: "com.apple.TextEdit")
        XCTAssertEqual(injector.contextQueue.count, 1)
        XCTAssertEqual(injector.contextQueue.first?.injectedText, "hello")
        XCTAssertEqual(injector.contextQueue.first?.appBundleID, "com.apple.TextEdit")
    }

    func testMultipleInjectsAppendToQueue() {
        let injector = TextInjector(correctionWindowSeconds: 30)

        injector.inject(text: "first", appBundleID: "com.apple.TextEdit")
        injector.inject(text: "second", appBundleID: "com.apple.Notes")
        injector.inject(text: "third", appBundleID: "com.microsoft.VSCode")

        XCTAssertEqual(injector.contextQueue.count, 3)
        XCTAssertEqual(injector.contextQueue[0].injectedText, "first")
        XCTAssertEqual(injector.contextQueue[1].injectedText, "second")
        XCTAssertEqual(injector.contextQueue[2].injectedText, "third")
    }

    func testEmptyTextNotAddedToQueue() {
        let injector = TextInjector(correctionWindowSeconds: 30)

        let result = injector.inject(text: "", appBundleID: "com.apple.TextEdit")

        XCTAssertNil(result)
        XCTAssertEqual(injector.contextQueue.count, 0)
    }

    func testContextHasCorrectCorrectionWindowExpiry() {
        let injector = TextInjector(correctionWindowSeconds: 30)
        let before = Date()

        injector.inject(text: "test", appBundleID: "com.apple.TextEdit")

        let after = Date()
        let context = injector.contextQueue.first!

        // Expiry should be ~30 seconds from now
        XCTAssertGreaterThanOrEqual(context.correctionWindowExpiry, before.addingTimeInterval(30))
        XCTAssertLessThanOrEqual(context.correctionWindowExpiry, after.addingTimeInterval(30))
    }

    func testPurgeExpiredRemovesOldContexts() {
        let injector = TextInjector(correctionWindowSeconds: 30)

        // Manually add an already-expired context
        let expiredContext = InjectionContext(
            injectedText: "expired",
            timestamp: Date().addingTimeInterval(-60),
            axElementRef: nil,
            appBundleID: "com.apple.TextEdit",
            correctionWindowExpiry: Date().addingTimeInterval(-30)
        )

        // Add a valid (non-expired) context via inject
        injector.inject(text: "valid", appBundleID: "com.apple.Notes")

        // Manually insert the expired context at the beginning
        // (We need to work around private(set) by using inject for valid ones)
        // Instead, create injector, inject expired one first by using a very short window
        let shortInjector = TextInjector(correctionWindowSeconds: 0)
        shortInjector.inject(text: "will expire", appBundleID: "com.apple.TextEdit")

        // Wait a tiny bit so the 0-second window expires
        Thread.sleep(forTimeInterval: 0.01)

        // Add a non-expired context
        let longInjector = TextInjector(correctionWindowSeconds: 30)
        longInjector.inject(text: "first", appBundleID: "com.apple.TextEdit")

        // The short injector should have its context expire on purge
        shortInjector.purgeExpired()
        XCTAssertEqual(shortInjector.contextQueue.count, 0)

        // The long injector should retain its context
        longInjector.purgeExpired()
        XCTAssertEqual(longInjector.contextQueue.count, 1)
    }

    func testPurgeExpiredKeepsValidContexts() {
        let injector = TextInjector(correctionWindowSeconds: 30)

        injector.inject(text: "still valid", appBundleID: "com.apple.TextEdit")
        injector.inject(text: "also valid", appBundleID: "com.apple.Notes")

        injector.purgeExpired()

        XCTAssertEqual(injector.contextQueue.count, 2)
    }

    func testInjectPurgesExpiredBeforeAdding() {
        // Use a very short window so contexts expire quickly
        let injector = TextInjector(correctionWindowSeconds: 0)

        injector.inject(text: "will expire", appBundleID: "com.apple.TextEdit")
        XCTAssertEqual(injector.contextQueue.count, 1)

        // Wait for the context to expire
        Thread.sleep(forTimeInterval: 0.01)

        // Next inject should purge the expired context before adding
        injector.inject(text: "fresh", appBundleID: "com.apple.Notes")

        // Should have only the fresh context (expired one was purged)
        XCTAssertEqual(injector.contextQueue.count, 1)
        XCTAssertEqual(injector.contextQueue.first?.injectedText, "fresh")
    }

    func testQueueCountReflectsActiveContexts() {
        let injector = TextInjector(correctionWindowSeconds: 30)

        XCTAssertEqual(injector.contextQueue.count, 0)

        injector.inject(text: "one", appBundleID: "com.apple.TextEdit")
        XCTAssertEqual(injector.contextQueue.count, 1)

        injector.inject(text: "two", appBundleID: "com.apple.Notes")
        XCTAssertEqual(injector.contextQueue.count, 2)

        injector.inject(text: "three", appBundleID: "com.microsoft.VSCode")
        XCTAssertEqual(injector.contextQueue.count, 3)
    }

    func testQueueIsFIFOOrder() {
        let injector = TextInjector(correctionWindowSeconds: 30)

        injector.inject(text: "first", appBundleID: "app1")
        injector.inject(text: "second", appBundleID: "app2")
        injector.inject(text: "third", appBundleID: "app3")

        // Verify FIFO order
        XCTAssertEqual(injector.contextQueue[0].injectedText, "first")
        XCTAssertEqual(injector.contextQueue[1].injectedText, "second")
        XCTAssertEqual(injector.contextQueue[2].injectedText, "third")

        XCTAssertEqual(injector.contextQueue[0].appBundleID, "app1")
        XCTAssertEqual(injector.contextQueue[1].appBundleID, "app2")
        XCTAssertEqual(injector.contextQueue[2].appBundleID, "app3")
    }

    func testCorrectionWindowSecondsConfigurable() {
        let injector15 = TextInjector(correctionWindowSeconds: 15)
        XCTAssertEqual(injector15.correctionWindowSeconds, 15)

        let injector60 = TextInjector(correctionWindowSeconds: 60)
        XCTAssertEqual(injector60.correctionWindowSeconds, 60)

        let injectorDefault = TextInjector()
        XCTAssertEqual(injectorDefault.correctionWindowSeconds, 30)
    }
}
