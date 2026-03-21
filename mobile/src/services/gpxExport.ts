/**
 * OpenCatch — GPX/KML Import/Export Service
 *
 * Full GPX 1.1 and KML export for interoperability with Google Earth,
 * Garmin, Navionics, and other navigation apps.
 *
 * Features:
 * - Multi-track GPX export with waypoints, route points, speeds, elevations
 * - KML export for Google Earth with styled track lines
 * - Multi-track GPX/KML import with preview metadata
 * - Native share sheet integration
 */

import { Share, Platform } from 'react-native';
import type { FishingTrack, TrackPoint, TrackWaypoint } from './trackRecorder';

// expo-file-system and expo-sharing are optional — only used for native share
let FileSystem: any;
let Sharing: any;
try { FileSystem = require('expo-file-system'); } catch {}
try { Sharing = require('expo-sharing'); } catch {}

// ── XML Helpers ──────────────────────────────────────────────────────────────

function escapeXml(str: string): string {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');
}

function isoTime(ts: number): string {
  return new Date(ts).toISOString();
}

// ── Haversine (for imported tracks) ──────────────────────────────────────────

function haversine(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const R = 3958.8;
  const dLat = (lat2 - lat1) * Math.PI / 180;
  const dLon = (lon2 - lon1) * Math.PI / 180;
  const a = Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
    Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function totalDistance(points: TrackPoint[]): number {
  let total = 0;
  for (let i = 1; i < points.length; i++) {
    total += haversine(points[i - 1].lat, points[i - 1].lon, points[i].lat, points[i].lon);
  }
  return total;
}

// ── GPX Export ───────────────────────────────────────────────────────────────

/**
 * Export one or more tracks as a GPX 1.1 XML string.
 * Includes all track points, waypoints, speeds, elevations, and timestamps.
 */
export function exportTracksAsGPX(tracks: FishingTrack[]): string {
  if (tracks.length === 0) return '';

  const allWaypoints = tracks.flatMap((track) =>
    (track.waypoints ?? []).map((wp) => {
      const sym = wp.type === 'catch' ? 'Fish' : 'Flag';
      return `  <wpt lat="${wp.lat}" lon="${wp.lon}">
    <name>${escapeXml(wp.label ?? wp.type)}</name>
    <time>${isoTime(wp.timestamp)}</time>
    <type>${escapeXml(wp.type)}</type>
    <sym>${sym}</sym>
    ${wp.species ? `<desc>Species: ${escapeXml(wp.species)}</desc>` : ''}
  </wpt>`;
    }),
  ).join('\n');

  const allTracks = tracks.map((track) => {
    const segments = buildTrackSegments(track.points);
    const segs = segments.map((seg) => {
      const pts = seg.map((p) => {
        const parts: string[] = [];
        if (p.altitude !== undefined) parts.push(`        <ele>${p.altitude.toFixed(1)}</ele>`);
        parts.push(`        <time>${isoTime(p.timestamp)}</time>`);
        if (p.speed !== undefined) parts.push(`        <speed>${p.speed.toFixed(2)}</speed>`);
        if (p.heading !== undefined) parts.push(`        <course>${p.heading.toFixed(1)}</course>`);
        return `      <trkpt lat="${p.lat}" lon="${p.lon}">\n${parts.join('\n')}\n      </trkpt>`;
      }).join('\n');
      return `    <trkseg>\n${pts}\n    </trkseg>`;
    }).join('\n');

    return `  <trk>
    <name>${escapeXml(track.name)}</name>
    ${track.notes ? `<desc>${escapeXml(track.notes)}</desc>` : ''}
${segs}
  </trk>`;
  }).join('\n');

  const firstName = tracks[0].name;
  const metaName = tracks.length === 1 ? firstName : `${firstName} (+${tracks.length - 1} more)`;

  return `<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="OpenCatch"
  xmlns="http://www.topografix.com/GPX/1/1"
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
  xsi:schemaLocation="http://www.topografix.com/GPX/1/1 http://www.topografix.com/GPX/1/1/gpx.xsd">
  <metadata>
    <name>${escapeXml(metaName)}</name>
    <time>${isoTime(tracks[0].startTime)}</time>
    <desc>Exported from OpenCatch</desc>
  </metadata>
${allWaypoints}
${allTracks}
</gpx>`;
}

/**
 * Build track segments. If there's a gap > 5 min between points,
 * start a new segment (handles pause/resume).
 */
function buildTrackSegments(points: TrackPoint[]): TrackPoint[][] {
  if (points.length === 0) return [];
  const GAP_THRESHOLD_MS = 5 * 60 * 1000;
  const segments: TrackPoint[][] = [[points[0]]];
  for (let i = 1; i < points.length; i++) {
    const gap = points[i].timestamp - points[i - 1].timestamp;
    if (gap > GAP_THRESHOLD_MS) {
      segments.push([points[i]]);
    } else {
      segments[segments.length - 1].push(points[i]);
    }
  }
  return segments;
}

// ── KML Export ───────────────────────────────────────────────────────────────

/**
 * Export one or more tracks as KML for Google Earth.
 * Includes styled track lines and waypoint placemarks.
 */
export function exportTracksAsKML(tracks: FishingTrack[]): string {
  if (tracks.length === 0) return '';

  const placemarks = tracks.map((track) => {
    // Track line
    const coords = track.points
      .map((p) => {
        const alt = p.altitude !== undefined ? p.altitude.toFixed(1) : '0';
        return `${p.lon},${p.lat},${alt}`;
      })
      .join('\n            ');

    const trackPlacemark = `    <Placemark>
      <name>${escapeXml(track.name)}</name>
      ${track.notes ? `<description>${escapeXml(track.notes)}</description>` : ''}
      <Style>
        <LineStyle>
          <color>ff0a6ebd</color>
          <width>3</width>
        </LineStyle>
      </Style>
      <TimeSpan>
        <begin>${isoTime(track.startTime)}</begin>
        ${track.endTime ? `<end>${isoTime(track.endTime)}</end>` : ''}
      </TimeSpan>
      <LineString>
        <extrude>0</extrude>
        <tessellate>1</tessellate>
        <altitudeMode>clampToGround</altitudeMode>
        <coordinates>
            ${coords}
        </coordinates>
      </LineString>
    </Placemark>`;

    // Waypoints
    const wpPlacemarks = (track.waypoints ?? []).map((wp) => {
      const iconUrl = wp.type === 'catch'
        ? 'http://maps.google.com/mapfiles/kml/shapes/fishing.png'
        : 'http://maps.google.com/mapfiles/kml/shapes/flag.png';
      return `    <Placemark>
      <name>${escapeXml(wp.label ?? wp.type)}</name>
      ${wp.species ? `<description>Species: ${escapeXml(wp.species)}</description>` : ''}
      <Style>
        <IconStyle>
          <Icon><href>${iconUrl}</href></Icon>
        </IconStyle>
      </Style>
      <TimeStamp><when>${isoTime(wp.timestamp)}</when></TimeStamp>
      <Point>
        <coordinates>${wp.lon},${wp.lat},0</coordinates>
      </Point>
    </Placemark>`;
    }).join('\n');

    return [trackPlacemark, wpPlacemarks].filter(Boolean).join('\n');
  }).join('\n');

  const firstName = tracks[0].name;
  const docName = tracks.length === 1 ? firstName : `${firstName} (+${tracks.length - 1} more)`;

  return `<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>${escapeXml(docName)}</name>
    <description>Exported from OpenCatch</description>
${placemarks}
  </Document>
</kml>`;
}

// ── GPX Import ───────────────────────────────────────────────────────────────

/** Preview metadata returned before full import */
export interface ImportPreview {
  trackCount: number;
  totalPoints: number;
  totalWaypoints: number;
  trackNames: string[];
  totalDistanceMiles: number;
  estimatedDurationMinutes: number;
}

/**
 * Parse a GPX string into FishingTrack[] objects.
 * Handles multi-track files, waypoints, route points, and track segments.
 */
export function importGPX(gpxString: string): FishingTrack[] {
  const tracks: FishingTrack[] = [];

  // ── Parse standalone waypoints (outside <trk>) ──
  const globalWaypoints: TrackWaypoint[] = [];
  const wptRegex = /<wpt\s+lat="([^"]+)"\s+lon="([^"]+)"[^>]*>([\s\S]*?)<\/wpt>/g;
  let wptMatch;
  while ((wptMatch = wptRegex.exec(gpxString)) !== null) {
    const lat = parseFloat(wptMatch[1]);
    const lon = parseFloat(wptMatch[2]);
    const content = wptMatch[3];
    const name = extractTag(content, 'name') ?? 'Waypoint';
    const time = extractTag(content, 'time');
    const type = extractTag(content, 'type');
    globalWaypoints.push({
      id: `wp-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
      lat,
      lon,
      timestamp: time ? new Date(time).getTime() : Date.now(),
      label: name,
      type: type === 'catch' ? 'catch' : 'waypoint',
      species: type === 'catch' ? (extractTag(content, 'desc')?.replace('Species: ', '')) : undefined,
    });
  }

  // ── Parse <trk> blocks ──
  const trkRegex = /<trk>([\s\S]*?)<\/trk>/g;
  let trkMatch;
  while ((trkMatch = trkRegex.exec(gpxString)) !== null) {
    const trkContent = trkMatch[1];
    const trackName = extractTag(trkContent, 'name') ?? 'Imported Track';
    const trackDesc = extractTag(trkContent, 'desc');

    // Parse all <trkseg> within this <trk>
    const points: TrackPoint[] = [];
    const segRegex = /<trkseg>([\s\S]*?)<\/trkseg>/g;
    let segMatch;
    while ((segMatch = segRegex.exec(trkContent)) !== null) {
      const segPoints = parseTrackPoints(segMatch[1]);
      points.push(...segPoints);
    }

    if (points.length === 0) continue;

    const dist = totalDistance(points);
    const duration = points.length >= 2
      ? Math.round((points[points.length - 1].timestamp - points[0].timestamp) / 60000)
      : 0;

    let maxSpeedMph = 0;
    for (const p of points) {
      if (p.speed !== undefined) {
        const mph = p.speed * 2.237;
        if (mph > maxSpeedMph) maxSpeedMph = mph;
      }
    }

    tracks.push({
      id: `imported-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
      name: trackName,
      notes: trackDesc ?? undefined,
      startTime: points[0].timestamp,
      endTime: points[points.length - 1].timestamp,
      points,
      waypoints: [],
      distanceMiles: dist,
      durationMinutes: duration,
      maxSpeedMph: Math.round(maxSpeedMph * 10) / 10,
      avgSpeedMph: duration > 0 ? Math.round((dist / (duration / 60)) * 10) / 10 : 0,
    });
  }

  // ── Parse <rte> blocks as tracks ──
  const rteRegex = /<rte>([\s\S]*?)<\/rte>/g;
  let rteMatch;
  while ((rteMatch = rteRegex.exec(gpxString)) !== null) {
    const rteContent = rteMatch[1];
    const routeName = extractTag(rteContent, 'name') ?? 'Imported Route';
    const rteptRegex = /<rtept\s+lat="([^"]+)"\s+lon="([^"]+)"[^>]*>([\s\S]*?)<\/rtept>/g;
    const points: TrackPoint[] = [];
    let rteptMatch;
    while ((rteptMatch = rteptRegex.exec(rteContent)) !== null) {
      const lat = parseFloat(rteptMatch[1]);
      const lon = parseFloat(rteptMatch[2]);
      const content = rteptMatch[3];
      const time = extractTag(content, 'time');
      const ele = extractTag(content, 'ele');
      points.push({
        lat,
        lon,
        altitude: ele ? parseFloat(ele) : undefined,
        timestamp: time ? new Date(time).getTime() : Date.now(),
      });
    }
    if (points.length === 0) continue;
    const dist = totalDistance(points);
    tracks.push({
      id: `imported-rte-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
      name: routeName,
      startTime: points[0].timestamp,
      endTime: points[points.length - 1].timestamp,
      points,
      waypoints: [],
      distanceMiles: dist,
      durationMinutes: 0,
      maxSpeedMph: 0,
      avgSpeedMph: 0,
    });
  }

  // If no tracks found but we have waypoints, create a synthetic track
  if (tracks.length === 0 && globalWaypoints.length > 0) {
    const docName = extractTag(gpxString, 'name') ?? 'Imported Waypoints';
    tracks.push({
      id: `imported-wp-${Date.now()}`,
      name: docName,
      startTime: globalWaypoints[0].timestamp,
      endTime: globalWaypoints[globalWaypoints.length - 1].timestamp,
      points: globalWaypoints.map((wp) => ({
        lat: wp.lat,
        lon: wp.lon,
        timestamp: wp.timestamp,
      })),
      waypoints: globalWaypoints,
      distanceMiles: 0,
      durationMinutes: 0,
      maxSpeedMph: 0,
      avgSpeedMph: 0,
    });
  } else if (tracks.length > 0 && globalWaypoints.length > 0) {
    // Attach global waypoints to the first track
    tracks[0].waypoints = [...(tracks[0].waypoints ?? []), ...globalWaypoints];
  }

  return tracks;
}

function parseTrackPoints(segContent: string): TrackPoint[] {
  const points: TrackPoint[] = [];
  const trkptRegex = /<trkpt\s+lat="([^"]+)"\s+lon="([^"]+)"[^>]*>([\s\S]*?)<\/trkpt>/g;
  let match;
  while ((match = trkptRegex.exec(segContent)) !== null) {
    const lat = parseFloat(match[1]);
    const lon = parseFloat(match[2]);
    const content = match[3];
    const time = extractTag(content, 'time');
    const ele = extractTag(content, 'ele');
    const speed = extractTag(content, 'speed');
    const course = extractTag(content, 'course');
    points.push({
      lat,
      lon,
      altitude: ele ? parseFloat(ele) : undefined,
      speed: speed ? parseFloat(speed) : undefined,
      heading: course ? parseFloat(course) : undefined,
      timestamp: time ? new Date(time).getTime() : Date.now(),
    });
  }
  return points;
}

// ── KML Import ───────────────────────────────────────────────────────────────

/**
 * Parse a KML string into FishingTrack[] objects.
 * Handles LineString tracks and Point placemarks (waypoints).
 */
export function importKML(kmlString: string): FishingTrack[] {
  const tracks: FishingTrack[] = [];
  const waypoints: TrackWaypoint[] = [];

  // Parse each <Placemark>
  const placemarkRegex = /<Placemark>([\s\S]*?)<\/Placemark>/g;
  let pmMatch;
  while ((pmMatch = placemarkRegex.exec(kmlString)) !== null) {
    const content = pmMatch[1];
    const name = extractTag(content, 'name') ?? 'Imported';
    const description = extractTag(content, 'description');

    // Check for LineString (track)
    const lineMatch = /<LineString>([\s\S]*?)<\/LineString>/.exec(content);
    if (lineMatch) {
      const coordsMatch = /<coordinates>([\s\S]*?)<\/coordinates>/.exec(lineMatch[1]);
      if (coordsMatch) {
        const points = parseKMLCoordinates(coordsMatch[1]);
        if (points.length > 0) {
          const dist = totalDistance(points);
          const duration = points.length >= 2
            ? Math.round((points[points.length - 1].timestamp - points[0].timestamp) / 60000)
            : 0;
          tracks.push({
            id: `imported-kml-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
            name,
            notes: description ?? undefined,
            startTime: points[0].timestamp,
            endTime: points[points.length - 1].timestamp,
            points,
            waypoints: [],
            distanceMiles: dist,
            durationMinutes: duration,
            maxSpeedMph: 0,
            avgSpeedMph: duration > 0 ? Math.round((dist / (duration / 60)) * 10) / 10 : 0,
          });
        }
      }
      continue;
    }

    // Check for Point (waypoint)
    const pointMatch = /<Point>([\s\S]*?)<\/Point>/.exec(content);
    if (pointMatch) {
      const coordsMatch = /<coordinates>([\s\S]*?)<\/coordinates>/.exec(pointMatch[1]);
      if (coordsMatch) {
        const coords = coordsMatch[1].trim().split(',');
        if (coords.length >= 2) {
          waypoints.push({
            id: `wp-kml-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
            lat: parseFloat(coords[1]),
            lon: parseFloat(coords[0]),
            timestamp: Date.now(),
            label: name,
            type: 'waypoint',
          });
        }
      }
    }
  }

  // If no tracks but have waypoints, create a synthetic track
  if (tracks.length === 0 && waypoints.length > 0) {
    const docName = extractTag(kmlString, 'name') ?? 'Imported KML';
    tracks.push({
      id: `imported-kml-wp-${Date.now()}`,
      name: docName,
      startTime: waypoints[0].timestamp,
      endTime: waypoints[waypoints.length - 1].timestamp,
      points: waypoints.map((wp) => ({
        lat: wp.lat,
        lon: wp.lon,
        timestamp: wp.timestamp,
      })),
      waypoints,
      distanceMiles: 0,
      durationMinutes: 0,
      maxSpeedMph: 0,
      avgSpeedMph: 0,
    });
  } else if (tracks.length > 0 && waypoints.length > 0) {
    tracks[0].waypoints = [...(tracks[0].waypoints ?? []), ...waypoints];
  }

  return tracks;
}

function parseKMLCoordinates(coordStr: string): TrackPoint[] {
  const now = Date.now();
  return coordStr
    .trim()
    .split(/\s+/)
    .filter((s) => s.includes(','))
    .map((coord, idx) => {
      const parts = coord.split(',');
      return {
        lat: parseFloat(parts[1]),
        lon: parseFloat(parts[0]),
        altitude: parts[2] ? parseFloat(parts[2]) : undefined,
        timestamp: now + idx * 1000, // Synthetic timestamps 1s apart
      };
    })
    .filter((p) => !isNaN(p.lat) && !isNaN(p.lon));
}

// ── Preview ──────────────────────────────────────────────────────────────────

/**
 * Get a quick preview of a GPX or KML file without full import.
 */
export function getImportPreview(fileContent: string, isKml: boolean): ImportPreview {
  const tracks = isKml ? importKML(fileContent) : importGPX(fileContent);
  return {
    trackCount: tracks.length,
    totalPoints: tracks.reduce((sum, t) => sum + t.points.length, 0),
    totalWaypoints: tracks.reduce((sum, t) => sum + (t.waypoints?.length ?? 0), 0),
    trackNames: tracks.map((t) => t.name),
    totalDistanceMiles: tracks.reduce((sum, t) => sum + t.distanceMiles, 0),
    estimatedDurationMinutes: tracks.reduce((sum, t) => sum + t.durationMinutes, 0),
  };
}

// ── Share via native sheet ───────────────────────────────────────────────────

/**
 * Export tracks as GPX and open the native share sheet.
 */
export async function shareGPXFile(tracks: FishingTrack[]): Promise<void> {
  const gpx = exportTracksAsGPX(tracks);
  const fileName = tracks.length === 1
    ? `${tracks[0].name.replace(/[^a-zA-Z0-9 ]/g, '')}.gpx`
    : `OpenCatch_${tracks.length}_tracks.gpx`;

  if (Platform.OS === 'web') {
    await Share.share({ message: gpx, title: fileName });
    return;
  }

  // Write to temp file for proper file sharing
  const filePath = `${(FileSystem?.cacheDirectory ?? '')}${fileName}`;
  await FileSystem?.writeAsStringAsync(filePath, gpx, {
    encoding: 'utf8',
  });

  if (await Sharing?.isAvailableAsync()) {
    await Sharing?.shareAsync(filePath, {
      mimeType: 'application/gpx+xml',
      dialogTitle: `Share ${fileName}`,
    });
  } else {
    await Share.share({ message: gpx, title: fileName });
  }
}

/**
 * Export tracks as KML and open the native share sheet.
 */
export async function shareKMLFile(tracks: FishingTrack[]): Promise<void> {
  const kml = exportTracksAsKML(tracks);
  const fileName = tracks.length === 1
    ? `${tracks[0].name.replace(/[^a-zA-Z0-9 ]/g, '')}.kml`
    : `OpenCatch_${tracks.length}_tracks.kml`;

  if (Platform.OS === 'web') {
    await Share.share({ message: kml, title: fileName });
    return;
  }

  const filePath = `${(FileSystem?.cacheDirectory ?? '')}${fileName}`;
  await FileSystem?.writeAsStringAsync(filePath, kml, {
    encoding: 'utf8',
  });

  if (await Sharing?.isAvailableAsync()) {
    await Sharing?.shareAsync(filePath, {
      mimeType: 'application/vnd.google-earth.kml+xml',
      dialogTitle: `Share ${fileName}`,
    });
  } else {
    await Share.share({ message: kml, title: fileName });
  }
}

// ── Utilities ────────────────────────────────────────────────────────────────

function extractTag(xml: string, tagName: string): string | null {
  const regex = new RegExp(`<${tagName}[^>]*>([^<]+)</${tagName}>`);
  const match = regex.exec(xml);
  return match ? match[1].trim() : null;
}
