import Foundation
import FileProvider
import Darwin
import os

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
        Logger(subsystem: "com.duoinput.configurator.fileprovider", category: "fetch").info("FETCH_ENTER")
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
            self.queue.async { self.finish(CocoaError(.userCancelled)) }
        }
        queue.async {
            guard !self.progress.isCancelled,
                  let parsed = DuoItemModel.parse(self.item.itemIdentifier), let index = parsed.index,
                  index >= 0, let size = self.item.documentSize?.int64Value, size >= 0 else {
                self.finish(self.error(7)); return
            }
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
            if let token { host?.cancelFetch(token) }
            if let url { try? FileManager.default.removeItem(at: url) }
            completion(nil, nil, error)
        } else {
            completion(url, item, nil)
        }
        host = nil
    }
}
