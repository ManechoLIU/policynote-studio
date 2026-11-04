#!/usr/bin/env swift
// Deterministic extraction from approved reference pixels. No model or network calls.
// Text/button masks sample blank rows from the SAME image. Decorative artwork is never masked.
import Foundation
import CoreGraphics
import ImageIO
import UniformTypeIdentifiers

struct Box { let x: Int; let y: Int; let width: Int; let height: Int }
struct Plan {
    let page: String
    let crop: Box
    let masks: [Box]
    let protected: [Box]
    let featherLeft: Int
    let featherRight: Int
}
struct Bitmap {
    let width: Int
    let height: Int
    var bytes: [UInt8]
    init(url: URL) throws {
        guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
              let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else { throw Failure.message("Cannot decode \(url.path)") }
        width = image.width; height = image.height
        bytes = [UInt8](repeating: 0, count: width * height * 4)
        let success = bytes.withUnsafeMutableBytes { storage -> Bool in
            guard let context = CGContext(data: storage.baseAddress, width: width, height: height,
                bitsPerComponent: 8, bytesPerRow: width * 4, space: CGColorSpaceCreateDeviceRGB(),
                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue | CGBitmapInfo.byteOrder32Big.rawValue) else { return false }
            context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
            return true
        }
        guard success else { throw Failure.message("Cannot rasterize \(url.path)") }
    }
    init(width: Int, height: Int) { self.width = width; self.height = height; bytes = [UInt8](repeating: 0, count: width * height * 4) }
    func index(_ x: Int, _ y: Int) -> Int { (y * width + x) * 4 }
    func cropped(_ box: Box) -> Bitmap {
        var output = Bitmap(width: box.width, height: box.height)
        for y in 0..<box.height {
            let source = index(box.x, box.y + y)
            let destination = output.index(0, y)
            output.bytes[destination..<(destination + box.width * 4)] = bytes[source..<(source + box.width * 4)]
        }
        return output
    }
    mutating func clear(_ box: Box, from original: Bitmap) {
        // Vertical interpolation of the two uncontaminated source rows nearest this mask.
        // The outer six-pixel band blends into original paper; it contains no foreground pixels.
        let sampleTop = max(0, box.y - 5)
        let sampleBottom = min(height - 1, box.y + box.height + 5)
        for y in box.y..<(box.y + box.height) {
            let t = Double(y - sampleTop) / Double(sampleBottom - sampleTop)
            for x in box.x..<(box.x + box.width) {
                let edge = min(min(x - box.x, box.x + box.width - 1 - x), min(y - box.y, box.y + box.height - 1 - y))
                let blendBand = box.y == 382 ? 18.0 : box.y == 680 ? 12.0 : 6.0
                let mix = min(1, Double(edge + 1) / blendBand)
                let destination = index(x, y)
                let upper = original.index(x, sampleTop), lower = original.index(x, sampleBottom)
                for channel in 0..<3 {
                    let sampled = Double(original.bytes[upper + channel]) * (1 - t) + Double(original.bytes[lower + channel]) * t
                    let value = Double(original.bytes[destination + channel]) * (1 - mix) + sampled * mix
                    bytes[destination + channel] = UInt8(clamping: Int(value.rounded()))
                }
            }
        }
    }
    mutating func feather(left: Int, right: Int) {
        for y in 0..<height { for x in 0..<width {
            let l = left > 0 ? min(1, Double(x) / Double(left)) : 1
            let r = right > 0 ? min(1, Double(width - 1 - x) / Double(right)) : 1
            let alpha = min(l, r)
            guard alpha < 1 else { continue }
            let offset = index(x, y)
            for channel in 0..<4 { bytes[offset + channel] = UInt8((Double(bytes[offset + channel]) * alpha).rounded()) }
        } }
    }
    func write(to url: URL) throws {
        guard let provider = CGDataProvider(data: Data(bytes) as CFData),
              let image = CGImage(width: width, height: height, bitsPerComponent: 8, bitsPerPixel: 32,
                bytesPerRow: width * 4, space: CGColorSpaceCreateDeviceRGB(),
                bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.premultipliedLast.rawValue | CGBitmapInfo.byteOrder32Big.rawValue),
                provider: provider, decode: nil, shouldInterpolate: false, intent: .defaultIntent),
              let destination = CGImageDestinationCreateWithURL(url as CFURL, UTType.png.identifier as CFString, 1, nil)
        else { throw Failure.message("Cannot encode \(url.path)") }
        CGImageDestinationAddImage(destination, image, nil)
        guard CGImageDestinationFinalize(destination) else { throw Failure.message("Cannot write \(url.path)") }
    }
}
enum Failure: Error { case message(String) }
let root = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
let destination = root.appendingPathComponent("web/static/mingya-derived", isDirectory: true)
try FileManager.default.createDirectory(at: destination, withIntermediateDirectories: true)
let original = try Bitmap(url: root.appendingPathComponent("design-references/calendar.png"))
guard original.width == 1536 && original.height == 1024 else { throw Failure.message("Unexpected source size") }
var clean = original
let mask = Box(x:294,y:150,width:500,height:87)
clean.clear(mask, from: original)
let protected = Box(x:1100,y:138,width:420,height:116)
var checked = 0
for y in protected.y..<(protected.y + protected.height) { for x in protected.x..<(protected.x + protected.width) {
  let i = original.index(x,y)
  guard clean.bytes[i..<(i+4)] == original.bytes[i..<(i+4)] else { throw Failure.message("Calendar artwork changed") }
  checked += 1
} }
let crop = Box(x:269,y:137,width:1253,height:118)
try clean.cropped(crop).write(to: destination.appendingPathComponent("calendar-banner.png"))
let manifest: [String:Any] = ["source":"design-references/calendar.png","crop":[269,137,1253,118],"cleared_source_rectangles":[[294,150,500,87]],"unchanged_artwork_pixels_checked":checked]
try JSONSerialization.data(withJSONObject: manifest, options:[.prettyPrinted,.sortedKeys]).write(to:destination.appendingPathComponent("calendar-provenance.json"))
print("calendar: 1253×118; \(checked) original artwork pixels unchanged")
