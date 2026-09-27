import FileProvider
import XCTest

/// Phase H: bounded rolling prefetch. A deterministic harness drives the
/// coordinator like the extension does: Finder fetches, requestDownloadForItem
/// claims, the resulting fetch callbacks, completions and their replenishment.
final class RollingPrefetchTests: XCTestCase {
    private typealias Coordinator = FinderBurstDownloadCoordinator

    private final class Clock {
        var t: TimeInterval = 1000
        func callAsFunction() -> TimeInterval { t }
    }

    private final class Harness {
        let coordinator: Coordinator
        let entries: [DuoManifestEntry]
        let transferId: String
        var issued: [Coordinator.Download] = []       // requestDownloadForItem calls
        var pendingFetch: [Coordinator.Download] = [] // issued, fetch not yet begun
        var maxOutstanding = 0

        init(_ coordinator: Coordinator, files: Int, transferId: String = "gen") {
            self.coordinator = coordinator
            self.transferId = transferId
            entries = (0..<files).map {
                DuoManifestEntry(path: "\($0).bin", kind: "file", size: 1, mtimeNs: 1)
            }
        }

        func issue(_ downloads: [Coordinator.Download]) {
            for d in downloads where coordinator.markBurstRequestIssued(d) {
                issued.append(d); pendingFetch.append(d)
            }
            maxOutstanding = max(maxOutstanding, coordinator.outstandingCount(transferId: transferId))
        }

        /// A genuine Finder fetch of `index` that completes immediately.
        @discardableResult
        func finder(_ index: Int, complete: Bool = true, error: Error? = nil) -> Coordinator.FetchContext {
            let decision = coordinator.beginFetch(transferId: transferId, index: index,
                                                  entries: entries, isFileViewerRequest: true)
            issue(decision.downloads)
            if complete { issue(coordinator.completeFetch(decision.context, error: error)) }
            return decision.context
        }

        /// fileproviderd serves the oldest issued request: its fetch callback
        /// and completion (which may replenish).
        @discardableResult
        func serveNext(error: Error? = nil) -> Coordinator.FetchContext? {
            guard !pendingFetch.isEmpty else { return nil }
            let d = pendingFetch.removeFirst()
            let decision = coordinator.beginFetch(transferId: transferId, index: d.index,
                                                  entries: entries, isFileViewerRequest: true)
            XCTAssertEqual(decision.context.origin, .burst)
            XCTAssertTrue(decision.downloads.isEmpty, "a prefetch callback must never open prefetch")
            issue(coordinator.completeFetch(decision.context, error: error))
            return decision.context
        }

        func drain(limit: Int = 10_000) { var n = 0; while serveNext() != nil, n < limit { n += 1 } }
    }

    private func make(horizon: Int = 8, lease: TimeInterval = 10, clock: Clock = Clock(),
                      current: @escaping (String) -> Bool = { _ in true }) -> Coordinator {
        Coordinator(burstWaveSize: horizon, demandLease: lease, issuedExpiry: 10,
                    now: { clock() }, isGenerationCurrent: current)
    }

    // 1 + 2: initial Finder demand opens rolling prefetch, horizon fills to 8.
    func testSecondGenuineDemandOpensAndFillsHorizon() {
        let h = Harness(make(), files: 100)
        h.finder(0)
        XCTAssertTrue(h.issued.isEmpty, "a single Finder request never opens prefetch")
        h.finder(1)
        XCTAssertEqual(h.issued.map(\.index), Array(2...9))
        XCTAssertEqual(h.coordinator.outstandingCount(transferId: "gen"), 8)
    }

    // 3: one completion schedules the next item without any new Finder request.
    func testCompletionReplenishesWithoutFinderRequest() {
        let h = Harness(make(), files: 100)
        h.finder(0); h.finder(1)
        h.serveNext()
        XCTAssertEqual(h.issued.last?.index, 10)
        XCTAssertEqual(h.coordinator.outstandingCount(transferId: "gen"), 8)
    }

    // 4 + 5 + 7 + 12: repeated completions drain the whole eligible set, bounded,
    // each item requested exactly once.
    func testDrainsEntireSetBoundedAndWithoutDuplicates() {
        let h = Harness(make(), files: 1000)
        h.finder(0); h.finder(1)
        h.drain()
        XCTAssertEqual(h.issued.count, 998)
        XCTAssertEqual(Set(h.issued.map(\.index)).count, 998, "no duplicate requests")
        XCTAssertEqual(Set(h.issued.map(\.index)), Set(2..<1000))
        XCTAssertLessThanOrEqual(h.maxOutstanding, 8)
        XCTAssertLessThanOrEqual(h.coordinator.maxOutstanding(transferId: "gen"), 8)
        XCTAssertEqual(h.coordinator.outstandingCount(transferId: "gen"), 0)
    }

    // 6: items Finder already fetched are never requested again.
    func testAlreadySeenItemsAreSkipped() {
        let h = Harness(make(), files: 30)
        h.finder(0); h.finder(12, complete: true)  // 12 seen (and opens prefetch)
        h.finder(11)                                // Finder goes back: 11 seen too
        h.drain()
        XCTAssertFalse(h.issued.contains { $0.index == 11 || $0.index == 12 || $0.index == 0 })
        XCTAssertEqual(Set(h.issued.map(\.index)), Set(13..<30))
    }

    // 6b: an issued request whose fetch never comes (already materialized)
    // releases its slot after the expiry; its late fetch is still ours.
    func testIssuedRequestWithoutFetchExpiresAndLateFetchIsNotDemand() {
        let clock = Clock()
        let h = Harness(make(horizon: 2, lease: 100, clock: clock), files: 20)
        h.finder(0); h.finder(1)
        XCTAssertEqual(h.issued.map(\.index), [2, 3])
        let lost = h.pendingFetch.removeFirst()   // item 2 never gets a fetch
        h.serveNext()                              // 3 completes -> replenishes 4
        XCTAssertEqual(h.issued.map(\.index), [2, 3, 4])
        clock.t += 11                              // past issued expiry
        h.serveNext()                              // 4 completes -> 2 expired, fills 2 slots
        XCTAssertEqual(h.issued.map(\.index), [2, 3, 4, 5, 6])
        XCTAssertLessThanOrEqual(h.maxOutstanding, 2)
        // the late fetch of the expired request is classified as ours
        let late = h.coordinator.beginFetch(transferId: "gen", index: lost.index,
                                            entries: h.entries, isFileViewerRequest: true)
        XCTAssertEqual(late.context.origin, .burst)
        XCTAssertTrue(late.downloads.isEmpty)
    }

    // 8: a cancelled fetch stops replenishment; nothing new is requested.
    func testCancellationStopsReplenishment() {
        let h = Harness(make(), files: 10_000)
        h.finder(0); h.finder(1)
        let before = h.issued.count
        let cancelled = NSError(domain: NSCocoaErrorDomain, code: NSUserCancelledError)
        h.serveNext(error: cancelled)
        h.drain()
        XCTAssertEqual(h.issued.count, before, "no new coordinator requests after cancellation")
    }

    // 8b: invisible Finder cancel -> no genuine demand -> lease expiry stops it.
    func testDemandLeaseBoundsWorkAfterUnobservableCancel() {
        let clock = Clock()
        let h = Harness(make(lease: 10, clock: clock), files: 10_000)
        h.finder(0); h.finder(1)
        for _ in 0..<40 { h.serveNext() }          // live paste, within lease
        let atExpiry = h.issued.count
        clock.t += 10.5                            // Finder silently gone
        h.drain()
        XCTAssertEqual(h.issued.count, atExpiry, "no replenishment after the lease")
        XCTAssertLessThan(h.issued.count, 100)
        // Finder's next genuine demand renews the lease and resumes rolling.
        h.finder(9_000)
        XCTAssertEqual(h.issued.suffix(8).map(\.index), Array(9_001...9_008))
    }

    // 9: a completion of a no-longer-current generation opens nothing.
    func testRetiredGenerationCannotReplenish() {
        var current = true
        let h = Harness(make(current: { _ in current }), files: 200)
        h.finder(0); h.finder(1)
        let before = h.issued.count
        current = false                            // generation N+1 became current
        h.drain()
        XCTAssertEqual(h.issued.count, before, "stale completions must not open downloads")
        h.finder(150)                              // even genuine demand: no speculation
        XCTAssertEqual(h.issued.count, before)
    }

    // 10: a failing prefetch does not loop and does not leak slots.
    func testTerminalFailureStopsWithoutLoopOrLeak() {
        let h = Harness(make(), files: 500)
        h.finder(0); h.finder(1)
        let before = h.issued.count
        let noSuchItem = NSError(domain: NSFileProviderErrorDomain,
                                 code: NSFileProviderError.noSuchItem.rawValue)
        while h.serveNext(error: noSuchItem) != nil {}
        XCTAssertEqual(h.issued.count, before, "failures never replenish")
        XCTAssertEqual(h.coordinator.outstandingCount(transferId: "gen"), 0, "no slot leak")
        // a failed requestDownloadForItem also stops (and releases) cleanly
        let h2 = Harness(make(), files: 500)
        h2.finder(0)
        let d = h2.coordinator.beginFetch(transferId: "gen", index: 1, entries: h2.entries,
                                          isFileViewerRequest: true)
        for x in d.downloads { XCTAssertTrue(h2.coordinator.markBurstRequestIssued(x)) }
        for x in d.downloads { h2.coordinator.burstRequestFailed(x) }
        XCTAssertEqual(h2.coordinator.outstandingCount(transferId: "gen"), 0)
        XCTAssertTrue(h2.coordinator.completeFetch(d.context).isEmpty)
    }

    // 11: a set smaller than the horizon.
    func testSmallSetBelowHorizon() {
        let h = Harness(make(), files: 5)
        h.finder(0); h.finder(1)
        XCTAssertEqual(h.issued.map(\.index), [2, 3, 4])
        h.drain()
        XCTAssertEqual(h.issued.count, 3)
        XCTAssertEqual(h.coordinator.outstandingCount(transferId: "gen"), 0)
    }

    // 13: plain Finder behaviour is unchanged when prefetch is disabled / not
    // yet opened, and Finder fetches of in-horizon items are never duplicated.
    func testFinderOnlyBehaviourUnchanged() {
        let h = Harness(make(horizon: 0), files: 20)
        for i in 0..<20 { h.finder(i) }
        XCTAssertTrue(h.issued.isEmpty)
        let h2 = Harness(make(), files: 20)
        h2.finder(0); h2.finder(1)
        // Finder races for an issued in-horizon item: served as our fetch
        let first = h2.pendingFetch.first!
        let race = h2.coordinator.beginFetch(transferId: "gen", index: first.index,
                                             entries: h2.entries, isFileViewerRequest: true)
        XCTAssertEqual(race.context.origin, .burst)
        XCTAssertEqual(Set(h2.issued.map(\.index)).count, h2.issued.count)
    }
}
