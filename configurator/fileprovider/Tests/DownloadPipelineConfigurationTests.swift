import XCTest

final class DownloadPipelineConfigurationTests: XCTestCase {
    func testExtensionAdvertisesFourConcurrentDownloadPipelines() throws {
        let extensionInfo = try XCTUnwrap(
            Bundle(for: FileProviderExtension.self)
                .object(forInfoDictionaryKey: "NSExtension") as? [String: Any]
        )

        XCTAssertEqual(
            extensionInfo["NSExtensionFileProviderDownloadPipelineDepth"] as? Int,
            4
        )
    }
}
