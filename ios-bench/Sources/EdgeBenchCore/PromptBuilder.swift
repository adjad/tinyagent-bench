import CryptoKit
import Foundation

/// Builds prompts of an exact templated length (spec 04 §3.1): a system message with a
/// fresh 8-character nonce at the top (defeats any prefix cache) and a user message made of
/// a prefix of the bundled corpus, sized by binary search on the model's own token count.
public struct PromptBuilder: Sendable {
    public let corpus: String
    public let corpusSHA256: String
    /// The corpus as words with their trailing whitespace.
    private let units: [Substring]

    public static let systemPreamble = "You are a helpful on-device assistant. Read the material the user provides and answer briefly."

    public init(corpus: String) {
        self.corpus = corpus
        corpusSHA256 = SHA256.hash(data: Data(corpus.utf8)).map { String(format: "%02x", $0) }.joined()
        // Words with their trailing whitespace, so a prompt can be sized to within a token
        // or two and still keeps the corpus's line breaks.
        var pieces = [Substring]()
        var start = corpus.startIndex
        var i = corpus.startIndex
        var inSpace = false
        while i < corpus.endIndex {
            let space = corpus[i].isWhitespace
            if inSpace && !space { pieces.append(corpus[start ..< i]); start = i }
            inSpace = space
            i = corpus.index(after: i)
        }
        if start < corpus.endIndex { pieces.append(corpus[start...]) }
        units = pieces
    }

    public static func nonce<R: RandomNumberGenerator>(using rng: inout R) -> String {
        let alphabet = Array("abcdefghijkmnpqrstuvwxyz23456789")
        return String((0 ..< 8).map { _ in alphabet[Int(rng.next() % UInt64(alphabet.count))] })
    }

    public func messages(words: Int, nonce: String) -> [BenchMessage] {
        var body = ""
        if words > 0, !units.isEmpty {
            // Repeat the corpus if a level asks for more text than it has.
            var taken = 0
            var parts = [Substring]()
            while taken < words {
                let n = min(units.count, words - taken)
                parts.append(contentsOf: units.prefix(n))
                taken += n
            }
            body = parts.joined().trimmingCharacters(in: .whitespacesAndNewlines)
        }
        return [
            BenchMessage(role: "system", content: "[run \(nonce)] " + Self.systemPreamble),
            BenchMessage(role: "user", content: body + "\n\nIn one sentence, what is the material above about?"),
        ]
    }

    public struct Built: Sendable {
        public var messages: [BenchMessage]
        public var tokens: Int
        public var words: Int
        public var countCalls: Int
    }

    /// Finds the longest corpus prefix whose templated length is ≤ `target`, so the actual
    /// count is `target` or a token or two below it. `count` is the generator's own count.
    public func build(target: Int, nonce: String, thinking: ThinkingMode,
                      count: (_ messages: [BenchMessage]) async throws -> Int) async throws -> Built {
        var calls = 0
        func measure(_ words: Int) async throws -> Int {
            calls += 1
            return try await count(messages(words: words, nonce: nonce))
        }
        let base = try await measure(0)
        guard target > base else {
            return Built(messages: messages(words: 0, nonce: nonce), tokens: base, words: 0, countCalls: calls)
        }
        // Bracket: grow until over the target.
        var lo = 0, loTokens = base
        var hi = max(16, Int(Double(target - base) * 0.8))
        var hiTokens = try await measure(hi)
        while hiTokens <= target {
            lo = hi; loTokens = hiTokens
            hi *= 2
            hiTokens = try await measure(hi)
        }
        while hi - lo > 1 {
            let mid = (lo + hi) / 2
            let t = try await measure(mid)
            if t <= target { lo = mid; loTokens = t } else { hi = mid; hiTokens = t }
        }
        return Built(messages: messages(words: lo, nonce: nonce), tokens: loTokens, words: lo, countCalls: calls)
    }

    /// Tokens per 1,000 characters of the corpus (spec 04 §3.1 tokenizer efficiency).
    public func tokensPer1kChars(count: (_ text: String) async throws -> Int) async throws -> Double {
        let sample = String(corpus.prefix(20_000))
        let n = try await count(sample)
        return Double(n) / Double(sample.count) * 1000
    }
}
