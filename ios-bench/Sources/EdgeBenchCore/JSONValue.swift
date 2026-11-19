import Foundation

/// A JSON value: tool schemas, tool-call arguments and free-form record fields.
public enum JSONValue: Codable, Sendable, Equatable, Hashable {
    case null
    case bool(Bool)
    case number(Double)
    case string(String)
    case array([JSONValue])
    case object([String: JSONValue])

    public init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null }
        else if let b = try? c.decode(Bool.self) { self = .bool(b) }
        else if let n = try? c.decode(Double.self) { self = .number(n) }
        else if let s = try? c.decode(String.self) { self = .string(s) }
        else if let a = try? c.decode([JSONValue].self) { self = .array(a) }
        else if let o = try? c.decode([String: JSONValue].self) { self = .object(o) }
        else { throw DecodingError.dataCorruptedError(in: c, debugDescription: "not JSON") }
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .null: try c.encodeNil()
        case .bool(let b): try c.encode(b)
        case .number(let n): try c.encode(n)
        case .string(let s): try c.encode(s)
        case .array(let a): try c.encode(a)
        case .object(let o): try c.encode(o)
        }
    }

    public subscript(key: String) -> JSONValue? {
        if case .object(let o) = self { return o[key] }
        return nil
    }

    public var stringValue: String? { if case .string(let s) = self { s } else { nil } }
    public var objectValue: [String: JSONValue]? { if case .object(let o) = self { o } else { nil } }
    public var arrayValue: [JSONValue]? { if case .array(let a) = self { a } else { nil } }
    public var numberValue: Double? { if case .number(let n) = self { n } else { nil } }

    /// True for null, "", [] and {} (the matcher module's notion of "empty").
    public var isEmptyValue: Bool {
        switch self {
        case .null: true
        case .string(let s): s.isEmpty
        case .array(let a): a.isEmpty
        case .object(let o): o.isEmpty
        default: false
        }
    }

    /// The value as a plain string, the way Python's `str()` would show a scalar.
    public var plainText: String {
        switch self {
        case .null: "None"
        case .bool(let b): b ? "True" : "False"
        case .number(let n): n.rounded() == n && abs(n) < 1e15 ? String(Int64(n)) : String(n)
        case .string(let s): s
        case .array, .object:
            (try? String(data: JSONEncoder.sorted.encode(self), encoding: .utf8)) ?? ""
        }
    }

    /// Converts a Foundation JSON object (from JSONSerialization) into a JSONValue.
    public static func from(any value: Any?) -> JSONValue {
        // NSNumber first: `as Bool` would also match the numbers 0 and 1.
        if let n = value as? NSNumber, !(value is String) {
            if CFGetTypeID(n) == CFBooleanGetTypeID() { return .bool(n.boolValue) }
            return .number(n.doubleValue)
        }
        switch value {
        case nil, is NSNull: return .null
        case let b as Bool: return .bool(b)
        case let s as String: return .string(s)
        case let a as [Any]: return .array(a.map { from(any: $0) })
        case let o as [String: Any]: return .object(o.mapValues { from(any: $0) })
        default: return .string(String(describing: value!))
        }
    }

    /// Converts back to Foundation types (for chat-template contexts).
    public var foundationValue: Any {
        switch self {
        case .null: NSNull()
        case .bool(let b): b
        case .number(let n): n.rounded() == n && abs(n) < 1e15 ? Int(n) as Any : n as Any
        case .string(let s): s
        case .array(let a): a.map(\.foundationValue)
        case .object(let o): o.mapValues(\.foundationValue)
        }
    }
}

extension JSONValue: ExpressibleByStringLiteral, ExpressibleByIntegerLiteral, ExpressibleByFloatLiteral,
    ExpressibleByBooleanLiteral, ExpressibleByArrayLiteral, ExpressibleByDictionaryLiteral {
    public init(stringLiteral value: String) { self = .string(value) }
    public init(integerLiteral value: Int) { self = .number(Double(value)) }
    public init(floatLiteral value: Double) { self = .number(value) }
    public init(booleanLiteral value: Bool) { self = .bool(value) }
    public init(arrayLiteral elements: JSONValue...) { self = .array(elements) }
    public init(dictionaryLiteral elements: (String, JSONValue)...) {
        self = .object(Dictionary(elements, uniquingKeysWith: { $1 }))
    }
}

extension JSONEncoder {
    /// Sorted keys, no escaped slashes: stable bytes for hashing and diffing.
    public static var sorted: JSONEncoder {
        let e = JSONEncoder()
        e.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        return e
    }

    public static var pretty: JSONEncoder {
        let e = JSONEncoder()
        e.outputFormatting = [.sortedKeys, .withoutEscapingSlashes, .prettyPrinted]
        return e
    }
}
