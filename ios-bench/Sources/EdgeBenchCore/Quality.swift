import Foundation

// Matcher semantics shared with the Mac harness (`scripts/toolbench/matchers.py`), used by the
// phone-item grader (PhoneItems.swift), and Gemma 4's output format.

/// A matcher spec: a bare value (shorthand for `eq`) or `{op: value}` with one key.
public typealias MatcherSpec = JSONValue

public enum Matchers {
    static let weekdays = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

    static func norm(_ v: JSONValue) -> JSONValue {
        if case .string(let s) = v { return .string(s.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()) }
        return v
    }

    static func isoDate(_ v: JSONValue?) -> DateComponents? {
        guard let s = v?.stringValue,
              let m = s.range(of: #"^\s*(\d{4})-(\d{2})-(\d{2})"#, options: .regularExpression) else { return nil }
        let parts = s[m].trimmingCharacters(in: .whitespaces).split(separator: "-").compactMap { Int($0) }
        guard parts.count == 3 else { return nil }
        var c = DateComponents()
        c.year = parts[0]; c.month = parts[1]; c.day = parts[2]
        return Calendar(identifier: .gregorian).date(from: c) == nil ? nil : c
    }

    /// nil when `value` satisfies `spec`, else why not (matchers.py `match_value`).
    public static func match(_ spec: MatcherSpec, _ value: JSONValue?, today: DateComponents) -> String? {
        var op = "eq", want = spec
        if case .object(let o) = spec {
            guard o.count == 1, let first = o.first else { return "bad matcher \(spec.plainText)" }
            op = first.key; want = first.value
        }
        let value = value ?? .null
        let truthy: Bool = { if case .bool(let b) = want { return b }; return true }()
        if op == "absent" { return value.isEmptyValue == truthy ? nil : "expected \(truthy ? "absent" : "present")" }
        if op == "present" { return (!value.isEmptyValue) == truthy ? nil : "expected \(truthy ? "present" : "absent")" }
        if value.isEmptyValue { return "missing" }
        let text = value.plainText
        let cal = Calendar(identifier: .gregorian)
        switch op {
        case "eq":
            return norm(value) == norm(want) || (value.numberValue != nil && value.plainText == want.plainText)
                ? nil : "\(text) != \(want.plainText)"
        case "icontains":
            return text.lowercased().contains(want.plainText.lowercased()) ? nil : "\(text) lacks \(want.plainText)"
        case "contains_any":
            let ws = want.arrayValue ?? []
            return ws.contains { text.lowercased().contains($0.plainText.lowercased()) } ? nil : "\(text) lacks any of \(want.plainText)"
        case "regex":
            return text.range(of: want.plainText, options: [.regularExpression, .caseInsensitive]) != nil ? nil : "\(text) !~ /\(want.plainText)/"
        case "in":
            let ws = (want.arrayValue ?? []).map(norm)
            return ws.contains(norm(value)) ? nil : "\(text) not in \(want.plainText)"
        case "date_offset":
            guard let d = isoDate(value), let day = cal.date(from: d), let base = cal.date(from: today),
                  let target = cal.date(byAdding: .day, value: Int(want.numberValue ?? 0), to: base) else { return "\(text) is not an ISO date" }
            return cal.isDate(day, inSameDayAs: target) ? nil : "\(text) is not today+\(Int(want.numberValue ?? 0))"
        case "weekday":
            guard let d = isoDate(value), let day = cal.date(from: d), let base = cal.date(from: today) else { return "\(text) is not an ISO date" }
            let idx = (cal.component(.weekday, from: day) + 5) % 7      // Monday = 0
            let name = weekdays[idx]
            if name != want.plainText.lowercased() { return "\(text) is a \(name), not \(want.plainText)" }
            return day > base ? nil : "\(text) is not in the future"
        default:
            return "unknown matcher \(op)"
        }
    }

    public static func matchArgs(_ spec: [String: MatcherSpec], _ args: [String: JSONValue], today: DateComponents) -> [String] {
        spec.keys.sorted().compactMap { key in match(spec[key]!, args[key], today: today).map { "\(key): \($0)" } }
    }
}

// MARK: - Gemma 4 output format

/// Gemma 4's output markup (from the model's chat_template.jinja): reasoning in
/// `<|channel>thought\n…<channel|>`, tool calls as `<|tool_call>call:NAME{k:v,…}<tool_call|>`
/// with strings quoted by `<|"|>`. Parsed in the core so it is unit-tested and the same
/// rules apply to every engine that returns raw text.
public enum GemmaOutput {
    public struct Parsed: Sendable, Equatable {
        public var reasoning: String?
        public var answer: String
        public var toolCalls: [ParsedToolCall]
    }

    public static let channelOpen = "<|channel>"
    public static let channelClose = "<channel|>"
    public static let callOpen = "<|tool_call>"
    public static let callClose = "<tool_call|>"
    public static let quote = "<|\"|>"
    static let stripTokens = ["<turn|>", "<|turn>", "<eos>", "<end_of_turn>", "<bos>"]

    public static func parse(_ raw: String) -> Parsed {
        var text = raw
        var reasoning = [String]()
        while let o = text.range(of: channelOpen) {
            let rest = text[o.upperBound...]
            if let c = rest.range(of: channelClose) {
                var r = String(rest[..<c.lowerBound])
                if r.hasPrefix("thought") { r = String(r.dropFirst("thought".count)) }
                reasoning.append(r.trimmingCharacters(in: .whitespacesAndNewlines))
                text.removeSubrange(o.lowerBound ..< c.upperBound)
            } else {
                // Unclosed: everything after is reasoning (generation was cut off while thinking).
                var r = String(rest)
                if r.hasPrefix("thought") { r = String(r.dropFirst("thought".count)) }
                reasoning.append(r.trimmingCharacters(in: .whitespacesAndNewlines))
                text.removeSubrange(o.lowerBound...)
            }
        }
        var calls = [ParsedToolCall]()
        while let o = text.range(of: callOpen) {
            let rest = text[o.upperBound...]
            let end = rest.range(of: callClose)
            let body = String(end.map { rest[..<$0.lowerBound] } ?? rest)
            if let call = parseCall(body) { calls.append(call) }
            text.removeSubrange(o.lowerBound ..< (end?.upperBound ?? text.endIndex))
        }
        for t in stripTokens { text = text.replacingOccurrences(of: t, with: "") }
        return Parsed(reasoning: reasoning.isEmpty ? nil : reasoning.joined(separator: "\n"),
                      answer: text.trimmingCharacters(in: .whitespacesAndNewlines), toolCalls: calls)
    }

    /// `call:name{key:<|"|>v<|"|>,n:3}` → name + JSON arguments.
    static func parseCall(_ body: String) -> ParsedToolCall? {
        var s = body.trimmingCharacters(in: .whitespacesAndNewlines)
        if s.hasPrefix("call:") { s = String(s.dropFirst(5)) }
        guard let brace = s.firstIndex(of: "{") else {
            let name = s.trimmingCharacters(in: .whitespaces)
            return name.isEmpty ? nil : ParsedToolCall(name: name, argumentsRaw: "{}")
        }
        let name = String(s[..<brace]).trimmingCharacters(in: .whitespaces)
        let args = String(s[brace...])
        let json = gemmaArgsToJSON(args) ?? args
        return ParsedToolCall(name: name, argumentsRaw: json)
    }

    /// Converts Gemma's argument syntax to JSON; nil if it does not parse.
    public static func gemmaArgsToJSON(_ s: String) -> String? {
        var p = GemmaArgParser(Array(s))
        guard let v = p.value() else { return nil }
        p.skipSpace()
        guard p.atEnd else { return nil }
        return (try? String(data: JSONEncoder.sorted.encode(v), encoding: .utf8)) ?? nil
    }

    struct GemmaArgParser {
        let c: [Character]
        var i = 0
        init(_ c: [Character]) { self.c = c }
        var atEnd: Bool { i >= c.count }
        mutating func skipSpace() { while i < c.count, c[i].isWhitespace { i += 1 } }
        func starts(_ s: String) -> Bool {
            let a = Array(s)
            return i + a.count <= c.count && Array(c[i ..< i + a.count]) == a
        }
        mutating func value() -> JSONValue? {
            skipSpace()
            guard i < c.count else { return nil }
            if starts(GemmaOutput.quote) {
                i += GemmaOutput.quote.count
                var out = ""
                while i < c.count, !starts(GemmaOutput.quote) { out.append(c[i]); i += 1 }
                guard i < c.count else { return nil }
                i += GemmaOutput.quote.count
                return .string(out)
            }
            if c[i] == "\"" {      // tolerate plain JSON strings
                var out = "\""; i += 1
                var esc = false
                while i < c.count {
                    let ch = c[i]; out.append(ch); i += 1
                    if esc { esc = false } else if ch == "\\" { esc = true } else if ch == "\"" { break }
                }
                return out.data(using: .utf8).flatMap { try? JSONDecoder().decode(String.self, from: $0) }.map { JSONValue.string($0) }
            }
            if c[i] == "{" {
                i += 1
                var obj = [String: JSONValue]()
                skipSpace()
                if i < c.count, c[i] == "}" { i += 1; return .object(obj) }
                while i < c.count {
                    skipSpace()
                    var key = ""
                    if starts(GemmaOutput.quote), case .string(let k)? = value() { key = k }
                    else if c[i] == "\"", case .string(let k)? = value() { key = k }
                    else { while i < c.count, c[i] != ":" { key.append(c[i]); i += 1 } }
                    skipSpace()
                    guard i < c.count, c[i] == ":" else { return nil }
                    i += 1
                    guard let v = value() else { return nil }
                    obj[key.trimmingCharacters(in: .whitespaces)] = v
                    skipSpace()
                    guard i < c.count else { return nil }
                    if c[i] == "," { i += 1; continue }
                    if c[i] == "}" { i += 1; return .object(obj) }
                    return nil
                }
                return nil
            }
            if c[i] == "[" {
                i += 1
                var arr = [JSONValue]()
                skipSpace()
                if i < c.count, c[i] == "]" { i += 1; return .array(arr) }
                while i < c.count {
                    guard let v = value() else { return nil }
                    arr.append(v)
                    skipSpace()
                    guard i < c.count else { return nil }
                    if c[i] == "," { i += 1; continue }
                    if c[i] == "]" { i += 1; return .array(arr) }
                    return nil
                }
                return nil
            }
            var bare = ""
            while i < c.count, !",}]".contains(c[i]) { bare.append(c[i]); i += 1 }
            let t = bare.trimmingCharacters(in: .whitespaces)
            switch t.lowercased() {
            case "true": return .bool(true)
            case "false": return .bool(false)
            case "null", "none": return .null
            default: return Double(t).map { .number($0) } ?? .string(t)
            }
        }
    }
}

/// Classifies streamed Gemma 4 token pieces as reasoning, tool call or answer.
public struct GemmaTokenClassifier: Sendable {
    private var inThought = false
    private var inCall = false
    public init() {}

    public mutating func classify(piece: String) -> TokenKind {
        if piece.contains(GemmaOutput.channelOpen) { inThought = true; return .reasoning }
        if piece.contains(GemmaOutput.channelClose) { inThought = false; return .reasoning }
        if piece.contains(GemmaOutput.callOpen) { inCall = true; return .toolCall }
        if piece.contains(GemmaOutput.callClose) { inCall = false; return .toolCall }
        if inThought { return .reasoning }
        if inCall { return .toolCall }
        return .answer
    }
}
