/**
 * AIS WiFi Receiver Service for OpenCatch.
 *
 * Connects to marine AIS receivers (Digital Yacht, Vesper, etc.) over
 * local WiFi/NMEA TCP. Parses !AIVDM/!AIVDO sentences into vessel
 * positions and static data for real-time map display.
 *
 * Since Expo does not support raw TCP sockets natively, this module
 * provides a WebSocket bridge interface and full NMEA/AIS parsing
 * logic. On native builds with `react-native-tcp-socket`, the TCP
 * transport can be swapped in.
 */

import AsyncStorage from '@react-native-async-storage/async-storage';

let TcpSocket: any = null;
try {
  TcpSocket = require('react-native-tcp-socket');
} catch {
  // TCP transport is optional; WebSocket fallback remains available.
}

// ── Types ────────────────────────────────────────────────────────

/** Connection status for the AIS receiver. */
export type AISConnectionStatus = 'disconnected' | 'connecting' | 'connected' | 'error';

/** A parsed AIS vessel update from NMEA data. */
export interface AISVesselUpdate {
  /** Maritime Mobile Service Identity (9-digit). */
  mmsi: string;
  /** Latitude in decimal degrees, or null if unavailable. */
  lat: number | null;
  /** Longitude in decimal degrees, or null if unavailable. */
  lon: number | null;
  /** Speed over ground in knots, or null. */
  sogKnots: number | null;
  /** Course over ground in degrees (0-360), or null. */
  cogDeg: number | null;
  /** True heading in degrees, or null. */
  headingDeg: number | null;
  /** Navigation status code. */
  navStatus: number;
  /** AIS message type that produced this update. */
  messageType: number;
  /** Ship name (from Type 5 / Type 19). */
  shipName: string | null;
  /** Ship type code (from Type 5). */
  shipType: number | null;
  /** Destination (from Type 5). */
  destination: string | null;
  /** Length in meters (from Type 5). */
  lengthM: number | null;
  /** Beam in meters (from Type 5). */
  beamM: number | null;
  /** Timestamp of when this update was received. */
  receivedAt: number;
}

/** Raw NMEA sentence. */
export interface NMEASentence {
  raw: string;
  talker: string;
  type: string;
  fields: string[];
  checksum: string;
}

export interface InstrumentPosition {
  lat: number;
  lon: number;
  sogKnots: number | null;
  cogDeg: number | null;
  fixQuality: number | null;
  receivedAt: number;
  source: 'nmea0183';
}

export interface InstrumentHeading {
  headingDeg: number;
  reference: 'true' | 'magnetic';
  receivedAt: number;
}

export interface InstrumentDepth {
  depthM: number;
  offsetM: number | null;
  sourceSentence: 'DBT' | 'DPT';
  receivedAt: number;
}

export interface InstrumentWind {
  angleDeg: number;
  speedKnots: number;
  reference: 'true' | 'relative';
  receivedAt: number;
}

export interface MarineInstrumentData {
  position: InstrumentPosition | null;
  heading: InstrumentHeading | null;
  depth: InstrumentDepth | null;
  wind: InstrumentWind | null;
  waterSpeedKnots: number | null;
  lastUpdatedAt: number | null;
  sourceTypes: string[];
}

/** Current state of the AIS receiver. */
export interface AISReceiverState {
  status: AISConnectionStatus;
  host: string | null;
  port: number | null;
  vessels: Map<string, AISVesselUpdate>;
  vesselCount: number;
  lastMessageAt: number | null;
  error: string | null;
  instruments: MarineInstrumentData;
}

// ── Constants ────────────────────────────────────────────────────

const STORAGE_KEY = '@opencatch_ais_settings';
const VESSEL_EXPIRY_MS = 5 * 60 * 1000; // 5 minutes
const COMMON_PORTS = [10110, 2000, 39150];
const KMH_TO_KNOTS = 0.539957;
const MS_TO_KNOTS = 1.94384;
const FATHOMS_TO_METERS = 1.8288;

/** 6-bit ASCII character table for AIS payload decoding. */
const AIS_CHARSET = '@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_ !"#$%&\'()*+,-./0123456789:;<=>?';

// ── AIS Payload Decoder ──────────────────────────────────────────

/** Decode armored 6-bit ASCII payload to a bit array. */
function decodeToBits(payload: string): number[] {
  const bits: number[] = [];
  for (let i = 0; i < payload.length; i++) {
    let val = payload.charCodeAt(i) - 48;
    if (val > 40) val -= 8;
    for (let b = 5; b >= 0; b--) {
      bits.push((val >> b) & 1);
    }
  }
  return bits;
}

/** Extract an unsigned integer from a bit array. */
function getUint(bits: number[], start: number, len: number): number {
  let val = 0;
  for (let i = start; i < start + len; i++) {
    val = (val << 1) | (bits[i] ?? 0);
  }
  return val;
}

/** Extract a signed integer from a bit array (two's complement). */
function getInt(bits: number[], start: number, len: number): number {
  let val = getUint(bits, start, len);
  if (val >= (1 << (len - 1))) {
    val -= 1 << len;
  }
  return val;
}

/** Extract a 6-bit ASCII string from a bit array. */
function getString(bits: number[], start: number, len: number): string {
  const chars: string[] = [];
  for (let i = start; i < start + len; i += 6) {
    const code = getUint(bits, i, 6);
    chars.push(AIS_CHARSET[code] ?? ' ');
  }
  return chars.join('').replace(/@+$/, '').trim();
}

/** Convert raw AIS longitude (1/10000 min) to decimal degrees. */
function decodeLon(raw: number): number | null {
  if (raw === 0x6791AC0) return null; // 181 degrees = not available
  return raw / 600000;
}

/** Convert raw AIS latitude (1/10000 min) to decimal degrees. */
function decodeLat(raw: number): number | null {
  if (raw === 0x3412140) return null; // 91 degrees = not available
  return raw / 600000;
}

function parseFloatSafe(value: string | undefined): number | null {
  if (!value) return null;
  const parsed = parseFloat(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function normalizeDegrees(value: number): number {
  return ((value % 360) + 360) % 360;
}

function parseNMEACoordinate(value: string | undefined, hemisphere: string | undefined): number | null {
  if (!value || !hemisphere) return null;
  const hemi = hemisphere.toUpperCase();
  const degreeDigits = hemi === 'N' || hemi === 'S' ? 2 : 3;
  if (value.length <= degreeDigits) return null;

  const degrees = parseInt(value.slice(0, degreeDigits), 10);
  const minutes = parseFloat(value.slice(degreeDigits));
  if (!Number.isFinite(degrees) || !Number.isFinite(minutes)) return null;

  const decimal = degrees + minutes / 60;
  return hemi === 'S' || hemi === 'W' ? -decimal : decimal;
}

function convertWindToKnots(speed: number, units: string | undefined): number | null {
  const normalizedUnits = units?.toUpperCase();
  if (!Number.isFinite(speed) || speed < 0) return null;
  switch (normalizedUnits) {
    case 'N':
      return speed;
    case 'K':
      return speed * KMH_TO_KNOTS;
    case 'M':
      return speed * MS_TO_KNOTS;
    default:
      return null;
  }
}

// ── NMEA Parser ──────────────────────────────────────────────────

/** Parse a raw NMEA sentence string. */
export function parseNMEA(sentence: string): NMEASentence | null {
  const trimmed = sentence.trim();
  if (!trimmed.startsWith('!') && !trimmed.startsWith('$')) return null;

  // Split off checksum
  const starIdx = trimmed.indexOf('*');
  const body = starIdx > 0 ? trimmed.slice(1, starIdx) : trimmed.slice(1);
  const checksum = starIdx > 0 ? trimmed.slice(starIdx + 1) : '';

  const fields = body.split(',');
  if (fields.length < 2) return null;

  const header = fields[0];
  const talker = header.slice(0, 2);
  const type = header.slice(2);

  return { raw: trimmed, talker, type, fields, checksum };
}

/** Verify NMEA checksum. */
function verifyChecksum(sentence: string): boolean {
  const start = sentence.indexOf('!') >= 0 ? sentence.indexOf('!') : sentence.indexOf('$');
  const end = sentence.indexOf('*');
  if (start < 0 || end < 0) return false;

  let calc = 0;
  for (let i = start + 1; i < end; i++) {
    calc ^= sentence.charCodeAt(i);
  }

  const expected = sentence.slice(end + 1, end + 3).toUpperCase();
  const actual = calc.toString(16).toUpperCase().padStart(2, '0');
  return expected === actual;
}

// ── AIS Message Parsers ──────────────────────────────────────────

/** Parse Type 1, 2, 3: Position Report (Class A). */
function parseType123(bits: number[]): Partial<AISVesselUpdate> {
  return {
    mmsi: String(getUint(bits, 8, 30)).padStart(9, '0'),
    navStatus: getUint(bits, 38, 4),
    sogKnots: getUint(bits, 50, 10) / 10,
    lon: decodeLon(getInt(bits, 61, 28)),
    lat: decodeLat(getInt(bits, 89, 27)),
    cogDeg: getUint(bits, 116, 12) / 10,
    headingDeg: getUint(bits, 128, 9) === 511 ? null : getUint(bits, 128, 9),
  };
}

/** Parse Type 5: Static and Voyage Related Data. */
function parseType5(bits: number[]): Partial<AISVesselUpdate> {
  const mmsi = String(getUint(bits, 8, 30)).padStart(9, '0');
  const shipType = getUint(bits, 232, 8);
  const toBow = getUint(bits, 240, 9);
  const toStern = getUint(bits, 249, 9);
  const toPort = getUint(bits, 258, 6);
  const toStarboard = getUint(bits, 264, 6);
  const shipName = getString(bits, 112, 120);
  const destination = getString(bits, 302, 120);

  return {
    mmsi,
    shipType,
    shipName: shipName || null,
    destination: destination || null,
    lengthM: toBow + toStern > 0 ? toBow + toStern : null,
    beamM: toPort + toStarboard > 0 ? toPort + toStarboard : null,
  };
}

/** Parse Type 18: Standard Class B Position Report. */
function parseType18(bits: number[]): Partial<AISVesselUpdate> {
  return {
    mmsi: String(getUint(bits, 8, 30)).padStart(9, '0'),
    sogKnots: getUint(bits, 46, 10) / 10,
    lon: decodeLon(getInt(bits, 57, 28)),
    lat: decodeLat(getInt(bits, 85, 27)),
    cogDeg: getUint(bits, 112, 12) / 10,
    headingDeg: getUint(bits, 124, 9) === 511 ? null : getUint(bits, 124, 9),
    navStatus: 15, // Not available for Class B
  };
}

/** Parse Type 19: Extended Class B Position Report. */
function parseType19(bits: number[]): Partial<AISVesselUpdate> {
  const base = parseType18(bits);
  const shipName = getString(bits, 143, 120);
  const shipType = getUint(bits, 263, 8);

  return {
    ...base,
    shipName: shipName || null,
    shipType,
  };
}

/** Parse an AIS payload into a vessel update. */
function parseAISPayload(payload: string, fillBits: number): Partial<AISVesselUpdate> | null {
  const bits = decodeToBits(payload);
  if (bits.length < 38) return null;

  const messageType = getUint(bits, 0, 6);

  switch (messageType) {
    case 1:
    case 2:
    case 3:
      return { ...parseType123(bits), messageType };
    case 5:
      return { ...parseType5(bits), messageType };
    case 18:
      return { ...parseType18(bits), messageType };
    case 19:
      return { ...parseType19(bits), messageType };
    default:
      return null; // Unsupported message type
  }
}

// ── Multi-part message accumulator ───────────────────────────────

interface MultiPartMessage {
  total: number;
  parts: Map<number, string>;
  seqId: string;
  receivedAt: number;
}

// ── AIS WiFi Receiver Class ──────────────────────────────────────

type AISListener = (vessel: AISVesselUpdate) => void;
type InstrumentListener = (data: MarineInstrumentData) => void;

class AISWifiReceiver {
  private status: AISConnectionStatus = 'disconnected';
  private host: string | null = null;
  private port: number | null = null;
  private error: string | null = null;
  private vessels = new Map<string, AISVesselUpdate>();
  private listeners = new Set<AISListener>();
  private instrumentListeners = new Set<InstrumentListener>();
  private lastMessageAt: number | null = null;
  private ws: WebSocket | null = null;
  private tcpSocket: any = null;
  private multiParts = new Map<string, MultiPartMessage>();
  private cleanupTimer: ReturnType<typeof setInterval> | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private buffer = '';
  private instrumentSourceTypes = new Set<string>();
  private instrumentData: MarineInstrumentData = {
    position: null,
    heading: null,
    depth: null,
    wind: null,
    waterSpeedKnots: null,
    lastUpdatedAt: null,
    sourceTypes: [],
  };

  constructor() {
    // Start periodic cleanup of expired vessels
    this.cleanupTimer = setInterval(() => this.cleanupExpired(), 60_000);
  }

  // ── Public API ───────────────────────────────────────────────────

  /**
   * Connect to an AIS receiver at the given host and port.
   * Uses WebSocket as transport (ws://host:port). For native TCP
   * builds, swap in react-native-tcp-socket here.
   */
  async connectToAIS(host: string, port: number = 10110): Promise<boolean> {
    if (this.status === 'connected' || this.status === 'connecting') {
      this.disconnectAIS();
    }

    this.host = host;
    this.port = port;
    this.status = 'connecting';
    this.error = null;

    const tcpSupported = !!TcpSocket?.createConnection;
    if (tcpSupported) {
      const tcpConnected = await this.connectViaTcp(host, port);
      if (tcpConnected) return true;
      this.status = 'connecting';
      this.error = null;
    }

    return this.connectViaWebSocket(host, port);
  }

  /** Disconnect from the AIS receiver. */
  disconnectAIS(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      this.ws.onopen = null;
      this.ws.onmessage = null;
      this.ws.onerror = null;
      this.ws.onclose = null;
      this.ws.close();
      this.ws = null;
    }
    if (this.tcpSocket) {
      try {
        this.tcpSocket.removeAllListeners?.();
        this.tcpSocket.destroy?.();
        this.tcpSocket.end?.();
      } catch {
        // Ignore transport cleanup errors.
      }
      this.tcpSocket = null;
    }
    this.status = 'disconnected';
    this.error = null;
    this.buffer = '';
    this.instrumentSourceTypes.clear();
    this.instrumentData = {
      position: null,
      heading: null,
      depth: null,
      wind: null,
      waterSpeedKnots: null,
      lastUpdatedAt: null,
      sourceTypes: [],
    };
    this.emitInstrumentUpdate();
  }

  /** Get current connection status. */
  getAISStatus(): AISReceiverState {
    return {
      status: this.status,
      host: this.host,
      port: this.port,
      vessels: this.vessels,
      vesselCount: this.vessels.size,
      lastMessageAt: this.lastMessageAt,
      error: this.error,
      instruments: this.getInstrumentData(),
    };
  }

  /** Register a listener for real-time vessel updates. */
  addAISListener(callback: AISListener): () => void {
    this.listeners.add(callback);
    return () => {
      this.listeners.delete(callback);
    };
  }

  addInstrumentListener(callback: InstrumentListener): () => void {
    this.instrumentListeners.add(callback);
    return () => {
      this.instrumentListeners.delete(callback);
    };
  }

  /** Get all currently tracked vessels (not expired). */
  getVessels(): AISVesselUpdate[] {
    this.cleanupExpired();
    return Array.from(this.vessels.values());
  }

  getInstrumentData(): MarineInstrumentData {
    return {
      position: this.instrumentData.position ? { ...this.instrumentData.position } : null,
      heading: this.instrumentData.heading ? { ...this.instrumentData.heading } : null,
      depth: this.instrumentData.depth ? { ...this.instrumentData.depth } : null,
      wind: this.instrumentData.wind ? { ...this.instrumentData.wind } : null,
      waterSpeedKnots: this.instrumentData.waterSpeedKnots,
      lastUpdatedAt: this.instrumentData.lastUpdatedAt,
      sourceTypes: [...this.instrumentData.sourceTypes],
    };
  }

  /** Get a GeoJSON FeatureCollection of tracked vessels. */
  getVesselsGeoJSON(): GeoJSON.FeatureCollection {
    const vessels = this.getVessels().filter((v) => v.lat != null && v.lon != null);
    return {
      type: 'FeatureCollection',
      features: vessels.map((v) => ({
        type: 'Feature' as const,
        geometry: {
          type: 'Point' as const,
          coordinates: [v.lon!, v.lat!],
        },
        properties: {
          mmsi: v.mmsi,
          name: v.shipName ?? `MMSI ${v.mmsi}`,
          speed: v.sogKnots,
          heading: v.headingDeg ?? v.cogDeg ?? 0,
          course: v.cogDeg,
          shipType: v.shipType,
          source: 'ais-wifi',
        },
      })),
    };
  }

  /**
   * Attempt to scan common AIS receiver ports on the given host.
   * Returns the first port that responds, or null.
   */
  async scanForAIS(host: string): Promise<number | null> {
    for (const port of COMMON_PORTS) {
      try {
        const connected = await this.connectToAIS(host, port);
        if (connected) {
          return port;
        }
        this.disconnectAIS();
      } catch {
        // Try next port
      }
    }
    return null;
  }

  /** Load saved AIS settings from AsyncStorage. */
  async loadSettings(): Promise<{ host: string; port: number; autoConnect: boolean } | null> {
    try {
      const raw = await AsyncStorage.getItem(STORAGE_KEY);
      if (!raw) return null;
      return JSON.parse(raw);
    } catch {
      return null;
    }
  }

  /** Inject a raw NMEA sentence for testing / mock data. */
  injectSentence(sentence: string): void {
    this.processSentence(sentence);
  }

  /** Destroy the receiver (cleanup timers). */
  destroy(): void {
    this.disconnectAIS();
    if (this.cleanupTimer) {
      clearInterval(this.cleanupTimer);
      this.cleanupTimer = null;
    }
  }

  // ── Private ────────────────────────────────────────────────────

  private async saveSettings(): Promise<void> {
    if (!this.host || !this.port) return;
    try {
      await AsyncStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({ host: this.host, port: this.port, autoConnect: true }),
      );
    } catch {
      // Non-critical
    }
  }

  private connectViaTcp(host: string, port: number): Promise<boolean> {
    if (!TcpSocket?.createConnection) {
      return Promise.resolve(false);
    }

    return new Promise<boolean>((resolve) => {
      let settled = false;
      const timeout = setTimeout(() => {
        if (settled) return;
        settled = true;
        this.status = 'error';
        this.error = 'TCP connection timed out';
        try {
          this.tcpSocket?.destroy?.();
        } catch {
          // ignore
        }
        this.tcpSocket = null;
        resolve(false);
      }, 10_000);

      try {
        this.tcpSocket = TcpSocket.createConnection({ host, port }, () => {
          if (settled) return;
          settled = true;
          clearTimeout(timeout);
          this.status = 'connected';
          this.error = null;
          this.saveSettings();
          resolve(true);
        });

        this.tcpSocket.on('data', (data: any) => {
          const payload = typeof data === 'string'
            ? data
            : typeof data?.toString === 'function'
              ? data.toString('utf8')
              : '';
          this.handleData(payload);
        });

        this.tcpSocket.on('error', (err: any) => {
          clearTimeout(timeout);
          this.error = err instanceof Error ? err.message : 'TCP connection failed';
          this.status = 'error';
          try {
            this.tcpSocket?.destroy?.();
          } catch {
            // ignore
          }
          this.tcpSocket = null;
          if (!settled) {
            settled = true;
            resolve(false);
          }
        });

        this.tcpSocket.on('close', () => {
          if (this.status === 'connected') {
            this.status = 'disconnected';
          }
        });
      } catch (err) {
        clearTimeout(timeout);
        this.status = 'error';
        this.error = err instanceof Error ? err.message : 'TCP connection failed';
        if (!settled) {
          settled = true;
          resolve(false);
        }
      }
    });
  }

  private connectViaWebSocket(host: string, port: number): Promise<boolean> {
    try {
      const wsUrl = `ws://${host}:${port}`;
      this.ws = new WebSocket(wsUrl);

      return new Promise<boolean>((resolve) => {
        let settled = false;
        const timeout = setTimeout(() => {
          if (settled) return;
          settled = true;
          this.status = 'error';
          this.error = 'Connection timed out';
          this.ws?.close();
          resolve(false);
        }, 10_000);

        this.ws!.onopen = () => {
          if (settled) return;
          settled = true;
          clearTimeout(timeout);
          this.status = 'connected';
          this.error = null;
          this.saveSettings();
          resolve(true);
        };

        this.ws!.onmessage = (event) => {
          this.handleData(typeof event.data === 'string' ? event.data : '');
        };

        this.ws!.onerror = () => {
          clearTimeout(timeout);
          this.status = 'error';
          this.error = 'Connection failed';
          if (!settled) {
            settled = true;
            resolve(false);
          }
        };

        this.ws!.onclose = () => {
          if (this.status === 'connected') {
            this.status = 'disconnected';
          }
        };
      });
    } catch (err) {
      this.status = 'error';
      this.error = err instanceof Error ? err.message : 'Unknown error';
      return Promise.resolve(false);
    }
  }

  private emitInstrumentUpdate(): void {
    const snapshot = this.getInstrumentData();
    for (const listener of this.instrumentListeners) {
      try {
        listener(snapshot);
      } catch {
        // Listener issues should never break the stream parser.
      }
    }
  }

  private updateInstrumentData(
    patch: Partial<MarineInstrumentData>,
    sentenceType: string,
    receivedAt: number,
  ): void {
    let changed = false;

    if (patch.position) {
      const previous = this.instrumentData.position;
      const next: InstrumentPosition = {
        lat: patch.position.lat ?? previous?.lat ?? 0,
        lon: patch.position.lon ?? previous?.lon ?? 0,
        sogKnots: patch.position.sogKnots ?? previous?.sogKnots ?? null,
        cogDeg: patch.position.cogDeg ?? previous?.cogDeg ?? null,
        fixQuality: patch.position.fixQuality ?? previous?.fixQuality ?? null,
        receivedAt,
        source: 'nmea0183',
      };
      if (
        !previous ||
        previous.lat !== next.lat ||
        previous.lon !== next.lon ||
        previous.sogKnots !== next.sogKnots ||
        previous.cogDeg !== next.cogDeg ||
        previous.fixQuality !== next.fixQuality
      ) {
        this.instrumentData.position = next;
        changed = true;
      }
    }

    if (patch.heading) {
      const previous = this.instrumentData.heading;
      const next: InstrumentHeading = {
        headingDeg: normalizeDegrees(patch.heading.headingDeg),
        reference: patch.heading.reference,
        receivedAt,
      };
      if (
        !previous ||
        previous.headingDeg !== next.headingDeg ||
        previous.reference !== next.reference
      ) {
        this.instrumentData.heading = next;
        changed = true;
      }
    }

    if (patch.depth) {
      const previous = this.instrumentData.depth;
      const next: InstrumentDepth = {
        depthM: patch.depth.depthM,
        offsetM: patch.depth.offsetM ?? null,
        sourceSentence: patch.depth.sourceSentence,
        receivedAt,
      };
      if (
        !previous ||
        previous.depthM !== next.depthM ||
        previous.offsetM !== next.offsetM ||
        previous.sourceSentence !== next.sourceSentence
      ) {
        this.instrumentData.depth = next;
        changed = true;
      }
    }

    if (patch.wind) {
      const previous = this.instrumentData.wind;
      const next: InstrumentWind = {
        angleDeg: normalizeDegrees(patch.wind.angleDeg),
        speedKnots: patch.wind.speedKnots,
        reference: patch.wind.reference,
        receivedAt,
      };
      if (
        !previous ||
        previous.angleDeg !== next.angleDeg ||
        previous.speedKnots !== next.speedKnots ||
        previous.reference !== next.reference
      ) {
        this.instrumentData.wind = next;
        changed = true;
      }
    }

    if (patch.waterSpeedKnots != null && patch.waterSpeedKnots !== this.instrumentData.waterSpeedKnots) {
      this.instrumentData.waterSpeedKnots = patch.waterSpeedKnots;
      changed = true;
    }

    if (!changed) return;

    this.instrumentSourceTypes.add(sentenceType);
    this.instrumentData.lastUpdatedAt = receivedAt;
    this.instrumentData.sourceTypes = Array.from(this.instrumentSourceTypes).slice(-8);
    this.emitInstrumentUpdate();
  }

  private processInstrumentSentence(nmea: NMEASentence, receivedAt: number): void {
    switch (nmea.type) {
      case 'RMC': {
        if (nmea.fields[2] !== 'A') return;
        const lat = parseNMEACoordinate(nmea.fields[3], nmea.fields[4]);
        const lon = parseNMEACoordinate(nmea.fields[5], nmea.fields[6]);
        if (lat == null || lon == null) return;
        this.updateInstrumentData({
          position: {
            lat,
            lon,
            sogKnots: parseFloatSafe(nmea.fields[7]),
            cogDeg: parseFloatSafe(nmea.fields[8]),
            fixQuality: 1,
            receivedAt,
            source: 'nmea0183',
          },
        }, 'RMC', receivedAt);
        return;
      }
      case 'GGA': {
        const lat = parseNMEACoordinate(nmea.fields[2], nmea.fields[3]);
        const lon = parseNMEACoordinate(nmea.fields[4], nmea.fields[5]);
        const fixQuality = parseFloatSafe(nmea.fields[6]);
        if (lat == null || lon == null || !fixQuality || fixQuality <= 0) return;
        this.updateInstrumentData({
          position: {
            lat,
            lon,
            sogKnots: this.instrumentData.position?.sogKnots ?? null,
            cogDeg: this.instrumentData.position?.cogDeg ?? null,
            fixQuality,
            receivedAt,
            source: 'nmea0183',
          },
        }, 'GGA', receivedAt);
        return;
      }
      case 'GLL': {
        if (nmea.fields[6] && nmea.fields[6] !== 'A') return;
        const lat = parseNMEACoordinate(nmea.fields[1], nmea.fields[2]);
        const lon = parseNMEACoordinate(nmea.fields[3], nmea.fields[4]);
        if (lat == null || lon == null) return;
        this.updateInstrumentData({
          position: {
            lat,
            lon,
            sogKnots: this.instrumentData.position?.sogKnots ?? null,
            cogDeg: this.instrumentData.position?.cogDeg ?? null,
            fixQuality: this.instrumentData.position?.fixQuality ?? null,
            receivedAt,
            source: 'nmea0183',
          },
        }, 'GLL', receivedAt);
        return;
      }
      case 'VTG': {
        const cogDeg = parseFloatSafe(nmea.fields[1]);
        const sogKnots = parseFloatSafe(nmea.fields[5]);
        const position = this.instrumentData.position;
        if (position && (cogDeg != null || sogKnots != null)) {
          this.updateInstrumentData({
            position: {
              lat: position.lat,
              lon: position.lon,
              sogKnots: sogKnots ?? position.sogKnots,
              cogDeg: cogDeg ?? position.cogDeg,
              fixQuality: position.fixQuality,
              receivedAt,
              source: 'nmea0183',
            },
          }, 'VTG', receivedAt);
        }
        return;
      }
      case 'HDT': {
        const heading = parseFloatSafe(nmea.fields[1]);
        if (heading == null) return;
        this.updateInstrumentData({
          heading: {
            headingDeg: heading,
            reference: 'true',
            receivedAt,
          },
        }, 'HDT', receivedAt);
        return;
      }
      case 'HDG':
      case 'HDM': {
        const heading = parseFloatSafe(nmea.fields[1]);
        if (heading == null) return;
        this.updateInstrumentData({
          heading: {
            headingDeg: heading,
            reference: 'magnetic',
            receivedAt,
          },
        }, nmea.type, receivedAt);
        return;
      }
      case 'VHW': {
        const heading = parseFloatSafe(nmea.fields[1]);
        const waterSpeedKnots = parseFloatSafe(nmea.fields[5]);
        const patch: Partial<MarineInstrumentData> = {};
        if (heading != null) {
          patch.heading = {
            headingDeg: heading,
            reference: 'true',
            receivedAt,
          };
        }
        if (waterSpeedKnots != null) {
          patch.waterSpeedKnots = waterSpeedKnots;
        }
        if (patch.heading || patch.waterSpeedKnots != null) {
          this.updateInstrumentData(patch, 'VHW', receivedAt);
        }
        return;
      }
      case 'MWV': {
        if (nmea.fields[5] !== 'A') return;
        const angleDeg = parseFloatSafe(nmea.fields[1]);
        const speed = parseFloatSafe(nmea.fields[3]);
        const speedKnots = speed != null ? convertWindToKnots(speed, nmea.fields[4]) : null;
        if (angleDeg == null || speedKnots == null) return;
        this.updateInstrumentData({
          wind: {
            angleDeg,
            speedKnots,
            reference: nmea.fields[2] === 'T' ? 'true' : 'relative',
            receivedAt,
          },
        }, 'MWV', receivedAt);
        return;
      }
      case 'MWD': {
        const angleDeg = parseFloatSafe(nmea.fields[1]);
        const speedKnots = parseFloatSafe(nmea.fields[5]);
        if (angleDeg == null || speedKnots == null) return;
        this.updateInstrumentData({
          wind: {
            angleDeg,
            speedKnots,
            reference: 'true',
            receivedAt,
          },
        }, 'MWD', receivedAt);
        return;
      }
      case 'DPT': {
        const depthM = parseFloatSafe(nmea.fields[1]);
        if (depthM == null) return;
        this.updateInstrumentData({
          depth: {
            depthM,
            offsetM: parseFloatSafe(nmea.fields[2]),
            sourceSentence: 'DPT',
            receivedAt,
          },
        }, 'DPT', receivedAt);
        return;
      }
      case 'DBT': {
        const depthM = parseFloatSafe(nmea.fields[3])
          ?? (parseFloatSafe(nmea.fields[1]) != null ? parseFloatSafe(nmea.fields[1])! * 0.3048 : null)
          ?? (parseFloatSafe(nmea.fields[5]) != null ? parseFloatSafe(nmea.fields[5])! * FATHOMS_TO_METERS : null);
        if (depthM == null) return;
        this.updateInstrumentData({
          depth: {
            depthM,
            offsetM: null,
            sourceSentence: 'DBT',
            receivedAt,
          },
        }, 'DBT', receivedAt);
      }
    }
  }

  /** Handle incoming data (may contain multiple lines). */
  private handleData(data: string): void {
    this.buffer += data;
    const lines = this.buffer.split('\n');
    // Keep the last incomplete line in the buffer
    this.buffer = lines.pop() ?? '';

    for (const line of lines) {
      const trimmed = line.trim();
      if (trimmed.length > 0) {
        this.processSentence(trimmed);
      }
    }
  }

  /** Process a single NMEA sentence. */
  private processSentence(sentence: string): void {
    if (!verifyChecksum(sentence)) {
      // Skip invalid checksum but don't error out
      return;
    }

    const nmea = parseNMEA(sentence);
    if (!nmea) return;

    const receivedAt = Date.now();
    this.lastMessageAt = receivedAt;
    this.processInstrumentSentence(nmea, receivedAt);

    // Only process AIS payloads below this point
    if (nmea.type !== 'VDM' && nmea.type !== 'VDO') return;

    // AIVDM fields: !AIVDM,fragCount,fragNum,seqId,channel,payload,fillBits*checksum
    const fragCount = parseInt(nmea.fields[1], 10);
    const fragNum = parseInt(nmea.fields[2], 10);
    const seqId = nmea.fields[3] || '0';
    const payload = nmea.fields[5];
    const fillBits = parseInt(nmea.fields[6]?.split('*')[0] ?? '0', 10);

    if (isNaN(fragCount) || isNaN(fragNum) || !payload) return;

    let fullPayload: string;

    if (fragCount === 1) {
      // Single-part message
      fullPayload = payload;
    } else {
      // Multi-part message
      const key = `${seqId}-${fragCount}`;
      let mp = this.multiParts.get(key);
      if (!mp) {
        mp = { total: fragCount, parts: new Map(), seqId: key, receivedAt: Date.now() };
        this.multiParts.set(key, mp);
      }
      mp.parts.set(fragNum, payload);

      if (mp.parts.size < mp.total) return; // Wait for more parts

      // Assemble full payload
      const parts: string[] = [];
      for (let i = 1; i <= mp.total; i++) {
        parts.push(mp.parts.get(i) ?? '');
      }
      fullPayload = parts.join('');
      this.multiParts.delete(key);
    }

    // Parse AIS payload
    const update = parseAISPayload(fullPayload, fillBits);
    if (!update || !update.mmsi) return;

    // Merge with existing vessel data
    const existing = this.vessels.get(update.mmsi);
    const merged: AISVesselUpdate = {
      mmsi: update.mmsi,
      lat: update.lat ?? existing?.lat ?? null,
      lon: update.lon ?? existing?.lon ?? null,
      sogKnots: update.sogKnots ?? existing?.sogKnots ?? null,
      cogDeg: update.cogDeg ?? existing?.cogDeg ?? null,
      headingDeg: update.headingDeg ?? existing?.headingDeg ?? null,
      navStatus: update.navStatus ?? existing?.navStatus ?? 15,
      messageType: update.messageType ?? 0,
      shipName: update.shipName ?? existing?.shipName ?? null,
      shipType: update.shipType ?? existing?.shipType ?? null,
      destination: update.destination ?? existing?.destination ?? null,
      lengthM: update.lengthM ?? existing?.lengthM ?? null,
      beamM: update.beamM ?? existing?.beamM ?? null,
      receivedAt: Date.now(),
    };

    this.vessels.set(update.mmsi, merged);

    // Notify listeners
    for (const listener of this.listeners) {
      try {
        listener(merged);
      } catch {
        // Don't let listener errors break the receiver
      }
    }
  }

  /** Remove vessels that haven't been updated in VESSEL_EXPIRY_MS. */
  private cleanupExpired(): void {
    const now = Date.now();
    for (const [mmsi, vessel] of this.vessels) {
      if (now - vessel.receivedAt > VESSEL_EXPIRY_MS) {
        this.vessels.delete(mmsi);
      }
    }

    // Also cleanup stale multi-part messages (older than 30s)
    for (const [key, mp] of this.multiParts) {
      if (now - mp.receivedAt > 30_000) {
        this.multiParts.delete(key);
      }
    }
  }
}

// ── Singleton ────────────────────────────────────────────────────

export const aisReceiver = new AISWifiReceiver();
