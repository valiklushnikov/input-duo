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
        let endpoint = try DuoServiceSource().makeListenerEndpoint()
        XCTAssertNotNil(endpoint)
    }
}
