// MLXBenchEngine: loads an oMLX-layout model folder text-only with mlx-swift-lm and runs
// one generation at a time with a callback per token, so the measurement core can stamp
// every token on its own clock. No network: tokenizer and weights come from the folder.

import Foundation
import MLX
import MLXLLM
import MLXLMCommon
import Tokenizers

// MARK: - Tokenizer bridge (what mlx-swift-lm's #adaptHuggingFaceTokenizer macro expands to)

struct TokenizerBridge: MLXLMCommon.Tokenizer {
    let upstream: any Tokenizers.Tokenizer

    func encode(text: String, addSpecialTokens: Bool) -> [Int] {
        upstream.encode(text: text, addSpecialTokens: addSpecialTokens)
    }
    func decode(tokenIds: [Int], skipSpecialTokens: Bool) -> String {
        upstream.decode(tokens: tokenIds, skipSpecialTokens: skipSpecialTokens)
    }
    func convertTokenToId(_ token: String) -> Int? { upstream.convertTokenToId(token) }
    func convertIdToToken(_ id: Int) -> String? { upstream.convertIdToToken(id) }
    var bosToken: String? { upstream.bosToken }
    var eosToken: String? { upstream.eosToken }
    var unknownToken: String? { upstream.unknownToken }

    func applyChatTemplate(messages: [[String: any Sendable]], tools: [[String: any Sendable]]?,
                           additionalContext: [String: any Sendable]?) throws -> [Int] {
        do {
            return try upstream.applyChatTemplate(messages: messages, tools: tools, additionalContext: additionalContext)
        } catch Tokenizers.TokenizerError.missingChatTemplate {
            throw MLXLMCommon.TokenizerError.missingChatTemplate
        }
    }
}

struct LocalTokenizerLoader: MLXLMCommon.TokenizerLoader {
    func load(from directory: URL) async throws -> any MLXLMCommon.Tokenizer {
        TokenizerBridge(upstream: try await AutoTokenizer.from(modelFolder: directory))
    }
}

// MARK: - Text-only view of a model folder

/// oMLX's Gemma 4 folders list the multi-token-prediction head (`language_model.mtp.*`, in
/// `model-mtp.safetensors`) in `model.safetensors.index.json`. mlx-swift-lm 3.32.3's text
/// model has no module for those keys and loads with `verify: [.all]`, so it would refuse
/// them. This builds `<folder>/.edgebench-textonly/`: symlinks to every file except the MTP
/// shard and the index, plus an index without the MTP entries. Vision and audio tensors stay
/// listed; Gemma4Model.sanitize drops them and the lazy loader never reads their bytes.
public enum TextOnlyFolder {
    public static let name = ".edgebench-textonly"

    public static func prepare(source: URL) throws -> (URL, droppedKeys: Int) {
        let fm = FileManager.default
        let out = source.appendingPathComponent(name, isDirectory: true)
        try? fm.removeItem(at: out)
        try fm.createDirectory(at: out, withIntermediateDirectories: true)
        let indexName = "model.safetensors.index.json"
        for item in try fm.contentsOfDirectory(atPath: source.path)
        where !item.hasPrefix(".") && item != "model-mtp.safetensors" && item != indexName {
            try fm.createSymbolicLink(at: out.appendingPathComponent(item), withDestinationURL: source.appendingPathComponent(item))
        }
        var dropped = 0
        let index = source.appendingPathComponent(indexName)
        if let data = try? Data(contentsOf: index),
           var json = try JSONSerialization.jsonObject(with: data) as? [String: Any],
           let map = json["weight_map"] as? [String: String] {
            let kept = map.filter { !$0.key.contains(".mtp.") && !$0.key.hasPrefix("mtp.") && $0.value != "model-mtp.safetensors" }
            dropped = map.count - kept.count
            json["weight_map"] = kept
            try JSONSerialization.data(withJSONObject: json, options: [.sortedKeys]).write(to: out.appendingPathComponent(indexName))
        }
        return (out, dropped)
    }
}

// MARK: - Engine

/// A tool call as mlx-swift-lm's own `gemma4` parser read it (recovery disabled).
public struct EngineToolCall: Sendable {
    public var name: String
    /// The parsed arguments as a JSON object string.
    public var argumentsJSON: String
}

public struct EngineGeneration: Sendable {
    public var tokenIds: [Int]
    public var rawText: String
    public var toolCalls: [EngineToolCall]
    /// `RejectedToolCall`s, each as a JSON object string.
    public var rejectedCalls: [String]
    public var mlxActiveBytes: Int
    public var mlxCacheBytes: Int
    public var mlxPeakBytes: Int
    public var promptTokens: Int
    /// mlx-swift-lm's prefill time (TokenIterator.promptPrefillTime) plus the first step.
    public var promptSeconds: Double
    /// First token → last token.
    public var generateSeconds: Double
    public var stoppedOnEOS: Bool
    public var cancelled: Bool
}

public enum EngineError: LocalizedError {
    case notLoaded
    public var errorDescription: String? { "The model is not loaded." }
}

/// One model at a time; calls must not overlap (the runner is sequential).
public final class MLXBenchEngine: @unchecked Sendable {
    public static let mlxSwiftLMVersion = "3.32.3"
    public static let mlxSwiftVersion = "0.32.3"

    /// MLX keeps freed GPU buffers for reuse up to this size; on an iPhone they count against
    /// the app's memory limit. Fixed at 256 MiB, as Harbor's Ling runtime uses, and recorded
    /// (spec 04 §2.1); a lower limit changes the results.
    public var gpuCacheLimitBytes = 256 << 20
    /// Prompt tokens per prefill forward pass; bounds prefill transients on long prompts.
    public var prefillStepSize = 512

    private let lock = NSLock()
    private var context: ModelContext?
    private var stopIds = Set<Int>()
    private var unknownId: Int?

    public init() {}

    public var isLoaded: Bool { lock.withLock { context != nil } }

    /// Loads weights and tokenizer and materialises the weights (mlx-swift-lm's loadWeights
    /// realises the model before returning). Returns notes for the result record.
    public func load(directory: URL) async throws -> [String] {
        Memory.cacheLimit = gpuCacheLimitBytes
        let (dir, dropped) = try TextOnlyFolder.prepare(source: directory)
        let ctx = try await LLMModelFactory.shared.load(from: dir, using: LocalTokenizerLoader())
        var stops = ctx.configuration.eosTokenIds
        for t in ctx.configuration.extraEOSTokens { if let id = ctx.tokenizer.convertTokenToId(t) { stops.insert(id) } }
        if let id = ctx.tokenizer.eosTokenId { stops.insert(id) }
        for t in ["<turn|>", "<end_of_turn>", "<eos>"] { if let id = ctx.tokenizer.convertTokenToId(t) { stops.insert(id) } }
        let unknown = ctx.tokenizer.unknownTokenId
        lock.withLock {
            context = ctx
            stopIds = stops
            unknownId = unknown
        }
        Memory.clearCache()
        return [
            "text-only load via LLMModelFactory; \(dropped) MTP tensors left out of the index",
            "stop ids: \(stops.sorted())",
            "MLX memoryLimit \(Memory.memoryLimit >> 20) MiB, cacheLimit \(Memory.cacheLimit >> 20) MiB, "
                + "recommended working set \(((GPU.maxRecommendedWorkingSetBytes() ?? 0) >> 20)) MiB",
        ]
    }

    public func unload() {
        lock.withLock {
            context = nil
            stopIds = []
        }
        Memory.clearCache()
    }

    private func ctx() throws -> ModelContext {
        guard let c = lock.withLock({ context }) else { throw EngineError.notLoaded }
        return c
    }

    /// Token ids of the conversation after the model's chat template, generation prompt included.
    public func promptTokens(messages: [[String: any Sendable]], tools: [[String: any Sendable]]?, enableThinking: Bool) throws -> [Int] {
        try ctx().tokenizer.applyChatTemplate(messages: messages, tools: tools, additionalContext: ["enable_thinking": enableThinking])
    }

    public func countTextTokens(_ text: String) throws -> Int {
        try ctx().tokenizer.encode(text: text, addSpecialTokens: false).count
    }

    /// A prompt already rendered on the Mac, tokenized as given (its special tokens are text in it).
    public func encodeRendered(_ text: String) throws -> [Int] {
        try ctx().tokenizer.encode(text: text, addSpecialTokens: false)
    }

    public func tokenString(_ id: Int) -> String {
        (try? ctx())?.tokenizer.convertIdToToken(id) ?? ""
    }

    /// Runs prefill + decode on a fresh KV cache. `onPrefillDone` fires once the prefill has
    /// been evaluated on the GPU (MLX is lazy, so the stream is synchronised first);
    /// `onToken(id, rawTokenString)` fires as each token id reaches the CPU. `ignoreEOS` keeps
    /// decoding through end-of-sequence tokens (fixed-length perf samples). Cancelling the
    /// calling task stops before the next token. `tools` (OpenAI shape) are what the output is
    /// parsed against with mlx-swift-lm's `gemma4` parser, recovery disabled.
    public func generate(promptTokens: [Int], maxTokens: Int, temperature: Float, topP: Float, topK: Int, seed: UInt64,
                         ignoreEOS: Bool, tools: [[String: any Sendable]]?, onPrefillDone: () -> Void,
                         onToken: (Int, String) -> Void) throws -> EngineGeneration {
        let c = try ctx()
        let stops = lock.withLock { stopIds }
        let unknown = lock.withLock { unknownId }
        var p = GenerateParameters(maxTokens: maxTokens, temperature: temperature, topP: topP, topK: topK, seed: seed)
        p.prefill.stepSize = prefillStepSize
        let input = LMInput(tokens: MLXArray(promptTokens.map { Int32($0) }))
        let now = { Date.timeIntervalSinceReferenceDate }
        Memory.peakMemory = 0   // the setter resets MLX's peak counter
        var iterator = try TokenIterator(input: input, model: c.model, parameters: p)
        MLX.Stream.defaultStream.synchronize()
        onPrefillDone()
        let afterInit = now()
        var ids = [Int]()
        var firstAt: Double?
        var eos = false, cancelled = false
        while let token = iterator.next() {
            if firstAt == nil { firstAt = now() }
            if !ignoreEOS, stops.contains(token) || token == unknown { eos = true; break }
            ids.append(token)
            onToken(token, c.tokenizer.convertIdToToken(token) ?? "")
            if Task.isCancelled { cancelled = true; break }
        }
        MLX.Stream.defaultStream.synchronize()
        let end = now()
        let text = c.tokenizer.decode(tokenIds: ids, skipSpecialTokens: false)
        let snap = (Memory.activeMemory, Memory.cacheMemory, Memory.peakMemory)
        Memory.clearCache()
        let (calls, rejected) = Self.parseToolCalls(text, tools: tools)
        return EngineGeneration(
            tokenIds: ids, rawText: text, toolCalls: calls, rejectedCalls: rejected,
            mlxActiveBytes: snap.0, mlxCacheBytes: snap.1, mlxPeakBytes: snap.2, promptTokens: promptTokens.count,
            promptSeconds: iterator.promptPrefillTime, generateSeconds: end - (firstAt ?? end),
            stoppedOnEOS: eos, cancelled: cancelled)
    }

    /// mlx-swift-lm's own Gemma 4 tool-call parser over the complete text, with cross-dialect
    /// recovery disabled and rejected calls kept (spec 02 §4, "Recovery and validation").
    public static func parseToolCalls(_ text: String, tools: [[String: any Sendable]]?) -> ([EngineToolCall], [String]) {
        let processor = ToolCallProcessor(format: .gemma4, tools: tools,
                                          toolCallPolicy: ToolCallPolicy(recovery: .disabled, validation: .permissive))
        _ = processor.processChunk(text)
        _ = processor.processEOS()
        let enc = JSONEncoder()
        enc.outputFormatting = [.sortedKeys]
        let calls = processor.toolCalls.map { call in
            EngineToolCall(name: call.function.name,
                           argumentsJSON: (try? enc.encode(call.function.arguments)).flatMap { String(data: $0, encoding: .utf8) } ?? "{}")
        }
        let rejected = processor.rejectedToolCalls.compactMap { (try? enc.encode($0)).flatMap { String(data: $0, encoding: .utf8) } }
        return (calls, rejected)
    }

    public static func memorySnapshot() -> (active: Int, cache: Int, peak: Int) {
        (Memory.activeMemory, Memory.cacheMemory, Memory.peakMemory)
    }
}
