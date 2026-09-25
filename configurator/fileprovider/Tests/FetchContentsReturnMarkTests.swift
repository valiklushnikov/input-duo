import FileProvider
import XCTest

/// Finder post-fetch tail diagnostics: `fetch_contents_returned` marks the
/// moment the fetchContents completionHandler has RETURNED (T7), strictly after
/// `fetch_contents_complete`/`completion_call` (logged just before it is
/// invoked, T6). Passive: exactly one mark per fetchContents call, emitted
/// after the handler ran, completion ordering unchanged.
final class FetchContentsReturnMarkTests: XCTestCase {
    func testReturnMarkIsEmittedOnceAfterCompletionHandlerReturns() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        var lines: [String] = []
        var handlerRanAtLine: Int?
        let perf = PerfTrace(clock: { 1 }, emit: { lines.append($0) })
        let domain = NSFileProviderDomain(
            identifier: NSFileProviderDomainIdentifier("com.duoinput.configurator.fileprovider.tests.returnmark"),
            displayName: "Duo Input Tests"
        )
        let provider = FileProviderExtension(
            domain: domain,
            replicaStore: ReplicaStore(baseDirectory: directory),
            temporaryDirectory: directory,
            perf: perf
        )
        defer { provider.invalidate() }

        _ = provider.fetchContents(
            for: NSFileProviderItemIdentifier("absent-transfer:0"),
            version: nil,
            request: NSFileProviderRequest()
        ) { _, _, error in
            XCTAssertNotNil(error)
            handlerRanAtLine = lines.count
        }

        let events = lines.compactMap { line -> String? in
            line.split(separator: " ").first { $0.hasPrefix("event=") }.map { String($0.dropFirst(6)) }
        }
        XCTAssertEqual(events.filter { $0 == "fetch_contents_returned" }.count, 1)
        let returnedIndex = try XCTUnwrap(events.firstIndex(of: "fetch_contents_returned"))
        let callIndex = try XCTUnwrap(events.firstIndex(of: "completion_call"))
        XCTAssertLessThan(callIndex, returnedIndex)
        // emitted only after the handler body ran
        XCTAssertGreaterThanOrEqual(returnedIndex, try XCTUnwrap(handlerRanAtLine))
    }
}
