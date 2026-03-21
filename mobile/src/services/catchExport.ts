/**
 * OpenCatch — Catch History Export Service
 *
 * Exports catch log data as CSV or shareable text format.
 * Uses expo-sharing and expo-file-system for native share sheet.
 *
 * Competitor parity: None of the three competitors offer robust export.
 * OpenCatch EXCEEDS by providing CSV export with full metadata.
 */

import AsyncStorage from '@react-native-async-storage/async-storage';
import * as Sharing from 'expo-sharing';
import * as FileSystem from 'expo-file-system';

// ── Types ────────────────────────────────────────────────────────────────────

export interface CatchRecord {
  id: string;
  species: string;
  weight?: number;
  length?: number;
  lat: number;
  lon: number;
  locationName?: string;
  bait?: string;
  technique?: string;
  waterTemp?: number;
  airTemp?: number;
  windSpeed?: number;
  pressure?: number;
  waterClarity?: string;
  notes?: string;
  released: boolean;
  timestamp: number;
}

export interface ExportOptions {
  format: 'csv' | 'text' | 'pdf';
  dateRange?: { start: Date; end: Date };
  species?: string;
  includeWeather?: boolean;
  includeLocation?: boolean;
  includePhotos?: boolean;
  reportType?: 'monthly' | 'yearly' | 'custom';
}

export interface ExportResult {
  success: boolean;
  filename?: string;
  recordCount: number;
  error?: string;
}

// ── Storage Keys ─────────────────────────────────────────────────────────────

const CATCHES_KEY = 'opencatch_catches';

// ── Data Access ──────────────────────────────────────────────────────────────

export async function getAllCatches(): Promise<CatchRecord[]> {
  try {
    const raw = await AsyncStorage.getItem(CATCHES_KEY);
    if (!raw) return [];
    const catches: CatchRecord[] = JSON.parse(raw);
    return catches.sort((a, b) => b.timestamp - a.timestamp);
  } catch {
    return [];
  }
}

function filterCatches(catches: CatchRecord[], options: ExportOptions): CatchRecord[] {
  let filtered = [...catches];

  if (options.dateRange) {
    const startMs = options.dateRange.start.getTime();
    const endMs = options.dateRange.end.getTime();
    filtered = filtered.filter((c) => c.timestamp >= startMs && c.timestamp <= endMs);
  }

  if (options.species) {
    filtered = filtered.filter((c) =>
      c.species.toLowerCase().includes(options.species!.toLowerCase()),
    );
  }

  return filtered;
}

// ── CSV Generation ───────────────────────────────────────────────────────────

function escapeCSV(val: string | number | boolean | undefined | null): string {
  if (val === undefined || val === null) return '';
  const str = String(val);
  if (str.includes(',') || str.includes('"') || str.includes('\n')) {
    return `"${str.replace(/"/g, '""')}"`;
  }
  return str;
}

function generateCSV(catches: CatchRecord[], options: ExportOptions): string {
  const headers = [
    'Date',
    'Time',
    'Species',
    'Weight (lbs)',
    'Length (in)',
    'Released',
    'Bait/Lure',
    'Technique',
  ];

  if (options.includeLocation !== false) {
    headers.push('Location', 'Latitude', 'Longitude');
  }

  if (options.includeWeather !== false) {
    headers.push('Water Temp (\u00B0F)', 'Air Temp (\u00B0F)', 'Wind (mph)', 'Pressure (hPa)', 'Water Clarity');
  }

  headers.push('Notes');

  const rows = catches.map((c) => {
    const d = new Date(c.timestamp);
    const row = [
      escapeCSV(d.toLocaleDateString()),
      escapeCSV(d.toLocaleTimeString()),
      escapeCSV(c.species),
      escapeCSV(c.weight),
      escapeCSV(c.length),
      escapeCSV(c.released ? 'Yes' : 'No'),
      escapeCSV(c.bait),
      escapeCSV(c.technique),
    ];

    if (options.includeLocation !== false) {
      row.push(
        escapeCSV(c.locationName),
        escapeCSV(c.lat?.toFixed(6)),
        escapeCSV(c.lon?.toFixed(6)),
      );
    }

    if (options.includeWeather !== false) {
      row.push(
        escapeCSV(c.waterTemp),
        escapeCSV(c.airTemp),
        escapeCSV(c.windSpeed),
        escapeCSV(c.pressure),
        escapeCSV(c.waterClarity),
      );
    }

    row.push(escapeCSV(c.notes));

    return row.join(',');
  });

  return [headers.join(','), ...rows].join('\n');
}

// ── Text Report Generation ───────────────────────────────────────────────────

function generateTextReport(catches: CatchRecord[]): string {
  const lines: string[] = [
    'OpenCatch \u2014 Fishing Log Export',
    `Generated: ${new Date().toLocaleDateString()} ${new Date().toLocaleTimeString()}`,
    `Total Catches: ${catches.length}`,
    '='.repeat(50),
    '',
  ];

  // Summary stats
  const speciesCounts: Record<string, number> = {};
  let totalWeight = 0;
  let weightCount = 0;
  let releasedCount = 0;

  for (const c of catches) {
    speciesCounts[c.species] = (speciesCounts[c.species] ?? 0) + 1;
    if (c.weight) { totalWeight += c.weight; weightCount++; }
    if (c.released) releasedCount++;
  }

  lines.push('SUMMARY');
  lines.push('-'.repeat(30));
  lines.push(`Species caught: ${Object.keys(speciesCounts).length}`);
  if (weightCount > 0) lines.push(`Avg weight: ${(totalWeight / weightCount).toFixed(1)} lbs`);
  lines.push(`Released: ${releasedCount}/${catches.length} (${Math.round(releasedCount / Math.max(1, catches.length) * 100)}%)`);
  lines.push('');

  lines.push('TOP SPECIES');
  lines.push('-'.repeat(30));
  const sorted = Object.entries(speciesCounts).sort((a, b) => b[1] - a[1]);
  for (const [species, count] of sorted.slice(0, 10)) {
    lines.push(`  ${species}: ${count} catches`);
  }
  lines.push('');

  lines.push('DETAILED LOG');
  lines.push('-'.repeat(30));
  for (const c of catches) {
    const d = new Date(c.timestamp);
    lines.push(`${d.toLocaleDateString()} ${d.toLocaleTimeString()}`);
    lines.push(`  Species: ${c.species}`);
    if (c.weight) lines.push(`  Weight: ${c.weight} lbs`);
    if (c.length) lines.push(`  Length: ${c.length} in`);
    if (c.bait) lines.push(`  Bait: ${c.bait}`);
    if (c.locationName) lines.push(`  Location: ${c.locationName}`);
    lines.push(`  Released: ${c.released ? 'Yes' : 'No'}`);
    if (c.notes) lines.push(`  Notes: ${c.notes}`);
    lines.push('');
  }

  return lines.join('\n');
}

// ── PDF Generation ──────────────────────────────────────────────────────────

/**
 * Generate a formatted PDF of catch history using expo-print.
 * Includes OpenCatch branding, summary stats, species chart, and detailed table.
 */
export async function exportAsPDF(
  catches: CatchRecord[],
  options: ExportOptions,
): Promise<ExportResult> {
  try {
    // Dynamic import — expo-print may not be installed
    const Print = await import('expo-print').catch(() => null);
    if (!Print) {
      return { success: false, recordCount: 0, error: 'expo-print not available' };
    }

    const filtered = filterCatches(catches, options);
    if (filtered.length === 0) {
      return { success: false, recordCount: 0, error: 'No catches found matching criteria' };
    }

    const html = generatePDFHTML(filtered, options);
    const dateStr = new Date().toISOString().split('T')[0];
    const reportLabel = options.reportType === 'monthly'
      ? 'monthly'
      : options.reportType === 'yearly'
      ? 'yearly'
      : 'report';
    const filename = `opencatch-${reportLabel}-${dateStr}.pdf`;

    const { uri } = await Print.printToFileAsync({ html, width: 612, height: 792 });

    // Move to a friendlier filename
    const cacheDir = (FileSystem as any).cacheDirectory ?? `${(FileSystem as any).documentDirectory}`;
    const destUri = `${cacheDir}${filename}`;

    try {
      await FileSystem.deleteAsync(destUri, { idempotent: true });
    } catch {
      // ignore
    }
    await FileSystem.moveAsync({ from: uri, to: destUri });

    // Share via native sheet
    const canShare = await Sharing.isAvailableAsync();
    if (canShare) {
      await Sharing.shareAsync(destUri, {
        mimeType: 'application/pdf',
        dialogTitle: 'Export Fishing Report',
        UTI: 'com.adobe.pdf',
      });
    }

    return { success: true, filename, recordCount: filtered.length };
  } catch (err) {
    return {
      success: false,
      recordCount: 0,
      error: err instanceof Error ? err.message : 'PDF export failed',
    };
  }
}

function escapeHTML(str: string): string {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function generatePDFHTML(catches: CatchRecord[], options: ExportOptions): string {
  // Compute summary stats
  const speciesCounts: Record<string, number> = {};
  let totalWeight = 0;
  let weightCount = 0;
  let releasedCount = 0;
  let heaviest: CatchRecord | null = null;

  for (const c of catches) {
    speciesCounts[c.species] = (speciesCounts[c.species] ?? 0) + 1;
    if (c.weight) {
      totalWeight += c.weight;
      weightCount++;
      if (!heaviest || c.weight > (heaviest.weight ?? 0)) heaviest = c;
    }
    if (c.released) releasedCount++;
  }

  const speciesCount = Object.keys(speciesCounts).length;
  const avgWeight = weightCount > 0 ? (totalWeight / weightCount).toFixed(1) : '--';
  const releaseRate = catches.length > 0
    ? Math.round((releasedCount / catches.length) * 100)
    : 0;

  // Date range label
  let dateRangeLabel = 'All Time';
  if (options.dateRange) {
    const s = options.dateRange.start.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
    const e = options.dateRange.end.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
    dateRangeLabel = `${s} - ${e}`;
  } else if (options.reportType === 'monthly') {
    const now = new Date();
    dateRangeLabel = now.toLocaleDateString('en-US', { month: 'long', year: 'numeric' });
  } else if (options.reportType === 'yearly') {
    dateRangeLabel = new Date().getFullYear().toString();
  }

  // Top species
  const topSpecies = Object.entries(speciesCounts)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 5);

  // Catch rows
  const catchRows = catches.map((c) => {
    const d = new Date(c.timestamp);
    const ds = d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    const ts = d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
    return `
      <tr>
        <td>${ds}<br/><span class="time">${ts}</span></td>
        <td><strong>${escapeHTML(c.species)}</strong></td>
        <td>${c.weight ? `${c.weight} lbs` : '--'}</td>
        <td>${c.length ? `${c.length}"` : '--'}</td>
        <td>${c.locationName ? escapeHTML(c.locationName) : `${c.lat.toFixed(3)}, ${c.lon.toFixed(3)}`}</td>
        <td>${c.bait ? escapeHTML(c.bait) : '--'}</td>
        <td class="center">${c.released ? 'Yes' : 'No'}</td>
      </tr>`;
  }).join('');

  return `<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Playfair+Display:wght@700&family=Inter:wght@400;500;600;700&display=swap');
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      font-family: 'Inter', -apple-system, sans-serif;
      color: #1A1A18;
      background: #FFFFFF;
      padding: 40px;
      font-size: 11px;
      line-height: 1.5;
    }
    .header {
      display: flex;
      justify-content: space-between;
      align-items: flex-end;
      border-bottom: 3px solid #0A6EBD;
      padding-bottom: 16px;
      margin-bottom: 24px;
    }
    .brand {
      font-family: 'Playfair Display', serif;
      font-size: 28px;
      font-weight: 700;
      color: #0A6EBD;
      letter-spacing: -0.5px;
    }
    .brand-sub { color: #8A8A85; font-size: 11px; margin-top: 2px; }
    .date-range { text-align: right; color: #4A4A45; font-size: 12px; font-weight: 600; }
    .generated { color: #8A8A85; font-size: 10px; margin-top: 2px; }
    .summary { display: flex; gap: 16px; margin-bottom: 24px; }
    .stat-card {
      flex: 1;
      background: #FAFAF7;
      border: 1px solid #E5E5E0;
      border-radius: 8px;
      padding: 12px;
      text-align: center;
    }
    .stat-value {
      font-size: 22px;
      font-weight: 700;
      color: #0A6EBD;
      font-family: 'Playfair Display', serif;
    }
    .stat-label {
      font-size: 10px;
      color: #8A8A85;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      margin-top: 2px;
    }
    .section-title {
      font-family: 'Playfair Display', serif;
      font-size: 16px;
      font-weight: 700;
      color: #1A1A18;
      margin-bottom: 10px;
      margin-top: 20px;
    }
    .species-bar { display: flex; align-items: center; margin-bottom: 6px; }
    .species-name { width: 120px; font-weight: 600; font-size: 11px; }
    .species-fill { height: 14px; background: #0A6EBD; border-radius: 3px; min-width: 4px; }
    .species-count { margin-left: 8px; color: #8A8A85; font-size: 10px; font-weight: 600; }
    table { width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 10px; }
    th {
      background: #0A6EBD;
      color: #FFFFFF;
      padding: 6px 8px;
      text-align: left;
      font-weight: 600;
      font-size: 9px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }
    td { padding: 6px 8px; border-bottom: 1px solid #E5E5E0; vertical-align: top; }
    tr:nth-child(even) td { background: #FAFAF7; }
    .time { color: #8A8A85; font-size: 9px; }
    .center { text-align: center; }
    .footer {
      margin-top: 32px;
      padding-top: 12px;
      border-top: 1px solid #E5E5E0;
      text-align: center;
      color: #B5B5B0;
      font-size: 9px;
    }
    .footer a { color: #0A6EBD; text-decoration: none; }
  </style>
</head>
<body>
  <div class="header">
    <div>
      <div class="brand">OpenCatch</div>
      <div class="brand-sub">Fishing Log Report</div>
    </div>
    <div>
      <div class="date-range">${escapeHTML(dateRangeLabel)}</div>
      <div class="generated">Generated ${new Date().toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' })}</div>
    </div>
  </div>

  <div class="summary">
    <div class="stat-card">
      <div class="stat-value">${catches.length}</div>
      <div class="stat-label">Total Catches</div>
    </div>
    <div class="stat-card">
      <div class="stat-value">${speciesCount}</div>
      <div class="stat-label">Species</div>
    </div>
    <div class="stat-card">
      <div class="stat-value">${avgWeight}</div>
      <div class="stat-label">Avg Weight (lbs)</div>
    </div>
    <div class="stat-card">
      <div class="stat-value">${releaseRate}%</div>
      <div class="stat-label">Release Rate</div>
    </div>
    ${heaviest ? `<div class="stat-card"><div class="stat-value">${heaviest.weight}</div><div class="stat-label">Biggest (lbs)</div></div>` : ''}
  </div>

  <div class="section-title">Top Species</div>
  ${topSpecies.map(([name, count]) => {
    const maxCount = topSpecies[0][1] as number;
    const pct = Math.round((count / maxCount) * 100);
    return `<div class="species-bar"><span class="species-name">${escapeHTML(name)}</span><div class="species-fill" style="width: ${pct}%"></div><span class="species-count">${count}</span></div>`;
  }).join('')}

  <div class="section-title">Detailed Log</div>
  <table>
    <thead>
      <tr><th>Date</th><th>Species</th><th>Weight</th><th>Length</th><th>Location</th><th>Bait</th><th class="center">Released</th></tr>
    </thead>
    <tbody>${catchRows}</tbody>
  </table>

  <div class="footer">OpenCatch Fishing Log &mdash; opencatch.app</div>
</body>
</html>`;
}

// ── Public API ───────────────────────────────────────────────────────────────

/**
 * Export catch history to a file and open the share sheet.
 */
export async function exportCatches(options: ExportOptions): Promise<ExportResult> {
  try {
    const allCatches = await getAllCatches();
    const filtered = filterCatches(allCatches, options);

    if (filtered.length === 0) {
      return { success: false, recordCount: 0, error: 'No catches found matching criteria' };
    }

    // PDF export uses a separate pipeline via expo-print
    if (options.format === 'pdf') {
      return exportAsPDF(filtered, options);
    }

    const content = options.format === 'csv'
      ? generateCSV(filtered, options)
      : generateTextReport(filtered);

    const ext = options.format === 'csv' ? 'csv' : 'txt';
    const dateStr = new Date().toISOString().split('T')[0];
    const filename = `opencatch-catches-${dateStr}.${ext}`;
    const cacheDir = (FileSystem as any).cacheDirectory ?? `${(FileSystem as any).documentDirectory}`;
    const fileUri = `${cacheDir}${filename}`;

    await FileSystem.writeAsStringAsync(fileUri, content);

    const canShare = await Sharing.isAvailableAsync();
    if (canShare) {
      await Sharing.shareAsync(fileUri, {
        mimeType: options.format === 'csv' ? 'text/csv' : 'text/plain',
        dialogTitle: 'Export Catch Log',
        UTI: options.format === 'csv' ? 'public.comma-separated-values-text' : 'public.plain-text',
      });
    }

    return { success: true, filename, recordCount: filtered.length };
  } catch (err) {
    return {
      success: false,
      recordCount: 0,
      error: err instanceof Error ? err.message : 'Export failed',
    };
  }
}

/**
 * Get catch statistics for the profile/stats screen.
 */
export async function getCatchStats(): Promise<{
  totalCatches: number;
  speciesCount: number;
  totalWeight: number;
  avgWeight: number;
  releaseRate: number;
  topSpecies: { name: string; count: number }[];
  monthlyTrend: { month: string; count: number }[];
}> {
  const catches = await getAllCatches();

  const speciesCounts: Record<string, number> = {};
  let totalWeight = 0;
  let weightCount = 0;
  let releasedCount = 0;
  const monthlyCounts: Record<string, number> = {};

  for (const c of catches) {
    speciesCounts[c.species] = (speciesCounts[c.species] ?? 0) + 1;
    if (c.weight) { totalWeight += c.weight; weightCount++; }
    if (c.released) releasedCount++;

    const d = new Date(c.timestamp);
    const monthKey = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
    monthlyCounts[monthKey] = (monthlyCounts[monthKey] ?? 0) + 1;
  }

  const topSpecies = Object.entries(speciesCounts)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 5)
    .map(([name, count]) => ({ name, count }));

  const monthlyTrend = Object.entries(monthlyCounts)
    .sort((a, b) => a[0].localeCompare(b[0]))
    .slice(-12)
    .map(([month, count]) => ({ month, count }));

  return {
    totalCatches: catches.length,
    speciesCount: Object.keys(speciesCounts).length,
    totalWeight,
    avgWeight: weightCount > 0 ? totalWeight / weightCount : 0,
    releaseRate: catches.length > 0 ? releasedCount / catches.length : 0,
    topSpecies,
    monthlyTrend,
  };
}
