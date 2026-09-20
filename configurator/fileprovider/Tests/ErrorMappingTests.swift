import XCTest
import FileProvider

/// Task 14: every `DuoFPErrorDomain` code (`Shared/DuoFPErrors.h`) maps to a
/// SPECIFIC Foundation/`NSFileProviderError`, per spec §16's table - no
/// generic/uninterpreted `NSError` for a case this table defines. Two halves:
///
/// - `ErrorMap.toNSFileProviderError` (`FetchController.swift`) - the fetch
///   path, exercised both directly (unit) and end-to-end through a real
///   `FetchController.fetch` that fails mid-stream with a host-delivered
///   `DuoFPErrorDomain` error (real behavior, not a mocked internal).
/// - `DuoExtensionControlService.mapError`'s `.io -> DuoFPErrorDiskFull(6)`
///   fix (ruling #3b) - exercised through a REAL `ReplicaStore` durable-write
///   failure (an unwritable generations directory), not a mocked
///   `ReplicaStoreError`.
///
/// Ruling #2's hard correctness point - host-down/peerLost/timeout must be
/// RETRIABLE and must NEVER look like an item deletion to Finder - is tested
/// explicitly: all three map to `.serverUnreachable` (Apple's documented
/// transient/retry code) and explicitly NOT to `.noSuchItem` or
/// `.cannotSynchronize` (the codes that make Finder treat the item as gone).
final class ErrorMappingTests: XCTestCase {
    private var directory: URL!

    override func setUpWithError() throws {
        directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: directory)
    }

    private func duoFPError(_ code: Int) -> NSError {
        NSError(domain: DuoFPErrorDomain, code: code)
    }

    // MARK: - ErrorMap: the full spec §16 table, one case at a time

    func testSourceMissingMapsToNoSuchItem() {
        let mapped = ErrorMap.toNSFileProviderError(duoFPError(1))
        XCTAssertEqual(mapped.domain, NSFileProviderErrorDomain)
        XCTAssertEqual(mapped.code, NSFileProviderError.noSuchItem.rawValue)
    }

    func testSourceChangedMapsToCannotSynchronize() {
        let mapped = ErrorMap.toNSFileProviderError(duoFPError(2))
        XCTAssertEqual(mapped.domain, NSFileProviderErrorDomain)
        XCTAssertEqual(mapped.code, NSFileProviderError.cannotSynchronize.rawValue)
    }

    func testUnauthorizedMapsToNotAuthenticated() {
        let mapped = ErrorMap.toNSFileProviderError(duoFPError(4))
        XCTAssertEqual(mapped.domain, NSFileProviderErrorDomain)
        XCTAssertEqual(mapped.code, NSFileProviderError.notAuthenticated.rawValue)
    }

    func testDiskFullMapsToPosixENOSPC() {
        let mapped = ErrorMap.toNSFileProviderError(duoFPError(6))
        XCTAssertEqual(mapped.domain, NSPOSIXErrorDomain)
        XCTAssertEqual(mapped.code, Int(ENOSPC))
    }

    func testProtocolMapsToCannotSynchronize() {
        let mapped = ErrorMap.toNSFileProviderError(duoFPError(7))
        XCTAssertEqual(mapped.domain, NSFileProviderErrorDomain)
        XCTAssertEqual(mapped.code, NSFileProviderError.cannotSynchronize.rawValue)
    }

    func testUnrecognizedDuoFPCodeFallsBackToCannotSynchronizeNotAGenericError() {
        // Any future/unexpected code must still resolve to a SPECIFIC,
        // meaningful NSFileProviderError - never an unmapped pass-through.
        let mapped = ErrorMap.toNSFileProviderError(duoFPError(999))
        XCTAssertEqual(mapped.domain, NSFileProviderErrorDomain)
        XCTAssertEqual(mapped.code, NSFileProviderError.cannotSynchronize.rawValue)
    }

    func testNonDuoFPDomainErrorsPassThroughUnchanged() {
        // The literal NSUserCancelledError FetchOperation's
        // cancellationHandler builds - already specific, must not be re-wrapped.
        let cancelled = NSError(domain: NSCocoaErrorDomain, code: NSUserCancelledError)
        let mapped = ErrorMap.toNSFileProviderError(cancelled)
        XCTAssertEqual(mapped.domain, NSCocoaErrorDomain)
        XCTAssertEqual(mapped.code, NSUserCancelledError)
    }

    // MARK: - Ruling #2: host-down / peerLost / timeout are ALL retriable
    // serverUnreachable, and NEVER a deletion-triggering code.

    func testPeerLostTimeoutAndNotConnectedAllMapToRetriableServerUnreachable() {
        for (code, name) in [(3, "peerLost"), (5, "timeout"), (8, "notConnected")] {
            let mapped = ErrorMap.toNSFileProviderError(duoFPError(code))
            XCTAssertEqual(mapped.domain, NSFileProviderErrorDomain, name)
            XCTAssertEqual(mapped.code, NSFileProviderError.serverUnreachable.rawValue, name)
            // The hard correctness point: this must NEVER look like the item
            // itself is gone or unsyncable - Finder evicts/deletes for those.
            XCTAssertNotEqual(mapped.code, NSFileProviderError.noSuchItem.rawValue, name)
            XCTAssertNotEqual(mapped.code, NSFileProviderError.cannotSynchronize.rawValue, name)
        }
    }

    // MARK: - End-to-end through a real FetchController: host-down mid-fetch
    // never surfaces as a deletion-triggering error.

    private final class FailingHost: NSObject, DuoHostCallback {
        let failureCode: Int
        init(failureCode: Int) { self.failureCode = failureCode }
        var cancelled: [String] = []
        func openFetch(_ generationId: String, entryId: NSNumber, reply: @escaping (String?, NSNumber?, Error?) -> Void) {
            reply("token", 3, nil)
        }
        func pullChunk(_ fetchToken: String, reply: @escaping (Data?, Bool, Error?) -> Void) {
            reply(nil, false, NSError(domain: DuoFPErrorDomain, code: failureCode))
        }
        func cancelFetch(_ fetchToken: String) { cancelled.append(fetchToken) }
    }

    private func item() -> DuoItem {
        DuoItem(itemIdentifier: NSFileProviderItemIdentifier("generation:0"), parentItemIdentifier: .rootContainer,
                filename: "file.bin", contentType: .data, documentSize: 3, capabilities: [.allowsReading],
                contentVersion: Data(), metadataVersion: Data())
    }

    func testFetchFailingWithHostDownSurfacesRetriableServerUnreachableNotDeletion() throws {
        let host = FailingHost(failureCode: 8) // DuoFPErrorNotConnected
        // Retry off: this test pins the ERROR MAPPING (host-down -> retriable
        // serverUnreachable, not a deletion) + cleanup; the transient-retry
        // behavior has its own tests in FetchControllerTests.
        let controller = FetchController(hostProvider: { _ in host }, temporaryDirectory: directory,
                                         retry: .init(maxAttempts: 1))
        let done = expectation(description: "host-down fetch failed")
        _ = controller.fetch(item(), request: NSFileProviderRequest()) { url, item, error in
            XCTAssertNil(url)
            XCTAssertNil(item)
            let nsError = error as NSError?
            XCTAssertEqual(nsError?.domain, NSFileProviderErrorDomain)
            XCTAssertEqual(nsError?.code, NSFileProviderError.serverUnreachable.rawValue)
            XCTAssertNotEqual(nsError?.code, NSFileProviderError.noSuchItem.rawValue)
            done.fulfill()
        }
        wait(for: [done], timeout: 3)
        // The temp file is still cleaned up and the host proxy still told to
        // drop the fetch - retriable does not mean "leak the partial state".
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath: directory.path), [])
        XCTAssertEqual(host.cancelled, ["token"])
    }

    func testFetchFailingWithPeerLostSurfacesRetriableServerUnreachable() throws {
        let host = FailingHost(failureCode: 3) // DuoFPErrorPeerLost
        let controller = FetchController(hostProvider: { _ in host }, temporaryDirectory: directory,
                                         retry: .init(maxAttempts: 1))
        let done = expectation(description: "peer-lost fetch failed")
        _ = controller.fetch(item(), request: NSFileProviderRequest()) { _, _, error in
            let nsError = error as NSError?
            XCTAssertEqual(nsError?.domain, NSFileProviderErrorDomain)
            XCTAssertEqual(nsError?.code, NSFileProviderError.serverUnreachable.rawValue)
            done.fulfill()
        }
        wait(for: [done], timeout: 3)
    }

    // MARK: - DuoExtensionControlService.mapError: `.io` -> DuoFPErrorDiskFull(6)

    func testReplicaStoreIOFailureMapsToDiskFullNotProtocol() throws {
        let store = ReplicaStore(baseDirectory: directory.appendingPathComponent("replica"))
        // Force a REAL durable-write failure: make the generations directory
        // unwritable so durableWrite's createFile(atPath:) fails with
        // ReplicaStoreError.io - not a mocked/injected error.
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o500], ofItemAtPath: store.generationsDir.path
        )
        defer {
            try? FileManager.default.setAttributes(
                [.posixPermissions: 0o700], ofItemAtPath: store.generationsDir.path
            )
        }
        let service = DuoExtensionControlService(store: store)
        let record = try goldenFixtureData()

        var replyAck: Bool?
        var replyError: Error?
        service.publishGeneration(record) { ack, error in
            replyAck = ack
            replyError = error
        }

        XCTAssertEqual(replyAck, false)
        let nsError = try XCTUnwrap(replyError as NSError?)
        XCTAssertEqual(nsError.domain, DuoFPErrorDomain)
        XCTAssertEqual(nsError.code, 6) // DuoFPErrorDiskFull, NOT 7 (Protocol)
    }

    private func goldenFixtureData() throws -> Data {
        let url = try XCTUnwrap(
            Bundle(for: type(of: self)).url(forResource: "generation_record", withExtension: "json"),
            "golden fixture must be bundled as a test resource (see project.yml)"
        )
        return try Data(contentsOf: url)
    }
}
