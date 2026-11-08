import Cocoa
import Foundation

class AppDelegate: NSObject, NSApplicationDelegate {
    var handledOpenFiles = false
    let port = 18765

    var toolboxRoot: String {
        let devRoot = "/Users/become/Claude/Projects/toolbox"
        if Bundle.main.bundlePath.hasPrefix(devRoot),
           FileManager.default.fileExists(atPath: "\(devRoot)/server.py") {
            return devRoot
        }
        if let resPath = Bundle.main.resourcePath,
           FileManager.default.fileExists(atPath: "\(resPath)/server.py") {
            return resPath
        }
        return devRoot
    }

    func ensureServerRunning(initialPath: String?) {
        let checkUrl = URL(string: "http://127.0.0.1:\(port)/api/pending-open")!
        let sem = DispatchSemaphore(value: 0)
        var isRunning = false

        var req = URLRequest(url: checkUrl)
        req.timeoutInterval = 0.25
        URLSession.shared.dataTask(with: req) { _, resp, _ in
            if let http = resp as? HTTPURLResponse, http.statusCode == 200 {
                isRunning = true
            }
            sem.signal()
        }.resume()
        _ = sem.wait(timeout: .now() + 0.35)

        if !isRunning {
            let root = toolboxRoot
            let task = Process()
            task.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
            var args = ["\(root)/server.py"]
            if let p = initialPath {
                args.append(p)
            }
            task.arguments = args
            task.currentDirectoryURL = URL(fileURLWithPath: root)
            task.standardOutput = FileHandle.nullDevice
            task.standardError = FileHandle.nullDevice
            try? task.run()
            for _ in 0..<15 {
                usleep(100_000)
                let s2 = DispatchSemaphore(value: 0)
                var up = false
                URLSession.shared.dataTask(with: req) { _, resp, _ in
                    if let http = resp as? HTTPURLResponse, http.statusCode == 200 {
                        up = true
                    }
                    s2.signal()
                }.resume()
                _ = s2.wait(timeout: .now() + 0.2)
                if up { break }
            }
        } else if let p = initialPath {
            var postReq = URLRequest(url: URL(string: "http://127.0.0.1:\(port)/api/open-target")!)
            postReq.httpMethod = "POST"
            postReq.setValue("application/json", forHTTPHeaderField: "Content-Type")
            postReq.httpBody = try? JSONSerialization.data(withJSONObject: ["path": p])
            let s3 = DispatchSemaphore(value: 0)
            URLSession.shared.dataTask(with: postReq) { _, _, _ in s3.signal() }.resume()
            _ = s3.wait(timeout: .now() + 0.3)
        }
    }

    func openBrowser(targetPath: String?) {
        ensureServerRunning(initialPath: targetPath)
        var urlStr = "http://127.0.0.1:\(port)/"
        if let p = targetPath,
           let encoded = p.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) {
            urlStr += "?path=\(encoded)"
        }
        // Explicitly open with Google Chrome if installed, otherwise default browser
        let chromePath = "/Applications/Google Chrome.app"
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/usr/bin/open")
        if FileManager.default.fileExists(atPath: chromePath) {
            proc.arguments = ["-a", chromePath, urlStr]
        } else {
            proc.arguments = [urlStr]
        }
        try? proc.run()
        proc.waitUntilExit()

        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
            NSApp.terminate(nil)
        }
    }

    func application(_ sender: NSApplication, openFiles filenames: [String]) {
        handledOpenFiles = true
        openBrowser(targetPath: filenames.first)
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let args = CommandLine.arguments
        if args.count > 1 && !args[1].hasPrefix("-psn") {
            handledOpenFiles = true
            openBrowser(targetPath: args[1])
            return
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) {
            if !self.handledOpenFiles {
                self.openBrowser(targetPath: nil)
            }
        }
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
