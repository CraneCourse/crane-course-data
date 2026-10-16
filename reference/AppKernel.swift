// Minimal data-model and matching reference, unchanged algorithms from app c37e603.
import Foundation
import CoreLocation

struct Point: Codable, Equatable, Sendable {
    let latitude: Double
    let longitude: Double

    // Match the existing Android JSON, including downloaded courses.
    enum CodingKeys: String, CodingKey {
        case latitude = "lat"
        case longitude = "lon"
    }

    func meters(to other: Point) -> Double {
        CLLocation(latitude: latitude, longitude: longitude).distance(
            from: CLLocation(latitude: other.latitude, longitude: other.longitude))
    }

    func yards(to other: Point) -> Int {
        Int((meters(to: other) * CourseData.yardsPerMeter).rounded())
    }
}

struct CourseFeature: Codable, Equatable, Sendable {
    let kind: String
    let points: [Point]
    var interiorRings: [[Point]]? = nil
}

struct Hole: Codable, Equatable, Identifiable, Sendable {
    var id: Int { number }
    let number: Int
    let par: Int
    let teeYardages: [String: Int]
    let tee: Point
    let front: Point
    let center: Point
    let back: Point
    let path: [Point]
    let features: [CourseFeature]

    /// Scorecard-derived preview position, not a surveyed tee marker.
    func pathFromTee(_ name: String) -> [Point] {
        guard let yards = teeYardages[name] else {
            return path.isEmpty ? [tee, center] : path
        }
        var route = path.isEmpty ? [tee, center] : path
        if route[route.count - 1].meters(to: center) > 1 {
            route.append(center)
        } else {
            route[route.count - 1] = center
        }
        var remaining = Double(yards) / CourseData.yardsPerMeter
        if route.count > 1 {
            for index in stride(from: route.count - 1, through: 1, by: -1) {
                let near = route[index]
                let far = route[index - 1]
                let segment = near.meters(to: far)
                if remaining <= segment && segment > 0 {
                    let fraction = remaining / segment
                    let start = Point(
                        latitude: near.latitude + (far.latitude - near.latitude) * fraction,
                        longitude: near.longitude + (far.longitude - near.longitude) * fraction)
                    return [start] + Array(route[index...])
                }
                remaining -= segment
            }
        }
        guard route.count >= 2 else { return [route[0], center] }
        let start = route[0]
        let next = route[1]
        let extra = max(remaining, 0) / max(start.meters(to: next), 1)
        return [Point(latitude: start.latitude + (start.latitude - next.latitude) * extra,
                      longitude: start.longitude + (start.longitude - next.longitude) * extra)] + route
    }

    func teeFor(_ name: String) -> Point { pathFromTee(name)[0] }

    /// The scorecard-derived point is only an estimate. Match a nearby mapped
    /// tee surface conservatively; course features can include neighboring tees.
    func progressionTeeArea(for name: String) -> CourseFeature? {
        let estimated = teeFor(name)
        let candidates = features.compactMap { feature -> (CourseFeature, Double)? in
            guard feature.kind == "tee", feature.interiorRings?.isEmpty != false,
                  (3...128).contains(feature.points.count),
                  feature.points.allSatisfy({ $0.isValid && $0.meters(to: estimated) <= 100 }),
                  let distance = feature.distanceToSurface(from: estimated), distance <= 35 else { return nil }
            return (feature, distance)
        }.sorted { $0.1 < $1.1 }
        guard let nearest = candidates.first,
              candidates.count == 1 || candidates[1].1 > nearest.1 + 10 else { return nil }
        return nearest.0
    }
}

extension CourseFeature {
    /// Local metre projection and point-in-polygon/edge distance for tee arrival
    /// only. This never changes green positions, playable yardages or map data.
    func distanceToSurface(from point: Point) -> Double? {
        guard point.isValid, points.count >= 3, points.allSatisfy(\.isValid) else { return nil }
        let scale = cos(point.latitude * .pi / 180)
        let vertices = points.map { p in
            (x: (p.longitude - point.longitude) * .pi / 180 * 6_371_000 * scale,
             y: (p.latitude - point.latitude) * .pi / 180 * 6_371_000)
        }
        var inside = false
        var minimum = Double.infinity
        var twiceArea = 0.0
        for index in vertices.indices {
            let a = vertices[index], b = vertices[(index + 1) % vertices.count]
            twiceArea += a.x * b.y - b.x * a.y
            if (a.y > 0) != (b.y > 0), 0 < a.x + (b.x - a.x) * -a.y / (b.y - a.y) {
                inside.toggle()
            }
            let dx = b.x - a.x, dy = b.y - a.y
            let lengthSquared = dx * dx + dy * dy
            let fraction = lengthSquared > 0 ? min(1, max(0, -(a.x * dx + a.y * dy) / lengthSquared)) : 0
            minimum = min(minimum, hypot(a.x + fraction * dx, a.y + fraction * dy))
        }
        guard abs(twiceArea) >= 1, minimum.isFinite else { return nil }
        return inside ? 0 : minimum
    }
}

enum CourseData { static let yardsPerMeter = 1.0936133 }
extension Point { var isValid: Bool { latitude.isFinite && longitude.isFinite && abs(latitude) <= 90 && abs(longitude) <= 180 } }
struct CourseDownloadError: LocalizedError { let message: String; let retryable: Bool; init(_ message: String, retryable: Bool = true) { self.message = message; self.retryable = retryable }; var errorDescription: String? { message } }
enum CourseDirectory {
    static func parseFeatures(_ data: Data, courseID: String) throws -> Geometry {
        // A missing or error response may be transient; a feature that breaks the rules is
        // a data problem retrying won't fix, so it reads as unsupported, not as "offline".
        func invalid(retryable: Bool = false) -> CourseDownloadError {
            CourseDownloadError(retryable ? "The course map data could not be verified. Please try again later."
                                          : "This course's map data has features we can't verify yet.", retryable: retryable)
        }
        guard let root = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              root["error"] == nil, let features = root["features"] as? [[String: Any]],
              features.count <= 10_000 else { throw invalid(retryable: true) }
        let kinds = ["hole_line": "hole", "green": "green", "fairway": "fairway",
                     "tee": "tee", "bunker": "bunker", "water_hazard": "water_hazard"]
        var ways: [Way] = []
        var identifiers = Set<Int>()
        func points(_ coordinates: [[Double]], ring: Bool) throws -> [Point] {
            let result = try coordinates.map { coordinate -> Point in
                guard coordinate.count == 2 else { throw invalid() }
                let point = Point(latitude: coordinate[1], longitude: coordinate[0])
                guard point.isValid else { throw invalid() }
                return point
            }
            guard result.count >= (ring ? 4 : 2), !ring || result.first == result.last else { throw invalid() }
            return result
        }
        for feature in features {
            guard let type = feature["feature_type"] as? String, let kind = kinds[type] else { continue }
            guard let osmID = feature["osm_id"] as? String else { throw invalid() }
            guard osmID.hasPrefix("way/") else { continue }
            guard feature["course_id"] as? String == courseID, feature["source"] as? String == "osm",
                  feature["license"] as? String == "odbl", let id = Int(osmID.dropFirst(4)), id > 0,
                  identifiers.insert(id).inserted, let rawTags = feature["tags"] as? [String],
                  let geometry = feature["geometry"] as? [String: Any] else { throw invalid() }
            var tags: [String: String] = [:]
            for raw in rawTags {
                let pair = raw.split(separator: "=", maxSplits: 1).map(String.init)
                guard pair.count == 2, tags.updateValue(pair[1], forKey: pair[0]) == nil else { throw invalid() }
            }
            // The API also labels nearby rivers/lakes as hazards. Preserve the
            // original golf-tagged scope so unrelated water cannot expand a map.
            if kind == "water_hazard", tags["golf"] == nil { continue }
            // OSM marks red-staked hazards golf=lateral_water_hazard; both are water.
            let golfTags = kind == "water_hazard" ? ["water_hazard", "lateral_water_hazard"] : [kind]
            guard let golf = tags["golf"], golfTags.contains(golf) else { throw invalid() }
            if kind == "hole", let number = feature["hole_number"] as? Int {
                guard (1...36).contains(number), tags["ref"] == nil || Int(tags["ref"]!) == number else { throw invalid() }
                tags["ref"] = String(number)
            }
            if kind == "hole" {
                guard geometry["type"] as? String == "LineString",
                      let coordinates = geometry["coordinates"] as? [[Double]] else { throw invalid() }
                ways.append(Way(id: id, tags: tags, geometry: try points(coordinates, ring: false)))
                continue
            }
            let polygons: [[[[Double]]]]
            switch geometry["type"] as? String {
            case "LineString":
                // Some OSM area ways arrive as closed lines (e.g. PGA West's
                // tee way/395164684). Preserve their ring exactly; the same
                // closed-ring validation below still rejects open boundaries.
                guard let coordinates = geometry["coordinates"] as? [[Double]] else { throw invalid() }
                polygons = [[coordinates]]
            case "Polygon":
                guard let coordinates = geometry["coordinates"] as? [[[Double]]] else { throw invalid() }
                polygons = [coordinates]
            case "MultiPolygon":
                guard let coordinates = geometry["coordinates"] as? [[[[Double]]]] else { throw invalid() }
                polygons = coordinates
            default: throw invalid()
            }
            // Yardages require one unambiguous green, not a guessed component or
            // a center inside an excluded interior area.
            guard !polygons.isEmpty, kind != "green" || (polygons.count == 1 && polygons[0].count == 1) else {
                throw invalid()
            }
            for polygon in polygons {
                guard let exterior = polygon.first else { throw invalid() }
                let interiors = try polygon.dropFirst().map { try points($0, ring: true) }
                ways.append(Way(id: id, tags: tags, geometry: try points(exterior, ring: true),
                                interiorRings: interiors.isEmpty ? nil : interiors))
            }
        }
        return Geometry(elements: ways)
    }

    struct Scorecard: Decodable { let holes: [Card] }
    struct Card: Decodable { let number: Int; let par: Int; let yardages: [String: Int]? }
    struct Geometry: Codable { let elements: [Way] }
    struct Way: Codable {
        let id: Int
        let tags: [String: String]?
        let geometry: [Point]?
        var interiorRings: [[Point]]? = nil
        var kind: String { tags?["golf"] ?? "" }
        var number: Int? { Int(tags?["ref"] ?? "") ?? Int((tags?["name"] ?? "").filter(\.isNumber)) }
        var centroid: Point {
            var points = geometry ?? []
            // Closed rings repeat the first vertex. Counting it twice shifts the
            // center when an equivalent provider rotates the ring's start point.
            if points.count > 1, points.first == points.last { points.removeLast() }
            return Point(latitude: points.map(\.latitude).reduce(0,+) / Double(points.count),
                         longitude: points.map(\.longitude).reduce(0,+) / Double(points.count))
        }
    }
    static func match(scorecard: Data, geometry: Data, center: Point) throws -> [Hole] {
        try match(scorecard: scorecard, geometry: JSONDecoder().decode(Geometry.self, from: geometry), center: center)
    }
    static func validatedCards(_ scorecard: Data) throws -> [Card] {
        let cards = try JSONDecoder().decode(Scorecard.self, from: scorecard).holes.sorted { $0.number < $1.number }
        guard (cards.count == 9 || cards.count == 18), cards.map(\.number) == Array(1...cards.count),
              cards.allSatisfy({ (3...6).contains($0.par) }) else { throw CourseDownloadError("A complete numbered scorecard is not available for this course.", retryable: false) }
        return cards
    }
    static func match(scorecard: Data, geometry: Geometry, center: Point) throws -> [Hole] {
        let cards = try validatedCards(scorecard)
        let ways = geometry.elements.filter {
            guard let points = $0.geometry, points.count >= 2 else { return false }
            return (points + ($0.interiorRings ?? []).flatMap { $0 }).allSatisfy { $0.isValid && $0.meters(to: center) < 3500 }
        }
        let paths = ways.filter { $0.kind == "hole" && $0.number != nil }
        let groups = Dictionary(grouping: paths, by: { $0.number! })
        guard cards.allSatisfy({ groups[$0.number]?.count == 1 }) else {
            throw CourseDownloadError("Mapped hole numbers are missing or ambiguous. This course cannot be downloaded reliably yet.", retryable: false)
        }
        var usedGreens = Set<Int>()
        let result = try cards.map { card -> Hole in
            let path = groups[card.number]![0].geometry!
            let near = ways.filter { $0.kind == "green" && ($0.geometry?.count ?? 0) >= 4 }
                .sorted { $0.centroid.meters(to: path.last!) < $1.centroid.meters(to: path.last!) }
            guard let green = near.first, green.centroid.meters(to: path.last!) < 60,
                  near.count < 2 || near[1].centroid.meters(to: path.last!) > green.centroid.meters(to: path.last!) + 10,
                  usedGreens.insert(green.id).inserted else { throw CourseDownloadError("The green for hole \(card.number) cannot be matched reliably.", retryable: false) }
            let center = green.centroid, tee = path[0]
            guard tee.meters(to: center) > 25 else { throw CourseDownloadError("Hole \(card.number) has an invalid mapped path.", retryable: false) }
            let yards = (card.yardages ?? [:]).filter { !$0.key.isEmpty && (30...900).contains($0.value) }
            guard !yards.isEmpty else { throw CourseDownloadError("Hole \(card.number) has no usable tee yardages.", retryable: false) }
            let length = zip(path, path.dropFirst()).reduce(0.0) { $0 + $1.0.meters(to: $1.1) } * CourseData.yardsPerMeter
            let longest = Double(yards.values.max()!)
            guard length > longest * 0.45, length < longest * 1.7 else { throw CourseDownloadError("Hole \(card.number)'s map does not match its scorecard distance.", retryable: false) }
            let ax = (center.longitude - tee.longitude) * cos(center.latitude * .pi / 180)
            let ay = center.latitude - tee.latitude
            func projection(_ p: Point) -> Double { (p.longitude - center.longitude) * cos(center.latitude * .pi / 180) * ax + (p.latitude - center.latitude) * ay }
            let greenPoints = green.geometry!
            let features = ways.filter { way in
                guard way.kind != "hole" else { return false }
                return paths.min { a,b in
                    a.geometry!.map { $0.meters(to: way.centroid) }.min()! < b.geometry!.map { $0.meters(to: way.centroid) }.min()!
                }?.number == card.number
            }.map { CourseFeature(kind: $0.kind, points: $0.geometry!, interiorRings: $0.interiorRings) }
            return Hole(number: card.number, par: card.par, teeYardages: yards, tee: tee,
                        front: greenPoints.min { projection($0) < projection($1) }!, center: center,
                        back: greenPoints.max { projection($0) < projection($1) }!,
                        path: Array(path.dropLast()) + [center], features: features)
        }
        guard result.dropFirst().reduce(Set(result[0].teeYardages.keys), { $0.intersection($1.teeYardages.keys) }).count > 0 else {
            throw CourseDownloadError("No tee has yardages for every hole.", retryable: false)
        }
        return result
    }
}
