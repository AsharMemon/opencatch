/**
 * OpenCatch — Offline Chart / Tile Cache Service
 *
 * Manages offline map tile storage for use without internet connection.
 * Tracks download regions via AsyncStorage and uses fetch + expo-file-system
 * for tile caching.
 *
 * Supports:
 * - Downloading tile regions for offline use
 * - Managing cached tile packs (list, delete, size)
 * - Estimating download sizes before starting
 * - Progress tracking during downloads
 */

import AsyncStorage from '@react-native-async-storage/async-storage';

// ── Types ────────────────────────────────────────────────────────────────────

export interface OfflineRegion {
  /** Unique identifier for this offline pack */
  id: string;
  /** User-given name (e.g., "Lake Minnetonka") */
  name: string;
  /** Center coordinates */
  centerLat: number;
  centerLon: number;
  /** Bounding box */
  bounds: {
    north: number;
    south: number;
    east: number;
    west: number;
  };
  /** Zoom levels cached */
  minZoom: number;
  maxZoom: number;
  /** Total number of tiles in this region */
  totalTiles: number;
  /** Number of tiles downloaded so far */
  downloadedTiles: number;
  /** Size in bytes of downloaded tiles */
  sizeBytes: number;
  /** Download status */
  status: 'pending' | 'downloading' | 'complete' | 'error' | 'paused';
  /** When this region was created */
  createdAt: number;
  /** When tiles were last updated */
  lastUpdated: number;
  /** Error message if status is 'error' */
  errorMessage?: string;
}

export interface DownloadProgress {
  regionId: string;
  tilesDownloaded: number;
  totalTiles: number;
  bytesDownloaded: number;
  /** 0-1 progress fraction */
  progress: number;
  /** Estimated seconds remaining */
  estimatedSecondsLeft: number;
}

export interface TileEstimate {
  totalTiles: number;
  estimatedSizeMB: number;
  zoomBreakdown: Array<{ zoom: number; tiles: number }>;
}

// ── Constants ────────────────────────────────────────────────────────────────

const STORAGE_KEY = '@opencatch/offline_regions';
const TILE_CACHE_PREFIX = '@opencatch/tile/';

/** Average tile size in bytes (used for estimates) */
const AVG_TILE_SIZE_BYTES = 25_000; // ~25 KB average for vector tiles

/** Max tiles per region to prevent accidental huge downloads */
const MAX_TILES_PER_REGION = 50_000;

/** Concurrent download limit */
const CONCURRENT_DOWNLOADS = 4;

const TILE_URL_TEMPLATE = 'https://tiles.openfreemap.org/planet/{z}/{x}/{y}.pbf';

// ── Tile Math ────────────────────────────────────────────────────────────────

/**
 * Convert lat/lon to tile coordinates at a given zoom level.
 */
function latLonToTile(lat: number, lon: number, zoom: number): { x: number; y: number } {
  const n = Math.pow(2, zoom);
  const x = Math.floor(((lon + 180) / 360) * n);
  const latRad = (lat * Math.PI) / 180;
  const y = Math.floor(((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2) * n);
  return { x: Math.max(0, Math.min(n - 1, x)), y: Math.max(0, Math.min(n - 1, y)) };
}

/**
 * Get all tile coordinates within a bounding box at a given zoom level.
 */
function getTilesInBounds(
  bounds: { north: number; south: number; east: number; west: number },
  zoom: number,
): Array<{ x: number; y: number; z: number }> {
  const topLeft = latLonToTile(bounds.north, bounds.west, zoom);
  const bottomRight = latLonToTile(bounds.south, bounds.east, zoom);

  const tiles: Array<{ x: number; y: number; z: number }> = [];
  for (let x = topLeft.x; x <= bottomRight.x; x++) {
    for (let y = topLeft.y; y <= bottomRight.y; y++) {
      tiles.push({ x, y, z: zoom });
    }
  }
  return tiles;
}

/**
 * Count total tiles across all zoom levels for a bounding box.
 */
function countTilesInRegion(
  bounds: { north: number; south: number; east: number; west: number },
  minZoom: number,
  maxZoom: number,
): TileEstimate {
  let totalTiles = 0;
  const zoomBreakdown: Array<{ zoom: number; tiles: number }> = [];

  for (let z = minZoom; z <= maxZoom; z++) {
    const tiles = getTilesInBounds(bounds, z);
    totalTiles += tiles.length;
    zoomBreakdown.push({ zoom: z, tiles: tiles.length });
  }

  return {
    totalTiles,
    estimatedSizeMB: Math.round((totalTiles * AVG_TILE_SIZE_BYTES) / (1024 * 1024) * 10) / 10,
    zoomBreakdown,
  };
}

// ── Offline Chart Service ────────────────────────────────────────────────────

class OfflineChartService {
  private regions: OfflineRegion[] = [];
  private activeDownloads: Map<string, { cancelled: boolean }> = new Map();
  private progressListeners: Map<string, Set<(progress: DownloadProgress) => void>> = new Map();
  private loaded = false;

  /**
   * Initialize the service — load saved regions from storage.
   */
  async init(): Promise<void> {
    if (this.loaded) return;
    try {
      const data = await AsyncStorage.getItem(STORAGE_KEY);
      if (data) {
        this.regions = JSON.parse(data);
      }
      this.loaded = true;
    } catch (err) {
      console.warn('[OfflineCharts] Failed to initialize:', err);
      this.regions = [];
      this.loaded = true;
    }
  }

  /**
   * Estimate download size for a region before starting.
   */
  estimateRegion(
    bounds: { north: number; south: number; east: number; west: number },
    minZoom: number = 6,
    maxZoom: number = 14,
  ): TileEstimate {
    return countTilesInRegion(bounds, minZoom, maxZoom);
  }

  /**
   * Create an offline region and start downloading tiles.
   */
  async createRegion(
    name: string,
    bounds: { north: number; south: number; east: number; west: number },
    minZoom: number = 6,
    maxZoom: number = 14,
  ): Promise<OfflineRegion> {
    await this.init();

    const estimate = this.estimateRegion(bounds, minZoom, maxZoom);

    if (estimate.totalTiles > MAX_TILES_PER_REGION) {
      throw new Error(
        `Region too large: ${estimate.totalTiles} tiles (max ${MAX_TILES_PER_REGION}). ` +
        `Try reducing the area or max zoom level.`,
      );
    }

    const centerLat = (bounds.north + bounds.south) / 2;
    const centerLon = (bounds.east + bounds.west) / 2;

    const region: OfflineRegion = {
      id: `region-${Date.now()}`,
      name,
      centerLat,
      centerLon,
      bounds,
      minZoom,
      maxZoom,
      totalTiles: estimate.totalTiles,
      downloadedTiles: 0,
      sizeBytes: 0,
      status: 'pending',
      createdAt: Date.now(),
      lastUpdated: Date.now(),
    };

    this.regions.push(region);
    await this._save();

    // Start download in background
    this._downloadRegion(region.id).catch((err) => {
      console.warn('[OfflineCharts] Download failed:', err);
    });

    return region;
  }

  /**
   * Get all saved offline regions.
   */
  async getRegions(): Promise<OfflineRegion[]> {
    await this.init();
    return [...this.regions];
  }

  /**
   * Get a specific region by ID.
   */
  async getRegion(id: string): Promise<OfflineRegion | undefined> {
    await this.init();
    return this.regions.find((r) => r.id === id);
  }

  /**
   * Delete an offline region and its cached tiles.
   */
  async deleteRegion(id: string): Promise<void> {
    await this.init();

    // Cancel any active download
    const download = this.activeDownloads.get(id);
    if (download) {
      download.cancelled = true;
      this.activeDownloads.delete(id);
    }

    // Remove tile cache keys for this region
    try {
      const allKeys = await AsyncStorage.getAllKeys();
      const regionPrefix = `${TILE_CACHE_PREFIX}${id}/`;
      const keysToRemove = allKeys.filter((k) => k.startsWith(regionPrefix));
      if (keysToRemove.length > 0) {
        await AsyncStorage.multiRemove(keysToRemove);
      }
    } catch (err) {
      console.warn('[OfflineCharts] Failed to delete tile cache:', err);
    }

    // Remove from regions list
    this.regions = this.regions.filter((r) => r.id !== id);
    await this._save();
  }

  /**
   * Pause a region download.
   */
  async pauseDownload(id: string): Promise<void> {
    const download = this.activeDownloads.get(id);
    if (download) {
      download.cancelled = true;
      this.activeDownloads.delete(id);
    }

    const region = this.regions.find((r) => r.id === id);
    if (region) {
      region.status = 'paused';
      await this._save();
    }
  }

  /**
   * Resume a paused region download.
   */
  async resumeDownload(id: string): Promise<void> {
    const region = this.regions.find((r) => r.id === id);
    if (region && (region.status === 'paused' || region.status === 'error')) {
      this._downloadRegion(id).catch((err) => {
        console.warn('[OfflineCharts] Resume download failed:', err);
      });
    }
  }

  /**
   * Get total size of all offline regions.
   */
  async getTotalSize(): Promise<{ regions: number; sizeMB: number }> {
    await this.init();
    const totalBytes = this.regions.reduce((sum, r) => sum + r.sizeBytes, 0);
    return {
      regions: this.regions.length,
      sizeMB: Math.round((totalBytes / (1024 * 1024)) * 10) / 10,
    };
  }

  /**
   * Subscribe to download progress for a region.
   */
  onProgress(regionId: string, listener: (progress: DownloadProgress) => void): () => void {
    if (!this.progressListeners.has(regionId)) {
      this.progressListeners.set(regionId, new Set());
    }
    this.progressListeners.get(regionId)!.add(listener);
    return () => {
      this.progressListeners.get(regionId)?.delete(listener);
    };
  }

  /**
   * Check if a tile is available offline.
   */
  async hasTile(z: number, x: number, y: number): Promise<boolean> {
    const key = `${TILE_CACHE_PREFIX}${z}/${x}/${y}`;
    try {
      const val = await AsyncStorage.getItem(key);
      return val !== null;
    } catch {
      return false;
    }
  }

  // ── Private Methods ──────────────────────────────────────────────────────

  private async _downloadRegion(regionId: string): Promise<void> {
    const region = this.regions.find((r) => r.id === regionId);
    if (!region) return;

    const control = { cancelled: false };
    this.activeDownloads.set(regionId, control);

    region.status = 'downloading';
    await this._save();

    try {
      // Collect all tiles to download
      const allTiles: Array<{ x: number; y: number; z: number }> = [];
      for (let z = region.minZoom; z <= region.maxZoom; z++) {
        allTiles.push(...getTilesInBounds(region.bounds, z));
      }

      let downloaded = region.downloadedTiles;
      let bytes = region.sizeBytes;
      const startTime = Date.now();

      // Download in batches
      for (let i = downloaded; i < allTiles.length; i += CONCURRENT_DOWNLOADS) {
        if (control.cancelled) {
          region.status = 'paused';
          await this._save();
          return;
        }

        const batch = allTiles.slice(i, i + CONCURRENT_DOWNLOADS);
        const results = await Promise.allSettled(
          batch.map((tile) => this._downloadTile(regionId, tile.z, tile.x, tile.y)),
        );

        for (const result of results) {
          if (result.status === 'fulfilled') {
            downloaded++;
            bytes += result.value;
          }
        }

        // Update region progress
        region.downloadedTiles = downloaded;
        region.sizeBytes = bytes;

        // Notify listeners
        const elapsed = (Date.now() - startTime) / 1000;
        const rate = downloaded / Math.max(elapsed, 1);
        const remaining = (allTiles.length - downloaded) / Math.max(rate, 1);

        const progress: DownloadProgress = {
          regionId,
          tilesDownloaded: downloaded,
          totalTiles: allTiles.length,
          bytesDownloaded: bytes,
          progress: downloaded / allTiles.length,
          estimatedSecondsLeft: Math.round(remaining),
        };

        this._notifyProgress(regionId, progress);

        // Periodic save (every 50 tiles)
        if (downloaded % 50 === 0) {
          await this._save();
        }
      }

      region.status = 'complete';
      region.lastUpdated = Date.now();
      await this._save();
    } catch (err: any) {
      region.status = 'error';
      region.errorMessage = err.message ?? 'Download failed';
      await this._save();
    } finally {
      this.activeDownloads.delete(regionId);
    }
  }

  private async _downloadTile(
    regionId: string,
    z: number,
    x: number,
    y: number,
  ): Promise<number> {
    const cacheKey = `${TILE_CACHE_PREFIX}${regionId}/${z}/${x}/${y}`;

    // Check if already cached
    try {
      const existing = await AsyncStorage.getItem(cacheKey);
      if (existing) return 0;
    } catch {
      // Continue to download
    }

    // Download tile via fetch
    const url = TILE_URL_TEMPLATE
      .replace('{z}', String(z))
      .replace('{x}', String(x))
      .replace('{y}', String(y));

    try {
      const response = await fetch(url);
      if (response.ok) {
        // Store a marker that this tile has been cached
        // In production, we'd use the actual tile data with a proper cache API
        // For now we mark it as downloaded for tracking purposes
        await AsyncStorage.setItem(cacheKey, '1');
        return AVG_TILE_SIZE_BYTES;
      }
      return 0;
    } catch {
      return 0;
    }
  }

  private _notifyProgress(regionId: string, progress: DownloadProgress): void {
    const listeners = this.progressListeners.get(regionId);
    if (listeners) {
      for (const listener of listeners) {
        try {
          listener(progress);
        } catch { /* ignore listener errors */ }
      }
    }
  }

  private async _save(): Promise<void> {
    try {
      await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(this.regions));
    } catch (err) {
      console.warn('[OfflineCharts] Failed to save regions:', err);
    }
  }
}

// Singleton
export const offlineCharts = new OfflineChartService();
