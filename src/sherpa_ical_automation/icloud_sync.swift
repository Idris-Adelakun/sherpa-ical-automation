import AppKit
import EventKit
import Foundation

private let calendarTitle = "Sherpa Lessons (iCloud)"
private let ownershipPrefix = "Sherpa sync ID: "

private struct Lesson: Decodable {
    let uid: String
    let summary: String
    let start: Double
    let end: Double
    let tentative: Bool
}

private struct Request: Decodable {
    let setup: Bool
    let lessons: [Lesson]
}

private struct Target: Codable {
    let calendar: String
    var latestEnd: Double?
}

private struct Outcome: Codable {
    var added = 0
    var updated = 0
    var removed = 0
    var error: String?
}

private struct SyncError: LocalizedError {
    let errorDescription: String?
    init(_ message: String) { errorDescription = message }
}

@main
struct ICloudSync {
    @MainActor
    static func main() async {
        let arguments = CommandLine.arguments
        guard arguments.count == 4 else { return }
        _ = NSApplication.shared
        let requestURL = URL(fileURLWithPath: arguments[1])
        let resultURL = URL(fileURLWithPath: arguments[2])
        let targetURL = URL(fileURLWithPath: arguments[3])
        let outcome: Outcome
        do {
            let request = try JSONDecoder().decode(Request.self, from: Data(contentsOf: requestURL))
            let store = EKEventStore()
            if EKEventStore.authorizationStatus(for: .event) != .fullAccess {
                guard request.setup, try await store.requestFullAccessToEvents() else {
                    throw SyncError("Enable Full Access for Sherpa iCloud Sync in Calendar privacy settings.")
                }
            }
            outcome = try reconcile(request, in: store, targetURL: targetURL)
        } catch {
            outcome = Outcome(error: error.localizedDescription)
        }
        do {
            try JSONEncoder().encode(outcome).write(to: resultURL, options: .atomic)
        } catch {
            FileHandle.standardError.write(Data("Could not write iCloud sync result.\n".utf8))
        }
    }

    private static func reconcile(
        _ request: Request, in store: EKEventStore, targetURL: URL
    ) throws -> Outcome {
        let calendar: EKCalendar
        var target: Target
        if FileManager.default.fileExists(atPath: targetURL.path) {
            target = try JSONDecoder().decode(Target.self, from: Data(contentsOf: targetURL))
            guard let saved = store.calendar(withIdentifier: target.calendar),
                  isICloud(saved.source), saved.allowsContentModifications else {
                throw SyncError("The dedicated iCloud calendar is missing or read-only.")
            }
            calendar = saved
        } else {
            guard request.setup else { throw SyncError("Set up the dedicated iCloud calendar first.") }
            let sources = store.sources.filter(isICloud)
            guard sources.count == 1, let source = sources.first else {
                throw SyncError("Enable a single iCloud Calendar account on this Mac.")
            }
            guard !source.calendars(for: .event).contains(where: { $0.title == calendarTitle }) else {
                throw SyncError("An iCloud calendar named Sherpa Lessons (iCloud) already exists. Rename it before setup.")
            }
            calendar = EKCalendar(for: .event, eventStore: store)
            calendar.title = calendarTitle
            calendar.source = source
            try store.saveCalendar(calendar, commit: true)
            target = Target(calendar: calendar.calendarIdentifier)
            try JSONEncoder().encode(target).write(to: targetURL, options: .atomic)
        }

        let now = Date()
        let latest = request.lessons.map(\.end).max()
        let first = request.lessons.map(\.start).min().map(Date.init(timeIntervalSince1970:))
        let lastArchived = max(latest ?? 0, target.latestEnd ?? 0)
        let day: TimeInterval = 86_400
        var lower = min(first ?? now, now.addingTimeInterval(-day))
        let upper = max(now.addingTimeInterval(365 * day),
                        Date(timeIntervalSince1970: lastArchived + day))
        var byUID: [String: [EKEvent]] = [:]
        var seen: Set<String> = []
        while lower < upper {
            let end = min(lower.addingTimeInterval(365 * day), upper)
            let predicate = store.predicateForEvents(withStart: lower, end: end, calendars: [calendar])
            for event in store.events(matching: predicate) {
                guard let notes = event.notes, notes.hasPrefix(ownershipPrefix),
                      let marker = notes.split(separator: "\n").first,
                      let id = event.eventIdentifier, seen.insert(id).inserted else { continue }
                let uid = String(marker.dropFirst(ownershipPrefix.count))
                if UUID(uuidString: uid) != nil { byUID[uid, default: []].append(event) }
            }
            lower = end
        }

        var result = Outcome()
        for lesson in request.lessons {
            let existing = byUID.removeValue(forKey: lesson.uid) ?? []
            let start = Date(timeIntervalSince1970: lesson.start)
            let end = Date(timeIntervalSince1970: lesson.end)
            let current = existing.first(where: { $0.startDate == start }) ?? existing.first
            let event = current ?? EKEvent(eventStore: store)
            let availability: EKEventAvailability = lesson.tentative ? .free : .busy
            let flag: EKCalendarEventAvailabilityMask = lesson.tentative ? .free : .busy
            let supportsAvailability = calendar.supportedEventAvailabilities.contains(flag)
            if current == nil || event.title != lesson.summary || event.startDate != start ||
                event.endDate != end || event.isAllDay ||
                (supportsAvailability && event.availability != availability) {
                event.calendar = calendar
                event.title = lesson.summary
                event.isAllDay = false
                event.startDate = start
                event.endDate = end
                if current == nil { event.notes = ownershipPrefix + lesson.uid }
                if supportsAvailability { event.availability = availability }
                try store.save(event, span: .thisEvent, commit: false)
                if current == nil { result.added += 1 } else { result.updated += 1 }
            }
            for duplicate in existing where duplicate.eventIdentifier != event.eventIdentifier {
                try store.remove(duplicate, span: .thisEvent, commit: false)
                result.removed += 1
            }
        }
        for event in byUID.values.joined() where event.startDate > now {
            try store.remove(event, span: .thisEvent, commit: false)
            result.removed += 1
        }
        if result.added + result.updated + result.removed > 0 { try store.commit() }
        target.latestEnd = latest
        try JSONEncoder().encode(target).write(to: targetURL, options: .atomic)
        return result
    }

    private static func isICloud(_ source: EKSource) -> Bool {
        source.sourceType == .calDAV && source.title.caseInsensitiveCompare("iCloud") == .orderedSame
    }
}
