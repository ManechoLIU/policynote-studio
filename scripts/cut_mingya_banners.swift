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
let destination = root.appendingPathComponent("web/static/mingya-banners", isDirectory: true)
try FileManager.default.createDirectory(at: destination, withIntermediateDirectories: true)
let plans: [Plan] = [
    Plan(page: "home", crop: Box(x:269,y:64,width:1253,height:348), masks: [
        Box(x:300,y:89,width:250,height:40), Box(x:300,y:132,width:416,height:139),
        Box(x:300,y:273,width:443,height:35), Box(x:299,y:319,width:407,height:67)
    ], protected: [Box(x:750,y:74,width:770,height:338)], featherLeft:80, featherRight:0),
    Plan(page: "skills", crop: Box(x:269,y:64,width:1253,height:220), masks: [
        Box(x:298,y:88,width:445,height:100), Box(x:299,y:198,width:568,height:63)
    ], protected: [Box(x:880,y:70,width:640,height:214)], featherLeft:80, featherRight:0),
    Plan(page: "outputs", crop: Box(x:269,y:64,width:1253,height:185), masks: [
        Box(x:300,y:90,width:440,height:118)
    ], protected: [Box(x:875,y:73,width:645,height:176)], featherLeft:80, featherRight:0),
    Plan(page: "accounts", crop: Box(x:269,y:68,width:1253,height:141), masks: [
        Box(x:300,y:87,width:448,height:101), Box(x:1366,y:105,width:135,height:62)
    ], protected: [Box(x:920,y:75,width:255,height:133)], featherLeft:80, featherRight:0),
    Plan(page: "profile", crop: Box(x:269,y:64,width:1253,height:156), masks: [
        Box(x:510,y:96,width:504,height:95), Box(x:1090,y:106,width:381,height:68)
    ], protected: [Box(x:269,y:76,width:230,height:143),Box(x:1473,y:174,width:47,height:45)], featherLeft:0, featherRight:0),
    Plan(page: "chat", crop: Box(x:249,y:64,width:1287,height:960), masks: [
        Box(x:291,y:88,width:214,height:42), Box(x:291,y:135,width:562,height:73),
        Box(x:291,y:226,width:780,height:144),
        Box(x:292,y:382,width:1205,height:294), Box(x:274,y:680,width:1237,height:158),
        Box(x:670,y:914,width:407,height:60)
    ], protected: [Box(x:1080,y:111,width:448,height:264)], featherLeft:80, featherRight:0)
]
var manifest: [[String: Any]] = []
for plan in plans {
    let original = try Bitmap(url: root.appendingPathComponent("design-references/\(plan.page).png"))
    guard original.width == 1536 && original.height == 1024 else { throw Failure.message("Unexpected source size") }
    var clean = original
    for mask in plan.masks { clean.clear(mask, from: original) }
    var checked = 0
    for box in plan.protected {
        for y in box.y..<(box.y + box.height) { for x in box.x..<(box.x + box.width) {
            let i = original.index(x,y)
            guard clean.bytes[i..<(i+4)] == original.bytes[i..<(i+4)] else { throw Failure.message("Artwork altered: \(plan.page) \(x),\(y)") }
            checked += 1
        } }
    }
    var output = clean.cropped(plan.crop)
    output.feather(left:plan.featherLeft,right:plan.featherRight)
    try output.write(to: destination.appendingPathComponent("\(plan.page).png"))
    manifest.append(["page":plan.page,"source":"design-references/\(plan.page).png", "crop":[plan.crop.x,plan.crop.y,plan.crop.width,plan.crop.height], "cleared_source_rectangles":plan.masks.map {[$0.x,$0.y,$0.width,$0.height]},"unchanged_artwork_pixels_checked":checked,"left_edge_alpha_feather":plan.featherLeft])
    print("\(plan.page): \(output.width)×\(output.height), \(checked) original artwork pixels unchanged")
}
let json = try JSONSerialization.data(withJSONObject: manifest, options: [.prettyPrinted,.sortedKeys])
try json.write(to: destination.appendingPathComponent("provenance.json"))
