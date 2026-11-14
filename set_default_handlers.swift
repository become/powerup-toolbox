import Cocoa
import CoreServices
import UniformTypeIdentifiers

let BUNDLE_ID = "com.become.mactoolbox"

struct FormatDef {
    let key: String
    let name: String
    let exts: [String]
    let utis: [String]
    let category: String
    let categoryName: String
    let recommended: Bool
    let defaultAppFallback: String?
}

let FORMATS: [FormatDef] = [
    // 🖼️ 圖片工坊與相片
    FormatDef(key: "image_common", name: "常用圖片 (PNG / JPG / WEBP / HEIC / GIF)", exts: ["png", "jpg", "jpeg", "webp", "heic", "gif"], utis: ["public.image", "public.png", "public.jpeg", "org.webmproject.webp", "public.heic", "com.compuserve.gif"], category: "image", categoryName: "🖼️ 圖片工坊與相片", recommended: true, defaultAppFallback: "com.apple.Preview"),
    FormatDef(key: "image_svg", name: "SVG 向量圖形", exts: ["svg"], utis: ["public.svg-image"], category: "image", categoryName: "🖼️ 圖片工坊與相片", recommended: false, defaultAppFallback: "com.apple.Safari"),
    FormatDef(key: "image_pro", name: "專業影像 (TIFF, BMP)", exts: ["tiff", "tif", "bmp"], utis: ["public.tiff", "com.microsoft.bmp"], category: "image", categoryName: "🖼️ 圖片工坊與相片", recommended: false, defaultAppFallback: "com.apple.Preview"),

    // 📑 文件與簡報
    FormatDef(key: "md", name: "Markdown 筆記文件", exts: ["md", "markdown", "mdx"], utis: ["net.daringfireball.markdown"], category: "doc", categoryName: "📑 文件與簡報", recommended: true, defaultAppFallback: "com.apple.TextEdit"),
    FormatDef(key: "csv", name: "CSV / TSV 試算數據", exts: ["csv", "tsv"], utis: ["public.comma-separated-values-text", "public.tab-separated-values-text"], category: "data", categoryName: "📊 試算表與數據", recommended: true, defaultAppFallback: "com.apple.iWork.Numbers"),
    FormatDef(key: "json", name: "JSON / JSONL 結構化資料", exts: ["json", "jsonl", "ndjson", "geojson"], utis: ["public.json"], category: "data", categoryName: "📊 試算表與數據", recommended: true, defaultAppFallback: "com.apple.TextEdit"),
    FormatDef(key: "pptx", name: "PowerPoint 簡報", exts: ["pptx", "ppt"], utis: ["org.openxmlformats.presentationml.presentation", "com.microsoft.powerpoint.ppt"], category: "doc", categoryName: "📑 文件與簡報", recommended: true, defaultAppFallback: "com.apple.iWork.Keynote"),
    FormatDef(key: "docx", name: "Word 文件", exts: ["docx", "doc"], utis: ["org.openxmlformats.wordprocessingml.document", "com.microsoft.word.doc"], category: "doc", categoryName: "📑 文件與簡報", recommended: false, defaultAppFallback: "com.apple.iWork.Pages"),
    FormatDef(key: "xlsx", name: "Excel 試算表", exts: ["xlsx", "xlsm", "xls"], utis: ["org.openxmlformats.spreadsheetml.sheet", "com.microsoft.excel.xls"], category: "data", categoryName: "📊 試算表與數據", recommended: false, defaultAppFallback: "com.apple.iWork.Numbers"),
    FormatDef(key: "pdf", name: "PDF 文件", exts: ["pdf"], utis: ["com.adobe.pdf"], category: "doc", categoryName: "📑 文件與簡報", recommended: false, defaultAppFallback: "com.apple.Preview"),

    // 📦 結構化與程式碼
    FormatDef(key: "xml", name: "XML / Plist 標記資料", exts: ["xml", "plist", "rss", "atom"], utis: ["public.xml", "com.apple.property-list"], category: "code", categoryName: "📦 結構化與程式碼", recommended: false, defaultAppFallback: "com.apple.TextEdit"),
    FormatDef(key: "java", name: "Java 原始碼", exts: ["java"], utis: [], category: "code", categoryName: "📦 結構化與程式碼", recommended: false, defaultAppFallback: "com.jetbrains.intellij"),
    FormatDef(key: "groovy", name: "Groovy / Gradle 腳本", exts: ["groovy", "gradle"], utis: [], category: "code", categoryName: "📦 結構化與程式碼", recommended: false, defaultAppFallback: "com.jetbrains.intellij"),
    FormatDef(key: "gsp", name: "GSP 頁面範本", exts: ["gsp"], utis: [], category: "code", categoryName: "📦 結構化與程式碼", recommended: false, defaultAppFallback: "com.jetbrains.intellij"),
    FormatDef(key: "js", name: "JavaScript / CSS 樣式", exts: ["js", "mjs", "cjs", "jsx", "ts", "tsx", "css", "scss"], utis: [], category: "code", categoryName: "📦 結構化與程式碼", recommended: false, defaultAppFallback: "com.microsoft.VSCode"),

    // 🌐 網頁與純文字
    FormatDef(key: "html", name: "HTML 網頁檔", exts: ["html", "htm", "xhtml"], utis: ["public.html"], category: "web", categoryName: "🌐 網頁與純文字", recommended: false, defaultAppFallback: "com.google.Chrome"),
    FormatDef(key: "txt", name: "純文字文件", exts: ["txt", "text", "log"], utis: [], category: "web", categoryName: "🌐 網頁與純文字", recommended: false, defaultAppFallback: "com.apple.TextEdit")
]

func getUti(for ext: String) -> String? {
    if #available(macOS 11.0, *), let t = UTType(filenameExtension: ext) {
        return t.identifier
    }
    return nil
}

func getAllUtis(for format: FormatDef) -> [String] {
    var set = Set<String>(format.utis)
    for ext in format.exts {
        if let u = getUti(for: ext) {
            set.insert(u)
        }
    }
    return Array(set)
}

func getAppDisplayName(bundleId: String) -> String {
    if bundleId.isEmpty || bundleId == "none" { return "系統預設" }
    if bundleId == BUNDLE_ID { return "PowerUp Toolbox" }
    if let url = NSWorkspace.shared.urlForApplication(withBundleIdentifier: bundleId) {
        let name = FileManager.default.displayName(atPath: url.path)
        return name.hasSuffix(".app") ? String(name.dropLast(4)) : name
    }
    return bundleId
}

func getCurrentHandler(for uti: String) -> String {
    if let handler = LSCopyDefaultRoleHandlerForContentType(uti as CFString, .all)?.takeRetainedValue() as String? {
        return handler
    }
    return ""
}

func findAlternativeHandler(for uti: String, fallback: String?) -> String? {
    if let list = LSCopyAllRoleHandlersForContentType(uti as CFString, .all)?.takeRetainedValue() as? [String] {
        for app in list {
            if app != BUNDLE_ID && !app.contains("mactoolbox") {
                return app
            }
        }
    }
    return fallback
}

func setHandler(for uti: String, bundleId: String) -> Bool {
    let status = LSSetDefaultRoleHandlerForContentType(uti as CFString, [.viewer, .editor, .all], bundleId as CFString)
    return status == noErr
}

func getStatusList() -> [[String: Any]] {
    var result: [[String: Any]] = []
    for f in FORMATS {
        let utis = getAllUtis(for: f)
        let primaryUti = utis.first ?? (getUti(for: f.exts.first ?? f.key) ?? "")
        let curBundle = getCurrentHandler(for: primaryUti)
        let isDefault = (curBundle == BUNDLE_ID)
        let appName = getAppDisplayName(bundleId: curBundle)

        result.append([
            "key": f.key,
            "name": f.name,
            "exts": f.exts.map { ".\($0)" },
            "category": f.category,
            "category_name": f.categoryName,
            "recommended": f.recommended,
            "is_default": isDefault,
            "current_bundle": curBundle,
            "current_app": appName
        ])
    }
    return result
}

func applySelections(keys: Set<String>) -> ([String], [String]) {
    var applied: [String] = []
    var reverted: [String] = []

    for f in FORMATS {
        let utis = getAllUtis(for: f)
        let primaryUti = utis.first ?? ""
        let curBundle = getCurrentHandler(for: primaryUti)
        let wasDefault = (curBundle == BUNDLE_ID)
        let shouldBeDefault = keys.contains(f.key)

        if shouldBeDefault {
            for uti in utis {
                _ = setHandler(for: uti, bundleId: BUNDLE_ID)
            }
            applied.append(f.key)
        } else if wasDefault {
            if let alt = findAlternativeHandler(for: primaryUti, fallback: f.defaultAppFallback) {
                for uti in utis {
                    _ = setHandler(for: uti, bundleId: alt)
                }
                reverted.append(f.key)
            }
        }
    }
    return (applied, reverted)
}

let args = CommandLine.arguments

if args.count > 1 && args[1] == "--apply" {
    let rawList = (args.count > 2) ? args[2] : ""
    let selectedKeys = Set(rawList.split(separator: ",").map { String($0).trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty })
    let (applied, reverted) = applySelections(keys: selectedKeys)
    let output: [String: Any] = [
        "ok": true,
        "applied": applied,
        "reverted": reverted,
        "items": getStatusList()
    ]
    if let data = try? JSONSerialization.data(withJSONObject: output, options: .prettyPrinted),
       let str = String(data: data, encoding: .utf8) {
        print(str)
    }
} else {
    let output: [String: Any] = [
        "ok": true,
        "bundle_id": BUNDLE_ID,
        "items": getStatusList()
    ]
    if let data = try? JSONSerialization.data(withJSONObject: output, options: .prettyPrinted),
       let str = String(data: data, encoding: .utf8) {
        print(str)
    }
}
