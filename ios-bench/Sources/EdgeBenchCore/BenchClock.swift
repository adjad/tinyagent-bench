import Darwin
import Foundation

/// Monotonic time for every measurement. The runner never uses wall-clock time for
/// durations. Tests inject `ManualClock`, so timing logic is checked to the nanosecond.
public protocol BenchClock: Sendable {
    /// Recorded as the run's `clock` (spec 04 §2.5).
    var name: String { get }
    /// Nanoseconds since an arbitrary fixed origin; never goes backwards.
    func nowNanos() -> UInt64
    /// Waits `nanos` (a manual clock advances instead of sleeping).
    func sleep(nanos: UInt64) async throws
}

extension BenchClock {
    public func seconds(since start: UInt64) -> Double {
        Double(nowNanos() &- start) / 1e9
    }
}

/// `mach_continuous_time()` in nanoseconds (spec 04 §2.5): monotonic, keeps counting through
/// sleep, and on the same timebase as the system's own logs. Values are since boot, so they
/// survive an app relaunch: samples before and after a kill share one clock.
public struct MachClock: BenchClock {
    private let numer: UInt64
    private let denom: UInt64
    public var name: String { EDAT.clockName }
    public init() {
        var tb = mach_timebase_info_data_t()
        mach_timebase_info(&tb)
        numer = UInt64(max(1, tb.numer))
        denom = UInt64(max(1, tb.denom))
    }
    public func nowNanos() -> UInt64 {
        let t = mach_continuous_time()
        if numer == denom { return t }
        // Split to avoid overflow: t * numer can pass 2^64 after weeks of uptime.
        return (t / denom) * numer + (t % denom) * numer / denom
    }
    public func sleep(nanos: UInt64) async throws {
        try await Task.sleep(nanoseconds: nanos)
    }
}

/// A clock that only moves when told to. Safe to share between tasks.
public final class ManualClock: BenchClock, @unchecked Sendable {
    private let lock = NSLock()
    private var now: UInt64

    public init(start: UInt64 = 1_000_000_000) { now = start }

    public var name: String { "manual-test-clock" }

    public func nowNanos() -> UInt64 { lock.withLock { now } }

    public func advance(nanos: UInt64) { lock.withLock { now &+= nanos } }

    public func advance(seconds: Double) { advance(nanos: UInt64(max(0, seconds) * 1e9)) }

    public func sleep(nanos: UInt64) async throws {
        try Task.checkCancellation()
        advance(nanos: nanos)
        await Task.yield()
    }
}
