// swift-tools-version: 5.9

import PackageDescription

let package = Package(
    name: "Vox",
    platforms: [
        .macOS(.v14),
    ],
    products: [
        .executable(name: "VoxDaemon", targets: ["VoxDaemon"]),
        .library(name: "CorrectionObserver", targets: ["CorrectionObserver"]),
        .library(name: "HotkeyListener", targets: ["HotkeyListener"]),
    ],
    targets: [
        .executableTarget(
            name: "VoxDaemon",
            dependencies: [
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
            path: "Sources/HotkeyListener"
        ),
        .testTarget(
            name: "VoxTests",
            dependencies: [
                "CorrectionObserver",
                "HotkeyListener",
            ],
            path: "Tests"
        ),
    ]
)
