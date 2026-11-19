import Foundation

/// Builds the run's export (spec 04 §2.10): `run-<run_id>.json` (the `edat.perf/1.0` record),
/// `mem-<run_id>.jsonl` (the footprint log), `phone-<run_id>.jsonl` (phone-item results, when
/// the run had any) and `manifest-<run_id>.json` (each file's sha256), zipped as
/// `tinyagent-bench-<run_id>.zip`. Raw samples only: statistics, cells and verdicts are the Mac aggregator's.
public enum RunExporter {
    nonisolated(unsafe) static let iso: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()

    public static func record(manifest m: RunManifest, store: RunStore) -> PerfRecord {
        let samples = store.samples(m.runID).sorted { ($0.order_index, $0.sample_id) < ($1.order_index, $1.sample_id) }
        let finals = store.units(m.runID).filter(\.final)
        var attachments = [RecordAttachment]()
        for (url, kind) in [(store.memLogURL(m.runID), "footprint_log"), (store.phoneResultsURL(m.runID), "phone_results")] {
            if let d = try? Data(contentsOf: url) { attachments.append(RecordAttachment(name: url.lastPathComponent, sha256: sha256Hex(d), kind: kind)) }
        }
        let settings = m.plan.settings
        var profile = [String: JSONValue]()
        for rm in m.models {
            profile[rm.entry.id] = .object([
                "sampling": .string(settings.sampling.temperature == 0 ? "greedy" : "temperature \(settings.sampling.temperature)"),
                "seed": .number(Double(settings.sampling.seed)),
                "output_tokens": .number(Double(settings.outputTokens)),
                "ignore_eos": .bool(true),
                "thinking": .string(settings.thinking.rawValue),
                "speculation": .bool(settings.speculation),
                "new_kv_cache_per_request": .bool(true),
            ])
        }
        let r = m.runtime
        return PerfRecord(
            schema: EDAT.perfSchema, kind: "local", run_id: m.runID,
            runner: RecordRunner(name: EDAT.runnerName, version: EDAT.runnerVersion, commit: m.appCommit),
            clock: EDAT.clockName,
            started_utc: iso.string(from: m.createdAt), ended_utc: m.endedAt.map { iso.string(from: $0) },
            device: device(m.device),
            runtime: RecordRuntime(
                name: r.name, version: r.version, commit: r.commit, packages: r.packages, backend: r.backend,
                mlx_cache_limit_bytes: r.cacheLimitBytes, prefill_step_tokens: r.prefillStep, kv_dtype: r.kvDtype,
                prefix_cache: "none", ssd_kv_cache: false, expert_offload: false, gpu_power_cap_W: nil,
                memory_guard_ceiling_gb: nil, engine_profile: profile, notes: r.notes),
            models: m.models.enumerated().map { i, rm in model(rm, index: i, finals: finals, settings: settings) },
            prompt_pack: Dictionary(m.models.map { ($0.entry.id, $0.pack) }, uniquingKeysWith: { a, _ in a }),
            conditions_start: RecordConditions(m.conditionsStart, attested: m.operatorInput),
            conditions_end: m.conditionsEnd.map { RecordConditions($0, attested: m.operatorInput) },
            samples: samples, events: store.events(m.runID), energy_windows: [],
            balloon: balloon(store, m), attachments: attachments,
            plan: m.plan.id, benchmark: EDAT.benchmarkName)
    }

    static func device(_ d: DeviceIdentity) -> RecordDevice {
        RecordDevice(
            class: d.deviceClass, model_id: d.model, soc: d.soc, ram_bytes: d.ramBytes, os: d.os, os_build: d.osBuild,
            entitlements: d.entitlements.filter(\.value).map(\.key).sorted(), gpu_wired_limit_mb: nil,
            battery_nominal_Wh: nil,
            battery_nominal_source: "not readable on the device; the aggregator supplies the nominal figure and its source",
            battery_health_pct: nil, marketing_name: d.marketingName, is_simulator: d.isSimulator,
            storage_free_bytes: d.storageFreeGB.map { Int64($0 * 1e9) },
            app_budget_at_start_bytes: d.availableAtStartBytes.flatMap { a in d.footprintAtStartBytes.map { a + $0 } })
    }

    static func model(_ rm: RunModel, index: Int, finals: [UnitDone], settings: PlanSettings) -> RecordModel {
        let v = rm.variant
        let f = v.facts
        let mine = finals.filter { $0.model == index }
        let status: String
        if mine.contains(where: { $0.during_load && SampleStatus(rawValue: $0.status)?.isKill == true }) { status = "oom_killed_at_load" }
        else if mine.contains(where: { $0.status == SampleStatus.unsupported_by_runtime.rawValue }) { status = "unsupported_by_runtime" }
        else if mine.isEmpty { status = "not_loaded" }
        else { status = "ok" }
        let scheme = f.quantBits == nil ? nil : "mlx-\(f.quantMode ?? "affine")\(f.quantMixed.isEmpty ? "" : "-mixed")"
        return RecordModel(
            id: rm.entry.id, entry_id: rm.entry.id, role: rm.entry.role.rawValue, variant_of: rm.entry.variantOf,
            headline_entry: true, mode: "plain",
            files: v.files.map { RecordModelFile(name: $0.name, sha256: $0.sha256, bytes: $0.bytes) },
            quant: RecordQuant(scheme: scheme, bits: f.quantBits, group_size: f.quantGroupSize, mixed: f.quantMixed),
            architecture: f.architecture, params_total: nil, params_active: nil, params_embedding: nil,
            n_experts: nil, n_experts_active: nil, native_window_tokens: f.nativeWindow, profile_window_tokens: nil,
            thinking: settings.thinking.rawValue, speculation: settings.speculation ? "mtp" : "none", drafter: nil,
            template_sha256: v.sha256(of: "chat_template.jinja"), tokenizer_sha256: v.sha256(of: "tokenizer.json"),
            label: rm.entry.label, folder: v.id, status: status, tensor_bytes: f.tensorBytes)
    }

    static func balloon(_ store: RunStore, _ m: RunManifest) -> RecordBalloon? {
        let lines = store.balloonLines(m.runID)
        guard !lines.isEmpty else { return nil }
        let killedIDs = Set(store.events(m.runID).filter { $0.kind == "relaunch_after_kill" }.map(\.detail))
        var runOfUnit = [Int: Int]()
        for u in Schedule.expand(m.plan, models: m.slotMap) { if case .balloonRun(let i) = u.kind { runOfUnit[u.index] = i } }
        let killedRuns = Set(store.units(m.runID).compactMap { done -> Int? in
            guard let id = done.sample_id, killedIDs.contains(id) else { return nil }
            return runOfUnit[done.unit_index]
        })
        let runs = Dictionary(grouping: lines, by: \.run).sorted { $0.key < $1.key }.compactMap { run, ls -> BalloonRun? in
            guard let last = ls.max(by: { $0.step < $1.step }) else { return nil }
            let killed = killedRuns.contains(run)
            return BalloonRun(last_survived_bytes: last.footprint, footprint_plus_available_bytes: last.available.map { $0 + last.footprint },
                              steps: ls.count, killed: killed)
        }
        let survived = runs.filter(\.killed).map { Double($0.last_survived_bytes) }
        return RecordBalloon(runs: runs, budget_bytes: survived.isEmpty ? nil : UInt64(Stats.nearestRank(survived, 50)))
    }

    public static func recordData(manifest: RunManifest, store: RunStore) throws -> Data {
        try JSONEncoder.pretty.encode(record(manifest: manifest, store: store))
    }

    public static func zipName(_ runID: String) -> String { "\(EDAT.benchmarkName)-\(runID).zip" }

    /// Writes the export folder and its zip into `exportsRoot`; returns the zip.
    public static func export(runID: String, store: RunStore, to exportsRoot: URL) throws -> URL {
        let m = try store.manifest(runID)
        let fm = FileManager.default
        let folder = exportsRoot.appendingPathComponent("\(EDAT.benchmarkName)-\(runID)", isDirectory: true)
        if fm.fileExists(atPath: folder.path) { try fm.removeItem(at: folder) }
        try fm.createDirectory(at: folder, withIntermediateDirectories: true)
        var files: [(String, Data)] = [("run-\(runID).json", try recordData(manifest: m, store: store))]
        for url in [store.memLogURL(runID), store.phoneResultsURL(runID)] {
            if let d = try? Data(contentsOf: url) { files.append((url.lastPathComponent, d)) }
        }
        for (name, data) in files { try data.write(to: folder.appendingPathComponent(name), options: .atomic) }
        let manifest: [String: JSONValue] = [
            "schema": .string("edat.export-manifest/1"), "run_id": .string(runID), "record_schema": .string(EDAT.perfSchema),
            "files": .array(files.map { .object(["name": .string($0.0), "sha256": .string(sha256Hex($0.1)), "bytes": .number(Double($0.1.count))]) }),
        ]
        try JSONEncoder.pretty.encode(JSONValue.object(manifest)).write(to: folder.appendingPathComponent("manifest-\(runID).json"), options: .atomic)
        let zip = exportsRoot.appendingPathComponent(zipName(runID))
        try zipFolder(folder, to: zip)
        return zip
    }

    /// Zips a folder with the system's own archiver (`NSFileCoordinator` `.forUploading`).
    public static func zipFolder(_ folder: URL, to zip: URL) throws {
        var coordError: NSError?
        var copyError: Error?
        NSFileCoordinator().coordinate(readingItemAt: folder, options: [.forUploading], error: &coordError) { tmp in
            do {
                if FileManager.default.fileExists(atPath: zip.path) { try FileManager.default.removeItem(at: zip) }
                try FileManager.default.copyItem(at: tmp, to: zip)
            } catch { copyError = error }
        }
        if let e = coordError ?? (copyError as NSError?) { throw e }
    }
}

/// On-phone preview of a run, for the Results screen only. The published numbers come from
/// the Mac aggregator reading the raw samples.
public struct CellPreview: Sendable, Identifiable {
    public var id: String { "\(model)|\(test)|\(level ?? -1)" }
    public var model: String
    public var test: String
    public var level: Int?
    public var okCount: Int
    public var statuses: [String: Int]
    public var decodeTPS: Double?
    public var prefillTPS: Double?
    public var ttftS: Double?
    public var peakFootprintBytes: UInt64?

    public static func cells(_ samples: [PerfSample]) -> [CellPreview] {
        let groups = Dictionary(grouping: samples.filter { $0.phase == "measured" || $0.test.hasPrefix("load") }) {
            "\($0.model_id)|\($0.test)|\($0.level_tokens ?? -1)"
        }
        return groups.values.compactMap { g -> CellPreview? in
            guard let first = g.first else { return nil }
            let ok = g.filter { $0.status == .ok }
            func med(_ v: [Double]) -> Double? { v.isEmpty ? nil : Stats.nearestRank(v, 50) }
            return CellPreview(
                model: first.model_id, test: first.test, level: first.level_tokens, okCount: ok.count,
                statuses: Dictionary(grouping: g, by: \.status.rawValue).mapValues(\.count),
                decodeTPS: med(ok.compactMap(\.decodeTPS)), prefillTPS: med(ok.compactMap(\.prefillTPS)),
                ttftS: med(ok.compactMap(\.ttftS)), peakFootprintBytes: g.compactMap(\.footprint_peak_bytes).max())
        }.sorted { ($0.model, $0.test, $0.level ?? 0) < ($1.model, $1.test, $1.level ?? 0) }
    }
}
