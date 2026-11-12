// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "audiotap",
    platforms: [.macOS("14.2")],
    targets: [
        .executableTarget(
            name: "audiotap",
            linkerSettings: [
                // Embed an Info.plist so macOS can show a purpose string for audio capture.
                .unsafeFlags(["-Xlinker", "-sectcreate", "-Xlinker", "__TEXT",
                              "-Xlinker", "__info_plist", "-Xlinker", "Sources/audiotap/Info.plist"])
            ]
        )
    ]
)
