import XCTest
import FileProvider

/// Records everything `NSFileProviderEnumerator.enumerateItems` reports, so
/// tests can assert on the resulting tree without a real File Provider host.
private final class RecordingEnumerationObserver: NSObject, NSFileProviderEnumerationObserver {
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

private final class RecordingChangeObserver: NSObject, NSFileProviderChangeObserver {
    private(set) var updatedItems: [NSFileProviderItem] = []
    private(set) var deletedIdentifiers: [NSFileProviderItemIdentifier] = []
    private(set) var finishedAnchor: NSFileProviderSyncAnchor?
    private(set) var moreComing = false

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

    func finishEnumeratingWithError(_ error: Error) {}
}

/// `DuoEnumerator` + `FileProviderExtension.item(for:)`/`enumerator(for:)`
/// (Task 5), proven fully from a temp-directory `ReplicaStore` - no
/// network, no host. Every test gets its own unique temp directory, never
/// the real sandbox container (this bundle is not sandboxed).
final class EnumeratorTests: XCTestCase {
    private var tmpDir: URL!
    private var store: ReplicaStore!

    override func setUpWithError() throws {
        try super.setUpWithError()
        tmpDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("EnumeratorTests-\(UUID().uuidString)", isDirectory: true)
        store = ReplicaStore(baseDirectory: tmpDir)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: tmpDir)
        store = nil
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
        let enumerator = DuoEnumerator(enumeratedItemIdentifier: identifier, store: store)
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

    // MARK: - enumerateChanges / currentSyncAnchor contract

    func testEnumerateChangesFinishesWithNoChangesReported() throws {
        try publishNestedGeneration()
        let enumerator = DuoEnumerator(enumeratedItemIdentifier: .rootContainer, store: store)
        let observer = RecordingChangeObserver()
        let anchor = NSFileProviderSyncAnchor(Data("v1".utf8))

        enumerator.enumerateChanges(for: observer, from: anchor)

        XCTAssertEqual(observer.finishedAnchor, anchor)
        XCTAssertFalse(observer.moreComing)
        XCTAssertTrue(observer.updatedItems.isEmpty)
        XCTAssertTrue(observer.deletedIdentifiers.isEmpty)
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
