import Foundation
#if canImport(EdgeBenchMLX)
import EdgeBenchMLX

/// The measurement core's engine boundary on a real iPhone: MLX Swift (mlx-swift-lm 3.32.3),
/// text-only, no speculation. Tool calls come from mlx-swift-lm's own `gemma4` parser with
/// recovery disabled; reasoning and answer text from the core's Gemma 4 rules.
final class MLXGenerator: BenchGenerator, @unchecked Sendable {
    let engine = MLXBenchEngine()
    private let lock = NSLock()
    private var notes: [String] = []

    var runtimeInfo: RuntimeInfo {
        RuntimeInfo(name: "mlx-swift", version: MLXBenchEngine.mlxSwiftVersion, commit: nil,
                    packages: ["mlx-swift": MLXBenchEngine.mlxSwiftVersion, "mlx-swift-lm": MLXBenchEngine.mlxSwiftLMVersion,
                               "swift-transformers": "1.3.5"],
                    backend: "metal", kvDtype: "bf16", prefillStep: engine.prefillStepSize,
                    speculation: "none", cacheLimitBytes: UInt64(engine.gpuCacheLimitBytes),
                    toolFormat: "gemma4", toolRecovery: "disabled",
                    notes: lock.withLock { notes } + ["MTP head not used: mlx-swift-lm 3.32.3 needs the VLM model class and a separate drafter folder for it (design doc §2)."])
    }

    var isLoaded: Bool { engine.isLoaded }

    func load(variant: ModelVariant) async throws -> LoadReport {
        let n = try await engine.load(directory: variant.directory)
        lock.withLock { notes = n }
        return LoadReport(notes: n, speculationAvailable: false)
    }

    func unload() async { engine.unload() }

    static func sendable(_ v: JSONValue) -> any Sendable {
        switch v {
        case .null: return Optional<String>.none as any Sendable
        case .bool(let b): return b
        case .number(let n): return n.rounded() == n && abs(n) < 1e15 ? Int(n) : n
        case .string(let s): return s
        case .array(let a): return a.map { sendable($0) }
        case .object(let o): return o.mapValues { sendable($0) }
        }
    }

    /// Messages in the shape the Gemma 4 chat template reads. Earlier tool calls carry their
    /// arguments as an object (the template iterates them), parsed from the OpenAI JSON string.
    static func templateMessages(_ messages: [BenchMessage]) -> [[String: any Sendable]] {
        messages.map { m in
            var d: [String: any Sendable] = ["role": m.role, "content": m.content]
            if let calls = m.toolCalls, !calls.isEmpty {
                d["tool_calls"] = calls.map { call -> [String: any Sendable] in
                    var fn = call["function"]?.objectValue ?? [:]
                    if case .string(let raw)? = fn["arguments"], let data = raw.data(using: .utf8),
                       let obj = try? JSONSerialization.jsonObject(with: data) {
                        fn["arguments"] = JSONValue.from(any: obj)
                    }
                    var out: [String: any Sendable] = ["type": "function", "function": sendable(.object(fn))]
                    if let id = call["id"]?.stringValue { out["id"] = id }
                    return out
                }
            }
            if let id = m.toolCallID { d["tool_call_id"] = id }
            return d
        }
    }

    static func templateTools(_ tools: [JSONValue]) -> [[String: any Sendable]]? {
        tools.isEmpty ? nil : tools.compactMap { sendable($0) as? [String: any Sendable] }
    }

    func tokens(_ messages: [BenchMessage], tools: [JSONValue], thinking: ThinkingMode) throws -> [Int] {
        try engine.promptTokens(messages: Self.templateMessages(messages), tools: Self.templateTools(tools), enableThinking: thinking == .on)
    }

    func countPromptTokens(_ messages: [BenchMessage], tools: [JSONValue], thinking: ThinkingMode) async throws -> Int {
        try tokens(messages, tools: tools, thinking: thinking).count
    }

    func countTextTokens(_ text: String) async throws -> Int { try engine.countTextTokens(text) }

    func generate(_ request: GenerationRequest, hooks: GenerationHooks) async throws -> GenerationResult {
        let prompt: [Int]
        if let ids = request.promptIDs { prompt = ids }
        else if let rendered = request.renderedPrompt { prompt = try engine.encodeRendered(rendered) }
        else { prompt = try tokens(request.messages, tools: request.tools, thinking: request.thinking) }
        var classifier = GemmaTokenClassifier()
        var reasoning = 0
        let s = request.sampling
        let g = try engine.generate(
            promptTokens: prompt, maxTokens: request.maxTokens, temperature: Float(s.temperature),
            topP: Float(s.topP ?? 1.0), topK: s.topK ?? 0, seed: s.seed, ignoreEOS: request.ignoreEOS,
            tools: Self.templateTools(request.tools), onPrefillDone: hooks.onPrefillDone
        ) { _, piece in
            let kind = classifier.classify(piece: piece)
            if kind == .reasoning { reasoning += 1 }
            hooks.onToken(kind)
        }
        let parsed = GemmaOutput.parse(g.rawText)
        var result = GenerationResult(
            promptTokens: g.promptTokens, completionTokens: g.tokenIds.count, reasoningTokens: reasoning,
            text: parsed.answer, reasoning: parsed.reasoning,
            toolCalls: g.toolCalls.map { ParsedToolCall(name: $0.name, argumentsRaw: $0.argumentsJSON) },
            finishReason: g.cancelled ? "cancelled" : (g.stoppedOnEOS ? "stop" : "length"),
            engine: EngineTimings(promptSeconds: g.promptSeconds, generateSeconds: g.generateSeconds,
                                  convention: "mlx-swift-lm TokenIterator.promptPrefillTime"))
        result.rawText = g.rawText
        result.rejectedCalls = g.rejectedCalls.compactMap { s in
            s.data(using: .utf8).flatMap { try? JSONSerialization.jsonObject(with: $0) }.map { JSONValue.from(any: $0) }
        }
        result.mlxActiveBytes = UInt64(max(0, g.mlxActiveBytes))
        result.mlxCacheBytes = UInt64(max(0, g.mlxCacheBytes))
        result.mlxPeakBytes = UInt64(max(0, g.mlxPeakBytes))
        return result
    }
}
#endif
