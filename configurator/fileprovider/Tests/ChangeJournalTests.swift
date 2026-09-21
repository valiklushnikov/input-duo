import XCTest

/// `ChangeJournal` (T3): durable, monotonic namespace-change log backing the
/// working-set sync anchor and deletion reconciliation. Metadata only. Every
/// test gets its own temp directory - never the real sandbox container.
/// Durable ops (`append`/`recordTombstone`) throw on write failure (T5) so the
/// control path never signals a change that did not reach stable storage.
final class ChangeJournalTests: XCTestCase {
    private var tmpDir: URL!
    private var journal: ChangeJournal!

    override func setUpWithError() throws {
        try super.setUpWithError()
        tmpDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("ChangeJournalTests-\(UUID().uuidString)", isDirectory: true)
        journal = ChangeJournal(baseDirectory: tmpDir)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: tmpDir)
        journal = nil
        tmpDir = nil
        try super.tearDownWithError()
    }

    func testEmptyJournalHasZeroHeadAndNoChanges() {
        XCTAssertEqual(journal.headRevision(), 0)
        XCTAssertTrue(journal.changes(after: 0).isEmpty)
    }

    func testAppendIncrementsHeadAndIsReadable() throws {
        let rev = try journal.append(kind: .update, itemIdentifiers: ["gen:0", "gen:1"])
        XCTAssertEqual(rev, 1)
        XCTAssertEqual(journal.headRevision(), 1)
        let changes = journal.changes(after: 0)
        XCTAssertEqual(changes.count, 1)
        XCTAssertEqual(changes[0].revision, 1)
        XCTAssertEqual(changes[0].kind, .update)
        XCTAssertEqual(changes[0].itemIdentifiers, ["gen:0", "gen:1"])
    }

    func testChangesAfterFiltersByRevision() throws {
        let first = try journal.append(kind: .update, itemIdentifiers: ["a:0"])
        let second = try journal.append(kind: .delete, itemIdentifiers: ["a:0"])
        XCTAssertEqual(journal.headRevision(), second)
        let afterFirst = journal.changes(after: first)
        XCTAssertEqual(afterFirst.map(\.revision), [second])
        XCTAssertEqual(afterFirst[0].kind, .delete)
    }

    func testChangesAreOrderedAscendingByRevision() throws {
        for i in 0..<5 { _ = try journal.append(kind: .update, itemIdentifiers: ["x:\(i)"]) }
        let revisions = journal.changes(after: 0).map(\.revision)
        XCTAssertEqual(revisions, [1, 2, 3, 4, 5])
    }

    func testHeadIsMonotonicAcrossRestart() throws {
        _ = try journal.append(kind: .update, itemIdentifiers: ["a:0"])
        _ = try journal.append(kind: .update, itemIdentifiers: ["b:0"])

        let restarted = ChangeJournal(baseDirectory: tmpDir)
        XCTAssertEqual(restarted.headRevision(), 2)
        let next = try restarted.append(kind: .delete, itemIdentifiers: ["a:0"])
        XCTAssertEqual(next, 3, "revision must continue past the persisted head, never reset")
    }

    func testChangesReplayableAcrossRestart() throws {
        _ = try journal.append(kind: .update, itemIdentifiers: ["a:0"])
        let del = try journal.append(kind: .delete, itemIdentifiers: ["a:0"])

        let restarted = ChangeJournal(baseDirectory: tmpDir)
        let replay = restarted.changes(after: del - 1)
        XCTAssertEqual(replay.map(\.revision), [del])
        XCTAssertEqual(replay[0].kind, .delete)
        XCTAssertEqual(replay[0].itemIdentifiers, ["a:0"])
    }

    func testRecordTombstoneWritesMarkerAndDeleteChange() throws {
        let rev = try journal.recordTombstone(transferId: "gen", itemIdentifiers: ["gen", "gen:0", "gen:1"])
        XCTAssertEqual(journal.headRevision(), rev)
        XCTAssertTrue(journal.tombstonedTransferIds().contains("gen"))
        let changes = journal.changes(after: rev - 1)
        XCTAssertEqual(changes.count, 1)
        XCTAssertEqual(changes[0].kind, .delete)
        XCTAssertEqual(changes[0].itemIdentifiers, ["gen", "gen:0", "gen:1"])
    }

    func testTombstoneMarkerSurvivesRestart() throws {
        let rev = try journal.recordTombstone(transferId: "gen", itemIdentifiers: ["gen:0"])

        let restarted = ChangeJournal(baseDirectory: tmpDir)
        XCTAssertTrue(restarted.tombstonedTransferIds().contains("gen"))
        XCTAssertEqual(restarted.changes(after: rev - 1).map(\.kind), [.delete])
    }

    func testObservedRevisionPersistsAndIsMonotonic() {
        journal.noteObserved(5)
        journal.noteObserved(3) // stale/lower must not regress the high-water mark
        XCTAssertEqual(journal.observedRevision(), 5)

        let restarted = ChangeJournal(baseDirectory: tmpDir)
        XCTAssertEqual(restarted.observedRevision(), 5)
    }
}
