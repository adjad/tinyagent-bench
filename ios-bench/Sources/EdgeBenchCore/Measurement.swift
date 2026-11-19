import Foundation

/// Appends footprint ticks to `mem-<run_id>.jsonl` and flushes them to disk at least every
/// 250 ms (spec 04 §2.5), so a kill leaves the last readings behind.
public final class MemLogWriter: @unchecked Sendable {
    private let url: URL
    private let lock = NSLock()
    private var buffer = Data()
    private var lastFlush: UInt64 = 0
    private let flushNanos: UInt64

    public init(url: URL, flushMilliseconds: Int = 250) {
        self.url = url
        flushNanos = UInt64(flushMilliseconds) * 1_000_000
        if !FileManager.default.fileExists(atPath: url.path) { FileManager.default.createFile(atPath: url.path, contents: nil) }
    }

    public func write(_ line: MemLine, now: UInt64) {
        guard let data = try? JSONEncoder.sorted.encode(line) else { return }
        let due = lock.withLock { () -> Bool in
            buffer.append(data)
            buffer.append(0x0A)
            return now &- lastFlush >= flushNanos
        }
        if due { flush(now: now) }
    }

    public func flush(now: UInt64) {
        lock.withLock {
            lastFlush = now
            guard !buffer.isEmpty, let h = try? FileHandle(forWritingTo: url) else { return }
            defer { try? h.close() }
            _ = try? h.seekToEnd()
            try? h.write(contentsOf: buffer)
            try? h.synchronize()
            buffer.removeAll(keepingCapacity: true)
        }
    }
}

/// Reads `phys_footprint` and `os_proc_available_memory()` every 50 ms (20 Hz) while a sample
/// runs, logs each tick, and summarises the sample's memory (spec 04 §2.5).
public final class MemorySampler: @unchecked Sendable {
    public struct Tick: Sendable, Equatable {
        public var tNanos: UInt64
        public var footprint: UInt64
        public var available: UInt64?
        public var decoding: Bool
    }

    public struct Result: Sendable, Equatable {
        public var startBytes: UInt64
        public var peakBytes: UInt64
        /// The kernel's lifetime peak after the sample, when it rose during it.
        public var peakLedgerBytes: UInt64?
        /// Median footprint during decode.
        public var steadyBytes: UInt64?
        public var endBytes: UInt64
        public var availableMinBytes: UInt64?
        public var ticks: Int
    }

    private let probe: MemoryProbe
    private let clock: BenchClock
    private let intervalNanos: UInt64
    private let log: MemLogWriter?
    private let sampleID: String
    private let lock = NSLock()
    private var ticks: [Tick] = []
    private var decoding = false
    private var task: Task<Void, Never>?
    private var ledgerAtStart: UInt64?
    private let onTick: (@Sendable (Tick) -> Void)?

    public init(probe: MemoryProbe, clock: BenchClock, intervalMilliseconds: Int, log: MemLogWriter?, sampleID: String,
                onTick: (@Sendable (Tick) -> Void)? = nil) {
        self.probe = probe
        self.clock = clock
        intervalNanos = UInt64(max(5, intervalMilliseconds)) * 1_000_000
        self.log = log
        self.sampleID = sampleID
        self.onTick = onTick
    }

    public func setDecoding() {
        lock.withLock { decoding = true }
        tick()
    }

    @discardableResult
    public func tick() -> Tick {
        let r = probe.read()
        let now = clock.nowNanos()
        let t = lock.withLock { () -> Tick in
            let t = Tick(tNanos: now, footprint: r.footprintBytes, available: r.availableBytes, decoding: decoding)
            ticks.append(t)
            return t
        }
        log?.write(MemLine(t_ns: t.tNanos, footprint: t.footprint, available: t.available, sample_id: sampleID), now: now)
        onTick?(t)
        return t
    }

    public func start() {
        lock.withLock {
            ticks = []
            decoding = false
            ledgerAtStart = probe.read().lifetimePeakBytes
        }
        tick()
        let interval = intervalNanos
        // Real time between ticks (not the injected clock): a manual test clock must only
        // move when the code under test moves it.
        task = Task.detached(priority: .high) { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: interval)
                guard let self, !Task.isCancelled else { return }
                self.tick()
            }
        }
    }

    public func stop() -> Result {
        task?.cancel()
        task = nil
        tick()
        log?.flush(now: clock.nowNanos())
        let ledgerEnd = probe.read().lifetimePeakBytes
        return lock.withLock {
            let fps = ticks.map(\.footprint)
            var ledger: UInt64?
            if let s = ledgerAtStart, let e = ledgerEnd, e > s { ledger = e }
            let decode = ticks.filter(\.decoding).map { Double($0.footprint) }
            return Result(
                startBytes: fps.first ?? 0,
                peakBytes: fps.max() ?? 0,
                peakLedgerBytes: ledger,
                steadyBytes: decode.isEmpty ? nil : UInt64(Stats.nearestRank(decode, 50)),
                endBytes: fps.last ?? 0,
                availableMinBytes: ticks.compactMap(\.available).min(),
                ticks: ticks.count)
        }
    }
}

/// Stamps prefill-done and every token's arrival on the run's clock.
public final class TokenTimer: @unchecked Sendable {
    private let clock: BenchClock
    private let lock = NSLock()
    private(set) var prefillDone: UInt64?
    private(set) var times: [UInt64] = []
    private(set) var reasoningCount = 0
    private var onFirst: (@Sendable () -> Void)?

    public init(clock: BenchClock, onFirst: (@Sendable () -> Void)? = nil) {
        self.clock = clock
        self.onFirst = onFirst
    }

    public func markPrefillDone() {
        let now = clock.nowNanos()
        lock.withLock { if prefillDone == nil { prefillDone = now } }
    }

    public func token(_ kind: TokenKind) {
        let now = clock.nowNanos()
        let fire: (@Sendable () -> Void)? = lock.withLock {
            times.append(now)
            if kind == .reasoning { reasoningCount += 1 }
            guard times.count == 1 else { return nil }
            defer { onFirst = nil }
            return onFirst
        }
        fire?()
    }

    /// Live decode rate for the progress screen.
    public var liveDecodeTPS: Double? {
        lock.withLock {
            guard times.count > 1, let f = times.first, let l = times.last, l > f else { return nil }
            return Double(times.count - 1) / (Double(l - f) / 1e9)
        }
    }

    public var count: Int { lock.withLock { times.count } }

    public var snapshot: (prefillDone: UInt64?, times: [UInt64], reasoning: Int) {
        lock.withLock { (prefillDone, times, reasoningCount) }
    }
}
