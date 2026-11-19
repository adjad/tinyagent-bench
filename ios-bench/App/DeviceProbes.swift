import Foundation
import Metal
import UIKit

/// UIKit's battery, thermal and power-mode readings, cached under a lock so the runner can
/// read them from any thread (UIDevice is main-actor only). Refreshed on every system
/// notification and every 2 s.
final class UIKitConditionsProbe: ConditionsProbe, @unchecked Sendable {
    private let lock = NSLock()
    private var current = Conditions(thermal: .unknown, powerSource: .unknown, batteryPct: nil, lowPowerMode: false)
    private var timer: Timer?

    @MainActor
    init() {
        UIDevice.current.isBatteryMonitoringEnabled = true
        refresh()
        let nc = NotificationCenter.default
        for name in [UIDevice.batteryLevelDidChangeNotification, UIDevice.batteryStateDidChangeNotification,
                     ProcessInfo.thermalStateDidChangeNotification, Notification.Name.NSProcessInfoPowerStateDidChange] {
            nc.addObserver(forName: name, object: nil, queue: .main) { [weak self] _ in
                MainActor.assumeIsolated { self?.refresh() }
            }
        }
        timer = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.refresh() }
        }
    }

    @MainActor
    func refresh() {
        let d = UIDevice.current
        let source: PowerSource = switch d.batteryState {
        case .unplugged: .battery
        case .charging: .charging
        case .full: .full
        default: .unknown
        }
        let level = d.batteryLevel
        let app: String = switch UIApplication.shared.applicationState {
        case .active: "active"
        case .inactive: "inactive"
        case .background: "background"
        @unknown default: "unknown"
        }
        let c = Conditions(
            thermal: ThermalLevel(ProcessInfo.processInfo.thermalState), powerSource: source,
            batteryPct: level < 0 ? nil : Double(level) * 100,
            lowPowerMode: ProcessInfo.processInfo.isLowPowerModeEnabled,
            screenBrightness: Double(UIScreen.main.brightness), appState: app,
            uptimeS: ProcessInfo.processInfo.systemUptime)
        lock.withLock { current = c }
    }

    func read() -> Conditions {
        var c = lock.withLock { current }
        c.thermal = ThermalLevel(ProcessInfo.processInfo.thermalState)   // thread-safe, read fresh
        c.uptimeS = ProcessInfo.processInfo.systemUptime
        return c
    }
}

/// The budget balloon (spec 04 §2.9): 64 MiB shared `MTLBuffer`s, as MLX allocates, with
/// every page written so it counts in `phys_footprint`.
final class MetalBalloonAllocator: BalloonAllocator, @unchecked Sendable {
    private let device: MTLDevice
    private let lock = NSLock()
    private var buffers: [MTLBuffer] = []

    init?() {
        guard let d = MTLCreateSystemDefaultDevice() else { return nil }
        device = d
    }

    func allocate(bytes: Int) throws -> Bool {
        guard let b = device.makeBuffer(length: bytes, options: .storageModeShared) else { return false }
        let page = Int(getpagesize())
        let p = b.contents().assumingMemoryBound(to: UInt8.self)
        var i = 0
        while i < bytes { p[i] = UInt8(truncatingIfNeeded: i / page); i += page }
        lock.withLock { buffers.append(b) }
        return true
    }

    func releaseAll() { lock.withLock { buffers.removeAll() } }
}

enum Provisioning {
    /// Entitlements the embedded provisioning profile grants (what the team was allowed),
    /// read from the profile's plist. A Simulator build has no profile.
    static func entitlements() -> [String: Bool] {
        let keys = ["com.apple.developer.kernel.increased-memory-limit",
                    "com.apple.developer.kernel.extended-virtual-addressing",
                    "get-task-allow"]
        guard let url = Bundle.main.url(forResource: "embedded", withExtension: "mobileprovision"),
              let data = try? Data(contentsOf: url),
              let text = String(data: data, encoding: .isoLatin1),
              let start = text.range(of: "<?xml"), let end = text.range(of: "</plist>"),
              let plist = try? PropertyListSerialization.propertyList(from: Data(text[start.lowerBound ..< end.upperBound].utf8), format: nil) as? [String: Any],
              let ents = plist["Entitlements"] as? [String: Any]
        else { return ["provisioning_profile_present": false] }
        var out = ["provisioning_profile_present": true]
        for k in keys { out[k] = (ents[k] as? Bool) ?? false }
        return out
    }
}

enum Storage {
    static var freeBytes: Int64? {
        let url = URL(fileURLWithPath: NSHomeDirectory())
        return (try? url.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey]))?.volumeAvailableCapacityForImportantUsage
    }
}
