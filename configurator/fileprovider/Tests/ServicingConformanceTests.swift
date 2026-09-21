import XCTest
import FileProvider

/// Regression guard for the Phase 10.2 root-cause invariant: the extension MUST
/// conform to `NSFileProviderServicing`, otherwise the framework never routes
/// `getService` to `supportedServiceSources(...)` and `getService` returns nil.
/// It must also remain an `NSFileProviderReplicatedExtension`.
///
/// The extension sources are compiled directly into this test bundle (see
/// project.yml source membership), so `FileProviderExtension` is referenced
/// without `@testable import` of the (non-linkable) .appex product.
final class ServicingConformanceTests: XCTestCase {
    func testExtensionConformsToServicing() {
        XCTAssertTrue((FileProviderExtension.self as Any) is NSFileProviderServicing.Type)
    }

    func testExtensionConformsToReplicated() {
        XCTAssertTrue((FileProviderExtension.self as Any) is NSFileProviderReplicatedExtension.Type)
    }

    func testServiceSourceVendsAnonymousEndpoint() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let endpoint = try DuoServiceSource(store: ReplicaStore(baseDirectory: directory), journal: ChangeJournal(baseDirectory: directory)).makeListenerEndpoint()
        XCTAssertNotNil(endpoint)
    }

    func testExtensionReturnsExpectedServiceSourceWithCurrentDependencies() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let domain = NSFileProviderDomain(
            identifier: NSFileProviderDomainIdentifier("com.duoinput.configurator.fileprovider.tests.servicing"),
            displayName: "Duo Input Tests"
        )
        let provider = FileProviderExtension(
            domain: domain,
            replicaStore: ReplicaStore(baseDirectory: directory),
            temporaryDirectory: directory
        )
        defer { provider.invalidate() }

        var returnedSources: [NSFileProviderServiceSource]?
        var returnedError: Error?
        _ = provider.supportedServiceSources(for: .rootContainer) { sources, error in
            returnedSources = sources
            returnedError = error
        }

        XCTAssertNil(returnedError)
        let source = try XCTUnwrap(returnedSources?.only)
        XCTAssertTrue(source is DuoServiceSource)
        XCTAssertEqual(source.serviceName, duoFileProviderServiceName)
    }
}

private extension Collection {
    var only: Element? { count == 1 ? first : nil }
}
