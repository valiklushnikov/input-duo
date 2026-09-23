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
    /// Deferred scheduler for fetch retries (a real timer in production, a
    /// controllable one in tests). Never a busy loop.
    typealias Scheduler = (TimeInterval, @escaping () -> Void) -> Void

    /// Bounded retry for TRANSIENT host-unreachable fetch failures (peerLost/
    /// timeout/notConnected - DuoFP codes 3/5/8). The peer TCP link drops and
    /// auto-reconnects within seconds; without this a single drop fails every
    /// in-flight fetch and Finder aborts the whole folder copy. Retrying across
    /// the reconnect lets the copy survive it. Non-transient errors (source
    /// missing/changed, unauthorized, disk-full, protocol, local write, cancel)
    /// are NEVER retried - they are already specific and final.
    struct RetryPolicy {
        var maxAttempts: Int
        var backoffBase: TimeInterval
        var backoffMax: TimeInterval
        init(maxAttempts: Int = 6, backoffBase: TimeInterval = 0.5, backoffMax: TimeInterval = 4) {
            self.maxAttempts = maxAttempts
            self.backoffBase = backoffBase
            self.backoffMax = backoffMax
        }
    }

    private let hostProvider: HostProvider
    private let temporaryDirectory: URL
    private let retry: RetryPolicy
    private let scheduler: Scheduler
    private let perf: PerfTrace
    private let inFlightLock = NSLock()
    private var inFlight: [String: SharedFetch] = [:]

    private final class SharedFetch {
        let progress: Progress
        var completions: [(URL?, NSFileProviderItem?, Error?) -> Void]

        init(progress: Progress, completion: @escaping (URL?, NSFileProviderItem?, Error?) -> Void) {
            self.progress = progress
            self.completions = [completion]
        }
    }

    init(hostProvider: @escaping HostProvider,
         temporaryDirectory: URL = FileManager.default.temporaryDirectory,
         retry: RetryPolicy = RetryPolicy(),
         scheduler: Scheduler? = nil,
         perf: PerfTrace = .live) {
        self.hostProvider = hostProvider
        self.temporaryDirectory = temporaryDirectory
        self.retry = retry
        self.perf = perf
        self.scheduler = scheduler ?? { delay, work in
            DispatchQueue.global(qos: .utility).asyncAfter(deadline: .now() + delay, execute: work)
        }
    }

    func fetch(_ item: DuoItem, request: NSFileProviderRequest,
               fetchStartedAt: UInt64? = nil,
               extraTraceFields: [(String, String)] = [],
               completion: @escaping (URL?, NSFileProviderItem?, Error?) -> Void) -> Progress {
        let parsed = DuoItemModel.parse(item.itemIdentifier)
        let key = item.itemIdentifier.rawValue
        inFlightLock.lock()
        if let shared = inFlight[key] {
            shared.completions.append(completion)
            let progress = shared.progress
            inFlightLock.unlock()
            perf.mark("fetch_coalesced", fields: [
                ("item_identifier", key),
                ("transfer_id", parsed?.transferId ?? "unknown"),
                ("entry_index", String(parsed?.index ?? -1))
            ] + extraTraceFields)
            return progress
        }
        if fetchStartedAt == nil {
            perf.mark("fetch_enter", fields: [
                ("item_identifier", item.itemIdentifier.rawValue),
                ("transfer_id", parsed?.transferId ?? "unknown"),
                ("entry_index", String(parsed?.index ?? -1))
            ])
        }
        fetchLog.info("FETCH_ENTER transfer_id=\(parsed?.transferId ?? "?", privacy: .public) entry_index=\(parsed?.index ?? -1, privacy: .public)")
        let operation = FetchOperation(item: item, directory: temporaryDirectory,
                                       hostProvider: hostProvider, retry: retry,
                                       scheduler: scheduler, perf: perf,
                                       extraTraceFields: extraTraceFields,
                                       completion: { [weak self] url, fetchedItem, error in
                                           self?.completeSharedFetch(
                                               key: key, url: url, item: fetchedItem, error: error
                                           )
                                       })
        inFlight[key] = SharedFetch(progress: operation.progress, completion: completion)
        inFlightLock.unlock()
        operation.start()
        return operation.progress
    }

    private func completeSharedFetch(
        key: String,
        url: URL?,
        item: NSFileProviderItem?,
        error: Error?
    ) {
        inFlightLock.lock()
        let completions = inFlight.removeValue(forKey: key)?.completions ?? []
        inFlightLock.unlock()
        for completion in completions {
            completion(url, item, error)
        }
    }
}

private final class FetchOperation {
    let progress: Progress
    private let item: DuoItem
    private let directory: URL
    private let hostProvider: FetchController.HostProvider
    private let retry: FetchController.RetryPolicy
    private let scheduler: FetchController.Scheduler
    private let perf: PerfTrace
    private let extraTraceFields: [(String, String)]
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
    private var wroteFirstChunk = false
    //: 1-based fetch attempt. Bumped on every (re)start and captured by each
    //: async callback, so a superseded attempt's late callback (e.g. the old
    //: host's connection-lost handler firing after we already retried) is
    //: ignored instead of corrupting the live attempt.
    private var attempt = 0
    //: Wall-clock start of the byte transfer (first pull), for throughput /
    //: estimated-time-remaining on `progress` so the system shows speed and
    //: "N left", not just a bare fraction.
    private var startNs: UInt64 = 0
    //: Correlation ids for the log chain (task 17) - filled in once `start()`
    //: parses the item identifier. Never a path/filename, only the ids.
    private var transferId: String?
    private var entryIndex: Int?

    init(item: DuoItem, directory: URL,
         hostProvider: @escaping FetchController.HostProvider,
         retry: FetchController.RetryPolicy,
         scheduler: @escaping FetchController.Scheduler,
         perf: PerfTrace,
         extraTraceFields: [(String, String)] = [],
         completion: @escaping (URL?, NSFileProviderItem?, Error?) -> Void) {
        self.item = item
        self.directory = directory
        self.hostProvider = hostProvider
        self.retry = retry
        self.scheduler = scheduler
        self.perf = perf
        self.extraTraceFields = extraTraceFields
        self.completion = completion
        let progress = Progress(totalUnitCount: item.documentSize?.int64Value ?? 0)
        // Classify the fetch as a file DOWNLOAD so Finder/fileproviderd render a
        // real determinate progress bar while materializing a dataless item
        // (the "Preparing to copy" phase of a paste into a non-FP folder),
        // instead of an indeterminate spinner. Without kind=.file +
        // fileOperationKind=.downloading the system does not treat the returned
        // Progress as user-facing download progress even though totalUnitCount/
        // completedUnitCount are set.
        progress.kind = .file
        progress.setUserInfoObject(Progress.FileOperationKind.downloading, forKey: .fileOperationKindKey)
        self.progress = progress
    }

    private func error(_ code: Int) -> NSError { NSError(domain: DuoFPErrorDomain, code: code) }

    func start() {
        if attempt == 0 {
            progress.cancellationHandler = { [weak self] in
                guard let self else { return }
                // Task 12: Finder cancel -> Progress.cancellationHandler -> here.
                // finish(error) below already does the rest generically (settle
                // once, remove temp, tell the host via cancelFetch(token)) - see
                // finish(_:) - so this handler only needs to supply the literal
                // NSUserCancelledError the plan mandates. Cancel is terminal, so
                // it goes straight to finish, never through the retry path.
                self.queue.async {
                    self.finish(NSError(domain: NSCocoaErrorDomain, code: NSUserCancelledError))
                }
            }
        }
        queue.async {
            guard !self.finished else { return }
            self.attempt += 1
            let attempt = self.attempt
            guard !self.progress.isCancelled,
                  let parsed = DuoItemModel.parse(self.item.itemIdentifier), let index = parsed.index,
                  index >= 0, let size = self.item.documentSize?.int64Value, size >= 0 else {
                self.finish(self.error(7)); return
            }
            self.transferId = parsed.transferId
            self.entryIndex = index
            self.host = self.hostProvider { error in self.queue.async { self.settle(error, attempt: attempt) } }
            guard let host = self.host else { self.settle(self.error(8), attempt: attempt); return }
            let openFields = self.baseTraceFields(attempt: attempt)
            self.perf.mark(
                "open_fetch_call_begin", fields: openFields + [("fetch_token", "none")]
            )
            host.openFetch(parsed.transferId, entryId: NSNumber(value: index)) { token, total, error in
                self.perf.mark("open_fetch_reply", fields: openFields + [
                    ("fetch_token", token ?? "none"),
                    ("status", error == nil ? "ok" : "error")
                ])
                self.queue.async {
                    guard self.attempt == attempt, !self.opened else {
                        // Superseded attempt (we already retried): drop it, but
                        // free any host session it opened out from under us.
                        if self.attempt != attempt, let token { host.cancelFetch(token) }
                        return
                    }
                    self.opened = true
                    if self.finished {
                        if let token { host.cancelFetch(token) }
                        return
                    }
                    self.token = token
                    fetchLog.info("fp_fetch_started transfer_id=\(parsed.transferId, privacy: .public) entry_index=\(index, privacy: .public) fetch_token=\(token ?? "?", privacy: .public)")
                    if let error { self.settle(error, attempt: attempt); return }
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
                            self.perf.mark("fsync_complete", fields: self.traceFields(attempt: attempt))
                            try self.file?.close()
                            self.perf.mark("close_complete", fields: self.traceFields(attempt: attempt))
                            self.file = nil
                            self.perf.mark("finalize_complete", fields: self.traceFields(attempt: attempt))
                            self.finish(nil)
                        } else {
                            self.startNs = DispatchTime.now().uptimeNanoseconds
                            self.pull(attempt: attempt)
                        }
                    } catch { self.finish(error) }  // local write/create error: never retried
                }
            }
        }
    }

    private func pull(attempt: Int) {
        guard !finished, self.attempt == attempt, let token, let host else { return }
        pullSequence += 1
        let sequence = pullSequence
        let pullFields = traceFields(attempt: attempt) + [("pull_sequence", String(sequence))]
        perf.mark("pull_call_begin", fields: pullFields)
        host.pullChunk(token) { chunk, eof, error in
            self.perf.mark("pull_reply", fields: pullFields + [
                ("bytes", String(chunk?.count ?? 0)),
                ("eof", eof ? "true" : "false"),
                ("status", error == nil ? "ok" : "error")
            ])
            self.queue.async {
                guard !self.finished, self.attempt == attempt, self.pullSequence == sequence else { return }
                self.pullSequence += 1
                if let error { self.settle(error, attempt: attempt); return }
                guard let chunk, chunk.count <= 1_048_576,
                      Int64(chunk.count) <= self.progress.totalUnitCount - self.offset,
                      (!chunk.isEmpty || (eof && self.offset == self.progress.totalUnitCount)),
                      eof == (self.offset + Int64(chunk.count) == self.progress.totalUnitCount) else {
                    self.finish(self.error(7)); return
                }
                do {
                    try self.file?.write(contentsOf: chunk)
                    let writeDoneNs = self.perf.mark(
                        "chunk_write_complete",
                        fields: self.traceFields(attempt: attempt) + [
                            ("pull_sequence", String(sequence)),
                            ("bytes", String(chunk.count))
                        ]
                    )
                    if !self.wroteFirstChunk {
                        self.perf.mark(
                            "first_write_complete", at: writeDoneNs,
                            fields: self.traceFields(attempt: attempt) + [("bytes", String(chunk.count))]
                        )
                        self.wroteFirstChunk = true
                        if eof {
                            self.perf.mark(
                                "last_write_complete", at: writeDoneNs,
                                fields: self.traceFields(attempt: attempt) + [("bytes", String(chunk.count))]
                            )
                        }
                    } else if eof {
                        self.perf.mark(
                            "last_write_complete",
                            fields: self.traceFields(attempt: attempt) + [("bytes", String(chunk.count))]
                        )
                    }
                    self.offset += Int64(chunk.count)
                    self.progress.completedUnitCount = self.offset
                    // Feed throughput + estimated time remaining so the system
                    // shows speed and "N left" alongside the fraction, not just
                    // a bare bar. Derived from average rate since the first pull.
                    let elapsedNs = DispatchTime.now().uptimeNanoseconds &- self.startNs
                    if elapsedNs > 0, self.offset > 0 {
                        let bytesPerSecond = Double(self.offset) * 1_000_000_000.0 / Double(elapsedNs)
                        if bytesPerSecond > 0 {
                            self.progress.throughput = Int(bytesPerSecond)
                            let remaining = self.progress.totalUnitCount - self.offset
                            self.progress.estimatedTimeRemaining = Double(remaining) / bytesPerSecond
                        }
                    }
                    // fp_bytes_received: the byte COUNT only, never the chunk itself.
                    fetchLog.info("fp_bytes_received transfer_id=\(self.transferId ?? "?", privacy: .public) entry_index=\(self.entryIndex ?? -1, privacy: .public) fetch_token=\(token, privacy: .public) bytes=\(chunk.count, privacy: .public)")
                    if eof {
                        try self.file?.synchronize()
                        self.perf.mark("fsync_complete", fields: self.traceFields(attempt: attempt))
                        try self.file?.close()
                        self.perf.mark("close_complete", fields: self.traceFields(attempt: attempt))
                        self.file = nil
                        self.perf.mark("finalize_complete", fields: self.traceFields(attempt: attempt))
                        self.finish(nil)
                    } else { self.pull(attempt: attempt) }
                } catch { self.finish(error) }  // local write error: never retried
            }
        }
    }

    /// A transient host-unreachable error (peerLost/timeout/notConnected) is
    /// retried within budget; anything else finishes immediately.
    private func settle(_ error: Error, attempt: Int) {
        guard !finished, self.attempt == attempt else { return }
        if isRetryable(error), self.attempt < retry.maxAttempts {
            scheduleRetry(error)
        } else {
            finish(error)
        }
    }

    private func isRetryable(_ error: Error) -> Bool {
        let ns = error as NSError
        // DuoFPError peerLost(3)/timeout(5)/notConnected(8): the peer link is
        // gone right now but auto-reconnects. NOT sourceMissing/changed/
        // unauthorized/diskFull/protocol, and never a local/Cocoa error.
        return ns.domain == DuoFPErrorDomain && (ns.code == 3 || ns.code == 5 || ns.code == 8)
    }

    private func scheduleRetry(_ error: Error) {
        // Tear down this attempt's partial state; the next start() re-acquires a
        // fresh host (waiting for the peer link to reconnect) and re-opens from
        // the beginning. The Progress object is reused so Finder keeps the same
        // download; its completed count restarts at 0 for the new attempt.
        if let token { host?.cancelFetch(token) }
        try? file?.close()
        file = nil
        if let url { try? FileManager.default.removeItem(at: url) }
        url = nil
        token = nil
        opened = false
        offset = 0
        wroteFirstChunk = false
        pullSequence = 0
        host = nil
        progress.completedUnitCount = 0
        let delay = min(retry.backoffBase * pow(2, Double(attempt - 1)), retry.backoffMax)
        fetchLog.info("fp_fetch_retry transfer_id=\(self.transferId ?? "?", privacy: .public) entry_index=\(self.entryIndex ?? -1, privacy: .public) attempt=\(self.attempt, privacy: .public) next_retry_ms=\(Int(delay * 1000), privacy: .public) code=\((error as NSError).code, privacy: .public)")
        scheduler(delay) { self.queue.async { self.start() } }
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
            perf.mark("completion_call", fields: traceFields(attempt: attempt) + [
                ("status", "error"), ("error_code", String(mapped.code))
            ])
            completion(nil, nil, mapped)
        } else {
            fetchLog.info("fp_fetch_completed transfer_id=\(self.transferId ?? "?", privacy: .public) entry_index=\(self.entryIndex ?? -1, privacy: .public) fetch_token=\(self.token ?? "?", privacy: .public)")
            perf.mark("completion_call", fields: traceFields(attempt: attempt) + [("status", "ok")])
            completion(url, item, nil)
        }
        host = nil
    }

    private func traceFields(attempt: Int) -> [(String, String)] {
        baseTraceFields(attempt: attempt) + [
            ("fetch_token", token ?? "none")
        ]
    }

    private func baseTraceFields(attempt: Int) -> [(String, String)] {
        [
            ("item_identifier", item.itemIdentifier.rawValue),
            ("transfer_id", transferId ?? "unknown"),
            ("entry_index", String(entryIndex ?? -1)),
            ("attempt", String(attempt))
        ] + extraTraceFields
    }
}
