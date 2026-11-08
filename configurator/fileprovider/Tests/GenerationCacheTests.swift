import XCTest
import FileProvider

/// `GenerationCache`: decoded records are reused across lookups but never
/// served stale - every publish/retire/GC is observed on the very next call,
/// because entries are keyed by the record file's on-disk signature.
final class GenerationCacheTests: XCTestCase {
    private var tmpDir: URL!
    private var store: ReplicaStore!

    override func setUpWithError() throws {
        try super.setUpWithError()
        tmpDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("GenerationCacheTests-\(UUID().uuidString)", isDirectory: true)
        store = ReplicaStore(baseDirectory: tmpDir)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: tmpDir)
        store = nil
        tmpDir = nil
        try super.tearDownWithError()
    }

    private func publish(_ transferId: String, files: [String]) throws {
        let record: [String: Any] = [
            "schema": 1, "transfer_id": transferId, "state": "active",
            "created_ns": 1, "lease_deadline_ns": 2,
            "manifest": [
                "transfer_id": transferId, "drop_effect": 1, "total_bytes": 0, "skipped": [],
                "entries": files.map { ["path": $0, "kind": "file", "size": 1, "mtime_ns": 1] },
            ],
        ]
        try store.publish(recordJSON: try JSONSerialization.data(withJSONObject: record, options: [.sortedKeys]))
    }

    func testDecodedMatchesStoreAndUnknownIsNil() throws {
        try publish("g1", files: ["a.txt", "b.txt"])
        let cache = GenerationCache(store: store)

        let generation = try XCTUnwrap(cache.decoded(for: "g1"))
        XCTAssertEqual(generation.record.transferId, "g1")
        XCTAssertEqual(generation.manifest.entries.map(\.path), ["a.txt", "b.txt"])
        XCTAssertNil(cache.decoded(for: "missing"))
        XCTAssertNil(cache.decoded(for: "../escape"))
    }

    func testRetireIsObservedOnNextLookup() throws {
        try publish("g1", files: ["a.txt"])
        let cache = GenerationCache(store: store)
        XCTAssertEqual(cache.decoded(for: "g1")?.record.isActive, true)
        XCTAssertEqual(cache.summary(for: "g1")?.isActive, true)

        try store.retire("g1")

        XCTAssertEqual(cache.decoded(for: "g1")?.record.isActive, false)
        XCTAssertEqual(cache.summary(for: "g1")?.isActive, false)
    }

    func testRepublishWithNewManifestIsObservedOnNextLookup() throws {
        try publish("g1", files: ["a.txt"])
        let cache = GenerationCache(store: store)
        XCTAssertEqual(cache.decoded(for: "g1")?.manifest.entries.count, 1)
        XCTAssertEqual(cache.summary(for: "g1")?.entryCount, 1)

        try publish("g1", files: ["a.txt", "b.txt", "c.txt"])

        XCTAssertEqual(cache.decoded(for: "g1")?.manifest.entries.count, 3)
        XCTAssertEqual(cache.summary(for: "g1")?.entryCount, 3)
    }

    func testDeleteIsObservedOnNextLookup() throws {
        try publish("g1", files: ["a.txt"])
        let cache = GenerationCache(store: store)
        XCTAssertNotNil(cache.decoded(for: "g1"))

        try store.delete("g1")

        XCTAssertNil(cache.decoded(for: "g1"))
        XCTAssertNil(cache.summary(for: "g1"))
        XCTAssertTrue(cache.summaries().isEmpty)
    }

    func testLRUEvictionStillServesEveryGenerationCorrectly() throws {
        for g in 0..<5 { try publish("g\(g)", files: (0...g).map { "f\($0).txt" }) }
        let cache = GenerationCache(store: store, capacity: 2)

        for round in 0..<3 {
            for g in 0..<5 {
                XCTAssertEqual(cache.decoded(for: "g\(g)")?.manifest.entries.count, g + 1, "round \(round) g\(g)")
            }
        }
    }

    func testSummariesMatchAllRecords() throws {
        try publish("g1", files: ["a.txt"])
        try publish("g2", files: ["a.txt", "b.txt"])
        try publish("g3", files: [])
        try store.retire("g2")
        let cache = GenerationCache(store: store)
        _ = cache.summaries() // warm
        try store.delete("g3")

        let summaries = cache.summaries().sorted { $0.transferId < $1.transferId }
        let expected = store.allRecords()
            .map { GenerationSummary(transferId: $0.transferId, isActive: $0.isActive, entryCount: DuoItemModel.entryCount(in: $0)) }
            .sorted { $0.transferId < $1.transferId }
        XCTAssertEqual(summaries, expected)
        XCTAssertEqual(summaries.map(\.transferId), ["g1", "g2"])
        XCTAssertEqual(summaries.map(\.isActive), [true, false])
    }
}
