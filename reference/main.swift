import Foundation

// Identical fields to a newly started validation round; optional nil fields are omitted.
struct ProgressionSetting: Codable { var automatic: Bool; var counter: Int; var device: String }
struct ValidationRound: Codable {
    var id = UUID(uuidString: "00000000-0000-0000-0000-000000000000")!
    var startedAt = Date(timeIntervalSinceReferenceDate: 814000000.123456)
    var courseName: String
    var courseCenter: Point
    var tee: String
    var holes: [Hole]
    var progression = ProgressionSetting(automatic: true, counter: 0, device: "validation")
    var watchPayload: Self {
        var copy = self
        copy.holes = holes.map {
            Hole(number: $0.number, par: $0.par, teeYardages: $0.teeYardages,
                 tee: $0.tee, front: $0.front, center: $0.center, back: $0.back,
                 path: $0.path, features: $0.progressionTeeArea(for: tee).map { [$0] } ?? [])
        }
        if let data = try? JSONEncoder().encode(copy), data.count >= 55_000 {
            copy.holes = copy.holes.map {
                Hole(number: $0.number, par: $0.par, teeYardages: $0.teeYardages,
                     tee: $0.tee, front: $0.front, center: $0.center, back: $0.back,
                     path: $0.path, features: [])
            }
        }
        return copy
    }
}
guard ProcessInfo.processInfo.environment["GITHUB_ACTIONS"] == "true" else {
    fatalError("Course processing is restricted to GitHub Actions")
}
let root = URL(fileURLWithPath: CommandLine.arguments[1])
let encoder = JSONEncoder()
encoder.outputFormatting = [.sortedKeys]
var output: [String: Any] = [:]
for file in try FileManager.default.contentsOfDirectory(at: root, includingPropertiesForKeys: nil).sorted(by: { $0.path < $1.path }) where file.pathExtension == "json" {
    let data = try Data(contentsOf: file)
    let row = try JSONSerialization.jsonObject(with: data) as! [String: Any]
    let candidate = row["candidate"] as! [String: Any]
    let id = candidate["id"] as! String
    func bytes(_ value: Any) throws -> Data { try JSONSerialization.data(withJSONObject: value) }
    var controlGeometry: Any? = nil
    do {
        let center = try JSONDecoder().decode(Point.self, from: bytes(candidate["center"]!))
        let geometry: CourseDirectory.Geometry
        if let features = row["providerFeatures"] {
            geometry = try CourseDirectory.parseFeatures(bytes(features), courseID: id)
        } else {
            geometry = try JSONDecoder().decode(CourseDirectory.Geometry.self, from: bytes(row["geometry"]!))
        }
        if row["providerFeatures"] != nil { controlGeometry = try JSONSerialization.jsonObject(with: encoder.encode(geometry)) }
        let holes = try CourseDirectory.match(scorecard: bytes(row["scorecard"]!), geometry: geometry, center: center)
        let tees = holes.dropFirst().reduce(Set(holes[0].teeYardages.keys)) { $0.intersection($1.teeYardages.keys) }.sorted()
        let sizes = try tees.map { tee in
            try encoder.encode(ValidationRound(courseName: candidate["name"] as! String, courseCenter: center, tee: tee, holes: holes).watchPayload).count
        }
        output[file.deletingPathExtension().lastPathComponent] = ["pass": true, "holes": try JSONSerialization.jsonObject(with: encoder.encode(holes)), "watchBytes": sizes.max()!]
    } catch {
        output[file.deletingPathExtension().lastPathComponent] = ["pass": false, "reason": error.localizedDescription]
    }
    if let geometry = controlGeometry {
        var entry = output[file.deletingPathExtension().lastPathComponent] as! [String: Any]
        entry["parsedGeometry"] = geometry
        output[file.deletingPathExtension().lastPathComponent] = entry
    }
}
try JSONSerialization.data(withJSONObject: output, options: [.sortedKeys]).write(to: URL(fileURLWithPath: CommandLine.arguments[2]))
