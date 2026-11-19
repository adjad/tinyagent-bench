import SwiftUI
import UniformTypeIdentifiers

// Plain, legible screens: this is an instrument. Numbers are monospaced, units always shown.

struct RootView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        TabView {
            NavigationStack { ModelsView() }.tabItem { Label("Models", systemImage: "shippingbox") }
            NavigationStack { RunView() }.tabItem { Label("Run", systemImage: "play.circle") }
            NavigationStack { ResultsView() }.tabItem { Label("Results", systemImage: "list.bullet.rectangle") }
        }
        .overlay(alignment: .top) {
            if let msg = model.busyMessage {
                Text(msg).font(.footnote).padding(8).background(.thinMaterial, in: Capsule()).padding(.top, 4)
            }
        }
        .task { model.autoRunIfRequested() }
        .alert("Error", isPresented: Binding(get: { model.lastError != nil }, set: { if !$0 { model.lastError = nil } })) {
            Button("OK") { model.lastError = nil }
        } message: { Text(model.lastError ?? "") }
    }
}

enum Fmt {
    static func bytes(_ b: Int64) -> String { ByteCountFormatter.string(fromByteCount: b, countStyle: .file) }
    static func mib(_ v: Double?) -> String { v.map { String(format: "%.0f MiB", $0) } ?? "—" }
    static func mib(_ b: UInt64?) -> String { mib(b.map { Double($0) / 1_048_576 }) }
    static func num(_ v: Double?, _ digits: Int = 1, _ unit: String = "") -> String {
        v.map { String(format: "%.\(digits)f", $0) + (unit.isEmpty ? "" : " \(unit)") } ?? "—"
    }
}

struct Row: View {
    let label: String
    let value: String
    var body: some View {
        HStack {
            Text(label).foregroundStyle(.secondary)
            Spacer()
            Text(value).monospacedDigit().multilineTextAlignment(.trailing)
        }
    }
}

// MARK: - Models

struct ModelsView: View {
    @Environment(AppModel.self) private var model
    @State private var importing = false

    var body: some View {
        List {
            Section {
                Picker("Engine", selection: Binding(get: { model.engine }, set: { model.switchEngine($0) })) {
                    ForEach(AppModel.Engine.allCases) { e in Text(e.rawValue).tag(e) }
                }
                .disabled(model.isRunning || !AppModel.mlxAvailable)
                if !AppModel.mlxAvailable {
                    Text("MLX needs a real iPhone GPU. This build uses the stub generator.").font(.footnote).foregroundStyle(.secondary)
                }
            }
            Section("On this phone") {
                if model.variants.isEmpty {
                    Text("No model folders yet. Copy one in with Finder (iPhone → Files → tinyagent-bench) or Import below.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                ForEach(model.variants) { v in
                    NavigationLink(value: v.id) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(v.entry.label).font(.body)
                            Text(v.id).font(.caption.monospaced()).lineLimit(2)
                            Text("\(v.entry.role.rawValue) · \(Fmt.bytes(v.totalBytes)) · \(v.facts.quantBits.map { "\($0)-bit" } ?? "?")\(v.facts.hasMTPHead ? " · MTP head" : "") · \(model.packs[v.id] == nil ? "no prompt pack" : "prompt pack")")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
            }
            Section("Phone items") {
                if let items = model.phoneItems {
                    Row(label: "Items", value: "\(items.items.count)")
                    Text(items.manifest?.source ?? "No manifest").font(.caption).foregroundStyle(.secondary)
                } else {
                    Text(model.phoneItemsError ?? "None loaded").font(.caption).foregroundStyle(.orange)
                }
            }
            Section {
                Button("Import a model folder, prompt pack or item file…") { importing = true }
                Button("Rescan") { model.refreshModels() }
            } footer: {
                Text("Model folders as oMLX serves them on the Mac (config.json, tokenizer.json, *.safetensors). Prompt packs (*.json) go to Packs, phone items (*.jsonl) to PhoneItems. Free space: \(Storage.freeBytes.map(Fmt.bytes) ?? "?").")
            }
        }
        .navigationTitle("Models")
        .navigationDestination(for: String.self) { id in
            if let v = model.variants.first(where: { $0.id == id }) { ModelDetailView(variant: v) }
        }
        .fileImporter(isPresented: $importing, allowedContentTypes: [.folder, .json, .data]) { result in
            if case .success(let url) = result { Task { await model.importItem(url) } }
        }
        .refreshable { model.refreshModels() }
    }
}

struct ModelDetailView: View {
    @Environment(AppModel.self) private var model
    let variant: ModelVariant
    @State private var verifyResult: String?

    func quantText(_ f: ModelFacts) -> String {
        var s = "\(f.quantMode ?? "?") \(f.quantBits.map { "\($0)-bit" } ?? "?") g\(f.quantGroupSize.map(String.init) ?? "?")"
        if !f.quantMixed.isEmpty {
            let parts: [String] = f.quantMixed.sorted { $0.key < $1.key }.map { "\($0.value)×\($0.key)-bit" }
            s += " + " + parts.joined(separator: ", ")
        }
        return s
    }

    var body: some View {
        let f = variant.facts
        let e = variant.entry
        List {
            Section("Benchmark entry") {
                Row(label: "Engine id", value: e.id)
                Row(label: "Role", value: e.role.rawValue + (e.variantOf.map { " of \($0)" } ?? ""))
                Row(label: "Prompt pack", value: model.packs[variant.id]?.lastPathComponent ?? "none (stand-in prompts)")
            }
            Section("Facts") {
                Row(label: "Type", value: [f.modelType, f.textModelType].compactMap { $0 }.joined(separator: " / "))
                Row(label: "Layers / hidden", value: "\(f.numLayers.map(String.init) ?? "?") / \(f.hiddenSize.map(String.init) ?? "?")")
                Row(label: "Native window", value: f.nativeWindow.map { "\($0) tokens" } ?? "?")
                Row(label: "Quantisation", value: quantText(f))
                Row(label: "Text weights", value: Fmt.bytes(f.tensorBytes["text"] ?? 0))
                Row(label: "MTP head", value: f.hasMTPHead ? Fmt.bytes(f.tensorBytes["mtp"] ?? 0) + " (not used)" : "none")
                Row(label: "Vision + audio", value: Fmt.bytes((f.tensorBytes["vision"] ?? 0) + (f.tensorBytes["audio"] ?? 0)) + " (not loaded)")
                if !f.missing.isEmpty { Row(label: "Missing", value: f.missing.joined(separator: ", ")) }
            }
            Section("Files") {
                ForEach(variant.files, id: \.name) { file in
                    VStack(alignment: .leading) {
                        Row(label: file.name, value: Fmt.bytes(file.bytes))
                        if let s = file.sha256 { Text(s).font(.caption2.monospaced()).foregroundStyle(.secondary) }
                    }
                }
            }
            Section {
                Button("Verify SHA-256") { Task { verifyResult = await model.verify(variant) } }
                if let r = verifyResult { Text(r).font(.footnote) }
                if !variant.id.hasPrefix("stub-") {
                    Button("Delete from phone", role: .destructive) { model.delete(variant) }
                }
            }
        }
        .navigationTitle(e.id)
        .navigationBarTitleDisplayMode(.inline)
    }
}

// MARK: - Run

struct RunView: View {
    @Environment(AppModel.self) private var model
    @State private var primaryID: String?
    @State private var referenceID: String?
    @State private var planID = TestPlan.builtIn[0].id
    @State private var attest = RecordAttest()
    @State private var rebooted = false
    @State private var ambient = ""

    var plan: TestPlan? { TestPlan.builtIn.first { $0.id == planID } }

    var body: some View {
        List {
            if model.isRunning || model.progress?.phase == "done" {
                Section("Live") { LiveView() }
            }
            Section("Test plan") {
                ForEach(TestPlan.builtIn) { p in
                    Button { planID = p.id } label: {
                        HStack(alignment: .top) {
                            Image(systemName: planID == p.id ? "largecircle.fill.circle" : "circle")
                            VStack(alignment: .leading, spacing: 2) {
                                Text(p.title).foregroundStyle(.primary)
                                Text(p.summary).font(.caption).foregroundStyle(.secondary)
                                Text("About \(p.estimatedMinutes) min").font(.caption2).foregroundStyle(.secondary)
                            }
                        }
                    }
                }
            }
            Section("Models") {
                if plan?.slots.contains(.primary) == true {
                    Picker("Primary", selection: $primaryID) {
                        Text("Choose…").tag(String?.none)
                        ForEach(model.variants) { v in Text(v.entry.id).tag(Optional(v.id)) }
                    }
                }
                if plan?.slots.contains(.reference) == true {
                    Picker("Reference", selection: $referenceID) {
                        Text("Choose…").tag(String?.none)
                        ForEach(model.variants) { v in Text(v.entry.id).tag(Optional(v.id)) }
                    }
                }
                if plan?.slots.isEmpty == true { Text("This plan uses no model.").font(.footnote).foregroundStyle(.secondary) }
            }
            Section("Before you start") {
                PreflightView(variant: model.variants.first { $0.id == primaryID })
                Toggle("Airplane mode on", isOn: $attest.airplane_mode)
                Toggle("No case, on a hard surface", isOn: Binding(get: { attest.no_case && attest.hard_surface },
                                                                    set: { attest.no_case = $0; attest.hard_surface = $0 }))
                Toggle("Auto-brightness off", isOn: $attest.auto_brightness_off)
                Toggle("Other apps closed", isOn: $attest.other_apps_closed)
                Toggle("I rebooted and waited 5 minutes", isOn: $rebooted)
                TextField("Room temperature °C", text: $ambient).keyboardType(.decimalPad)
            }
            Section {
                if model.isRunning {
                    Button("Stop (can resume)", role: .destructive) { model.stop() }
                } else {
                    Button("Start") {
                        guard let plan else { return }
                        var models = [ModelSlot: ModelVariant]()
                        if let v = model.variants.first(where: { $0.id == primaryID }) { models[.primary] = v }
                        if let v = model.variants.first(where: { $0.id == referenceID }) { models[.reference] = v }
                        let input = OperatorAttestation(attest: attest, ambient_C: Double(ambient.replacingOccurrences(of: ",", with: ".")),
                                                        rebooted_before_run: rebooted)
                        model.start(plan: plan, models: models, operatorInput: input)
                    }
                    .disabled(!(plan?.slots ?? []).allSatisfy { slot in slot == .primary ? primaryID != nil : referenceID != nil })
                }
            } footer: {
                Text("The screen stays on at minimum brightness during a run. Leaving the app voids the sample in flight and pauses the run (iPhone apps get no GPU in the background). If iOS kills the app, that sample is recorded as killed and retried once; open the run in Results to resume.")
            }
        }
        .navigationTitle("Run")
        .onAppear {
            if primaryID == nil { primaryID = model.defaultModel(for: .primary)?.id }
            if referenceID == nil { referenceID = model.defaultModel(for: .reference)?.id }
        }
    }
}

struct PreflightView: View {
    @Environment(AppModel.self) private var model
    let variant: ModelVariant?

    var body: some View {
        TimelineView(.periodic(from: .now, by: 2)) { _ in content }
    }

    @ViewBuilder var content: some View {
        let c = model.conditions.read()
        let mem = model.memoryProbe.read()
        let budget = mem.availableMiB.map { $0 + mem.footprintMiB }
        let weights = variant.map { Double($0.facts.residentWeightBytes(withMTP: false)) / 1_048_576 }
        VStack(alignment: .leading, spacing: 4) {
            Row(label: "Battery", value: "\(Fmt.num(c.batteryPct, 0, "%")) · \(c.powerSource.batteryState)")
            Row(label: "Thermal", value: c.thermal.rawValue)
            Row(label: "Low Power Mode", value: c.lowPowerMode ? "ON" : "off")
            Row(label: "Uptime", value: Fmt.num((c.uptimeS ?? 0) / 60, 0, "min"))
            Row(label: "App memory budget", value: Fmt.mib(budget))
            Row(label: "Text weights", value: Fmt.mib(weights))
            if c.powerSource == .charging || c.powerSource == .full { warn("Unplug: runs on the charger are recorded as such and do not count for battery tests.") }
            if c.lowPowerMode { warn("Turn Low Power Mode off; it lowers CPU and GPU clocks.") }
            if let b = c.batteryPct, b < 50 || b > 95 { warn("Start between 50 % and 95 %; runs pause below 30 %.") }
            if (c.uptimeS ?? 9_999) < 600 { warn("Let the phone rest 10 minutes after a boot (except for the cold-load test).") }
            if c.thermal > .nominal { warn("The phone is warm; each sample waits for it to cool (up to 5 minutes).") }
            if let b = budget, let w = weights, w > 0, w + 600 > b {
                warn("The weights plus ~600 MiB exceed this app's budget. A kill is the recorded result; then try the iPhone fallback quant.")
            }
        }
        .font(.callout)
    }

    func warn(_ s: String) -> some View {
        Label(s, systemImage: "exclamationmark.triangle").font(.caption).foregroundStyle(.orange)
    }
}

struct LiveView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        if let p = model.progress {
            VStack(alignment: .leading, spacing: 6) {
                Text(p.label).font(.headline)
                if let id = p.modelID { Text(id).font(.caption.monospaced()) }
                ProgressView(value: Double(min(p.unitIndex, p.unitCount)), total: Double(max(1, p.unitCount)))
                Row(label: "Step", value: "\(min(p.unitIndex + 1, p.unitCount)) of \(p.unitCount) · \(p.phase)")
                Row(label: "Decode now", value: Fmt.num(p.liveDecodeTPS, 1, "tok/s"))
                Row(label: "Last decode / prefill", value: "\(Fmt.num(p.lastDecodeTPS, 1)) / \(Fmt.num(p.lastPrefillTPS, 0)) tok/s")
                Row(label: "Last TTFT", value: Fmt.num(p.lastTTFT, 2, "s"))
                Row(label: "Footprint / available", value: "\(Fmt.mib(p.footprintBytes)) / \(Fmt.mib(p.availableBytes))")
                if let c = p.conditions {
                    Row(label: "Thermal", value: c.thermal.rawValue)
                    Row(label: "Battery", value: "\(Fmt.num(c.batteryPct, 0, "%")) · \(c.powerSource.batteryState)\(c.lowPowerMode ? " · LPM" : "")")
                }
                if let s = p.lastStatus, s != "ok" { Row(label: "Last status", value: s) }
                if let m = p.message { Text(m).font(.footnote).foregroundStyle(.secondary) }
            }
        } else {
            Text("Starting…").foregroundStyle(.secondary)
        }
    }
}

// MARK: - Results

struct ResultsView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        List {
            if !model.recovered.isEmpty {
                Section {
                    Text("The app was terminated during a run. The sample it was on is recorded as killed; open the run to resume.")
                        .font(.footnote)
                }
            }
            ForEach(model.runs, id: \.runID) { m in
                NavigationLink(value: m.runID) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(m.plan.title).font(.body)
                        Text(m.models.map(\.entry.id).joined(separator: ", ")).font(.caption.monospaced()).lineLimit(1)
                        Text("\(m.createdAt.formatted(date: .abbreviated, time: .shortened)) · \(m.state.rawValue)\(m.stopReason.map { " · \($0)" } ?? "")")
                            .font(.caption).foregroundStyle(m.state == .complete ? Color.secondary : Color.orange)
                    }
                }
            }
        }
        .navigationTitle("Results")
        .navigationDestination(for: String.self) { RunDetailView(runID: $0) }
        .onAppear { model.refreshRuns() }
        .refreshable { model.refreshRuns() }
    }
}

struct RunDetailView: View {
    @Environment(AppModel.self) private var model
    let runID: String
    @State private var exportURL: URL?

    var body: some View {
        let m = try? model.store.manifest(runID)
        let samples = model.samples(runID)
        let phone = model.phoneResults(runID)
        List {
            if let m {
                Section("Run") {
                    Row(label: "Plan", value: m.plan.title)
                    ForEach(m.models, id: \.variant.id) { rm in Row(label: rm.entry.role.rawValue, value: rm.entry.id) }
                    Row(label: "Engine", value: "\(m.runtime.name) \(m.runtime.version)")
                    Row(label: "Device", value: "\(m.device.marketingName ?? m.device.model) · \(m.device.os)")
                    Row(label: "App budget at start", value: Fmt.mib(m.device.measuredAppBudgetMiB))
                    Row(label: "State", value: m.state.rawValue + (m.stopReason.map { " (\($0))" } ?? ""))
                    Row(label: "Steps done", value: "\(model.store.nextUnitIndex(runID)) of \(m.unitCount)")
                }
                Section {
                    ForEach(CellPreview.cells(samples)) { c in
                        VStack(alignment: .leading, spacing: 2) {
                            HStack {
                                Text("\(c.test)\(c.level.map { " · \($0)" } ?? "")").font(.headline)
                                Spacer()
                                Text(c.statuses.sorted { $0.key < $1.key }.map { "\($0.key) \($0.value)" }.joined(separator: " "))
                                    .font(.caption.monospaced())
                            }
                            Text(c.model).font(.caption2.monospaced()).foregroundStyle(.secondary)
                            Text("TTFT \(Fmt.num(c.ttftS, 2)) s · prefill \(Fmt.num(c.prefillTPS, 0)) · decode \(Fmt.num(c.decodeTPS, 1)) tok/s · peak \(Fmt.mib(c.peakFootprintBytes))")
                                .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                        }
                    }
                } header: { Text("Samples (preview)") } footer: {
                    Text("Medians of measured samples, for a quick look only. The published numbers come from the Mac aggregator reading the raw samples.")
                }
                if !phone.isEmpty {
                    Section("Phone items (local check, display only)") {
                        Row(label: "Passed", value: "\(phone.filter(\.localPass).count) of \(phone.count)")
                        ForEach(Array(phone.filter { !$0.localPass }.prefix(30).enumerated()), id: \.offset) { pair in
                            let r = pair.element
                            VStack(alignment: .leading) {
                                Text("\(r.id) · trial \(r.trial + 1) · \(r.status)").font(.caption.monospaced())
                                Text(r.localLabels.joined(separator: "; ")).font(.caption2).foregroundStyle(.secondary)
                            }
                        }
                    }
                }
                let events = model.events(runID)
                if !events.isEmpty {
                    Section("Events") {
                        ForEach(Array(events.suffix(50).enumerated()), id: \.offset) { pair in
                            Text("\(pair.element.kind): \(pair.element.detail)").font(.caption)
                        }
                    }
                }
                Section {
                    if m.state != .complete && !model.isRunning {
                        Button("Resume") { model.resume(runID) }
                    }
                    if let url = exportURL {
                        ShareLink(item: url) { Label("Share \(url.lastPathComponent)", systemImage: "square.and.arrow.up") }
                    }
                    Button("Export") {
                        do { exportURL = try model.exportFile(runID) } catch { model.lastError = error.localizedDescription }
                    }
                    Button("Delete run", role: .destructive) { model.delete(run: runID) }
                        .disabled(model.isRunning && model.activeRunID == runID)
                } footer: {
                    Text("A zip with run-<id>.json (\(EDAT.perfSchema), raw samples), the 20 Hz footprint log, phone-item results and a sha256 manifest. Exports also stay in Files → On My iPhone → EDAT → Exports.")
                }
            }
        }
        .navigationTitle("Run")
        .navigationBarTitleDisplayMode(.inline)
    }
}
