import XCTest
import FileProvider

private final class ServiceTestHost: NSObject, DuoHostCallback {
    func openFetch(_ generationId: String, entryId: NSNumber, reply: @escaping (String?, NSNumber?, Error?) -> Void) {
        reply("native-token", 3, nil)
    }
    func pullChunk(_ fetchToken: String, reply: @escaping (Data?, Bool, Error?) -> Void) {
        reply(Data("abc".utf8), true, nil)
    }
    func cancelFetch(_ fetchToken: String) {}
}

final class ServiceSourceTests: XCTestCase {
    func testAnonymousXPCPublishesAndFetchesThroughTheRetainedHostProxy() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = ReplicaStore(baseDirectory: directory.appendingPathComponent("replica"))
        let source = DuoServiceSource(store: store, journal: ChangeJournal(baseDirectory: store.baseDirectory), peerVerifier: { _ in true })
        defer { source.invalidate() }
        let connection = NSXPCConnection(listenerEndpoint: try source.makeListenerEndpoint())
        connection.exportedInterface = DuoXPC.hostCallbackInterface()
        connection.exportedObject = ServiceTestHost()
        connection.remoteObjectInterface = DuoXPC.extensionControlInterface()
        connection.resume()
        defer { connection.invalidate() }
        let published = expectation(description: "published over XPC")
        let control = try XCTUnwrap(connection.remoteObjectProxyWithErrorHandler { error in
            XCTFail("XPC error: \(error)")
            published.fulfill()
        } as? DuoExtensionControl)
        let fixture = try Data(contentsOf: XCTUnwrap(Bundle(for: Self.self).url(forResource: "generation_record", withExtension: "json")))
        control.publishGeneration(fixture) { ack, error in
            XCTAssertTrue(ack)
            XCTAssertNil(error)
            published.fulfill()
        }
        wait(for: [published], timeout: 5)
        let item = DuoItem(itemIdentifier: NSFileProviderItemIdentifier("generation:0"), parentItemIdentifier: .rootContainer,
            filename: "file.bin", contentType: .data, documentSize: 3, capabilities: [.allowsReading],
            contentVersion: Data(), metadataVersion: Data())
        let controller = FetchController(hostProvider: { source.hostProxy(errorHandler: $0) }, temporaryDirectory: directory)
        let fetched = expectation(description: "fetched over XPC")
        _ = controller.fetch(item, request: NSFileProviderRequest()) { url, _, error in
            XCTAssertNil(error)
            XCTAssertEqual(try? Data(contentsOf: XCTUnwrap(url)), Data("abc".utf8))
            fetched.fulfill()
        }
        wait(for: [fetched], timeout: 5)
    }

    func testProductionVerifierFailsClosedWithoutConfiguredSigningTeam() {
        let listener = NSXPCListener.anonymous()
        let connection = NSXPCConnection(listenerEndpoint: listener.endpoint)
        XCTAssertFalse(DuoPeerVerifier.verify(connection))
    }

    func testRejectedPeerGetsNoExportedService() throws {
        let listener = NSXPCListener.anonymous()
        let connection = NSXPCConnection(listenerEndpoint: listener.endpoint)
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let source = DuoServiceSource(store: ReplicaStore(baseDirectory: directory), journal: ChangeJournal(baseDirectory: directory), peerVerifier: { _ in false })
        XCTAssertFalse(source.listener(listener, shouldAcceptNewConnection: connection))
        XCTAssertNil(connection.exportedObject)
    }

    func testAcceptedConnectionPublishesIntoTheSharedReplica() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = ReplicaStore(baseDirectory: directory)
        let source = DuoServiceSource(store: store, journal: ChangeJournal(baseDirectory: store.baseDirectory), peerVerifier: { _ in true })
        let listener = NSXPCListener.anonymous()
        let connection = NSXPCConnection(listenerEndpoint: listener.endpoint)
        XCTAssertTrue(source.listener(listener, shouldAcceptNewConnection: connection))
        defer { connection.invalidate() }
        XCTAssertNotNil(connection.remoteObjectInterface)
        let control = try XCTUnwrap(connection.exportedObject as? DuoExtensionControl)
        let fixture = try Data(contentsOf: XCTUnwrap(Bundle(for: Self.self).url(forResource: "generation_record", withExtension: "json")))
        var acknowledged = false
        control.publishGeneration(fixture) { ack, error in
            acknowledged = ack
            XCTAssertNil(error)
        }
        XCTAssertTrue(acknowledged)
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: fixture) as? [String: Any])
        XCTAssertNotNil(store.record(for: try XCTUnwrap(json["transfer_id"] as? String)))
    }
}
