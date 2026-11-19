import Foundation

/// Counts memory warnings; the app increments it from UIKit's notification. The runner
/// installs a sink that writes the event line at once (spec 04 §2.8 item 2).
public final class MemoryWarningCounter: @unchecked Sendable {
    private let lock = NSLock()
    private var n = 0
    private var sink: (@Sendable () -> Void)?
    public init() {}
    public func increment() {
        let s = lock.withLock { () -> (@Sendable () -> Void)? in n += 1; return sink }
        s?()
    }
    public func setSink(_ s: (@Sendable () -> Void)?) { lock.withLock { sink = s } }
    public var value: Int { lock.withLock { n } }
}

/// What the live screen shows.
public struct RunProgress: Sendable, Equatable {
    public var runID: String
    public var unitIndex: Int
    public var unitCount: Int
    public var label: String
    public var modelID: String?
    public var phase: String               // loading | prefill | decode | cooldown | waiting | gap | idle | done
    public var liveDecodeTPS: Double?
    public var lastDecodeTPS: Double?
    public var lastPrefillTPS: Double?
    public var lastTTFT: Double?
    public var footprintBytes: UInt64?
    public var availableBytes: UInt64?
    public var conditions: Conditions?
    public var lastStatus: String?
    public var message: String?

    public init(runID: String, unitIndex: Int, unitCount: Int, label: String, phase: String) {
        self.runID = runID
        self.unitIndex = unitIndex
        self.unitCount = unitCount
        self.label = label
        self.phase = phase
    }
}

public struct RunnerDependencies: Sendable {
    public var generator: BenchGenerator
    public var memoryProbe: MemoryProbe
    public var conditionsProbe: ConditionsProbe
    public var clock: BenchClock
    public var store: RunStore
    /// Stand-in prompts when a model has no prompt pack (not comparable with the Mac).
    public var prompts: PromptBuilder
    public var phoneItems: PhoneItemSet?
    public var balloon: BalloonAllocator?
    public var memoryWarnings: MemoryWarningCounter
    /// Wall clock, for time-boxed steps and the record's UTC times.
    public var now: @Sendable () -> Date

    public init(generator: BenchGenerator, memoryProbe: MemoryProbe, conditionsProbe: ConditionsProbe, clock: BenchClock,
                store: RunStore, prompts: PromptBuilder, phoneItems: PhoneItemSet? = nil, balloon: BalloonAllocator? = nil,
                memoryWarnings: MemoryWarningCounter = .init(), now: @escaping @Sendable () -> Date = { Date() }) {
        self.generator = generator
        self.memoryProbe = memoryProbe
        self.conditionsProbe = conditionsProbe
        self.clock = clock
        self.store = store
        self.prompts = prompts
        self.phoneItems = phoneItems
        self.balloon = balloon
        self.memoryWarnings = memoryWarnings
        self.now = now
    }
}

public enum RunnerError: LocalizedError {
    case noSuchRun(String)
    case alreadyComplete
    case slotEmpty(ModelSlot)
    case packMismatch(String)
    public var errorDescription: String? {
        switch self {
        case .noSuchRun(let id): "No run \(id)."
        case .alreadyComplete: "That run is already complete."
        case .slotEmpty(let s): "The plan needs a \(s.rawValue) model."
        case .packMismatch(let s): s
        }
    }
}

/// The sample currently running, shared with the memory-warning sink.
final class CurrentSample: @unchecked Sendable {
    private let lock = NSLock()
    private var id: String?
    private var warned = false
    func set(_ s: String?) { lock.withLock { id = s; warned = false } }
    func warn() -> String? { lock.withLock { warned = true; return id } }
    var sampleID: String? { lock.withLock { id } }
    var wasWarned: Bool { lock.withLock { warned } }
}

/// How a unit ended.
struct UnitOutcome {
    var sample: PerfSample?
    var phoneLine: Data?
    var status: String
    var final = true
    var duringLoad = false
}

/// Counts loads in this process, so a cold load is only claimed for the first one.
final class ProcessLoads: @unchecked Sendable {
    static let shared = ProcessLoads()
    private let lock = NSLock()
    private var n = 0
    func next() -> Int { lock.withLock { n += 1; return n } }
}

public actor BenchRunner {
    let deps: RunnerDependencies
    let onProgress: @Sendable (RunProgress) -> Void
    private var progress: RunProgress?
    private var lastProgressPush: UInt64 = 0
    private var loadedModel: Int?
    private var packs: [Int: PromptPack] = [:]
    private var lastThermal: ThermalLevel?
    private let current = CurrentSample()

    public init(deps: RunnerDependencies, onProgress: @escaping @Sendable (RunProgress) -> Void = { _ in }) {
        self.deps = deps
        self.onProgress = onProgress
    }

    // MARK: creating and resuming

    public static func makeRunID(now: Date, seed: UInt64) -> String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "UTC")
        f.dateFormat = "yyyy-MM-dd'T'HH-mm-ss'Z'"
        return f.string(from: now) + "-" + String(format: "%04x", seed & 0xFFFF)
    }

    /// Creates a run's manifest; call `run(_:)` next. `packs` maps a model folder id to its
    /// imported prompt pack file.
    public func create(plan: TestPlan, models slotModels: [ModelSlot: ModelVariant], packs: [String: URL] = [:],
                       device: DeviceIdentity, operatorInput: OperatorAttestation = .init(), appCommit: String? = nil,
                       seed: UInt64 = UInt64.random(in: 1 ... UInt64.max)) async throws -> String {
        let now = deps.now()
        let id = Self.makeRunID(now: now, seed: seed)
        var models = [RunModel]()
        var slots = [String: Int]()
        for slot in ModelSlot.allCases {
            guard let v = slotModels[slot], plan.slots.contains(slot) else { continue }
            if let i = models.firstIndex(where: { $0.variant.id == v.id }) { slots[slot.rawValue] = i; continue }
            models.append(try runModel(v, packURL: packs[v.id]))
            slots[slot.rawValue] = models.count - 1
        }
        for slot in plan.slots where slots[slot.rawValue] == nil { throw RunnerError.slotEmpty(slot) }
        let units = Schedule.expand(plan, models: Self.slotMap(slots), phoneItemCount: deps.phoneItems?.items.count ?? 0)
        let manifest = RunManifest(
            runID: id, createdAt: now, endedAt: nil, plan: plan, models: models, slots: slots,
            runtime: deps.generator.runtimeInfo, device: device, operatorInput: operatorInput,
            conditionsStart: deps.conditionsProbe.read(), conditionsEnd: nil, seed: seed, state: .paused, stopReason: nil,
            levelWords: [:], stepStartedAt: [:], unitCount: units.count,
            phoneItemsSHA256: deps.phoneItems?.sha256, phoneItemsCount: deps.phoneItems?.items.count ?? 0,
            phoneItemsSource: deps.phoneItems?.manifest?.source, appCommit: appCommit)
        try deps.store.create(manifest)
        return id
    }

    static func slotMap(_ s: [String: Int]) -> [ModelSlot: Int] {
        Dictionary(uniqueKeysWithValues: s.compactMap { k, v in ModelSlot(rawValue: k).map { ($0, v) } })
    }

    func runModel(_ v: ModelVariant, packURL: URL?) throws -> RunModel {
        let entry = v.entry
        if let url = packURL {
            let (pack, sha) = try PromptPack.load(Data(contentsOf: url))
            guard pack.model_id == entry.id || pack.model_id == v.id else {
                throw RunnerError.packMismatch("The prompt pack is for \(pack.model_id), not \(entry.id).")
            }
            return RunModel(variant: v, entry: entry, packPath: url.path, pack: RecordPromptPack(
                pack_sha256: pack.pack_sha256 ?? sha, corpus: pack.corpus, version: pack.version, language: pack.language,
                source: "pack", file_sha256: sha, tokenizer_sha256: pack.tokenizer_sha256, template_sha256: pack.template_sha256))
        }
        if deps.generator.runtimeInfo.name == "stub" {
            return RunModel(variant: v, entry: entry, packPath: nil, pack: RecordPromptPack(
                pack_sha256: nil, corpus: "synthetic-stub", version: "0", language: "en", source: "synthetic_stub",
                file_sha256: nil, tokenizer_sha256: nil, template_sha256: nil))
        }
        return RunModel(variant: v, entry: entry, packPath: nil, pack: RecordPromptPack(
            pack_sha256: nil, corpus: "provisional-ios-agent-v0", version: "0", language: "en", source: "on_device_builder",
            file_sha256: deps.prompts.corpusSHA256, tokenizer_sha256: nil, template_sha256: nil))
    }

    func expandUnits(_ m: RunManifest) -> [WorkUnit] {
        Schedule.expand(m.plan, models: m.slotMap, phoneItemCount: m.phoneItemsCount)
    }

    func pack(_ m: RunManifest, model: Int) throws -> PromptPack? {
        if let p = packs[model] { return p }
        let rm = m.models[model]
        var p: PromptPack?
        if let path = rm.packPath {
            p = try PromptPack.load(Data(contentsOf: URL(fileURLWithPath: path))).0
        } else if rm.pack.source == "synthetic_stub" {
            p = PromptPack.synthetic(modelID: rm.entry.id, levels: TestPlan.ladder)
        }
        packs[model] = p
        return p
    }

    /// Runs (or resumes) a run until it completes, is stopped, or pauses itself (battery floor,
    /// background). Cancelling the calling task pauses the run; it can be resumed.
    public func run(_ runID: String) async throws {
        let store = deps.store
        var m = try store.manifest(runID)
        guard m.state != .complete else { throw RunnerError.alreadyComplete }
        let units = expandUnits(m)
        let start = store.nextUnitIndex(runID)
        m.state = .running
        m.stopReason = nil
        try store.save(m)
        event(runID, start > 0 ? "resumed" : "started", m.plan.id)
        let cur = self.current
        let clock = deps.clock
        deps.memoryWarnings.setSink { [store, cur, clock] in
            let id = cur.warn()
            try? store.append(RecordEvent(t_ns: clock.nowNanos(), kind: "memory_warning", detail: id ?? ""), to: runID)
        }
        defer { deps.memoryWarnings.setSink(nil) }
        push(RunProgress(runID: runID, unitIndex: start, unitCount: units.count, label: "Starting", phase: "idle"))

        for unit in units where unit.index >= start {
            if Task.isCancelled { try pause(runID, reason: "stopped by operator"); return }
            let cond = deps.conditionsProbe.read()
            if let pct = cond.batteryPct, cond.powerSource == .battery, pct < m.plan.settings.batteryFloorPct {
                event(runID, "battery_floor", "\(Int(pct)) %")
                try pause(runID, reason: "battery below \(Int(m.plan.settings.batteryFloorPct)) %")
                push(phase: "idle", message: "Paused: battery low. Charge, unplug, then resume.")
                return
            }
            m = try store.manifest(runID)
            let done = store.units(runID).filter { $0.unit_index == unit.index }
            let attempt = done.filter { SampleStatus(rawValue: $0.status)?.isKill == true }.count + 1
            let seq = done.count + 1
            let sampleID = String(format: "s%04d", unit.index) + (seq > 1 ? "-\(seq)" : "")
            var p = RunProgress(runID: runID, unitIndex: unit.index, unitCount: units.count, label: unit.label, phase: "starting")
            p.modelID = unit.model.map { m.models[$0].entry.id }
            p.lastDecodeTPS = progress?.lastDecodeTPS
            p.lastPrefillTPS = progress?.lastPrefillTPS
            p.lastTTFT = progress?.lastTTFT
            p.lastStatus = progress?.lastStatus
            p.conditions = cond
            push(p)

            var outcome: UnitOutcome
            if let skip = skipStatus(unit, runID: runID) {
                outcome = UnitOutcome(status: skip.rawValue)
                if unit.makesSample {
                    var s = baseSample(unit, m, sampleID: sampleID, attempt: attempt, status: skip)
                    s.error = skip == .not_run_above_oom ? "Not run: this model was killed at this level or below, or at load." : "Not run: \(skip.rawValue)."
                    outcome.sample = s
                }
            } else {
                current.set(sampleID)
                do {
                    outcome = try await execute(unit, m, sampleID: sampleID, attempt: attempt)
                } catch is SimulatedProcessDeath {
                    throw SimulatedProcessDeath()   // leave inflight.json, as a real kill would
                } catch is CancellationError {
                    current.set(nil)
                    if let f = store.inflight(runID), f.backgrounded {
                        if unit.makesSample {
                            var s = baseSample(unit, m, sampleID: sampleID, attempt: attempt, status: .voided_backgrounded)
                            s.error = "The app left the foreground during this sample; it is run again on resume."
                            try store.append(s, to: runID)
                        }
                        try store.append(UnitDone(unit_index: unit.index, attempt: attempt, final: false, status: SampleStatus.voided_backgrounded.rawValue,
                                                  sample_id: sampleID, model: unit.model, level: unit.level, during_load: false), to: runID)
                        event(runID, "voided_backgrounded", sampleID)
                        store.clearInflight(runID)
                        try pause(runID, reason: "the app left the foreground")
                    } else {
                        store.clearInflight(runID)
                        try pause(runID, reason: "stopped by operator")
                    }
                    return
                }
                current.set(nil)
            }
            if let s = outcome.sample { try store.append(s, to: runID) }
            if let line = outcome.phoneLine { try store.append(phoneLine: line, to: runID) }
            try store.append(UnitDone(unit_index: unit.index, attempt: attempt, final: outcome.final, status: outcome.status,
                                      sample_id: outcome.sample == nil && outcome.phoneLine == nil ? nil : sampleID,
                                      model: unit.model, level: unit.level, during_load: outcome.duringLoad), to: runID)
            store.clearInflight(runID)

            var q = progress ?? p
            q.unitIndex = unit.index + 1
            q.lastStatus = outcome.status
            if let s = outcome.sample, s.status == .ok {
                q.lastDecodeTPS = s.decodeTPS ?? q.lastDecodeTPS
                q.lastPrefillTPS = s.prefillTPS ?? q.lastPrefillTPS
                q.lastTTFT = s.ttftS ?? q.lastTTFT
            }
            q.liveDecodeTPS = nil
            push(q)
            if outcome.sample?.status == .ok, needsGap(unit) {
                push(phase: "gap", message: nil)
                try? await deps.clock.sleep(nanos: UInt64(m.plan.settings.gapSeconds * 1e9))
            }
        }
        if loadedModel != nil { await deps.generator.unload(); loadedModel = nil }
        let end = deps.conditionsProbe.read()
        try store.updateManifest(runID) {
            $0.state = .complete
            $0.endedAt = self.deps.now()
            $0.conditionsEnd = end
        }
        event(runID, "complete", "")
        push(phase: "done", message: "Run complete")
    }

    func pause(_ runID: String, reason: String) throws {
        let end = deps.conditionsProbe.read()
        try deps.store.updateManifest(runID) {
            $0.state = .paused
            $0.stopReason = reason
            $0.conditionsEnd = end
            $0.endedAt = self.deps.now()
        }
        event(runID, "paused", reason)
        push(phase: "idle", message: "Paused: \(reason)")
    }

    func event(_ runID: String, _ kind: String, _ detail: String) {
        try? deps.store.append(RecordEvent(t_ns: deps.clock.nowNanos(), kind: kind, detail: detail), to: runID)
    }

    func needsGap(_ unit: WorkUnit) -> Bool {
        switch unit.kind {
        case .batteryTask, .batteryEnd, .idleSegment, .phoneItem, .balloonRun: false
        default: true
        }
    }

    // MARK: skipping after a kill (spec 04 §2.8 item 5)

    /// After a final kill at level L, the rest of that cell and every higher level of that model
    /// become `not_run_above_oom`; after a final kill at load, every later unit of that model.
    func skipStatus(_ unit: WorkUnit, runID: String) -> SampleStatus? {
        guard let model = unit.model else { return nil }
        let finals = deps.store.units(runID).filter { $0.final && $0.model == model }
        if finals.contains(where: { $0.status == SampleStatus.unsupported_by_runtime.rawValue }) { return .unsupported_by_runtime }
        let kills = finals.filter { SampleStatus(rawValue: $0.status)?.isKill == true }
        if kills.contains(where: \.during_load) { return .not_run_above_oom }
        if let l = unit.level, let lowest = kills.compactMap(\.level).min(), l >= lowest { return .not_run_above_oom }
        return nil
    }

    // MARK: units

    func baseSample(_ unit: WorkUnit, _ m: RunManifest, sampleID: String, attempt: Int, status: SampleStatus) -> PerfSample {
        var s = PerfSample(sample_id: sampleID, model_id: unit.model.map { m.models[$0].entry.id } ?? "none", mode: "plain",
                           test: unit.test, phase: unit.isWarmup ? "warmup" : "measured", order_index: unit.index,
                           status: status, attempt: attempt, device_model_id: m.device.model, os: m.device.os)
        if case .level(_, let round, let rep, _) = unit.kind { s.round = round; s.rep = rep }
        if case .replayStep(let replay, let step, _) = unit.kind { s.round = replay; s.rep = replay; s.step = step }
        if case .sustained(_, let i, _) = unit.kind { s.rep = i }
        if case .batteryTask(_, let i, _, _) = unit.kind { s.rep = i }
        if case .loadWarm(let rep) = unit.kind { s.rep = rep }
        s.level_tokens = unit.level
        return s
    }

    func inflight(_ unit: WorkUnit, _ m: RunManifest, sampleID: String, attempt: Int, phase: String, sample: PerfSample?,
                  item: (String, Int, String)? = nil) throws {
        let r = deps.memoryProbe.read()
        try deps.store.writeInflight(Inflight(
            run_id: m.runID, sample_id: sampleID, unit_index: unit.index, attempt: attempt, model: unit.model,
            model_id: unit.model.map { m.models[$0].entry.id } ?? "none", mode: "plain", test: unit.test, level: unit.level,
            phase: phase, sample_phase: unit.isWarmup ? "warmup" : "measured", round: sample?.round, rep: sample?.rep,
            variant: sample?.variant, step: sample?.step, t_start_ns: deps.clock.nowNanos(), footprint_start: r.footprintBytes,
            available_start: r.availableBytes, item_id: item?.0, trial: item?.1, item_mode: item?.2, backgrounded: false))
    }

    func execute(_ unit: WorkUnit, _ m: RunManifest, sampleID: String, attempt: Int) async throws -> UnitOutcome {
        var s = baseSample(unit, m, sampleID: sampleID, attempt: attempt, status: .ok)
        do {
            switch unit.kind {
            case .loadWarm, .loadCold:
                try await cooldown(&s, m, runID: m.runID)
                try await measureLoad(&s, unit, m, attempt: attempt)
            case .level(let level, _, let rep, _):
                try await cooldown(&s, m, runID: m.runID)
                try await performLevel(&s, unit, m, level: level, variant: rep, attempt: attempt)
            case .replayStep(_, let step, _):
                try await cooldown(&s, m, runID: m.runID)
                try await performReplay(&s, unit, m, step: step, attempt: attempt)
            case .sustained(let level, let index, let minutes):
                let started = try stepStart(m.runID, step: unit.stepIndex)
                if deps.now().timeIntervalSince(started) >= Double(minutes * 60) { return UnitOutcome(status: "skipped_time_up") }
                try await performLevel(&s, unit, m, level: level, variant: index, attempt: attempt)
            case .batteryTask(let level, let index, let atSecond, let withModel):
                let started = try stepStart(m.runID, step: unit.stepIndex)
                try await waitUntil(started.addingTimeInterval(Double(atSecond)))
                guard withModel else {
                    if loadedModel != nil { await deps.generator.unload(); loadedModel = nil }
                    return UnitOutcome(status: "ok")
                }
                try await performLevel(&s, unit, m, level: level, variant: index, attempt: attempt)
            case .batteryEnd(let minutes, _):
                let started = try stepStart(m.runID, step: unit.stepIndex)
                try await waitUntil(started.addingTimeInterval(Double(minutes * 60)))
                return UnitOutcome(status: "ok")
            case .idleSegment(_, let loaded, let minutes):
                try await idleSegment(&s, unit, m, loaded: loaded, minutes: minutes, attempt: attempt)
            case .wake:
                try await performLevel(&s, unit, m, level: 512, variant: 0, attempt: attempt)
            case .phoneItem(let item, let trial, let mode):
                return try await performPhoneItem(unit, m, item: item, trial: trial, mode: mode, sampleID: sampleID, attempt: attempt)
            case .balloonRun(let index):
                return try await performBalloon(unit, m, index: index, sampleID: sampleID, attempt: attempt)
            }
        } catch let e as CancellationError {
            throw e
        } catch let e as SimulatedProcessDeath {
            throw e
        } catch let e as GeneratorError {
            switch e {
            case .engineRefused: s.status = .engine_refused
            case .unsupported: s.status = .unsupported_by_runtime
            default: s.status = .engine_refused   // an engine fault: 04's excluded status; detail in `error`
            }
            s.error = e.localizedDescription
        } catch {
            s.status = .engine_refused
            s.error = error.localizedDescription
        }
        return UnitOutcome(sample: s, status: s.status.rawValue)
    }

    func stepStart(_ runID: String, step: Int) throws -> Date {
        let m = try deps.store.manifest(runID)
        if let d = m.stepStartedAt[String(step)] { return d }
        let now = deps.now()
        try deps.store.updateManifest(runID) { $0.stepStartedAt[String(step)] = now }
        return now
    }

    func waitUntil(_ date: Date) async throws {
        push(phase: "waiting", message: nil)
        while true {
            try Task.checkCancellation()
            let left = date.timeIntervalSince(deps.now())
            if left <= 0 { return }
            try await deps.clock.sleep(nanos: UInt64(min(left, 1.0) * 1e9))
            pushLive(nil, phase: "waiting")
        }
    }

    /// Before every sample: wait for thermal nominal, up to 300 s (spec 04 §2.6).
    func cooldown(_ s: inout PerfSample, _ m: RunManifest, runID: String) async throws {
        let settings = m.plan.settings
        let t0 = deps.clock.nowNanos()
        var current = deps.conditionsProbe.read().thermal
        if current > .nominal { push(phase: "cooldown", message: "Waiting for the phone to cool (\(current.rawValue))") }
        while current > .nominal, deps.clock.seconds(since: t0) < settings.cooldownMaxWaitSeconds {
            try await deps.clock.sleep(nanos: UInt64(settings.cooldownPollSeconds * 1e9))
            current = deps.conditionsProbe.read().thermal
        }
        s.cooldown_wait_s = deps.clock.seconds(since: t0)
        if current > .nominal {
            s.thermal_not_nominal = true
            event(runID, "cooldown_timeout", "\(s.sample_id): \(current.rawValue) after \(Int(settings.cooldownMaxWaitSeconds)) s")
        }
    }

    // MARK: loading

    func isAnythingLoaded() async -> Bool {
        if loadedModel != nil { return true }
        return await deps.generator.isLoaded
    }

    /// Makes `model` the loaded model, unloading another first. A kill in here is a kill at load.
    func ensureLoaded(_ unit: WorkUnit, _ m: RunManifest, sampleID: String, attempt: Int, sample: PerfSample) async throws {
        guard let model = unit.model else { return }
        if loadedModel == model, await deps.generator.isLoaded { return }
        if await isAnythingLoaded() { await deps.generator.unload(); loadedModel = nil }
        try inflight(unit, m, sampleID: sampleID, attempt: attempt, phase: "load", sample: sample)
        push(phase: "loading", message: "Loading \(m.models[model].entry.id)")
        // The footprint log runs during the load too, so a kill at load leaves its evidence.
        let sampler = makeSampler(m, sampleID: sampleID)
        sampler.start()
        let t0 = deps.clock.nowNanos()
        do { _ = try await deps.generator.load(variant: m.models[model].variant) } catch { _ = sampler.stop(); throw error }
        _ = sampler.stop()
        _ = ProcessLoads.shared.next()
        loadedModel = model
        event(m.runID, "model_loaded", "\(m.models[model].entry.id) in \(String(format: "%.2f", deps.clock.seconds(since: t0))) s")
    }

    func measureLoad(_ s: inout PerfSample, _ unit: WorkUnit, _ m: RunManifest, attempt: Int) async throws {
        guard let model = unit.model else { return }
        let gen = deps.generator
        if await gen.isLoaded { await gen.unload(); loadedModel = nil }
        let cond = deps.conditionsProbe.read()
        try inflight(unit, m, sampleID: s.sample_id, attempt: attempt, phase: "load", sample: s)
        let sampler = makeSampler(m, sampleID: s.sample_id)
        sampler.start()
        stampStart(&s, cond)
        push(phase: "loading", message: nil)
        let blocks0 = DiskReadProbe.blockInputs()
        let t0 = deps.clock.nowNanos()
        do { _ = try await gen.load(variant: m.models[model].variant) } catch { _ = sampler.stop(); throw error }
        let loadsThisProcess = ProcessLoads.shared.next()
        let ready = deps.clock.nowNanos()
        loadedModel = model
        let blocks1 = DiskReadProbe.blockInputs()
        try inflight(unit, m, sampleID: s.sample_id, attempt: attempt, phase: "generate", sample: s)
        // First possible token: the pack's 8-token prompt, one output token.
        var req = GenerationRequest(messages: [BenchMessage(role: "user", content: "Say OK.")], maxTokens: 1,
                                    sampling: .greedy, thinking: m.plan.settings.thinking, ignoreEOS: true)
        if let p = try pack(m, model: model), let ids = p.first_token?.ids { req.promptIDs = ids }
        let timer = TokenTimer(clock: deps.clock)
        s.t_submit_ns = deps.clock.nowNanos()
        let result: GenerationResult
        do {
            result = try await gen.generate(req, hooks: GenerationHooks(onPrefillDone: { timer.markPrefillDone() }, onToken: { timer.token($0) }))
        } catch { _ = sampler.stop(); throw error }
        s.t_end_ns = deps.clock.nowNanos()
        let mem = sampler.stop()
        fill(&s, timer: timer, result: result, mem: mem, cond: deps.conditionsProbe.read())
        let uptime = cond.uptimeS ?? ProcessInfo.processInfo.systemUptime
        let cold = unit.kind == .loadCold
        s.load = LoadInfo(
            t_load_start_ns: t0, t_ready_ns: ready, t_first_token_possible_ns: timer.snapshot.times.first,
            cold_verified: cold && m.operatorInput.rebooted_before_run && loadsThisProcess == 1 && uptime >= 300 && uptime <= 3_600,
            disk_read_bytes: nil, disk_block_inputs: blocks0 >= 0 && blocks1 >= 0 ? blocks1 - blocks0 : nil)
    }

    // MARK: one generation

    func stampStart(_ s: inout PerfSample, _ c: Conditions) {
        s.thermal_start = c.thermal.rawValue
        s.battery_level_start = c.batteryLevel
        s.power_source = c.powerSource.rawValue
        s.low_power_mode = c.lowPowerMode
        s.app_state = c.appState
    }

    func fill(_ s: inout PerfSample, timer: TokenTimer, result: GenerationResult?, mem: MemorySampler.Result, cond: Conditions) {
        let snap = timer.snapshot
        s.t_prefill_done_ns = snap.prefillDone
        s.t_first_token_ns = snap.times.first
        s.t_last_token_ns = snap.times.last
        if let f = snap.times.first { s.token_times_ns = snap.times.dropFirst().map { $0 - f } }
        s.output_tokens = snap.times.count
        s.reasoning_tokens = result?.reasoningTokens ?? snap.reasoning
        s.drafted_tokens = result?.draftedTokens
        s.accepted_tokens = result?.acceptedDraftTokens
        s.prompt_tokens = result?.promptTokens ?? s.prompt_tokens
        s.footprint_start_bytes = mem.startBytes
        s.footprint_peak_bytes = max(mem.peakBytes, mem.peakLedgerBytes ?? 0)
        s.footprint_peak_ledger_bytes = mem.peakLedgerBytes
        s.footprint_steady_bytes = mem.steadyBytes
        s.footprint_end_bytes = mem.endBytes
        s.available_min_bytes = mem.availableMinBytes
        s.mlx_active_bytes = result?.mlxActiveBytes
        s.mlx_cache_bytes = result?.mlxCacheBytes
        s.mlx_peak_bytes = result?.mlxPeakBytes
        s.thermal_end = cond.thermal.rawValue
        s.battery_level_end = cond.batteryLevel
        s.memory_warning = current.wasWarned
        s.engine_prompt_s = result?.engine?.promptSeconds
        s.finish_reason = result?.finishReason
        if let a = cond.appState, a != "active" { s.app_state = a }
    }

    func performLevel(_ s: inout PerfSample, _ unit: WorkUnit, _ m: RunManifest, level: Int, variant: Int, attempt: Int) async throws {
        guard let model = unit.model else { return }
        let settings = m.plan.settings
        s.level_tokens = level
        if let window = m.models[model].variant.facts.nativeWindow, level + settings.outputTokens > window {
            s.status = .exceeds_native_window
            return
        }
        try inflight(unit, m, sampleID: s.sample_id, attempt: attempt, phase: "generate", sample: s)
        try await ensureLoaded(unit, m, sampleID: s.sample_id, attempt: attempt, sample: s)
        var request = GenerationRequest(messages: [], maxTokens: settings.outputTokens, sampling: settings.sampling,
                                        thinking: settings.thinking, ignoreEOS: true, speculation: settings.speculation)
        if let p = try pack(m, model: model) {
            let ids = try p.ids(level: level, variant: variant)
            request.promptIDs = ids
            s.variant = variant % max(1, p.variantCount(level: level))
            s.prompt_tokens = ids.count
        } else {
            request.messages = try await standInMessages(m, model: model, level: level, variant: variant)
            s.variant = variant
            s.prompt_tokens = try await deps.generator.countPromptTokens(request.messages, tools: [], thinking: settings.thinking)
        }
        try inflight(unit, m, sampleID: s.sample_id, attempt: attempt, phase: "generate", sample: s)
        let want = s.prompt_tokens
        try await timedGenerate(&s, request, m)
        if s.status == .ok, let w = want, let got = s.prompt_tokens, got + 2 < w { s.status = .truncated }
    }

    func performReplay(_ s: inout PerfSample, _ unit: WorkUnit, _ m: RunManifest, step: Int, attempt: Int) async throws {
        guard let model = unit.model else { return }
        guard let p = try pack(m, model: model), let st = p.agent_replay?.steps.first(where: { $0.step == step }) else {
            throw GeneratorError.noPromptPack("agent replay needs the pack's 6-step trajectory")
        }
        try inflight(unit, m, sampleID: s.sample_id, attempt: attempt, phase: "generate", sample: s)
        try await ensureLoaded(unit, m, sampleID: s.sample_id, attempt: attempt, sample: s)
        let settings = m.plan.settings
        var request = GenerationRequest(messages: [], maxTokens: settings.outputTokens, sampling: settings.sampling,
                                        thinking: settings.thinking, ignoreEOS: true)
        request.promptIDs = st.ids
        s.prompt_tokens = st.ids.count
        try inflight(unit, m, sampleID: s.sample_id, attempt: attempt, phase: "generate", sample: s)
        try await timedGenerate(&s, request, m)
    }

    /// Stand-in prompts built on the phone with its own template (no prompt pack).
    func standInMessages(_ m: RunManifest, model: Int, level: Int, variant: Int) async throws -> [BenchMessage] {
        var rng = SplitMix64(seed: m.seed &+ UInt64(level) &* 31 &+ UInt64(variant))
        let nonce = PromptBuilder.nonce(using: &rng)
        let key = "\(model):\(level)"
        if let words = try deps.store.manifest(m.runID).levelWords[key] { return deps.prompts.messages(words: words, nonce: nonce) }
        push(phase: "preparing", message: "Sizing the \(WorkUnit.fmt(level))-token prompt")
        let gen = deps.generator
        let thinking = m.plan.settings.thinking
        let built = try await deps.prompts.build(target: level, nonce: nonce, thinking: thinking) {
            try await gen.countPromptTokens($0, tools: [], thinking: thinking)
        }
        try deps.store.updateManifest(m.runID) { $0.levelWords[key] = built.words }
        return built.messages
    }

    /// One generation with the 20 Hz sampler on and every token stamped, stopped at the per-sample limit.
    func timedGenerate(_ s: inout PerfSample, _ request: GenerationRequest, _ m: RunManifest) async throws {
        let cond = deps.conditionsProbe.read()
        stampStart(&s, cond)
        let sampler = makeSampler(m, sampleID: s.sample_id)
        sampler.start()
        push(phase: "prefill", message: nil)
        let clock = deps.clock
        let deadline = clock.nowNanos() + UInt64(m.plan.settings.cellTimeoutSeconds * 1e9)
        let timedOut = Flag()
        let gen = deps.generator
        let timer = TokenTimer(clock: clock) { [weak sampler] in sampler?.setDecoding() }
        let handle = TaskHandle()
        let hooks = GenerationHooks(onPrefillDone: { timer.markPrefillDone() }, onToken: { kind in
            timer.token(kind)
            if clock.nowNanos() > deadline { timedOut.set(); handle.cancel() }
        })
        s.t_submit_ns = clock.nowNanos()
        let task = Task { () -> GenerationResult in try await gen.generate(request, hooks: hooks) }
        handle.set(task)
        let progressTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 250_000_000)
                await self?.pushLive(timer.liveDecodeTPS, phase: timer.count == 0 ? "prefill" : "decode")
            }
        }
        defer { progressTask.cancel() }
        var result: GenerationResult?
        do {
            result = try await withTaskCancellationHandler { try await task.value } onCancel: { task.cancel() }
        } catch is CancellationError where timedOut.value && !Task.isCancelled {
            result = nil
        } catch {
            _ = sampler.stop()
            throw error
        }
        s.t_end_ns = clock.nowNanos()
        let mem = sampler.stop()
        fill(&s, timer: timer, result: result, mem: mem, cond: deps.conditionsProbe.read())
        if timedOut.value { s.status = .timeout; s.finish_reason = "timeout" }
    }

    // MARK: idle resident

    func idleSegment(_ s: inout PerfSample, _ unit: WorkUnit, _ m: RunManifest, loaded: Bool, minutes: Int, attempt: Int) async throws {
        s.resident = loaded
        try inflight(unit, m, sampleID: s.sample_id, attempt: attempt, phase: "idle", sample: s)
        if loaded {
            try await ensureLoaded(unit, m, sampleID: s.sample_id, attempt: attempt, sample: s)
            try inflight(unit, m, sampleID: s.sample_id, attempt: attempt, phase: "idle", sample: s)
        } else if await isAnythingLoaded() {
            await deps.generator.unload()
            loadedModel = nil
        }
        let cond = deps.conditionsProbe.read()
        stampStart(&s, cond)
        // 1 Hz is enough while idle (and keeps the log small over two hours).
        let sampler = MemorySampler(probe: deps.memoryProbe, clock: deps.clock, intervalMilliseconds: 1_000,
                                    log: MemLogWriter(url: deps.store.memLogURL(m.runID)), sampleID: s.sample_id)
        sampler.start()
        s.t_submit_ns = deps.clock.nowNanos()
        let until = deps.now().addingTimeInterval(Double(minutes * 60))
        do { try await waitUntil(until) } catch { _ = sampler.stop(); throw error }
        s.t_end_ns = deps.clock.nowNanos()
        let mem = sampler.stop()
        let end = deps.conditionsProbe.read()
        s.footprint_start_bytes = mem.startBytes
        s.footprint_peak_bytes = mem.peakBytes
        s.footprint_end_bytes = mem.endBytes
        s.available_min_bytes = mem.availableMinBytes
        s.thermal_end = end.thermal.rawValue
        s.battery_level_end = end.batteryLevel
        s.output_tokens = 0
        s.prompt_tokens = 0
    }

    // MARK: phone items (spec 02 §6)

    func performPhoneItem(_ unit: WorkUnit, _ m: RunManifest, item index: Int, trial: Int, mode: String,
                          sampleID: String, attempt: Int) async throws -> UnitOutcome {
        guard let set = deps.phoneItems, set.items.indices.contains(index), set.sha256 == m.phoneItemsSHA256 else {
            return UnitOutcome(status: "error")
        }
        let item = set.items[index]
        try inflight(unit, m, sampleID: sampleID, attempt: attempt, phase: "generate", sample: nil, item: (item.id, trial, mode))
        let base = baseSample(unit, m, sampleID: sampleID, attempt: attempt, status: .ok)
        try await ensureLoaded(unit, m, sampleID: sampleID, attempt: attempt, sample: base)
        try inflight(unit, m, sampleID: sampleID, attempt: attempt, phase: "generate", sample: nil, item: (item.id, trial, mode))
        let modelID = unit.model.map { m.models[$0].entry.id } ?? "none"
        let runtime = PhoneRuntime.from(m.runtime)
        let weights = unit.model.flatMap { m.models[$0].variant.weightsDigest }
        func fail(_ status: String, _ error: String) -> UnitOutcome {
            let r = PhoneResult(id: item.id, trial: trial, mode: mode, device: m.device.shortDescription, runtime: runtime,
                                weightsSHA256: weights, rawText: "", finishReason: status, promptTokens: 0, completionTokens: 0,
                                ttftS: nil, totalS: 0, localPass: false, localLabels: [error], modelID: modelID, status: status, error: error)
            return UnitOutcome(phoneLine: try? r.jsonlLine(), status: status)
        }
        var sampling = item.sampling.sampling
        sampling.seed = sampling.seed &+ UInt64(trial)
        var request = GenerationRequest(messages: item.benchMessages, tools: item.tools, maxTokens: item.sampling.maxTokens,
                                        sampling: sampling, thinking: item.thinking ? .on : .off, ignoreEOS: false)
        if mode == "rendered" {
            guard let rendered = item.renderedPrompt else { return fail("error", "the item has no rendered_prompt") }
            request.renderedPrompt = rendered
        }
        let timer = TokenTimer(clock: deps.clock)
        let t0 = deps.clock.nowNanos()
        let result: GenerationResult
        do {
            result = try await deps.generator.generate(request, hooks: GenerationHooks(onPrefillDone: { timer.markPrefillDone() },
                                                                                        onToken: { timer.token($0) }))
        } catch let e as GeneratorError {
            return fail("error", e.localizedDescription)
        }
        let end = deps.clock.nowNanos()
        let calls = result.toolCalls.map(PhoneParsedCall.init)
        let grade = PhoneGrader.grade(item, answer: result.text, calls: calls)
        let r = PhoneResult(
            id: item.id, trial: trial, mode: mode, device: m.device.shortDescription, runtime: runtime, weightsSHA256: weights,
            rawText: result.rawText, reasoningText: result.reasoning ?? "", parsedCalls: calls, rejectedCalls: result.rejectedCalls,
            finishReason: result.finishReason, promptTokens: result.promptTokens, completionTokens: result.completionTokens,
            ttftS: timer.snapshot.times.first.map { Double($0 - t0) / 1e9 }, totalS: Double(end - t0) / 1e9,
            localPass: grade.pass, localLabels: grade.labels, modelID: modelID)
        return UnitOutcome(phoneLine: try r.jsonlLine(), status: "ok")
    }

    // MARK: balloon (spec 04 §2.9)

    func performBalloon(_ unit: WorkUnit, _ m: RunManifest, index: Int, sampleID: String, attempt: Int) async throws -> UnitOutcome {
        guard let balloon = deps.balloon else { return UnitOutcome(status: "error") }
        if await isAnythingLoaded() { await deps.generator.unload(); loadedModel = nil }
        try inflight(unit, m, sampleID: sampleID, attempt: attempt, phase: "balloon", sample: nil)
        push(phase: "balloon", message: "Allocating until iOS ends the app. Open tinyagent-bench again afterwards.")
        let step = 64 << 20
        // Never more than the phone's RAM (the Simulator has no jetsam limit).
        let cap = ProcessInfo.processInfo.physicalMemory
        var allocated: UInt64 = 0
        var n = 0
        defer { balloon.releaseAll() }
        while allocated + UInt64(step) < cap {
            try Task.checkCancellation()
            guard try balloon.allocate(bytes: step) else { break }
            allocated += UInt64(step)
            n += 1
            let r = deps.memoryProbe.read()
            try deps.store.append(BalloonLine(run: index, step: n, allocated_bytes: allocated, footprint: r.footprintBytes,
                                              available: r.availableBytes), to: m.runID)
            pushLive(nil, phase: "balloon")
            await Task.yield()
        }
        return UnitOutcome(status: "ok")
    }

    // MARK: sampling and progress

    func makeSampler(_ m: RunManifest, sampleID: String) -> MemorySampler {
        MemorySampler(probe: deps.memoryProbe, clock: deps.clock, intervalMilliseconds: m.plan.settings.memorySampleMilliseconds,
                      log: MemLogWriter(url: deps.store.memLogURL(m.runID)), sampleID: sampleID) { [weak self] t in
            guard let self else { return }
            Task { await self.onTick(t, runID: m.runID) }
        }
    }

    func onTick(_ t: MemorySampler.Tick, runID: String) {
        let thermal = deps.conditionsProbe.read().thermal
        if let last = lastThermal, last != thermal { event(runID, "thermal_change", "\(last.rawValue)->\(thermal.rawValue)") }
        lastThermal = thermal
        if var p = progress, t.tNanos &- lastProgressPush >= 250_000_000 {
            lastProgressPush = t.tNanos
            p.footprintBytes = t.footprint
            p.availableBytes = t.available
            push(p)
        }
    }

    func pushLive(_ tps: Double?, phase: String) {
        guard var p = progress else { return }
        p.liveDecodeTPS = tps
        p.phase = phase
        let r = deps.memoryProbe.read()
        p.footprintBytes = r.footprintBytes
        p.availableBytes = r.availableBytes
        p.conditions = deps.conditionsProbe.read()
        push(p)
    }

    func push(_ p: RunProgress) {
        progress = p
        onProgress(p)
    }

    func push(phase: String, message: String?) {
        guard var p = progress else { return }
        p.phase = phase
        p.message = message
        push(p)
    }
}

final class TaskHandle: @unchecked Sendable {
    private let lock = NSLock()
    private var task: Task<GenerationResult, Error>?
    private var cancelled = false
    func set(_ t: Task<GenerationResult, Error>) {
        let c = lock.withLock { task = t; return cancelled }
        if c { t.cancel() }
    }
    func cancel() {
        let t = lock.withLock { cancelled = true; return task }
        t?.cancel()
    }
}

final class Flag: @unchecked Sendable {
    private let lock = NSLock()
    private var v = false
    func set() { lock.withLock { v = true } }
    var value: Bool { lock.withLock { v } }
}
