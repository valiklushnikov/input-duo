import XCTest
import FileProvider

private final class CannedHost: NSObject, DuoHostCallback {
    var chunks: [(Data?, Bool, Error?)] = [(Data("ab".utf8), false, nil), (Data("c".utf8), true, nil)]
    var cancelled = [String]()
    var totalSize: NSNumber = 3
    func openFetch(_ generationId: String, entryId: NSNumber, reply: @escaping (String?, NSNumber?, Error?) -> Void) {
        reply("token", totalSize, nil)
    }
    func pullChunk(_ fetchToken: String, reply: @escaping (Data?, Bool, Error?) -> Void) {
        let next = chunks.removeFirst()
        reply(next.0, next.1, next.2)
    }
    func cancelFetch(_ fetchToken: String) { cancelled.append(fetchToken) }
}

final class FetchControllerTests: XCTestCase {
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
        let controller = FetchController(hostProvider: { _ in host }, temporaryDirectory: directory)
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
            XCTAssertEqual((error as NSError?)?.code, 7)
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
        try store.retire("generation")
        _ = provider.fetchContents(for: NSFileProviderItemIdentifier("generation:0"), version: nil, request: NSFileProviderRequest()) { url, _, error in
            XCTAssertNil(url)
            XCTAssertNotNil(error)
        }
        XCTAssertEqual(connections, 1)
    }
}
