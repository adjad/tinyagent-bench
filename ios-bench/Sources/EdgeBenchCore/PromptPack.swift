import CryptoKit
import Foundation

/// The shared prompt pack (spec 04 §2.3): built once on the Mac per model entry with the
/// model's own chat template and tokenizer, so the phone feeds the exact same token ids.
///
/// File shape (`edat.prompt-pack/0.1`, proposed by this app; the spec fixes the contents, not
/// the layout):
/// ```json
/// {"schema": "edat.prompt-pack/0.1", "model_id": "Gemma-Wisp", "entry_id": "Gemma-Wisp",
///  "thinking": "on", "corpus": "provisional-prose-v0", "version": "0.1", "language": "en",
///  "pack_sha256": "…", "tokenizer_sha256": "…", "template_sha256": "…",
///  "levels": [{"level_tokens": 512, "prefix_ids": [2, 105], "body_ids": [ … ], "suffix_ids": [106, 107],
///              "variants": [{"nonce_ids": [ … ]}, … 16 …]}],
///  "first_token": {"ids": [ … 8 … ]},
///  "agent_replay": {"steps": [{"step": 1, "ids": [ … ]}, … 6 …]}}
/// ```
/// Variant `i` of a level is `prefix_ids + nonce_ids[i] + body_ids[..<n] + suffix_ids`, with
/// `n` chosen so the total is exactly `level_tokens`. A variant may instead give its full
/// `ids` list, which is used as is.
public struct PromptPack: Codable, Sendable, Equatable {
    public struct Variant: Codable, Sendable, Equatable {
        public var nonce_ids: [Int]?
        public var ids: [Int]?
        public init(nonce_ids: [Int]? = nil, ids: [Int]? = nil) {
            self.nonce_ids = nonce_ids
            self.ids = ids
        }
    }
    public struct Level: Codable, Sendable, Equatable {
        public var level_tokens: Int
        public var prefix_ids: [Int]
        public var body_ids: [Int]
        public var suffix_ids: [Int]
        public var variants: [Variant]
    }
    public struct IDs: Codable, Sendable, Equatable {
        public var ids: [Int]
    }
    public struct Step: Codable, Sendable, Equatable {
        public var step: Int
        public var ids: [Int]
    }
    public struct Replay: Codable, Sendable, Equatable {
        public var steps: [Step]
    }

    public var schema: String
    public var model_id: String
    public var entry_id: String?
    public var thinking: String?
    public var corpus: String
    public var version: String
    public var language: String
    public var pack_sha256: String?
    public var tokenizer_sha256: String?
    public var template_sha256: String?
    public var levels: [Level]
    public var first_token: IDs?
    public var agent_replay: Replay?

    public enum PackError: LocalizedError, Equatable {
        case wrongSchema(String)
        case notEnglish(String)
        case noLevel(Int)
        case noVariants(Int)
        case bodyTooShort(level: Int, variant: Int)
        case wrongLength(level: Int, variant: Int, got: Int)
        public var errorDescription: String? {
            switch self {
            case .wrongSchema(let s): "Not a prompt pack (schema \(s))."
            case .notEnglish(let l): "The pack's language is \(l); prompt packs are English only."
            case .noLevel(let l): "The pack has no \(l)-token level."
            case .noVariants(let l): "The \(l)-token level has no variants."
            case .bodyTooShort(let l, let v): "The \(l)-token level's body is too short for variant \(v)."
            case .wrongLength(let l, let v, let g): "Variant \(v) of the \(l)-token level has \(g) tokens."
            }
        }
    }

    /// Decodes and checks a pack file; returns the pack and the sha256 of the file's bytes.
    public static func load(_ data: Data) throws -> (PromptPack, String) {
        let pack = try JSONDecoder().decode(PromptPack.self, from: data)
        guard pack.schema == EDAT.promptPackSchema else { throw PackError.wrongSchema(pack.schema) }
        guard pack.language == "en" else { throw PackError.notEnglish(pack.language) }
        for level in pack.levels {
            for i in level.variants.indices { _ = try pack.ids(level: level.level_tokens, variant: i) }
        }
        return (pack, sha256Hex(data))
    }

    public var levelTokens: [Int] { levels.map(\.level_tokens).sorted() }

    /// The exact prompt for repetition `variant` (variant `i` is used for repetition `i`, wrapping at 16).
    public func ids(level: Int, variant: Int) throws -> [Int] {
        guard let l = levels.first(where: { $0.level_tokens == level }) else { throw PackError.noLevel(level) }
        guard !l.variants.isEmpty else { throw PackError.noVariants(level) }
        let v = variant % l.variants.count
        let out: [Int]
        if let full = l.variants[v].ids {
            out = full
        } else {
            let nonce = l.variants[v].nonce_ids ?? []
            let bodyCount = l.level_tokens - l.prefix_ids.count - nonce.count - l.suffix_ids.count
            guard bodyCount >= 0, bodyCount <= l.body_ids.count else { throw PackError.bodyTooShort(level: level, variant: v) }
            out = l.prefix_ids + nonce + l.body_ids.prefix(bodyCount) + l.suffix_ids
        }
        guard out.count == l.level_tokens else { throw PackError.wrongLength(level: level, variant: v, got: out.count) }
        return out
    }

    public func variantCount(level: Int) -> Int { levels.first { $0.level_tokens == level }?.variants.count ?? 0 }

    /// A synthetic pack for the stub generator (Simulator and tests). Token ids are arbitrary
    /// and mean nothing; the record says so (`corpus: "synthetic-stub"`).
    public static func synthetic(modelID: String, levels: [Int], variants: Int = 16) -> PromptPack {
        var rng = SplitMix64(seed: 0xED47)
        func ids(_ n: Int) -> [Int] { (0 ..< n).map { _ in Int(rng.next() % 200_000) + 10 } }
        let body = ids((levels.max() ?? 512) + 64)
        return PromptPack(
            schema: EDAT.promptPackSchema, model_id: modelID, entry_id: modelID, thinking: "on",
            corpus: "synthetic-stub", version: "0", language: "en", pack_sha256: nil,
            tokenizer_sha256: nil, template_sha256: nil,
            levels: levels.map { l in
                Level(level_tokens: l, prefix_ids: [2, 105], body_ids: body, suffix_ids: [106, 107],
                      variants: (0 ..< variants).map { _ in Variant(nonce_ids: ids(6)) })
            },
            first_token: IDs(ids: ids(8)),
            agent_replay: Replay(steps: (1 ... 6).map { Step(step: $0, ids: ids(900 + 350 * $0)) }))
    }
}

public func sha256Hex(_ data: Data) -> String {
    SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
}

/// Allocates memory for the budget calibration (spec 04 §2.9). The app's version allocates
/// 64 MiB shared `MTLBuffer`s and writes every page; tests use `StubBalloonAllocator`.
public protocol BalloonAllocator: AnyObject, Sendable {
    /// Allocates and touches `bytes` more. Returns false when the allocator itself refuses.
    func allocate(bytes: Int) throws -> Bool
    func releaseAll()
}
