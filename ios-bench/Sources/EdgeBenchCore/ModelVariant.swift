import CryptoKit
import Foundation

/// One model folder on the device, in the same layout oMLX serves on the Mac
/// (`config.json`, `tokenizer.json`, `*.safetensors`, optional `model-mtp.safetensors`).
public struct ModelVariant: Codable, Sendable, Equatable, Identifiable {
    public var id: String                 // folder name, e.g. gemma-4-E4B-it-r5-step-01800-oQ4e-mtp
    public var directory: URL
    public var files: [ModelFile]
    public var facts: ModelFacts

    public var totalBytes: Int64 { files.reduce(0) { $0 + $1.bytes } }

    public init(id: String, directory: URL, files: [ModelFile], facts: ModelFacts) {
        self.id = id
        self.directory = directory
        self.files = files
        self.facts = facts
    }
}

public struct ModelFile: Codable, Sendable, Equatable {
    public var name: String
    public var bytes: Int64
    public var sha256: String?
    public init(name: String, bytes: Int64, sha256: String? = nil) {
        self.name = name
        self.bytes = bytes
        self.sha256 = sha256
    }
}

/// Facts read from config.json and the safetensors headers (never the weights themselves).
public struct ModelFacts: Codable, Sendable, Equatable {
    public var modelType: String?
    public var textModelType: String?
    public var architectures: [String]
    public var numLayers: Int?
    public var hiddenSize: Int?
    public var nativeWindow: Int?
    public var slidingWindow: Int?
    public var quantBits: Int?
    public var quantGroupSize: Int?
    public var quantMode: String?
    /// bits → number of per-layer overrides, e.g. {"5": 93, "6": 14}.
    public var quantMixed: [String: Int]
    public var hasMTPHead: Bool
    /// Tensor bytes by component, from the safetensors headers: text, mtp, vision, audio, other.
    public var tensorBytes: [String: Int64]
    public var hasChatTemplate: Bool
    public var missing: [String]

    /// Bytes a text-only load materialises (text + MTP head when used).
    public func residentWeightBytes(withMTP: Bool) -> Int64 {
        (tensorBytes["text"] ?? 0) + (withMTP ? (tensorBytes["mtp"] ?? 0) : 0)
    }

    public var architecture: String {
        if modelType?.hasPrefix("gemma") == true, (tensorBytes["text_ple"] ?? 0) > 0 { return "dense_ple" }
        return "dense"
    }
}

public enum ModelScanner {
    public static let requiredFiles = ["config.json", "tokenizer.json", "tokenizer_config.json"]

    /// Lists model folders (each a directory with a config.json) directly under `root`.
    public static func scan(root: URL) -> [ModelVariant] {
        let fm = FileManager.default
        guard let entries = try? fm.contentsOfDirectory(at: root, includingPropertiesForKeys: [.isDirectoryKey]) else { return [] }
        return entries.compactMap { dir in
            guard (try? dir.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) == true,
                  fm.fileExists(atPath: dir.appendingPathComponent("config.json").path) else { return nil }
            return try? inspect(directory: dir)
        }.sorted { $0.id < $1.id }
    }

    public static func inspect(directory: URL) throws -> ModelVariant {
        let fm = FileManager.default
        let names = try fm.contentsOfDirectory(atPath: directory.path).filter { !$0.hasPrefix(".") }.sorted()
        var files = [ModelFile]()
        for name in names {
            let url = directory.appendingPathComponent(name)
            let values = try url.resourceValues(forKeys: [.fileSizeKey, .isDirectoryKey])
            if values.isDirectory == true { continue }
            files.append(ModelFile(name: name, bytes: Int64(values.fileSize ?? 0)))
        }
        let facts = readFacts(directory: directory, fileNames: Set(files.map(\.name)))
        return ModelVariant(id: directory.lastPathComponent, directory: directory, files: files, facts: facts)
    }

    static func readFacts(directory: URL, fileNames: Set<String>) -> ModelFacts {
        let config = (try? Data(contentsOf: directory.appendingPathComponent("config.json")))
            .flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] } ?? [:]
        let text = (config["text_config"] as? [String: Any]) ?? config
        let quant = (config["quantization"] as? [String: Any]) ?? [:]
        var mixed = [String: Int]()
        for value in quant.values {
            if let layer = value as? [String: Any], let bits = layer["bits"] as? Int { mixed[String(bits), default: 0] += 1 }
        }
        var tensorBytes = [String: Int64]()
        for name in fileNames where name.hasSuffix(".safetensors") {
            guard let header = try? safetensorsHeader(directory.appendingPathComponent(name)) else { continue }
            for (key, entry) in header where key != "__metadata__" {
                guard let e = entry as? [String: Any], let off = e["data_offsets"] as? [Int], off.count == 2 else { continue }
                let bytes = Int64(off[1] - off[0])
                tensorBytes[component(of: key), default: 0] += bytes
                if key.contains("embed_tokens_per_layer") { tensorBytes["text_ple", default: 0] += bytes }
            }
        }
        let missing = requiredFiles.filter { !fileNames.contains($0) }
            + (fileNames.contains(where: { $0.hasSuffix(".safetensors") }) ? [] : ["*.safetensors"])
        return ModelFacts(
            modelType: config["model_type"] as? String,
            textModelType: text["model_type"] as? String,
            architectures: config["architectures"] as? [String] ?? [],
            numLayers: text["num_hidden_layers"] as? Int,
            hiddenSize: text["hidden_size"] as? Int,
            nativeWindow: text["max_position_embeddings"] as? Int,
            slidingWindow: text["sliding_window"] as? Int,
            quantBits: quant["bits"] as? Int,
            quantGroupSize: quant["group_size"] as? Int,
            quantMode: quant["mode"] as? String,
            quantMixed: mixed,
            hasMTPHead: fileNames.contains("model-mtp.safetensors") || (tensorBytes["mtp"] ?? 0) > 0,
            tensorBytes: tensorBytes,
            hasChatTemplate: fileNames.contains("chat_template.jinja")
                || ((try? Data(contentsOf: directory.appendingPathComponent("tokenizer_config.json")))
                    .flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }?["chat_template"] != nil),
            missing: missing)
    }

    /// Which part of a multimodal checkpoint a tensor belongs to.
    public static func component(of key: String) -> String {
        if key.contains(".mtp.") || key.hasPrefix("mtp.") { return "mtp" }
        if key.hasPrefix("vision_tower") || key.hasPrefix("embed_vision") || key.contains("vision_model") { return "vision" }
        if key.hasPrefix("audio_tower") || key.hasPrefix("embed_audio") { return "audio" }
        if key.hasPrefix("language_model") || key.hasPrefix("model.") || key.hasPrefix("lm_head") { return "text" }
        return "other"
    }

    /// Reads only the JSON header of a safetensors file (8-byte length + JSON).
    public static func safetensorsHeader(_ url: URL) throws -> [String: Any] {
        let handle = try FileHandle(forReadingFrom: url)
        defer { try? handle.close() }
        guard let lenData = try handle.read(upToCount: 8), lenData.count == 8 else { throw CocoaError(.fileReadCorruptFile) }
        let length = lenData.withUnsafeBytes { $0.loadUnaligned(as: UInt64.self) }.littleEndian
        guard length > 0, length < 64 << 20, let json = try handle.read(upToCount: Int(length)) else { throw CocoaError(.fileReadCorruptFile) }
        return (try JSONSerialization.jsonObject(with: json) as? [String: Any]) ?? [:]
    }

    /// SHA-256 of a file, streamed in 8 MiB chunks.
    public static func sha256(of url: URL) throws -> String {
        let handle = try FileHandle(forReadingFrom: url)
        defer { try? handle.close() }
        var hasher = SHA256()
        while let chunk = try handle.read(upToCount: 8 << 20), !chunk.isEmpty {
            try Task.checkCancellation()
            hasher.update(data: chunk)
        }
        return hasher.finalize().map { String(format: "%02x", $0) }.joined()
    }
}

// MARK: - Model entries and roles (spec 04 §1.2, §1.5, §1.6, §3.4)

public enum ModelRole: String, Codable, Sendable, CaseIterable {
    case baseline, reference, candidate, variant
}

/// What a model folder is to the benchmark: its engine id, role and label.
public struct ModelEntry: Codable, Sendable, Equatable {
    /// The engine id the Mac uses (`Gemma-Wisp`, `gemma-4-E4B-it-oQ4e-mtp`, …).
    public var id: String
    public var role: ModelRole
    public var variantOf: String?
    public var label: String
    public init(id: String, role: ModelRole, variantOf: String?, label: String) {
        self.id = id
        self.role = role
        self.variantOf = variantOf
        self.label = label
    }
}

public enum ModelCatalog {
    public static let baselineID = "Gemma-Wisp"
    public static let baselineFolder = "gemma-4-E4B-it-r5-step-01800-oQ4e-mtp"
    public static let referenceID = "gemma-4-E4B-it-oQ4e-mtp"

    /// Classifies a folder by name. The shipped folders map to their engine ids. A smaller
    /// quantisation made for the phone is a fallback **variant** (spec 04 §1.6) when its folder
    /// name ends in `-iphone` and starts with the baseline's or the reference's name, e.g.
    /// `Gemma-Wisp-q3-iphone` or `gemma-4-E4B-it-q3-iphone`.
    public static func entry(folder: String) -> ModelEntry {
        let lower = folder.lowercased()
        if folder == baselineFolder || folder == baselineID {
            return ModelEntry(id: baselineID, role: .baseline, variantOf: nil, label: "Gemma-Wisp (Harbor r5, oQ4e)")
        }
        if folder == referenceID {
            return ModelEntry(id: referenceID, role: .reference, variantOf: nil, label: "Base Gemma 4 E4B (oQ4e)")
        }
        if lower.hasSuffix("-iphone") {
            if lower.hasPrefix("gemma-wisp") || lower.contains("-r5-") {
                let quant = quantName(folder)
                return ModelEntry(id: folder, role: .variant, variantOf: baselineID,
                                  label: "Gemma-Wisp, \(quant) (iPhone fallback; shipped quant did not fit)")
            }
            if lower.hasPrefix("gemma-4-e4b-it") {
                let quant = quantName(folder)
                return ModelEntry(id: folder, role: .variant, variantOf: referenceID,
                                  label: "Base Gemma 4 E4B, \(quant) (iPhone fallback; shipped quant did not fit)")
            }
        }
        if folder.hasPrefix("stub-") {
            return ModelEntry(id: folder, role: .candidate, variantOf: nil, label: "Stub (no model, simulated numbers)")
        }
        return ModelEntry(id: folder, role: .candidate, variantOf: nil, label: folder)
    }

    static func quantName(_ folder: String) -> String {
        let parts = folder.split(separator: "-").map(String.init)
        return parts.first { $0.range(of: #"^(o?q\d|\dbit|q\d)"#, options: [.regularExpression, .caseInsensitive]) != nil } ?? "smaller quant"
    }
}

extension ModelVariant {
    public var entry: ModelEntry { ModelCatalog.entry(folder: id) }

    /// sha256 of a file if it has been hashed (by Verify, or from the pinned manifest).
    public func sha256(of name: String) -> String? { files.first { $0.name == name }?.sha256 }
}
