import XCTest
@testable import CorrectionObserver

final class LevenshteinTests: XCTestCase {

    func testIdenticalStrings() {
        XCTAssertEqual(levenshteinDistance("hello", "hello"), 0)
    }

    func testEmptyStrings() {
        XCTAssertEqual(levenshteinDistance("", ""), 0)
    }

    func testOneEmpty() {
        XCTAssertEqual(levenshteinDistance("", "abc"), 3)
        XCTAssertEqual(levenshteinDistance("abc", ""), 3)
    }

    func testSingleCharChange() {
        XCTAssertEqual(levenshteinDistance("cat", "hat"), 1)
    }

    func testInsertion() {
        XCTAssertEqual(levenshteinDistance("cat", "cats"), 1)
    }

    func testDeletion() {
        XCTAssertEqual(levenshteinDistance("cats", "cat"), 1)
    }

    func testKnownPairs() {
        XCTAssertEqual(levenshteinDistance("kitten", "sitting"), 3)
        XCTAssertEqual(levenshteinDistance("saturday", "sunday"), 3)
    }

    func testCompletelyDifferent() {
        XCTAssertEqual(levenshteinDistance("abc", "xyz"), 3)
    }

    func testRatioIdentical() {
        XCTAssertEqual(levenshteinRatio("hello", "hello"), 0.0, accuracy: 0.001)
    }

    func testRatioBothEmpty() {
        XCTAssertEqual(levenshteinRatio("", ""), 0.0)
    }

    func testRatioCompleteDifference() {
        // "abc" vs "xyz": distance=3, max_len=3, ratio=1.0
        XCTAssertEqual(levenshteinRatio("abc", "xyz"), 1.0, accuracy: 0.001)
    }

    func testRatioPartialEdit() {
        // "hello world" (11 chars) vs "hello wordl" (11 chars): distance=2
        // ratio = 2/11 ≈ 0.182
        XCTAssertEqual(levenshteinRatio("hello world", "hello wordl"), 2.0 / 11.0, accuracy: 0.001)
    }
}

final class ObservationSessionTests: XCTestCase {

    func testSessionCreation() {
        let element = NSObject()
        let now = Date()
        let expiry = now.addingTimeInterval(30)

        let session = ObservationSession(
            injectedText: "test input",
            injectionTimestamp: now,
            appBundleID: "com.apple.TextEdit",
            axElement: element,
            strategy: .native,
            latestValue: nil,
            windowExpiry: expiry
        )

        XCTAssertEqual(session.injectedText, "test input")
        XCTAssertEqual(session.appBundleID, "com.apple.TextEdit")
        XCTAssertEqual(session.strategy, .native)
        XCTAssertNil(session.latestValue)
        XCTAssertEqual(session.windowExpiry, expiry)
    }
}

final class CorrectionObserverConfigTests: XCTestCase {

    func testDefaultConfig() {
        let observer = CorrectionObserver()
        XCTAssertEqual(observer.correctionWindowSeconds, 30)
        XCTAssertEqual(observer.debounceSeconds, 2)
        XCTAssertEqual(observer.minEditRatio, 0.05, accuracy: 0.001)
        XCTAssertEqual(observer.maxEditRatio, 0.80, accuracy: 0.001)
    }

    func testCustomConfig() {
        let observer = CorrectionObserver(
            correctionWindowSeconds: 15,
            debounceSeconds: 1,
            minEditRatio: 0.10,
            maxEditRatio: 0.50
        )
        XCTAssertEqual(observer.correctionWindowSeconds, 15)
        XCTAssertEqual(observer.debounceSeconds, 1)
        XCTAssertEqual(observer.minEditRatio, 0.10, accuracy: 0.001)
        XCTAssertEqual(observer.maxEditRatio, 0.50, accuracy: 0.001)
    }

    func testNoActiveSessionInitially() {
        let observer = CorrectionObserver()
        XCTAssertNil(observer.activeSession)
    }
}

final class CorrectionObserverBehaviorTests: XCTestCase {

    func testStartObservingCreatesSession() {
        let observer = CorrectionObserver(debounceSeconds: 0.1)
        let element = NSObject()
        let expiry = Date().addingTimeInterval(30)

        observer.startObserving(
            injectedText: "hello wrold",
            appBundleID: "com.apple.TextEdit",
            axElement: element,
            correctionWindowExpiry: expiry
        )

        XCTAssertNotNil(observer.activeSession)
        XCTAssertEqual(observer.activeSession?.injectedText, "hello wrold")
        XCTAssertEqual(observer.activeSession?.appBundleID, "com.apple.TextEdit")
        XCTAssertEqual(observer.activeSession?.strategy, .native)
    }

    func testStopObservingClearsSession() {
        let observer = CorrectionObserver(debounceSeconds: 0.1)
        let element = NSObject()
        let expiry = Date().addingTimeInterval(30)

        observer.startObserving(
            injectedText: "test",
            appBundleID: "com.apple.TextEdit",
            axElement: element,
            correctionWindowExpiry: expiry
        )

        XCTAssertNotNil(observer.activeSession)
        observer.stopObserving()
        XCTAssertNil(observer.activeSession)
    }

    func testNewObservationCancelsPrevious() {
        let observer = CorrectionObserver(debounceSeconds: 0.1)
        let element = NSObject()
        let expiry = Date().addingTimeInterval(30)

        observer.startObserving(
            injectedText: "first",
            appBundleID: "com.apple.TextEdit",
            axElement: element,
            correctionWindowExpiry: expiry
        )
        observer.startObserving(
            injectedText: "second",
            appBundleID: "com.apple.Notes",
            axElement: element,
            correctionWindowExpiry: expiry
        )

        XCTAssertEqual(observer.activeSession?.injectedText, "second")
        XCTAssertEqual(observer.activeSession?.appBundleID, "com.apple.Notes")
    }

    func testHandleValueUpdateSetsLatestValue() {
        let observer = CorrectionObserver(debounceSeconds: 10)
        let element = NSObject()
        let expiry = Date().addingTimeInterval(30)

        observer.startObserving(
            injectedText: "test",
            appBundleID: "com.apple.TextEdit",
            axElement: element,
            correctionWindowExpiry: expiry
        )

        observer.handleValueUpdate("test updated")
        XCTAssertEqual(observer.activeSession?.latestValue, "test updated")
    }

    func testCorrectionCapturedForValidEdit() {
        let expectation = XCTestExpectation(description: "Correction captured")
        // Use short debounce for test speed
        let observer = CorrectionObserver(
            correctionWindowSeconds: 10,
            debounceSeconds: 0.1,
            minEditRatio: 0.05,
            maxEditRatio: 0.80
        )
        let element = NSObject()
        let expiry = Date().addingTimeInterval(10)

        var capturedEvent: CorrectionEvent?
        observer.onCorrectionCaptured = { event in
            capturedEvent = event
            expectation.fulfill()
        }

        observer.startObserving(
            injectedText: "hello wrold",
            appBundleID: "com.apple.TextEdit",
            axElement: element,
            correctionWindowExpiry: expiry
        )

        // Simulate user correcting "wrold" to "world"
        observer.handleValueUpdate("hello world")

        // Wait for debounce to fire
        wait(for: [expectation], timeout: 2.0)

        XCTAssertNotNil(capturedEvent)
        XCTAssertEqual(capturedEvent?.injected, "hello wrold")
        XCTAssertEqual(capturedEvent?.corrected, "hello world")
        XCTAssertEqual(capturedEvent?.appBundleID, "com.apple.TextEdit")

        // Session should be cleared after correction capture
        XCTAssertNil(observer.activeSession)
    }

    func testEditDistanceBelowMinimumDiscarded() {
        let expectation = XCTestExpectation(description: "Should not fire")
        expectation.isInverted = true
        let observer = CorrectionObserver(
            correctionWindowSeconds: 10,
            debounceSeconds: 0.1,
            minEditRatio: 0.05,
            maxEditRatio: 0.80
        )
        let element = NSObject()
        let expiry = Date().addingTimeInterval(10)

        observer.onCorrectionCaptured = { _ in
            expectation.fulfill()
        }

        observer.startObserving(
            injectedText: "hello world this is a long sentence for ratio",
            appBundleID: "com.apple.TextEdit",
            axElement: element,
            correctionWindowExpiry: expiry
        )

        // Change a single char in a long string → ratio < 0.05 (noise)
        observer.handleValueUpdate("Hello world this is a long sentence for ratio")

        // Wait to confirm callback does NOT fire
        wait(for: [expectation], timeout: 0.5)

        // Session should still be active (not cleared on discard)
        XCTAssertNotNil(observer.activeSession)
    }

    func testEditDistanceAboveMaximumDiscarded() {
        let expectation = XCTestExpectation(description: "Should not fire")
        expectation.isInverted = true
        let observer = CorrectionObserver(
            correctionWindowSeconds: 10,
            debounceSeconds: 0.1,
            minEditRatio: 0.05,
            maxEditRatio: 0.80
        )
        let element = NSObject()
        let expiry = Date().addingTimeInterval(10)

        observer.onCorrectionCaptured = { _ in
            expectation.fulfill()
        }

        observer.startObserving(
            injectedText: "hello",
            appBundleID: "com.apple.TextEdit",
            axElement: element,
            correctionWindowExpiry: expiry
        )

        // Complete rewrite — ratio > 0.80
        observer.handleValueUpdate("completely different text entirely")

        wait(for: [expectation], timeout: 0.5)
        XCTAssertNotNil(observer.activeSession)
    }

    func testDebounceResetsOnNewChange() {
        let expectation = XCTestExpectation(description: "Correction captured")
        let observer = CorrectionObserver(
            correctionWindowSeconds: 10,
            debounceSeconds: 0.3,
            minEditRatio: 0.05,
            maxEditRatio: 0.80
        )
        let element = NSObject()
        let expiry = Date().addingTimeInterval(10)

        var capturedEvent: CorrectionEvent?
        observer.onCorrectionCaptured = { event in
            capturedEvent = event
            expectation.fulfill()
        }

        observer.startObserving(
            injectedText: "hello wrold",
            appBundleID: "com.apple.TextEdit",
            axElement: element,
            correctionWindowExpiry: expiry
        )

        // First change — starts debounce
        observer.handleValueUpdate("hello worl")

        // After 0.1s, another change — should reset debounce
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) {
            observer.handleValueUpdate("hello world")
        }

        // Wait for debounce to fire after final change
        wait(for: [expectation], timeout: 2.0)

        // Should capture the FINAL value, not the intermediate one
        XCTAssertEqual(capturedEvent?.corrected, "hello world")
    }

    func testWindowExpiryRemovesObserver() {
        let expectation = XCTestExpectation(description: "Should not fire")
        expectation.isInverted = true
        // Very short window so it expires quickly
        let observer = CorrectionObserver(
            correctionWindowSeconds: 0.2,
            debounceSeconds: 0.1,
            minEditRatio: 0.05,
            maxEditRatio: 0.80
        )
        let element = NSObject()
        let expiry = Date().addingTimeInterval(0.2)

        observer.onCorrectionCaptured = { _ in
            expectation.fulfill()
        }

        observer.startObserving(
            injectedText: "hello wrold",
            appBundleID: "com.apple.TextEdit",
            axElement: element,
            correctionWindowExpiry: expiry
        )

        // Simulate edit after window has expired
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) {
            observer.handleValueUpdate("hello world")
        }

        wait(for: [expectation], timeout: 1.0)

        // Session should be cleared after window expiry
        XCTAssertNil(observer.activeSession)
    }
}

// MARK: - Blocklist Tests

final class BlocklistTests: XCTestCase {

    func testBlocklistBundleIDMatch() {
        let observer = CorrectionObserver(
            blocklistBundleIDs: ["com.1password.1password", "com.bitwarden.desktop"],
            blocklistTitlePatterns: []
        )
        XCTAssertFalse(observer.shouldObserve(appBundleID: "com.1password.1password", windowTitle: ""))
        XCTAssertFalse(observer.shouldObserve(appBundleID: "com.bitwarden.desktop", windowTitle: ""))
    }

    func testBlocklistTitlePatternMatch() {
        let observer = CorrectionObserver(
            blocklistBundleIDs: [],
            blocklistTitlePatterns: ["password", "secret"]
        )
        // Case-insensitive matching
        XCTAssertFalse(observer.shouldObserve(appBundleID: "com.example.app", windowTitle: "Enter Password"))
        XCTAssertFalse(observer.shouldObserve(appBundleID: "com.example.app", windowTitle: "PASSWORD FIELD"))
        XCTAssertFalse(observer.shouldObserve(appBundleID: "com.example.app", windowTitle: "My Secret Notes"))
    }

    func testBlocklistAllowsNormalApps() {
        let observer = CorrectionObserver(
            blocklistBundleIDs: ["com.1password.1password"],
            blocklistTitlePatterns: ["password"]
        )
        XCTAssertTrue(observer.shouldObserve(appBundleID: "com.apple.TextEdit", windowTitle: "Untitled"))
        XCTAssertTrue(observer.shouldObserve(appBundleID: "com.apple.Notes", windowTitle: "My Notes"))
    }

    func testBlocklistPreventsSessionCreation() {
        let observer = CorrectionObserver(
            blocklistBundleIDs: ["com.1password.1password"],
            blocklistTitlePatterns: []
        )
        let element = NSObject()
        let expiry = Date().addingTimeInterval(30)

        observer.startObserving(
            injectedText: "test",
            appBundleID: "com.1password.1password",
            axElement: element,
            correctionWindowExpiry: expiry,
            windowTitle: ""
        )

        // No session should be created for blocklisted app
        XCTAssertNil(observer.activeSession)
    }

    func testBlocklistTitlePreventsSessionCreation() {
        let observer = CorrectionObserver(
            blocklistBundleIDs: [],
            blocklistTitlePatterns: ["password"]
        )
        let element = NSObject()
        let expiry = Date().addingTimeInterval(30)

        observer.startObserving(
            injectedText: "test",
            appBundleID: "com.example.app",
            axElement: element,
            correctionWindowExpiry: expiry,
            windowTitle: "Change Password"
        )

        XCTAssertNil(observer.activeSession)
    }

    func testBlocklistLogsWhenSkipped() {
        let observer = CorrectionObserver(
            blocklistBundleIDs: ["com.1password.1password"],
            blocklistTitlePatterns: []
        )
        let element = NSObject()
        let expiry = Date().addingTimeInterval(30)

        var logMessages: [String] = []
        observer.logHandler = { logMessages.append($0) }

        observer.startObserving(
            injectedText: "test",
            appBundleID: "com.1password.1password",
            axElement: element,
            correctionWindowExpiry: expiry,
            windowTitle: ""
        )

        XCTAssertTrue(logMessages.contains { $0.contains("blocklisted") })
    }
}

// MARK: - Browser Detection Tests

final class BrowserDetectionTests: XCTestCase {

    func testChromeDetected() {
        XCTAssertEqual(
            CorrectionObserver.selectStrategy(appBundleID: "com.google.Chrome"),
            .browserHybrid
        )
    }

    func testSafariDetected() {
        XCTAssertEqual(
            CorrectionObserver.selectStrategy(appBundleID: "com.apple.Safari"),
            .browserHybrid
        )
    }

    func testArcDetected() {
        XCTAssertEqual(
            CorrectionObserver.selectStrategy(appBundleID: "company.thebrowser.Browser"),
            .browserHybrid
        )
    }

    func testFirefoxDetected() {
        XCTAssertEqual(
            CorrectionObserver.selectStrategy(appBundleID: "org.mozilla.firefox"),
            .browserHybrid
        )
    }

    func testBraveDetected() {
        XCTAssertEqual(
            CorrectionObserver.selectStrategy(appBundleID: "com.brave.Browser"),
            .browserHybrid
        )
    }

    func testEdgeDetected() {
        XCTAssertEqual(
            CorrectionObserver.selectStrategy(appBundleID: "com.microsoft.edgemac"),
            .browserHybrid
        )
    }

    func testNonBrowserUsesNativeStrategy() {
        XCTAssertEqual(
            CorrectionObserver.selectStrategy(appBundleID: "com.apple.TextEdit"),
            .native
        )
        XCTAssertEqual(
            CorrectionObserver.selectStrategy(appBundleID: "com.apple.Notes"),
            .native
        )
    }

    func testKnownBrowserBundleIDsCount() {
        // Ensure all 6 browsers are in the set
        XCTAssertEqual(CorrectionObserver.knownBrowserBundleIDs.count, 6)
    }

    func testBrowserStrategyInSession() {
        let observer = CorrectionObserver()
        let element = NSObject()
        let expiry = Date().addingTimeInterval(30)

        observer.startObserving(
            injectedText: "test",
            appBundleID: "com.google.Chrome",
            axElement: element,
            correctionWindowExpiry: expiry
        )

        XCTAssertNotNil(observer.activeSession)
        XCTAssertEqual(observer.activeSession?.strategy, .browserHybrid)
    }

    func testNativeStrategyInSession() {
        let observer = CorrectionObserver()
        let element = NSObject()
        let expiry = Date().addingTimeInterval(30)

        observer.startObserving(
            injectedText: "test",
            appBundleID: "com.apple.TextEdit",
            axElement: element,
            correctionWindowExpiry: expiry
        )

        XCTAssertNotNil(observer.activeSession)
        XCTAssertEqual(observer.activeSession?.strategy, .native)
    }
}
