import XCTest
import UniformTypeIdentifiers

/// `DuoItemModel` / `DuoItemFactory` (Task 5): the pure, deterministic
/// mapping from a `GenerationRecord` (Task 4's replica) to
/// identifiers/versions/capabilities. Every test here builds records
/// in-memory (no disk I/O) except the golden-fixture sanity check, which
/// goes through a temp-directory `ReplicaStore` - never the real sandbox
/// container, since this bundle is not sandboxed.
final class ItemModelTests: XCTestCase {
    private func makeRecord(
        transferId: String = "abc123",
        createdNs: Int64 = 1,
        entries: [[String: Any]]
    ) -> GenerationRecord {
        GenerationRecord(
            schema: 1,
            transferId: transferId,
            state: "active",
            createdNs: createdNs,
            leaseDeadlineNs: 2,
            manifest: [
                "transfer_id": transferId,
                "entries": entries,
                "skipped": [],
                "total_bytes": 0,
                "drop_effect": 1,
            ]
        )
    }

    // MARK: - Determinism

    func testSameManifestProducesIdenticalIdentifierAndVersionAcrossConstructions() {
        let record = makeRecord(entries: [
            ["path": "a.txt", "kind": "file", "size": 3, "mtime_ns": 111],
        ])

        let item1 = DuoItemFactory.item(for: record, index: 0)
        let item2 = DuoItemFactory.item(for: record, index: 0)

        XCTAssertNotNil(item1)
        XCTAssertNotNil(item2)
        XCTAssertEqual(item1?.itemIdentifier, item2?.itemIdentifier)
        XCTAssertEqual(item1?.itemVersion.contentVersion, item2?.itemVersion.contentVersion)
        XCTAssertEqual(item1?.itemVersion.metadataVersion, item2?.itemVersion.metadataVersion)
        XCTAssertEqual(item1?.itemIdentifier.rawValue, "abc123:0")
    }

    func testContainerIdentifierIsBareTransferIdAndDeterministic() {
        let record = makeRecord(entries: [])

        let container1 = DuoItemFactory.containerItem(for: record)
        let container2 = DuoItemFactory.containerItem(for: record)

        XCTAssertEqual(container1.itemIdentifier.rawValue, "abc123")
        XCTAssertEqual(container1.parentItemIdentifier, .rootContainer)
        XCTAssertEqual(container1.itemVersion.contentVersion, container2.itemVersion.contentVersion)
        XCTAssertEqual(container1.itemVersion.metadataVersion, container2.itemVersion.metadataVersion)
    }

    // MARK: - Identity scheme: same filename, different directories

    func testSameFilenameInDifferentDirsGetsDifferentIdentifiersAndParents() {
        let record = makeRecord(entries: [
            ["path": "dir1", "kind": "directory", "size": 0, "mtime_ns": 1],
            ["path": "dir2", "kind": "directory", "size": 0, "mtime_ns": 1],
            ["path": "dir1/f.txt", "kind": "file", "size": 5, "mtime_ns": 10],
            ["path": "dir2/f.txt", "kind": "file", "size": 5, "mtime_ns": 10],
        ])

        let itemInDir1 = try! XCTUnwrap(DuoItemFactory.item(for: record, index: 2))
        let itemInDir2 = try! XCTUnwrap(DuoItemFactory.item(for: record, index: 3))

        XCTAssertEqual(itemInDir1.filename, "f.txt")
        XCTAssertEqual(itemInDir2.filename, "f.txt")
        XCTAssertNotEqual(itemInDir1.itemIdentifier, itemInDir2.itemIdentifier)
        XCTAssertNotEqual(itemInDir1.parentItemIdentifier, itemInDir2.parentItemIdentifier)
        XCTAssertEqual(itemInDir1.parentItemIdentifier.rawValue, "abc123:0")
        XCTAssertEqual(itemInDir2.parentItemIdentifier.rawValue, "abc123:1")
    }

    func testTopLevelEntryParentIsGenerationContainer() {
        let record = makeRecord(entries: [
            ["path": "root.txt", "kind": "file", "size": 1, "mtime_ns": 1],
        ])

        let item = try! XCTUnwrap(DuoItemFactory.item(for: record, index: 0))

        XCTAssertEqual(item.parentItemIdentifier.rawValue, "abc123")
    }

    // MARK: - Capabilities / flags (0600, no uchg - expressed via API only)

    func testFileItemHasReadWriteCapabilitiesAndUserFlags() {
        let record = makeRecord(entries: [
            ["path": "a.txt", "kind": "file", "size": 3, "mtime_ns": 111],
        ])
        let item = try! XCTUnwrap(DuoItemFactory.item(for: record, index: 0))

        XCTAssertTrue(item.capabilities.contains(.allowsReading))
        XCTAssertTrue(item.capabilities.contains(.allowsWriting))
        // Production contract: a materialized file MUST be purgeable, else
        // NSFileProviderManager.evictItem fails with -2008 (nonEvictable) and
        // post-fetch cache cleanup can never dehydrate the blob. See
        // NSFileProviderManager.h: evictItem returns NSFileProviderErrorNonEvictable
        // "if the item has been marked as non-purgeable by the provider".
        XCTAssertTrue(item.capabilities.contains(.allowsEvicting))
        // NOTE: NSFileProviderItemCapabilitiesAllowsContentEnumerating is
        // defined by the SDK itself as a bit-identical alias of
        // AllowsReading (see NSFileProviderItem.h), so it is trivially
        // "contained" by any item that allows reading at all - it is not a
        // usable file-vs-directory discriminator and is intentionally not
        // asserted false here.
        XCTAssertTrue(item.fileSystemFlags.contains(.userReadable))
        XCTAssertTrue(item.fileSystemFlags.contains(.userWritable))
        XCTAssertEqual(item.documentSize, NSNumber(value: 3))
    }

    // MARK: - Directories / containers

    func testDirectoryItemIsFolderWithEnumeratingCapabilityAndNilSize() {
        let record = makeRecord(entries: [
            ["path": "dir1", "kind": "directory", "size": 0, "mtime_ns": 1],
        ])
        let item = try! XCTUnwrap(DuoItemFactory.item(for: record, index: 0))

        XCTAssertEqual(item.contentType, .folder)
        XCTAssertTrue(item.capabilities.contains(.allowsContentEnumerating))
        XCTAssertTrue(item.capabilities.contains(.allowsReading))
        XCTAssertTrue(item.capabilities.contains(.allowsWriting))
        XCTAssertNil(item.documentSize)
    }

    func testGenerationContainerIsFolderWithEnumeratingCapability() {
        let record = makeRecord(entries: [])
        let container = DuoItemFactory.containerItem(for: record)

        XCTAssertEqual(container.contentType, .folder)
        XCTAssertTrue(container.capabilities.contains(.allowsContentEnumerating))
        XCTAssertNil(container.documentSize)
    }

    // MARK: - contentType by extension

    func testFileContentTypeDerivedFromExtensionFallsBackToData() {
        let record = makeRecord(entries: [
            ["path": "a.txt", "kind": "file", "size": 1, "mtime_ns": 1],
            ["path": "noext", "kind": "file", "size": 1, "mtime_ns": 1],
        ])

        let txt = try! XCTUnwrap(DuoItemFactory.item(for: record, index: 0))
        let noExt = try! XCTUnwrap(DuoItemFactory.item(for: record, index: 1))

        XCTAssertEqual(txt.contentType, .plainText)
        XCTAssertEqual(noExt.contentType, .data)
    }

    // MARK: - Out of range

    func testItemForOutOfRangeIndexReturnsNil() {
        let record = makeRecord(entries: [
            ["path": "a.txt", "kind": "file", "size": 1, "mtime_ns": 1],
        ])
        XCTAssertNil(DuoItemFactory.item(for: record, index: 5))
    }

    // MARK: - Golden fixture sanity check

    func testGoldenFixtureProducesExpectedContainerAndFileItem() throws {
        let tmpDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("ItemModelTests-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: tmpDir) }
        let store = ReplicaStore(baseDirectory: tmpDir)
        let url = try XCTUnwrap(
            Bundle(for: type(of: self)).url(forResource: "generation_record", withExtension: "json")
        )
        try store.publish(recordJSON: try Data(contentsOf: url))
        let record = try XCTUnwrap(store.record(for: "abc123"))

        let container = DuoItemFactory.containerItem(for: record)
        XCTAssertEqual(container.itemIdentifier.rawValue, "abc123")
        XCTAssertEqual(container.parentItemIdentifier, .rootContainer)

        let file = try XCTUnwrap(DuoItemFactory.item(for: record, index: 0))
        XCTAssertEqual(file.itemIdentifier.rawValue, "abc123:0")
        XCTAssertEqual(file.parentItemIdentifier.rawValue, "abc123")
        XCTAssertEqual(file.filename, "a.txt")
        XCTAssertEqual(file.documentSize, NSNumber(value: 3))
    }
}
