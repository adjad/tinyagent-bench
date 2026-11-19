import Foundation

public enum RunState: String, Codable, Sendable {
    case running, paused, interrupted, complete
}

/// One of the run's models: the folder, what it is to the benchmark, and its prompt pack.
public struct RunModel: Codable, Sendable, Equatable {
    public var variant: ModelVariant
    public var entry: ModelEntry
    /// Path of the imported prompt pack, nil when prompts are built on the phone (stand-in).
    public var packPath: String?
    public var pack: RecordPromptPack
    public init(variant: ModelVariant, entry: ModelEntry, packPath: String?, pack: RecordPromptPack) {
        self.variant = variant
        self.entry = entry
        self.packPath = packPath
        self.pack = pack
    }
}

/// Everything about a run except its results. Rewritten atomically on change.
public struct RunManifest: Codable, Sendable, Equatable {
    public var runID: String
    public var createdAt: Date
    public var endedAt: Date?
    public var plan: TestPlan
    public var models: [RunModel]
    /// Slot → index into `models`, keyed by `ModelSlot.rawValue`.
    public var slots: [String: Int]
    public var runtime: RuntimeInfo
    public var device: DeviceIdentity
    public var operatorInput: OperatorAttestation
    public var conditionsStart: Conditions
    public var conditionsEnd: Conditions?
    public var seed: UInt64
    public var state: RunState
    public var stopReason: String?
    /// Words per "model:level" found by the on-device prompt builder (stand-in prompts only).
    public var levelWords: [String: Int]
    /// Wall-clock start of each time-boxed step (sustained, battery), by step index.
    public var stepStartedAt: [String: Date]
    public var unitCount: Int
    public var phoneItemsSHA256: String?
    public var phoneItemsCount: Int
    public var phoneItemsSource: String?
    public var appCommit: String?

    public var slotMap: [ModelSlot: Int] {
        Dictionary(uniqueKeysWithValues: slots.compactMap { k, v in ModelSlot(rawValue: k).map { ($0, v) } })
    }
}

/// One line per finished unit attempt in `units.jsonl`.
public struct UnitDone: Codable, Sendable, Equatable {
    public var unit_index: Int
    public var attempt: Int
    /// A final result ends the unit; a non-final one (first kill, voided) means run it again.
    public var final: Bool
    public var status: String
    public var sample_id: String?
    public var model: Int?
    public var level: Int?
    public var during_load: Bool
}

/// Written with fsync before each sample, deleted after it (spec 04 §2.8). Found at launch,
/// it means the app died inside that sample.
public struct Inflight: Codable, Sendable, Equatable {
    public var run_id: String
    public var sample_id: String
    public var unit_index: Int
    public var attempt: Int
    public var model: Int?
    public var model_id: String
    public var mode: String
    public var test: String
    public var level: Int?
    public var phase: String               // load | generate | idle | balloon
    public var sample_phase: String        // warmup | measured
    public var round: Int?
    public var rep: Int?
    public var variant: Int?
    public var step: Int?
    public var t_start_ns: UInt64
    public var footprint_start: UInt64
    public var available_start: UInt64?
    public var item_id: String?
    public var trial: Int?
    public var item_mode: String?
    /// Set by the app when it leaves the foreground, before iOS can suspend it.
    public var backgrounded: Bool
}

/// One footprint tick in `mem-<run_id>.jsonl` (spec 04 §2.5).
public struct MemLine: Codable, Sendable, Equatable {
    public var t_ns: UInt64
    public var footprint: UInt64
    public var available: UInt64?
    public var sample_id: String
}

/// One balloon step in `balloon.jsonl` (spec 04 §2.9).
public struct BalloonLine: Codable, Sendable, Equatable {
    public var run: Int
    public var step: Int
    public var allocated_bytes: UInt64
    public var footprint: UInt64
    public var available: UInt64?
}

/// One folder per run:
/// - `manifest.json` (atomic rewrite)
/// - `units.jsonl`, `samples.jsonl`, `events.jsonl`, `phone-<run_id>.jsonl`, `balloon.jsonl`
///   (append-only, fsync'd per line; a torn last line is ignored)
/// - `mem-<run_id>.jsonl` (the 20 Hz footprint log, flushed every 250 ms)
/// - `inflight.json` while a sample runs.
public final class RunStore: @unchecked Sendable {
    public let root: URL
    private let lock = NSLock()

    public init(root: URL) {
        self.root = root
        try? FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    }

    public func directory(_ runID: String) -> URL { root.appendingPathComponent(runID, isDirectory: true) }
    public func file(_ runID: String, _ name: String) -> URL { directory(runID).appendingPathComponent(name) }
    public func memLogURL(_ runID: String) -> URL { file(runID, "mem-\(runID).jsonl") }
    public func phoneResultsURL(_ runID: String) -> URL { file(runID, "phone-\(runID).jsonl") }

    // MARK: manifest

    public func create(_ manifest: RunManifest) throws {
        try FileManager.default.createDirectory(at: directory(manifest.runID), withIntermediateDirectories: true)
        try save(manifest)
    }

    public func save(_ manifest: RunManifest) throws {
        try lock.withLock {
            try JSONEncoder.pretty.encode(manifest).write(to: file(manifest.runID, "manifest.json"), options: .atomic)
        }
    }

    public func manifest(_ runID: String) throws -> RunManifest {
        try JSONDecoder().decode(RunManifest.self, from: Data(contentsOf: file(runID, "manifest.json")))
    }

    public func updateManifest(_ runID: String, _ change: (inout RunManifest) -> Void) throws {
        var m = try manifest(runID)
        change(&m)
        try save(m)
    }

    public func allRuns() -> [RunManifest] {
        let dirs = (try? FileManager.default.contentsOfDirectory(at: root, includingPropertiesForKeys: nil)) ?? []
        return dirs.compactMap { try? manifest($0.lastPathComponent) }.sorted { $0.createdAt > $1.createdAt }
    }

    public func delete(_ runID: String) throws {
        try FileManager.default.removeItem(at: directory(runID))
    }

    // MARK: append-only files

    public func append(_ done: UnitDone, to runID: String) throws { try appendLine(JSONEncoder.sorted.encode(done), to: file(runID, "units.jsonl")) }
    public func append(_ sample: PerfSample, to runID: String) throws { try appendLine(JSONEncoder.sorted.encode(sample), to: file(runID, "samples.jsonl")) }
    public func append(_ event: RecordEvent, to runID: String) throws { try appendLine(JSONEncoder.sorted.encode(event), to: file(runID, "events.jsonl")) }
    public func append(phoneLine: Data, to runID: String) throws { try appendLine(phoneLine, to: phoneResultsURL(runID)) }
    public func append(_ line: BalloonLine, to runID: String) throws { try appendLine(JSONEncoder.sorted.encode(line), to: file(runID, "balloon.jsonl")) }

    private func appendLine(_ data: Data, to url: URL) throws {
        try lock.withLock {
            if !FileManager.default.fileExists(atPath: url.path) {
                FileManager.default.createFile(atPath: url.path, contents: nil)
            }
            let h = try FileHandle(forWritingTo: url)
            defer { try? h.close() }
            try h.seekToEnd()
            try h.write(contentsOf: data + Data([0x0A]))
            try h.synchronize()
        }
    }

    func readLines(_ url: URL) -> [Data] {
        guard let data = try? Data(contentsOf: url) else { return [] }
        return data.split(separator: 0x0A).map { Data($0) }
    }

    private func decodeLines<T: Decodable>(_ url: URL, as: T.Type) -> [T] {
        readLines(url).compactMap { try? JSONDecoder().decode(T.self, from: $0) }
    }

    public func units(_ runID: String) -> [UnitDone] { decodeLines(file(runID, "units.jsonl"), as: UnitDone.self) }
    public func samples(_ runID: String) -> [PerfSample] { decodeLines(file(runID, "samples.jsonl"), as: PerfSample.self) }
    public func events(_ runID: String) -> [RecordEvent] { decodeLines(file(runID, "events.jsonl"), as: RecordEvent.self) }
    public func balloonLines(_ runID: String) -> [BalloonLine] { decodeLines(file(runID, "balloon.jsonl"), as: BalloonLine.self) }
    public func phoneResults(_ runID: String) -> [PhoneResult] { decodeLines(phoneResultsURL(runID), as: PhoneResult.self) }

    /// Index of the first unit without a final result.
    public func nextUnitIndex(_ runID: String) -> Int {
        let finals = Set(units(runID).filter(\.final).map(\.unit_index))
        var i = 0
        while finals.contains(i) { i += 1 }
        return i
    }

    /// The last `seconds` of footprint lines for one sample (kill evidence), read from the end of the log.
    public func memTail(_ runID: String, sampleID: String, seconds: Double = 2) -> [MemLine] {
        guard let h = try? FileHandle(forReadingFrom: memLogURL(runID)) else { return [] }
        defer { try? h.close() }
        let size = (try? h.seekToEnd()) ?? 0
        let chunk: UInt64 = 256 << 10
        try? h.seek(toOffset: size > chunk ? size - chunk : 0)
        guard let data = try? h.readToEnd() else { return [] }
        let lines = data.split(separator: 0x0A).compactMap { try? JSONDecoder().decode(MemLine.self, from: Data($0)) }
            .filter { $0.sample_id == sampleID }
        guard let last = lines.last else { return [] }
        let from = last.t_ns > UInt64(seconds * 1e9) ? last.t_ns - UInt64(seconds * 1e9) : 0
        return lines.filter { $0.t_ns >= from }
    }

    // MARK: inflight

    public func writeInflight(_ f: Inflight) throws {
        let url = file(f.run_id, "inflight.json")
        let data = try JSONEncoder.sorted.encode(f)
        try lock.withLock {
            try data.write(to: url, options: .atomic)
            let h = try FileHandle(forWritingTo: url)
            try h.synchronize()
            try h.close()
        }
    }

    public func inflight(_ runID: String) -> Inflight? {
        (try? Data(contentsOf: file(runID, "inflight.json"))).flatMap { try? JSONDecoder().decode(Inflight.self, from: $0) }
    }

    public func clearInflight(_ runID: String) {
        try? FileManager.default.removeItem(at: file(runID, "inflight.json"))
    }

    /// Marks the running sample as voided by a background transition, synchronously, so a
    /// suspension or kill that follows is not counted as a memory kill (spec 04 §2.8 item 7).
    public func markBackgrounded(_ runID: String) {
        guard var f = inflight(runID) else { return }
        f.backgrounded = true
        try? writeInflight(f)
    }

    // MARK: recovery

    /// Call once at launch, before anything runs. An `inflight.json` left behind means the app
    /// died inside that sample: it becomes a sample with `oom_killed` (a memory warning fired,
    /// or the last logged available memory was below 256 MiB), `oom_killed_at_load` (the same,
    /// during a load), or `killed_unknown`, with the last 2 s of the footprint log as evidence.
    /// The first kill of a unit is not final (it is retried once); the second is the result.
    @discardableResult
    public func recoverInterruptedRuns(clock: BenchClock, deviceModel: String, os: String) -> [String] {
        var recovered = [String]()
        for m in allRuns() where m.state == .running {
            defer { recovered.append(m.runID) }
            guard let f = inflight(m.runID) else {
                try? updateManifest(m.runID) { $0.state = .interrupted; $0.stopReason = "the app ended between samples" }
                continue
            }
            let now = clock.nowNanos()
            if f.test == "balloon" {
                // The kill is the balloon's result: its log says how far it got.
                try? append(UnitDone(unit_index: f.unit_index, attempt: f.attempt, final: true, status: "ok",
                                     sample_id: f.sample_id, model: nil, level: nil, during_load: false), to: m.runID)
                try? append(RecordEvent(t_ns: now, kind: "relaunch_after_kill", detail: f.sample_id), to: m.runID)
            } else if f.backgrounded {
                if f.item_id == nil {
                    var s = Self.sample(from: f, status: .voided_backgrounded, deviceModel: deviceModel, os: os)
                    s.error = "The app left the foreground during this sample; it is run again."
                    try? append(s, to: m.runID)
                }
                try? append(UnitDone(unit_index: f.unit_index, attempt: f.attempt, final: false, status: SampleStatus.voided_backgrounded.rawValue,
                                     sample_id: f.sample_id, model: f.model, level: f.level, during_load: f.phase == "load"), to: m.runID)
                try? append(RecordEvent(t_ns: now, kind: "voided_backgrounded", detail: f.sample_id), to: m.runID)
            } else {
                let tail = memTail(m.runID, sampleID: f.sample_id)
                let warned = events(m.runID).contains { $0.kind == "memory_warning" && $0.detail == f.sample_id }
                let lastAvailable = tail.last?.available ?? f.available_start
                let oom = warned || (lastAvailable.map { $0 < EDAT.oomAvailableThresholdBytes } ?? false)
                let atLoad = f.phase == "load"
                let status: SampleStatus = oom ? (atLoad ? .oom_killed_at_load : .oom_killed) : .killed_unknown
                let final = f.attempt >= 2
                let evidence = KillEvidence(
                    last_footprint_bytes: tail.last?.footprint ?? f.footprint_start, last_available_bytes: lastAvailable,
                    warning: warned, tail: tail.map { [$0.t_ns, $0.footprint, $0.available ?? 0] }, phase: f.phase)
                if let item = f.item_id {
                    let result = PhoneResult(
                        id: item, trial: f.trial ?? 0, mode: f.item_mode ?? "messages", device: m.device.shortDescription,
                        runtime: PhoneRuntime.from(m.runtime), weightsSHA256: m.models[safe: f.model]?.variant.weightsDigest,
                        rawText: "", finishReason: "killed", promptTokens: 0, completionTokens: 0, ttftS: nil, totalS: 0,
                        localPass: false, localLabels: ["killed: \(status.rawValue)"], modelID: f.model_id,
                        status: status == .killed_unknown ? "killed_unknown" : "oom_killed",
                        error: "The app was terminated during this item (detected at next launch).")
                    if let line = try? result.jsonlLine() { try? append(phoneLine: line, to: m.runID) }
                } else {
                    var s = Self.sample(from: f, status: status, deviceModel: deviceModel, os: os)
                    s.memory_warning = warned
                    s.kill_evidence = evidence
                    s.available_min_bytes = (tail.compactMap(\.available) + [f.available_start].compactMap { $0 }).min()
                    s.footprint_peak_bytes = (tail.map(\.footprint) + [f.footprint_start]).max()
                    s.error = "The app was terminated during this sample (detected at next launch)\(final ? "" : "; it is retried once")."
                    try? append(s, to: m.runID)
                }
                try? append(UnitDone(unit_index: f.unit_index, attempt: f.attempt, final: final, status: status.rawValue,
                                     sample_id: f.sample_id, model: f.model, level: f.level, during_load: atLoad), to: m.runID)
                try? append(RecordEvent(t_ns: now, kind: "relaunch_after_kill", detail: f.sample_id), to: m.runID)
            }
            clearInflight(m.runID)
            try? updateManifest(m.runID) { $0.state = .interrupted; $0.stopReason = "the app was terminated during \(f.sample_id)" }
        }
        return recovered
    }

    static func sample(from f: Inflight, status: SampleStatus, deviceModel: String, os: String) -> PerfSample {
        var s = PerfSample(sample_id: f.sample_id, model_id: f.model_id, mode: f.mode, test: f.test, phase: f.sample_phase,
                           order_index: f.unit_index, status: status, attempt: f.attempt, device_model_id: deviceModel, os: os)
        s.round = f.round
        s.rep = f.rep
        s.level_tokens = f.level
        s.variant = f.variant
        s.step = f.step
        s.t_submit_ns = f.phase == "generate" ? f.t_start_ns : nil
        s.footprint_start_bytes = f.footprint_start
        s.available_min_bytes = f.available_start
        return s
    }
}

extension Array {
    subscript(safe i: Int?) -> Element? {
        guard let i, indices.contains(i) else { return nil }
        return self[i]
    }
}

extension ModelVariant {
    /// One hash for the weights, as the results store defines it (results-record.md §2.1):
    /// sha256 over the sorted `<name>\0<sha256>\n` lines of every file in `models[].files`,
    /// when every one has been hashed. Phone results carry the same value.
    public var weightsDigest: String? {
        let all = files.sorted { $0.name < $1.name }
        guard !all.isEmpty, all.allSatisfy({ $0.sha256 != nil }) else { return nil }
        return sha256Hex(Data(all.map { "\($0.name)\u{0}\($0.sha256!)\n" }.joined().utf8))
    }
}

extension PhoneRuntime {
    public static func from(_ r: RuntimeInfo) -> PhoneRuntime {
        PhoneRuntime(mlxSwiftLM: r.packages["mlx-swift-lm"] ?? r.version, mlxSwift: r.packages["mlx-swift"] ?? r.version,
                     toolFormat: r.toolFormat, recovery: r.toolRecovery, schemaValidation: "record_only")
    }
}
