import SwiftUI

@main
struct EdgeBenchApp: App {
    @State private var model = AppModel()
    @Environment(\.scenePhase) private var phase

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(model)
        }
        .onChange(of: phase) { _, new in
            if new == .background { model.didEnterBackground() }
        }
    }
}
