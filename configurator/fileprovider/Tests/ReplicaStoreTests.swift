import XCTest

/// `ReplicaStore` + `DuoExtensionControlService` (Task 4). Covers: publish,
/// overwrite, retire-keeps-record, delete-removes, corrupt-quarantine,
/// restart-recovery, and ACK-strictly-after-durable-write for all three
/// control ops. Every test gets its OWN unique temp directory under
/// `FileManager.default.temporaryDirectory` - never the real sandbox
/// container path, since this bundle is not sandboxed and must never touch
/// the real user home.
final class ReplicaStoreTests: XCTestCase {
    private var tmpDir: URL!
    private var store: ReplicaStore!

    override func setUpWithError() throws {
        try super.setUpWithError()
        tmpDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("ReplicaStoreTests-\(UUID().uuidString)", isDirectory: true)
        store = ReplicaStore(baseDirectory: tmpDir)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: tmpDir)
        store = nil
        tmpDir = nil
        try super.tearDownWithError()
    }

    private func goldenFixtureData() throws -> Data {
        let url = try XCTUnwrap(
            Bundle(for: type(of: self)).url(forResource: "generation_record", withExtension: "json"),
            "golden fixture must be bundled as a test resource (see project.yml)"
        )
        return try Data(contentsOf: url)
    }

    private func goldenFixtureDict() throws -> [String: Any] {
        let obj = try JSONSerialization.jsonObject(with: try goldenFixtureData())
        return try XCTUnwrap(obj as? [String: Any])
    }

    private func encode(_ dict: [String: Any]) throws -> Data {
        try JSONSerialization.data(withJSONObject: dict, options: [.sortedKeys])
    }

    // MARK: - ReplicaStore

    func testPublishGoldenVectorThenRecordForReturnsIt() throws {
        try store.publish(recordJSON: try goldenFixtureData())

        let record = try XCTUnwrap(store.record(for: "abc123"))
        XCTAssertEqual(record.schema, 1)
        XCTAssertEqual(record.transferId, "abc123")
        XCTAssertEqual(record.state, "active")
        XCTAssertEqual(record.createdNs, 1)
        XCTAssertEqual(record.leaseDeadlineNs, 2)
        XCTAssertEqual(record.manifest["transfer_id"] as? String, "abc123")
    }

    func testSecondPublishOverwritesAtomically() throws {
        try store.publish(recordJSON: try goldenFixtureData())

        var mutated = try goldenFixtureDict()
        mutated["created_ns"] = 999
        try store.publish(recordJSON: try encode(mutated))

        let record = try XCTUnwrap(store.record(for: "abc123"))
        XCTAssertEqual(record.createdNs, 999, "second publish of the same id must overwrite, not append")

        // Exactly one live file for this id - no leftover temp/old copy.
        let liveFiles = try FileManager.default.contentsOfDirectory(
            at: tmpDir.appendingPathComponent("generations"), includingPropertiesForKeys: nil
        ).filter { $0.lastPathComponent == "abc123.json" }
        XCTAssertEqual(liveFiles.count, 1)
    }

    func testRetireKeepsRecordButExcludesFromAllActive() throws {
        try store.publish(recordJSON: try goldenFixtureData())
        XCTAssertEqual(store.allActive().count, 1)

        try store.retire("abc123")

        let record = try XCTUnwrap(store.record(for: "abc123"), "retire must keep the record, not remove it")
        XCTAssertEqual(record.state, "retired")
        XCTAssertTrue(store.allActive().isEmpty, "allActive() must exclude retired records")
    }

    func testDeleteRemovesRecordFile() throws {
        try store.publish(recordJSON: try goldenFixtureData())
        XCTAssertNotNil(store.record(for: "abc123"))

        try store.delete("abc123")

        XCTAssertNil(store.record(for: "abc123"))
    }

    func testRetireOnMissingGenerationThrowsNotFound() throws {
        XCTAssertThrowsError(try store.retire("does-not-exist")) { error in
            XCTAssertEqual(error as? ReplicaStoreError, .notFound("does-not-exist"))
        }
    }

    func testDeleteOnMissingGenerationThrowsNotFound() throws {
        XCTAssertThrowsError(try store.delete("does-not-exist")) { error in
            XCTAssertEqual(error as? ReplicaStoreError, .notFound("does-not-exist"))
        }
    }

    func testCorruptFileIsQuarantinedByRecoverAndSkippedByAllActive() throws {
        try store.publish(recordJSON: try goldenFixtureData())

        // Simulate a half-written file: truncated JSON dropped directly into
        // the live directory, bypassing the store's own write path entirely.
        let generationsDir = tmpDir.appendingPathComponent("generations")
        let corruptURL = generationsDir.appendingPathComponent("half-written.json")
        try Data("{\"schema\": 1, \"transfer_id\": \"half".utf8).write(to: corruptURL)

        let quarantined = store.recover()

        XCTAssertEqual(quarantined.map(\.lastPathComponent), ["half-written.json"])
        XCTAssertFalse(FileManager.default.fileExists(atPath: corruptURL.path),
                        "the corrupt file must be moved out of the live directory")
        let quarantinedURL = generationsDir.appendingPathComponent("quarantine/half-written.json")
        XCTAssertTrue(FileManager.default.fileExists(atPath: quarantinedURL.path))

        // The valid record survives untouched, and allActive() never trips
        // over the corrupt sibling.
        let active = store.allActive()
        XCTAssertEqual(active.map(\.transferId), ["abc123"])
    }

    func testRestartNewReplicaStoreOnSameDirStillReadsSurvivingRecords() throws {
        try store.publish(recordJSON: try goldenFixtureData())
        try store.retire("abc123") // exercise a non-trivial durable state too

        let restarted = ReplicaStore(baseDirectory: tmpDir)
        let record = try XCTUnwrap(restarted.record(for: "abc123"))
        XCTAssertEqual(record.state, "retired")
    }

    func testRecoverRunsAutomaticallyOnInit() throws {
        // Drop a corrupt file directly (no store involved yet), then
        // construct a store on that directory: startup recovery, not just
        // the explicit recover() call, must quarantine it.
        let generationsDir = tmpDir.appendingPathComponent("generations")
        try FileManager.default.createDirectory(at: generationsDir, withIntermediateDirectories: true)
        try Data("not json at all".utf8).write(to: generationsDir.appendingPathComponent("junk.json"))

        let fresh = ReplicaStore(baseDirectory: tmpDir)

        XCTAssertNil(fresh.record(for: "junk"))
        XCTAssertTrue(FileManager.default.fileExists(
            atPath: generationsDir.appendingPathComponent("quarantine/junk.json").path
        ))
    }

    // MARK: - DuoExtensionControlService: ACK strictly after durable write

    func testPublishGenerationAcksTrueOnlyAfterDurableWrite() throws {
        let service = DuoExtensionControlService(store: store)
        let liveURL = tmpDir.appendingPathComponent("generations/abc123.json")
        var observedDurableBeforeAck = false
        var ack = false
        var replyError: Error?

        service.publishGeneration(try goldenFixtureData()) { gotAck, error in
            // Read straight off disk (bypassing the store) from INSIDE the
            // reply closure: if the file is not there yet, ACK fired before
            // the durable write, which is exactly the bug this test exists
            // to catch.
            observedDurableBeforeAck = FileManager.default.fileExists(atPath: liveURL.path)
            ack = gotAck
            replyError = error
        }

        XCTAssertTrue(observedDurableBeforeAck, "file must already exist on disk by the time reply() fires")
        XCTAssertTrue(ack)
        XCTAssertNil(replyError)
    }

    func testPublishGenerationOnInvalidRecordAcksFalseAndWritesNothing() throws {
        let service = DuoExtensionControlService(store: store)
        var ack = true
        var replyError: Error?

        service.publishGeneration(Data("not json".utf8)) { gotAck, error in
            ack = gotAck
            replyError = error
        }

        XCTAssertFalse(ack)
        let nsError = try XCTUnwrap(replyError as NSError?)
        XCTAssertEqual(nsError.domain, DuoFPErrorDomain)
        XCTAssertTrue(store.allActive().isEmpty, "a rejected record must never be written")
    }

    func testRetireGenerationAcksTrueOnlyAfterDurableRewrite() throws {
        try store.publish(recordJSON: try goldenFixtureData())
        let service = DuoExtensionControlService(store: store)
        let liveURL = tmpDir.appendingPathComponent("generations/abc123.json")
        var observedRetiredBeforeAck = false
        var ack = false

        service.retireGeneration("abc123") { gotAck, _ in
            let onDisk = (try? Data(contentsOf: liveURL)).flatMap {
                try? JSONSerialization.jsonObject(with: $0) as? [String: Any]
            }
            observedRetiredBeforeAck = (onDisk?["state"] as? String) == "retired"
            ack = gotAck
        }

        XCTAssertTrue(observedRetiredBeforeAck)
        XCTAssertTrue(ack)
    }

    func testDeleteGenerationAcksTrueOnlyAfterRemoval() throws {
        try store.publish(recordJSON: try goldenFixtureData())
        let service = DuoExtensionControlService(store: store)
        let liveURL = tmpDir.appendingPathComponent("generations/abc123.json")
        var observedRemovedBeforeAck = false
        var ack = false

        service.deleteGeneration("abc123") { gotAck, _ in
            observedRemovedBeforeAck = !FileManager.default.fileExists(atPath: liveURL.path)
            ack = gotAck
        }

        XCTAssertTrue(observedRemovedBeforeAck)
        XCTAssertTrue(ack)
    }

    func testRetireGenerationOnMissingIdAcksFalseWithSourceMissingCode() throws {
        let service = DuoExtensionControlService(store: store)
        var ack = true
        var replyError: Error?

        service.retireGeneration("does-not-exist") { gotAck, error in
            ack = gotAck
            replyError = error
        }

        XCTAssertFalse(ack)
        let nsError = try XCTUnwrap(replyError as NSError?)
        XCTAssertEqual(nsError.domain, DuoFPErrorDomain)
        XCTAssertEqual(nsError.code, 1) // DuoFPErrorSourceMissing
    }
}
