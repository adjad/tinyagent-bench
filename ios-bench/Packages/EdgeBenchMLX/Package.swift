// swift-tools-version: 6.2
// EdgeBenchMLX: the iPhone runner's MLX engine. Same pins as Harbor's iPhone app
// (ios/Packages/HarborLing): mlx-swift-lm 3.32.3 with its default traits off (no
// FoundationModels adapter, no vendored xgrammar), mlx-swift 0.32.3, swift-transformers 1.3.5.
import PackageDescription

let package = Package(
    name: "EdgeBenchMLX",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [
        .library(name: "EdgeBenchMLX", targets: ["EdgeBenchMLX"]),
    ],
    dependencies: [
        .package(url: "https://github.com/ml-explore/mlx-swift-lm", exact: "3.32.3", traits: []),
        .package(url: "https://github.com/ml-explore/mlx-swift", exact: "0.32.3"),
        .package(url: "https://github.com/huggingface/swift-transformers", from: "1.3.5"),
    ],
    targets: [
        .target(
            name: "EdgeBenchMLX",
            dependencies: [
                .product(name: "MLXLLM", package: "mlx-swift-lm"),
                .product(name: "MLXLMCommon", package: "mlx-swift-lm"),
                .product(name: "MLX", package: "mlx-swift"),
                .product(name: "Tokenizers", package: "swift-transformers"),
            ]
        ),
    ]
)
