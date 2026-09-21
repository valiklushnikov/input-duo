import XCTest
import FileProvider

/// Records everything `NSFileProviderEnumerator.enumerateItems` reports, so
/// tests can assert on the resulting tree without a real File Provider host.
private final class RecordingEnumerationObserver: NSObject, NSFileProviderEnumerationObserver {
    private(set) var enumeratedItems: [NSFileProviderItem] = []
    /// Per-`didEnumerate` batch sizes, so pagination tests can assert no single
    /// callback exceeds the page size.
    private(set) var batchSizes: [Int] = []
    private(set) var finished = false
    private(set) var nextPage: NSFileProviderPage?
    private(set) var finishError: Error?

    func didEnumerate(_ items: [NSFileProviderItem]) {
        enumeratedItems.append(contentsOf: items)
        batchSizes.append(items.count)
    }

    func finishEnumerating(upTo nextPage: NSFileProviderPage?) {
        finished = true
        self.nextPage = nextPage
    }

    func finishEnumeratingWithError(_ error: Error) {
        finishError = error
    }
}

private final class RecordingChangeObserver: NSObject, NSFileProviderChangeObserver {
    private(set) var updatedItems: [NSFileProviderItem] = []
    private(set) var deletedIdentifiers: [NSFileProviderItemIdentifier] = []
    private(set) var finishedAnchor: NSFileProviderSyncAnchor?
    private(set) var moreComing = false
    private(set) var finishError: Error?

    func didUpdate(_ updatedItems: [NSFileProviderItem]) {
        self.updatedItems.append(contentsOf: updatedItems)
    }

    func didDeleteItems(withIdentifiers deletedItemIdentifiers: [NSFileProviderItemIdentifier]) {
        self.deletedIdentifiers.append(contentsOf: deletedItemIdentifiers)
    }

    func finishEnumeratingChanges(upTo anchor: NSFileProviderSyncAnchor, moreComing: Bool) {
        finishedAnchor = anchor
        self.moreComing = moreComing
    }

    func finishEnumeratingWithError(_ error: Error) { finishError = error }
}

/// `DuoEnumerator` + `FileProviderExtension.item(for:)`/`enumerator(for:)`
/// (Task 5), proven fully from a temp-directory `ReplicaStore` - no
/// network, no host. Every test gets its own unique temp directory, never
/// the real sandbox container (this bundle is not sandboxed).
final class EnumeratorTests: XCTestCase {
    private var tmpDir: URL!
    private var store: ReplicaStore!
    private var journal: ChangeJournal!

    override func setUpWithError() throws {
        try super.setUpWithError()
        tmpDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("EnumeratorTests-\(UUID().uuidString)", isDirectory: true)
        store = ReplicaStore(baseDirectory: tmpDir)
        journal = ChangeJournal(baseDirectory: tmpDir)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: tmpDir)
        store = nil
        journal = nil
        tmpDir = nil
        try super.tearDownWithError()
    }

    /// One generation ("abc123") with a nested tree:
    ///   root.txt
    ///   dir1/
    ///     nested.txt
    private func publishNestedGeneration(transferId: String = "abc123") throws {
        let record: [String: Any] = [
            "schema": 1,
            "transfer_id": transferId,
            "state": "active",
            "created_ns": 1,
            "lease_deadline_ns": 2,
            "manifest": [
                "transfer_id": transferId,
                "drop_effect": 1,
                "total_bytes": 9,
                "skipped": [],
                "entries": [
                    ["path": "root.txt", "kind": "file", "size": 4, "mtime_ns": 100],
                    ["path": "dir1", "kind": "directory", "size": 0, "mtime_ns": 200],
                    ["path": "dir1/nested.txt", "kind": "file", "size": 5, "mtime_ns": 300],
                ],
            ],
        ]
        let data = try JSONSerialization.data(withJSONObject: record, options: [.sortedKeys])
        try store.publish(recordJSON: data)
    }

    private func enumerate(_ identifier: NSFileProviderItemIdentifier) -> RecordingEnumerationObserver {
        let enumerator = DuoEnumerator(enumeratedItemIdentifier: identifier, store: store, journal: journal)
        let observer = RecordingEnumerationObserver()
        enumerator.enumerateItems(for: observer, startingAt: NSFileProviderPage(Data()))
        return observer
    }

    // MARK: - Root enumeration

    func testRootEnumerationYieldsOneGenerationContainerPerActiveGeneration() throws {
        try publishNestedGeneration()

        let observer = enumerate(.rootContainer)

        XCTAssertTrue(observer.finished)
        XCTAssertNil(observer.finishError)
        XCTAssertEqual(observer.enumeratedItems.map(\.itemIdentifier.rawValue), ["abc123"])
    }

    func testRootEnumerationExcludesRetiredGenerations() throws {
        try publishNestedGeneration()
        try store.retire("abc123")

        let observer = enumerate(.rootContainer)

        XCTAssertTrue(observer.enumeratedItems.isEmpty)
    }

    // MARK: - Container enumeration

    func testContainerEnumerationYieldsTopLevelEntriesOnly() throws {
        try publishNestedGeneration()

        let observer = enumerate(NSFileProviderItemIdentifier("abc123"))

        let ids = Set(observer.enumeratedItems.map(\.itemIdentifier.rawValue))
        XCTAssertEqual(ids, ["abc123:0", "abc123:1"], "root.txt (index 0) and dir1 (index 1) are top-level")
    }

    // MARK: - Directory enumeration

    func testDirectoryEnumerationYieldsItsChildrenOnly() throws {
        try publishNestedGeneration()

        let observer = enumerate(NSFileProviderItemIdentifier("abc123:1")) // dir1

        XCTAssertEqual(observer.enumeratedItems.map(\.itemIdentifier.rawValue), ["abc123:2"])
        XCTAssertEqual(observer.enumeratedItems.first?.filename, "nested.txt")
    }

    // MARK: - Full-tree traversal via recursive enumeration (pagination contract)

    func testRecursiveEnumerationFromRootCoversFullTree() throws {
        try publishNestedGeneration()

        var discovered: Set<String> = []
        var frontier: [NSFileProviderItemIdentifier] = [.rootContainer]
        while let next = frontier.popLast() {
            let observer = enumerate(next)
            XCTAssertTrue(observer.finished, "enumerateItems must always call finishEnumerating(upTo:)")
            for item in observer.enumeratedItems {
                discovered.insert(item.itemIdentifier.rawValue)
                if item.capabilities?.contains(.allowsContentEnumerating) == true {
                    frontier.append(item.itemIdentifier)
                }
            }
        }

        XCTAssertEqual(discovered, ["abc123", "abc123:0", "abc123:1", "abc123:2"])
    }

    // MARK: - enumerateChanges / currentSyncAnchor contract (journal-backed)

    private func workingSetEnumerator() -> DuoEnumerator {
        DuoEnumerator(enumeratedItemIdentifier: .workingSet, store: store, journal: journal)
    }

    func testCurrentSyncAnchorReflectsJournalHead() throws {
        _ = try journal.append(kind: .update, itemIdentifiers: ["abc123"])
        let enumerator = workingSetEnumerator()
        var reported: NSFileProviderSyncAnchor?
        enumerator.currentSyncAnchor { reported = $0 }
        XCTAssertEqual(DuoEnumerator.decodeAnchor(try XCTUnwrap(reported)), journal.headRevision())
        XCTAssertEqual(journal.headRevision(), 1)
    }

    /// R3: a tombstone's durable delete change is replayed to the change
    /// observer as didDeleteItems, finishing at an anchor >= the tombstone
    /// revision.
    func testEnumerateChangesReplaysTombstoneDeletes() throws {
        try publishNestedGeneration()
        let n = try journal.recordTombstone(transferId: "abc123", itemIdentifiers: ["abc123", "abc123:0"])
        let enumerator = workingSetEnumerator()
        let observer = RecordingChangeObserver()

        enumerator.enumerateChanges(for: observer, from: DuoEnumerator.encodeAnchor(n - 1))

        XCTAssertNil(observer.finishError)
        XCTAssertEqual(Set(observer.deletedIdentifiers.map(\.rawValue)), ["abc123", "abc123:0"])
        XCTAssertGreaterThanOrEqual(DuoEnumerator.decodeAnchor(try XCTUnwrap(observer.finishedAnchor)) ?? 0, n)
    }

    /// R8: the KNOWN legacy static "v1" anchor is SOFT-MIGRATED to baseline
    /// revision 0 - journal changes are replayed and enumeration finishes at the
    /// current head, WITHOUT SyncAnchorExpired. Returning -1002 here forced
    /// fileproviderd into a full working-set resync over every persisted orphan
    /// identity, saturating the extension session and starving getService (real
    /// E2E A evidence). This replaces the old R6 expectation on purpose.
    func testEnumerateChangesLegacyV1AnchorMigratesToBaselineNotExpired() throws {
        try publishNestedGeneration()
        _ = try journal.recordTombstone(transferId: "abc123", itemIdentifiers: ["abc123:0"])
        let enumerator = workingSetEnumerator()
        let observer = RecordingChangeObserver()

        enumerator.enumerateChanges(for: observer, from: NSFileProviderSyncAnchor(Data("v1".utf8)))

        XCTAssertNil(observer.finishError, "legacy v1 must NOT expire - it migrates to baseline 0")
        XCTAssertEqual(observer.deletedIdentifiers.map(\.rawValue), ["abc123:0"], "journal replayed from 0")
        XCTAssertEqual(DuoEnumerator.decodeAnchor(try XCTUnwrap(observer.finishedAnchor)), journal.headRevision())
    }

    /// R9: a malformed/unknown anchor (neither a valid 8-byte revision nor the
    /// known legacy "v1") still reports SyncAnchorExpired (-1002). Corruption
    /// must NOT be silently masked as a migration to revision 0.
    func testEnumerateChangesMalformedAnchorStillExpires() throws {
        let enumerator = workingSetEnumerator()
        let observer = RecordingChangeObserver()

        enumerator.enumerateChanges(for: observer, from: NSFileProviderSyncAnchor(Data("garbage-anchor".utf8)))

        let nsError = try XCTUnwrap(observer.finishError as NSError?)
        XCTAssertEqual(nsError.domain, NSFileProviderErrorDomain)
        XCTAssertEqual(nsError.code, NSFileProviderError.syncAnchorExpired.rawValue)
        XCTAssertNil(observer.finishedAnchor)
    }

    /// R4: after a store/journal restart the tombstone deletion is still
    /// replayable from the persisted journal.
    func testEnumerateChangesReplaysTombstoneAfterRestart() throws {
        try publishNestedGeneration()
        let n = try journal.recordTombstone(transferId: "abc123", itemIdentifiers: ["abc123:0"])

        let restartedStore = ReplicaStore(baseDirectory: tmpDir)
        let restartedJournal = ChangeJournal(baseDirectory: tmpDir)
        let enumerator = DuoEnumerator(enumeratedItemIdentifier: .workingSet, store: restartedStore, journal: restartedJournal)
        let observer = RecordingChangeObserver()

        enumerator.enumerateChanges(for: observer, from: DuoEnumerator.encodeAnchor(n - 1))

        XCTAssertEqual(observer.deletedIdentifiers.map(\.rawValue), ["abc123:0"])
    }

    func testEnumerateChangesNotesObservedHighWaterMark() throws {
        _ = try journal.append(kind: .update, itemIdentifiers: ["abc123"])
        let enumerator = workingSetEnumerator()
        enumerator.enumerateChanges(for: RecordingChangeObserver(), from: DuoEnumerator.encodeAnchor(1))
        XCTAssertEqual(journal.observedRevision(), 1)
    }

    // MARK: - Working-set LIST = ACTIVE + RETIRED, TOMBSTONED excluded

    func testWorkingSetListsActiveAndRetiredButNotTombstoned() throws {
        try publishNestedGeneration(transferId: "active1")
        try publishNestedGeneration(transferId: "retired1")
        try store.retire("retired1")
        try publishNestedGeneration(transferId: "tomb1")
        try store.retire("tomb1")
        _ = try journal.recordTombstone(transferId: "tomb1", itemIdentifiers: ["tomb1", "tomb1:0", "tomb1:1", "tomb1:2"])

        let observer = enumerate(.workingSet)

        let ids = Set(observer.enumeratedItems.map(\.itemIdentifier.rawValue))
        XCTAssertTrue(ids.contains("active1"), "active generation container is namespace-live")
        XCTAssertTrue(ids.contains("retired1"), "retired generation stays in the working set")
        XCTAssertFalse(ids.contains("tomb1"), "tombstoned generation must be excluded from the LIST")
        XCTAssertFalse(ids.contains(where: { $0.hasPrefix("tomb1:") }))
    }

    // MARK: - Working-set pagination (Fix 2: never one-shot thousands of items)

    /// Publishes a generation with `fileEntries` top-level file entries.
    private func publishGeneration(transferId: String, fileEntries: Int) throws {
        var entries: [[String: Any]] = []
        for i in 0..<fileEntries {
            entries.append(["path": "f\(i).txt", "kind": "file", "size": 10, "mtime_ns": 100])
        }
        let record: [String: Any] = [
            "schema": 1, "transfer_id": transferId, "state": "active",
            "created_ns": 1, "lease_deadline_ns": 2,
            "manifest": ["transfer_id": transferId, "drop_effect": 1, "total_bytes": 0,
                         "skipped": [], "entries": entries],
        ]
        try store.publish(recordJSON: try JSONSerialization.data(withJSONObject: record, options: [.sortedKeys]))
    }

    /// Walk the working set page by page (fresh enumerator per page, as the
    /// system does), following the continuation token until nextPage is nil.
    private func walkWorkingSet(store s: ReplicaStore, journal j: ChangeJournal)
        -> (ids: [String], batchSizes: [Int], pages: Int) {
        var ids: [String] = []
        var batchSizes: [Int] = []
        var page = NSFileProviderPage(Data()) // initial
        var pages = 0
        while true {
            let observer = RecordingEnumerationObserver()
            DuoEnumerator(enumeratedItemIdentifier: .workingSet, store: s, journal: j)
                .enumerateItems(for: observer, startingAt: page)
            ids.append(contentsOf: observer.enumeratedItems.map(\.itemIdentifier.rawValue))
            batchSizes.append(contentsOf: observer.batchSizes)
            pages += 1
            guard let next = observer.nextPage else { break }
            page = next
            if pages > 10_000 { XCTFail("pagination did not terminate"); break }
        }
        return (ids, batchSizes, pages)
    }

    /// R10: ~2000 items across 5 generations enumerate correctly across pages -
    /// each page <= PAGE_SIZE, every live id exactly once, tombstoned excluded,
    /// no duplicates, eventual nextPage nil.
    func testWorkingSetPaginationCoversAllLiveIdsExactlyOnce() throws {
        for g in 0..<5 { try publishGeneration(transferId: "gen\(g)", fileEntries: 400) }
        try store.retire("gen1") // retired stays in the working set
        _ = try journal.recordTombstone(
            transferId: "gen2",
            itemIdentifiers: ["gen2"] + (0..<400).map { "gen2:\($0)" }
        )

        let (ids, batchSizes, pages) = walkWorkingSet(store: store, journal: journal)

        XCTAssertGreaterThan(pages, 1, "2000 items must span multiple pages")
        for size in batchSizes { XCTAssertLessThanOrEqual(size, DuoEnumerator.workingSetPageSize) }
        XCTAssertEqual(ids.count, Set(ids).count, "no duplicates across pages")
        XCTAssertFalse(ids.contains(where: { $0 == "gen2" || $0.hasPrefix("gen2:") }), "tombstoned excluded")
        // Expected live: gen0, gen1, gen3, gen4 (container + 400 entries each = 401)
        var expected: Set<String> = []
        for g in [0, 1, 3, 4] {
            expected.insert("gen\(g)")
            for i in 0..<400 { expected.insert("gen\(g):\(i)") }
        }
        XCTAssertEqual(Set(ids), expected, "every namespace-live id returned exactly once")
    }

    /// R11: the first callback of a large snapshot must NOT receive all items -
    /// it gets at most PAGE_SIZE and a continuation page.
    func testWorkingSetLargeSnapshotIsNotOneShot() throws {
        for g in 0..<5 { try publishGeneration(transferId: "gen\(g)", fileEntries: 400) }

        let observer = RecordingEnumerationObserver()
        workingSetEnumerator().enumerateItems(for: observer, startingAt: NSFileProviderPage(Data()))

        XCTAssertLessThanOrEqual(observer.enumeratedItems.count, DuoEnumerator.workingSetPageSize,
                                 "first page must not one-shot thousands of items")
        XCTAssertNotNil(observer.nextPage, "a large snapshot must hand back a continuation page")
    }

    /// A page boundary must also bound the work needed to build that page.
    /// Materializing every durable identity before slicing starves the same
    /// serial FPX queue that routes `fetchServicesForItemID`, so the service
    /// lookup cannot reach `supportedServiceSources` within its 5s deadline.
    func testWorkingSetFirstPageDoesNotStarveServiceLookup() throws {
        try publishGeneration(transferId: "large", fileEntries: 6_200)
        let observer = RecordingEnumerationObserver()

        let started = CFAbsoluteTimeGetCurrent()
        workingSetEnumerator().enumerateItems(
            for: observer,
            startingAt: NSFileProviderPage(Data())
        )
        let elapsed = CFAbsoluteTimeGetCurrent() - started

        XCTAssertEqual(observer.enumeratedItems.count, DuoEnumerator.workingSetPageSize)
        XCTAssertNotNil(observer.nextPage)
        XCTAssertLessThan(
            elapsed,
            2.0,
            "one 128-item page must leave the serial FPX queue available for service routing"
        )
    }

    /// R12: the continuation token is identifier-based, so it stays correct
    /// across a store/journal restart - page 2 continues after page 1 with no
    /// overlap.
    func testWorkingSetPaginationContinuationSurvivesRestart() throws {
        for g in 0..<3 { try publishGeneration(transferId: "gen\(g)", fileEntries: 200) }

        let page1 = RecordingEnumerationObserver()
        workingSetEnumerator().enumerateItems(for: page1, startingAt: NSFileProviderPage(Data()))
        let token = try XCTUnwrap(page1.nextPage, "first page must hand back a token")

        // Restart backing store + journal, then continue from the token.
        let store2 = ReplicaStore(baseDirectory: tmpDir)
        let journal2 = ChangeJournal(baseDirectory: tmpDir)
        let page2 = RecordingEnumerationObserver()
        DuoEnumerator(enumeratedItemIdentifier: .workingSet, store: store2, journal: journal2)
            .enumerateItems(for: page2, startingAt: token)

        let firstIds = Set(page1.enumeratedItems.map(\.itemIdentifier.rawValue))
        let secondIds = page2.enumeratedItems.map(\.itemIdentifier.rawValue)
        XCTAssertFalse(secondIds.isEmpty, "continuation after restart must still yield items")
        XCTAssertTrue(firstIds.isDisjoint(with: Set(secondIds)),
                      "restart continuation must not overlap page 1 (deterministic identifier cursor)")
    }

    // MARK: - FileProviderExtension wiring: item(for:) on a leaf

    private func makeExtension() -> FileProviderExtension {
        let identifier = NSFileProviderDomainIdentifier("com.duoinput.configurator.fileprovider.tests")
        let domain = NSFileProviderDomain(identifier: identifier, displayName: "Duo Input Tests")
        return FileProviderExtension(domain: domain, replicaStore: store)
    }

    func testItemForLeafIdentifierReturnsMatchingDuoItem() throws {
        try publishNestedGeneration()
        let ext = makeExtension()

        let expectation = expectation(description: "item(for:) completes")
        var resultItem: NSFileProviderItem?
        var resultError: Error?
        _ = ext.item(for: NSFileProviderItemIdentifier("abc123:2"), request: NSFileProviderRequest()) { item, error in
            resultItem = item
            resultError = error
            expectation.fulfill()
        }
        wait(for: [expectation], timeout: 5)

        XCTAssertNil(resultError)
        let item = try XCTUnwrap(resultItem)
        XCTAssertEqual(item.itemIdentifier.rawValue, "abc123:2")
        XCTAssertEqual(item.filename, "nested.txt")
        XCTAssertEqual(item.parentItemIdentifier.rawValue, "abc123:1")
    }

    /// Generation-lifetime fix (T1): a RETIRED generation is still resolvable
    /// by itemIdentifier - `item(for:)` must return the item, not noSuchItem.
    /// Retire only hides it from the root LIST; namespace deletion is a
    /// separate lifecycle. Mirrors the Python side, which serves retired
    /// generations (`_open_fetch`).
    func testItemForRetiredGenerationStillResolves() throws {
        try publishNestedGeneration()
        try store.retire("abc123")
        let ext = makeExtension()

        let expectation = expectation(description: "item(for:) completes")
        var resultItem: NSFileProviderItem?
        var resultError: Error?
        _ = ext.item(for: NSFileProviderItemIdentifier("abc123:2"), request: NSFileProviderRequest()) { item, error in
            resultItem = item
            resultError = error
            expectation.fulfill()
        }
        wait(for: [expectation], timeout: 5)

        XCTAssertNil(resultError)
        let item = try XCTUnwrap(resultItem)
        XCTAssertEqual(item.itemIdentifier.rawValue, "abc123:2")
        XCTAssertEqual(item.filename, "nested.txt")
    }

    /// TOMBSTONED direct-request semantics (reconciliation race): even after a
    /// generation is tombstoned (a durable delete change in the journal), a
    /// direct item(for:) still resolves while the backing record exists.
    /// Deletion reaches the daemon through working-set change enumeration, never
    /// by failing a direct request - that is what keeps the -1005 -> -36 race
    /// from ever returning.
    func testTombstonedGenerationStillResolvesByDirectItemRequest() throws {
        try publishNestedGeneration()
        try store.retire("abc123")
        _ = try journal.recordTombstone(transferId: "abc123", itemIdentifiers: ["abc123", "abc123:0", "abc123:1", "abc123:2"])
        let ext = makeExtension()

        let done = expectation(description: "item(for:) completes")
        var resultItem: NSFileProviderItem?
        var resultError: Error?
        _ = ext.item(for: NSFileProviderItemIdentifier("abc123:2"), request: NSFileProviderRequest()) { item, error in
            resultItem = item
            resultError = error
            done.fulfill()
        }
        wait(for: [done], timeout: 5)

        XCTAssertNil(resultError)
        XCTAssertEqual(try XCTUnwrap(resultItem).itemIdentifier.rawValue, "abc123:2")
    }

    func testItemForUnknownIdentifierReturnsNoSuchItemError() throws {
        let ext = makeExtension()

        let expectation = expectation(description: "item(for:) completes")
        var resultError: Error?
        _ = ext.item(for: NSFileProviderItemIdentifier("does-not-exist:0"), request: NSFileProviderRequest()) { item, error in
            XCTAssertNil(item)
            resultError = error
            expectation.fulfill()
        }
        wait(for: [expectation], timeout: 5)

        let nsError = try XCTUnwrap(resultError as NSError?)
        XCTAssertEqual(nsError.domain, NSFileProviderErrorDomain)
        XCTAssertEqual(nsError.code, NSFileProviderError.noSuchItem.rawValue)
    }

    func testEnumeratorForReturnsDuoEnumeratorBackedByReplica() throws {
        try publishNestedGeneration()
        let ext = makeExtension()

        let enumerator = try ext.enumerator(for: NSFileProviderItemIdentifier("abc123"), request: NSFileProviderRequest())

        let observer = RecordingEnumerationObserver()
        enumerator.enumerateItems(for: observer, startingAt: NSFileProviderPage(Data()))
        XCTAssertEqual(Set(observer.enumeratedItems.map(\.itemIdentifier.rawValue)), ["abc123:0", "abc123:1"])
    }
}
