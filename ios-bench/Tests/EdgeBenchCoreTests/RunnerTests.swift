import Foundation
import XCTest
@testable import EdgeBenchCore

final class RunnerTests: XCTestCase {
    func recordJSON(_ env: TestEnv, _ id: String) throws -> [String: Any] {
        let data = try RunExporter.recordData(manifest: env.store.manifest(id), store: env.store)
        return try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
    }

    // MARK: end to end

    func testSmokeRunExportsAnEdatPerfRecord() async throws {
        let env = try TestEnv()
        let smoke = TestPlan.builtIn.first { $0.id == "smoke" }!
        let runner = env.runner()
        let id = try await runner.create(plan: smoke, models: [.primary: env.wisp], device: env.device, seed: 7)
        try await runner.run(id)
        XCTAssertEqual(try env.store.manifest(id).state, .complete)

        let samples = env.store.samples(id)
        XCTAssertEqual(samples.count, 2 + 2 + 6)   // 2 warm loads, 2 warm-ups, 2 levels × 3 reps
        XCTAssertTrue(samples.allSatisfy { $0.status == .ok }, samples.map(\.status.rawValue).description)
        let levels = samples.filter { $0.test == "level" }
        XCTAssertEqual(levels.map { "\($0.phase.prefix(1))\($0.level_tokens!)" },
                       ["w512", "w2048", "m512", "m2048", "m2048", "m512", "m512", "m2048"])
        for s in levels {
            XCTAssertEqual(s.prompt_tokens, s.level_tokens, "pack ids are fed as is")
            XCTAssertEqual(s.output_tokens, 256)
            XCTAssertEqual(s.token_times_ns?.count, 255)
            XCTAssertNotNil(s.t_prefill_done_ns)
            XCTAssertGreaterThanOrEqual(s.t_first_token_ns!, s.t_prefill_done_ns!)
            XCTAssertGreaterThan(s.footprint_peak_bytes!, 4_000 << 20)
            XCTAssertNotNil(s.cooldown_wait_s)
            XCTAssertEqual(s.device_model_id, "Test1,1")
            XCTAssertEqual(s.power_source, "battery")
            XCTAssertNotNil(s.mlx_active_bytes)
        }
        XCTAssertEqual(Set(levels.filter { $0.phase == "measured" && $0.level_tokens == 512 }.map(\.variant)), [0, 1, 2])
        let load = try XCTUnwrap(samples.first { $0.test == "load_warm" }?.load)
        XCTAssertGreaterThan(load.t_ready_ns!, load.t_load_start_ns)
        XCTAssertNotNil(load.t_first_token_possible_ns)

        let phone = env.store.phoneResults(id)
        XCTAssertEqual(phone.count, 6)
        XCTAssertTrue(phone.allSatisfy { $0.schema == "edat-phone-result/v1" && $0.mode == "messages" && $0.status == "ok" })
        XCTAssertTrue(phone.allSatisfy { !$0.rawText.isEmpty })

        let rec = try recordJSON(env, id)
        XCTAssertEqual(rec["schema"] as? String, "edat.perf/1.0")
        XCTAssertEqual(rec["kind"] as? String, "local")
        XCTAssertEqual(rec["clock"] as? String, "mach_continuous_time")
        XCTAssertEqual((rec["runner"] as? [String: Any])?["name"] as? String, "iphone-mlx-swift")
        XCTAssertEqual((rec["energy_windows"] as? [Any])?.count, 0)
        XCTAssertNil(rec["cells"], "cells are the aggregator's")
        let models = try XCTUnwrap(rec["models"] as? [[String: Any]])
        XCTAssertEqual(models.first?["id"] as? String, "Gemma-Wisp")
        XCTAssertEqual(models.first?["role"] as? String, "baseline")
        XCTAssertEqual(models.first?["status"] as? String, "ok")
        let pack = try XCTUnwrap((rec["prompt_pack"] as? [String: Any])?["Gemma-Wisp"] as? [String: Any])
        XCTAssertEqual(pack["language"] as? String, "en")
        XCTAssertEqual(pack["source"] as? String, "synthetic_stub")
        let runtime = try XCTUnwrap(rec["runtime"] as? [String: Any])
        XCTAssertEqual(runtime["mlx_cache_limit_bytes"] as? Int, 256 << 20)
        XCTAssertEqual(runtime["prefix_cache"] as? String, "none")
        let attachments = try XCTUnwrap(rec["attachments"] as? [[String: Any]])
        XCTAssertEqual(Set(attachments.compactMap { $0["kind"] as? String }), ["footprint_log", "phone_results"])

        // The footprint log has lines for every sample.
        let mem = env.store.readLines(env.store.memLogURL(id)).compactMap { try? JSONDecoder().decode(MemLine.self, from: $0) }
        XCTAssertEqual(Set(mem.map(\.sample_id)).isSuperset(of: Set(levels.map(\.sample_id))), true)

        // The zip export.
        let out = env.root.appendingPathComponent("Exports")
        try FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)
        let zip = try RunExporter.export(runID: id, store: env.store, to: out)
        XCTAssertEqual(zip.lastPathComponent, "tinyagent-bench-\(id).zip")
        XCTAssertGreaterThan((try zip.resourceValues(forKeys: [.fileSizeKey])).fileSize ?? 0, 1_000)
        let folder = out.appendingPathComponent("tinyagent-bench-\(id)")
        let manifest = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: folder.appendingPathComponent("manifest-\(id).json"))) as? [String: Any])
        let files = try XCTUnwrap(manifest["files"] as? [[String: Any]])
        XCTAssertEqual(Set(files.compactMap { $0["name"] as? String }), ["run-\(id).json", "mem-\(id).jsonl", "phone-\(id).jsonl"])
        for f in files {
            let data = try Data(contentsOf: folder.appendingPathComponent(f["name"] as! String))
            XCTAssertEqual(f["sha256"] as? String, sha256Hex(data))
        }
    }

    func testBracketRunsWispThenBaseThenWisp() async throws {
        let env = try TestEnv()
        let plan = env.plan([PlanStep(.primary, .levels([512], reps: 1)), PlanStep(.reference, .levels([512], reps: 1)),
                             PlanStep(.reference, .levels([512], reps: 1)), PlanStep(.primary, .levels([512], reps: 1))])
        let runner = env.runner()
        let id = try await runner.create(plan: plan, models: [.primary: env.wisp, .reference: env.base], device: env.device, seed: 1)
        try await runner.run(id)
        XCTAssertEqual(env.store.samples(id).map(\.model_id),
                       ["Gemma-Wisp", "Gemma-Wisp", "gemma-4-E4B-it-oQ4e-mtp", "gemma-4-E4B-it-oQ4e-mtp",
                        "gemma-4-E4B-it-oQ4e-mtp", "gemma-4-E4B-it-oQ4e-mtp", "Gemma-Wisp", "Gemma-Wisp"])
        XCTAssertEqual(env.store.events(id).filter { $0.kind == "model_loaded" }.count, 3)
        let models = try XCTUnwrap(try recordJSON(env, id)["models"] as? [[String: Any]])
        XCTAssertEqual(models.compactMap { $0["role"] as? String }, ["baseline", "reference"])
    }

    func testCreateRefusesAPlanWithAnEmptySlot() async throws {
        let env = try TestEnv()
        let bracket = TestPlan.builtIn.first { $0.id == "bracket" }!
        do {
            _ = try await env.runner().create(plan: bracket, models: [.primary: env.wisp], device: env.device)
            XCTFail("expected slotEmpty")
        } catch RunnerError.slotEmpty(let s) { XCTAssertEqual(s, .reference) }
    }

    // MARK: kills (spec 04 §2.8)

    func testKillIsRetriedOnceThenHigherLevelsAreNotRun() async throws {
        // 65,536 tokens push the stub past a 5,600 MiB budget: a simulated jetsam kill.
        let env = try TestEnv(budgetMiB: 5_600)
        let plan = env.plan([PlanStep(.primary, .levels([512, 2_048, 65_536], reps: 2))])
        let id = try await env.runner().create(plan: plan, models: [.primary: env.wisp], device: env.device, seed: 3)
        let kills = try await env.runThroughKills(id)
        XCTAssertEqual(kills, 2, "killed, retried once, killed again")
        XCTAssertEqual(try env.store.manifest(id).state, .complete)
        let samples = env.store.samples(id)
        let killed = samples.filter { $0.status.isKill }
        XCTAssertEqual(killed.map(\.sample_id), ["s0002", "s0002-2"])
        XCTAssertEqual(killed.map(\.attempt), [1, 2])
        XCTAssertTrue(killed.allSatisfy { $0.status == .oom_killed && $0.level_tokens == 65_536 })
        let evidence = try XCTUnwrap(killed.first?.kill_evidence)
        XCTAssertFalse(evidence.tail.isEmpty)
        XCTAssertLessThan(evidence.last_available_bytes ?? .max, EDAT.oomAvailableThresholdBytes)
        XCTAssertEqual(evidence.phase, "generate")
        let notRun = samples.filter { $0.status == .not_run_above_oom }
        XCTAssertEqual(notRun.map(\.level_tokens), [65_536, 65_536], "both measured reps at 65,536")
        XCTAssertEqual(samples.filter { $0.status == .ok }.count, 2 + 4)
        XCTAssertEqual(env.store.events(id).filter { $0.kind == "relaunch_after_kill" }.count, 2)
    }

    func testKillWithoutMemoryEvidenceIsKilledUnknown() async throws {
        let env = try TestEnv()
        let plan = env.plan([PlanStep(.primary, .levels([512], reps: 1))])
        let id = try await env.runner().create(plan: plan, models: [.primary: env.wisp], device: env.device, seed: 4)
        env.generator.dieOnGenerateCall = 1   // the first measured sample (call 0 is the warm-up)
        _ = try await env.runThroughKills(id)
        let samples = env.store.samples(id)
        XCTAssertEqual(samples.map(\.status), [.ok, .killed_unknown, .ok], "killed once, then the retry ran")
        XCTAssertEqual(samples.last?.attempt, 2)
        XCTAssertEqual(samples[1].sample_id, "s0001")
        XCTAssertEqual(samples[2].sample_id, "s0001-2")
    }

    func testKillAtLoadStopsTheModel() async throws {
        let env = try TestEnv(budgetMiB: 4_000)   // the weights alone pass the budget
        let plan = env.plan([PlanStep(.primary, .levels([512, 2_048], reps: 1))])
        let id = try await env.runner().create(plan: plan, models: [.primary: env.wisp], device: env.device, seed: 5)
        let kills = try await env.runThroughKills(id)
        XCTAssertEqual(kills, 2)
        let samples = env.store.samples(id)
        XCTAssertEqual(samples.prefix(2).map(\.status), [.oom_killed_at_load, .oom_killed_at_load])
        XCTAssertEqual(samples.prefix(2).map { $0.kill_evidence?.phase }, ["load", "load"])
        XCTAssertTrue(samples.dropFirst(2).allSatisfy { $0.status == .not_run_above_oom })
        XCTAssertEqual(samples.count, 2 + 3)
        let models = try XCTUnwrap(try recordJSON(env, id)["models"] as? [[String: Any]])
        XCTAssertEqual(models.first?["status"] as? String, "oom_killed_at_load")
    }

    func testBackgroundedSampleIsVoidedAndRunAgain() async throws {
        let env = try TestEnv()
        let plan = env.plan([PlanStep(.primary, .levels([512], reps: 1))])
        let id = try await env.runner().create(plan: plan, models: [.primary: env.wisp], device: env.device, seed: 6)
        // The app left the foreground during sample 0 and iOS then ended it.
        try env.store.updateManifest(id) { $0.state = .running }
        try env.store.writeInflight(Inflight(run_id: id, sample_id: "s0000", unit_index: 0, attempt: 1, model: 0, model_id: "Gemma-Wisp",
                                             mode: "plain", test: "level", level: 512, phase: "generate", sample_phase: "warmup",
                                             round: 0, rep: 0, variant: 0, step: nil, t_start_ns: 1, footprint_start: 1,
                                             available_start: 1, item_id: nil, trial: nil, item_mode: nil, backgrounded: false))
        env.store.markBackgrounded(id)
        env.store.recoverInterruptedRuns(clock: env.clock, deviceModel: "Test1,1", os: "test")
        XCTAssertEqual(env.store.nextUnitIndex(id), 0, "a voided sample is run again")
        try await env.runner().run(id)
        XCTAssertEqual(env.store.samples(id).map(\.status), [.voided_backgrounded, .ok, .ok])
    }

    // MARK: conditions

    func testCooldownGateBeforeEverySample() async throws {
        let env = try TestEnv()
        env.conditions.set { $0.hotUntilNanos = env.clock.nowNanos() + 12_000_000_000 }
        let plan = env.plan([PlanStep(.primary, .levels([512], reps: 1))])
        let runner = env.runner()
        let id = try await runner.create(plan: plan, models: [.primary: env.wisp], device: env.device, seed: 8)
        try await runner.run(id)
        let s = env.store.samples(id)
        XCTAssertEqual(s[0].cooldown_wait_s ?? 0, 15, accuracy: 0.01, "polled every 5 s until nominal")
        XCTAssertFalse(s[0].thermal_not_nominal)
        XCTAssertEqual(s[1].cooldown_wait_s ?? -1, 0, accuracy: 0.01)
    }

    func testStillHotAfterFiveMinutesRunsAnywayAndFlagsIt() async throws {
        let env = try TestEnv()
        env.conditions.set { $0.hotUntilNanos = .max }
        let plan = env.plan([PlanStep(.primary, .levels([512], reps: 1))])
        let runner = env.runner()
        let id = try await runner.create(plan: plan, models: [.primary: env.wisp], device: env.device, seed: 9)
        try await runner.run(id)
        let s = env.store.samples(id)
        XCTAssertTrue(s.allSatisfy { $0.thermal_not_nominal && $0.status == .ok })
        XCTAssertEqual(s[0].cooldown_wait_s ?? 0, 300, accuracy: 0.01)
        XCTAssertEqual(env.store.events(id).filter { $0.kind == "cooldown_timeout" }.count, 2)
    }

    func testBatteryFloorPausesTheRun() async throws {
        let env = try TestEnv()
        env.conditions.set { $0.battery = 25 }
        let plan = env.plan([PlanStep(.primary, .levels([512], reps: 1))])
        let runner = env.runner()
        let id = try await runner.create(plan: plan, models: [.primary: env.wisp], device: env.device, seed: 10)
        try await runner.run(id)
        let m = try env.store.manifest(id)
        XCTAssertEqual(m.state, .paused)
        XCTAssertTrue(m.stopReason?.contains("battery") == true)
        XCTAssertTrue(env.store.samples(id).isEmpty)
    }

    func testPerSampleTimeout() async throws {
        var profile = StubGenerator.Profile()
        profile.decodeTPS = 1
        let env = try TestEnv(profile: profile)
        let plan = env.plan([PlanStep(.primary, .levels([512], reps: 1))]) { $0.cellTimeoutSeconds = 30 }
        let runner = env.runner()
        let id = try await runner.create(plan: plan, models: [.primary: env.wisp], device: env.device, seed: 11)
        try await runner.run(id)
        let s = env.store.samples(id)
        XCTAssertEqual(s.map(\.status), [.timeout, .timeout])
        XCTAssertLessThan(s[0].output_tokens ?? 999, 40)
    }

    func testCancelPausesAndResumeFinishes() async throws {
        let env = try TestEnv()
        let plan = env.plan([PlanStep(.primary, .levels([512, 2_048], reps: 2))])
        let id = try await env.runner().create(plan: plan, models: [.primary: env.wisp], device: env.device, seed: 12)
        let box = RunTaskBox()
        let runner = env.runner { p in if p.unitIndex >= 3 { box.cancel() } }
        let task = Task { try await runner.run(id) }
        box.set(task)
        try await task.value
        XCTAssertEqual(try env.store.manifest(id).state, .paused)
        let partial = env.store.samples(id).count
        XCTAssertLessThan(partial, 6)
        try await env.runner().run(id)
        XCTAssertEqual(try env.store.manifest(id).state, .complete)
        XCTAssertEqual(env.store.samples(id).filter { $0.status == .ok }.count, 6)
        XCTAssertEqual(Set(env.store.samples(id).map(\.sample_id)).count, env.store.samples(id).count, "sample ids are unique")
    }

    // MARK: phone items and the balloon

    func testPhoneItemsWriteResultLinesForBothModes() async throws {
        let env = try TestEnv()
        let plan = env.plan([PlanStep(.primary, .phoneItems(trials: 2, mode: "messages", limit: 3)),
                             PlanStep(.primary, .phoneItems(trials: 1, mode: "rendered", limit: 2))])
        let runner = env.runner()
        let id = try await runner.create(plan: plan, models: [.primary: env.wisp], device: env.device, seed: 13)
        try await runner.run(id)
        let results = env.store.phoneResults(id)
        XCTAssertEqual(results.count, 3 * 2 + 2)
        XCTAssertEqual(results.filter { $0.mode == "messages" }.map(\.trial), [0, 0, 0, 1, 1, 1])
        XCTAssertTrue(results.filter { $0.mode == "messages" }.allSatisfy { $0.status == "ok" && $0.runtime.recovery == "disabled" })
        // The stand-in items carry no Mac-rendered prompt, so rendered mode records an error per item.
        XCTAssertTrue(results.filter { $0.mode == "rendered" }.allSatisfy { $0.status == "error" })
        XCTAssertTrue(env.store.samples(id).isEmpty, "phone items are not perf samples")
        let line = try XCTUnwrap(env.store.readLines(env.store.phoneResultsURL(id)).first)
        let obj = try XCTUnwrap(JSONSerialization.jsonObject(with: line) as? [String: Any])
        for key in ["schema", "id", "trial", "mode", "device", "runtime", "weights_sha256", "raw_text", "reasoning_text",
                    "parsed_calls", "rejected_calls", "finish_reason", "prompt_tokens", "completion_tokens", "ttft_s", "total_s",
                    "local_pass", "local_labels"] {
            XCTAssertTrue(obj.keys.contains(key), key)
        }
        XCTAssertEqual(obj["device"] as? String, "Test Phone 12 GB")
    }

    func testBalloonRunsUntilKilledThreeTimes() async throws {
        let env = try TestEnv(budgetMiB: 2_000)
        let plan = env.plan([PlanStep(nil, .balloon(runs: 3))])
        let id = try await env.runner().create(plan: plan, models: [:], device: env.device, seed: 14)
        let kills = try await env.runThroughKills(id)
        XCTAssertEqual(kills, 3)
        let rec = try recordJSON(env, id)
        let balloon = try XCTUnwrap(rec["balloon"] as? [String: Any])
        let runs = try XCTUnwrap(balloon["runs"] as? [[String: Any]])
        XCTAssertEqual(runs.count, 3)
        XCTAssertTrue(runs.allSatisfy { $0["killed"] as? Bool == true })
        let budget = try XCTUnwrap(balloon["budget_bytes"] as? Int)
        XCTAssertGreaterThan(budget, 1_900 << 20)
        XCTAssertLessThanOrEqual(budget, 2_000 << 20)
    }
}

/// Lets an onProgress callback cancel the task that is running the runner.
final class RunTaskBox: @unchecked Sendable {
    private let lock = NSLock()
    private var task: Task<Void, Error>?
    private var pending = false
    func set(_ t: Task<Void, Error>) {
        let c = lock.withLock { task = t; return pending }
        if c { t.cancel() }
    }
    func cancel() {
        let t = lock.withLock { pending = true; return task }
        t?.cancel()
    }
}
