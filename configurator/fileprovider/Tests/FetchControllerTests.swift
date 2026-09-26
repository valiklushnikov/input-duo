import XCTest
import FileProvider

private final class CannedHost: NSObject, DuoHostCallback {
    var chunks: [(Data?, Bool, Error?)] = [(Data("ab".utf8), false, nil), (Data("c".utf8), true, nil)]
    var cancelled = [String]()
    var totalSize: NSNumber = 3
    var pullCallCount = 0
    func openFetch(_ generationId: String, entryId: NSNumber, reply: @escaping (String?, NSNumber?, Error?) -> Void) {
        reply("token", totalSize, nil)
    }
    func pullChunk(_ fetchToken: String, reply: @escaping (Data?, Bool, Error?) -> Void) {
        pullCallCount += 1
        let next = chunks.removeFirst()
        reply(next.0, next.1, next.2)
    }
    func cancelFetch(_ fetchToken: String) { cancelled.append(fetchToken) }
}

/// Fails `openFetch` with `code` the first `failOpenTimes` calls, then serves a
/// normal 3-byte "abc" so a transient-error retry can succeed on a later attempt.
private final class FlakyHost: NSObject, DuoHostCallback {
    let failOpenTimes: Int
    let code: Int
    var totalSize: NSNumber = 3
    var chunks: [(Data?, Bool, Error?)] = [(Data("abc".utf8), true, nil)]
    var openCalls = 0
    var cancelled = [String]()
    init(failOpenTimes: Int, code: Int) { self.failOpenTimes = failOpenTimes; self.code = code }
    func openFetch(_ generationId: String, entryId: NSNumber, reply: @escaping (String?, NSNumber?, Error?) -> Void) {
        openCalls += 1
        if openCalls <= failOpenTimes {
            reply(nil, nil, NSError(domain: DuoFPErrorDomain, code: code))
        } else {
            reply("token", totalSize, nil)
        }
    }
    func pullChunk(_ fetchToken: String, reply: @escaping (Data?, Bool, Error?) -> Void) {
        let next = chunks.removeFirst()
        reply(next.0, next.1, next.2)
    }
    func cancelFetch(_ fetchToken: String) { cancelled.append(fetchToken) }
}

private final class DeferredOpenHost: NSObject, DuoHostCallback {
    private let lock = NSLock()
    private var replies: [(String?, NSNumber?, Error?) -> Void] = []
    var onFirstOpen: (() -> Void)?
    private(set) var openCalls = 0

    func openFetch(
        _ generationId: String,
        entryId: NSNumber,
        reply: @escaping (String?, NSNumber?, Error?) -> Void
    ) {
        lock.lock()
        openCalls += 1
        replies.append(reply)
        let first = openCalls == 1
        lock.unlock()
        if first { onFirstOpen?() }
    }

    func releaseAll() {
        lock.lock()
        let pending = replies
        replies.removeAll()
        lock.unlock()
        for (position, reply) in pending.enumerated() {
            reply("token-\(position)", 3, nil)
        }
    }

    func pullChunk(
        _ fetchToken: String,
        reply: @escaping (Data?, Bool, Error?) -> Void
    ) {
        reply(Data("abc".utf8), true, nil)
    }

    func cancelFetch(_ fetchToken: String) {}
}

final class FetchControllerTests: XCTestCase {
    /// Runs the scheduled retry immediately - deterministic, no real waiting.
    private let now: FetchController.Scheduler = { _, work in work() }
    private var directory: URL!
    override func setUpWithError() throws {
        directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    }
    override func tearDownWithError() throws { try FileManager.default.removeItem(at: directory) }
    private func item(size: NSNumber = 3) -> DuoItem {
        DuoItem(itemIdentifier: NSFileProviderItemIdentifier("generation:0"), parentItemIdentifier: .rootContainer,
                filename: "file.bin", contentType: .data, documentSize: size, capabilities: [.allowsReading],
                contentVersion: Data(), metadataVersion: Data())
    }

    private func trace() -> (PerfTrace, () -> [String]) {
        var tick: UInt64 = 100
        var lines: [String] = []
        let trace = PerfTrace(clock: { tick += 10; return tick }, emit: { lines.append($0) })
        return (trace, { lines })
    }

    private func eventNames(_ lines: [String]) -> [String] {
        lines.compactMap { line in
            line.split(separator: " ").first(where: { $0.hasPrefix("event=") })
                .map { String($0.dropFirst("event=".count)) }
        }
    }

    func testSingleChunkFetchRecordsOrderedStagesWithoutChangingBytes() throws {
        let host = CannedHost()
        host.chunks = [(Data("abc".utf8), true, nil)]
        let (perf, captured) = trace()
        let controller = FetchController(
            hostProvider: { _ in host }, temporaryDirectory: directory, perf: perf
        )
        let done = expectation(description: "traced fetch")
        var resultURL: URL?

        _ = controller.fetch(item(), request: NSFileProviderRequest()) { url, _, error in
            XCTAssertNil(error)
            resultURL = url
            done.fulfill()
        }
        wait(for: [done], timeout: 3)

        XCTAssertEqual(eventNames(captured()), [
            "fetch_enter", "open_fetch_call_begin", "open_fetch_reply",
            "pull_call_begin", "pull_reply", "chunk_write_complete", "first_write_complete",
            "last_write_complete", "fsync_complete", "close_complete",
            "finalize_complete", "completion_call"
        ])
        let writes = captured().filter {
            $0.contains("event=first_write_complete") || $0.contains("event=last_write_complete")
        }
        let writeStamps = writes.compactMap { line in
            line.split(separator: " ").first(where: { $0.hasPrefix("mono_ns=") })
        }
        XCTAssertEqual(Set(writeStamps).count, 1)
        XCTAssertEqual(try Data(contentsOf: XCTUnwrap(resultURL)), Data("abc".utf8))
    }

    func testSimultaneousFetchesForSameItemShareOneUnderlyingRead() {
        let host = DeferredOpenHost()
        let (perf, captured) = trace()
        let firstOpen = expectation(description: "first underlying open")
        host.onFirstOpen = { firstOpen.fulfill() }
        let controller = FetchController(
            hostProvider: { _ in host }, temporaryDirectory: directory, perf: perf
        )
        let completions = expectation(description: "both logical fetches complete")
        completions.expectedFulfillmentCount = 2

        _ = controller.fetch(
            item(), request: NSFileProviderRequest(),
            extraTraceFields: [("perf_fetch_id", "first"), ("origin", "BURST")]
        ) { _, _, error in
            XCTAssertNil(error)
            completions.fulfill()
        }
        _ = controller.fetch(
            item(), request: NSFileProviderRequest(),
            extraTraceFields: [("perf_fetch_id", "second"), ("origin", "FINDER")]
        ) { _, _, error in
            XCTAssertNil(error)
            completions.fulfill()
        }

        wait(for: [firstOpen], timeout: 3)
        Thread.sleep(forTimeInterval: 0.05)
        XCTAssertEqual(host.openCalls, 1)
        let coalesced = captured().filter { $0.contains("event=fetch_coalesced") }
        XCTAssertEqual(coalesced.count, 1)
        XCTAssertTrue(coalesced[0].contains("origin=FINDER"))
        XCTAssertTrue(coalesced[0].contains("perf_fetch_id=second"))
        host.releaseAll()
        wait(for: [completions], timeout: 3)
    }

    func testMultiChunkFetchMarksOnlyTheFirstAndLastWrites() throws {
        let host = CannedHost()
        let (perf, captured) = trace()
        let controller = FetchController(
            hostProvider: { _ in host }, temporaryDirectory: directory, perf: perf
        )
        let done = expectation(description: "multi chunk traced")

        _ = controller.fetch(item(), request: NSFileProviderRequest()) { _, _, error in
            XCTAssertNil(error)
            done.fulfill()
        }
        wait(for: [done], timeout: 3)

        let events = eventNames(captured())
        XCTAssertEqual(events.filter { $0 == "pull_reply" }.count, 2)
        XCTAssertEqual(events.filter { $0 == "first_write_complete" }.count, 1)
        XCTAssertEqual(events.filter { $0 == "last_write_complete" }.count, 1)
    }

    func testFetchWritesExactBytesAndCompletesWithOriginalItem() throws {
        let host = CannedHost()
        let controller = FetchController(hostProvider: { _ in host }, temporaryDirectory: directory)
        let original = item()
        let done = expectation(description: "completed once")
        done.assertForOverFulfill = true
        let progress = controller.fetch(original, request: NSFileProviderRequest()) { url, returned, error in
            XCTAssertNil(error)
            XCTAssertTrue((returned as AnyObject?) === original)
            XCTAssertEqual(try? Data(contentsOf: XCTUnwrap(url)), Data("abc".utf8))
            XCTAssertEqual(url?.deletingLastPathComponent().path, self.directory.path)
            done.fulfill()
        }
        wait(for: [done], timeout: 3)
        XCTAssertEqual(progress.completedUnitCount, 3)
        XCTAssertTrue(host.cancelled.isEmpty)
    }

    func testHostErrorAfterPartialWriteRemovesTempAndCompletesOnce() throws {
        let host = CannedHost()
        host.chunks[1] = (nil, false, NSError(domain: DuoFPErrorDomain, code: 3))
        // Retry disabled here: this test pins the partial-write cleanup on a host
        // error, which is orthogonal to the transient-retry behavior covered by
        // its own tests below.
        let controller = FetchController(hostProvider: { _ in host }, temporaryDirectory: directory,
                                         retry: .init(maxAttempts: 1))
        let done = expectation(description: "failed once")
        done.assertForOverFulfill = true
        _ = controller.fetch(item(), request: NSFileProviderRequest()) { url, item, error in
            XCTAssertNil(url)
            XCTAssertNil(item)
            XCTAssertNotNil(error)
            done.fulfill()
        }
        wait(for: [done], timeout: 3)
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath: directory.path), [])
        XCTAssertEqual(host.cancelled, ["token"])
    }

    func testPrematureEOFIsRejectedAndTempRemoved() throws {
        let host = CannedHost()
        host.chunks = [(Data("ab".utf8), true, nil)]
        let controller = FetchController(hostProvider: { _ in host }, temporaryDirectory: directory)
        let done = expectation(description: "failed")
        _ = controller.fetch(item(), request: NSFileProviderRequest()) { url, _, error in
            XCTAssertNil(url)
            // Task 14: the raw DuoFPErrorProtocol(7) FetchController used to
            // hand straight to Finder is now mapped through ErrorMap to the
            // specific NSFileProviderError it actually means.
            XCTAssertEqual((error as NSError?)?.domain, NSFileProviderErrorDomain)
            XCTAssertEqual((error as NSError?)?.code, NSFileProviderError.cannotSynchronize.rawValue)
            done.fulfill()
        }
        wait(for: [done], timeout: 3)
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath: directory.path), [])
    }

    func testFullWireCeilingChunkIsAccepted() throws {
        let host = CannedHost()
        host.totalSize = 1_048_576
        let bytes = Data(repeating: 0x65, count: 1_048_576)
        host.chunks = [(bytes, true, nil)]
        let controller = FetchController(hostProvider: { _ in host }, temporaryDirectory: directory)
        let done = expectation(description: "full chunk")
        _ = controller.fetch(item(size: 1_048_576), request: NSFileProviderRequest()) { url, _, error in
            XCTAssertNil(error)
            XCTAssertEqual(try? Data(contentsOf: XCTUnwrap(url)), bytes)
            done.fulfill()
        }
        wait(for: [done], timeout: 3)
    }

    func testZeroByteFetchSkipsPullChunkAndCompletesWithEmptyTemp() throws {
        let host = CannedHost()
        host.totalSize = 0
        let (perf, captured) = trace()
        let controller = FetchController(
            hostProvider: { _ in host }, temporaryDirectory: directory, perf: perf
        )
        let original = item(size: 0)
        let done = expectation(description: "zero byte completed")
        let progress = controller.fetch(original, request: NSFileProviderRequest()) { url, returned, error in
            XCTAssertNil(error)
            XCTAssertTrue((returned as AnyObject?) === original)
            XCTAssertEqual(try? Data(contentsOf: XCTUnwrap(url)), Data())
            done.fulfill()
        }
        wait(for: [done], timeout: 3)
        XCTAssertEqual(progress.completedUnitCount, 0)
        XCTAssertEqual(host.pullCallCount, 0, "zero-byte fetches must never call pullChunk")
        XCTAssertTrue(host.cancelled.isEmpty)
        XCTAssertFalse(eventNames(captured()).contains("pull_call_begin"))
        XCTAssertTrue(eventNames(captured()).contains("finalize_complete"))
    }

    func testDirectoryCreationFailureSurfacesErrorAndCancelsFetch() throws {
        // Point temporaryDirectory at a path already occupied by a regular
        // file, so FileManager.createDirectory(...) throws - this exercises
        // the same finish(error)/cancelFetch path a mid-stream local write
        // error would take. Task 14 resolves the DiskFull(6) vs Protocol(7)
        // question this used to defer: this particular failure ("path
        // already exists") is a local Cocoa error, not a DuoFPErrorDomain
        // one, and not actually a full-disk condition - ErrorMap passes it
        // through unchanged rather than reclassifying it as either. A GENUINE
        // local disk-full write failure already surfaces as its own
        // NSPOSIXErrorDomain/ENOSPC on its own, with no mapping needed - see
        // ErrorMap's doc comment. Only cleanliness of the failure (single
        // completion, cancelFetch, no leaked temp) is asserted here.
        let blockedPath = directory.appendingPathComponent("blocked")
        try Data().write(to: blockedPath)
        let host = CannedHost()
        let (perf, captured) = trace()
        let controller = FetchController(
            hostProvider: { _ in host }, temporaryDirectory: blockedPath, perf: perf
        )
        let done = expectation(description: "disk write error")
        _ = controller.fetch(item(), request: NSFileProviderRequest()) { url, item, error in
            XCTAssertNil(url)
            XCTAssertNil(item)
            XCTAssertNotNil(error)
            done.fulfill()
        }
        wait(for: [done], timeout: 3)
        XCTAssertEqual(host.cancelled, ["token"])
        let completions = captured().filter { $0.contains("event=completion_call") }
        XCTAssertEqual(completions.count, 1)
        XCTAssertTrue(completions[0].contains("status=error"))
    }

    // MARK: - Transient host-unreachable retry (peer link drop mid-transfer)

    /// A transient host error (notConnected) on the first attempt is retried and
    /// succeeds once the host is reachable again - the fetch completes normally
    /// instead of surfacing a failure that would abort a Finder folder copy.
    func testTransientHostErrorRetriesThenSucceeds() throws {
        let host = FlakyHost(failOpenTimes: 1, code: 8)  // notConnected once
        let (perf, captured) = trace()
        let controller = FetchController(hostProvider: { _ in host }, temporaryDirectory: directory,
                                         scheduler: now, perf: perf)
        let done = expectation(description: "retried then completed")
        _ = controller.fetch(item(), request: NSFileProviderRequest()) { url, _, error in
            XCTAssertNil(error)
            XCTAssertEqual(try? Data(contentsOf: XCTUnwrap(url)), Data("abc".utf8))
            done.fulfill()
        }
        wait(for: [done], timeout: 3)
        XCTAssertEqual(host.openCalls, 2, "one retry after the transient failure")
        XCTAssertEqual(eventNames(captured()).filter { $0 == "open_fetch_call_begin" }.count, 2)
        XCTAssertEqual(eventNames(captured()).filter { $0 == "completion_call" }.count, 1)
    }

    /// A host that never becomes reachable retries up to the budget, then fails
    /// with serverUnreachable (Apple's transient/retry-me code) - never a
    /// deletion-shaped error, and bounded so it cannot hang forever.
    func testHostUnavailableRetriesUntilBudgetThenServerUnreachable() throws {
        var providerCalls = 0
        let controller = FetchController(hostProvider: { _ in providerCalls += 1; return nil },
                                         temporaryDirectory: directory,
                                         retry: .init(maxAttempts: 3), scheduler: now)
        let done = expectation(description: "exhausted")
        _ = controller.fetch(item(), request: NSFileProviderRequest()) { url, _, error in
            XCTAssertNil(url)
            XCTAssertEqual((error as NSError?)?.domain, NSFileProviderErrorDomain)
            XCTAssertEqual((error as NSError?)?.code, NSFileProviderError.serverUnreachable.rawValue)
            done.fulfill()
        }
        wait(for: [done], timeout: 3)
        XCTAssertEqual(providerCalls, 3, "three attempts, then give up")
    }

    /// A non-transient host error (sourceChanged) is NOT retried - it is final.
    func testNonTransientErrorIsNotRetried() throws {
        let host = FlakyHost(failOpenTimes: 99, code: 2)  // sourceChanged, always
        let controller = FetchController(hostProvider: { _ in host }, temporaryDirectory: directory,
                                         scheduler: now)
        let done = expectation(description: "failed immediately")
        _ = controller.fetch(item(), request: NSFileProviderRequest()) { _, _, error in
            // sourceChanged reaches Finder as POSIX EBUSY ("item is in use").
            XCTAssertEqual((error as NSError?)?.domain, NSPOSIXErrorDomain)
            XCTAssertEqual((error as NSError?)?.code, Int(EBUSY))
            done.fulfill()
        }
        wait(for: [done], timeout: 3)
        XCTAssertEqual(host.openCalls, 1, "sourceChanged is final: no retry")
    }

    func testExtensionFetchUsesPublishedRecordAndRejectsInvalidItems() throws {
        let store = ReplicaStore(baseDirectory: directory.appendingPathComponent("replica"))
        let record: [String: Any] = ["schema": 1, "transfer_id": "generation", "state": "active",
            "created_ns": 1, "lease_deadline_ns": 2,
            "manifest": ["transfer_id": "generation", "entries": [
                ["path": "file.bin", "kind": "file", "size": 3, "mtime_ns": 0],
                ["path": "folder", "kind": "directory", "size": 0, "mtime_ns": 0]],
                "skipped": [], "total_bytes": 3, "drop_effect": 1]]
        try store.publish(recordJSON: JSONSerialization.data(withJSONObject: record))
        let host = CannedHost()
        var connections = 0
        let provider = FileProviderExtension(domain: NSFileProviderDomain(identifier: NSFileProviderDomainIdentifier("test"), displayName: "test"),
            replicaStore: store, hostProvider: { _ in connections += 1; return host }, temporaryDirectory: directory)
        for raw in ["missing:0", "generation", "generation:-1", "generation:2", "generation:1"] {
            _ = provider.fetchContents(for: NSFileProviderItemIdentifier(raw), version: nil, request: NSFileProviderRequest()) { url, _, error in
                XCTAssertNil(url)
                XCTAssertNotNil(error)
            }
        }
        XCTAssertEqual(connections, 0)
        let done = expectation(description: "extension completed")
        _ = provider.fetchContents(for: NSFileProviderItemIdentifier("generation:0"), version: nil, request: NSFileProviderRequest()) { url, _, error in
            XCTAssertNil(error)
            XCTAssertEqual(try? Data(contentsOf: XCTUnwrap(url)), Data("abc".utf8))
            done.fulfill()
        }
        wait(for: [done], timeout: 3)
        XCTAssertEqual(connections, 1)
        // Generation-lifetime fix (T1): a RETIRED generation stays servable by
        // itemIdentifier - namespace deletion is a separate lifecycle. So a
        // fetch after retire must still REACH the host (connections == 2) and
        // deliver bytes, NOT fail with noSuchItem before the host is contacted.
        host.chunks = [(Data("ab".utf8), false, nil), (Data("c".utf8), true, nil)]
        try store.retire("generation")
        let retiredDone = expectation(description: "retired fetch reaches host")
        _ = provider.fetchContents(for: NSFileProviderItemIdentifier("generation:0"), version: nil, request: NSFileProviderRequest()) { url, _, error in
            XCTAssertNil(error)
            XCTAssertEqual(try? Data(contentsOf: XCTUnwrap(url)), Data("abc".utf8))
            retiredDone.fulfill()
        }
        wait(for: [retiredDone], timeout: 3)
        XCTAssertEqual(connections, 2)
    }

    func testSuccessfulExtensionFetchLeavesMaterializedCacheUnderSystemControl() throws {
        let store = ReplicaStore(baseDirectory: directory.appendingPathComponent("replica-system-cache"))
        let record: [String: Any] = ["schema": 1, "transfer_id": "generation", "state": "active",
            "created_ns": 1, "lease_deadline_ns": 2,
            "manifest": ["transfer_id": "generation", "entries": [
                ["path": "file.bin", "kind": "file", "size": 3, "mtime_ns": 0]],
                "skipped": [], "total_bytes": 3, "drop_effect": 1]]
        try store.publish(recordJSON: JSONSerialization.data(withJSONObject: record))
        let host = CannedHost()
        let eagerCleanup = expectation(description: "no eager post-fetch cleanup")
        eagerCleanup.isInverted = true
        let perf = PerfTrace(clock: { 1 }, emit: { line in
            if line.contains("event=cleanup_scheduled") { eagerCleanup.fulfill() }
        })
        let provider = FileProviderExtension(
            domain: NSFileProviderDomain(
                identifier: NSFileProviderDomainIdentifier("test-system-cache"),
                displayName: "test-system-cache"
            ),
            replicaStore: store,
            hostProvider: { _ in host },
            temporaryDirectory: directory,
            perf: perf
        )
        let fetched = expectation(description: "fetch completed")

        _ = provider.fetchContents(
            for: NSFileProviderItemIdentifier("generation:0"), version: nil,
            request: NSFileProviderRequest()
        ) { url, _, error in
            XCTAssertNil(error)
            XCTAssertEqual(try? Data(contentsOf: XCTUnwrap(url)), Data("abc".utf8))
            fetched.fulfill()
        }

        wait(for: [fetched, eagerCleanup], timeout: 0.25)
    }
}
