// Local-only OCR helper. Source text is returned as data, never interpreted.
import Foundation
import Vision
import ImageIO

let args = Array(CommandLine.arguments.dropFirst())
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = true
do {
    let supported = try request.supportedRecognitionLanguages()
    let languages = ["zh-Hans", "zh-Hant", "en-US"].filter { supported.contains($0) }
    request.recognitionLanguages = languages
    if #available(macOS 13.0, *) { request.automaticallyDetectsLanguage = true }
    if args == ["--probe"] {
        let data = try JSONSerialization.data(withJSONObject: ["engine": "vision", "languages": languages])
        print(String(decoding: data, as: UTF8.self))
    } else {
        var images: [[String: Any]] = []
        for path in args {
            var result: [String: Any] = ["image": path, "languages": languages]
            do {
                let url = URL(fileURLWithPath: path)
                guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
                      let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [String: Any],
                      let width = properties[kCGImagePropertyPixelWidth as String] as? NSNumber,
                      let height = properties[kCGImagePropertyPixelHeight as String] as? NSNumber,
                      width.intValue > 0, height.intValue > 0,
                      width.intValue <= 10000, height.intValue <= 10000,
                      width.intValue * height.intValue <= 40000000 else {
                    throw NSError(domain: "ResumeOCR", code: 1,
                                  userInfo: [NSLocalizedDescriptionKey: "Unreadable image or pixel limit exceeded"])
                }
                let handler = VNImageRequestHandler(url: url, options: [:])
                try handler.perform([request])
                result["blocks"] = (request.results ?? []).compactMap { observation -> [String: Any]? in
                    guard let candidate = observation.topCandidates(1).first else { return nil }
                    let box = observation.boundingBox
                    return ["text": candidate.string, "confidence": candidate.confidence,
                            "bbox": [box.minX, box.minY, box.width, box.height]]
                }
            } catch {
                result["error"] = error.localizedDescription
                result["blocks"] = []
            }
            images.append(result)
        }
        let data = try JSONSerialization.data(withJSONObject: ["engine": "vision", "images": images])
        print(String(decoding: data, as: UTF8.self))
    }
} catch {
    fputs("Local Vision OCR unavailable: \(error.localizedDescription)\n", stderr)
    exit(1)
}
