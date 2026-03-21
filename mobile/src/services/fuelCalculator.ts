/**
 * OpenCatch — Fuel Calculator Service
 *
 * Calculates fuel needs, range, and trip costs for boating.
 * Integrates with boat profile service for vessel specs.
 */

import { getDefaultBoat, type BoatProfile } from './boatProfile';

// ── Types ────────────────────────────────────────────────────────────────────

export interface FuelCalculation {
  fuelNeededGallons: number;
  tripTimeHours: number;
  costEstimate: number | null; // null if no price provided
}

export interface RangeCalculation {
  rangeNm: number;
  rangeMi: number;
  tripTimeHours: number;
}

export interface TripCost {
  fuelCost: number;
  fuelGallons: number;
}

export interface BoatFuelProfile {
  fuelCapacityGallons: number;
  cruiseSpeedKnots: number;
  fuelConsumptionGPH: number;  // gallons per hour at cruise speed
}

// ── Common fuel consumption estimates by boat type (GPH at cruise) ──────────

export const FUEL_CONSUMPTION_ESTIMATES: Record<string, { low: number; cruise: number; high: number }> = {
  bass_boat: { low: 3, cruise: 6, high: 12 },
  center_console: { low: 5, cruise: 10, high: 20 },
  pontoon: { low: 2, cruise: 4, high: 8 },
  jon_boat: { low: 1, cruise: 2.5, high: 5 },
  skiff: { low: 2, cruise: 5, high: 10 },
  kayak: { low: 0, cruise: 0, high: 0 },
  canoe: { low: 0, cruise: 0, high: 0 },
  sailboat: { low: 0.5, cruise: 1.5, high: 3 },
  inflatable: { low: 1, cruise: 3, high: 6 },
  other: { low: 2, cruise: 5, high: 10 },
};

// ── Calculator functions ─────────────────────────────────────────────────────

/**
 * Calculate fuel needed for a trip.
 * @param distanceNm Distance in nautical miles
 * @param speedKnots Speed in knots
 * @param fuelConsumptionGPH Fuel consumption in gallons per hour
 * @returns Gallons needed for the trip
 */
export function calculateFuelNeeded(
  distanceNm: number,
  speedKnots: number,
  fuelConsumptionGPH: number,
): number {
  if (speedKnots <= 0 || fuelConsumptionGPH <= 0) return 0;
  const tripTimeHours = distanceNm / speedKnots;
  return tripTimeHours * fuelConsumptionGPH;
}

/**
 * Calculate maximum range with available fuel.
 * @param fuelGallons Available fuel in gallons
 * @param speedKnots Speed in knots
 * @param fuelConsumptionGPH Fuel consumption in gallons per hour
 * @returns Maximum range in nautical miles
 */
export function calculateRange(
  fuelGallons: number,
  speedKnots: number,
  fuelConsumptionGPH: number,
): number {
  if (fuelConsumptionGPH <= 0 || speedKnots <= 0) return 0;
  const hoursOfFuel = fuelGallons / fuelConsumptionGPH;
  return hoursOfFuel * speedKnots;
}

/**
 * Calculate trip fuel cost.
 * @param fuelGallons Gallons of fuel needed
 * @param pricePerGallon Price per gallon in dollars
 * @returns Trip fuel cost in dollars
 */
export function calculateTripCost(
  fuelGallons: number,
  pricePerGallon: number,
): number {
  return fuelGallons * pricePerGallon;
}

/**
 * Full trip calculation with all details.
 */
export function calculateTrip(
  distanceNm: number,
  speedKnots: number,
  fuelConsumptionGPH: number,
  pricePerGallon?: number,
): FuelCalculation {
  const tripTimeHours = speedKnots > 0 ? distanceNm / speedKnots : 0;
  const fuelNeededGallons = calculateFuelNeeded(distanceNm, speedKnots, fuelConsumptionGPH);
  const costEstimate = pricePerGallon != null ? calculateTripCost(fuelNeededGallons, pricePerGallon) : null;

  return {
    fuelNeededGallons,
    tripTimeHours,
    costEstimate,
  };
}

/**
 * Calculate range details from available fuel.
 */
export function calculateRangeDetails(
  fuelGallons: number,
  speedKnots: number,
  fuelConsumptionGPH: number,
): RangeCalculation {
  const rangeNm = calculateRange(fuelGallons, speedKnots, fuelConsumptionGPH);
  return {
    rangeNm,
    rangeMi: rangeNm * 1.15078,
    tripTimeHours: fuelConsumptionGPH > 0 ? fuelGallons / fuelConsumptionGPH : 0,
  };
}

/**
 * Get default fuel profile from boat profile, with estimates for missing data.
 */
export async function getDefaultFuelProfile(): Promise<BoatFuelProfile | null> {
  const boat = await getDefaultBoat();
  if (!boat) return null;

  const estimates = FUEL_CONSUMPTION_ESTIMATES[boat.type] ?? FUEL_CONSUMPTION_ESTIMATES.other;

  return {
    fuelCapacityGallons: boat.fuelCapacity ?? 30,
    cruiseSpeedKnots: 20, // reasonable default
    fuelConsumptionGPH: estimates.cruise,
  };
}

/**
 * Format duration in hours to a human-readable string.
 */
export function formatDuration(hours: number): string {
  if (hours < 1) {
    return `${Math.round(hours * 60)} min`;
  }
  const h = Math.floor(hours);
  const m = Math.round((hours - h) * 60);
  return m > 0 ? `${h}h ${m}m` : `${h}h`;
}
