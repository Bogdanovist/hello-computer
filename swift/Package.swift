// swift-tools-version: 5.9

import PackageDescription

let package = Package(
    name: "Vox",
    platforms: [
        .macOS(.v14),
    ],
    products: [
        .executable(name: "VoxDaemon", targets: ["VoxDaemon"]),
        .library(name: "VoxCore", targets: ["VoxCore"]),
        .library(name: "CorrectionObserver", targets: ["CorrectionObserver"]),
        .library(name: "HotkeyListener", targets: ["HotkeyListener"]),
    ],
    targets: [
        .target(
            name: "VoxCore",
            path: "Sources/VoxCore"
        ),
        .executableTarget(
            name: "VoxDaemon",
            dependencies: [
                "VoxCore",
                "CorrectionObserver",
                "HotkeyListener",
            ],
            path: "Sources/VoxDaemon"
        ),
        .target(
            name: "CorrectionObserver",
            path: "Sources/CorrectionObserver"
        ),
        .target(
            name: "HotkeyListener",
            dependencies: [
                "CWhisper",
            ],
            path: "Sources/HotkeyListener"
        ),
        .systemLibrary(
            name: "CWhisper",
            path: "Sources/CWhisper"
        ),
        .testTarget(
            name: "VoxTests",
            dependencies: [
                "VoxCore",
                "CorrectionObserver",
                "HotkeyListener",
            ],
            path: "Tests"
        ),
    ]
)
