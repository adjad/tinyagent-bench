import Foundation

/// A test plan: what to run, on which model slot, in which order. Plans are data, so a run's
/// manifest carries its exact plan and a resumed run follows the same schedule.
public struct TestPlan: Codable, Sendable, Equatable, Identifiable {
    public var id: String
    public var title: String
    public var summary: String
    /// Operator-facing duration estimate for the baseline on an iPhone Air (a guess until measured).
    public var estimatedMinutes: Int
    public var steps: [PlanStep]
    public var settings: PlanSettings

    public init(id: String, title: String, summary: String, estimatedMinutes: Int, steps: [PlanStep], settings: PlanSettings = .init()) {
        self.id = id
        self.title = title
        self.summary = summary
        self.estimatedMinutes = estimatedMinutes
        self.steps = steps
        self.settings = settings
    }

    /// The model slots this plan needs filled before it can start.
    public var slots: Set<ModelSlot> { Set(steps.compactMap(\.slot)) }
}

public struct PlanSettings: Codable, Sendable, Equatable {
    /// Exactly 256 output tokens, EOS ignored (spec 04 §2.4).
    public var outputTokens = 256
    /// Reps per level up to 10,240 / at 32,768 and 65,536 (spec 04 §2.6: 10 and 5).
    public var reps = 10
    public var repsLarge = 5
    public var largeLevelThreshold = 32_768
    /// Greedy decoding with a recorded seed (spec 04 §2.4), for every perf test.
    public var sampling = Sampling.greedy
    public var thinking = ThinkingMode.on
    public var speculation = false
    /// Cooldown gate before every sample: wait for thermal nominal up to this long (spec 04 §2.6).
    public var cooldownMaxWaitSeconds = 300.0
    public var cooldownPollSeconds = 5.0
    /// Fixed gap after every sample.
    public var gapSeconds = 5.0
    /// Pause the run when the battery is below this and not charging (spec 04 §2.2).
    public var batteryFloorPct = 30.0
    /// Per-sample wall limit.
    public var cellTimeoutSeconds = 600.0
    /// Footprint sampling interval: 50 ms = 20 Hz (spec 04 §2.5).
    public var memorySampleMilliseconds = 50

    public init() {}

    public func reps(for level: Int) -> Int { level >= largeLevelThreshold ? repsLarge : reps }
}

/// Which of the run's models a step uses. The operator fills the slots on the Run screen:
/// `primary` is Gemma-Wisp (or its iPhone fallback variant), `reference` is base Gemma 4 E4B
/// (or its fallback).
public enum ModelSlot: String, Codable, Sendable, CaseIterable {
    case primary, reference
}

public struct PlanStep: Codable, Sendable, Equatable {
    public var slot: ModelSlot?
    public var test: StepTest
    public init(_ slot: ModelSlot?, _ test: StepTest) {
        self.slot = slot
        self.test = test
    }
}

public enum StepTest: Codable, Sendable, Equatable {
    /// Unload (release, clear cache), load, first token of the pack's 8-token prompt.
    case loadWarm(reps: Int)
    /// The first load after a reboot plus 5 minutes' rest (the operator attests the reboot).
    case loadCold
    /// Each level, 256 output tokens: round 0 warm-up ascending, then measured rounds
    /// alternating ascending / descending. `reps` overrides the settings' 10 / 5.
    case levels([Int], reps: Int?)
    /// The pack's 6-step trajectory, fresh cache per step.
    case agentReplay(replays: Int)
    /// Back-to-back requests at one level for `minutes` (no cooldown gate: it measures throttling).
    case sustained(minutes: Int, level: Int)
    /// Unplugged hour: a burst every `everySeconds` (the agent replay if the pack has one, else
    /// one request at `level`), idle in between. `withModel: false` is the matched control.
    case battery(minutes: Int, everySeconds: Int, level: Int, withModel: Bool)
    /// Loaded-idle / unloaded segments of `minutes`, ABBA, then one wake request.
    case idleResident(minutes: Int)
    /// The phone parity items (`edat-phone-item/v1`), `trials` each, in one mode.
    case phoneItems(trials: Int, mode: String, limit: Int?)
    /// Memory-budget calibration: allocate 64 MiB buffers until the app is killed.
    case balloon(runs: Int)
}

/// One schedulable unit. Each produces one sample (or one phone result, or one balloon run),
/// and the store records it, so a resumed run continues at the first unit without a final result.
public struct WorkUnit: Codable, Sendable, Equatable {
    public enum Kind: Codable, Sendable, Equatable {
        case loadWarm(rep: Int)
        case loadCold
        case level(level: Int, round: Int, rep: Int, warmup: Bool)
        case replayStep(replay: Int, step: Int, warmup: Bool)
        case sustained(level: Int, index: Int, minutes: Int)
        case batteryTask(level: Int, index: Int, atSecond: Int, withModel: Bool)
        case batteryEnd(minutes: Int, withModel: Bool)
        case idleSegment(index: Int, loaded: Bool, minutes: Int)
        case wake
        case phoneItem(item: Int, trial: Int, mode: String)
        case balloonRun(index: Int)
    }

    public var index: Int
    public var stepIndex: Int
    /// Index into the run's models; nil for units with no model (balloon, battery control).
    public var model: Int?
    public var kind: Kind

    /// The record's `test` value.
    public var test: String {
        switch kind {
        case .loadWarm: "load_warm"
        case .loadCold: "load_cold"
        case .level: "level"
        case .replayStep: "agent_replay"
        case .sustained: "sustained"
        case .batteryTask, .batteryEnd: "battery"
        case .idleSegment: "idle_resident"
        case .wake: "wake"
        case .phoneItem: "phone_item"
        case .balloonRun: "balloon"
        }
    }

    public var level: Int? {
        switch kind {
        case .level(let l, _, _, _), .sustained(let l, _, _): l
        case .batteryTask(let l, _, _, let withModel): withModel ? l : nil
        case .wake: 512
        default: nil
        }
    }

    public var isWarmup: Bool {
        switch kind {
        case .level(_, _, _, let w), .replayStep(_, _, let w): w
        default: false
        }
    }

    /// Units that make no sample of their own (waits and the balloon, which has its own log).
    public var makesSample: Bool {
        switch kind {
        case .batteryEnd, .phoneItem, .balloonRun: false
        case .batteryTask(_, _, _, let withModel): withModel
        default: true
        }
    }

    public var label: String {
        switch kind {
        case .loadWarm(let rep): "Warm load #\(rep + 1)"
        case .loadCold: "Cold load"
        case .level(let l, let round, _, let w): w ? "\(Self.fmt(l)) tokens · warm-up" : "\(Self.fmt(l)) tokens · round \(round)"
        case .replayStep(let r, let s, let w): "Agent replay \(w ? "warm-up" : "#\(r)") · step \(s)"
        case .sustained(let l, let i, _): "Sustained \(Self.fmt(l)) · request \(i + 1)"
        case .batteryTask(_, let i, _, let w): "Battery \(w ? "test" : "control") · burst \(i + 1)"
        case .batteryEnd: "Battery · end of hour"
        case .idleSegment(let i, let loaded, _): "Idle \(loaded ? "loaded" : "unloaded") · segment \(i + 1)"
        case .wake: "Wake request"
        case .phoneItem(let i, let t, let m): "Item \(i + 1) · trial \(t + 1) · \(m)"
        case .balloonRun(let i): "Balloon #\(i + 1)"
        }
    }

    static func fmt(_ n: Int) -> String {
        let f = NumberFormatter()
        f.numberStyle = .decimal
        return f.string(from: NSNumber(value: n)) ?? String(n)
    }
}

public enum Schedule {
    /// Expands a plan into units. `models` maps each slot to an index into the run's model
    /// list; steps whose slot is not filled are left out. Time-boxed tests are expanded to an
    /// upper bound and the runner stops them when their time is up.
    public static func expand(_ plan: TestPlan, models: [ModelSlot: Int], phoneItemCount: Int = 0,
                              replaySteps: Int = 6) -> [WorkUnit] {
        var units = [WorkUnit]()
        for (s, step) in plan.steps.enumerated() {
            let model = step.slot.flatMap { models[$0] }
            if step.slot != nil, model == nil { continue }
            func add(_ kind: WorkUnit.Kind, model m: Int? = model) {
                units.append(WorkUnit(index: units.count, stepIndex: s, model: m, kind: kind))
            }
            switch step.test {
            case .loadWarm(let reps):
                for r in 0 ..< reps { add(.loadWarm(rep: r)) }
            case .loadCold:
                add(.loadCold)
            case .levels(let levels, let repsOverride):
                let ascending = levels.sorted()
                func reps(_ l: Int) -> Int { repsOverride ?? plan.settings.reps(for: l) }
                // Round 0: one warm-up per level, ascending (also finds kills early).
                for l in ascending { add(.level(level: l, round: 0, rep: 0, warmup: true)) }
                let rounds = ascending.map(reps).max() ?? 0
                for round in 1 ... max(1, rounds) where rounds > 0 {
                    let order = round % 2 == 1 ? ascending : ascending.reversed()
                    for l in order where round <= reps(l) { add(.level(level: l, round: round, rep: round - 1, warmup: false)) }
                }
            case .agentReplay(let replays):
                for st in 1 ... replaySteps { add(.replayStep(replay: 0, step: st, warmup: true)) }
                for r in 1 ... max(1, replays) {
                    for st in 1 ... replaySteps { add(.replayStep(replay: r, step: st, warmup: false)) }
                }
            case .sustained(let minutes, let level):
                // Upper bound: one request per 2 s of the window.
                for i in 0 ..< max(1, minutes * 30) { add(.sustained(level: level, index: i, minutes: minutes)) }
            case .battery(let minutes, let every, let level, let withModel):
                let count = max(1, (minutes * 60) / max(1, every))
                for i in 0 ..< count {
                    add(.batteryTask(level: level, index: i, atSecond: i * every, withModel: withModel), model: withModel ? model : nil)
                }
                add(.batteryEnd(minutes: minutes, withModel: withModel), model: withModel ? model : nil)
            case .idleResident(let minutes):
                for (i, loaded) in [true, false, false, true].enumerated() { add(.idleSegment(index: i, loaded: loaded, minutes: minutes)) }
                add(.wake)
            case .phoneItems(let trials, let mode, let limit):
                let n = min(phoneItemCount, limit ?? Int.max)
                for t in 0 ..< max(1, trials) {
                    for i in 0 ..< n { add(.phoneItem(item: i, trial: t, mode: mode)) }
                }
            case .balloon(let runs):
                for i in 0 ..< max(1, runs) { add(.balloonRun(index: i), model: nil) }
            }
        }
        return units
    }
}

extension TestPlan {
    public static let ladder = [512, 1_024, 2_048, 5_120, 10_240, 32_768, 65_536]
    public static let bracketLevels = [512, 2_048, 10_240]

    /// Built-in plans, cheapest first. Estimates assume roughly 300–600 prefill tok/s and
    /// 12–25 decode tok/s on an iPhone Air (unmeasured guesses), plus the 5 s gaps.
    public static let builtIn: [TestPlan] = [
        TestPlan(id: "smoke", title: "Smoke check",
                 summary: "Two warm loads, then 512 and 2,048 tokens × 3 reps, plus 6 phone items. Proves the model loads and every field is recorded.",
                 estimatedMinutes: 8,
                 steps: [PlanStep(.primary, .loadWarm(reps: 2)), PlanStep(.primary, .levels([512, 2_048], reps: 3)),
                         PlanStep(.primary, .phoneItems(trials: 1, mode: "messages", limit: 6))]),
        TestPlan(id: "load-cold", title: "Cold load (after a reboot)",
                 summary: "Reboot the phone, wait 5 minutes, open tinyagent-bench and run this first. Run it three times (three reboots).",
                 estimatedMinutes: 2,
                 steps: [PlanStep(.primary, .loadCold)]),
        TestPlan(id: "load-warm", title: "Warm loads",
                 summary: "Unload and load again, 5 times; each ends with the first token of the 8-token prompt.",
                 estimatedMinutes: 5,
                 steps: [PlanStep(.primary, .loadWarm(reps: 5))]),
        TestPlan(id: "bracket", title: "Reference bracket (Wisp, base … base, Wisp)",
                 summary: "Gemma-Wisp then base Gemma 4 E4B, and at the end base then Gemma-Wisp, at 512, 2,048 and 10,240 tokens, 5 reps per half: 10 reps per model per level. The repeatability check (spec 04 §1.5).",
                 estimatedMinutes: 70,
                 steps: [PlanStep(.primary, .levels(bracketLevels, reps: 5)), PlanStep(.reference, .levels(bracketLevels, reps: 5)),
                         PlanStep(.reference, .levels(bracketLevels, reps: 5)), PlanStep(.primary, .levels(bracketLevels, reps: 5))]),
        TestPlan(id: "ladder-full", title: "Full level sweep (to 64k)",
                 summary: "All seven levels: 10 reps up to 10,240, 5 at 32,768 and 65,536. A kill is the result for that level; larger levels are then not run.",
                 estimatedMinutes: 150,
                 steps: [PlanStep(.primary, .levels(ladder, reps: nil))]),
        TestPlan(id: "reference-sweep", title: "Base Gemma full sweep",
                 summary: "Base Gemma 4 E4B over all seven levels plus warm loads. Once per new device, iOS and runtime version.",
                 estimatedMinutes: 160,
                 steps: [PlanStep(.reference, .loadWarm(reps: 5)), PlanStep(.reference, .levels(ladder, reps: nil))]),
        TestPlan(id: "agent-replay", title: "Agent replay",
                 summary: "The prompt pack's 6-step trajectory, 5 replays, fresh cache per step. Needs a prompt pack.",
                 estimatedMinutes: 25,
                 steps: [PlanStep(.primary, .agentReplay(replays: 5))]),
        TestPlan(id: "phone-items", title: "Phone parity items (messages mode)",
                 summary: "Every phone item, 3 trials, prompt rendered on the phone. These are the published iPhone quality numbers.",
                 estimatedMinutes: 90,
                 steps: [PlanStep(.primary, .phoneItems(trials: 3, mode: "messages", limit: nil))]),
        TestPlan(id: "phone-items-rendered", title: "Phone items, rendered-prompt slice",
                 summary: "The first 30 items in messages mode and again with the Mac-rendered prompt, to split template differences from numeric ones (diagnostic).",
                 estimatedMinutes: 30,
                 steps: [PlanStep(.primary, .phoneItems(trials: 1, mode: "messages", limit: 30)),
                         PlanStep(.primary, .phoneItems(trials: 1, mode: "rendered", limit: 30))]),
        TestPlan(id: "sustained", title: "Sustained 20 min",
                 summary: "Back-to-back 2,048-token requests for 20 minutes with 5 s gaps and no cooldown wait.",
                 estimatedMinutes: 22,
                 steps: [PlanStep(.primary, .sustained(minutes: 20, level: 2_048))]),
        TestPlan(id: "battery", title: "Battery hour",
                 summary: "Unplugged from 50–95 %: a burst every 3 minutes for 60 minutes, model resident, screen on at minimum brightness.",
                 estimatedMinutes: 61,
                 steps: [PlanStep(.primary, .battery(minutes: 60, everySeconds: 180, level: 2_048, withModel: true))]),
        TestPlan(id: "battery-control", title: "Battery hour, control (no model)",
                 summary: "The same hour with the app open and no model loaded. Run it on the same day as the battery hour.",
                 estimatedMinutes: 61,
                 steps: [PlanStep(nil, .battery(minutes: 60, everySeconds: 180, level: 2_048, withModel: false))]),
        TestPlan(id: "idle-resident", title: "Idle resident",
                 summary: "30 minutes loaded and idle, 30 unloaded, ABBA (2 hours), then one wake request.",
                 estimatedMinutes: 121,
                 steps: [PlanStep(.primary, .idleResident(minutes: 30))]),
        TestPlan(id: "balloon", title: "Memory budget (balloon)",
                 summary: "Allocates 64 MiB buffers until iOS kills the app, three times. Open the app again after each kill. No model.",
                 estimatedMinutes: 5,
                 steps: [PlanStep(nil, .balloon(runs: 3))]),
    ]
}
