// Voicenotes.app launcher: hosts Python *inside* the app process.
//
// Because Python runs in this (bundle) process, macOS treats everything as "Voicenotes":
// menu bar name, Dock icon and privacy permissions (microphone, system audio, input monitoring,
// accessibility) belong to Voicenotes, not to Terminal or a python binary.
//
// Contents/Resources/launcher.json tells it where the Python installation and the project are.
// Without arguments it runs `-m voicenotes.app` (plus e.g. --hidden); with python-style
// arguments (-m/-c/script) it behaves like the python interpreter, so the app can start its own
// background jobs via sys.executable.

import Foundation

func fail(_ msg: String) -> Never {
    FileHandle.standardError.write("Voicenotes: \(msg)\n".data(using: .utf8)!)
    exit(1)
}

let resources = Bundle.main.resourceURL ?? URL(fileURLWithPath: CommandLine.arguments[0])
    .deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("Resources")
guard let data = try? Data(contentsOf: resources.appendingPathComponent("launcher.json")),
      let cfg = (try? JSONSerialization.jsonObject(with: data)) as? [String: String],
      let libpython = cfg["libpython"], let home = cfg["python_home"] else {
    fail("launcher.json ontbreekt of is ongeldig; installeer opnieuw met `voicenotes install-app`")
}

setenv("PYTHONHOME", home, 1)
setenv("PYTHONPATH", resources.appendingPathComponent("boot").path, 1)  // sitecustomize.py
setenv("PYTHONNOUSERSITE", "1", 1)
for (key, env) in [("site_packages", "VOICENOTES_SITE_PACKAGES"), ("src", "VOICENOTES_SRC"),
                   ("audiotap", "VOICENOTES_AUDIOTAP")] {
    if let v = cfg[key] { setenv(env, v, 1) }
}
if let d = cfg["data_dir"], !d.isEmpty { setenv("VOICENOTES_HOME", d, 1) }

guard let handle = dlopen(libpython, RTLD_NOW | RTLD_GLOBAL) else {
    fail("kan Python niet laden (\(libpython)): \(String(cString: dlerror()))")
}
guard let sym = dlsym(handle, "Py_BytesMain") else { fail("Py_BytesMain niet gevonden") }
typealias PyBytesMain = @convention(c) (Int32, UnsafeMutablePointer<UnsafeMutablePointer<CChar>?>?) -> Int32
let pyMain = unsafeBitCast(sym, to: PyBytesMain.self)

var args = CommandLine.arguments
let first = args.count > 1 ? args[1] : ""
// Any interpreter flag (-c, -m, -s, -E, -I, -u, ...) means "act like python": e.g.
// multiprocessing starts helpers as `<sys.executable> -s -c ...`. Only --hidden is ours.
let pythonStyle = (first.hasPrefix("-") && first != "--hidden" && !first.hasPrefix("-psn_"))
    || first.hasSuffix(".py")
var pyArgs: [String]
if pythonStyle {
    pyArgs = args
} else {
    pyArgs = [args[0], "-m", "voicenotes.app"] + args.dropFirst().filter { !$0.hasPrefix("-psn_") }
}
var cArgs: [UnsafeMutablePointer<CChar>?] = pyArgs.map { strdup($0) } + [nil]
exit(pyMain(Int32(pyArgs.count), &cArgs))
