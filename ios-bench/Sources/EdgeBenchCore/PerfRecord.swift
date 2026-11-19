import Foundation

/// Names and ids shared with the Mac (spec 04 §2–§3). Every schema id lives here, once.
public enum EDAT {
    /// The result record's schema id (spec 04 §3.1).
    public static let perfSchema = "edat.perf/1.0"
    /// The benchmark's name as readers see it (app name, export files). Schema ids keep their
    /// `edat` prefix until the benchmark repository renames them.
    public static let benchmarkName = "tinyagent-bench"
    public static let runnerName = "iphone-mlx-swift"
    public static let runnerVersion = "0.1.0"
    public static let clockName = "mach_continuous_time"
    /// The export folder the Files app shows under On My iPhone.
    public static let exportFolderName = "tinyagent-bench"
    /// The MLX buffer-cache limit every run uses and records (spec 04 §2.1).
    public static let mlxCacheLimitBytes: UInt64 = 256 << 20
    /// A kill counts as `oom_killed` when the last logged available memory was below this (spec 04 §2.8).
    public static let oomAvailableThresholdBytes: UInt64 = 256 << 20
    /// The prompt pack format this app reads (proposed here; spec 04 §2.3 names the contents only).
    public static let promptPackSchema = "edat.prompt-pack/0.1"
}

// The wire types below use the record's own snake_case names as Swift property names, so the
// JSON keys are exactly the spec's (no key-conversion strategy that could mangle `_Wh` or `_C`).
// They encode every field, writing `null` for a missing one: the spec says a real record
// carries every §3.6 field.

/// Encodes every stored property under its own name, `null` for nil.
public protocol AllFieldsEncodable: Encodable {}

struct AnyKey: CodingKey {
    var stringValue: String
    var intValue: Int? { nil }
    init(_ s: String) { stringValue = s }
    init?(stringValue: String) { self.stringValue = stringValue }
    init?(intValue: Int) { nil }
}

extension AllFieldsEncodable {
    public func encodeAllFields(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: AnyKey.self)
        for child in Mirror(reflecting: self).children {
            guard let label = child.label else { continue }
            if let v = child.value as? any Encodable {
                try c.encode(v, forKey: AnyKey(label))
            } else {
                // A nil Optional does not cast to an existential: write the null explicitly.
                try c.encodeNil(forKey: AnyKey(label))
            }
        }
    }
}

public enum SampleStatus: String, Codable, Sendable, CaseIterable {
    case ok
    case oom_killed
    case oom_killed_at_load
    case killed_unknown
    case not_run_above_oom
    case exceeds_native_window
    case engine_refused
    case truncated
    case timeout
    case unsupported_by_runtime
    case voided_backgrounded

    public var isKill: Bool { self == .oom_killed || self == .oom_killed_at_load || self == .killed_unknown }
}

public struct LoadInfo: Codable, Sendable, Equatable, AllFieldsEncodable {
    public var t_load_start_ns: UInt64
    public var t_ready_ns: UInt64?
    public var t_first_token_possible_ns: UInt64?
    public var cold_verified: Bool
    public var disk_read_bytes: Int64?
    /// Additive: `getrusage` block-input operations during the load (a count, not bytes;
    /// whether iOS counts mmap page-ins here is unverified).
    public var disk_block_inputs: Int64?
    public func encode(to encoder: Encoder) throws { try encodeAllFields(to: encoder) }
}

public struct KillEvidence: Codable, Sendable, Equatable, AllFieldsEncodable {
    public var last_footprint_bytes: UInt64?
    public var last_available_bytes: UInt64?
    public var warning: Bool
    /// `[t_ns, footprint, available]` for the last 2 s of the footprint log.
    public var tail: [[UInt64]]
    /// Additive: where the kill happened (`load` | `prefill` | `decode` | `idle` | `balloon`).
    public var phase: String?
    public func encode(to encoder: Encoder) throws { try encodeAllFields(to: encoder) }
}

/// One request (spec 04 §3.6). Warm-ups included. Additive iPhone fields at the end.
public struct PerfSample: Codable, Sendable, Equatable, AllFieldsEncodable {
    public var sample_id: String
    public var model_id: String
    public var mode: String
    public var test: String
    public var phase: String
    public var round: Int?
    public var rep: Int?
    public var order_index: Int
    public var level_tokens: Int?
    public var prompt_tokens: Int?
    public var cached_tokens: Int?
    public var variant: Int?
    public var shape: String?
    public var step: Int?
    public var t_submit_ns: UInt64?
    public var t_prefill_done_ns: UInt64?
    public var t_first_token_ns: UInt64?
    public var t_last_token_ns: UInt64?
    public var t_end_ns: UInt64?
    public var token_times_ns: [UInt64]?
    public var output_tokens: Int?
    public var reasoning_tokens: Int?
    public var drafted_tokens: Int?
    public var accepted_tokens: Int?
    public var load: LoadInfo?
    public var footprint_start_bytes: UInt64?
    public var footprint_peak_bytes: UInt64?
    public var footprint_peak_ledger_bytes: UInt64?
    public var footprint_steady_bytes: UInt64?
    public var footprint_end_bytes: UInt64?
    public var neural_bytes: UInt64?
    public var available_min_bytes: UInt64?
    public var swap_delta_bytes: Int64?
    public var compressed_delta_bytes: Int64?
    public var mlx_active_bytes: UInt64?
    public var mlx_cache_bytes: UInt64?
    public var mlx_peak_bytes: UInt64?
    public var thermal_start: String?
    public var thermal_end: String?
    public var cooldown_wait_s: Double?
    public var thermal_not_nominal: Bool
    public var memory_warning: Bool
    public var battery_level_start: Double?
    public var battery_level_end: Double?
    public var energy_window_id: String?
    public var status: SampleStatus
    public var error: String?
    public var kill_evidence: KillEvidence?
    // Additive (iPhone): every sample is self-describing.
    public var attempt: Int
    public var device_model_id: String
    public var os: String
    public var power_source: String?
    public var low_power_mode: Bool?
    public var app_state: String?
    /// `idle_resident` segments: whether the model was loaded.
    public var resident: Bool?
    /// Engine-reported prompt time, beside the client clock (diagnostic).
    public var engine_prompt_s: Double?
    public var finish_reason: String?

    public func encode(to encoder: Encoder) throws { try encodeAllFields(to: encoder) }

    public init(sample_id: String, model_id: String, mode: String, test: String, phase: String, order_index: Int,
                status: SampleStatus, attempt: Int, device_model_id: String, os: String) {
        self.sample_id = sample_id
        self.model_id = model_id
        self.mode = mode
        self.test = test
        self.phase = phase
        self.order_index = order_index
        self.status = status
        self.attempt = attempt
        self.device_model_id = device_model_id
        self.os = os
        thermal_not_nominal = false
        memory_warning = false
    }

    // Derived numbers for the on-phone preview only; the Mac aggregator computes the published ones.
    public var decodeTPS: Double? {
        guard let times = token_times_ns, times.count >= 2, let last = times.last, last > 0 else { return nil }
        return Double(times.count - 1) / (Double(last) / 1e9)
    }
    public var prefillTPS: Double? {
        guard let s = t_submit_ns, let p = t_prefill_done_ns, p > s, let n = prompt_tokens else { return nil }
        return Double(n) / (Double(p - s) / 1e9)
    }
    public var ttftS: Double? {
        guard let s = t_submit_ns, let f = t_first_token_ns, f >= s else { return nil }
        return Double(f - s) / 1e9
    }
}

public struct RecordEvent: Codable, Sendable, Equatable {
    public var t_ns: UInt64
    /// `thermal_change`, `memory_warning`, `relaunch_after_kill`, `cooldown_timeout`, `operator_note`;
    /// additive: `paused`, `resumed`, `voided_backgrounded`, `battery_floor`, `model_loaded`.
    public var kind: String
    public var detail: String
    public init(t_ns: UInt64, kind: String, detail: String) {
        self.t_ns = t_ns
        self.kind = kind
        self.detail = detail
    }
}

public struct RecordAttest: Codable, Sendable, Equatable {
    public var airplane_mode: Bool
    public var no_case: Bool
    public var hard_surface: Bool
    public var auto_brightness_off: Bool
    public var other_apps_closed: Bool
    public init(airplane_mode: Bool = false, no_case: Bool = false, hard_surface: Bool = false,
                auto_brightness_off: Bool = false, other_apps_closed: Bool = false) {
        self.airplane_mode = airplane_mode
        self.no_case = no_case
        self.hard_surface = hard_surface
        self.auto_brightness_off = auto_brightness_off
        self.other_apps_closed = other_apps_closed
    }
}

/// What the operator checks and types before a run (spec 04 §2.2).
public struct OperatorAttestation: Codable, Sendable, Equatable {
    public var attest: RecordAttest
    public var ambient_C: Double?
    /// The operator rebooted, waited 5 minutes and opened the app (cold-load test).
    public var rebooted_before_run: Bool
    public var note: String?
    public init(attest: RecordAttest = .init(), ambient_C: Double? = nil, rebooted_before_run: Bool = false, note: String? = nil) {
        self.attest = attest
        self.ambient_C = ambient_C
        self.rebooted_before_run = rebooted_before_run
        self.note = note
    }
}

public struct RecordConditions: Codable, Sendable, Equatable, AllFieldsEncodable {
    public var power_source: String
    public var battery_level: Double?
    public var battery_state: String
    public var low_power_mode: Bool
    public var power_mode: String?
    public var display: String
    public var brightness: Double?
    public var thermal_state: String
    public var uptime_s: Double
    public var ambient_C: Double?
    public var attest: RecordAttest
    public var foreign_gpu_work: Bool?
    public var app_state: String?
    public func encode(to encoder: Encoder) throws { try encodeAllFields(to: encoder) }

    public init(_ c: Conditions, attested o: OperatorAttestation) {
        power_source = c.powerSource == .battery ? "battery" : (c.powerSource == .unknown ? "unknown" : "ac")
        battery_level = c.batteryPct.map { $0 / 100 }
        battery_state = c.powerSource.batteryState
        low_power_mode = c.lowPowerMode
        power_mode = nil
        display = "on"
        brightness = c.screenBrightness
        thermal_state = c.thermal.rawValue
        uptime_s = c.uptimeS ?? ProcessInfo.processInfo.systemUptime
        ambient_C = o.ambient_C
        attest = o.attest
        foreign_gpu_work = nil
        app_state = c.appState
    }
}

public struct RecordDevice: Codable, Sendable, Equatable, AllFieldsEncodable {
    public var `class`: String
    public var model_id: String
    public var soc: String?
    public var ram_bytes: UInt64
    public var os: String
    public var os_build: String?
    public var entitlements: [String]
    public var gpu_wired_limit_mb: Int?
    public var battery_nominal_Wh: Double?
    public var battery_nominal_source: String?
    public var battery_health_pct: Double?
    // Additive
    public var marketing_name: String?
    public var is_simulator: Bool
    public var storage_free_bytes: Int64?
    /// `phys_footprint + os_proc_available_memory()` when the run was created.
    public var app_budget_at_start_bytes: UInt64?
    public func encode(to encoder: Encoder) throws { try encodeAllFields(to: encoder) }
}

public struct RecordRuntime: Codable, Sendable, Equatable, AllFieldsEncodable {
    public var name: String
    public var version: String
    public var commit: String?
    public var packages: [String: String]
    public var backend: String
    public var mlx_cache_limit_bytes: UInt64?
    public var prefill_step_tokens: Int?
    public var kv_dtype: String?
    public var prefix_cache: String
    public var ssd_kv_cache: Bool
    public var expert_offload: Bool
    public var gpu_power_cap_W: Double?
    public var memory_guard_ceiling_gb: Double?
    public var engine_profile: [String: JSONValue]
    public var notes: [String]
    public func encode(to encoder: Encoder) throws { try encodeAllFields(to: encoder) }
}

public struct RecordModelFile: Codable, Sendable, Equatable, AllFieldsEncodable {
    public var name: String
    public var sha256: String?
    public var bytes: Int64
    public func encode(to encoder: Encoder) throws { try encodeAllFields(to: encoder) }
}

public struct RecordQuant: Codable, Sendable, Equatable, AllFieldsEncodable {
    public var scheme: String?
    public var bits: Int?
    public var group_size: Int?
    public var mixed: [String: Int]
    public func encode(to encoder: Encoder) throws { try encodeAllFields(to: encoder) }
}

public struct RecordModel: Codable, Sendable, Equatable, AllFieldsEncodable {
    public var id: String
    public var entry_id: String
    public var role: String
    public var variant_of: String?
    public var headline_entry: Bool
    public var mode: String
    public var files: [RecordModelFile]
    public var quant: RecordQuant
    public var architecture: String
    public var params_total: Int64?
    public var params_active: Int64?
    public var params_embedding: Int64?
    public var n_experts: Int?
    public var n_experts_active: Int?
    public var native_window_tokens: Int?
    public var profile_window_tokens: Int?
    public var thinking: String
    public var speculation: String
    public var drafter: JSONValue?
    public var template_sha256: String?
    public var tokenizer_sha256: String?
    // Additive
    public var label: String
    public var folder: String
    /// `ok` | `oom_killed_at_load` | `unsupported_by_runtime` | `not_loaded`
    public var status: String
    public var tensor_bytes: [String: Int64]
    public func encode(to encoder: Encoder) throws { try encodeAllFields(to: encoder) }
}

public struct RecordPromptPack: Codable, Sendable, Equatable, AllFieldsEncodable {
    public var pack_sha256: String?
    public var corpus: String
    public var version: String
    public var language: String
    // Additive
    /// `pack` (pre-tokenized on the Mac) or `on_device_builder` (stand-in, not comparable).
    public var source: String
    public var file_sha256: String?
    public var tokenizer_sha256: String?
    public var template_sha256: String?
    public func encode(to encoder: Encoder) throws { try encodeAllFields(to: encoder) }
}

public struct BalloonRun: Codable, Sendable, Equatable, AllFieldsEncodable {
    public var last_survived_bytes: UInt64
    public var footprint_plus_available_bytes: UInt64?
    // Additive
    public var steps: Int
    public var killed: Bool
    public func encode(to encoder: Encoder) throws { try encodeAllFields(to: encoder) }
}

public struct RecordBalloon: Codable, Sendable, Equatable, AllFieldsEncodable {
    public var runs: [BalloonRun]
    public var budget_bytes: UInt64?
    public func encode(to encoder: Encoder) throws { try encodeAllFields(to: encoder) }
}

public struct RecordAttachment: Codable, Sendable, Equatable {
    public var name: String
    public var sha256: String
    public var kind: String
}

public struct RecordRunner: Codable, Sendable, Equatable, AllFieldsEncodable {
    public var name: String
    public var version: String
    public var commit: String?
    public func encode(to encoder: Encoder) throws { try encodeAllFields(to: encoder) }
}

/// The whole `edat.perf/1.0` run record (spec 04 §3.1). `cells` and `verdicts` are the
/// aggregator's and never written here.
public struct PerfRecord: Codable, Sendable, Equatable, AllFieldsEncodable {
    public var schema: String
    public var kind: String
    public var run_id: String
    public var runner: RecordRunner
    public var clock: String
    public var started_utc: String
    public var ended_utc: String?
    public var device: RecordDevice
    public var runtime: RecordRuntime
    public var models: [RecordModel]
    public var prompt_pack: [String: RecordPromptPack]
    public var conditions_start: RecordConditions
    public var conditions_end: RecordConditions?
    public var samples: [PerfSample]
    public var events: [RecordEvent]
    public var energy_windows: [JSONValue]
    public var balloon: RecordBalloon?
    public var attachments: [RecordAttachment]
    // Additive
    public var plan: String
    public var benchmark: String
    public func encode(to encoder: Encoder) throws { try encodeAllFields(to: encoder) }
}
