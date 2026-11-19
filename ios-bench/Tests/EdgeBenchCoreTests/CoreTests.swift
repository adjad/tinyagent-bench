import Foundation
import XCTest
@testable import EdgeBenchCore

final class StatsTests: XCTestCase {
    func testNearestRankMatchesHarborPython() {
        // scripts/release_performance.py nearest_rank: rank = ceil(p*n/100)
        let v = (1 ... 10).map(Double.init)
        XCTAssertEqual(Stats.nearestRank(v, 50), 5)
        XCTAssertEqual(Stats.nearestRank(v, 25), 3)
        XCTAssertEqual(Stats.nearestRank(v, 75), 8)
        XCTAssertEqual(Stats.nearestRank(v, 95), 10)
        XCTAssertEqual(Stats.nearestRank([7], 95), 7)
    }
}

final class ClockTests: XCTestCase {
    func testMachClockIsMonotonicNanoseconds() async throws {
        let c = MachClock()
        XCTAssertEqual(c.name, "mach_continuous_time")
        let a = c.nowNanos()
        try await Task.sleep(nanoseconds: 20_000_000)
        let b = c.nowNanos()
        XCTAssertGreaterThan(b, a)
        XCTAssertGreaterThan(b - a, 15_000_000)
        XCTAssertLessThan(b - a, 2_000_000_000)
    }
}

final class ScheduleTests: XCTestCase {
    func code(_ u: WorkUnit) -> String {
        switch u.kind {
        case .level(let l, let round, _, let w): w ? "W\(l)" : "R\(round):\(l)"
        default: "?"
        }
    }

    func testWarmupRoundAscendingThenAlternatingRounds() throws {
        let env = try TestEnv()
        let plan = env.plan([PlanStep(.primary, .levels([2_048, 512, 65_536], reps: nil))]) { $0.reps = 3; $0.repsLarge = 1 }
        let units = Schedule.expand(plan, models: [.primary: 0])
        XCTAssertEqual(units.map(code), ["W512", "W2048", "W65536",
                                         "R1:512", "R1:2048", "R1:65536",
                                         "R2:2048", "R2:512",
                                         "R3:512", "R3:2048"])
        XCTAssertEqual(units.map(\.index), Array(0 ..< units.count))
        XCTAssertTrue(units.allSatisfy { $0.model == 0 })
    }

    func testBracketUsesBothSlotsInABBAOrder() {
        let bracket = TestPlan.builtIn.first { $0.id == "bracket" }!
        XCTAssertEqual(bracket.slots, [.primary, .reference])
        let units = Schedule.expand(bracket, models: [.primary: 0, .reference: 1])
        var runs = [Int]()
        for u in units where runs.last != u.model { runs.append(u.model!) }
        XCTAssertEqual(runs, [0, 1, 0])   // Wisp, base, base (one block), Wisp
        let base = units.filter { $0.model == 1 && !$0.isWarmup }
        XCTAssertEqual(Dictionary(grouping: base, by: { $0.level! }).mapValues(\.count), [512: 10, 2_048: 10, 10_240: 10])
        // A plan step whose slot is empty is left out.
        XCTAssertTrue(Schedule.expand(bracket, models: [.primary: 0]).allSatisfy { $0.model == 0 })
    }

    func testEveryBuiltInPlanExpands() {
        for plan in TestPlan.builtIn {
            XCTAssertFalse(Schedule.expand(plan, models: [.primary: 0, .reference: 1], phoneItemCount: 35).isEmpty, plan.id)
        }
        let battery = TestPlan.builtIn.first { $0.id == "battery-control" }!
        let units = Schedule.expand(battery, models: [:])
        XCTAssertEqual(units.count, 21)
        XCTAssertTrue(units.allSatisfy { $0.model == nil && !$0.makesSample })
    }
}

final class PromptPackTests: XCTestCase {
    func testVariantsHitTheLevelExactlyAndWrapAt16() throws {
        let pack = PromptPack.synthetic(modelID: "Gemma-Wisp", levels: TestPlan.ladder)
        for level in TestPlan.ladder {
            let a = try pack.ids(level: level, variant: 0), b = try pack.ids(level: level, variant: 1)
            XCTAssertEqual(a.count, level)
            XCTAssertEqual(b.count, level)
            XCTAssertNotEqual(a, b, "the nonce differs per variant")
            XCTAssertEqual(try pack.ids(level: level, variant: 16), a)
        }
        XCTAssertThrowsError(try pack.ids(level: 777, variant: 0))
    }

    func testLoadChecksSchemaLanguageAndLengths() throws {
        var pack = PromptPack.synthetic(modelID: "Gemma-Wisp", levels: [512])
        let data = try JSONEncoder().encode(pack)
        let (loaded, sha) = try PromptPack.load(data)
        XCTAssertEqual(loaded, pack)
        XCTAssertEqual(sha, sha256Hex(data))
        pack.language = "es"
        XCTAssertThrowsError(try PromptPack.load(JSONEncoder().encode(pack)))
        pack.language = "en"
        pack.levels[0].body_ids = Array(pack.levels[0].body_ids.prefix(10))
        XCTAssertThrowsError(try PromptPack.load(JSONEncoder().encode(pack)))
    }
}

final class PromptBuilderTests: XCTestCase {
    func testBuildsExactLengthWithinTwoTokens() async throws {
        let env = try TestEnv()
        _ = try await env.generator.load(variant: env.wisp)
        let builder = env.deps.prompts
        for target in [512, 2_048, 10_240] {
            let built = try await builder.build(target: target, nonce: "abcdefgh", thinking: .on) {
                try await env.generator.countPromptTokens($0, tools: [], thinking: .on)
            }
            XCTAssertLessThanOrEqual(built.tokens, target)
            XCTAssertGreaterThanOrEqual(built.tokens, target - 2, "target \(target)")
            XCTAssertTrue(built.messages[0].content.hasPrefix("[run abcdefgh]"))
        }
    }
}

final class ModelTests: XCTestCase {
    func testFactsFromConfigAndSafetensorsHeaders() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("scan-\(UUID().uuidString)")
        let dir = try Fixtures.makeModelFolder(in: root)
        let variants = ModelScanner.scan(root: root)
        XCTAssertEqual(variants.map(\.id), [dir.lastPathComponent])
        let f = variants[0].facts
        XCTAssertEqual(f.quantBits, 4)
        XCTAssertEqual(f.quantMixed, ["5": 1, "6": 1])
        XCTAssertEqual(f.tensorBytes["text"], 1500)
        XCTAssertEqual(f.tensorBytes["mtp"], 50)
        XCTAssertTrue(f.hasMTPHead)
        XCTAssertEqual(f.architecture, "dense_ple")
        XCTAssertEqual(f.nativeWindow, 131_072)
    }

    func testCatalogRoles() {
        XCTAssertEqual(ModelCatalog.entry(folder: "gemma-4-E4B-it-r5-step-01800-oQ4e-mtp").id, "Gemma-Wisp")
        XCTAssertEqual(ModelCatalog.entry(folder: "gemma-4-E4B-it-r5-step-01800-oQ4e-mtp").role, .baseline)
        XCTAssertEqual(ModelCatalog.entry(folder: "gemma-4-E4B-it-oQ4e-mtp").role, .reference)
        let v = ModelCatalog.entry(folder: "Gemma-Wisp-q3-iphone")
        XCTAssertEqual(v.role, .variant)
        XCTAssertEqual(v.variantOf, "Gemma-Wisp")
        XCTAssertEqual(v.label, "Gemma-Wisp, q3 (iPhone fallback; shipped quant did not fit)")
        XCTAssertEqual(ModelCatalog.entry(folder: "gemma-4-E4B-it-q3-iphone").variantOf, "gemma-4-E4B-it-oQ4e-mtp")
        XCTAssertEqual(ModelCatalog.entry(folder: "Qwen3-4B").role, .candidate)
    }

    func testSHA256AndWeightsDigest() throws {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("sha-\(UUID().uuidString)")
        try Data("abc".utf8).write(to: url)
        XCTAssertEqual(try ModelScanner.sha256(of: url), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
        var v = ModelVariant(id: "x", directory: url, files: [ModelFile(name: "a.safetensors", bytes: 1)], facts: try TestEnv().wisp.facts)
        XCTAssertNil(v.weightsDigest)
        v.files[0].sha256 = "00"
        v.files.append(ModelFile(name: "b.json", bytes: 1, sha256: nil))
        XCTAssertNil(v.weightsDigest, "every file must be hashed")
        v.files[1].sha256 = "11"
        v.files.reverse()
        // The results store's formula (edat/results/convert.py weights_sha256), computed in Python.
        XCTAssertEqual(v.weightsDigest, "0a4b8c45f7124cf27382dbedc3cb65e8bceedb0a75f9c7a48615dcbf70feb7df")
    }
}

final class RecordTests: XCTestCase {
    func testSampleEncodesEveryFieldWithNulls() throws {
        let s = PerfSample(sample_id: "s0001", model_id: "Gemma-Wisp", mode: "plain", test: "level", phase: "measured",
                           order_index: 1, status: .ok, attempt: 1, device_model_id: "iPhone18,4", os: "iOS 27.1")
        let obj = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder.sorted.encode(s)) as? [String: Any])
        for key in ["cached_tokens", "t_prefill_done_ns", "token_times_ns", "footprint_peak_ledger_bytes", "kill_evidence",
                    "energy_window_id", "neural_bytes", "load", "shape", "error"] {
            XCTAssertTrue(obj.keys.contains(key), key)
            XCTAssertTrue(obj[key] is NSNull, key)
        }
        XCTAssertEqual(obj["status"] as? String, "ok")
        XCTAssertEqual(obj["thermal_not_nominal"] as? Bool, false)
        // Every §3.6 field is present.
        let spec = ["sample_id", "model_id", "mode", "test", "phase", "round", "rep", "order_index", "level_tokens", "prompt_tokens",
                    "cached_tokens", "variant", "shape", "step", "t_submit_ns", "t_prefill_done_ns", "t_first_token_ns", "t_last_token_ns",
                    "t_end_ns", "token_times_ns", "output_tokens", "reasoning_tokens", "drafted_tokens", "accepted_tokens", "load",
                    "footprint_start_bytes", "footprint_peak_bytes", "footprint_peak_ledger_bytes", "footprint_steady_bytes",
                    "footprint_end_bytes", "neural_bytes", "available_min_bytes", "swap_delta_bytes", "compressed_delta_bytes",
                    "mlx_active_bytes", "mlx_cache_bytes", "mlx_peak_bytes", "thermal_start", "thermal_end", "cooldown_wait_s",
                    "thermal_not_nominal", "memory_warning", "battery_level_start", "battery_level_end", "energy_window_id",
                    "status", "error", "kill_evidence"]
        XCTAssertEqual(Set(spec).subtracting(obj.keys), [])
        XCTAssertEqual(try JSONDecoder().decode(PerfSample.self, from: JSONEncoder.sorted.encode(s)), s)
        // Nested objects write their nulls too.
        let runner = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder.sorted.encode(
            RecordRunner(name: EDAT.runnerName, version: EDAT.runnerVersion, commit: nil))) as? [String: Any])
        XCTAssertTrue(runner["commit"] is NSNull)
        let balloon = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder.sorted.encode(
            RecordBalloon(runs: [], budget_bytes: nil))) as? [String: Any])
        XCTAssertTrue(balloon["budget_bytes"] is NSNull)
    }

    func testLargeNanosecondValuesStayIntegers() throws {
        var s = PerfSample(sample_id: "s", model_id: "m", mode: "plain", test: "level", phase: "measured",
                           order_index: 0, status: .ok, attempt: 1, device_model_id: "d", os: "o")
        s.t_submit_ns = 912_000_000_123_456_789
        s.footprint_peak_bytes = 5_650_000_123
        let text = String(decoding: try JSONEncoder.sorted.encode(s), as: UTF8.self)
        XCTAssertTrue(text.contains("\"t_submit_ns\":912000000123456789"), text)
        XCTAssertTrue(text.contains("\"footprint_peak_bytes\":5650000123"))
    }
}

final class QualityTests: XCTestCase {
    let today = DateComponents(year: 2026, month: 11, day: 18)   // a Wednesday

    func testMatchersFollowToolbench() {
        XCTAssertNil(Matchers.match(.string("Mom"), .string(" mom "), today: today))
        XCTAssertNil(Matchers.match(["icontains": "plant"], .string("Water the PLANTS"), today: today))
        XCTAssertNotNil(Matchers.match(["icontains": "plant"], .string("trash"), today: today))
        XCTAssertNil(Matchers.match(["regex": "^\\d{2}:\\d{2}$"], .string("08:30"), today: today))
        XCTAssertNil(Matchers.match(["date_offset": 1], .string("2026-11-19"), today: today))
        XCTAssertNil(Matchers.match(["weekday": "sunday"], .string("2026-11-22T10:00"), today: today))
        XCTAssertNotNil(Matchers.match(["weekday": "sunday"], .string("2026-11-15T10:00"), today: today), "past Sunday")
    }

    func testGemmaOutputParsing() throws {
        let raw = "<|channel>thought\nThe user wants a reminder.<channel|><|tool_call>call:add_reminder{title:<|\"|>water the plants<|\"|>,priority:2,tags:[<|\"|>home<|\"|>],flag:true}<tool_call|>"
        let p = GemmaOutput.parse(raw)
        XCTAssertEqual(p.reasoning, "The user wants a reminder.")
        XCTAssertEqual(p.answer, "")
        XCTAssertEqual(p.toolCalls.first?.name, "add_reminder")
        let args = try XCTUnwrap(p.toolCalls.first?.arguments)
        XCTAssertEqual(args["title"], .string("water the plants"))
        XCTAssertEqual(args["priority"], .number(2))
        XCTAssertEqual(GemmaOutput.parse("Hello there.<turn|>").answer, "Hello there.")
    }
}

final class StoreTests: XCTestCase {
    func testTornLastLineIsIgnoredAndNextUnitSkipsFinals() throws {
        let env = try TestEnv()
        let dir = env.store.directory("r1")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        try env.store.append(UnitDone(unit_index: 0, attempt: 1, final: true, status: "ok", sample_id: "s0000", model: 0, level: 512, during_load: false), to: "r1")
        try env.store.append(UnitDone(unit_index: 1, attempt: 1, final: false, status: "oom_killed", sample_id: "s0001", model: 0, level: 2_048, during_load: false), to: "r1")
        let h = try FileHandle(forWritingTo: dir.appendingPathComponent("units.jsonl"))
        try h.seekToEnd(); try h.write(contentsOf: Data("{\"unit_index\":2,\"fin".utf8)); try h.close()
        XCTAssertEqual(env.store.units("r1").count, 2)
        XCTAssertEqual(env.store.nextUnitIndex("r1"), 1, "a first kill is not final: the unit runs again")
    }

    func testMemTailReturnsTheLastTwoSecondsOfOneSample() throws {
        let env = try TestEnv()
        try FileManager.default.createDirectory(at: env.store.directory("r2"), withIntermediateDirectories: true)
        let log = MemLogWriter(url: env.store.memLogURL("r2"))
        for i in 0 ..< 100 { log.write(MemLine(t_ns: UInt64(i) * 50_000_000, footprint: UInt64(i), available: 10, sample_id: i < 90 ? "a" : "b"), now: UInt64(i) * 50_000_000) }
        log.flush(now: 0)
        let tail = env.store.memTail("r2", sampleID: "a")
        XCTAssertEqual(tail.last?.footprint, 89)
        XCTAssertEqual(tail.first?.footprint, 49)   // 2 s at 20 Hz before the last line, inclusive
    }
}
