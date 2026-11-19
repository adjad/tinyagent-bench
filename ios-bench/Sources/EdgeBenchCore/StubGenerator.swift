import Foundation

/// Thrown by the stub to imitate the OS killing the app mid-unit. The runner never
/// catches it, so nothing is written for the unit, exactly as after a real jetsam kill.
public struct SimulatedProcessDeath: Error, Sendable {
    public init() {}
}

/// Shared simulated memory state, read by `StubMemoryProbe`.
public final class StubMemory: @unchecked Sendable {
    private let lock = NSLock()
    public var baseMiB: Double
    public var budgetMiB: Double
    private var weightsMiB = 0.0
    private var kvMiB = 0.0
    private var transientMiB = 0.0
    private var lifetimePeakMiB = 0.0

    public init(baseMiB: Double = 120, budgetMiB: Double = 6_144) {
        self.baseMiB = baseMiB
        self.budgetMiB = budgetMiB
        lifetimePeakMiB = baseMiB
    }

    public func set(weights: Double? = nil, kv: Double? = nil, transient: Double? = nil) {
        lock.withLock {
            if let weights { weightsMiB = weights }
            if let kv { kvMiB = kv }
            if let transient { transientMiB = transient }
            lifetimePeakMiB = max(lifetimePeakMiB, baseMiB + weightsMiB + kvMiB + transientMiB)
        }
    }

    public var footprintMiB: Double { lock.withLock { baseMiB + weightsMiB + kvMiB + transientMiB } }
    public var peakMiB: Double { lock.withLock { lifetimePeakMiB } }
}

public struct StubMemoryProbe: MemoryProbe {
    public let memory: StubMemory
    public init(memory: StubMemory) { self.memory = memory }
    public func read() -> MemoryReading {
        let fp = memory.footprintMiB
        return MemoryReading(
            footprintBytes: UInt64(fp * 1_048_576),
            lifetimePeakBytes: UInt64(memory.peakMiB * 1_048_576),
            availableBytes: UInt64(max(0, memory.budgetMiB - fp) * 1_048_576),
            residentBytes: UInt64(fp * 1_048_576))
    }
}

/// A deterministic stand-in for a model. Time passes on the injected clock (a manual clock
/// in tests, the real one in the Simulator), tokens are counted at ~4 characters each, and
/// memory follows weights + KV slope + a prefill transient. Tool calls come back in Gemma 4
/// markup so the same parser runs as on the phone.
public final class StubGenerator: BenchGenerator, @unchecked Sendable {
    public struct Profile: Sendable {
        public var loadSeconds = 1.2
        public var coldExtraSeconds = 2.0
        public var prefillTPS = 900.0
        public var decodeTPS = 40.0
        public var weightsMiB = 4_400.0
        public var kvMiBPer1k = 18.0
        public var prefillTransientMiBPer1k = 6.0
        public var templateOverheadTokens = 14
        public var charsPerToken = 4.0
        /// Simulate an OS kill when the footprint would pass the budget.
        public var killAtBudget = true
        public var nativeWindow = 131_072
        public init() {}
    }

    public let profile: Profile
    public let clock: BenchClock
    public let memory: StubMemory
    private let lock = NSLock()
    private var loaded = false
    private var loads = 0
    /// Unit test hook: throw `SimulatedProcessDeath` on the nth generate call (0-based).
    public var dieOnGenerateCall: Int?
    private var generateCalls = 0

    public init(profile: Profile = .init(), clock: BenchClock, memory: StubMemory) {
        self.profile = profile
        self.clock = clock
        self.memory = memory
    }

    public var runtimeInfo: RuntimeInfo {
        RuntimeInfo(name: "stub", version: "1", backend: "none", kvDtype: nil, prefillStep: 512,
                    speculation: "none", cacheLimitBytes: EDAT.mlxCacheLimitBytes, toolFormat: "gemma4-core-parser",
                    notes: ["Stub generator: times and memory are simulated, not measured."])
    }

    public var isLoaded: Bool { lock.withLock { loaded } }

    public func load(variant: ModelVariant) async throws -> LoadReport {
        let first = lock.withLock { () -> Bool in loads += 1; return loads == 1 }
        try await clock.sleep(nanos: UInt64((profile.loadSeconds + (first ? profile.coldExtraSeconds : 0)) * 1e9))
        memory.set(weights: profile.weightsMiB, kv: 0, transient: 0)
        if profile.killAtBudget, memory.baseMiB + profile.weightsMiB > memory.budgetMiB { throw SimulatedProcessDeath() }
        lock.withLock { loaded = true }
        return LoadReport(notes: ["stub"], speculationAvailable: false)
    }

    public func unload() async {
        memory.set(weights: 0, kv: 0, transient: 0)
        lock.withLock { loaded = false }
    }

    func tokens(_ text: String) -> Int { Int((Double(text.count) / profile.charsPerToken).rounded(.up)) }

    public func countPromptTokens(_ messages: [BenchMessage], tools: [JSONValue], thinking: ThinkingMode) async throws -> Int {
        let toolChars = tools.isEmpty ? 0 : ((try? JSONEncoder.sorted.encode(tools))?.count ?? 0)
        return messages.reduce(profile.templateOverheadTokens) { $0 + 4 + tokens($1.content) }
            + Int(Double(toolChars) / profile.charsPerToken) + (thinking == .on ? 3 : 0)
    }

    public func countTextTokens(_ text: String) async throws -> Int { tokens(text) }

    public func generate(_ request: GenerationRequest, hooks: GenerationHooks) async throws -> GenerationResult {
        guard isLoaded else { throw GeneratorError.notLoaded }
        let call = lock.withLock { () -> Int in defer { generateCalls += 1 }; return generateCalls }
        if dieOnGenerateCall == call { throw SimulatedProcessDeath() }
        let prompt: Int
        if let ids = request.promptIDs { prompt = ids.count }
        else if let r = request.renderedPrompt { prompt = tokens(r) }
        else { prompt = try await countPromptTokens(request.messages, tools: request.tools, thinking: request.thinking) }
        let kv = Double(prompt) / 1000 * profile.kvMiBPer1k
        let transient = Double(prompt) / 1000 * profile.prefillTransientMiBPer1k
        if profile.killAtBudget, memory.baseMiB + profile.weightsMiB + kv + transient > memory.budgetMiB {
            memory.set(kv: kv, transient: transient)
            throw SimulatedProcessDeath()
        }
        // Prefill in 512-token chunks so the memory sampler sees it.
        var done = 0
        while done < prompt {
            try Task.checkCancellation()
            let chunk = min(512, prompt - done)
            try await clock.sleep(nanos: UInt64(Double(chunk) / profile.prefillTPS * 1e9))
            done += chunk
            memory.set(kv: Double(done) / 1000 * profile.kvMiBPer1k, transient: transient)
        }
        memory.set(transient: 0)
        hooks.onPrefillDone()
        let reply = Self.reply(for: request)
        let pieces = Self.pieces(reply)
        let n = request.ignoreEOS ? request.maxTokens : min(request.maxTokens, pieces.count)
        var classifier = GemmaTokenClassifier()
        var out = ""
        for i in 0 ..< n {
            if Task.isCancelled { break }
            // Like the MLX engine: cancellation during decode returns what was generated.
            if i > 0 { try? await clock.sleep(nanos: UInt64(1e9 / profile.decodeTPS)) }
            if Task.isCancelled { break }
            let piece = i < pieces.count ? pieces[i] : " x"
            out += piece
            hooks.onToken(classifier.classify(piece: piece))
            memory.set(kv: Double(prompt + i + 1) / 1000 * profile.kvMiBPer1k)
        }
        let parsed = GemmaOutput.parse(out)
        var result = GenerationResult(
            promptTokens: prompt, completionTokens: n, text: parsed.answer, reasoning: parsed.reasoning,
            toolCalls: parsed.toolCalls, finishReason: Task.isCancelled ? "cancelled" : (n < request.maxTokens ? "stop" : "length"),
            engine: EngineTimings(promptSeconds: Double(prompt) / profile.prefillTPS, generateSeconds: Double(max(0, n - 1)) / profile.decodeTPS,
                                  convention: "stub"))
        result.rawText = out
        let mib = UInt64(1_048_576)
        result.mlxActiveBytes = UInt64(profile.weightsMiB + Double(prompt + n) / 1000 * profile.kvMiBPer1k) * mib
        result.mlxCacheBytes = 64 * mib
        result.mlxPeakBytes = (result.mlxActiveBytes ?? 0) + UInt64(transient) * mib
        return result
    }

    /// Splits markup tags into their own pieces, the rest into ~4-character pieces.
    static func pieces(_ s: String) -> [String] {
        let tags = [GemmaOutput.channelOpen, GemmaOutput.channelClose, GemmaOutput.callOpen, GemmaOutput.callClose, GemmaOutput.quote]
        var out = [String]()
        var rest = Substring(s)
        while !rest.isEmpty {
            if let tag = tags.first(where: { rest.hasPrefix($0) }) {
                out.append(tag); rest = rest.dropFirst(tag.count); continue
            }
            var piece = ""
            while !rest.isEmpty, piece.count < 4, !tags.contains(where: { rest.hasPrefix($0) }) {
                piece.append(rest.removeFirst())
            }
            out.append(piece)
        }
        return out
    }

    /// A canned reply: picks the tool whose name shares most words with the last user message.
    static func reply(for request: GenerationRequest) -> String {
        let thought = request.thinking == .on ? "\(GemmaOutput.channelOpen)thought\nStub reasoning.\(GemmaOutput.channelClose)" : ""
        let user = request.messages.last(where: { $0.role == "user" })?.content.lowercased() ?? ""
        let words = Set(user.split(whereSeparator: { !$0.isLetter }).map(String.init))
        let names = request.tools.compactMap { $0["function"]?["name"]?.stringValue }
        let best = names.map { name -> (String, Int) in
            let parts = name.lowercased().split(separator: "_").map(String.init)
            return (name, parts.filter { words.contains($0) || words.contains($0 + "s") }.count)
        }.max { $0.1 < $1.1 }
        if let best, best.1 > 0 {
            return thought + "\(GemmaOutput.callOpen)call:\(best.0){}\(GemmaOutput.callClose)"
        }
        if user.contains("json") { return thought + "{\"stub\": true}" }
        return thought + "This is a stub answer about the material."
    }
}

/// Simulated `MTLBuffer` balloon (spec 04 §2.9): grows the stub's memory until the budget,
/// then "dies" like a jetsam kill.
public final class StubBalloonAllocator: BalloonAllocator, @unchecked Sendable {
    public let memory: StubMemory
    private let lock = NSLock()
    private var allocatedMiB = 0.0
    public init(memory: StubMemory) { self.memory = memory }
    public func allocate(bytes: Int) throws -> Bool {
        let total = lock.withLock { () -> Double in allocatedMiB += Double(bytes) / 1_048_576; return allocatedMiB }
        memory.set(transient: total)
        if memory.footprintMiB > memory.budgetMiB { throw SimulatedProcessDeath() }
        return true
    }
    public func releaseAll() {
        lock.withLock { allocatedMiB = 0 }
        memory.set(transient: 0)
    }
}
