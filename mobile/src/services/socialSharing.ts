/**
 * OpenCatch — Social Sharing Service
 *
 * Generate shareable content for catches, trips, and stats.
 * Uses expo-sharing and native Share API for cross-platform sharing.
 */

import { Share, Platform } from 'react-native';
import type { EnhancedCatch } from './catchEnhancements';
import type { PlannedTrip } from './tripPlanner';

// ── Types ────────────────────────────────────────────────────────────────────

export interface ShareableStats {
  totalCatches: number;
  totalSpecies: number;
  personalBests: Array<{ species: string; weight: number }>;
  topLocation: string;
  memberSince: string;
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function formatDate(iso: string | number): string {
  const d = new Date(iso);
  return d.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
}

function formatWeight(lbs: number | undefined): string {
  if (!lbs) return '';
  return `${lbs} lb`;
}

// ── Share Catch ──────────────────────────────────────────────────────────────

export async function shareCatchToSocial(catchData: EnhancedCatch): Promise<void> {
  const lines: string[] = [
    `\u{1F3A3} Caught a ${catchData.species}!`,
    '',
  ];

  if (catchData.weight) {
    lines.push(`Weight: ${formatWeight(catchData.weight)}`);
  }
  if (catchData.length) {
    lines.push(`Length: ${catchData.length} in`);
  }
  if (catchData.locationName) {
    lines.push(`Location: ${catchData.locationName}`);
  }
  if (catchData.bait) {
    lines.push(`Bait: ${catchData.bait}`);
  }
  if (catchData.technique) {
    lines.push(`Technique: ${catchData.technique}`);
  }

  lines.push(`Date: ${formatDate(catchData.timestamp)}`);
  lines.push('');
  lines.push('Shared from OpenCatch - Know before you go.');

  try {
    await Share.share({
      message: lines.join('\n'),
      title: `${catchData.species} catch on OpenCatch`,
    });
  } catch {
    // User cancelled or share failed — silent
  }
}

// ── Share Trip ───────────────────────────────────────────────────────────────

export async function shareTrip(trip: PlannedTrip): Promise<void> {
  const lines: string[] = [
    `\u{1F3A3} Fishing Trip Planned!`,
    '',
    `Location: ${trip.locationName}`,
    `Date: ${formatDate(trip.date)}`,
  ];

  if (trip.forecastSummary) {
    const f = trip.forecastSummary;
    lines.push('');
    lines.push(`Forecast: ${f.weatherLabel}`);
    lines.push(`Temp: ${f.highTemp}\u00B0F / ${f.lowTemp}\u00B0F`);
    lines.push(`Wind: ${f.windSpeed} mph`);
    lines.push(`Rain: ${f.precipChance}%`);
  }

  if (trip.notes) {
    lines.push('');
    lines.push(`Notes: ${trip.notes}`);
  }

  lines.push('');
  lines.push('Planned with OpenCatch - Know before you go.');

  try {
    await Share.share({
      message: lines.join('\n'),
      title: `Fishing trip to ${trip.locationName}`,
    });
  } catch {
    // User cancelled or share failed — silent
  }
}

// ── Share Stats ──────────────────────────────────────────────────────────────

export async function shareStats(stats: ShareableStats): Promise<void> {
  const lines: string[] = [
    `\u{1F4CA} My OpenCatch Fishing Stats`,
    '',
    `Total Catches: ${stats.totalCatches}`,
    `Species Caught: ${stats.totalSpecies}`,
  ];

  if (stats.topLocation) {
    lines.push(`Top Location: ${stats.topLocation}`);
  }

  if (stats.personalBests.length > 0) {
    lines.push('');
    lines.push('Personal Bests:');
    for (const pb of stats.personalBests.slice(0, 5)) {
      lines.push(`  ${pb.species}: ${formatWeight(pb.weight)}`);
    }
  }

  lines.push('');
  lines.push(`Fishing since ${stats.memberSince}`);
  lines.push('');
  lines.push('Tracked with OpenCatch - Know before you go.');

  try {
    await Share.share({
      message: lines.join('\n'),
      title: 'My OpenCatch Fishing Stats',
    });
  } catch {
    // User cancelled or share failed — silent
  }
}

// ── Generate Catch Card Preview Text ─────────────────────────────────────────

export function generateCatchPreview(catchData: EnhancedCatch): string {
  const parts: string[] = [catchData.species];
  if (catchData.weight) parts.push(`${formatWeight(catchData.weight)}`);
  if (catchData.locationName) parts.push(`at ${catchData.locationName}`);
  parts.push(`on ${formatDate(catchData.timestamp)}`);
  return parts.join(' \u2022 ');
}
