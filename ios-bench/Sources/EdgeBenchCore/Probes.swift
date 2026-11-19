import Darwin
import Foundation
#if canImport(os)
import os
#endif

// Everything the runner reads about the device. The protocols keep the core testable
// on the Mac; the app supplies the UIKit-backed conditions probe on the phone.

public struct MemoryReading: Codable, Sendable, Equatable {
    /// `task_vm_info.phys_footprint`: the number jetsam judges an iOS app by.
    public var footprintBytes: UInt64
    /// `task_vm_info.ledger_phys_footprint_peak`: the kernel's lifetime high-water mark.
    public var lifetimePeakBytes: UInt64?
    /// `os_proc_available_memory()`: bytes left before this app hits its limit (iOS only).
    public var availableBytes: UInt64?
    public var residentBytes: UInt64?

    public init(footprintBytes: UInt64, lifetimePeakBytes: UInt64? = nil, availableBytes: UInt64? = nil, residentBytes: UInt64? = nil) {
        self.footprintBytes = footprintBytes
        self.lifetimePeakBytes = lifetimePeakBytes
        self.availableBytes = availableBytes
        self.residentBytes = residentBytes
    }

    public var footprintMiB: Double { Double(footprintBytes) / 1_048_576 }
    public var availableMiB: Double? { availableBytes.map { Double($0) / 1_048_576 } }
}

public protocol MemoryProbe: Sendable {
    func read() -> MemoryReading
}

/// The real probe: Mach `task_info(TASK_VM_INFO)` on this process, plus
/// `os_proc_available_memory()` where the SDK has it.
public struct TaskMemoryProbe: MemoryProbe {
    public init() {}

    public func read() -> MemoryReading {
        var info = task_vm_info_data_t()
        var count = mach_msg_type_number_t(MemoryLayout<task_vm_info_data_t>.size / MemoryLayout<natural_t>.size)
        let kr = withUnsafeMutablePointer(to: &info) {
            $0.withMemoryRebound(to: integer_t.self, capacity: Int(count)) {
                task_info(mach_task_self_, task_flavor_t(TASK_VM_INFO), $0, &count)
            }
        }
        var available: UInt64?
        #if os(iOS) && !targetEnvironment(macCatalyst)
        available = UInt64(os_proc_available_memory())
        #endif
        guard kr == KERN_SUCCESS else { return MemoryReading(footprintBytes: 0, availableBytes: available) }
        // ledger_phys_footprint_peak exists from TASK_VM_INFO revision 2 on.
        let peakRevCount = mach_msg_type_number_t(
            (MemoryLayout<task_vm_info_data_t>.offset(of: \task_vm_info_data_t.ledger_phys_footprint_peak)! + 8)
                / MemoryLayout<natural_t>.size)
        return MemoryReading(
            footprintBytes: info.phys_footprint,
            lifetimePeakBytes: count >= peakRevCount ? UInt64(info.ledger_phys_footprint_peak) : nil,
            availableBytes: available,
            residentBytes: info.resident_size)
    }
}

public enum PowerSource: String, Codable, Sendable {
    case battery, charging, full, unknown
}

public enum ThermalLevel: String, Codable, Sendable, Comparable {
    case nominal, fair, serious, critical, unknown

    public init(_ state: ProcessInfo.ThermalState) {
        switch state {
        case .nominal: self = .nominal
        case .fair: self = .fair
        case .serious: self = .serious
        case .critical: self = .critical
        @unknown default: self = .unknown
        }
    }

    var rank: Int { [.nominal: 0, .fair: 1, .serious: 2, .critical: 3, .unknown: 4][self]! }
    public static func < (a: ThermalLevel, b: ThermalLevel) -> Bool { a.rank < b.rank }
}

/// Conditions recorded with every sample and every unit (spec 04 §2.2, §3.5).
public struct Conditions: Codable, Sendable, Equatable {
    public var thermal: ThermalLevel
    public var powerSource: PowerSource
    /// 0…100, nil when the OS reports unknown (Simulator, or monitoring off).
    public var batteryPct: Double?
    public var lowPowerMode: Bool
    public var screenBrightness: Double?
    /// `active` | `inactive` | `background` (UIApplication.applicationState).
    public var appState: String?
    public var uptimeS: Double?
    public init(thermal: ThermalLevel, powerSource: PowerSource, batteryPct: Double?, lowPowerMode: Bool,
                screenBrightness: Double? = nil, appState: String? = nil, uptimeS: Double? = nil) {
        self.thermal = thermal
        self.powerSource = powerSource
        self.batteryPct = batteryPct
        self.lowPowerMode = lowPowerMode
        self.screenBrightness = screenBrightness
        self.appState = appState
        self.uptimeS = uptimeS
    }

    /// 0–1, the record's unit.
    public var batteryLevel: Double? { batteryPct.map { $0 / 100 } }
}

extension PowerSource {
    /// `UIDevice.BatteryState` spelled as the record wants it.
    public var batteryState: String {
        switch self {
        case .battery: "unplugged"
        case .charging: "charging"
        case .full: "full"
        case .unknown: "unknown"
        }
    }
}

public protocol ConditionsProbe: Sendable {
    func read() -> Conditions
}

/// Mac/test fallback: thermal state and Low Power Mode from ProcessInfo, battery unknown.
public struct ProcessInfoConditionsProbe: ConditionsProbe {
    public init() {}
    public func read() -> Conditions {
        Conditions(thermal: ThermalLevel(ProcessInfo.processInfo.thermalState), powerSource: .unknown,
                   batteryPct: nil, lowPowerMode: ProcessInfo.processInfo.isLowPowerModeEnabled,
                   appState: "active", uptimeS: ProcessInfo.processInfo.systemUptime)
    }
}

/// Device identity (spec 04 §7.1 "Device"), collected once per run.
public struct DeviceIdentity: Codable, Sendable, Equatable {
    public var deviceClass: String
    public var model: String               // hw.machine, e.g. iPhone18,4
    public var marketingName: String?
    public var soc: String?
    public var ramGB: Double
    public var os: String                  // "iOS 26.1 (23B85)"
    public var isSimulator: Bool
    public var measuredAppBudgetMiB: Double?
    public var budgetSource: String?
    public var entitlements: [String: Bool]
    public var storageFreeGB: Double?
    public var ramBytes: UInt64 = ProcessInfo.processInfo.physicalMemory
    public var osBuild: String? = DeviceIdentity.sysctlString("kern.osversion")
    public var footprintAtStartBytes: UInt64?
    public var availableAtStartBytes: UInt64?

    public init(deviceClass: String, model: String, marketingName: String?, soc: String?, ramGB: Double, os: String,
                isSimulator: Bool, measuredAppBudgetMiB: Double?, budgetSource: String?, entitlements: [String: Bool],
                storageFreeGB: Double?) {
        self.deviceClass = deviceClass
        self.model = model
        self.marketingName = marketingName
        self.soc = soc
        self.ramGB = ramGB
        self.os = os
        self.isSimulator = isSimulator
        self.measuredAppBudgetMiB = measuredAppBudgetMiB
        self.budgetSource = budgetSource
        self.entitlements = entitlements
        self.storageFreeGB = storageFreeGB
    }

    public static func sysctlString(_ name: String) -> String? {
        var size = 0
        guard sysctlbyname(name, nil, &size, nil, 0) == 0, size > 0 else { return nil }
        var buffer = [CChar](repeating: 0, count: size)
        guard sysctlbyname(name, &buffer, &size, nil, 0) == 0 else { return nil }
        return String(cString: buffer)
    }

    /// `hw.machine`; in the Simulator, the simulated model from the environment.
    public static var machine: String {
        if let sim = ProcessInfo.processInfo.environment["SIMULATOR_MODEL_IDENTIFIER"] { return sim }
        var u = utsname()
        uname(&u)
        return withUnsafeBytes(of: &u.machine) { String(decoding: $0.prefix { $0 != 0 }, as: UTF8.self) }
    }

    /// Known phones (the Air's identifier: theapplewiki.com/wiki/IPhone18,4).
    /// Device class: `iphone-ref` is the benchmark's reference iPhone Air (spec 04 §1.1);
    /// other phones are recorded but are not the reference class.
    public static let knownModels: [String: (name: String, soc: String, cls: String)] = [
        "iPhone18,4": ("iPhone Air", "A19 Pro", "iphone-ref"),
        "iPhone18,1": ("iPhone 17 Pro", "A19 Pro", "iphone-other"),
        "iPhone18,2": ("iPhone 17 Pro Max", "A19 Pro", "iphone-other"),
        "iPhone18,3": ("iPhone 17", "A19", "iphone-other"),
        "iPhone17,1": ("iPhone 16 Pro", "A18 Pro", "iphone-other"),
        "iPhone17,2": ("iPhone 16 Pro Max", "A18 Pro", "iphone-other"),
        "iPhone17,3": ("iPhone 16", "A18", "iphone-other"),
        "iPhone17,4": ("iPhone 16 Plus", "A18", "iphone-other"),
        "iPhone16,1": ("iPhone 15 Pro", "A17 Pro", "iphone-other"),
        "iPhone16,2": ("iPhone 15 Pro Max", "A17 Pro", "iphone-other"),
    ]

    public static func osDescription() -> String {
        let v = ProcessInfo.processInfo.operatingSystemVersion
        #if os(iOS)
        let name = "iOS"
        #else
        let name = "macOS"
        #endif
        let build = sysctlString("kern.osversion").map { " (\($0))" } ?? ""
        return "\(name) \(v.majorVersion).\(v.minorVersion).\(v.patchVersion)\(build)"
    }

    /// Identity from the system; the per-app budget is footprint + available memory now.
    public static func collect(memory: MemoryReading, entitlements: [String: Bool], storageFreeBytes: Int64?) -> DeviceIdentity {
        let machine = machine
        let known = knownModels[machine]
        let ramGB = (Double(ProcessInfo.processInfo.physicalMemory) / 1_073_741_824 * 10).rounded() / 10
        #if targetEnvironment(simulator)
        let simulator = true
        #else
        let simulator = false
        #endif
        let budget = memory.availableBytes.map { Double($0 + memory.footprintBytes) / 1_048_576 }
        var d = DeviceIdentity(
            deviceClass: simulator ? "simulator" : (known?.cls ?? "iphone-other"),
            model: machine, marketingName: known?.name, soc: known?.soc, ramGB: ramGB,
            os: osDescription(), isSimulator: simulator,
            measuredAppBudgetMiB: budget, budgetSource: budget == nil ? nil : "os_proc_available_memory+phys_footprint@run_start",
            entitlements: entitlements,
            storageFreeGB: storageFreeBytes.map { Double($0) / 1e9 })
        d.footprintAtStartBytes = memory.footprintBytes
        d.availableAtStartBytes = memory.availableBytes
        return d
    }

    /// "iPhone Air 12 GB": the phone-result `device` string (spec 02 §6.3).
    public var shortDescription: String {
        "\(marketingName ?? model) \(Int(ramGB.rounded())) GB"
    }
}

/// `getrusage` block-input count: on a cold load the weights come off flash, so this rises
/// by roughly the weight size / block size. Used as cold-load evidence (spec 04 §4.1 cold-check);
/// whether iOS counts mmap page-ins here is unverified.
public enum DiskReadProbe {
    public static func blockInputs() -> Int64 {
        var usage = rusage()
        guard getrusage(RUSAGE_SELF, &usage) == 0 else { return -1 }
        return Int64(usage.ru_inblock)
    }
}
