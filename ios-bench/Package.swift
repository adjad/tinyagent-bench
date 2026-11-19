// swift-tools-version: 6.0
// EdgeBenchCore: the iPhone runner's measurement core (schedule, timing, statistics,
// probes, persistence, records, quality scoring, stub generator). No MLX dependency,
// so `swift test --package-path ios-bench` runs on the Mac without a model.
import PackageDescription

let package = Package(
    name: "EdgeBenchRunner",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [
        .library(name: "EdgeBenchCore", targets: ["EdgeBenchCore"]),
    ],
    targets: [
        .target(name: "EdgeBenchCore"),
        .testTarget(
            name: "EdgeBenchCoreTests",
            dependencies: ["EdgeBenchCore"]
        ),
    ]
)
