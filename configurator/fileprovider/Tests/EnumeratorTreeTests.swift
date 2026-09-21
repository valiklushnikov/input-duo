import XCTest
import FileProvider

/// Task 13: `DuoEnumerator` + `DuoItemModel`/`DuoItemFactory` (Task 5) against
/// REAL tree shapes that `EnumeratorTests.swift` doesn't reach: nesting of
/// depth >= 2 (a directory inside a directory), and a manifest entry whose
/// parent directory is missing from the manifest entirely (the defensive
/// fallback in `DuoItemModel.parentIdentifier` - "attach to the generation
/// container rather than dropping the item from the tree").
///
/// Mirrors `EnumeratorTests.swift`'s fixtures/helpers (duplicated here,
/// file-private, exactly like every other Test file in this target
/// duplicates its own small doubles rather than sharing them across files).
private final class TreeRecordingEnumerationObserver: NSObject, NSFileProviderEnumerationObserver {
    private(set) var enumeratedItems: [NSFileProviderItem] = []
    private(set) var finished = false
    private(set) var finishError: Error?

    func didEnumerate(_ items: [NSFileProviderItem]) {
        enumeratedItems.append(contentsOf: items)
    }

    func finishEnumerating(upTo nextPage: NSFileProviderPage?) {
        finished = true
    }

    func finishEnumeratingWithError(_ error: Error) {
        finishError = error
    }
}

final class EnumeratorTreeTests: XCTestCase {
    private var tmpDir: URL!
    private var store: ReplicaStore!
    private var journal: ChangeJournal!

    override func setUpWithError() throws {
        try super.setUpWithError()
        tmpDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("EnumeratorTreeTests-\(UUID().uuidString)", isDirectory: true)
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

    private func publish(transferId: String, entries: [[String: Any]]) throws {
        let record: [String: Any] = [
            "schema": 1,
            "transfer_id": transferId,
            "state": "active",
            "created_ns": 1,
            "lease_deadline_ns": 2,
            "manifest": [
                "transfer_id": transferId,
                "drop_effect": 1,
                "total_bytes": 0,
                "skipped": [],
                "entries": entries,
            ],
        ]
        let data = try JSONSerialization.data(withJSONObject: record, options: [.sortedKeys])
        try store.publish(recordJSON: data)
    }

    private func enumerate(_ identifier: NSFileProviderItemIdentifier) -> TreeRecordingEnumerationObserver {
        let enumerator = DuoEnumerator(enumeratedItemIdentifier: identifier, store: store, journal: journal)
        let observer = TreeRecordingEnumerationObserver()
        enumerator.enumerateItems(for: observer, startingAt: NSFileProviderPage(Data()))
        return observer
    }

    private func makeExtension(transferId: String) -> FileProviderExtension {
        let identifier = NSFileProviderDomainIdentifier("com.duoinput.configurator.fileprovider.tests.\(transferId)")
        let domain = NSFileProviderDomain(identifier: identifier, displayName: "Duo Input Tests")
        return FileProviderExtension(domain: domain, replicaStore: store)
    }

    // MARK: - Depth >= 2 nesting: dirA/ -> dirA/sub/ -> dirA/sub/deep.txt

    private let deepTransferId = "deep-tree"

    private func publishDepthTwoGeneration() throws {
        try publish(transferId: deepTransferId, entries: [
            ["path": "dirA", "kind": "directory", "size": 0, "mtime_ns": 1],
            ["path": "dirA/sub", "kind": "directory", "size": 0, "mtime_ns": 2],
            ["path": "dirA/sub/deep.txt", "kind": "file", "size": 6, "mtime_ns": 3],
            ["path": "dirA/shallow.txt", "kind": "file", "size": 4, "mtime_ns": 4],
        ])
        // index: 0 dirA, 1 dirA/sub, 2 dirA/sub/deep.txt, 3 dirA/shallow.txt
    }

    func testContainerEnumerationOfDepthTwoTreeYieldsOnlyTheTopLevelDirectory() throws {
        try publishDepthTwoGeneration()

        let observer = enumerate(NSFileProviderItemIdentifier(deepTransferId))

        XCTAssertEqual(observer.enumeratedItems.map(\.itemIdentifier.rawValue), ["\(deepTransferId):0"])
    }

    func testMidLevelDirectoryEnumerationYieldsItsOwnChildrenOnlyNotGrandchildren() throws {
        try publishDepthTwoGeneration()

        // dirA (index 0): direct children are dirA/sub (dir) and dirA/shallow.txt (file).
        let observer = enumerate(NSFileProviderItemIdentifier("\(deepTransferId):0"))

        XCTAssertEqual(
            Set(observer.enumeratedItems.map(\.itemIdentifier.rawValue)),
            ["\(deepTransferId):1", "\(deepTransferId):3"]
        )
    }

    func testLeafLevelDirectoryEnumerationAtDepthTwoYieldsItsOwnChild() throws {
        try publishDepthTwoGeneration()

        // dirA/sub (index 1): its only child is dirA/sub/deep.txt (index 2).
        let observer = enumerate(NSFileProviderItemIdentifier("\(deepTransferId):1"))

        XCTAssertEqual(observer.enumeratedItems.map(\.itemIdentifier.rawValue), ["\(deepTransferId):2"])
        XCTAssertEqual(observer.enumeratedItems.first?.filename, "deep.txt")
    }

    func testDeepLeafItemParentIsItsImmediateDirectoryNotTheGrandparentOrContainer() throws {
        try publishDepthTwoGeneration()
        let record = try XCTUnwrap(store.record(for: deepTransferId))

        let deepItem = try XCTUnwrap(DuoItemFactory.item(for: record, index: 2)) // dirA/sub/deep.txt

        XCTAssertEqual(deepItem.parentItemIdentifier.rawValue, "\(deepTransferId):1", "must be dirA/sub, not dirA or the container")
    }

    func testRecursiveEnumerationFromRootCoversTheFullDepthTwoTree() throws {
        try publishDepthTwoGeneration()

        var discovered: Set<String> = []
        var frontier: [NSFileProviderItemIdentifier] = [.rootContainer]
        while let next = frontier.popLast() {
            let observer = enumerate(next)
            XCTAssertTrue(observer.finished)
            for item in observer.enumeratedItems {
                discovered.insert(item.itemIdentifier.rawValue)
                if item.capabilities?.contains(.allowsContentEnumerating) == true {
                    frontier.append(item.itemIdentifier)
                }
            }
        }

        XCTAssertEqual(
            discovered,
            [deepTransferId, "\(deepTransferId):0", "\(deepTransferId):1", "\(deepTransferId):2", "\(deepTransferId):3"]
        )
    }

    // MARK: - Orphaned / missing-parent fallback (ItemModel.swift's defensive branch)

    private let orphanTransferId = "orphan-tree"

    /// "ghost/child.txt" claims a parent directory ("ghost") that is NOT
    /// itself present anywhere in the manifest as a directory entry - the
    /// exact defensive case `DuoItemModel.parentIdentifier` guards: "manifest
    /// referenced a parent directory that isn't itself an entry".
    private func publishOrphanedEntryGeneration() throws {
        try publish(transferId: orphanTransferId, entries: [
            ["path": "ghost/child.txt", "kind": "file", "size": 5, "mtime_ns": 1],
            ["path": "normal.txt", "kind": "file", "size": 2, "mtime_ns": 2],
        ])
        // index: 0 ghost/child.txt (orphaned), 1 normal.txt (genuinely top-level)
    }

    func testOrphanedEntryParentIdentifierFallsBackToGenerationContainer() throws {
        try publishOrphanedEntryGeneration()
        let record = try XCTUnwrap(store.record(for: orphanTransferId))
        let entries = DuoItemModel.entries(in: record)

        let parent = DuoItemModel.parentIdentifier(transferId: orphanTransferId, entries: entries, index: 0)

        XCTAssertEqual(parent, DuoItemModel.containerIdentifier(transferId: orphanTransferId))
        XCTAssertEqual(parent.rawValue, orphanTransferId)
    }

    func testOrphanedEntryStillProducesAWellFormedItemAttachedToTheContainer() throws {
        try publishOrphanedEntryGeneration()
        let record = try XCTUnwrap(store.record(for: orphanTransferId))

        // The item is NOT dropped from the tree (per the code's own ruling
        // comment) - item(for:) still resolves it directly by index/identifier.
        let orphan = try XCTUnwrap(DuoItemFactory.item(for: record, index: 0))

        XCTAssertEqual(orphan.itemIdentifier.rawValue, "\(orphanTransferId):0")
        XCTAssertEqual(orphan.parentItemIdentifier.rawValue, orphanTransferId)
        XCTAssertEqual(orphan.filename, "child.txt")
    }

    func testFileProviderExtensionItemForResolvesTheOrphanedEntryDirectlyByIdentifier() throws {
        try publishOrphanedEntryGeneration()
        let ext = makeExtension(transferId: orphanTransferId)

        let expectation = expectation(description: "item(for:) completes")
        var resultItem: NSFileProviderItem?
        var resultError: Error?
        _ = ext.item(for: NSFileProviderItemIdentifier("\(orphanTransferId):0"), request: NSFileProviderRequest()) { item, error in
            resultItem = item
            resultError = error
            expectation.fulfill()
        }
        wait(for: [expectation], timeout: 5)

        XCTAssertNil(resultError)
        let item = try XCTUnwrap(resultItem)
        XCTAssertEqual(item.parentItemIdentifier.rawValue, orphanTransferId, "falls back to the container, never nil/crash")
    }

    func testOrphanedEntryIsNotListedWhenEnumeratingTheGenerationContainer() throws {
        // Pins the CURRENT reality: DuoEnumerator.children() matches children
        // by literal parentPath equality ("" for top-level), so an entry
        // whose parent segment ("ghost") never actually exists as a
        // directory entry is unreachable via container enumeration - even
        // though item(for:) above still resolves it directly by identifier
        // with a container fallback parent. Only "normal.txt" (truly
        // top-level, parentPath == "") is listed.
        try publishOrphanedEntryGeneration()

        let observer = enumerate(NSFileProviderItemIdentifier(orphanTransferId))

        XCTAssertEqual(observer.enumeratedItems.map(\.itemIdentifier.rawValue), ["\(orphanTransferId):1"])
        XCTAssertEqual(observer.enumeratedItems.first?.filename, "normal.txt")
    }
}
