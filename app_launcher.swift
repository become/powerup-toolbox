import Cocoa
import Foundation
import WebKit

class WebDelegateHandler: NSObject, WKNavigationDelegate, WKUIDelegate {
    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let alert = NSAlert()
        alert.messageText = "PowerUp Toolbox"
        alert.informativeText = message
        alert.alertStyle = .informational
        alert.addButton(withTitle: "確定")
        if let win = webView.window {
            alert.beginSheetModal(for: win) { _ in completionHandler() }
        } else {
            alert.runModal()
            completionHandler()
        }
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let alert = NSAlert()
        alert.messageText = "PowerUp Toolbox"
        alert.informativeText = message
        alert.alertStyle = .warning
        alert.addButton(withTitle: "確定")
        alert.addButton(withTitle: "取消")
        if let win = webView.window {
            alert.beginSheetModal(for: win) { resp in
                completionHandler(resp == .alertFirstButtonReturn)
            }
        } else {
            let resp = alert.runModal()
            completionHandler(resp == .alertFirstButtonReturn)
        }
    }

    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let openPanel = NSOpenPanel()
        openPanel.allowsMultipleSelection = parameters.allowsMultipleSelection
        openPanel.canChooseDirectories = parameters.allowsDirectories
        openPanel.canChooseFiles = true
        if let win = webView.window {
            openPanel.beginSheetModal(for: win) { resp in
                completionHandler(resp == .OK ? openPanel.urls : nil)
            }
        } else {
            completionHandler(openPanel.runModal() == .OK ? openPanel.urls : nil)
        }
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if navigationAction.navigationType == .linkActivated,
           let url = navigationAction.request.url,
           let host = url.host?.lowercased(),
           host != "127.0.0.1" && host != "localhost" {
            NSWorkspace.shared.open(url)
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.allow)
    }
}

class MainWindowController: NSWindowController, NSWindowDelegate {
    var webView: WKWebView!
    var delegateHandler: WebDelegateHandler!

    convenience init() {
        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1260, height: 840),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "PowerUp Toolbox"
        window.minSize = NSSize(width: 960, height: 600)
        window.center()
        window.setFrameAutosaveName("PowerUpToolboxMainWindow")

        self.init(window: window)
        window.delegate = self

        let config = WKWebViewConfiguration()
        config.preferences.setValue(true, forKey: "developerExtrasEnabled")
        config.defaultWebpagePreferences.allowsContentJavaScript = true

        self.delegateHandler = WebDelegateHandler()
        self.webView = WKWebView(frame: window.contentView!.bounds, configuration: config)
        self.webView.autoresizingMask = [.width, .height]
        self.webView.navigationDelegate = self.delegateHandler
        self.webView.uiDelegate = self.delegateHandler

        window.contentView?.addSubview(self.webView)
    }

    func windowWillClose(_ notification: Notification) {
        NSApp.terminate(nil)
    }
}

class AppDelegate: NSObject, NSApplicationDelegate {
    let port = 18765
    var serverProcess: Process?
    var windowController: MainWindowController?
    var initialTargetFile: String?

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

    func findPython() -> String {
        let candidates = [
            "/usr/bin/python3",
            "/opt/homebrew/bin/python3",
            "/usr/local/bin/python3"
        ]
        for c in candidates {
            if FileManager.default.isExecutableFile(atPath: c) {
                return c
            }
        }
        return "/usr/bin/python3"
    }

    func ensureServerRunning(completion: @escaping (Bool) -> Void) {
        let checkUrl = URL(string: "http://127.0.0.1:\(port)/api/pending-open")!
        var req = URLRequest(url: checkUrl)
        req.timeoutInterval = 0.3

        URLSession.shared.dataTask(with: req) { [weak self] _, resp, _ in
            if let http = resp as? HTTPURLResponse, http.statusCode == 200 {
                DispatchQueue.main.async { completion(true) }
                return
            }

            DispatchQueue.main.async {
                guard let self = self else { return }
                self.launchServerProcess()
                self.waitForServerReady(attempt: 0, completion: completion)
            }
        }.resume()
    }

    func launchServerProcess() {
        let root = toolboxRoot
        let pyBin = findPython()
        let task = Process()
        task.executableURL = URL(fileURLWithPath: pyBin)
        task.arguments = ["\(root)/server.py"]
        task.currentDirectoryURL = URL(fileURLWithPath: root)

        let logsDir = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Logs/PowerUpToolbox")
        try? FileManager.default.createDirectory(at: logsDir, withIntermediateDirectories: true)
        let logFile = logsDir.appendingPathComponent("server.log")
        if !FileManager.default.fileExists(atPath: logFile.path) {
            FileManager.default.createFile(atPath: logFile.path, contents: nil)
        }
        if let handle = try? FileHandle(forWritingTo: logFile) {
            handle.seekToEndOfFile()
            task.standardOutput = handle
            task.standardError = handle
        }

        try? task.run()
        self.serverProcess = task
    }

    func waitForServerReady(attempt: Int, completion: @escaping (Bool) -> Void) {
        if attempt >= 45 {
            completion(false)
            return
        }
        DispatchQueue.global().asyncAfter(deadline: .now() + 0.1) { [weak self] in
            guard let self = self else { return }
            let checkUrl = URL(string: "http://127.0.0.1:\(self.port)/api/pending-open")!
            var req = URLRequest(url: checkUrl)
            req.timeoutInterval = 0.25
            URLSession.shared.dataTask(with: req) { _, resp, _ in
                if let http = resp as? HTTPURLResponse, http.statusCode == 200 {
                    DispatchQueue.main.async { completion(true) }
                } else {
                    self.waitForServerReady(attempt: attempt + 1, completion: completion)
                }
            }.resume()
        }
    }

    func loadAppWindow(targetPath: String?) {
        var urlStr = "http://127.0.0.1:\(port)/"
        if let p = targetPath,
           let encoded = p.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) {
            urlStr += "?path=\(encoded)"
        }
        guard let url = URL(string: urlStr) else { return }

        if windowController == nil {
            windowController = MainWindowController()
        }
        guard let wc = windowController, let win = wc.window else { return }

        wc.webView.load(URLRequest(url: url))
        win.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func openTargetFile(_ path: String) {
        if let wc = windowController, let win = wc.window {
            win.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)

            if let postUrl = URL(string: "http://127.0.0.1:\(port)/api/open-target") {
                var postReq = URLRequest(url: postUrl)
                postReq.httpMethod = "POST"
                postReq.setValue("application/json", forHTTPHeaderField: "Content-Type")
                postReq.httpBody = try? JSONSerialization.data(withJSONObject: ["path": path])
                URLSession.shared.dataTask(with: postReq).resume()
            }

            let escaped = path.replacingOccurrences(of: "\\", with: "\\\\")
                              .replacingOccurrences(of: "'", with: "\\'")
            let js = "if (typeof loadDirectory === 'function') { loadDirectory('\(escaped)', '\(escaped)', true); }"
            wc.webView.evaluateJavaScript(js, completionHandler: nil)
        }
    }

    func application(_ sender: NSApplication, openFiles filenames: [String]) {
        guard let file = filenames.first else { return }
        if windowController == nil {
            initialTargetFile = file
        } else {
            openTargetFile(file)
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        if let proc = serverProcess, proc.isRunning {
            proc.terminate()
            usleep(100_000)
        }
    }

    @objc func reloadPage() {
        windowController?.webView.reload()
    }

    func setupMainMenu() {
        let mainMenu = NSMenu()

        // 1. Application Menu
        let appMenuItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "關於 PowerUp Toolbox", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(NSMenuItem.separator())
        appMenu.addItem(withTitle: "隱藏 PowerUp Toolbox", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        let hideOthers = NSMenuItem(title: "隱藏其他", action: #selector(NSApplication.hideOtherApplications(_:)), keyEquivalent: "h")
        hideOthers.keyEquivalentModifierMask = [.command, .option]
        appMenu.addItem(hideOthers)
        appMenu.addItem(withTitle: "全部顯示", action: #selector(NSApplication.unhideAllApplications(_:)), keyEquivalent: "")
        appMenu.addItem(NSMenuItem.separator())
        appMenu.addItem(withTitle: "結束 PowerUp Toolbox", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appMenuItem.submenu = appMenu
        mainMenu.addItem(appMenuItem)

        // 2. Edit Menu
        let editMenuItem = NSMenuItem()
        let editMenu = NSMenu(title: "編輯")
        editMenu.addItem(withTitle: "還原", action: #selector(UndoManager.undo), keyEquivalent: "z")
        editMenu.addItem(withTitle: "重做", action: #selector(UndoManager.redo), keyEquivalent: "Z")
        editMenu.addItem(NSMenuItem.separator())
        editMenu.addItem(withTitle: "剪下", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "拷貝", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "貼上", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "全選", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editMenuItem.submenu = editMenu
        mainMenu.addItem(editMenuItem)

        // 3. View Menu
        let viewMenuItem = NSMenuItem()
        let viewMenu = NSMenu(title: "顯示")
        viewMenu.addItem(withTitle: "重新載入", action: #selector(AppDelegate.reloadPage), keyEquivalent: "r")
        viewMenuItem.submenu = viewMenu
        mainMenu.addItem(viewMenuItem)

        // 4. Window Menu
        let windowMenuItem = NSMenuItem()
        let windowMenu = NSMenu(title: "視窗")
        windowMenu.addItem(withTitle: "最小化", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        windowMenu.addItem(withTitle: "縮放", action: #selector(NSWindow.performZoom(_:)), keyEquivalent: "")
        windowMenu.addItem(NSMenuItem.separator())
        windowMenu.addItem(withTitle: "關閉視窗", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        windowMenuItem.submenu = windowMenu
        mainMenu.addItem(windowMenuItem)

        NSApp.mainMenu = mainMenu
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        setupMainMenu()

        let args = CommandLine.arguments
        if args.count > 1 && !args[1].hasPrefix("-psn") && initialTargetFile == nil {
            initialTargetFile = args[1]
        }

        ensureServerRunning { [weak self] ready in
            guard let self = self else { return }
            self.loadAppWindow(targetPath: self.initialTargetFile)
        }
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
