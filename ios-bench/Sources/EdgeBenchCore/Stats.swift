import Foundation

/// Summary statistics for one metric in one cell, in the shape of spec 04 §11:
/// `{"median", "iqr": [p25, p75], "min", "max", "ci95": [lo, hi], "p95"?, "mean", "stdev", "n"}`.
///
/// Percentiles are nearest-rank, as Harbor's `scripts/release_performance.py:231-250`
/// (`rank = ceil(p·n/100)`), so the Mac and the phone agree on every value. The median is
/// nearest-rank p50 too, i.e. the lower middle for even n. p95 is reported only when
/// n ≥ 20 (spec 04 §3.4); with fewer reps nearest-rank p95 is just the max.
public struct Summary: Codable, Sendable, Equatable {
    public var n: Int
    public var median: Double
    public var iqr: [Double]
    public var min: Double
    public var max: Double
    public var ci95: [Double]?
    public var p95: Double?
    public var mean: Double
    public var stdev: Double?
}

public enum Stats {
    /// Nearest-rank percentile; `values` must be non-empty.
    public static func nearestRank(_ values: [Double], _ percentile: Int) -> Double {
        precondition(!values.isEmpty, "percentile of an empty sample")
        let ordered = values.sorted()
        let rank = Swift.max(1, (percentile * ordered.count + 99) / 100)
        return ordered[rank - 1]
    }

    public static func mean(_ v: [Double]) -> Double { v.reduce(0, +) / Double(v.count) }

    /// Sample standard deviation (n−1); nil when n < 2.
    public static func stdev(_ v: [Double]) -> Double? {
        guard v.count >= 2 else { return nil }
        let m = mean(v)
        return (v.map { ($0 - m) * ($0 - m) }.reduce(0, +) / Double(v.count - 1)).squareRoot()
    }

    /// Percentile bootstrap 95 % CI of the nearest-rank median, from a fixed seed so the
    /// same samples always give the same interval. nil when n < 3.
    public static func bootstrapMedianCI(_ v: [Double], resamples: Int = 2000, seed: UInt64 = 0x5EED) -> [Double]? {
        guard v.count >= 3 else { return nil }
        var rng = SplitMix64(seed: seed)
        var medians = [Double]()
        medians.reserveCapacity(resamples)
        var draw = [Double](repeating: 0, count: v.count)
        for _ in 0 ..< resamples {
            for i in draw.indices { draw[i] = v[Int(rng.next() % UInt64(v.count))] }
            medians.append(nearestRank(draw, 50))
        }
        medians.sort()
        let lo = medians[Int(Double(resamples) * 0.025)]
        let hi = medians[Swift.min(resamples - 1, Int(Double(resamples) * 0.975))]
        return [lo, hi]
    }

    /// nil for an empty sample: a missing measurement is never summarised as zero.
    public static func summarize(_ values: [Double]) -> Summary? {
        let v = values.filter { $0.isFinite }
        guard !v.isEmpty else { return nil }
        return Summary(
            n: v.count,
            median: nearestRank(v, 50),
            iqr: [nearestRank(v, 25), nearestRank(v, 75)],
            min: v.min()!, max: v.max()!,
            ci95: bootstrapMedianCI(v),
            p95: v.count >= 20 ? nearestRank(v, 95) : nil,
            mean: mean(v), stdev: stdev(v))
    }

    /// Least-squares slope and intercept of y on x; nil with fewer than 2 distinct x.
    public static func linearFit(x: [Double], y: [Double]) -> (slope: Double, intercept: Double)? {
        guard x.count == y.count, x.count >= 2, Set(x).count >= 2 else { return nil }
        let mx = mean(x), my = mean(y)
        let sxy = zip(x, y).map { ($0 - mx) * ($1 - my) }.reduce(0, +)
        let sxx = x.map { ($0 - mx) * ($0 - mx) }.reduce(0, +)
        let slope = sxy / sxx
        return (slope, my - slope * mx)
    }
}

/// Small, fast, seedable PRNG (public-domain SplitMix64). Used for bootstrap resampling,
/// nonces and the stub generator, so every test is reproducible.
public struct SplitMix64: RandomNumberGenerator, Sendable {
    private var state: UInt64
    public init(seed: UInt64) { state = seed }
    public mutating func next() -> UInt64 {
        state &+= 0x9E37_79B9_7F4A_7C15
        var z = state
        z = (z ^ (z >> 30)) &* 0xBF58_476D_1CE4_E5B9
        z = (z ^ (z >> 27)) &* 0x94D0_49BB_1331_11EB
        return z ^ (z >> 31)
    }
}
