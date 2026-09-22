import XCTest

final class PerfTraceTests: XCTestCase {
    func testMarkUsesInjectedUptimeAndStableFields() {
        var lines: [String] = []
        let trace = PerfTrace(clock: { 123_456_789 }, emit: { lines.append($0) })

        let stamp = trace.mark("fetch_enter", fields: [
            ("transfer_id", "generation"), ("entry_index", "2")
        ])

        XCTAssertEqual(stamp, 123_456_789)
        XCTAssertEqual(lines, [
            "fp_perf event=fetch_enter mono_ns=123456789 clock=swift_uptime " +
            "entry_index=2 transfer_id=generation"
        ])
    }

    func testInvalidAtomsAreMarkedInsteadOfEmittedRaw() {
        var lines: [String] = []
        let trace = PerfTrace(clock: { 7 }, emit: { lines.append($0) })

        trace.mark("bad event", fields: [("value", "line\nbreak")])

        XCTAssertEqual(
            lines,
            ["fp_perf event=invalid_atom mono_ns=7 clock=swift_uptime value=invalid_atom"]
        )
    }
}
