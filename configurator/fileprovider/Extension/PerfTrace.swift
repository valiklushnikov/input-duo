import Foundation
import os

/// Lightweight, parseable performance marks on one monotonic clock domain.
/// Values are correlation identifiers and numeric metadata only; paths and
/// payload bytes must never be passed as fields.
final class PerfTrace {
    typealias Clock = () -> UInt64
    typealias Emit = (String) -> Void

    private let clock: Clock
    private let emit: Emit

    init(clock: @escaping Clock, emit: @escaping Emit) {
        self.clock = clock
        self.emit = emit
    }

    @discardableResult
    func mark(
        _ event: String,
        at stamp: UInt64? = nil,
        fields: [(String, String)] = []
    ) -> UInt64 {
        let value = stamp ?? clock()
        let suffix = fields.sorted { $0.0 < $1.0 }
            .map { "\(Self.atom($0.0))=\(Self.atom($0.1))" }
            .joined(separator: " ")
        emit(
            "fp_perf event=\(Self.atom(event)) mono_ns=\(value) " +
            "clock=swift_uptime\(suffix.isEmpty ? "" : " " + suffix)"
        )
        return value
    }

    private static func atom(_ value: String) -> String {
        guard !value.isEmpty,
              value.unicodeScalars.allSatisfy({ scalar in
                  scalar.value >= 0x21 && scalar.value <= 0x7e
              }) else {
            return "invalid_atom"
        }
        return value
    }

    static let live: PerfTrace = {
        let logger = Logger(
            subsystem: "com.duoinput.configurator.fileprovider",
            category: "perf"
        )
        return PerfTrace(
            clock: { DispatchTime.now().uptimeNanoseconds },
            emit: { line in logger.info("\(line, privacy: .public)") }
        )
    }()
}
