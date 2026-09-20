import Foundation
import os

/// Injection seam for the eviction lifecycle so it is testable without a live
/// NSFileProviderManager: materialized-set truth, the evict call, and a
/// deferred scheduler (real timer in production, controllable fake in tests).
protocol EvictionEnvironment: AnyObject {
    /// Authoritative materialized-item identifiers (from the manager's
    /// materialized enumerator in production).
    func queryMaterialized(_ completion: @escaping (Set<String>) -> Void)
    /// Evict one item back to dataless; completion carries an error or nil.
    func evict(_ itemIdentifier: String, completion: @escaping (Error?) -> Void)
    /// Run `work` after `seconds` (a real timer in production; drained by hand
    /// in tests). Never a busy loop.
    func schedule(after seconds: TimeInterval, _ work: @escaping () -> Void)
}

enum EvictionState: String {
    case waitMaterialization
    case grace
    case evicting
    case evicted
    case abandoned
}

/// SDK: `NSFileProviderErrorDomain` / `NSFileProviderErrorNonEvictable` (-2008).
/// Kept as literals so this coordinator stays FileProvider-framework-free and
/// unit-testable; `ManagerEvictionEnvironment` owns the real framework types.
/// Post-materialization, this code is POTENTIALLY_TRANSIENT: Finder still pins a
/// just-fetched item during a multi-file copy and releases it shortly after, so
/// it is retried within a bounded window rather than treated as permanent.
enum FPNonEvictable {
    static let domain = "NSFileProviderErrorDomain"
    static let code = -2008
}

/// Post-fetch cache cleanup as an explicit state machine, keyed per exact
/// itemIdentifier, run by ONE coordinator with bounded concurrent jobs:
///
///   FETCH_COMPLETED → WAIT_MATERIALIZATION → (observed) → GRACE → EVICTING
///                   → (verified not materialized) → EVICTED
///   timeout → ABANDONED ; evict error past retries → ABANDONED ;
///   re-materialized after evict → back to WAIT_MATERIALIZATION.
///
/// Cleanup state, not transfer state: the transfer already succeeded before a
/// job is scheduled. Eviction is best-effort cache cleanup.
final class EvictionCoordinator {
    struct Config {
        var pollInterval: TimeInterval
        var materializationTimeout: TimeInterval
        var grace: TimeInterval
        /// Bounded window for POTENTIALLY_TRANSIENT_NON_EVICTABLE (-2008) retries,
        /// measured from the FIRST eviction attempt — NOT an attempt count. A
        /// multi-file Finder copy pins already-fetched items and outlasts a small
        /// fixed retry budget, so cleanup lifetime is time-bounded instead.
        var evictionRetryWindow: TimeInterval
        /// Exponential backoff for -2008: min(base * 2^n, max). Async, no busy poll.
        var backoffBase: TimeInterval
        var backoffMax: TimeInterval
        /// Existing bounded policy for NON-(-2008) evict errors (unchanged).
        var maxEvictAttempts: Int
        var evictRetryDelay: TimeInterval

        init(pollInterval: TimeInterval = 0.5,
             materializationTimeout: TimeInterval = 120,
             grace: TimeInterval = 2,
             evictionRetryWindow: TimeInterval = 120,
             backoffBase: TimeInterval = 1,
             backoffMax: TimeInterval = 10,
             maxEvictAttempts: Int = 3,
             evictRetryDelay: TimeInterval = 2) {
            self.pollInterval = pollInterval
            self.materializationTimeout = materializationTimeout
            self.grace = grace
            self.evictionRetryWindow = evictionRetryWindow
            self.backoffBase = backoffBase
            self.backoffMax = backoffMax
            self.maxEvictAttempts = maxEvictAttempts
            self.evictRetryDelay = evictRetryDelay
        }
    }

    /// Serializes access to `jobs`. Production wraps a serial DispatchQueue;
    /// tests inject a synchronous executor for determinism.
    typealias Executor = (@escaping () -> Void) -> Void

    private final class Job {
        let itemIdentifier: String
        let transferId: String
        var state: EvictionState = .waitMaterialization
        var elapsedPolling: TimeInterval = 0
        var evictAttempts = 0
        /// Backoff exponent for consecutive -2008 deferrals of THIS materialization.
        var nonEvictableRetries = 0
        /// Virtual time elapsed since the first eviction attempt, summed from the
        /// scheduled backoff delays. Wall-clock-free so tests stay deterministic;
        /// it is the terminal condition for the -2008 retry window.
        var evictWindowElapsed: TimeInterval = 0
        init(itemIdentifier: String, transferId: String) {
            self.itemIdentifier = itemIdentifier
            self.transferId = transferId
        }
    }

    private let env: EvictionEnvironment
    private let config: Config
    private let exec: Executor
    private let log = Logger(subsystem: "com.duoinput.configurator.fileprovider", category: "cleanup")
    private var jobs: [String: Job] = [:]
    /// Test hook: fired when a job reaches a terminal state (evicted/abandoned).
    var onTerminal: ((String, EvictionState) -> Void)?
    /// Test hook: fired on ABANDONED with the reason string (EVICT_ERROR vs
    /// EVICTION_TIMEOUT vs MATERIALIZATION_TIMEOUT).
    var onAbandoned: ((String, String?) -> Void)?

    init(environment: EvictionEnvironment,
         config: Config = Config(),
         executor: Executor? = nil) {
        self.env = environment
        self.config = config
        if let executor {
            self.exec = executor
        } else {
            let queue = DispatchQueue(label: "com.duoinput.fileprovider.cleanup")
            self.exec = { work in queue.async(execute: work) }
        }
    }

    /// Schedule cleanup for a freshly-fetched item. Idempotent per identifier;
    /// supports many concurrent jobs (no global "current item").
    func schedule(itemIdentifier: String, transferId: String) {
        exec {
            guard self.jobs[itemIdentifier] == nil else { return }
            let job = Job(itemIdentifier: itemIdentifier, transferId: transferId)
            self.jobs[itemIdentifier] = job
            self.event("fp_cleanup_scheduled", job)
            self.pollMaterialization(job)
        }
    }

    private func pollMaterialization(_ job: Job) {
        env.queryMaterialized { materialized in
            self.exec {
                guard self.jobs[job.itemIdentifier] === job else { return }
                if materialized.contains(job.itemIdentifier) {
                    self.event("fp_materialization_observed", job)
                    job.state = .grace
                    self.env.schedule(after: self.config.grace) {
                        self.exec { self.startEvict(job) }
                    }
                } else {
                    job.elapsedPolling += self.config.pollInterval
                    if job.elapsedPolling >= self.config.materializationTimeout {
                        self.finish(job, .abandoned, reason: "MATERIALIZATION_TIMEOUT")
                    } else {
                        self.env.schedule(after: self.config.pollInterval) {
                            self.exec { self.pollMaterialization(job) }
                        }
                    }
                }
            }
        }
    }

    private func startEvict(_ job: Job) {
        guard self.jobs[job.itemIdentifier] === job else { return }
        job.state = .evicting
        job.evictAttempts += 1
        self.event("fp_eviction_started", job)
        env.evict(job.itemIdentifier) { error in
            self.exec {
                guard self.jobs[job.itemIdentifier] === job else { return }
                if let error = error {
                    let ns = error as NSError
                    if ns.domain == FPNonEvictable.domain && ns.code == FPNonEvictable.code {
                        // POTENTIALLY_TRANSIENT_NON_EVICTABLE: retry within the
                        // bounded window with exponential backoff (do NOT treat
                        // every -2008 globally as transient — only this
                        // post-materialization cleanup state).
                        self.deferNonEvictable(job, error: error)
                    } else {
                        // Other errors keep the existing bounded attempt policy.
                        self.event("fp_eviction_retry", job, error: error)
                        if job.evictAttempts < self.config.maxEvictAttempts {
                            self.env.schedule(after: self.config.evictRetryDelay) {
                                self.exec { self.startEvict(job) }
                            }
                        } else {
                            self.finish(job, .abandoned, reason: "EVICT_ERROR")
                        }
                    }
                    return
                }
                self.event("fp_eviction_success", job)
                self.env.queryMaterialized { materialized in
                    self.exec {
                        guard self.jobs[job.itemIdentifier] === job else { return }
                        if materialized.contains(job.itemIdentifier) {
                            // Still/again materialized: not yet dehydrated or
                            // legitimately reused. Re-run cleanup, don't corrupt.
                            self.event("fp_rematerialized", job)
                            job.state = .waitMaterialization
                            // A genuinely new materialization cycle: reset the
                            // whole eviction budget (attempts, -2008 backoff
                            // exponent, and the retry window) so the previous
                            // cycle's elapsed time/backoff is not charged against
                            // it (section 7 - re-materialization is not failure).
                            job.evictAttempts = 0
                            job.nonEvictableRetries = 0
                            job.evictWindowElapsed = 0
                            self.env.schedule(after: self.config.pollInterval) {
                                self.exec { self.pollMaterialization(job) }
                            }
                        } else {
                            self.event("fp_eviction_verified", job)
                            self.finish(job, .evicted, reason: nil)
                        }
                    }
                }
            }
        }
    }

    /// Handle a -2008 during post-fetch cleanup: bounded window + exponential
    /// backoff. The transfer already succeeded, so abandoning only forgoes cache
    /// reclamation for now (see ABANDONED semantics).
    private func deferNonEvictable(_ job: Job, error: Error) {
        if job.evictWindowElapsed >= config.evictionRetryWindow {
            finish(job, .abandoned, reason: "EVICTION_TIMEOUT")
            return
        }
        let delay = min(config.backoffBase * pow(2, Double(job.nonEvictableRetries)),
                        config.backoffMax)
        eventDeferred(job, error: error, elapsed: job.evictWindowElapsed, nextRetry: delay)
        job.nonEvictableRetries += 1
        job.evictWindowElapsed += delay
        env.schedule(after: delay) {
            self.exec { self.retryEvictAfterRecheck(job) }
        }
    }

    /// Before every -2008 retry, re-check the materialized set so the loop is
    /// idempotent: if Finder released the item and the system already dehydrated
    /// it, finish EVICTED without issuing another `evictItem`.
    private func retryEvictAfterRecheck(_ job: Job) {
        guard jobs[job.itemIdentifier] === job else { return }
        env.queryMaterialized { materialized in
            self.exec {
                guard self.jobs[job.itemIdentifier] === job else { return }
                if materialized.contains(job.itemIdentifier) {
                    self.startEvict(job)
                } else {
                    self.event("fp_eviction_verified", job)
                    self.finish(job, .evicted, reason: nil)
                }
            }
        }
    }

    private func finish(_ job: Job, _ state: EvictionState, reason: String?) {
        job.state = state
        if state == .abandoned {
            self.event("fp_cleanup_abandoned", job, reasonText: reason)
            onAbandoned?(job.itemIdentifier, reason)
        }
        jobs[job.itemIdentifier] = nil
        onTerminal?(job.itemIdentifier, state)
    }

    /// Cancel all in-flight cleanup (e.g. extension invalidation): pending
    /// scheduled closures become no-ops because their `jobs[id] === job` guard
    /// fails once the table is cleared. No thread is blocked or joined.
    func shutdown() {
        exec { self.jobs.removeAll() }
    }

    private func event(_ name: String, _ job: Job, error: Error? = nil, reasonText: String? = nil) {
        let err = error.map { e -> String in
            let ns = e as NSError
            // Domain:code only (plus any nested error's domain:code). The
            // classification we need is the code; localizedDescription/userInfo
            // carry the user's filename/URL and MUST NOT be logged - the whole
            // fileprovider log chain is "never a path/filename, only the ids".
            let underlying = (ns.userInfo[NSUnderlyingErrorKey] as? NSError)
                .map { " underlying=\($0.domain):\($0.code)" } ?? ""
            return " error=\(ns.domain):\(ns.code)\(underlying)"
        } ?? ""
        let reason = reasonText.map { " reason=\($0)" } ?? ""
        log.log("\(name, privacy: .public) transfer_id=\(job.transferId, privacy: .public) item=\(job.itemIdentifier, privacy: .public) attempt=\(job.evictAttempts, privacy: .public)\(err, privacy: .public)\(reason, privacy: .public)")
    }

    /// Structured diagnostic for a deferred (bounded-retry) -2008: carries the
    /// window progress so a log alone shows whether cleanup is still trying.
    private func eventDeferred(_ job: Job, error: Error, elapsed: TimeInterval, nextRetry: TimeInterval) {
        let ns = error as NSError
        log.log("fp_eviction_deferred_non_evictable transfer_id=\(job.transferId, privacy: .public) item=\(job.itemIdentifier, privacy: .public) attempt=\(job.evictAttempts, privacy: .public) elapsed_ms=\(Int(elapsed * 1000), privacy: .public) next_retry_ms=\(Int(nextRetry * 1000), privacy: .public) error=\(ns.domain, privacy: .public):\(ns.code, privacy: .public)")
    }
}
