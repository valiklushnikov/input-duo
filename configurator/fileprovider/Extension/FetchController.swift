import Foundation
import FileProvider
import Darwin
import os

/// Maps a `DuoFPErrorDomain` error (`Shared/DuoFPErrors.h` - the wire
/// vocabulary the host/Python side emits over the fetch XPC) onto a specific
/// Foundation/`NSFileProviderError`, per spec §16's table (Task 14). Numeric
/// codes are matched directly rather than through the Swift-bridged
/// `DuoFPError` enum case names, for the same reason
/// `DuoExtensionControlService.mapError` gives for doing the same thing:
/// NS_ERROR_ENUM's exact Swift import shape (and `protocol` being a Swift
/// keyword) is not something worth depending on here.
///
/// `.serverUnreachable` is Apple's documented transient/"retry me" code -
/// unlike `.noSuchItem`/`.cannotSynchronize`, returning it from a fetch does
/// NOT cause Finder to evict or delete the dataless placeholder. Every code
/// that means "the connection to the sender is gone right now" - peerLost,
/// timeout, notConnected (host down) - maps here, on purpose (task-14 brief
/// ruling #2): those must never look like a deletion.
///
/// Anything that is not a `DuoFPErrorDomain` error (the literal
/// `NSUserCancelledError` `FetchOperation`'s `cancellationHandler` builds, or
/// a genuine local POSIX/Cocoa error from writing the temp file - e.g. a
/// truly full LOCAL disk already surfaces as `NSPOSIXErrorDomain`/`ENOSPC`
/// on its own) is passed through unchanged rather than re-wrapped: it is
/// already a specific, meaningful Foundation error, not a generic one.
enum ErrorMap {
    static func toNSFileProviderError(_ error: Error) -> NSError {
        let nsError = error as NSError
        guard nsError.domain == DuoFPErrorDomain else {
            return nsError
        }
        switch nsError.code {
        case 1: // DuoFPErrorSourceMissing
            return NSError(domain: NSFileProviderErrorDomain,
                            code: NSFileProviderError.noSuchItem.rawValue)
        case 2: // DuoFPErrorSourceChanged
            return NSError(domain: NSFileProviderErrorDomain,
                            code: NSFileProviderError.cannotSynchronize.rawValue)
        case 3: // DuoFPErrorPeerLost
            return NSError(domain: NSFileProviderErrorDomain,
                            code: NSFileProviderError.serverUnreachable.rawValue)
        case 4: // DuoFPErrorUnauthorized
            return NSError(domain: NSFileProviderErrorDomain,
                            code: NSFileProviderError.notAuthenticated.rawValue)
        case 5: // DuoFPErrorTimeout
            return NSError(domain: NSFileProviderErrorDomain,
                            code: NSFileProviderError.serverUnreachable.rawValue)
        case 6: // DuoFPErrorDiskFull
            return NSError(domain: NSPOSIXErrorDomain, code: Int(ENOSPC))
        case 8: // DuoFPErrorNotConnected (host down)
            return NSError(domain: NSFileProviderErrorDomain,
                            code: NSFileProviderError.serverUnreachable.rawValue)
        default: // 7 DuoFPErrorProtocol, and any future/unrecognized code
            return NSError(domain: NSFileProviderErrorDomain,
                            code: NSFileProviderError.cannotSynchronize.rawValue)
        }
    }
}

/// Task 17: the correlation-id log chain for one fetch - `transfer_id`/
/// `entry_index` (parsed from the item identifier, `"<transfer_id>:<index>"`
/// - see `DuoItemModel.parse`) plus `fetch_token`/`read_id` once those exist.
/// Every field logged here is an id, a count, or an enum-ish label - NEVER a
/// path (`item.filename`, `url.path`) and NEVER chunk bytes, mirroring the
/// Python side's privacy invariant (task-17 brief ruling #1 / spec §15,§28).
/// Ids are marked `.public` on purpose: they carry no user path/content, and
/// a redacted correlation id is useless for the log chain this task exists
/// to add.
private let fetchLog = Logger(subsystem: "com.duoinput.configurator.fileprovider", category: "fetch")

/// Each operation owns one temp file and serializes all XPC callbacks.
final class FetchController {
    typealias HostProvider = (@escaping (Error) -> Void) -> DuoHostCallback?
    private let hostProvider: HostProvider
    private let temporaryDirectory: URL

    init(hostProvider: @escaping HostProvider,
         temporaryDirectory: URL = FileManager.default.temporaryDirectory) {
        self.hostProvider = hostProvider
        self.temporaryDirectory = temporaryDirectory
    }

    func fetch(_ item: DuoItem, request: NSFileProviderRequest,
               completion: @escaping (URL?, NSFileProviderItem?, Error?) -> Void) -> Progress {
        let parsed = DuoItemModel.parse(item.itemIdentifier)
        fetchLog.info("FETCH_ENTER transfer_id=\(parsed?.transferId ?? "?", privacy: .public) entry_index=\(parsed?.index ?? -1, privacy: .public)")
        let operation = FetchOperation(item: item, directory: temporaryDirectory, completion: completion)
        operation.start(hostProvider)
        return operation.progress
    }
}

private final class FetchOperation {
    let progress: Progress
    private let item: DuoItem
    private let directory: URL
    private let completion: (URL?, NSFileProviderItem?, Error?) -> Void
    private let queue = DispatchQueue(label: "com.duoinput.fileprovider.fetch")
    private var host: DuoHostCallback?
    private var token: String?
    private var file: FileHandle?
    private var url: URL?
    private var finished = false
    private var opened = false
    private var pullSequence = 0
    private var offset: Int64 = 0
    //: Correlation ids for the log chain (task 17) - filled in once `start()`
    //: parses the item identifier. Never a path/filename, only the ids.
    private var transferId: String?
    private var entryIndex: Int?

    init(item: DuoItem, directory: URL, completion: @escaping (URL?, NSFileProviderItem?, Error?) -> Void) {
        self.item = item
        self.directory = directory
        self.completion = completion
        progress = Progress(totalUnitCount: item.documentSize?.int64Value ?? 0)
    }

    private func error(_ code: Int) -> NSError { NSError(domain: DuoFPErrorDomain, code: code) }

    func start(_ provider: @escaping FetchController.HostProvider) {
        progress.cancellationHandler = { [weak self] in
            guard let self else { return }
            // Task 12: Finder cancel -> Progress.cancellationHandler -> here.
            // finish(error) below already does the rest generically (settle
            // once, remove temp, tell the host via cancelFetch(token)) - see
            // finish(_:) - so this handler only needs to supply the literal
            // NSUserCancelledError the plan mandates.
            self.queue.async {
                self.finish(NSError(domain: NSCocoaErrorDomain, code: NSUserCancelledError))
            }
        }
        queue.async {
            guard !self.progress.isCancelled,
                  let parsed = DuoItemModel.parse(self.item.itemIdentifier), let index = parsed.index,
                  index >= 0, let size = self.item.documentSize?.int64Value, size >= 0 else {
                self.finish(self.error(7)); return
            }
            self.transferId = parsed.transferId
            self.entryIndex = index
            self.host = provider { error in self.queue.async { self.finish(error) } }
            guard let host = self.host else { self.finish(self.error(8)); return }
            host.openFetch(parsed.transferId, entryId: NSNumber(value: index)) { token, total, error in
                self.queue.async {
                    guard !self.opened else { return }
                    self.opened = true
                    if self.finished {
                        if let token { host.cancelFetch(token) }
                        return
                    }
                    self.token = token
                    fetchLog.info("fp_fetch_started transfer_id=\(parsed.transferId, privacy: .public) entry_index=\(index, privacy: .public) fetch_token=\(token ?? "?", privacy: .public)")
                    if let error { self.finish(error); return }
                    guard let token, !token.isEmpty, total?.int64Value == size else {
                        self.finish(self.error(7)); return
                    }
                    do {
                        try FileManager.default.createDirectory(at: self.directory, withIntermediateDirectories: true)
                        let url = self.directory.appendingPathComponent(UUID().uuidString)
                        let fd = Darwin.open(url.path, O_WRONLY | O_CREAT | O_EXCL, 0o600)
                        guard fd >= 0 else { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
                        self.url = url
                        self.file = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
                        if size == 0 {
                            // Zero-byte fetch: the empty temp IS the whole
                            // contents - no pullChunk round trip needed.
                            try self.file?.synchronize()
                            try self.file?.close()
                            self.file = nil
                            self.finish(nil)
                        } else {
                            self.pull()
                        }
                    } catch { self.finish(error) }
                }
            }
        }
    }

    private func pull() {
        guard !finished, let token, let host else { return }
        pullSequence += 1
        let sequence = pullSequence
        host.pullChunk(token) { chunk, eof, error in
            self.queue.async {
                guard !self.finished, self.pullSequence == sequence else { return }
                self.pullSequence += 1
                if let error { self.finish(error); return }
                guard let chunk, chunk.count <= 1_048_576,
                      Int64(chunk.count) <= self.progress.totalUnitCount - self.offset,
                      (!chunk.isEmpty || (eof && self.offset == self.progress.totalUnitCount)),
                      eof == (self.offset + Int64(chunk.count) == self.progress.totalUnitCount) else {
                    self.finish(self.error(7)); return
                }
                do {
                    try self.file?.write(contentsOf: chunk)
                    self.offset += Int64(chunk.count)
                    self.progress.completedUnitCount = self.offset
                    // fp_bytes_received: the byte COUNT only, never the chunk itself.
                    fetchLog.info("fp_bytes_received transfer_id=\(self.transferId ?? "?", privacy: .public) entry_index=\(self.entryIndex ?? -1, privacy: .public) fetch_token=\(token, privacy: .public) bytes=\(chunk.count, privacy: .public)")
                    if eof {
                        try self.file?.synchronize()
                        try self.file?.close()
                        self.file = nil
                        self.finish(nil)
                    } else { self.pull() }
                } catch { self.finish(error) }
            }
        }
    }

    private func finish(_ error: Error?) {
        guard !finished else { return }
        finished = true
        progress.cancellationHandler = nil
        try? file?.close()
        file = nil
        if let error {
            let mapped = ErrorMap.toNSFileProviderError(error)
            // fp_fetch_failed/_cancelled: the mapped NSFileProviderError code
            // only - never the local file's URL/path, never chunk content.
            let nsError = error as NSError
            let event = (nsError.domain == NSCocoaErrorDomain && nsError.code == NSUserCancelledError)
                ? "fp_fetch_cancelled" : "fp_fetch_failed"
            fetchLog.info("\(event, privacy: .public) transfer_id=\(self.transferId ?? "?", privacy: .public) entry_index=\(self.entryIndex ?? -1, privacy: .public) fetch_token=\(self.token ?? "?", privacy: .public) code=\(mapped.code, privacy: .public)")
            if let token { host?.cancelFetch(token) }
            if let url { try? FileManager.default.removeItem(at: url) }
            completion(nil, nil, mapped)
        } else {
            fetchLog.info("fp_fetch_completed transfer_id=\(self.transferId ?? "?", privacy: .public) entry_index=\(self.entryIndex ?? -1, privacy: .public) fetch_token=\(self.token ?? "?", privacy: .public)")
            completion(url, item, nil)
        }
        host = nil
    }
}
