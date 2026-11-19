import Foundation

// The engine boundary. The measurement core never imports MLX: the app hands it a
// `BenchGenerator` (MLX on a device, `StubGenerator` in tests and the Simulator) and the
// core does every timestamp itself, on its own monotonic clock.

public struct BenchMessage: Codable, Sendable, Equatable {
    public var role: String          // system | user | assistant | tool
    public var content: String
    /// Earlier assistant tool calls in a frozen transcript (OpenAI shape:
    /// `{"id","type":"function","function":{"name","arguments": "<json string>"}}`).
    public var toolCalls: [JSONValue]?
    /// For `role == "tool"`: the call this result answers.
    public var toolCallID: String?
    public init(role: String, content: String, toolCalls: [JSONValue]? = nil, toolCallID: String? = nil) {
        self.role = role
        self.content = content
        self.toolCalls = toolCalls
        self.toolCallID = toolCallID
    }
}

public enum ThinkingMode: String, Codable, Sendable {
    case on, off
}

public struct Sampling: Codable, Sendable, Equatable {
    public var temperature: Double
    public var topP: Double?
    public var topK: Int?
    public var seed: UInt64

    public static let greedy = Sampling(temperature: 0, topP: nil, topK: nil, seed: 1234)
    /// Harbor's shipped Gemma profile (`service/setup/catalog.py`, GEMMA_E4B_MTP).
    public static let harborShipped = Sampling(temperature: 0.8, topP: 0.95, topK: 64, seed: 1234)

    public init(temperature: Double, topP: Double?, topK: Int?, seed: UInt64) {
        self.temperature = temperature
        self.topP = topP
        self.topK = topK
        self.seed = seed
    }
}

public struct GenerationRequest: Sendable {
    public var messages: [BenchMessage]
    /// OpenAI-style function schemas (`{"type":"function","function":{...}}`); empty for none.
    public var tools: [JSONValue]
    public var maxTokens: Int
    public var sampling: Sampling
    public var thinking: ThinkingMode
    /// Keep generating through end-of-sequence tokens until `maxTokens` (perf cells need
    /// exactly 256 output tokens, spec 04 §3.1).
    public var ignoreEOS: Bool
    /// Use the multi-token-prediction head / drafter when the engine has one.
    public var speculation: Bool
    /// Pre-tokenized prompt from the prompt pack (spec 04 §2.3): fed to the model as is,
    /// with no template or tokenizer on the phone. `messages` is then ignored.
    public var promptIDs: [Int]?
    /// A prompt string already rendered on the Mac (phone items' *rendered* mode): tokenized
    /// as given, no chat template.
    public var renderedPrompt: String?

    public init(messages: [BenchMessage], tools: [JSONValue] = [], maxTokens: Int,
                sampling: Sampling = .greedy, thinking: ThinkingMode = .on,
                ignoreEOS: Bool = false, speculation: Bool = false,
                promptIDs: [Int]? = nil, renderedPrompt: String? = nil) {
        self.messages = messages
        self.tools = tools
        self.maxTokens = maxTokens
        self.sampling = sampling
        self.thinking = thinking
        self.ignoreEOS = ignoreEOS
        self.speculation = speculation
        self.promptIDs = promptIDs
        self.renderedPrompt = renderedPrompt
    }
}

/// Callbacks from inside the engine's loop. The core stamps the time of each call.
public struct GenerationHooks: Sendable {
    /// After the prefill forward pass has been evaluated (MLX is lazy: the engine forces it first).
    public var onPrefillDone: @Sendable () -> Void
    /// Once per generated token, as soon as its id is on the CPU.
    public var onToken: @Sendable (TokenKind) -> Void
    public init(onPrefillDone: @escaping @Sendable () -> Void = {}, onToken: @escaping @Sendable (TokenKind) -> Void) {
        self.onPrefillDone = onPrefillDone
        self.onToken = onToken
    }
}

/// What kind of text a generated token belongs to, when the engine can tell.
public enum TokenKind: String, Codable, Sendable {
    case reasoning, answer, toolCall, unknown
}

public struct ParsedToolCall: Codable, Sendable, Equatable {
    public var name: String
    /// Arguments exactly as the model wrote them, so malformed JSON is the model's (spec 02 §4.1).
    public var argumentsRaw: String
    public init(name: String, argumentsRaw: String) {
        self.name = name
        self.argumentsRaw = argumentsRaw
    }

    public var arguments: [String: JSONValue]? {
        guard let data = argumentsRaw.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) else { return nil }
        return JSONValue.from(any: obj).objectValue
    }
}

/// Timings the engine itself reports, kept beside the client clock (spec 04 §3.2 "conventions differ").
public struct EngineTimings: Codable, Sendable, Equatable {
    public var promptSeconds: Double?
    public var generateSeconds: Double?
    public var convention: String
    public init(promptSeconds: Double?, generateSeconds: Double?, convention: String) {
        self.promptSeconds = promptSeconds
        self.generateSeconds = generateSeconds
        self.convention = convention
    }
}

public struct GenerationResult: Sendable {
    public var promptTokens: Int
    public var completionTokens: Int
    public var reasoningTokens: Int?
    public var text: String
    public var reasoning: String?
    public var toolCalls: [ParsedToolCall]
    public var finishReason: String          // stop | length | cancelled
    public var engine: EngineTimings?
    public var draftedTokens: Int?
    public var acceptedDraftTokens: Int?
    /// The complete generated text, reasoning and tool-call markup included.
    public var rawText: String = ""
    /// Tool-call-shaped output the engine's parser rejected (kept, not dropped: spec 02 §4).
    public var rejectedCalls: [JSONValue] = []
    /// MLX `Memory` snapshot after the request (diagnostic; spec 04 §2.5).
    public var mlxActiveBytes: UInt64?
    public var mlxCacheBytes: UInt64?
    public var mlxPeakBytes: UInt64?

    public init(promptTokens: Int, completionTokens: Int, reasoningTokens: Int? = nil, text: String,
                reasoning: String? = nil, toolCalls: [ParsedToolCall] = [], finishReason: String,
                engine: EngineTimings? = nil, draftedTokens: Int? = nil, acceptedDraftTokens: Int? = nil) {
        self.promptTokens = promptTokens
        self.completionTokens = completionTokens
        self.reasoningTokens = reasoningTokens
        self.text = text
        self.reasoning = reasoning
        self.toolCalls = toolCalls
        self.finishReason = finishReason
        self.engine = engine
        self.draftedTokens = draftedTokens
        self.acceptedDraftTokens = acceptedDraftTokens
    }
}

/// What the engine says about itself (spec 04 §7.1 "Runtime").
public struct RuntimeInfo: Codable, Sendable, Equatable {
    public var name: String                 // "mlx-swift" | "stub"
    public var version: String
    public var commit: String?
    public var packages: [String: String]
    public var backend: String
    public var kvDtype: String?
    public var prefillStep: Int?
    public var speculation: String          // "mtp" | "none" | "draft_model"
    public var cacheLimitBytes: UInt64?
    /// Tool-call parser and policy, for phone-item results (spec 02 §6.3).
    public var toolFormat: String
    public var toolRecovery: String
    public var notes: [String]
    public init(name: String, version: String, commit: String? = nil, packages: [String: String] = [:], backend: String,
                kvDtype: String?, prefillStep: Int?, speculation: String, cacheLimitBytes: UInt64?,
                toolFormat: String = "gemma4", toolRecovery: String = "disabled", notes: [String] = []) {
        self.name = name
        self.version = version
        self.commit = commit
        self.packages = packages
        self.backend = backend
        self.kvDtype = kvDtype
        self.prefillStep = prefillStep
        self.speculation = speculation
        self.cacheLimitBytes = cacheLimitBytes
        self.toolFormat = toolFormat
        self.toolRecovery = toolRecovery
        self.notes = notes
    }
}

public struct LoadReport: Sendable {
    /// Engine-reported readiness: weights materialised and the tokenizer loaded.
    public var notes: [String]
    public var speculationAvailable: Bool
    public init(notes: [String] = [], speculationAvailable: Bool) {
        self.notes = notes
        self.speculationAvailable = speculationAvailable
    }
}

/// One model engine. Implementations call `onToken` once per generated token, from the
/// generation loop, as soon as the token exists; the core timestamps the calls.
public protocol BenchGenerator: AnyObject, Sendable {
    var runtimeInfo: RuntimeInfo { get }
    var isLoaded: Bool { get async }
    func load(variant: ModelVariant) async throws -> LoadReport
    func unload() async
    /// Exact prompt length after the model's own chat template, generation prompt included.
    func countPromptTokens(_ messages: [BenchMessage], tools: [JSONValue], thinking: ThinkingMode) async throws -> Int
    /// Raw tokenizer count of plain text, no template (tokens per 1,000 characters).
    func countTextTokens(_ text: String) async throws -> Int
    func generate(_ request: GenerationRequest, hooks: GenerationHooks) async throws -> GenerationResult
}

public enum GeneratorError: LocalizedError, Sendable {
    case notLoaded
    case missingFiles([String])
    case unsupported(String)
    case engineRefused(String)
    case noPromptPack(String)

    public var errorDescription: String? {
        switch self {
        case .notLoaded: "The model is not loaded."
        case .missingFiles(let f): "The model folder is missing: \(f.joined(separator: ", "))"
        case .unsupported(let s): "Not supported: \(s)"
        case .engineRefused(let s): "The engine refused: \(s)"
        case .noPromptPack(let s): "No prompt pack: \(s)"
        }
    }
}
