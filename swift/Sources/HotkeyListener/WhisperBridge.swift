import Foundation
#if canImport(AVFoundation)
import AVFoundation
#endif
#if canImport(CWhisper)
import CWhisper
#endif

/// Loads a whisper.cpp model and transcribes audio files.
///
/// Metal GPU acceleration is used automatically on Apple Silicon.
/// After transcription, the input audio file is deleted immediately.
public final class WhisperBridge {

    // MARK: - Types

    public enum WhisperError: Error {
        case modelLoadFailed(String)
        case transcriptionFailed
        case audioLoadFailed(String)
    }

    // MARK: - Properties

    /// Optional logging callback. Messages are content-free (no transcribed text).
    public var logHandler: ((String) -> Void)?

    /// File path to the loaded whisper model.
    public let modelPath: String

    #if canImport(CWhisper)
    private var context: OpaquePointer?
    #endif

    // MARK: - Lifecycle

    /// Initialize WhisperBridge by loading a whisper.cpp model.
    ///
    /// - Parameters:
    ///   - modelName: Model identifier (e.g., "large-v3-turbo.en" or "base.en").
    ///     Resolved to `{modelsDirectory}/ggml-{modelName}.bin`.
    ///   - modelsDirectory: Directory containing model files.
    ///     Defaults to `~/.vox/models`.
    /// - Throws: `WhisperError.modelLoadFailed` if the model cannot be loaded.
    public init(modelName: String, modelsDirectory: String? = nil) throws {
        let modelsDir = modelsDirectory
            ?? FileManager.default.homeDirectoryForCurrentUser
                .appendingPathComponent(".vox/models").path
        self.modelPath = "\(modelsDir)/ggml-\(modelName).bin"

        #if canImport(CWhisper)
        var cparams = whisper_context_default_params()
        cparams.use_gpu = true  // Enable Metal GPU acceleration

        guard let ctx = whisper_init_from_file_with_params(modelPath, cparams) else {
            throw WhisperError.modelLoadFailed(modelPath)
        }
        self.context = ctx
        logHandler?("WhisperBridge: model loaded")
        #else
        logHandler?("WhisperBridge: CWhisper not available — transcription disabled")
        #endif
    }

    deinit {
        #if canImport(CWhisper)
        if let ctx = context {
            whisper_free(ctx)
        }
        #endif
    }

    // MARK: - Transcription

    /// Transcribe an audio file and delete it after processing.
    ///
    /// - Parameter audioFileURL: Path to a 16kHz mono 16-bit PCM WAV file.
    /// - Returns: Transcribed text, or `nil` if transcription produced no output.
    /// - Throws: `WhisperError` if transcription fails.
    ///
    /// The audio file is deleted immediately after processing completes,
    /// regardless of whether transcription succeeds or fails.
    public func transcribe(audioFileURL: URL) throws -> String? {
        defer {
            AudioCapture.deleteTempFile(audioFileURL)
            logHandler?("WhisperBridge: temp audio file deleted")
        }

        let samples = try loadAudioSamples(from: audioFileURL)

        guard !samples.isEmpty else {
            logHandler?("WhisperBridge: audio file contained no samples")
            return nil
        }

        #if canImport(CWhisper)
        guard let ctx = context else {
            throw WhisperError.transcriptionFailed
        }

        var params = whisper_full_default_params(WHISPER_SAMPLING_GREEDY)
        params.print_progress = false
        params.print_timestamps = false
        params.single_segment = false

        // Run transcription with language scoped to the whisper_full call
        let result: Int32 = "en".withCString { langPtr in
            params.language = langPtr
            return samples.withUnsafeBufferPointer { buffer in
                whisper_full(ctx, params, buffer.baseAddress, Int32(buffer.count))
            }
        }

        guard result == 0 else {
            logHandler?("WhisperBridge: whisper_full returned error code \(result)")
            throw WhisperError.transcriptionFailed
        }

        let segmentCount = whisper_full_n_segments(ctx)
        var text = ""
        for i in 0..<segmentCount {
            if let segmentText = whisper_full_get_segment_text(ctx, i) {
                text += String(cString: segmentText)
            }
        }

        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        logHandler?("WhisperBridge: transcription complete (\(trimmed.count) chars)")
        return trimmed.isEmpty ? nil : trimmed
        #else
        logHandler?("WhisperBridge: CWhisper not available — returning nil")
        return nil
        #endif
    }

    // MARK: - Audio Loading

    /// Load audio samples from a WAV file as Float32 normalized to [-1.0, 1.0].
    private func loadAudioSamples(from url: URL) throws -> [Float] {
        #if canImport(AVFoundation)
        return try loadSamplesWithAVFoundation(from: url)
        #else
        return try loadSamplesFromWAV(from: url)
        #endif
    }

    #if canImport(AVFoundation)
    /// Load and convert audio samples using AVFoundation.
    private func loadSamplesWithAVFoundation(from url: URL) throws -> [Float] {
        let audioFile: AVAudioFile
        do {
            audioFile = try AVAudioFile(forReading: url)
        } catch {
            throw WhisperError.audioLoadFailed("Failed to open audio file")
        }

        guard let format = AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: 16000,
            channels: 1,
            interleaved: false
        ) else {
            throw WhisperError.audioLoadFailed("Failed to create target audio format")
        }

        let frameCount = AVAudioFrameCount(audioFile.length)
        guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frameCount) else {
            throw WhisperError.audioLoadFailed("Failed to create audio buffer")
        }

        do {
            try audioFile.read(into: buffer)
        } catch {
            throw WhisperError.audioLoadFailed("Failed to read audio data")
        }

        guard let floatData = buffer.floatChannelData else {
            throw WhisperError.audioLoadFailed("No float channel data in buffer")
        }

        let samples = Array(UnsafeBufferPointer(
            start: floatData[0],
            count: Int(buffer.frameLength)
        ))

        logHandler?("WhisperBridge: loaded \(samples.count) audio samples")
        return samples
    }
    #endif

    /// Load audio samples by parsing WAV header directly (fallback for non-macOS).
    /// Expects: 16kHz, mono, 16-bit signed little-endian PCM.
    private func loadSamplesFromWAV(from url: URL) throws -> [Float] {
        let data: Data
        do {
            data = try Data(contentsOf: url)
        } catch {
            throw WhisperError.audioLoadFailed("Failed to read file")
        }

        // Standard WAV header is 44 bytes minimum
        guard data.count > 44 else {
            throw WhisperError.audioLoadFailed("File too small to be a valid WAV")
        }

        // Skip the 44-byte WAV header, read 16-bit PCM samples
        let pcmData = data.subdata(in: 44..<data.count)
        let sampleCount = pcmData.count / 2  // 16-bit = 2 bytes per sample

        var samples = [Float](repeating: 0, count: sampleCount)
        pcmData.withUnsafeBytes { rawBuffer in
            let int16Buffer = rawBuffer.bindMemory(to: Int16.self)
            for i in 0..<sampleCount {
                samples[i] = Float(int16Buffer[i]) / 32768.0
            }
        }

        logHandler?("WhisperBridge: loaded \(samples.count) audio samples (WAV parser)")
        return samples
    }
}
