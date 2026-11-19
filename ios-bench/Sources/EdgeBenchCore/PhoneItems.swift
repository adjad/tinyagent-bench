import CryptoKit
import Foundation

// Phone parity items (`edat-phone-item/v1`) and results (`edat-phone-result/v1`), spec 02 §6.3.
// The phone grades for display only; the Mac re-grades every result from `raw_text`.

public struct PhoneMessage: Codable, Sendable, Equatable {
    public var role: String
    public var content: String
    public var toolCalls: [JSONValue]?
    public var toolCallID: String?

    enum CodingKeys: String, CodingKey {
        case role, content
        case toolCalls = "tool_calls"
        case toolCallID = "tool_call_id"
    }

    public init(role: String, content: String, toolCalls: [JSONValue]? = nil, toolCallID: String? = nil) {
        self.role = role
        self.content = content
        self.toolCalls = toolCalls
        self.toolCallID = toolCallID
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        role = try c.decode(String.self, forKey: .role)
        content = try c.decodeIfPresent(String.self, forKey: .content) ?? ""
        toolCalls = try c.decodeIfPresent([JSONValue].self, forKey: .toolCalls)
        toolCallID = try c.decodeIfPresent(String.self, forKey: .toolCallID)
    }
}

public struct PhoneSampling: Codable, Sendable, Equatable {
    public var source: String
    public var temperature: Double
    public var topP: Double?
    public var topK: Int?
    public var seed: UInt64
    public var maxTokens: Int

    enum CodingKeys: String, CodingKey {
        case source, temperature, seed
        case topP = "top_p"
        case topK = "top_k"
        case maxTokens = "max_tokens"
    }

    public init(source: String, temperature: Double, topP: Double?, topK: Int?, seed: UInt64, maxTokens: Int) {
        self.source = source
        self.temperature = temperature
        self.topP = topP
        self.topK = topK
        self.seed = seed
        self.maxTokens = maxTokens
    }

    public var sampling: Sampling {
        Sampling(temperature: temperature, topP: topP, topK: topK, seed: seed)
    }
}

/// `text` block: all four parts are case-insensitive and optional.
public struct PhoneTextCheck: Codable, Sendable, Equatable {
    public var containsAll: [String]?
    public var containsAny: [String]?
    public var notContains: [String]?
    public var regex: String?

    enum CodingKeys: String, CodingKey {
        case regex
        case containsAll = "contains_all"
        case containsAny = "contains_any"
        case notContains = "not_contains"
    }
}

public struct PhoneCallSpec: Codable, Sendable, Equatable {
    public var name: String
    public var args: [String: JSONValue]?
}

public struct PhoneCheck: Codable, Sendable, Equatable {
    public var kind: String                  // calls | no_call | text | either
    public var sameStep: Bool?
    public var match: String?                // all_any_order (default) | any
    public var extraCalls: String?           // allowed (default) | forbidden
    public var calls: [PhoneCallSpec]?
    public var forbid: [String]?
    /// `either` only: acceptable outcomes; the check passes if any option passes.
    public var options: [PhoneCheck]?
    /// Per-option text block (the item's top-level `text` applies on top of this).
    public var text: PhoneTextCheck?

    enum CodingKeys: String, CodingKey {
        case kind, match, calls, forbid, options, text
        case sameStep = "same_step"
        case extraCalls = "extra_calls"
    }
}

public struct PhoneItem: Codable, Sendable, Equatable {
    public static let schemaID = "edat-phone-item/v1"
    public var schema: String
    public var id: String
    public var sourceCase: String?
    public var family: String
    public var lane: String?
    public var worldSeed: Int?
    public var today: String
    public var tz: String?
    public var thinking: Bool
    public var sampling: PhoneSampling
    public var messages: [PhoneMessage]
    public var tools: [JSONValue]
    public var renderedPrompt: String?
    public var renderedPromptSHA256: String?
    public var check: PhoneCheck
    public var text: PhoneTextCheck?

    enum CodingKeys: String, CodingKey {
        case schema, id, family, lane, today, tz, thinking, sampling, messages, tools, check, text
        case sourceCase = "source_case"
        case worldSeed = "world_seed"
        case renderedPrompt = "rendered_prompt"
        case renderedPromptSHA256 = "rendered_prompt_sha256"
    }

    public var benchMessages: [BenchMessage] {
        messages.map { BenchMessage(role: $0.role, content: $0.content, toolCalls: $0.toolCalls, toolCallID: $0.toolCallID) }
    }

    /// `today` ("YYYY-MM-DD") as date components; nil when it is not a real date.
    public var todayComponents: DateComponents? {
        let parts = today.split(separator: "-").compactMap { Int($0) }
        guard parts.count == 3 else { return nil }
        var c = DateComponents()
        c.year = parts[0]; c.month = parts[1]; c.day = parts[2]
        return Calendar(identifier: .gregorian).date(from: c) == nil ? nil : c
    }
}

public struct PhoneItemManifest: Codable, Sendable, Equatable {
    public var schema: String
    public var itemCount: Int
    public var jsonlSHA256: String
    public var exporterVersion: String?
    public var templateSHA256: String?
    public var weightsSHA256: String?
    public var source: String?

    enum CodingKeys: String, CodingKey {
        case schema, source
        case itemCount = "item_count"
        case jsonlSHA256 = "jsonl_sha256"
        case exporterVersion = "exporter_version"
        case templateSHA256 = "template_sha256"
        case weightsSHA256 = "weights_sha256"
    }
}

public enum PhoneItemError: Error, Equatable, LocalizedError {
    case badLine(line: Int, reason: String)
    case badManifest(String)

    public var errorDescription: String? {
        switch self {
        case .badLine(let line, let reason): "phone items line \(line): \(reason)"
        case .badManifest(let reason): "phone items manifest: \(reason)"
        }
    }
}

public struct PhoneItemSet: Sendable {
    public var items: [PhoneItem]
    public var sha256: String
    public var manifest: PhoneItemManifest?

    public static func load(jsonl: Data, manifest: Data? = nil) throws -> PhoneItemSet {
        var items: [PhoneItem] = []
        var seen = Set<String>()
        let decoder = JSONDecoder()
        for (index, raw) in jsonl.split(separator: 0x0A, omittingEmptySubsequences: false).enumerated() {
            let line = index + 1
            let bytes = Data(raw)
            if bytes.allSatisfy({ $0 == 0x20 || $0 == 0x09 || $0 == 0x0D }) { continue }
            let item: PhoneItem
            do { item = try decoder.decode(PhoneItem.self, from: bytes) }
            catch { throw PhoneItemError.badLine(line: line, reason: "\(error)") }
            guard item.schema == PhoneItem.schemaID else {
                throw PhoneItemError.badLine(line: line, reason: "schema \(item.schema) is not \(PhoneItem.schemaID)")
            }
            guard seen.insert(item.id).inserted else {
                throw PhoneItemError.badLine(line: line, reason: "duplicate id \(item.id)")
            }
            items.append(item)
        }
        var parsed: PhoneItemManifest?
        if let manifest {
            do { parsed = try decoder.decode(PhoneItemManifest.self, from: manifest) }
            catch { throw PhoneItemError.badManifest("\(error)") }
        }
        let sha = SHA256.hash(data: jsonl).map { String(format: "%02x", $0) }.joined()
        return PhoneItemSet(items: items, sha256: sha, manifest: parsed)
    }
}

public struct PhoneParsedCall: Codable, Sendable, Equatable {
    public var name: String
    public var argumentsRaw: String
    public var arguments: JSONValue?

    enum CodingKeys: String, CodingKey {
        case name, arguments
        case argumentsRaw = "arguments_raw"
    }

    public init(name: String, argumentsRaw: String, arguments: JSONValue?) {
        self.name = name
        self.argumentsRaw = argumentsRaw
        self.arguments = arguments
    }

    public init(_ c: ParsedToolCall) {
        self.init(name: c.name, argumentsRaw: c.argumentsRaw, arguments: c.arguments.map { .object($0) })
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(name, forKey: .name)
        try c.encode(argumentsRaw, forKey: .argumentsRaw)
        try c.encode(arguments, forKey: .arguments)      // null when the raw text did not parse
    }

    /// The arguments as an object: `arguments` if it is one, else `argumentsRaw` parsed.
    var argumentObject: [String: JSONValue]? {
        if let o = arguments?.objectValue { return o }
        guard let data = argumentsRaw.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) else { return nil }
        return JSONValue.from(any: obj).objectValue
    }
}

public enum PhoneGrader {
    /// Display-only local check. `answer` is the non-reasoning text with tool-call markup removed.
    public static func grade(_ item: PhoneItem, answer: String, calls: [PhoneParsedCall]) -> (pass: Bool, labels: [String]) {
        guard let today = item.todayComponents else { return (false, ["bad item date \(item.today)"]) }
        var out = labels(for: item.check, answer: answer, calls: calls, today: today)
        if let text = item.text { out += textLabels(text, answer) }
        return (out.isEmpty, out)
    }

    private static func labels(for check: PhoneCheck, answer: String, calls: [PhoneParsedCall], today: DateComponents) -> [String] {
        var out: [String]
        switch check.kind {
        case "calls":
            out = callLabels(check, calls, today)
        case "no_call", "text":
            out = calls.map { "unexpected call \($0.name)" }
        case "either":
            let options = check.options ?? []
            var all: [String] = []
            for (i, option) in options.enumerated() {
                let l = labels(for: option, answer: answer, calls: calls, today: today)
                if l.isEmpty { return [] }
                all += l.map { "option \(i + 1): \($0)" }
            }
            return options.isEmpty ? ["either has no options"] : all
        default:
            return ["unknown check kind \(check.kind)"]
        }
        if let text = check.text { out += textLabels(text, answer) }
        return out
    }

    private static func callLabels(_ check: PhoneCheck, _ calls: [PhoneParsedCall], _ today: DateComponents) -> [String] {
        let specs = check.calls ?? []
        var out: [String] = []
        for call in calls where (check.forbid ?? []).contains(call.name) { out.append("forbidden call \(call.name)") }
        // mismatches[i][j]: why call j does not satisfy spec i (empty means it does).
        let mismatches = specs.map { spec in calls.map { callMismatches(spec, $0, today) } }
        let ok = mismatches.map { $0.map(\.isEmpty) }
        var used = Set<Int>()
        var missing: [Int] = []
        if (check.match ?? "all_any_order") == "any" {
            let hit = ok.lazy.compactMap { $0.firstIndex(of: true) }.first
            if let hit { used.insert(hit) } else if !specs.isEmpty { missing = Array(specs.indices) }
        } else {
            var ownerOfCall = [Int?](repeating: nil, count: calls.count)
            for i in specs.indices {
                var seen = [Bool](repeating: false, count: calls.count)
                if !augment(i, ok, &seen, &ownerOfCall) { missing.append(i) }
            }
            for (j, owner) in ownerOfCall.enumerated() where owner != nil { used.insert(j) }
        }
        for i in missing {
            out.append("missing call \(specs[i].name)")
            if let j = calls.indices.first(where: { calls[$0].name == specs[i].name }) {
                out += mismatches[i][j].map { clip("arg \($0)") }
            }
        }
        if check.extraCalls == "forbidden" {
            for (j, call) in calls.enumerated() where !used.contains(j) && !(check.forbid ?? []).contains(call.name) {
                out.append("extra call \(call.name)")
            }
        }
        return out
    }

    /// Kuhn's augmenting-path step: gives spec `i` a distinct call, moving earlier picks if needed.
    private static func augment(_ i: Int, _ ok: [[Bool]], _ seen: inout [Bool], _ ownerOfCall: inout [Int?]) -> Bool {
        for j in ok[i].indices where ok[i][j] && !seen[j] {
            seen[j] = true
            if let other = ownerOfCall[j] {
                if !augment(other, ok, &seen, &ownerOfCall) { continue }
            }
            ownerOfCall[j] = i
            return true
        }
        return false
    }

    private static func callMismatches(_ spec: PhoneCallSpec, _ call: PhoneParsedCall, _ today: DateComponents) -> [String] {
        guard spec.name == call.name else { return ["name \(call.name) != \(spec.name)"] }
        let want = spec.args ?? [:]
        if want.isEmpty { return [] }
        guard let args = call.argumentObject else { return ["arguments are not a JSON object"] }
        return Matchers.matchArgs(want, args, today: today)
    }

    private static func textLabels(_ t: PhoneTextCheck, _ answer: String) -> [String] {
        let low = answer.lowercased()
        var out: [String] = []
        for w in t.containsAll ?? [] where !low.contains(w.lowercased()) { out.append(clip("answer lacks \"\(w)\"")) }
        if let any = t.containsAny, !any.isEmpty, !any.contains(where: { low.contains($0.lowercased()) }) {
            out.append(clip("answer has none of \(any)"))
        }
        for w in t.notContains ?? [] where low.contains(w.lowercased()) { out.append(clip("answer contains \"\(w)\"")) }
        if let pattern = t.regex {
            if let re = try? NSRegularExpression(pattern: pattern, options: [.caseInsensitive]) {
                let range = NSRange(answer.startIndex..., in: answer)
                if re.firstMatch(in: answer, options: [], range: range) == nil { out.append(clip("answer !~ /\(pattern)/")) }
            } else {
                out.append(clip("bad regex /\(pattern)/"))
            }
        }
        return out
    }

    private static func clip(_ s: String) -> String { s.count > 140 ? String(s.prefix(137)) + "..." : s }
}

public struct PhoneRuntime: Codable, Sendable, Equatable {
    public var mlxSwiftLM: String
    public var mlxSwift: String
    public var toolFormat: String
    public var recovery: String
    public var schemaValidation: String

    enum CodingKeys: String, CodingKey {
        case mlxSwiftLM = "mlx_swift_lm"
        case mlxSwift = "mlx_swift"
        case toolFormat = "tool_format"
        case recovery
        case schemaValidation = "schema_validation"
    }

    public init(mlxSwiftLM: String, mlxSwift: String, toolFormat: String, recovery: String, schemaValidation: String) {
        self.mlxSwiftLM = mlxSwiftLM
        self.mlxSwift = mlxSwift
        self.toolFormat = toolFormat
        self.recovery = recovery
        self.schemaValidation = schemaValidation
    }
}

public struct PhoneResult: Codable, Sendable, Equatable {
    public static let schemaID = "edat-phone-result/v1"
    public var schema: String
    public var id: String
    public var trial: Int
    public var mode: String                  // messages | rendered
    public var device: String
    public var runtime: PhoneRuntime
    public var weightsSHA256: String?
    public var rawText: String
    public var reasoningText: String
    public var parsedCalls: [PhoneParsedCall]
    public var rejectedCalls: [JSONValue]
    public var finishReason: String
    public var promptTokens: Int
    public var completionTokens: Int
    public var ttftS: Double?
    public var totalS: Double
    public var localPass: Bool
    public var localLabels: [String]
    public var modelID: String
    public var status: String                // ok | oom_killed | killed_unknown | timeout | error
    public var error: String?

    enum CodingKeys: String, CodingKey {
        case schema, id, trial, mode, device, runtime, status, error
        case weightsSHA256 = "weights_sha256"
        case rawText = "raw_text"
        case reasoningText = "reasoning_text"
        case parsedCalls = "parsed_calls"
        case rejectedCalls = "rejected_calls"
        case finishReason = "finish_reason"
        case promptTokens = "prompt_tokens"
        case completionTokens = "completion_tokens"
        case ttftS = "ttft_s"
        case totalS = "total_s"
        case localPass = "local_pass"
        case localLabels = "local_labels"
        case modelID = "model_id"
    }

    public init(id: String, trial: Int, mode: String, device: String, runtime: PhoneRuntime,
                weightsSHA256: String?, rawText: String, reasoningText: String = "",
                parsedCalls: [PhoneParsedCall] = [], rejectedCalls: [JSONValue] = [],
                finishReason: String, promptTokens: Int, completionTokens: Int,
                ttftS: Double?, totalS: Double, localPass: Bool, localLabels: [String] = [],
                modelID: String, status: String = "ok", error: String? = nil) {
        self.schema = PhoneResult.schemaID
        self.id = id
        self.trial = trial
        self.mode = mode
        self.device = device
        self.runtime = runtime
        self.weightsSHA256 = weightsSHA256
        self.rawText = rawText
        self.reasoningText = reasoningText
        self.parsedCalls = parsedCalls
        self.rejectedCalls = rejectedCalls
        self.finishReason = finishReason
        self.promptTokens = promptTokens
        self.completionTokens = completionTokens
        self.ttftS = ttftS
        self.totalS = totalS
        self.localPass = localPass
        self.localLabels = localLabels
        self.modelID = modelID
        self.status = status
        self.error = error
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(schema, forKey: .schema)
        try c.encode(id, forKey: .id)
        try c.encode(trial, forKey: .trial)
        try c.encode(mode, forKey: .mode)
        try c.encode(device, forKey: .device)
        try c.encode(runtime, forKey: .runtime)
        try c.encode(weightsSHA256, forKey: .weightsSHA256)   // explicit null when unknown
        try c.encode(rawText, forKey: .rawText)
        try c.encode(reasoningText, forKey: .reasoningText)
        try c.encode(parsedCalls, forKey: .parsedCalls)
        try c.encode(rejectedCalls, forKey: .rejectedCalls)
        try c.encode(finishReason, forKey: .finishReason)
        try c.encode(promptTokens, forKey: .promptTokens)
        try c.encode(completionTokens, forKey: .completionTokens)
        try c.encode(ttftS, forKey: .ttftS)
        try c.encode(totalS, forKey: .totalS)
        try c.encode(localPass, forKey: .localPass)
        try c.encode(localLabels, forKey: .localLabels)
        try c.encode(modelID, forKey: .modelID)
        try c.encode(status, forKey: .status)
        try c.encode(error, forKey: .error)
    }

    /// One JSONL line: sorted keys, no trailing newline.
    public func jsonlLine() throws -> Data { try JSONEncoder.sorted.encode(self) }
}
