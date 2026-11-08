import Foundation
import AppKit
import Vision
import CoreImage
import PDFKit

func printJSON(_ dict: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: dict, options: []),
       let str = String(data: data, encoding: .utf8) {
        print(str)
    }
}

func loadCGImage(_ path: String) -> CGImage? {
    let url = URL(fileURLWithPath: path)
    if let src = CGImageSourceCreateWithURL(url as CFURL, nil),
       let cg = CGImageSourceCreateImageAtIndex(src, 0, nil) {
        return cg
    }
    if let nsImg = NSImage(contentsOfFile: path) {
        var rect = CGRect(origin: .zero, size: nsImg.size)
        return nsImg.cgImage(forProposedRect: &rect, context: nil, hints: nil)
    }
    return nil
}

func saveCGImageAsPNG(_ cgImage: CGImage, to path: String) -> Data? {
    let rep = NSBitmapImageRep(cgImage: cgImage)
    guard let pngData = rep.representation(using: .png, properties: [:]) else { return nil }
    try? pngData.write(to: URL(fileURLWithPath: path))
    return pngData
}

func copyCGImageToClipboard(_ cgImage: CGImage) {
    let rep = NSBitmapImageRep(cgImage: cgImage)
    let pb = NSPasteboard.general
    pb.clearContents()
    if let pngData = rep.representation(using: .png, properties: [:]) {
        pb.setData(pngData, forType: .png)
    }
    if let tiffData = rep.tiffRepresentation {
        pb.setData(tiffData, forType: .tiff)
    }
}

func rotateCGImage(_ cgImg: CGImage, degrees: Int) -> CGImage {
    let d = ((degrees % 360) + 360) % 360
    if d == 0 { return cgImg }
    let ci = CIImage(cgImage: cgImg)
    let oriented: CIImage
    switch d {
    case 90:
        oriented = ci.oriented(.right)
    case 180:
        oriented = ci.oriented(.down)
    case 270:
        oriented = ci.oriented(.left)
    default:
        oriented = ci
    }
    let ctx = CIContext()
    return ctx.createCGImage(oriented, from: oriented.extent) ?? cgImg
}

let args = CommandLine.arguments
guard args.count >= 2 else {
    printJSON(["ok": false, "error": "Usage: mac_vision_helper <cmd> ..."])
    exit(1)
}

let cmd = args[1]

switch cmd {
case "copy-image":
    guard args.count >= 3, let cg = loadCGImage(args[2]) else {
        printJSON(["ok": false, "error": "Failed to load image"])
        exit(1)
    }
    copyCGImageToClipboard(cg)
    printJSON(["ok": true, "message": "Copied image to macOS clipboard", "path": args[2]])

case "paste-image":
    guard args.count >= 3 else {
        printJSON(["ok": false, "error": "Missing output path"])
        exit(1)
    }
    let outPath = args[2]
    let pb = NSPasteboard.general
    if let data = pb.data(forType: .png) {
        do {
            try data.write(to: URL(fileURLWithPath: outPath))
            printJSON(["ok": true, "path": outPath])
            exit(0)
        } catch {
            printJSON(["ok": false, "error": error.localizedDescription])
            exit(1)
        }
    } else if let image = NSImage(pasteboard: pb),
              let tiffData = image.tiffRepresentation,
              let bitmap = NSBitmapImageRep(data: tiffData),
              let pngData = bitmap.representation(using: .png, properties: [:]) {
        do {
            try pngData.write(to: URL(fileURLWithPath: outPath))
            printJSON(["ok": true, "path": outPath])
            exit(0)
        } catch {
            printJSON(["ok": false, "error": error.localizedDescription])
            exit(1)
        }
    } else {
        printJSON(["ok": false, "error": "目前系統剪貼簿中沒有圖片（可先用 Cmd+Ctrl+Shift+4 截圖或複製圖片）"])
        exit(1)
    }

case "crop-rotate":
    // Usage: crop-rotate <in_path> <out_path> <x> <y> <w> <h> <rotate_deg> [copy]
    guard args.count >= 9, let rawCG = loadCGImage(args[2]) else {
        printJSON(["ok": false, "error": "Cannot load source image"])
        exit(1)
    }
    let outPath = args[3]
    let nx = Double(args[4]) ?? 0.0
    let ny = Double(args[5]) ?? 0.0
    let nw = Double(args[6]) ?? 1.0
    let nh = Double(args[7]) ?? 1.0
    let rot = Int(args[8]) ?? 0
    let shouldCopy = args.count >= 10 && args[9] == "copy"

    let rotated = rotateCGImage(rawCG, degrees: rot)
    let W = Double(rotated.width)
    let H = Double(rotated.height)
    let cx = max(0.0, min(W - 1.0, (nx * W).rounded()))
    let cy = max(0.0, min(H - 1.0, (ny * H).rounded()))
    let cw = max(1.0, min(W - cx, (nw * W).rounded()))
    let ch = max(1.0, min(H - cy, (nh * H).rounded()))

    let cropRect = CGRect(x: cx, y: cy, width: cw, height: ch)
    let finalCG = rotated.cropping(to: cropRect) ?? rotated
    _ = saveCGImageAsPNG(finalCG, to: outPath)
    if shouldCopy {
        copyCGImageToClipboard(finalCG)
    }
    printJSON([
        "ok": true,
        "saved_path": outPath,
        "copied": shouldCopy,
        "width": finalCG.width,
        "height": finalCG.height
    ])

case "faces":
    guard args.count >= 3, let cg = loadCGImage(args[2]) else {
        printJSON(["ok": false, "error": "Cannot load image for face detection"])
        exit(1)
    }
    let W = Double(cg.width)
    let H = Double(cg.height)
    let handler = VNImageRequestHandler(cgImage: cg, options: [:])
    let req = VNDetectFaceRectanglesRequest()
    if #available(macOS 12.0, *) {
        req.revision = VNDetectFaceRectanglesRequestRevision3
    }
    do {
        try handler.perform([req])
        let results = req.results ?? []
        var faces: [[String: Any]] = []

        func addFaceBox(fx: Double, fy: Double, fw: Double, fh: Double, conf: Double, label: String = "") {
            let x1 = max(0.0, fx)
            let y1 = max(0.0, fy)
            let w = min(W - x1, fw)
            let h = min(H - y1, fh)

            // Generate avatar thumbnail
            let padX = w * 0.25
            let padY = h * 0.25
            let tx = max(0.0, x1 - padX)
            let ty = max(0.0, y1 - padY)
            let tw = min(W - tx, w + padX * 2.0)
            let th = min(H - ty, h + padY * 2.0)
            var thumbB64 = ""
            if let faceCG = cg.cropping(to: CGRect(x: tx, y: ty, width: max(1, tw), height: max(1, th))) {
                let rep = NSBitmapImageRep(cgImage: faceCG)
                if let jpg = rep.representation(using: .jpeg, properties: [.compressionFactor: 0.82]) {
                    thumbB64 = "data:image/jpeg;base64," + jpg.base64EncodedString()
                }
            }

            // Smart 3:4 ID photo crop box
            let cx = x1 + w / 2.0
            let cy = y1 + h / 2.0
            var idH = min(H, h * 2.35)
            var idW = idH * 0.75
            if idW > W {
                idW = W
                idH = min(H, idW / 0.75)
            }
            let idX = max(0.0, min(W - idW, cx - idW / 2.0))
            let idY = max(0.0, min(H - idH, cy - idH * 0.44))

            faces.append([
                "confidence": conf,
                "sharpness": Double(Int(w * h / 100.0)),
                "label": label,
                "box": [
                    "x": x1 / W,
                    "y": y1 / H,
                    "w": w / W,
                    "h": h / H
                ],
                "id_crop": [
                    "x": idX / W,
                    "y": idY / H,
                    "w": idW / W,
                    "h": idH / H
                ],
                "thumb": thumbB64
            ])
        }

        if !results.isEmpty {
            for obs in results {
                let b = obs.boundingBox
                let x1 = b.origin.x * W
                let y1 = (1.0 - b.origin.y - b.size.height) * H
                let fw = b.size.width * W
                let fh = b.size.height * H
                addFaceBox(fx: x1, fy: y1, fw: fw, fh: fh, conf: Double(obs.confidence))
            }
        } else {
            // Fallback for Anime, Illustration, Deity, and Character Art (Saliency-Guided Face Detection)
            let objReq = VNGenerateObjectnessBasedSaliencyImageRequest()
            try? handler.perform([objReq])
            if let obs = objReq.results?.first as? VNSaliencyImageObservation,
               let salient = obs.salientObjects, !salient.isEmpty {
                let topObjects = salient.sorted(by: { a, b in a.confidence > b.confidence }).prefix(3)
                for (idx, top) in topObjects.enumerated() {
                    let b = top.boundingBox
                    let rx = b.origin.x * W
                    let ry = (1.0 - b.origin.y - b.size.height) * H
                    let rw = b.size.width * W
                    let rh = b.size.height * H

                    // Character face is in upper-center of the character bounding box
                    let fw = rw * 0.42
                    let fh = rh * 0.30
                    let fx = rx + (rw - fw) / 2.0
                    let fy = ry + rh * 0.16
                    addFaceBox(fx: fx, fy: fy, fw: fw, fh: fh, conf: Double(top.confidence), label: idx == 0 ? "神仙萌顏" : "特徵焦點")
                }
            }
        }

        faces.sort {
            let ax = ($0["box"] as? [String: Double])?["x"] ?? 0
            let bx = ($1["box"] as? [String: Double])?["x"] ?? 0
            return ax < bx
        }
        for i in 0..<faces.count {
            faces[i]["index"] = i + 1
        }
        printJSON(["ok": true, "count": faces.count, "faces": faces])
    } catch {
        printJSON(["ok": false, "error": error.localizedDescription])
        exit(1)
    }

case "ocr":
    guard args.count >= 3 else {
        printJSON(["ok": false, "error": "Missing image path"])
        exit(1)
    }
    let path = args[2]
    let url = URL(fileURLWithPath: path)
    let handler = VNImageRequestHandler(url: url, options: [:])
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    request.recognitionLanguages = ["zh-Hant", "zh-Hans", "en-US", "ja-JP"]
    do {
        try handler.perform([request])
        let observations = request.results ?? []
        var lines: [String] = []
        var blocks: [[String: Any]] = []
        for obs in observations {
            if let top = obs.topCandidates(1).first {
                lines.append(top.string)
                let box = obs.boundingBox
                blocks.append([
                    "text": top.string,
                    "confidence": top.confidence,
                    "x": box.origin.x,
                    "y": 1.0 - box.origin.y - box.size.height,
                    "w": box.size.width,
                    "h": box.size.height
                ])
            }
        }
        printJSON([
            "ok": true,
            "text": lines.joined(separator: "\n"),
            "lines": lines,
            "blocks": blocks
        ])
    } catch {
        printJSON(["ok": false, "error": error.localizedDescription])
        exit(1)
    }

case "remove-bg":
    guard args.count >= 4 else {
        printJSON(["ok": false, "error": "Usage: remove-bg <in_path> <out_path> [copy]"])
        exit(1)
    }
    let inPath = args[2]
    let outPath = args[3]
    let shouldCopy = args.count >= 5 && args[4] == "copy"
    let url = URL(fileURLWithPath: inPath)
    guard let ciImage = CIImage(contentsOf: url) else {
        printJSON(["ok": false, "error": "Cannot load CIImage"])
        exit(1)
    }
    if #available(macOS 14.0, *) {
        let handler = VNImageRequestHandler(ciImage: ciImage, options: [:])
        let request = VNGenerateForegroundInstanceMaskRequest()
        do {
            try handler.perform([request])
            guard let result = request.results?.first else {
                printJSON(["ok": false, "error": "未偵測到明顯前景主體"])
                exit(1)
            }
            let maskedBuffer = try result.generateMaskedImage(
                ofInstances: result.allInstances,
                from: handler,
                croppedToInstancesExtent: false
            )
            let outCI = CIImage(cvPixelBuffer: maskedBuffer)
            let context = CIContext()
            guard let cgImage = context.createCGImage(outCI, from: outCI.extent) else {
                printJSON(["ok": false, "error": "Failed to render CGImage"])
                exit(1)
            }
            _ = saveCGImageAsPNG(cgImage, to: outPath)
            if shouldCopy {
                copyCGImageToClipboard(cgImage)
            }
            printJSON(["ok": true, "path": outPath, "copied": shouldCopy])
        } catch {
            printJSON(["ok": false, "error": error.localizedDescription])
            exit(1)
        }
    } else {
        printJSON(["ok": false, "error": "macOS 14.0+ required for foreground mask"])
        exit(1)
    }

case "pdf-info":
    guard args.count >= 3 else {
        printJSON(["ok": false, "error": "Usage: pdf-info <pdf_path>"])
        exit(1)
    }
    let pdfPath = args[2]
    guard let doc = PDFDocument(url: URL(fileURLWithPath: pdfPath)) else {
        printJSON(["ok": false, "error": "無法解析 PDF 檔案"])
        exit(1)
    }
    let count = doc.pageCount
    let attrs = doc.documentAttributes ?? [:]
    let title = (attrs[PDFDocumentAttribute.titleAttribute] as? String) ?? ""
    let author = (attrs[PDFDocumentAttribute.authorAttribute] as? String) ?? ""
    var pages: [[String: Any]] = []
    let maxPages = min(count, 300)
    for i in 0..<maxPages {
        if let p = doc.page(at: i) {
            let b = p.bounds(for: .mediaBox)
            let txt = (p.string ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            pages.append([
                "index": i + 1,
                "label": p.label ?? "\(i + 1)",
                "width": Int(b.width.rounded()),
                "height": Int(b.height.rounded()),
                "text": txt
            ])
        }
    }
    printJSON([
        "ok": true,
        "page_count": count,
        "title": title,
        "author": author,
        "pages": pages
    ])

case "pdf-page":
    // Usage: pdf-page <pdf_path> <page_1_based> <out_png_path> [scale]
    guard args.count >= 5 else {
        printJSON(["ok": false, "error": "Usage: pdf-page <pdf_path> <page_num> <out_png_path> [scale]"])
        exit(1)
    }
    let pdfPath = args[2]
    let pageNum = max(1, Int(args[3]) ?? 1)
    let outPath = args[4]
    let scale = CGFloat(args.count >= 6 ? (Double(args[5]) ?? 2.0) : 2.0)

    guard let doc = PDFDocument(url: URL(fileURLWithPath: pdfPath)),
          pageNum <= doc.pageCount,
          let page = doc.page(at: pageNum - 1) else {
        printJSON(["ok": false, "error": "無法讀取指定 PDF 頁碼"])
        exit(1)
    }
    let bounds = page.bounds(for: .mediaBox)
    let targetSize = CGSize(width: max(600, bounds.width * scale), height: max(800, bounds.height * scale))
    let thumbImg = page.thumbnail(of: targetSize, for: .mediaBox)
    if let tiff = thumbImg.tiffRepresentation,
       let rep = NSBitmapImageRep(data: tiff),
       let pngData = rep.representation(using: .png, properties: [:]) {
        do {
            try pngData.write(to: URL(fileURLWithPath: outPath))
            printJSON([
                "ok": true,
                "path": outPath,
                "width": rep.pixelsWide,
                "height": rep.pixelsHigh
            ])
        } catch {
            printJSON(["ok": false, "error": error.localizedDescription])
            exit(1)
        }
    } else {
        printJSON(["ok": false, "error": "無法渲染 PDF 頁面"])
        exit(1)
    }

case "export-pdf-slides":
    // Usage: export-pdf-slides <pdf_path> <out_dir> [max_width] [max_height]
    guard args.count >= 4 else {
        printJSON(["ok": false, "error": "Usage: export-pdf-slides <pdf_path> <out_dir> [max_width] [max_height]"])
        exit(1)
    }
    let pdfPath = args[2]
    let outDir = args[3]
    let maxW = CGFloat(args.count >= 5 ? (Double(args[4]) ?? 1920.0) : 1920.0)
    let maxH = CGFloat(args.count >= 6 ? (Double(args[5]) ?? 1080.0) : 1080.0)

    guard let doc = PDFDocument(url: URL(fileURLWithPath: pdfPath)) else {
        printJSON(["ok": false, "error": "無法開啟 PDF 文件: \(pdfPath)"])
        exit(1)
    }
    try? FileManager.default.createDirectory(atPath: outDir, withIntermediateDirectories: true, attributes: nil)
    let count = doc.pageCount
    var slides: [[String: Any]] = []

    for i in 0..<count {
        guard let page = doc.page(at: i) else { continue }
        let thumbImg = page.thumbnail(of: CGSize(width: maxW, height: maxH), for: .mediaBox)
        let outPath = "\(outDir)/slide_\(i + 1).png"
        if let tiff = thumbImg.tiffRepresentation,
           let rep = NSBitmapImageRep(data: tiff),
           let pngData = rep.representation(using: .png, properties: [:]) {
            try? pngData.write(to: URL(fileURLWithPath: outPath))
            slides.append([
                "index": i + 1,
                "path": outPath,
                "width": rep.pixelsWide,
                "height": rep.pixelsHigh
            ])
        }
    }
    printJSON([
        "ok": true,
        "count": slides.count,
        "slides": slides
    ])


default:
    printJSON(["ok": false, "error": "Unknown command: \(cmd)"])
    exit(1)
}
