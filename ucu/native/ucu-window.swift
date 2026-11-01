// ucu-window: windows and quiet launches for ucu.
//
// ucu (Sky computer use) binds `cua.getApp("App")` to the app's main window and has no
// window selector on macOS. This helper uses the Accessibility API to list an app's windows
// and to make one of them the main window (optionally leaving full screen first), so the next
// `cua.getApp(...)` targets it.
//
//   ucu-window list [app]
//   ucu-window focus <app> <title substring> [--unfullscreen] [--activate]
//   ucu-window unfullscreen <app> <title substring>
//   ucu-window watch
//
// <app> is a display name or bundle id of a running app; `list` without one covers every app.
// `focus` un-minimizes the window and makes it the app's main window; it shows the app and brings
// it to the front only with --activate. `watch` is what the proxy runs next to cua_repl: see Watcher.
// Needs Accessibility permission for the process that runs it (Claude Code).
// Build: `ucu install` (swiftc -O native/ucu-window.swift -o ~/.local/bin/ucu-window)
import AppKit
import ApplicationServices

func fail(_ message: String, _ code: Int32 = 1) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    exit(code)
}

func attribute<T>(_ element: AXUIElement, _ name: String) -> T? {
    var value: CFTypeRef?
    guard AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success else { return nil }
    return value as? T
}

func frame(_ window: AXUIElement) -> String {
    var origin = CGPoint.zero, size = CGSize.zero
    if let value: AXValue = attribute(window, kAXPositionAttribute) { AXValueGetValue(value, .cgPoint, &origin) }
    if let value: AXValue = attribute(window, kAXSizeAttribute) { AXValueGetValue(value, .cgSize, &size) }
    return "\(Int(origin.x)),\(Int(origin.y)) \(Int(size.width))x\(Int(size.height))"
}

// The same as app_key in ucu-proxy.py: case and invisible marks (WhatsApp's name starts with one) don't count.
func key(_ name: String) -> String {
    let visible = name.unicodeScalars.filter { $0.properties.generalCategory != .format }
    return String(String.UnicodeScalarView(visible)).trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
}

func matching(_ query: String) -> NSRunningApplication? {
    let wanted = key(query)
    let apps = NSWorkspace.shared.runningApplications.filter {
        key($0.bundleIdentifier ?? "") == wanted || key($0.localizedName ?? "") == wanted
            || key($0.bundleURL?.path ?? "") == wanted
    }
    return apps.first(where: { $0.activationPolicy == .regular }) ?? apps.first
}

func runningApp(_ query: String) -> NSRunningApplication {
    guard let app = matching(query) else { fail("No running app named or with bundle id \"\(query)\".") }
    return app
}

func requireTrust() {
    guard AXIsProcessTrusted() else {
        fail("Accessibility permission is missing for the process running this helper "
            + "(System Settings > Privacy & Security > Accessibility).", 3)
    }
}

func appElement(_ pid: pid_t) -> AXUIElement {
    let element = AXUIElementCreateApplication(pid)
    AXUIElementSetMessagingTimeout(element, 1)  // a busy app must not hold up the others
    return element
}

func windows(of app: NSRunningApplication) -> (AXUIElement, [AXUIElement]) {
    requireTrust()
    let element = appElement(app.processIdentifier)
    let list: [AXUIElement] = attribute(element, kAXWindowsAttribute) ?? []
    return (element, list)
}

func isMain(_ window: AXUIElement, of element: AXUIElement) -> Bool {
    (attribute(element, kAXMainWindowAttribute) as AXUIElement?).map { CFEqual($0, window) } ?? false
}

func title(_ window: AXUIElement) -> String { attribute(window, kAXTitleAttribute) ?? "" }
func flag(_ window: AXUIElement, _ name: String) -> Bool { (attribute(window, name) as Bool?) ?? false }

// A hidden app reports all its windows as dialogs, so that says nothing there.
func kind(of window: AXUIElement, in app: NSRunningApplication) -> String? {
    switch attribute(window, kAXSubroleAttribute) as String? {
    case _ where app.isHidden: return nil
    case kAXDialogSubrole, kAXSystemDialogSubrole: return "dialog"
    case kAXFloatingWindowSubrole, kAXSystemFloatingWindowSubrole: return "panel"
    default: return nil
    }
}

func printWindows(of app: NSRunningApplication, element: AXUIElement, list: [AXUIElement]) {
    let main: AXUIElement? = attribute(element, kAXMainWindowAttribute)
    let notes = (app.isActive ? ", active" : "") + (app.isHidden ? ", hidden" : "")
    print("\(app.localizedName ?? "?") (\(app.bundleIdentifier ?? "?"), pid \(app.processIdentifier)\(notes))")
    for (index, window) in list.enumerated() {
        let marks = [main.map { CFEqual($0, window) } ?? false ? "main" : nil, kind(of: window, in: app),
                     flag(window, kAXMinimizedAttribute) ? "minimized" : nil,
                     flag(window, "AXFullScreen") ? "fullscreen" : nil, frame(window)]
        print("[\(index)] \"\(title(window))\" " + marks.compactMap { $0 }.joined(separator: " "))
    }
}

func find(_ query: String, in list: [AXUIElement]) -> AXUIElement {
    let matches = list.filter { title($0).localizedCaseInsensitiveContains(query) }
    if matches.count == 1 { return matches[0] }
    if let exact = matches.first(where: { title($0).caseInsensitiveCompare(query) == .orderedSame }) { return exact }
    let titles = list.map { "\"\(title($0))\"" }.joined(separator: ", ")
    fail(matches.isEmpty ? "No window title contains \"\(query)\". Windows: \(titles)"
                         : "\(matches.count) windows match \"\(query)\"; be more specific. Windows: \(titles)", 2)
}

func leaveFullScreen(_ window: AXUIElement) {
    guard flag(window, "AXFullScreen") else { return }
    AXUIElementSetAttributeValue(window, "AXFullScreen" as CFString, kCFBooleanFalse)
    for _ in 0..<40 where flag(window, "AXFullScreen") { usleep(100_000) }  // the animation takes ~1 s
    usleep(300_000)
}

// What the window server knows about one window, without the cost of listing them all.
func windowInfo(_ id: CGWindowID) -> [String: Any]? {
    var pointer = UnsafeRawPointer(bitPattern: UInt(id))
    guard let list = CFArrayCreate(kCFAllocatorDefault, &pointer, 1, nil) else { return nil }
    return (CGWindowListCreateDescriptionFromArray(list) as? [[String: Any]])?.first
}

func bounds(of window: [String: Any]) -> CGRect {
    (window[kCGWindowBounds as String] as? NSDictionary).flatMap { CGRect(dictionaryRepresentation: $0) } ?? .null
}

func color(_ hex: String) -> NSColor? {
    guard let value = UInt32(hex.dropFirst(), radix: 16) else { return nil }
    return NSColor(srgbRed: CGFloat(value >> 16 & 255) / 255, green: CGFloat(value >> 8 & 255) / 255,
                   blue: CGFloat(value & 255) / 255, alpha: 1)
}

func emit(_ message: [String: Any]) {
    guard var line = try? JSONSerialization.data(withJSONObject: message) else { return }
    line.append(10)
    line.withUnsafeBytes { _ = fwrite($0.baseAddress, 1, $0.count, stdout) }
    fflush(stdout)
}

func applicationURL(_ query: String) -> URL? {
    if query.hasPrefix("/") { return FileManager.default.fileExists(atPath: query) ? URL(fileURLWithPath: query) : nil }
    if let url = NSWorkspace.shared.urlForApplication(withBundleIdentifier: query) { return url }
    return NSWorkspace.shared.fullPath(forApplication: query).map(URL.init(fileURLWithPath:))
}

// What a person would read first in a window: its text and its buttons, within a budget, because a web page
// can hold thousands of elements.
func contents(of window: AXUIElement) -> (text: String, buttons: [String]) {
    let controls: Set<String> = [kAXCloseButtonSubrole, kAXMinimizeButtonSubrole, kAXZoomButtonSubrole,
                                 kAXFullScreenButtonSubrole]
    var texts = [String](), buttons = [String](), queue = [window], visited = 0, length = 0
    while visited < queue.count, visited < 300, length < 300 {
        let element = queue[visited]
        visited += 1
        let role: String = attribute(element, kAXRoleAttribute) ?? ""
        if role == kAXStaticTextRole, let value: String = attribute(element, kAXValueAttribute), !value.isEmpty,
           !texts.contains(value) {
            texts.append(value)
            length += value.count
        } else if role == kAXButtonRole, let name: String = attribute(element, kAXTitleAttribute), !name.isEmpty,
                  !controls.contains(attribute(element, kAXSubroleAttribute) ?? ""), !buttons.contains(name) {
            buttons.append(name)  // those without a title are toolbar icons, not answers to a question
        }
        queue += attribute(element, kAXChildrenAttribute) as [AXUIElement]? ?? []
    }
    return (texts.joined(separator: " "), Array(buttons.prefix(6)))
}

// A display only an agent uses, placed so that it touches the user's screens at a corner at most. Windows on it are
// on screen for macOS, so they render and take clicks by position, but nobody sees them. When it goes away, macOS
// moves them back to where they were. The classes are private, so they are looked up at run time: without them
// `open()` returns nil and apps stay hidden or are shown behind the user's windows.
final class AgentDesktop {
    static let width: UInt = 1920, height: UInt = 1200, slots = 1...8
    static let modeSelector = NSSelectorFromString("initWithWidth:height:refreshRate:")
    static let settle = 0.6

    var display: NSObject?  // the display lasts as long as this object
    var made = Date.distantFuture
    var slot: UInt32?

    // Screens are remembered by serial number, so reusing a few numbers keeps macOS's list of them short.
    func takeSlot() -> UInt32? {
        let folder = NSHomeDirectory() + "/Library/Application Support/ucu"
        try? FileManager.default.createDirectory(atPath: folder, withIntermediateDirectories: true)
        for slot in Self.slots {
            let lock = Darwin.open("\(folder)/desktop-\(slot).lock", O_CREAT | O_RDWR, 0o600)
            if lock >= 0, flock(lock, LOCK_EX | LOCK_NB) == 0 {  // the file stays open: the lock goes with this process
                self.slot = UInt32(slot)
                return self.slot
            }
            close(lock)
        }
        return nil
    }

    func make() -> NSObject? {
        guard let descriptor = (NSClassFromString("CGVirtualDisplayDescriptor") as? NSObject.Type)?.init(),
              let settings = (NSClassFromString("CGVirtualDisplaySettings") as? NSObject.Type)?.init(),
              let displayClass = NSClassFromString("CGVirtualDisplay") as AnyObject?,
              let modeClass: AnyClass = NSClassFromString("CGVirtualDisplayMode"),
              let implementation = class_getMethodImplementation(modeClass, Self.modeSelector),
              let serial = slot ?? takeSlot() else { return nil }
        descriptor.setValue("UCU Agent Desktop", forKey: "name")
        descriptor.setValue(Self.width * 2, forKey: "maxPixelsWide")
        descriptor.setValue(Self.height * 2, forKey: "maxPixelsHigh")
        descriptor.setValue(NSValue(size: NSSize(width: 520, height: 325)), forKey: "sizeInMillimeters")
        descriptor.setValue(0x55C1, forKey: "productID")
        descriptor.setValue(0x55C1, forKey: "vendorID")
        descriptor.setValue(serial, forKey: "serialNum")
        descriptor.setValue(DispatchQueue.main, forKey: "queue")
        typealias ModeInit = @convention(c) (AnyObject, Selector, UInt, UInt, Double) -> Unmanaged<AnyObject>
        let allocated = (modeClass as AnyObject).perform(NSSelectorFromString("alloc")).takeUnretainedValue()
        let start = unsafeBitCast(implementation, to: ModeInit.self)
        let mode = start(allocated, Self.modeSelector, Self.width, Self.height, 30)
        settings.setValue(1, forKey: "hiDPI")  // the engine's screenshots and coordinates assume Retina
        settings.setValue([mode.takeUnretainedValue()], forKey: "modes")
        typealias Apply = @convention(c) (AnyObject, Selector, AnyObject) -> Bool
        let apply = NSSelectorFromString("applySettings:")
        let blank: AnyObject = displayClass.perform(NSSelectorFromString("alloc")).takeUnretainedValue()
        guard let made = blank.perform(NSSelectorFromString("initWithDescriptor:"), with: descriptor),
              let display = made.takeUnretainedValue() as? NSObject,
              let method = class_getMethodImplementation(type(of: display), apply),
              unsafeBitCast(method, to: Apply.self)(display, apply, settings) else { return nil }
        return display
    }

    func open() -> Bool {
        if display == nil {
            display = make()
            made = Date()
            guard let id = displayID else { return false }
            var configuration: CGDisplayConfigRef?
            CGBeginDisplayConfiguration(&configuration)
            CGConfigureDisplayOrigin(configuration, id, -6000, -6000)  // macOS moves it next to the others, at a corner
            CGCompleteDisplayConfiguration(configuration, .forSession)
        }
        return displayID != nil
    }

    // Where the desktop is, in the coordinates windows use.
    var bounds: CGRect? { displayID.map(CGDisplayBounds).flatMap { $0.width > 0 ? $0 : nil } }

    var displayID: CGDirectDisplayID? { (display?.value(forKey: "displayID") as? NSNumber)?.uint32Value }

    // Apps hear about a new display a moment after macOS shows it. Until then, one that gets a window moved there
    // brings it back to a screen it knows, in part onto the user's.
    var isReady: Bool {
        -made.timeIntervalSinceNow > Self.settle && NSScreen.screens.contains {
            ($0.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber)?.uint32Value == displayID
        }
    }
}

// The agent's own cursor. The engine draws its cursors in windows of its service, one per chat, with no setting for how
// they look. So each chat draws its own, in its color, wherever the engine's goes: the window server still reports those
// windows while the service is hidden. Hiding the service is what takes the engine's cursors off the screen, and its
// next action brings them back for about a frame, so it is hidden again each time. Shape and shadow copy the engine's.
final class AgentCursor {
    static let serviceID = "com.openai.sky.CUAService"
    static let size: CGFloat = 126, tick = 1.0 / 120, searchEvery = 0.1, hideEvery = 0.03, linger = 20.0, fadeTime = 0.25
    static let bend: CGFloat = 0.16, tiltLimit: CGFloat = 0.42, tiltEase = 0.07, restHeading = 3 * CGFloat.pi / 4

    var color: NSColor?  // without one from the proxy, the engine's cursor stays
    var timer: Timer?
    var panel: NSPanel?
    let arrow = CAShapeLayer()
    var service: NSRunningApplication?
    var seen = [CGWindowID: (center: CGPoint, onScreen: Bool)]()  // the engine's cursors
    var nextSearch = Date.distantPast, nextHide = Date.distantPast, restAt = Date.distantFuture, lastStep = Date()
    var target: CGWindowID?  // the window under the cursor, which it stays just above
    var ticks = 0
    var position: CGPoint?, velocity = CGPoint.zero, tilt: CGFloat = 0
    var path: (start: CGPoint, control: CGPoint, end: CGPoint, began: Date, time: Double, moving: Bool)?

    func wake(_ state: @escaping () -> (busy: Bool, apps: [NSRunningApplication])) {
        guard color != nil, timer == nil else { return }
        lastStep = Date()
        timer = Timer.scheduledTimer(withTimeInterval: Self.tick, repeats: true) { [self] _ in
            let now = state()
            step(busy: now.busy, apps: now.apps)
        }
    }

    func rest() { restAt = Date() }

    func step(busy: Bool, apps: [NSRunningApplication]) {
        let now = Date(), elapsed = now.timeIntervalSince(lastStep)
        lastStep = now
        ticks += 1
        if busy {
            restAt = now + Self.linger
            follow(apps, now)
        }
        glide(now, elapsed)
        if ticks % 30 == 0 { keepInPlace() }
        if let panel, position != nil, now >= restAt {
            panel.alphaValue -= elapsed / Self.fadeTime
            if panel.alphaValue <= 0 {
                panel.orderOut(nil)
                position = nil
                path = nil
                target = nil
            }
        }
        if !busy && position == nil {
            timer?.invalidate()
            timer = nil
        }
    }

    func follow(_ apps: [NSRunningApplication], _ now: Date) {
        if now >= nextSearch {
            nextSearch = now + Self.searchEvery
            for window in serviceWindows([.optionAll]).filter(Self.isCursor) {
                if let id = window[kCGWindowNumber as String] as? CGWindowID, seen[id] == nil {
                    seen[id] = (CGPoint(x: CGFloat.nan, y: 0), false)
                }
            }
        }
        var anyOnScreen = false
        for (id, last) in seen {
            guard let window = windowInfo(id) else {
                seen[id] = nil
                continue
            }
            let frame = bounds(of: window), center = CGPoint(x: frame.midX, y: frame.midY)
            let onScreen = window[kCGWindowIsOnscreen as String] as? Bool ?? false
            anyOnScreen = anyOnScreen || onScreen
            // A click on the same spot doesn't move the window, it only brings it back.
            if center != last.center || (onScreen && !last.onScreen), let under = mine(id, at: center, apps) {
                head(to: center, over: under, now)
            }
            seen[id] = (center, onScreen)
        }
        if anyOnScreen, now >= nextHide {
            nextHide = now + Self.hideEvery
            hideEngine()
        }
    }

    static func isCursor(_ window: [String: Any]) -> Bool {
        let frame = bounds(of: window)
        return abs(frame.width - frame.height) < 1 && (60...200).contains(frame.width)
    }

    func serviceWindows(_ options: CGWindowListOption) -> [[String: Any]] {
        if service?.isTerminated ?? true {
            service = NSRunningApplication.runningApplications(withBundleIdentifier: Self.serviceID).first
        }
        guard let pid = service?.processIdentifier else { return [] }
        let list = CGWindowListCopyWindowInfo(options, kCGNullWindowID) as? [[String: Any]] ?? []
        return list.filter { ($0[kCGWindowOwnerPID as String] as? Int) == Int(pid) }
    }

    // A permission window of the service has to stay on screen, and nothing can be hidden without the Accessibility
    // permission, so then the two cursors show together.
    func hideEngine() {
        guard let service, serviceWindows([.optionOnScreenOnly]).allSatisfy(Self.isCursor) else { return }
        AXUIElementSetAttributeValue(appElement(service.processIdentifier), kAXHiddenAttribute as CFString, kCFBooleanTrue)
    }

    // The window a cursor of the engine is over, if that window is one this chat drives. The engine keeps a cursor just
    // above its window, so the next window down tells, even where windows overlap. Once it is hidden, or when another
    // chat has hidden it first, only the place is left to go by.
    func mine(_ id: CGWindowID, at point: CGPoint, _ apps: [NSRunningApplication]) -> [String: Any]? {
        let pids = Set(apps.map { Int($0.processIdentifier) }), serviceID = Int(service?.processIdentifier ?? 0)
        let list = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID)
            as? [[String: Any]] ?? []
        let top = list.first { pids.contains($0[kCGWindowOwnerPID as String] as? Int ?? 0) && bounds(of: $0).contains(point) }
        guard let place = list.firstIndex(where: { ($0[kCGWindowNumber as String] as? CGWindowID) == id }) else { return top }
        let below = list[(place + 1)...].first {
            $0[kCGWindowOwnerPID as String] as? Int != serviceID && $0[kCGWindowOwnerName as String] as? String != "ucu-window"
        }
        guard let below, pids.contains(below[kCGWindowOwnerPID as String] as? Int ?? 0) else { return nil }
        return top ?? below
    }

    func head(to end: CGPoint, over window: [String: Any], _ now: Date) {
        let panel = panel ?? makePanel()
        target = window[kCGWindowNumber as String] as? CGWindowID
        panel.level = NSWindow.Level(rawValue: window[kCGWindowLayer as String] as? Int ?? 0)
        panel.alphaValue = 1
        guard let start = position else {
            position = end
            place(panel, at: end)
            keepInPlace()
            return
        }
        let dx = end.x - start.x, dy = end.y - start.y, distance = hypot(dx, dy)
        guard distance > 0.5 else { return }
        let time = min(0.55, 0.2 + Double(distance) / 1800)
        var control: CGPoint
        let moving = path != nil
        if moving {  // already on its way: it leaves at the speed it has, so the turn is a curve and not a corner
            let lead = CGFloat(time) / 4
            control = CGPoint(x: start.x + velocity.x * lead, y: start.y + velocity.y * lead)
        } else {  // a hand swings on an arc, so the path bows upward, or to the right when it goes straight up or down
            var side = CGPoint(x: dy / distance, y: -dx / distance)
            if side.y > 0.01 || (abs(side.y) <= 0.01 && side.x < 0) { side = CGPoint(x: -side.x, y: -side.y) }
            control = CGPoint(x: (start.x + end.x) / 2 + side.x * distance * Self.bend * 2,
                              y: (start.y + end.y) / 2 + side.y * distance * Self.bend * 2)
        }
        path = (start, control, end, now, time, moving)
    }

    func glide(_ now: Date, _ elapsed: Double) {
        guard let panel, let old = position else { return }
        var turn: CGFloat = 0
        if let path {
            let t = min(1, CGFloat(now.timeIntervalSince(path.began) / path.time))
            let u = path.moving ? t * (2 - t) : t * t * (3 - 2 * t)  // from rest it eases in as well as out
            let v = 1 - u
            let next = CGPoint(x: v * v * path.start.x + 2 * v * u * path.control.x + u * u * path.end.x,
                               y: v * v * path.start.y + 2 * v * u * path.control.y + u * u * path.end.y)
            if elapsed > 0 { velocity = CGPoint(x: (next.x - old.x) / elapsed, y: (next.y - old.y) / elapsed) }
            position = next
            place(panel, at: next)
            if t >= 1 {
                self.path = nil
                velocity = .zero
            }
            // It leans toward where it is going, more the faster it goes. Screen y grows downward, the layer's upward.
            let speed = hypot(velocity.x, velocity.y)
            if speed > 1 {
                var away = atan2(-velocity.y, velocity.x) - Self.restHeading
                away = atan2(sin(away), cos(away))
                turn = max(-Self.tiltLimit, min(Self.tiltLimit, away * 0.5)) * min(1, speed / 900)
            }
        }
        tilt += (turn - tilt) * CGFloat(1 - exp(-elapsed / Self.tiltEase))
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        arrow.setAffineTransform(CGAffineTransform(rotationAngle: tilt))
        CATransaction.commit()
    }

    func place(_ panel: NSPanel, at point: CGPoint) {
        let height = NSScreen.screens.first?.frame.height ?? 0  // window coordinates count from its top, these from its bottom
        panel.setFrameOrigin(NSPoint(x: point.x - Self.size / 2, y: height - point.y - Self.size / 2))
    }

    // The window under it may have come forward or gone to another Space since.
    func keepInPlace() {
        guard let panel, let target else { return }
        if let window = windowInfo(target), window[kCGWindowIsOnscreen as String] as? Bool == true {
            panel.order(.above, relativeTo: Int(target))
        } else {
            panel.orderOut(nil)
        }
    }

    func makePanel() -> NSPanel {
        NSApplication.shared.setActivationPolicy(.accessory)  // no Dock icon, and it never becomes the active app
        let size = Self.size, bounds = NSRect(x: 0, y: 0, width: size, height: size)
        let panel = NSPanel(contentRect: bounds, styleMask: [.borderless, .nonactivatingPanel], backing: .buffered,
                            defer: false)
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = false
        panel.ignoresMouseEvents = true
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .transient, .ignoresCycle]
        let view = NSView(frame: bounds)
        view.wantsLayer = true
        panel.contentView = view
        let tint = color ?? .gray
        let shadow = CAGradientLayer()  // these stops are the engine's shadow, measured from a screenshot of it
        shadow.type = .radial
        shadow.frame = bounds
        shadow.startPoint = CGPoint(x: 0.5, y: 0.5)
        shadow.endPoint = CGPoint(x: 1, y: 1)
        shadow.colors = [0.8, 0.65, 0.44, 0.22, 0.08, 0.02, 0].map { tint.withAlphaComponent($0).cgColor }
        shadow.locations = [0, 0.14, 0.24, 0.33, 0.43, 0.52, 0.6]
        view.layer?.addSublayer(shadow)
        arrow.frame = bounds
        arrow.path = Self.arrowPath(center: size / 2)
        arrow.fillColor = (tint.blended(withFraction: 0.28, of: .black) ?? tint).cgColor  // darker than the shadow
        arrow.strokeColor = NSColor.white.cgColor
        arrow.lineWidth = 1.3
        arrow.lineJoin = .round
        view.layer?.addSublayer(arrow)
        self.panel = panel
        return panel
    }

    // A rounded dart around the click point, pointing up and to the left. Its corners are in screen directions.
    static func arrowPath(center: CGFloat) -> CGPath {
        let corners: [(x: CGFloat, y: CGFloat, radius: CGFloat)] = [
            (-6, -6, 1.6), (10.4, 0.4, 1.4), (3.4, 3.4, 2.5), (0.4, 10.4, 1.4)]
        let points = corners.map { CGPoint(x: center + $0.x, y: center - $0.y) }
        let path = CGMutablePath()
        path.move(to: CGPoint(x: (points[0].x + points[3].x) / 2, y: (points[0].y + points[3].y) / 2))
        for (index, corner) in corners.enumerated() {
            path.addArc(tangent1End: points[index], tangent2End: points[(index + 1) % 4], radius: corner.radius)
        }
        path.closeSubpath()
        return path
    }
}

// Runs next to cua_repl for as long as its stdin stays open, so it can't outlive the proxy. Commands come as one
// JSON object per line on stdin; replies and reports go out the same way on stdout.
//
//   {"id": 1, "open": "Blender", "deny": ["Terminal"]}  open a closed app hidden, unless it is denied
//   {"id": 2, "show": ["Blender"]}                      show hidden apps, behind the user's windows
//   {"id": 3, "desktop": true}                          make the agent's desktop and say where it is
//   {"id": 4, "park": ["Blender"]}                      move apps' windows to that desktop and show them there
//   {"watch": ["safari"]}                               report these apps' new windows from now on
//   {"busy": true}                                      an agent's call began (false: it ended)
//   {"allow": "Blender"}                                bring this app back and let it come to the front
//   {"cursor": "#E5484D"}                               draw the agent's cursor in this color
//   {"turnEnded": true}                                 the turn ended, so the cursor can go
//
// Reports are {"event": "window", ...} for a new window, and {"event": "focus", ...} when an app that an agent's
// action started or drives came to the front by itself and the user's app was put back.
final class Watcher {
    static let dialogOwners: Set<String> = ["SecurityAgent", "UserNotificationCenter", "CoreServicesUIAgent",
                                            "AuthenticationServicesAgent", "coreautha"]
    static let quietTail = 1.5, windowSettle = 0.3, hideGuard = 3.0, noWindowLimit = 6.0, openLimit = 45.0
    static let inputWindow = 1.5, longestCall = 300.0, moveSettle = 0.3

    var watched = Set<String>()
    var driven = [NSRunningApplication]()  // the watched apps that were running when the last call began
    var seenWindows = [pid_t: [AXUIElement]]()
    var seenDialogs = Set<Int>()
    var known = [String: NSRunningApplication]()  // finding a running app takes ~15 ms, and agents get apps often
    var keptHidden = Set<pid_t>()
    let desktop = AgentDesktop()
    let cursor = AgentCursor()
    var parked = [pid_t: (app: NSRunningApplication, places: [(AXUIElement, CGPoint)], target: CGPoint)]()
    var busySince = Date.distantFuture
    var busyUntil = Date.distantPast
    var front = NSWorkspace.shared.frontmostApplication
    var allowed: pid_t?

    func run() -> Never {
        signal(SIGPIPE, SIG_IGN)
        AXUIElementSetMessagingTimeout(AXUIElementCreateSystemWide(), 0.5)  // a stuck app must not hold up the run loop
        let center = NSWorkspace.shared.notificationCenter
        func on(_ name: Notification.Name, _ action: @escaping (NSRunningApplication) -> Void) {
            center.addObserver(forName: name, object: nil, queue: .main) { note in
                if let app = note.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication { action(app) }
            }
        }
        on(NSWorkspace.didActivateApplicationNotification) { [self] in activated($0) }
        on(NSWorkspace.didTerminateApplicationNotification) { [self] in seenWindows[$0.processIdentifier] = nil }
        scanDialogs(reporting: false)
        Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { [self] _ in
            if Date() < busyUntil { scan() }
        }
        Thread.detachNewThread { [self] in
            while let line = readLine() {
                guard let data = line.data(using: .utf8),
                      let command = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { continue }
                DispatchQueue.main.async { self.handle(command) }
            }
            DispatchQueue.main.async { self.finish() }
        }
        RunLoop.main.run()
        exit(0)
    }

    // Without the desktop, macOS moves its windows back to the user's screens, so they are hidden first.
    func finish() {
        parked.values.forEach { $0.app.hide() }
        if parked.isEmpty { exit(0) }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) { exit(0) }
    }

    func handle(_ command: [String: Any]) {
        if let hex = command["cursor"] as? String { cursor.color = color(hex) }
        if command["turnEnded"] != nil { cursor.rest() }
        if let names = command["watch"] as? [String] {
            watched.formUnion(names.map(key))
            driven = watched.compactMap(running)
        }
        if let name = command["allow"] as? String, let app = running(name) {
            allowed = app.processIdentifier
            keptHidden.remove(app.processIdentifier)
            bringBack(app)
        }
        if let busy = command["busy"] as? Bool {
            busyUntil = Date() + (busy ? Self.longestCall : Self.quietTail)  // no end comes if cua_repl crashes
            if busy {
                busySince = Date()
                front = NSWorkspace.shared.frontmostApplication
                driven = watched.compactMap(running)
                scan()
                cursor.wake { [self] in (Date() < busyUntil, driven) }
            }
        }
        if let name = command["open"] as? String {
            open(name, deny: command["deny"] as? [String] ?? [], id: command["id"] ?? NSNull())
        }
        if let names = command["show"] as? [String] { show(names, id: command["id"]) }
        if let names = command["park"] as? [String] {
            park(names, onlyHidden: command["onlyHidden"] as? Bool ?? false, id: command["id"])
        }
        if command["desktop"] != nil, let id = command["id"] {
            withDesktop { area in
                let bounds = area.map { [Int($0.minX), Int($0.minY), Int($0.width), Int($0.height)] }
                emit(["id": id, "bounds": bounds as Any])
            }
        }
    }

    func running(_ query: String) -> NSRunningApplication? {
        if let app = known[key(query)], !app.isTerminated { return app }
        known[key(query)] = matching(query)
        return known[key(query)]
    }

    // MARK: quiet launches

    func open(_ query: String, deny: [String], id: Any) {
        var app = running(query)
        guard let url = app?.bundleURL ?? applicationURL(query) else {
            emit(["id": id, "state": app == nil ? "unknown" : "running"])  // cua_repl may still find it
            return
        }
        let bundle = Bundle(url: url), info = bundle?.infoDictionary
        let names = [query, url.deletingPathExtension().lastPathComponent,
                     FileManager.default.displayName(atPath: url.path), bundle?.bundleIdentifier,
                     info?["CFBundleName"] as? String, info?["CFBundleDisplayName"] as? String]
        guard Set(names.compactMap { $0 }.map(key)).isDisjoint(with: deny.map(key)) else {
            emit(["id": id, "state": "denied"])
            return
        }
        // The name asked for may not be the one it runs under: "Visual Studio Code" runs as "Code".
        if app == nil, let identifier = bundle?.bundleIdentifier {
            app = NSRunningApplication.runningApplications(withBundleIdentifier: identifier).first
            known[key(query)] = app
        }
        if let app {
            if app.activationPolicy == .regular, !Self.hasWindows(app) {
                launch(url, id: id, state: "reopened", limit: 2)  // the engine fails on an app without a window
                return
            }
            emit(["id": id, "state": "running", "active": app.isActive])
            return
        }
        launch(url, id: id, state: "opened", limit: Self.noWindowLimit)
    }

    func launch(_ url: URL, id: Any, state: String, limit: Double) {
        let configuration = NSWorkspace.OpenConfiguration()
        configuration.activates = false
        configuration.hides = true
        configuration.addsToRecentItems = false
        configuration.promptsUserIfNeeded = false
        NSWorkspace.shared.openApplication(at: url, configuration: configuration) { app, _ in
            DispatchQueue.main.async { [self] in keepHidden(app, id: id, state: state, limit: limit) }
        }
    }

    // Some apps ignore `hides` or show themselves once their window is up (Blender), and cua_repl needs that
    // window, so this hides the app until it has one, and for a moment after in case it shows itself again.
    func keepHidden(_ launched: NSRunningApplication?, id: Any?, state: String = "opened",
                    limit: Double = noWindowLimit) {
        func reply(_ state: String) { if let id { emit(["id": id, "state": state]) } }
        guard let app = launched else {
            reply("failed")
            return
        }
        let pid = app.processIdentifier, started = Date(), element = appElement(pid), trusted = AXIsProcessTrusted()
        var finished: Date?, replied: Date?
        keptHidden.insert(pid)
        Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { [self] timer in
            if app.isTerminated || !keptHidden.contains(pid) || (app.isActive && Self.userIsActive())
                || (replied.map { -$0.timeIntervalSinceNow > Self.hideGuard } ?? false) {
                if replied == nil { reply(state) }
                keptHidden.remove(pid)
                timer.invalidate()
                return
            }
            if !app.isHidden { app.hide() }
            if finished == nil, app.isFinishedLaunching { finished = Date() }
            guard replied == nil else { return }
            let list: [AXUIElement] = trusted ? attribute(element, kAXWindowsAttribute) ?? [] : []
            let waited = finished.map { -$0.timeIntervalSinceNow > limit } ?? false
            if (app.isHidden && !list.isEmpty) || (!trusted && finished != nil) || waited
                || -started.timeIntervalSinceNow > Self.openLimit {
                reply(state)
                replied = Date()
            }
        }
    }

    func show(_ names: [String], id: Any?) {
        let apps = names.compactMap(running)
        for app in apps {
            keptHidden.remove(app.processIdentifier)
            if app.isHidden { app.unhide() }
        }
        let started = Date()
        Timer.scheduledTimer(withTimeInterval: 0.02, repeats: true) { timer in
            if apps.allSatisfy({ !$0.isHidden && Self.isOnScreen($0) }) || -started.timeIntervalSinceNow > 2 {
                timer.invalidate()
                if let id { emit(["id": id]) }
            }
        }
    }

    // macOS takes a moment to put a new display in place, and a window moved before that stays where it was.
    func withDesktop(_ action: @escaping (CGRect?) -> Void) {
        guard desktop.open() else { return action(nil) }
        let started = Date()
        if desktop.isReady { return action(desktop.bounds) }
        Timer.scheduledTimer(withTimeInterval: 0.02, repeats: true) { [self] timer in
            if desktop.isReady || -started.timeIntervalSinceNow > 3 {
                timer.invalidate()
                action(desktop.isReady ? desktop.bounds : nil)
            }
        }
    }

    func park(_ names: [String], onlyHidden: Bool, id: Any?) {
        withDesktop { [self] area in
            if let area {
                // An app that isn't hidden any more is one the user took back.
                let apps = names.compactMap(running).filter {
                    !onlyHidden || $0.isHidden || parked[$0.processIdentifier] != nil
                }
                moveToDesktop(apps, area: area, id: id)
            } else if let id {
                emit(["id": id, "state": "no desktop"])
            }
        }
    }

    // A window moved while its app shows slides there, across the user's screen. Moved while hidden, it is just there.
    func moveToDesktop(_ apps: [NSRunningApplication], area: CGRect, id: Any?) {
        let moved = apps.contains { parked[$0.processIdentifier] == nil }
        var targets = [(NSRunningApplication, CGPoint)]()
        for app in apps {
            if let place = parked[app.processIdentifier] {
                targets.append((app, place.target))
                continue
            }
            app.hide()
            keptHidden.remove(app.processIdentifier)
            let offset = CGFloat(parked.count * 40 + 30)
            let target = CGPoint(x: area.minX + offset, y: area.minY + offset)
            let list: [AXUIElement] = attribute(appElement(app.processIdentifier), kAXWindowsAttribute) ?? []
            var places = [(AXUIElement, CGPoint)]()
            for window in list {
                var origin = CGPoint.zero
                if let value: AXValue = attribute(window, kAXPositionAttribute) {
                    AXValueGetValue(value, .cgPoint, &origin)
                }
                places.append((window, origin))
                move(window, to: target)
            }
            parked[app.processIdentifier] = (app, places, target)
            targets.append((app, target))
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + (moved ? Self.moveSettle : 0)) {
            let started = Date()
            func placed() -> Bool { targets.allSatisfy { !$0.0.isHidden && Self.isOnScreen($0.0, at: $0.1) } }
            func finish() {
                if let id { emit(["id": id, "state": placed() ? "parked" : "no desktop", "moved": moved]) }
            }
            apps.filter(\.isHidden).forEach { $0.unhide() }
            if placed() { return finish() }
            Timer.scheduledTimer(withTimeInterval: 0.02, repeats: true) { timer in
                if placed() || -started.timeIntervalSinceNow > 2 {
                    timer.invalidate()
                    finish()
                }
            }
        }
    }

    func move(_ window: AXUIElement, to point: CGPoint) {
        var point = point
        if let value = AXValueCreate(.cgPoint, &point) {
            AXUIElementSetAttributeValue(window, kAXPositionAttribute as CFString, value)
        }
    }

    func bringBack(_ app: NSRunningApplication) {
        guard let place = parked.removeValue(forKey: app.processIdentifier) else { return }
        place.places.forEach { move($0.0, to: $0.1) }
        let opened: [AXUIElement] = attribute(appElement(app.processIdentifier), kAXWindowsAttribute) ?? []
        for window in opened where !place.places.contains(where: { CFEqual($0.0, window) }) {
            move(window, to: place.places.first?.1 ?? CGPoint(x: 100, y: 100))  // opened while it was on the desktop
        }
    }

    // AX leaves out windows on other Spaces, but the window server knows them.
    static func hasWindows(_ app: NSRunningApplication) -> Bool {
        let seen: [AXUIElement]? = attribute(appElement(app.processIdentifier), kAXWindowsAttribute)
        guard seen?.isEmpty == true else { return true }
        let list = CGWindowListCopyWindowInfo([.optionAll, .excludeDesktopElements], kCGNullWindowID)
            as? [[String: Any]] ?? []
        return list.contains {
            ($0[kCGWindowOwnerPID as String] as? Int) == Int(app.processIdentifier)
                && ($0[kCGWindowLayer as String] as? Int) == 0 && ($0[kCGWindowAlpha as String] as? Double ?? 0) > 0
                && ($0[kCGWindowBounds as String] as? [String: CGFloat])?["Height"] ?? 0 > 60
        }
    }

    static func isOnScreen(_ app: NSRunningApplication, at point: CGPoint? = nil) -> Bool {
        let list = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID)
            as? [[String: Any]] ?? []
        return list.contains { window in
            guard (window[kCGWindowOwnerPID as String] as? Int) == Int(app.processIdentifier) else { return false }
            guard let point, let bounds = window[kCGWindowBounds as String] as? [String: CGFloat] else { return true }
            return abs((bounds["X"] ?? .infinity) - point.x) < 2 && abs((bounds["Y"] ?? .infinity) - point.y) < 2
        }
    }

    // MARK: new windows

    func scan() {
        scanDialogs(reporting: true)
        driven.forEach(scanWindows)
    }

    // Windows are compared because an app only announces one once it is on screen, which never happens while the
    // app is hidden. Those already open the first time an app is seen aren't new.
    func scanWindows(of app: NSRunningApplication) {
        let pid = app.processIdentifier
        guard let list: [AXUIElement] = attribute(appElement(pid), kAXWindowsAttribute) else { return }
        let current = list + list.flatMap { attribute($0, "AXSheets") as [AXUIElement]? ?? [] }
        defer { seenWindows[pid] = current }
        guard let before = seenWindows[pid] else { return }
        for window in current where !before.contains(where: { CFEqual($0, window) }) { appeared(window, in: app) }
    }

    // The window is read a moment later, once it has its content. Menus and tooltips have closed by then.
    func appeared(_ window: AXUIElement, in app: NSRunningApplication) {
        DispatchQueue.main.asyncAfter(deadline: .now() + Self.windowSettle) { [self] in
            guard let role: String = attribute(window, kAXRoleAttribute) else { return }
            let subrole: String = attribute(window, kAXSubroleAttribute) ?? ""
            guard role == kAXSheetRole || (role == kAXWindowRole && subrole != kAXUnknownSubrole) else { return }
            let main: AXUIElement? = attribute(appElement(app.processIdentifier), kAXMainWindowAttribute)
            let name = role == kAXSheetRole ? "sheet" : kind(of: window, in: app) ?? "window"
            report(window, owner: app.localizedName ?? "?", kind: name, main: main.map { CFEqual($0, window) } ?? false)
        }
    }

    func report(_ window: AXUIElement?, owner: String, kind: String, main: Bool = false) {
        var event: [String: Any] = ["event": "window", "app": owner, "kind": kind, "main": main]
        if let window {
            let found = contents(of: window)
            event["title"] = title(window)
            event["text"] = found.text == title(window) ? "" : found.text
            event["buttons"] = found.buttons
        }
        emit(event)
    }

    // Password and permission prompts come from system processes, not from the app that caused them, and those
    // processes can't be observed in advance. Their AX windows can still be read once they are on screen.
    func scanDialogs(reporting: Bool) {
        let list = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID)
            as? [[String: Any]] ?? []
        var told = Set<String>()
        for window in list {
            guard let owner = window[kCGWindowOwnerName as String] as? String, Self.dialogOwners.contains(owner),
                  let id = window[kCGWindowNumber as String] as? Int, seenDialogs.insert(id).inserted,
                  reporting, told.insert(owner).inserted,
                  let pid = window[kCGWindowOwnerPID as String] as? Int else { continue }
            let found: [AXUIElement] = attribute(appElement(pid_t(pid)), kAXWindowsAttribute) ?? []
            report(found.first, owner: owner, kind: "system dialog")
        }
    }

    // MARK: focus

    // The HID state only counts real input, not the events cua_repl sends to an app. Any click, key or swipe just
    // before may be the user switching apps themselves, and undoing that would be worse than leaving a stray app up.
    static func userIsActive(within seconds: Double = inputWindow) -> Bool {
        let swipe = CGEventType(rawValue: 29)!  // a trackpad gesture, which can change Spaces; it has no name here
        let types: [CGEventType] = [.leftMouseDown, .rightMouseDown, .otherMouseDown, .keyDown, .flagsChanged,
                                    .scrollWheel, swipe]
        return types.contains {
            CGEventSource.secondsSinceLastEventType(.hidSystemState, eventType: $0) < seconds
        }
    }

    func activated(_ app: NSRunningApplication) {
        let previous = front, pid = app.processIdentifier, launched = app.launchDate ?? .distantPast
        let isNew = launched > busySince
        // An app takes seconds to start, so a new one counts as the user's if they did anything since just before it.
        let sinceInput = isNew ? -launched.timeIntervalSinceNow + Self.inputWindow : Self.inputWindow
        front = app
        if Self.userIsActive() { bringBack(app) }  // they clicked its Dock icon: its windows can't stay out of sight
        guard Date() < busyUntil, let previous, previous.processIdentifier != pid, !previous.isTerminated,
              pid != allowed, isNew || driven.contains(app), !Self.userIsActive(within: sinceInput) else { return }
        front = previous
        AXUIElementSetAttributeValue(appElement(previous.processIdentifier), kAXFrontmostAttribute as CFString,
                                     kCFBooleanTrue)
        if isNew { keepHidden(app, id: nil) }
        emit(["event": "focus", "app": app.localizedName ?? "?", "back": previous.localizedName ?? "?"])
    }
}

let watcher = Watcher()
let args = Array(CommandLine.arguments.dropFirst())
guard let command = args.first else {
    fail("usage: ucu-window list [app] | focus <app> <title> [--unfullscreen] [--activate] | "
        + "unfullscreen <app> <title> | watch")
}
if command == "watch" { watcher.run() }
if command == "list", args.count == 1 {
    requireTrust()
    for app in NSWorkspace.shared.runningApplications where app.activationPolicy == .regular {
        let (element, list) = windows(of: app)
        if !list.isEmpty { printWindows(of: app, element: element, list: list) }
    }
    exit(0)
}
guard args.count >= 2 else { fail("usage: ucu-window \(command) <app>") }
let app = runningApp(args[1])
let (element, list) = windows(of: app)

switch command {
case "list":
    printWindows(of: app, element: element, list: list)
case "focus", "unfullscreen":
    guard args.count >= 3 else { fail("usage: ucu-window \(command) <app> <title substring>") }
    let window = find(args[2], in: list)
    if command == "unfullscreen" || args.contains("--unfullscreen") { leaveFullScreen(window) }
    if command == "focus" {
        if flag(window, kAXMinimizedAttribute) {
            AXUIElementSetAttributeValue(window, kAXMinimizedAttribute as CFString, kCFBooleanFalse)
        }
        AXUIElementSetAttributeValue(window, kAXMainAttribute as CFString, kCFBooleanTrue)
        if args.contains("--activate") {
            AXUIElementPerformAction(window, kAXRaiseAction as CFString)
            app.unhide()
            app.activate()
        }
        for _ in 0..<20 where !isMain(window, of: element) { usleep(10_000) }
    }
    let isMain = isMain(window, of: element)
    print("\"\(title(window))\" main=\(isMain) fullscreen=\(flag(window, "AXFullScreen")) frame=\(frame(window))")
    exit(command == "focus" && !isMain ? 4 : 0)
default:
    fail("unknown command \(command)")
}
