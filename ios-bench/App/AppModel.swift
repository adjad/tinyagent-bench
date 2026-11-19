import Foundation
import Observation
import UIKit

/// The app's state: model folders on the phone, runs on disk, the active run and its live
/// progress. Every measurement lives in EdgeBenchCore; this only wires it to UIKit.
@MainActor @Observable
final class AppModel {
    enum Engine: String, CaseIterable, Identifiable {
        case mlx = "MLX (on-device model)"
        case stub = "Stub (no model, simulated numbers)"
        var id: String { rawValue }
    }

    static let documents = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
    static let modelsRoot = documents.appendingPathComponent("Models", isDirectory: true)
    static let packsRoot = documents.appendingPathComponent("Packs", isDirectory: true)
    static let itemsRoot = documents.appendingPathComponent("PhoneItems", isDirectory: true)
    static let runsRoot = documents.appendingPathComponent("Runs", isDirectory: true)
    static let exportsRoot = documents.appendingPathComponent("Exports", isDirectory: true)
    static let stubRoot = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        .appendingPathComponent("StubModels", isDirectory: true)
    static let reservedFolders: Set<String> = ["Models", "Packs", "PhoneItems", "Runs", "Exports", "Inbox"]

    static var mlxAvailable: Bool {
        #if canImport(EdgeBenchMLX) && !targetEnvironment(simulator)
        true
        #else
        false
        #endif
    }

    static var isSimulator: Bool {
        #if targetEnvironment(simulator)
        true
        #else
        false
        #endif
    }

    let store = RunStore(root: AppModel.runsRoot)
    let conditions = UIKitConditionsProbe()
    let memoryProbe: MemoryProbe = TaskMemoryProbe()
    let memoryWarnings = MemoryWarningCounter()
    let clock = MachClock()
    let prompts: PromptBuilder
    let knownManifests: [String: [String: String]]

    var engine: Engine = AppModel.mlxAvailable ? .mlx : .stub
    var variants: [ModelVariant] = []
    var packs: [String: URL] = [:]          // model folder id → pack file
    var phoneItems: PhoneItemSet?
    var phoneItemsError: String?
    var runs: [RunManifest] = []
    var progress: RunProgress?
    var activeRunID: String?
    var lastError: String?
    var recovered: [String] = []
    var busyMessage: String?

    private var runTask: Task<Void, Never>?
    private var generator: BenchGenerator?
    private var stubMemory: StubMemory?
    private var savedBrightness: CGFloat?

    init() {
        let corpusURL = Bundle.main.url(forResource: "corpus-agent-v0", withExtension: "txt")!
        prompts = PromptBuilder(corpus: (try? String(contentsOf: corpusURL, encoding: .utf8)) ?? "")
        knownManifests = Bundle.main.url(forResource: "known-models", withExtension: "json")
            .flatMap { try? Data(contentsOf: $0) }
            .flatMap { try? JSONDecoder().decode([String: [String: String]].self, from: $0) } ?? [:]
        for dir in [Self.modelsRoot, Self.packsRoot, Self.itemsRoot, Self.runsRoot, Self.exportsRoot] {
            try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        }
        var models = Self.modelsRoot
        var rv = URLResourceValues()
        rv.isExcludedFromBackup = true
        try? models.setResourceValues(rv)
        NotificationCenter.default.addObserver(forName: UIApplication.didReceiveMemoryWarningNotification, object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.memoryWarnings.increment() }
        }
        // An inflight.json left by a killed run becomes that sample's result before anything else runs.
        recovered = store.recoverInterruptedRuns(clock: clock, deviceModel: DeviceIdentity.machine, os: DeviceIdentity.osDescription())
        refreshModels()
        refreshRuns()
    }

    // MARK: models, packs and items

    func refreshModels() {
        var found = ModelScanner.scan(root: Self.modelsRoot)
        // Finder / Files drops land in Documents itself.
        found += ModelScanner.scan(root: Self.documents).filter { !Self.reservedFolders.contains($0.id) }
        if engine == .stub || !Self.mlxAvailable { found.insert(contentsOf: stubVariants(), at: 0) }
        let order: [ModelRole: Int] = [.baseline: 0, .reference: 1, .variant: 2, .candidate: 3]
        variants = found.map(withSavedHashes).sorted { (order[$0.entry.role] ?? 9, $0.id) < (order[$1.entry.role] ?? 9, $1.id) }
        refreshPacks()
        refreshPhoneItems()
    }

    /// Prompt packs dropped into Documents/Packs, matched to a model by the pack's `model_id`.
    func refreshPacks() {
        var out = [String: URL]()
        let files = (try? FileManager.default.contentsOfDirectory(at: Self.packsRoot, includingPropertiesForKeys: nil)) ?? []
        for url in files where url.pathExtension == "json" {
            guard let data = try? Data(contentsOf: url), let loaded = try? PromptPack.load(data) else { continue }
            let pack = loaded.0
            for v in variants where v.entry.id == pack.model_id || v.id == pack.model_id { out[v.id] = url }
        }
        packs = out
    }

    /// The published parity subset if it has been copied to Documents/PhoneItems, otherwise the
    /// bundled stand-in (32 items converted from the portable cases; not the published subset).
    func refreshPhoneItems() {
        let fm = FileManager.default
        let dropped = ((try? fm.contentsOfDirectory(at: Self.itemsRoot, includingPropertiesForKeys: nil)) ?? [])
            .first { $0.pathExtension == "jsonl" }
        let url = dropped ?? Bundle.main.url(forResource: "phone-items-standin", withExtension: "jsonl")
        guard let url, let data = try? Data(contentsOf: url) else { phoneItems = nil; return }
        let manifestURL = url.deletingPathExtension().appendingPathExtension("manifest.json")
        do {
            phoneItems = try PhoneItemSet.load(jsonl: data, manifest: try? Data(contentsOf: manifestURL))
            phoneItemsError = nil
        } catch {
            phoneItems = nil
            phoneItemsError = "\(url.lastPathComponent): \(error.localizedDescription)"
        }
    }

    /// Two tiny folders with a Gemma-4-shaped config, so stub runs carry plausible model facts
    /// and the bracket plan has a primary and a reference.
    func stubVariants() -> [ModelVariant] {
        ["stub-gemma-4-E4B-wisp-shape", "stub-gemma-4-E4B-base-shape"].compactMap { name in
            let dir = Self.stubRoot.appendingPathComponent(name)
            if !FileManager.default.fileExists(atPath: dir.appendingPathComponent("config.json").path) {
                try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
                let config = #"{"model_type":"gemma4","architectures":["Gemma4ForConditionalGeneration"],"text_config":{"model_type":"gemma4_text","num_hidden_layers":42,"hidden_size":2560,"max_position_embeddings":131072,"sliding_window":512},"quantization":{"group_size":64,"bits":4,"mode":"affine"}}"#
                try? Data(config.utf8).write(to: dir.appendingPathComponent("config.json"))
                try? Data("{}".utf8).write(to: dir.appendingPathComponent("tokenizer.json"))
                try? Data("{}".utf8).write(to: dir.appendingPathComponent("tokenizer_config.json"))
                try? Data().write(to: dir.appendingPathComponent("model.safetensors"))
            }
            return try? ModelScanner.inspect(directory: dir)
        }
    }

    /// The default model for a slot: the shipped model, else its iPhone fallback variant.
    func defaultModel(for slot: ModelSlot) -> ModelVariant? {
        let want: ModelRole = slot == .primary ? .baseline : .reference
        let id = slot == .primary ? ModelCatalog.baselineID : ModelCatalog.referenceID
        if let v = variants.first(where: { $0.entry.role == want }) { return v }
        if let v = variants.first(where: { $0.entry.role == .variant && $0.entry.variantOf == id }) { return v }
        let stubs = variants.filter { $0.id.hasPrefix("stub-") }
        if !stubs.isEmpty {
            let shape = slot == .primary ? "wisp-shape" : "base-shape"
            return stubs.first { $0.id.hasSuffix(shape) } ?? (slot == .primary ? stubs.first : stubs.dropFirst().first)
        }
        return slot == .primary ? variants.first : nil
    }

    /// Copies a picked folder or file (Files app, iCloud Drive, USB drive) into Documents:
    /// model folders to Models, `*.json` prompt packs to Packs, `*.jsonl` items to PhoneItems.
    func importItem(_ url: URL) async {
        busyMessage = "Copying \(url.lastPathComponent)…"
        defer { busyMessage = nil }
        let access = url.startAccessingSecurityScopedResource()
        defer { if access { url.stopAccessingSecurityScopedResource() } }
        let root: URL = switch url.pathExtension {
        case "json": Self.packsRoot
        case "jsonl": Self.itemsRoot
        default: Self.modelsRoot
        }
        let dest = root.appendingPathComponent(url.lastPathComponent)
        do {
            try await Task.detached(priority: .userInitiated) {
                if FileManager.default.fileExists(atPath: dest.path) { try FileManager.default.removeItem(at: dest) }
                try FileManager.default.copyItem(at: url, to: dest)
            }.value
        } catch {
            lastError = "Import failed: \(error.localizedDescription)"
        }
        refreshModels()
    }

    func delete(_ v: ModelVariant) {
        try? FileManager.default.removeItem(at: v.directory)
        refreshModels()
    }

    /// Hashes every file and compares with a known manifest (Harbor's pinned sha256 list, and
    /// the base model's hashes taken on the Mac).
    func verify(_ v: ModelVariant) async -> String {
        busyMessage = "Hashing \(v.id) (about a minute per 5 GB)…"
        defer { busyMessage = nil }
        let manifest = knownManifests[v.id] ?? knownManifests[v.entry.id]
        var lines = [String]()
        var updated = v
        for (i, f) in v.files.enumerated() {
            let sha = await Task.detached(priority: .userInitiated) { try? ModelScanner.sha256(of: v.directory.appendingPathComponent(f.name)) }.value
            updated.files[i].sha256 = sha
            if let want = manifest?[f.name] { lines.append("\(f.name): \(sha == want ? "matches" : "MISMATCH")") }
        }
        if let i = variants.firstIndex(where: { $0.id == v.id }) { variants[i] = updated }
        try? JSONEncoder.pretty.encode(updated.files).write(to: v.directory.appendingPathComponent(".edat-sha256.json"))
        if manifest == nil { return "Hashed \(v.files.count) files; no known manifest to compare with. The run records these hashes." }
        let missing = (manifest?.keys.filter { k in !v.files.contains { $0.name == k } } ?? []).sorted()
        if !missing.isEmpty { lines.append("missing: \(missing.joined(separator: ", "))") }
        return lines.contains { $0.hasSuffix("MISMATCH") || $0.hasPrefix("missing") } ? lines.joined(separator: "\n") : "All \(lines.count) pinned files match."
    }

    /// Hashes saved by an earlier verify, so records carry sha256 without rehashing.
    func withSavedHashes(_ v: ModelVariant) -> ModelVariant {
        guard let data = try? Data(contentsOf: v.directory.appendingPathComponent(".edat-sha256.json")),
              let files = try? JSONDecoder().decode([ModelFile].self, from: data) else { return v }
        var out = v
        for (i, f) in out.files.enumerated() { if let s = files.first(where: { $0.name == f.name && $0.bytes == f.bytes })?.sha256 { out.files[i].sha256 = s } }
        return out
    }

    /// Hashes the files that have no sha256 yet (saved hashes are reused), so every record
    /// carries `models[].files[].sha256` and the results store can compute `weights_sha256`.
    func hashedForRun(_ v: ModelVariant) async -> ModelVariant {
        var out = withSavedHashes(v)
        let missing = out.files.indices.filter { out.files[$0].sha256 == nil }
        guard !missing.isEmpty else { return out }
        busyMessage = "Hashing \(v.id) before the run (about a minute per 5 GB, once)…"
        defer { busyMessage = nil }
        for i in missing {
            let url = v.directory.appendingPathComponent(out.files[i].name)
            out.files[i].sha256 = await Task.detached(priority: .userInitiated) { try? ModelScanner.sha256(of: url) }.value
        }
        if let i = variants.firstIndex(where: { $0.id == v.id }) { variants[i] = out }
        try? JSONEncoder.pretty.encode(out.files).write(to: v.directory.appendingPathComponent(".edat-sha256.json"))
        return out
    }

    // MARK: runs

    func refreshRuns() { runs = store.allRuns() }

    var isRunning: Bool { runTask != nil }

    func makeGenerator() -> (BenchGenerator, MemoryProbe) {
        if let g = generator { return (g, engine == .stub ? StubMemoryProbe(memory: stubMemory!) : memoryProbe) }
        switch engine {
        case .mlx:
            #if canImport(EdgeBenchMLX) && !targetEnvironment(simulator)
            let g = MLXGenerator()
            generator = g
            return (g, memoryProbe)
            #else
            fallthrough
            #endif
        case .stub:
            let mem = StubMemory(baseMiB: memoryProbe.read().footprintMiB, budgetMiB: 6_144)
            var profile = StubGenerator.Profile()
            // Fast enough that a Simulator smoke run finishes in a couple of minutes.
            profile.prefillTPS = 4_000; profile.decodeTPS = 120; profile.loadSeconds = 0.8; profile.coldExtraSeconds = 1.0
            let g = StubGenerator(profile: profile, clock: clock, memory: mem)
            stubMemory = mem
            generator = g
            return (g, StubMemoryProbe(memory: mem))
        }
    }

    func switchEngine(_ e: Engine) {
        guard !isRunning else { return }
        engine = e
        generator = nil
        refreshModels()
    }

    func device() -> DeviceIdentity {
        DeviceIdentity.collect(memory: memoryProbe.read(), entitlements: Provisioning.entitlements(), storageFreeBytes: Storage.freeBytes)
    }

    func start(plan: TestPlan, models: [ModelSlot: ModelVariant], operatorInput: OperatorAttestation) {
        guard !isRunning else { return }
        let (gen, probe) = makeGenerator()
        let runner = makeRunner(gen, probe)
        let dev = device()
        let packs = packs
        beginRunScreen()
        runTask = Task { [weak self] in
            do {
                var models = models
                for (slot, v) in models { models[slot] = await self?.hashedForRun(v) ?? v }
                let id = try await runner.create(plan: plan, models: models, packs: packs, device: dev, operatorInput: operatorInput,
                                                 appCommit: Bundle.main.object(forInfoDictionaryKey: "EDATCommit") as? String)
                await self?.didCreate(id)
                try await runner.run(id)
            } catch {
                await self?.failed(error)
            }
            await self?.finished()
        }
    }

    func resume(_ runID: String) {
        guard !isRunning else { return }
        let (gen, probe) = makeGenerator()
        let runner = makeRunner(gen, probe)
        activeRunID = runID
        beginRunScreen()
        runTask = Task { [weak self] in
            do { try await runner.run(runID) } catch { await self?.failed(error) }
            await self?.finished()
        }
    }

    func stop() { runTask?.cancel() }

    /// iPhone apps get no GPU time in the background: leaving the app voids the sample in
    /// flight (marked now, before iOS can suspend us) and pauses the run.
    func didEnterBackground() {
        guard isRunning, let id = activeRunID else { return }
        store.markBackgrounded(id)
        stop()
    }

    /// Screen on at minimum brightness for the whole run (spec 04 §2.2, D10).
    private func beginRunScreen() {
        UIApplication.shared.isIdleTimerDisabled = true
        if savedBrightness == nil { savedBrightness = UIScreen.main.brightness }
        UIScreen.main.brightness = 0.0
    }

    private func endRunScreen() {
        UIApplication.shared.isIdleTimerDisabled = false
        if let b = savedBrightness { UIScreen.main.brightness = b }
        savedBrightness = nil
    }

    private func makeRunner(_ gen: BenchGenerator, _ probe: MemoryProbe) -> BenchRunner {
        var balloon: BalloonAllocator?
        if Self.isSimulator {
            balloon = nil   // the Simulator has no jetsam limit: a balloon would only fill the Mac's memory
        } else if engine == .stub, let mem = stubMemory {
            balloon = StubBalloonAllocator(memory: mem)
        } else {
            balloon = MetalBalloonAllocator()
        }
        let deps = RunnerDependencies(generator: gen, memoryProbe: probe, conditionsProbe: conditions, clock: clock,
                                      store: store, prompts: prompts, phoneItems: phoneItems, balloon: balloon,
                                      memoryWarnings: memoryWarnings)
        return BenchRunner(deps: deps) { p in
            Task { @MainActor [weak self] in self?.progress = p }
        }
    }

    private func didCreate(_ id: String) {
        activeRunID = id
        refreshRuns()
    }

    private func failed(_ error: Error) {
        if error is SimulatedProcessDeath {
            // Stub only: end the process the way jetsam would, so the next launch exercises
            // the same recovery path a real kill takes.
            exit(0)
        }
        lastError = error.localizedDescription
    }

    private func finished() {
        runTask = nil
        endRunScreen()
        refreshRuns()
        if automated, let id = activeRunID {
            if let url = try? exportFile(id) { print("EDAT_EXPORT \(url.path)") }
            print("EDAT_STATE \((try? store.manifest(id).state.rawValue) ?? "?")")
        }
    }

    func delete(run id: String) {
        try? store.delete(id)
        refreshRuns()
    }

    /// Writes the run's export (record, footprint log, phone results, sha256 manifest) and
    /// returns the zip for the share sheet. Exports also stay in Files → On My iPhone → tinyagent-bench → Exports.
    func exportFile(_ id: String) throws -> URL {
        try RunExporter.export(runID: id, store: store, to: Self.exportsRoot)
    }

    func samples(_ id: String) -> [PerfSample] { store.samples(id) }
    func phoneResults(_ id: String) -> [PhoneResult] { store.phoneResults(id) }
    func events(_ id: String) -> [RecordEvent] { store.events(id) }

    // MARK: launch-argument automation (Simulator checks and scripted runs)

    /// `-EDATAutoRun <plan id>` starts that plan with the default models; `-EDATAutoResume 1`
    /// resumes the newest unfinished run; `-EDATAutoKillAtCall <n>` makes the stub die at its
    /// nth generate call (a simulated jetsam kill). Finished automated runs are exported to
    /// Documents/Exports and the path is logged.
    var automated = false

    func autoRunIfRequested() {
        let d = UserDefaults.standard
        if engine == .stub, let n = d.string(forKey: "EDATAutoKillAtCall").flatMap(Int.init) {
            _ = makeGenerator()
            (generator as? StubGenerator)?.dieOnGenerateCall = n
        }
        if d.bool(forKey: "EDATAutoResume"), let m = runs.first(where: { $0.state != .complete }) {
            automated = true
            resume(m.runID)
        } else if let planID = d.string(forKey: "EDATAutoRun"), let plan = TestPlan.builtIn.first(where: { $0.id == planID }) {
            var models = [ModelSlot: ModelVariant]()
            for slot in plan.slots { models[slot] = defaultModel(for: slot) }
            automated = true
            start(plan: plan, models: models, operatorInput: OperatorAttestation(note: "automation: launch argument"))
        }
    }
}
