import FileProvider
import XCTest

final class FinderBurstDownloadCoordinatorTests: XCTestCase {
    private let entries = [
        DuoManifestEntry(path: "a.txt", kind: "file", size: 1, mtimeNs: 1),
        DuoManifestEntry(path: "b.txt", kind: "file", size: 1, mtimeNs: 1),
        DuoManifestEntry(path: "c.txt", kind: "file", size: 1, mtimeNs: 1),
        DuoManifestEntry(path: "folder", kind: "directory", size: 0, mtimeNs: 1),
        DuoManifestEntry(path: "folder/d.txt", kind: "file", size: 1, mtimeNs: 1),
    ]

    func testSecondDistinctFinderFetchRequestsEveryRemainingFileOnce() {
        let coordinator = FinderBurstDownloadCoordinator()

        XCTAssertEqual(
            coordinator.downloadsAfterFetch(
                transferId: "generation", index: 0, entries: entries,
                isFileViewerRequest: true
            ),
            []
        )
        let downloads = coordinator.downloadsAfterFetch(
            transferId: "generation", index: 1, entries: entries,
            isFileViewerRequest: true
        )

        XCTAssertEqual(downloads.map(\.rawValue), ["generation:2", "generation:4"])
        XCTAssertEqual(
            coordinator.downloadsAfterFetch(
                transferId: "generation", index: 2, entries: entries,
                isFileViewerRequest: true
            ),
            []
        )
    }

    func testRepeatedFetchOfSameItemDoesNotTriggerBurst() {
        let coordinator = FinderBurstDownloadCoordinator()

        XCTAssertEqual(
            coordinator.downloadsAfterFetch(
                transferId: "generation", index: 0, entries: entries,
                isFileViewerRequest: true
            ),
            []
        )
        XCTAssertEqual(
            coordinator.downloadsAfterFetch(
                transferId: "generation", index: 0, entries: entries,
                isFileViewerRequest: true
            ),
            []
        )
    }

    func testCompletedFinderPasteRearmsBurstForSameGeneration() {
        let coordinator = FinderBurstDownloadCoordinator()

        XCTAssertEqual(
            coordinator.downloadsAfterFetch(
                transferId: "generation", index: 0, entries: entries,
                isFileViewerRequest: true
            ),
            []
        )
        XCTAssertEqual(
            coordinator.downloadsAfterFetch(
                transferId: "generation", index: 1, entries: entries,
                isFileViewerRequest: true
            ).map(\.rawValue),
            ["generation:2", "generation:4"]
        )
        XCTAssertEqual(
            coordinator.downloadsAfterFetch(
                transferId: "generation", index: 2, entries: entries,
                isFileViewerRequest: true
            ),
            []
        )
        XCTAssertEqual(
            coordinator.downloadsAfterFetch(
                transferId: "generation", index: 4, entries: entries,
                isFileViewerRequest: true
            ),
            []
        )

        XCTAssertEqual(
            coordinator.downloadsAfterFetch(
                transferId: "generation", index: 0, entries: entries,
                isFileViewerRequest: true
            ),
            []
        )
        XCTAssertEqual(
            coordinator.downloadsAfterFetch(
                transferId: "generation", index: 1, entries: entries,
                isFileViewerRequest: true
            ).map(\.rawValue),
            ["generation:2", "generation:4"]
        )
    }

    func testNonFinderFetchDoesNotCountTowardBurstThreshold() {
        let coordinator = FinderBurstDownloadCoordinator()

        XCTAssertEqual(
            coordinator.downloadsAfterFetch(
                transferId: "generation", index: 0, entries: entries,
                isFileViewerRequest: false
            ),
            []
        )
        XCTAssertEqual(
            coordinator.downloadsAfterFetch(
                transferId: "generation", index: 1, entries: entries,
                isFileViewerRequest: true
            ),
            []
        )
        XCTAssertEqual(
            coordinator.downloadsAfterFetch(
                transferId: "generation", index: 2, entries: entries,
                isFileViewerRequest: true
            ).map(\.rawValue),
            ["generation:0", "generation:4"]
        )
    }
}
