import XCTest
import FileProvider

/// Records which containers the control path asks the system to re-enumerate,
/// in order - so tests can assert the durable-before-signal contract without a
/// live NSFileProviderManager.
private final class FakeSignal: EnumerationSignaling {
    private(set) var signalled: [NSFileProviderItemIdentifier] = []
    func signalEnumerator(for container: NSFileProviderItemIdentifier) {
        signalled.append(container)
    }
}

/// T5: `DuoExtensionControlService` wiring of the change journal and enumerator
/// signals - publish appends update changes and signals, retire signals the
/// root only, delete tombstones (never physically deletes), and a failed
/// durable write blocks the signal and acks false (durable-before-signal).
final class ControlServiceWiringTests: XCTestCase {
    private var tmpDir: URL!
    private var store: ReplicaStore!
    private var journal: ChangeJournal!

    override func setUpWithError() throws {
        try super.setUpWithError()
        tmpDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("ControlWiringTests-\(UUID().uuidString)", isDirectory: true)
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

    private func goldenFixtureData() throws -> Data {
        let url = try XCTUnwrap(
            Bundle(for: type(of: self)).url(forResource: "generation_record", withExtension: "json")
        )
        return try Data(contentsOf: url)
    }

    func testPublishAppendsUpdateChangeAndSignalsWorkingSetThenRoot() throws {
        let signal = FakeSignal()
        let service = DuoExtensionControlService(store: store, journal: journal, signal: signal)
        var ack = false

        service.publishGeneration(try goldenFixtureData()) { gotAck, _ in ack = gotAck }

        XCTAssertTrue(ack)
        let changes = journal.changes(after: 0)
        XCTAssertEqual(changes.map(\.kind), [.update])
        XCTAssertTrue(changes[0].itemIdentifiers.contains("abc123"), "the container id must be in the update batch")
        XCTAssertEqual(signal.signalled, [.workingSet, .rootContainer])
    }

    func testRetireSignalsRootOnlyAndDoesNotTouchTheJournal() throws {
        try store.publish(recordJSON: try goldenFixtureData())
        let headBefore = journal.headRevision()
        let signal = FakeSignal()
        let service = DuoExtensionControlService(store: store, journal: journal, signal: signal)

        service.retireGeneration("abc123") { _, _ in }

        XCTAssertEqual(signal.signalled, [.rootContainer], "retire changes only the root LIST")
        XCTAssertEqual(journal.headRevision(), headBefore, "retire must not append to the journal")
    }

    func testDeleteTombstonesAndSignalsWorkingSetThenRoot() throws {
        try store.publish(recordJSON: try goldenFixtureData())
        let signal = FakeSignal()
        let service = DuoExtensionControlService(store: store, journal: journal, signal: signal)

        service.deleteGeneration("abc123") { _, _ in }

        XCTAssertNotNil(store.record(for: "abc123"), "delete must tombstone, not physically remove")
        XCTAssertTrue(journal.tombstonedTransferIds().contains("abc123"))
        XCTAssertEqual(journal.changes(after: 0).map(\.kind), [.delete])
        XCTAssertEqual(signal.signalled, [.workingSet, .rootContainer])
    }

    /// A publish's update change is a visibility optimisation with a backstop
    /// (enumerateItems lists the record regardless), so a journal write failure
    /// must NOT turn a successful publish - whose durable record is the host's
    /// arm contract - into a phantom rejection + orphan visible generation. The
    /// record is durable and the publish acks true even if the update change
    /// can't be written. (Delete changes, the only deletion channel, stay
    /// mandatory - see testDurableJournalFailureBlocksSignalAndAcksFalse.)
    func testPublishAcksTrueEvenIfUpdateChangeWriteFails() throws {
        let changesDir = tmpDir.appendingPathComponent("journal/changes")
        try FileManager.default.setAttributes([.posixPermissions: 0o500], ofItemAtPath: changesDir.path)
        defer {
            try? FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: changesDir.path)
        }
        let service = DuoExtensionControlService(store: store, journal: journal, signal: FakeSignal())
        var ack = false

        service.publishGeneration(try goldenFixtureData()) { gotAck, _ in ack = gotAck }

        XCTAssertTrue(ack, "publish acks on the durable record; the update change is best-effort")
        XCTAssertNotNil(store.record(for: "abc123"), "the record is durable and visible via enumerateItems")
    }

    /// SIGNAL_ORDERING (rule 6): the durable journal/tombstone write happens
    /// BEFORE any signal. If it fails, the signal is NOT sent, the op acks
    /// false, and nothing is deleted - File Provider is never told about a
    /// change that did not reach stable storage.
    func testDurableJournalFailureBlocksSignalAndAcksFalse() throws {
        try store.publish(recordJSON: try goldenFixtureData())
        let changesDir = tmpDir.appendingPathComponent("journal/changes")
        try FileManager.default.setAttributes([.posixPermissions: 0o500], ofItemAtPath: changesDir.path)
        defer {
            try? FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: changesDir.path)
        }
        let signal = FakeSignal()
        let service = DuoExtensionControlService(store: store, journal: journal, signal: signal)
        var ack = true
        var replyError: Error?

        service.deleteGeneration("abc123") { gotAck, error in ack = gotAck; replyError = error }

        XCTAssertFalse(ack, "a failed durable write must ack false")
        XCTAssertTrue(signal.signalled.isEmpty, "no signal may fire when the durable write failed")
        XCTAssertNotNil(store.record(for: "abc123"), "nothing is deleted on a failed op")
        let nsError = try XCTUnwrap(replyError as NSError?)
        XCTAssertEqual(nsError.code, 6) // DuoFPErrorDiskFull
    }
}
