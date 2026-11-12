// `audiotap --mic`: capture the default microphone with AVAudioEngine and stream it to stdout
// as raw float32 mono PCM. stderr: "FORMAT <rate> 1" first. Stops on SIGINT/SIGTERM/stdout close.
// Runs in its own process so the Python app never touches Core Audio for dictation.

import AVFoundation
import Foundation

func streamMic() -> Never {
    let engine = AVAudioEngine()
    let input = engine.inputNode
    let format = input.outputFormat(forBus: 0)
    guard format.sampleRate > 0 else {
        FileHandle.standardError.write("ERROR geen microfoon beschikbaar\n".data(using: .utf8)!)
        exit(1)
    }
    let out = FileHandle.standardOutput
    signal(SIGPIPE, SIG_IGN)
    input.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, _ in
        guard let ch = buffer.floatChannelData else { return }
        let n = Int(buffer.frameLength)
        // Mix all channels to mono.
        var mono = [Float](repeating: 0, count: n)
        let channels = Int(format.channelCount)
        for c in 0..<channels {
            let src = ch[c]
            for i in 0..<n { mono[i] += src[i] / Float(channels) }
        }
        let data = mono.withUnsafeBufferPointer { Data(buffer: $0) }
        do { try out.write(contentsOf: data) } catch { exit(0) }
    }
    do {
        try engine.start()
    } catch {
        FileHandle.standardError.write("ERROR microfoon starten mislukt: \(error)\n".data(using: .utf8)!)
        exit(1)
    }
    let name = AVCaptureDevice.default(for: .audio)?.localizedName ?? "Microfoon"
    FileHandle.standardError.write("FORMAT \(Int(format.sampleRate)) 1 \(name)\n".data(using: .utf8)!)
    for sig in [SIGINT, SIGTERM] {
        signal(sig, SIG_IGN)
        let src = DispatchSource.makeSignalSource(signal: sig, queue: .main)
        src.setEventHandler { engine.stop(); exit(0) }
        src.resume()
        _micSignalSources.append(src)
    }
    dispatchMain()
}

var _micSignalSources: [DispatchSourceSignal] = []
