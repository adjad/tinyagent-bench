import Foundation
@testable import EdgeBenchCore

/// A conditions probe whose readings tests script. The phone is "hot" until the manual
/// clock passes `hotUntilNanos`.
final class ScriptedConditions: ConditionsProbe, @unchecked Sendable {
    private let lock = NSLock()
    let clock: ManualClock
    var hotUntilNanos: UInt64 = 0
    var hotLevel: ThermalLevel = .serious
    var battery: Double? = 85
    var source: PowerSource = .battery
    var lowPower = false

    init(clock: ManualClock) { self.clock = clock }

    func read() -> Conditions {
        let now = clock.nowNanos()
        return lock.withLock {
            Conditions(thermal: now < hotUntilNanos ? hotLevel : .nominal, powerSource: source, batteryPct: battery,
                       lowPowerMode: lowPower, screenBrightness: 0, appState: "active", uptimeS: 900)
        }
    }

    func set(_ change: (ScriptedConditions) -> Void) { lock.withLock { change(self) } }
}

enum Paths {
    static let packageRoot = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
    static let resources = packageRoot.appendingPathComponent("Resources")
}

struct TestEnv {
    let clock = ManualClock()
    let memory: StubMemory
    let generator: StubGenerator
    let conditions: ScriptedConditions
    let store: RunStore
    let root: URL
    let deps: RunnerDependencies
    let wisp: ModelVariant
    let base: ModelVariant
    let device: DeviceIdentity
    let balloon: StubBalloonAllocator

    init(profile: StubGenerator.Profile = .init(), budgetMiB: Double = 6_144, items: Bool = true) throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent("edat-tests-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        memory = StubMemory(baseMiB: 120, budgetMiB: budgetMiB)
        generator = StubGenerator(profile: profile, clock: clock, memory: memory)
        conditions = ScriptedConditions(clock: clock)
        store = RunStore(root: root.appendingPathComponent("Runs"))
        balloon = StubBalloonAllocator(memory: memory)
        let corpus = try String(contentsOf: Paths.resources.appendingPathComponent("corpus-agent-v0.txt"), encoding: .utf8)
        var set: PhoneItemSet?
        if items {
            set = try PhoneItemSet.load(jsonl: Data(contentsOf: Paths.resources.appendingPathComponent("phone-items-standin.jsonl")),
                                        manifest: try? Data(contentsOf: Paths.resources.appendingPathComponent("phone-items-standin.manifest.json")))
        }
        let clock = clock
        let start = Date(timeIntervalSince1970: 1_795_000_000)
        deps = RunnerDependencies(generator: generator, memoryProbe: StubMemoryProbe(memory: memory), conditionsProbe: conditions,
                                  clock: clock, store: store, prompts: PromptBuilder(corpus: corpus), phoneItems: set, balloon: balloon,
                                  now: { start.addingTimeInterval(Double(clock.nowNanos()) / 1e9) })
        wisp = try ModelScanner.inspect(directory: Fixtures.makeModelFolder(in: root, name: ModelCatalog.baselineFolder))
        base = try ModelScanner.inspect(directory: Fixtures.makeModelFolder(in: root, name: ModelCatalog.referenceID))
        device = DeviceIdentity(deviceClass: "test", model: "Test1,1", marketingName: "Test Phone", soc: nil, ramGB: 12, os: "test",
                                isSimulator: true, measuredAppBudgetMiB: budgetMiB, budgetSource: "stub", entitlements: [:], storageFreeGB: nil)
    }

    func runner(onProgress: @escaping @Sendable (RunProgress) -> Void = { _ in }) -> BenchRunner {
        BenchRunner(deps: deps, onProgress: onProgress)
    }

    /// Runs to completion, treating each `SimulatedProcessDeath` as a jetsam kill: the next
    /// "launch" recovers inflight.json and resumes, as the app does.
    func runThroughKills(_ id: String, maxLaunches: Int = 10) async throws -> Int {
        var kills = 0
        for _ in 0 ..< maxLaunches {
            do {
                try await runner().run(id)
                if try store.manifest(id).state == .complete { return kills }
            } catch is SimulatedProcessDeath {
                kills += 1
                await generator.unload()
                store.recoverInterruptedRuns(clock: clock, deviceModel: "Test1,1", os: "test")
            }
        }
        return kills
    }

    func plan(_ steps: [PlanStep], configure: (inout PlanSettings) -> Void = { _ in }) -> TestPlan {
        var s = PlanSettings()
        configure(&s)
        return TestPlan(id: "test", title: "test", summary: "", estimatedMinutes: 1, steps: steps, settings: s)
    }
}

enum Fixtures {
    /// A model folder with a Gemma-4-like config and safetensors files holding headers only.
    static func makeModelFolder(in root: URL, name: String = "tiny-gemma-oQ4e-mtp") throws -> URL {
        let dir = root.appendingPathComponent(name)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let config: [String: Any] = [
            "model_type": "gemma4", "architectures": ["Gemma4ForConditionalGeneration"],
            "text_config": ["model_type": "gemma4_text", "num_hidden_layers": 4, "hidden_size": 64,
                            "max_position_embeddings": 131_072, "sliding_window": 512],
            "quantization": ["group_size": 64, "bits": 4, "mode": "affine",
                             "language_model.model.layers.0.mlp.down_proj": ["group_size": 64, "bits": 6],
                             "language_model.model.layers.1.mlp.down_proj": ["group_size": 64, "bits": 5]],
        ]
        try JSONSerialization.data(withJSONObject: config).write(to: dir.appendingPathComponent("config.json"))
        try Data("{}".utf8).write(to: dir.appendingPathComponent("tokenizer.json"))
        try Data("{}".utf8).write(to: dir.appendingPathComponent("tokenizer_config.json"))
        try Data("{{ messages }}".utf8).write(to: dir.appendingPathComponent("chat_template.jinja"))
        try writeSafetensors(dir.appendingPathComponent("model-00001-of-00001.safetensors"), tensors: [
            ("language_model.model.layers.0.mlp.down_proj.weight", 1000),
            ("language_model.model.embed_tokens_per_layer.weight", 500),
            ("vision_tower.encoder.layers.0.weight", 300),
            ("audio_tower.layers.0.weight", 200),
        ])
        try writeSafetensors(dir.appendingPathComponent("model-mtp.safetensors"), tensors: [("language_model.mtp.model.norm.weight", 50)])
        return dir
    }

    static func writeSafetensors(_ url: URL, tensors: [(String, Int)]) throws {
        var header = [String: Any]()
        var offset = 0
        for (name, bytes) in tensors {
            header[name] = ["dtype": "U8", "shape": [bytes], "data_offsets": [offset, offset + bytes]]
            offset += bytes
        }
        header["__metadata__"] = ["format": "mlx"]
        let json = try JSONSerialization.data(withJSONObject: header)
        var data = Data()
        var len = UInt64(json.count).littleEndian
        data.append(Data(bytes: &len, count: 8))
        data.append(json)
        data.append(Data(count: offset))
        try data.write(to: url)
    }
}
