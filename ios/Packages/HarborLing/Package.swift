// swift-tools-version: 6.2
import PackageDescription

let package = Package(
    name: "HarborLing",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [
        .library(name: "HarborLing", targets: ["HarborLing"]),
        .executable(name: "ling-parity", targets: ["LingParity"]),
    ],
    dependencies: [
        // FoundationModelsIntegration (default trait) is disabled: HarborLing needs
        // neither the FoundationModels adapter nor the vendored xgrammar build.
        .package(url: "https://github.com/ml-explore/mlx-swift-lm", exact: "3.32.3", traits: []),
        // Pinned to the version mlx-swift-lm 3.32.3 resolves (bundles MLX core 0.32.2).
        .package(url: "https://github.com/ml-explore/mlx-swift", exact: "0.32.3"),
        .package(url: "https://github.com/huggingface/swift-transformers", from: "1.3.5"),
    ],
    targets: [
        .target(
            name: "HarborLing",
            dependencies: [
                .product(name: "MLXLLM", package: "mlx-swift-lm"),
                .product(name: "MLXLMCommon", package: "mlx-swift-lm"),
                .product(name: "MLX", package: "mlx-swift"),
                .product(name: "MLXNN", package: "mlx-swift"),
                .product(name: "Tokenizers", package: "swift-transformers"),
            ]
        ),
        .executableTarget(
            name: "LingParity",
            dependencies: [
                "HarborLing",
                .product(name: "MLX", package: "mlx-swift"),
                .product(name: "MLXLMCommon", package: "mlx-swift-lm"),
            ]
        ),
    ]
)
