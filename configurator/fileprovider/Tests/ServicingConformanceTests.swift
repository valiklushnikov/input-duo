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
}
