import XCTest

/// Controllable environment: materialized set is set by the test; evict and
/// scheduled work are queued and drained by hand so timing is deterministic.
private final class FakeEvictionEnvironment: EvictionEnvironment {
    var materialized: Set<String> = []
    var evictCount = 0
    var evictError: Error?
    /// Per-call override: given (itemIdentifier, 1-based evictCount) return the
    /// error (nil = success). Wins over `evictError` when set — lets a test model
    /// "-2008 while pinned, then SUCCESS once Finder releases" deterministically.
    var evictHandler: ((String, Int) -> Error?)?
    /// Every `schedule(after:)` delay, in call order — for asserting the backoff.
    private(set) var scheduledDelays: [TimeInterval] = []
    private var pending: [() -> Void] = []

    func queryMaterialized(_ completion: @escaping (Set<String>) -> Void) {
        completion(materialized)
    }

    func evict(_ itemIdentifier: String, completion: @escaping (Error?) -> Void) {
        evictCount += 1
        let error = evictHandler?(itemIdentifier, evictCount) ?? evictError
        pending.append { completion(error) }
    }

    func schedule(after seconds: TimeInterval, _ work: @escaping () -> Void) {
        scheduledDelays.append(seconds)
        pending.append(work)
    }

    /// Run exactly one round of currently-pending work (FIFO snapshot).
    @discardableResult
    func drain() -> Int {
        let batch = pending
        pending = []
        batch.forEach { $0() }
        return batch.count
    }

    /// Run pending work until it settles (bounded).
    func drainAll(max: Int = 10_000) {
        var rounds = 0
        while !pending.isEmpty, rounds < max {
            drain()
            rounds += 1
        }
    }
}

final class EvictionCoordinatorTests: XCTestCase {
    private func makeCoordinator(_ env: FakeEvictionEnvironment,
                                 config: EvictionCoordinator.Config = .init(pollInterval: 0.5, materializationTimeout: 120, grace: 2, maxEvictAttempts: 3, evictRetryDelay: 2),
                                 perf: PerfTrace = .live)
        -> (EvictionCoordinator, () -> EvictionState?) {
        var terminal: EvictionState?
        let coord = EvictionCoordinator(
            environment: env, config: config, executor: { $0() }, perf: perf
        )
        coord.onTerminal = { _, state in terminal = state }
        return (coord, { terminal })
    }

    private func recordingTrace() -> (PerfTrace, () -> [String]) {
        var tick: UInt64 = 100
        var lines: [String] = []
        return (
            PerfTrace(clock: { tick += 10; return tick }, emit: { lines.append($0) }),
            { lines }
        )
    }

    private func eventNames(_ lines: [String]) -> [String] {
        lines.compactMap { line in
            line.split(separator: " ").first(where: { $0.hasPrefix("event=") })
                .map { String($0.dropFirst("event=".count)) }
        }
    }

    func testMaterializationEvictionAndVerificationCarryMonotonicItemTimeline() {
        let env = FakeEvictionEnvironment()
        let (perf, captured) = recordingTrace()
        let (coord, terminal) = makeCoordinator(env, perf: perf)
        env.materialized = ["gen:0"]

        coord.schedule(itemIdentifier: "gen:0", transferId: "gen")
        env.drain()
        env.materialized = []
        env.drainAll()

        XCTAssertEqual(eventNames(captured()), [
            "cleanup_scheduled", "materialization_observed", "eviction_attempt",
            "eviction_success", "eviction_verified"
        ])
        XCTAssertTrue(captured().allSatisfy { $0.contains("item_identifier=gen:0") })
        XCTAssertEqual(terminal(), .evicted)
    }

    func testNonEvictableTraceCarriesAttemptAndRetryCounts() {
        let env = FakeEvictionEnvironment()
        let (perf, captured) = recordingTrace()
        let (coord, _) = makeCoordinator(env, perf: perf)
        env.materialized = ["gen:0"]
        env.evictError = nonEvictable()

        coord.schedule(itemIdentifier: "gen:0", transferId: "gen")
        env.drain()
        env.drain()

        let events = captured()
        XCTAssertTrue(events.contains { $0.contains("event=eviction_attempt") && $0.contains("attempt=1") })
        XCTAssertTrue(events.contains { $0.contains("event=eviction_deferred") && $0.contains("retry_count=0") })
        XCTAssertEqual(env.scheduledDelays, [2, 1], "grace and existing first backoff stay unchanged")
    }

    /// THE regression for the measured bug: at fetch completion the item is not
    /// yet materialized, so evict must NOT fire; only after materialization is
    /// observed (+grace) does evict run exactly once, then verify → EVICTED.
    func test_does_not_evict_before_materialization_then_evicts_after() {
        let env = FakeEvictionEnvironment()
        let (coord, terminal) = makeCoordinator(env)

        env.materialized = []                       // T0: not materialized yet
        coord.schedule(itemIdentifier: "gen:0", transferId: "gen")
        XCTAssertEqual(env.evictCount, 0, "must not evict a not-yet-materialized item")

        env.drain()                                 // next poll fires, still empty
        XCTAssertEqual(env.evictCount, 0)

        env.materialized = ["gen:0"]                // T+1: now materialized
        env.drain()                                 // poll observes → schedules grace
        XCTAssertEqual(env.evictCount, 0, "grace not elapsed yet")

        env.drain()                                 // grace elapses → evict
        XCTAssertEqual(env.evictCount, 1)

        env.materialized = []                       // evict dehydrated it
        env.drainAll()                              // evict completion → verify → EVICTED
        XCTAssertEqual(terminal(), .evicted)
    }

    func test_abandons_when_materialization_never_observed() {
        let env = FakeEvictionEnvironment()
        let (coord, terminal) = makeCoordinator(env, config: .init(pollInterval: 10, materializationTimeout: 30, grace: 2, maxEvictAttempts: 3, evictRetryDelay: 2))
        env.materialized = []
        coord.schedule(itemIdentifier: "gen:0", transferId: "gen")
        env.drainAll()                              // polls accumulate to timeout
        XCTAssertEqual(env.evictCount, 0)
        XCTAssertEqual(terminal(), .abandoned)
    }

    func test_reschedules_when_item_rematerializes_after_evict() {
        let env = FakeEvictionEnvironment()
        let (coord, terminal) = makeCoordinator(env)
        env.materialized = ["gen:0"]
        coord.schedule(itemIdentifier: "gen:0", transferId: "gen")
        env.drain()                                 // observe materialized → grace
        env.drain()                                 // grace → evict #1
        XCTAssertEqual(env.evictCount, 1)
        env.drain()                                 // evict completion → verify: STILL materialized → reschedule
        XCTAssertNil(terminal(), "must not finish while still materialized")
        env.drain()                                 // re-poll observes materialized → grace
        env.drain()                                 // grace → evict #2 (reused → cleaned again)
        XCTAssertEqual(env.evictCount, 2, "re-materialized item is cleaned again, not corrupted")
    }

    func test_retries_evict_error_then_succeeds() {
        let env = FakeEvictionEnvironment()
        let (coord, terminal) = makeCoordinator(env)
        env.materialized = ["gen:0"]
        env.evictError = NSError(domain: "test", code: 1)
        coord.schedule(itemIdentifier: "gen:0", transferId: "gen")   // observe (sync) → grace queued
        env.drain()                                 // grace → evict #1 (errors)
        XCTAssertEqual(env.evictCount, 1)
        env.drain()                                 // completion(error) → retry scheduled
        env.evictError = nil                        // next attempt succeeds
        env.drain()                                 // retry → evict #2 (success)
        XCTAssertEqual(env.evictCount, 2, "one retry after the error")
        env.materialized = []
        env.drain()                                 // completion(success) → verify: dehydrated → EVICTED
        XCTAssertEqual(terminal(), .evicted)
    }

    func test_supports_concurrent_jobs() {
        let env = FakeEvictionEnvironment()
        var terminals: [String: EvictionState] = [:]
        let coord = EvictionCoordinator(environment: env,
                                        config: .init(pollInterval: 0.5, materializationTimeout: 120, grace: 2, maxEvictAttempts: 3, evictRetryDelay: 2),
                                        executor: { $0() })
        coord.onTerminal = { id, state in terminals[id] = state }
        env.materialized = ["a:0", "b:0"]
        coord.schedule(itemIdentifier: "a:0", transferId: "a")   // observe (sync) → grace_a queued
        coord.schedule(itemIdentifier: "b:0", transferId: "b")   // observe (sync) → grace_b queued
        env.drain()                                 // both grace → evict a, evict b
        XCTAssertEqual(env.evictCount, 2)
        env.materialized = []
        env.drain()                                 // both completions → verify dehydrated → EVICTED
        XCTAssertEqual(terminals["a:0"], .evicted)
        XCTAssertEqual(terminals["b:0"], .evicted)
    }

    // MARK: - Bounded -2008 retry/backoff (transient non-evictable)

    /// SDK NSFileProviderErrorNonEvictable during post-fetch cleanup.
    private func nonEvictable(_ file: String = "pinned") -> NSError {
        NSError(domain: "NSFileProviderErrorDomain", code: -2008,
                userInfo: [NSLocalizedDescriptionKey: "cannot evict \(file)"])
    }

    /// THE regression for the multi-file folder bug: while Finder still holds the
    /// item, evict returns -2008. The OLD implementation (maxEvictAttempts=3)
    /// would ABANDON after the third attempt; the new one keeps retrying within
    /// the bounded window and evicts once Finder releases the pin.
    func test_transient_non_evictable_retries_past_old_budget_then_evicts() {
        let env = FakeEvictionEnvironment()
        let (coord, terminal) = makeCoordinator(env)
        env.materialized = ["gen:0"]
        env.evictHandler = { id, attempt in
            if attempt <= 4 { return self.nonEvictable() }   // pinned by Finder
            env.materialized.remove(id)                       // released → evict succeeds
            return nil
        }
        coord.schedule(itemIdentifier: "gen:0", transferId: "gen")

        // Drive exactly to where the OLD 3-attempt budget would ABANDON:
        // d1 grace→evict#1; d2 -2008→defer; d3 retry→evict#2; d4 -2008→defer;
        // d5 retry→evict#3; d6 -2008→defer (old impl abandons here).
        for _ in 0..<6 { env.drain() }
        XCTAssertEqual(env.evictCount, 3)
        XCTAssertNil(terminal(),
                     "old maxEvictAttempts=3 would be ABANDONED here; new impl keeps retrying in-window")

        env.drainAll()
        XCTAssertEqual(terminal(), .evicted, "evicts once Finder releases the pin")
        XCTAssertGreaterThanOrEqual(env.evictCount, 5)
    }

    /// Backoff schedule is min(base*2^n, max): 1,2,4,8,10,10,... The grace delay
    /// is the only other scheduled delay here (materialized is observed
    /// synchronously, so no polling delays), so drop it and assert the tail.
    func test_non_evictable_backoff_is_bounded_exponential() {
        let env = FakeEvictionEnvironment()
        let (coord, _) = makeCoordinator(env,
            config: .init(grace: 99, evictionRetryWindow: 1_000, backoffBase: 1, backoffMax: 10))
        env.materialized = ["gen:0"]
        env.evictError = nonEvictable()             // always pinned
        coord.schedule(itemIdentifier: "gen:0", transferId: "gen")
        for _ in 0..<14 { env.drain() }             // several evict/defer cycles
        let backoff = Array(env.scheduledDelays.filter { $0 != 99 }.prefix(6))
        XCTAssertEqual(backoff, [1, 2, 4, 8, 10, 10])
    }

    /// -2008 for the whole window → ABANDONED(EVICTION_TIMEOUT), never SUCCESS.
    func test_non_evictable_full_window_abandons_with_timeout() {
        let env = FakeEvictionEnvironment()
        var reason: String?
        let (coord, terminal) = makeCoordinator(env,
            config: .init(grace: 0, evictionRetryWindow: 5, backoffBase: 1, backoffMax: 2))
        coord.onAbandoned = { _, r in reason = r }
        env.materialized = ["gen:0"]
        env.evictError = nonEvictable()             // never releases
        coord.schedule(itemIdentifier: "gen:0", transferId: "gen")
        env.drainAll()
        XCTAssertEqual(terminal(), .abandoned)
        XCTAssertEqual(reason, "EVICTION_TIMEOUT")
    }

    /// A permanent NON-(-2008) error keeps the existing 3-attempt policy and does
    /// NOT get the long -2008 window.
    func test_permanent_non_2008_error_uses_existing_policy() {
        let env = FakeEvictionEnvironment()
        var reason: String?
        let (coord, terminal) = makeCoordinator(env)
        coord.onAbandoned = { _, r in reason = r }
        env.materialized = ["gen:0"]
        env.evictError = NSError(domain: "server", code: 500)
        coord.schedule(itemIdentifier: "gen:0", transferId: "gen")
        env.drainAll()
        XCTAssertEqual(env.evictCount, 3, "existing bounded attempts, not the window")
        XCTAssertEqual(terminal(), .abandoned)
        XCTAssertEqual(reason, "EVICT_ERROR")
    }

    /// If the item disappears before a scheduled -2008 retry, finish EVICTED
    /// without issuing another evictItem (idempotent recheck).
    func test_item_gone_before_retry_evicts_without_extra_evict_call() {
        let env = FakeEvictionEnvironment()
        let (coord, terminal) = makeCoordinator(env)
        env.materialized = ["gen:0"]
        env.evictError = nonEvictable()
        coord.schedule(itemIdentifier: "gen:0", transferId: "gen")
        env.drain()                                 // grace → evict#1 (-2008)
        env.drain()                                 // completion → defer, retry scheduled
        XCTAssertEqual(env.evictCount, 1)
        env.materialized = []                        // Finder released; system dehydrated it
        env.drainAll()                               // retry rechecks → absent → EVICTED
        XCTAssertEqual(terminal(), .evicted)
        XCTAssertEqual(env.evictCount, 1, "no extra evictItem when already gone")
    }

    /// Two pinned items back off independently; neither serializes the other.
    func test_two_items_back_off_independently() {
        let env = FakeEvictionEnvironment()
        var terminals: [String: EvictionState] = [:]
        let coord = EvictionCoordinator(environment: env, config: .init(), executor: { $0() })
        coord.onTerminal = { id, state in terminals[id] = state }
        env.materialized = ["a:0", "b:0"]
        var per: [String: Int] = [:]
        env.evictHandler = { id, _ in
            per[id, default: 0] += 1
            if per[id]! <= 2 { return self.nonEvictable() }   // both pinned twice
            env.materialized.remove(id)
            return nil
        }
        coord.schedule(itemIdentifier: "a:0", transferId: "gen")
        coord.schedule(itemIdentifier: "b:0", transferId: "gen")
        env.drainAll()
        XCTAssertEqual(terminals["a:0"], .evicted)
        XCTAssertEqual(terminals["b:0"], .evicted)
    }

    /// shutdown() cancels pending scheduled retries cleanly: no further evict,
    /// no terminal transition.
    func test_shutdown_cancels_scheduled_retries() {
        let env = FakeEvictionEnvironment()
        let (coord, terminal) = makeCoordinator(env)
        env.materialized = ["gen:0"]
        env.evictError = nonEvictable()
        coord.schedule(itemIdentifier: "gen:0", transferId: "gen")
        env.drain()                                 // grace → evict#1 (-2008)
        env.drain()                                 // completion → defer, retry scheduled
        XCTAssertEqual(env.evictCount, 1)
        coord.shutdown()
        env.drainAll()                               // pending retry must be a no-op
        XCTAssertNil(terminal(), "no terminal after shutdown")
        XCTAssertEqual(env.evictCount, 1, "no evict after shutdown")
    }

    /// Re-materialization is a NEW cycle: the -2008 backoff exponent and the
    /// retry window reset, so a re-pinned item backs off from base again rather
    /// than continuing the previous cycle's schedule (section 7).
    func test_rematerialization_resets_backoff_window() {
        let env = FakeEvictionEnvironment()
        let (coord, terminal) = makeCoordinator(env, config: .init(grace: 0))
        env.materialized = ["gen:0"]
        env.evictHandler = { id, attempt in
            switch attempt {
            case 1, 2: return self.nonEvictable()          // pinned (backoff 1,2)
            case 3: return nil                              // success but STILL materialized → re-materialize
            case 4, 5: return self.nonEvictable()           // new cycle pinned again
            default: env.materialized.remove(id); return nil // released → EVICTED
            }
        }
        coord.schedule(itemIdentifier: "gen:0", transferId: "gen")
        env.drainAll()
        XCTAssertEqual(terminal(), .evicted)
        // Backoff delays across both cycles: without the reset the second cycle
        // would continue 4,8; with it, it restarts 1,2.
        let backoff = env.scheduledDelays.filter { [1, 2, 4, 8].contains($0) }
        XCTAssertEqual(backoff, [1, 2, 1, 2], "second materialization cycle restarts backoff at base")
    }

    /// Production defaults: 120 s window, 1 s base, 10 s max backoff.
    func test_default_config_window_and_backoff_constants() {
        let c = EvictionCoordinator.Config()
        XCTAssertEqual(c.evictionRetryWindow, 120)
        XCTAssertEqual(c.backoffBase, 1)
        XCTAssertEqual(c.backoffMax, 10)
    }
}
