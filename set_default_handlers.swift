import Foundation
import CoreServices
import UniformTypeIdentifiers

let bundleId = "com.become.mactoolbox" as CFString
let extensions = ["json", "jsonl", "ndjson", "geojson", "xml", "plist", "rss", "atom", "csv", "tsv", "md", "markdown", "mdx", "pptx", "ppt", "docx", "doc", "xlsx", "xlsm", "xls", "pdf"]
var utis = Set<String>([
    "public.json",
    "public.xml",
    "com.apple.property-list",
    "public.comma-separated-values-text",
    "public.tab-separated-values-text",
    "net.daringfireball.markdown",
    "org.openxmlformats.presentationml.presentation",
    "com.microsoft.powerpoint.ppt",
    "org.openxmlformats.wordprocessingml.document",
    "com.microsoft.word.doc",
    "org.openxmlformats.spreadsheetml.sheet",
    "com.microsoft.excel.xls",
    "com.adobe.pdf"
])

for ext in extensions {
    if #available(macOS 11.0, *) {
        if let utType = UTType(filenameExtension: ext) {
            utis.insert(utType.identifier)
        }
    }
    if let cfUti = UTTypeCreatePreferredIdentifierForTag(kUTTagClassFilenameExtension, ext as CFString, nil)?.takeRetainedValue() {
        utis.insert(cfUti as String)
    }
}

for uti in utis.sorted() {
    // Avoid overriding generic public.plain-text or public.text so .txt stays intact
    if uti == "public.plain-text" || uti == "public.text" || uti == "public.data" {
        continue
    }
    let status = LSSetDefaultRoleHandlerForContentType(uti as CFString, [.viewer, .editor, .all], bundleId)
    print("UTI: \(uti) -> MacToolbox (status: \(status))")
}
