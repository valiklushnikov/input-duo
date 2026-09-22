import XCTest

final class MaterializedSetQueryCoalescerTests: XCTestCase {
    func testTraceDistinguishesPhysicalQueryFromCoalescedCallers() {
        var tick: UInt64 = 0
        var lines: [String] = []
        let perf = PerfTrace(clock: { tick += 1; return tick }, emit: { lines.append($0) })
        var finishPhysicalQuery: ((Set<String>) -> Void)?
        let coalescer = MaterializedSetQueryCoalescer(perf: perf) { completion in
            finishPhysicalQuery = completion
        }

        coalescer.query { _ in }
        coalescer.query { _ in }
        coalescer.query { _ in }
        finishPhysicalQuery?(["gen:0", "gen:1"])

        XCTAssertEqual(lines.count, 4)
        XCTAssertTrue(lines[0].contains("event=materialized_query_started"))
        XCTAssertTrue(lines[1].contains("event=materialized_query_coalesced") && lines[1].contains("pending_callers=2"))
        XCTAssertTrue(lines[2].contains("event=materialized_query_coalesced") && lines[2].contains("pending_callers=3"))
        XCTAssertTrue(lines[3].contains("event=materialized_query_completed") && lines[3].contains("callers=3") && lines[3].contains("item_count=2"))
    }

    func testConcurrentCallersShareOnePhysicalQueryAndReceiveItsSnapshot() {
        var physicalQueryCount = 0
        var finishPhysicalQuery: ((Set<String>) -> Void)?
        let coalescer = MaterializedSetQueryCoalescer { completion in
            physicalQueryCount += 1
            finishPhysicalQuery = completion
        }
        var first: Set<String>?
        var second: Set<String>?

        coalescer.query { first = $0 }
        coalescer.query { second = $0 }

        XCTAssertEqual(physicalQueryCount, 1)
        XCTAssertNil(first)
        XCTAssertNil(second)

        finishPhysicalQuery?(["gen:0", "gen:1"])

        XCTAssertEqual(first, ["gen:0", "gen:1"])
        XCTAssertEqual(second, ["gen:0", "gen:1"])
    }

    func testCallerAfterCompletionStartsFreshPhysicalQuery() {
        var physicalQueryCount = 0
        var physicalCompletions: [(Set<String>) -> Void] = []
        let coalescer = MaterializedSetQueryCoalescer { completion in
            physicalQueryCount += 1
            physicalCompletions.append(completion)
        }
        var snapshots: [Set<String>] = []

        coalescer.query { snapshots.append($0) }
        physicalCompletions[0](["gen:0"])
        coalescer.query { snapshots.append($0) }

        XCTAssertEqual(physicalQueryCount, 2)
        XCTAssertEqual(snapshots, [["gen:0"]])

        physicalCompletions[1](["gen:1"])
        XCTAssertEqual(snapshots, [["gen:0"], ["gen:1"]])
    }
}
