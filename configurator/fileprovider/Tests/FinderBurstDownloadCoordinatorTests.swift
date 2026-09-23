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

    private func fileEntries(_ count: Int) -> [DuoManifestEntry] {
        (0..<count).map {
            DuoManifestEntry(path: "\($0).txt", kind: "file", size: 1, mtimeNs: 1)
        }
    }

    func testProductionConfigurationNeverSchedulesSpeculativeItems() {
        let coordinator = FinderBurstDownloadCoordinator()
        let manyEntries = fileEntries(20)

        XCTAssertEqual(
            coordinator.downloadsAfterFetch(
                transferId: "generation", index: 0, entries: manyEntries,
                isFileViewerRequest: true
            ),
            []
        )
        XCTAssertEqual(
            coordinator.downloadsAfterFetch(
                transferId: "generation", index: 1, entries: manyEntries,
                isFileViewerRequest: true
            ),
            []
        )
        XCTAssertEqual(
            coordinator.downloadsAfterFetch(
                transferId: "generation", index: 2, entries: manyEntries,
                isFileViewerRequest: true
            ),
            []
        )
        XCTAssertEqual(
            coordinator.downloadsAfterFetch(
                transferId: "generation", index: 3, entries: manyEntries,
                isFileViewerRequest: true
            ),
            []
        )
    }

    func testGenuineDemandAfterFirstWaveAdvancesOneMoreBoundedWave() {
        let coordinator = FinderBurstDownloadCoordinator(burstWaveSize: 1)
        let manyEntries = fileEntries(20)

        _ = coordinator.downloadsAfterFetch(
            transferId: "generation", index: 0, entries: manyEntries,
            isFileViewerRequest: true
        )
        let firstWave = coordinator.downloadsAfterFetch(
            transferId: "generation", index: 1, entries: manyEntries,
            isFileViewerRequest: true
        )
        XCTAssertEqual(firstWave.map(\.rawValue), ["generation:2"])
        XCTAssertEqual(
            coordinator.downloadsAfterFetch(
                transferId: "generation", index: 2, entries: manyEntries,
                isFileViewerRequest: true
            ),
            []
        )

        let secondWave = coordinator.downloadsAfterFetch(
            transferId: "generation", index: 3, entries: manyEntries,
            isFileViewerRequest: true
        )
        XCTAssertEqual(secondWave.map(\.rawValue), ["generation:4"])
    }

    func testBurstFetchesCannotRecursivelyTriggerAnotherWave() {
        let coordinator = FinderBurstDownloadCoordinator(burstWaveSize: 1)
        let manyEntries = fileEntries(20)

        _ = coordinator.beginFetch(
            transferId: "generation", index: 0, entries: manyEntries,
            isFileViewerRequest: true
        )
        let trigger = coordinator.beginFetch(
            transferId: "generation", index: 1, entries: manyEntries,
            isFileViewerRequest: true
        )
        XCTAssertEqual(trigger.downloads.count, 1)

        var burstContexts: [FinderBurstDownloadCoordinator.FetchContext] = []
        for download in trigger.downloads {
            XCTAssertTrue(coordinator.markBurstRequestIssued(download))
            let callback = coordinator.beginFetch(
                transferId: "generation", index: download.index, entries: manyEntries,
                isFileViewerRequest: true
            )
            XCTAssertEqual(callback.context.origin, .burst)
            XCTAssertEqual(callback.context.waveId, trigger.downloads.first?.waveId)
            XCTAssertTrue(callback.downloads.isEmpty)
            burstContexts.append(callback.context)
        }

        for context in burstContexts {
            coordinator.completeFetch(context)
        }
        let nextGenuineDemand = coordinator.beginFetch(
            transferId: "generation", index: 3, entries: manyEntries,
            isFileViewerRequest: true
        )
        XCTAssertEqual(nextGenuineDemand.downloads.first?.waveId, 2)
    }

    func testOverlappingGenuineDemandCannotOpenAnotherWave() {
        let coordinator = FinderBurstDownloadCoordinator(burstWaveSize: 1)
        let manyEntries = fileEntries(24)
        _ = coordinator.beginFetch(
            transferId: "generation", index: 0, entries: manyEntries,
            isFileViewerRequest: true
        )
        let trigger = coordinator.beginFetch(
            transferId: "generation", index: 1, entries: manyEntries,
            isFileViewerRequest: true
        )
        var active: [FinderBurstDownloadCoordinator.FetchContext] = []
        for download in trigger.downloads {
            XCTAssertTrue(coordinator.markBurstRequestIssued(download))
            active.append(coordinator.beginFetch(
                transferId: "generation", index: download.index, entries: manyEntries,
                isFileViewerRequest: true
            ).context)
        }

        let overlapping = coordinator.beginFetch(
            transferId: "generation", index: 3, entries: manyEntries,
            isFileViewerRequest: true
        )
        XCTAssertTrue(overlapping.downloads.isEmpty)

        coordinator.completeFetch(overlapping.context)
        for context in active { coordinator.completeFetch(context) }
        let later = coordinator.beginFetch(
            transferId: "generation", index: 4, entries: manyEntries,
            isFileViewerRequest: true
        )
        XCTAssertEqual(later.downloads.map(\.index), [5])
    }

    func testFinderDemandTakesOwnershipBeforeBurstRequestIsIssued() {
        let coordinator = FinderBurstDownloadCoordinator(burstWaveSize: 1)
        let manyEntries = fileEntries(20)
        _ = coordinator.beginFetch(
            transferId: "generation", index: 0, entries: manyEntries,
            isFileViewerRequest: true
        )
        let trigger = coordinator.beginFetch(
            transferId: "generation", index: 1, entries: manyEntries,
            isFileViewerRequest: true
        )
        let raced = trigger.downloads[0]

        let finder = coordinator.beginFetch(
            transferId: "generation", index: raced.index, entries: manyEntries,
            isFileViewerRequest: true
        )

        XCTAssertEqual(finder.context.origin, .finder)
        XCTAssertEqual(finder.downloads.map(\.index), [3])
        XCTAssertFalse(coordinator.markBurstRequestIssued(raced))
    }

    func testSecondDistinctFinderFetchRequestsOnlyNextFile() {
        let coordinator = FinderBurstDownloadCoordinator(burstWaveSize: 1)

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

        XCTAssertEqual(downloads.map(\.rawValue), ["generation:2"])
        XCTAssertEqual(
            coordinator.downloadsAfterFetch(
                transferId: "generation", index: 2, entries: entries,
                isFileViewerRequest: true
            ),
            []
        )
    }

    func testRepeatedFetchOfSameItemDoesNotTriggerBurst() {
        let coordinator = FinderBurstDownloadCoordinator(burstWaveSize: 1)

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
        let coordinator = FinderBurstDownloadCoordinator(burstWaveSize: 1)

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
            ["generation:2"]
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
            ["generation:2"]
        )
    }

    func testNonFinderFetchDoesNotCountTowardBurstThreshold() {
        let coordinator = FinderBurstDownloadCoordinator(burstWaveSize: 1)

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
            ["generation:4"]
        )
    }
}
