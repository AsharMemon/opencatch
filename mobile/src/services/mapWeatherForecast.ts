import { getMarineForecast } from './marineWeather';

const OPEN_METEO_URL = 'https://api.open-meteo.com/v1/forecast';
const CACHE_TTL_MS = 20 * 60 * 1000;

export interface MapWeatherHour {
  time: string;
  label: string;
  tempF: number;
  windMph: number;
  gustMph: number | null;
  windDirectionDeg: number;
  precipitationProbability: number;
  weatherCode: number | null;
  waveHeightFt: number | null;
  wavePeriodSec: number | null;
}

export interface MapWeatherSummary {
  tempF: number;
  windMph: number;
  gustMph: number | null;
  windDirectionDeg: number;
  precipitationProbability: number;
  weatherCode: number | null;
  label: string;
  icon: string;
  waveHeightFt: number | null;
  wavePeriodSec: number | null;
  waterTempF: number | null;
  currentSpeedKnots: number | null;
  currentDirectionDeg: number | null;
  visibilityNm: number | null;
}

export interface MapWeatherForecast {
  lat: number;
  lon: number;
  updatedAt: string;
  current: MapWeatherSummary;
  hourly: MapWeatherHour[];
}

interface OpenMeteoForecastResponse {
  hourly?: {
    time?: string[];
    temperature_2m?: number[];
    weather_code?: number[];
    precipitation_probability?: number[];
    wind_speed_10m?: number[];
    wind_gusts_10m?: number[];
    wind_direction_10m?: number[];
  };
}

interface CacheEntry {
  forecast: MapWeatherForecast;
  timestamp: number;
}

const cache = new Map<string, CacheEntry>();

function cacheKey(lat: number, lon: number): string {
  return `${lat.toFixed(2)},${lon.toFixed(2)}`;
}

function wmoToCondition(weatherCode: number | null | undefined): { label: string; icon: string } {
  if (weatherCode == null) return { label: 'Forecast', icon: 'partly-sunny-outline' };
  if (weatherCode === 0) return { label: 'Clear', icon: 'sunny-outline' };
  if (weatherCode === 1) return { label: 'Mostly Clear', icon: 'sunny-outline' };
  if (weatherCode === 2) return { label: 'Partly Cloudy', icon: 'partly-sunny-outline' };
  if (weatherCode === 3) return { label: 'Overcast', icon: 'cloudy-outline' };
  if (weatherCode >= 45 && weatherCode <= 48) return { label: 'Fog', icon: 'cloud-outline' };
  if (weatherCode >= 51 && weatherCode <= 57) return { label: 'Drizzle', icon: 'rainy-outline' };
  if (weatherCode >= 61 && weatherCode <= 67) return { label: 'Rain', icon: 'rainy-outline' };
  if (weatherCode >= 71 && weatherCode <= 77) return { label: 'Snow', icon: 'snow-outline' };
  if (weatherCode >= 80 && weatherCode <= 82) return { label: 'Showers', icon: 'rainy-outline' };
  if (weatherCode >= 85 && weatherCode <= 86) return { label: 'Snow Showers', icon: 'snow-outline' };
  if (weatherCode >= 95) return { label: 'Thunderstorm', icon: 'thunderstorm-outline' };
  return { label: 'Forecast', icon: 'partly-sunny-outline' };
}

function formatHourLabel(isoTime: string, isFirst: boolean): string {
  const date = new Date(isoTime);
  if (!Number.isFinite(date.getTime())) return isFirst ? 'Now' : '--';
  if (isFirst) return 'Now';
  return date.toLocaleTimeString([], { hour: 'numeric' }).replace(':00', '');
}

export async function getMapWeatherForecast(
  lat: number,
  lon: number,
): Promise<MapWeatherForecast> {
  const key = cacheKey(lat, lon);
  const cached = cache.get(key);
  if (cached && Date.now() - cached.timestamp < CACHE_TTL_MS) {
    return cached.forecast;
  }

  const params = new URLSearchParams({
    latitude: lat.toFixed(4),
    longitude: lon.toFixed(4),
    hourly: [
      'temperature_2m',
      'weather_code',
      'precipitation_probability',
      'wind_speed_10m',
      'wind_gusts_10m',
      'wind_direction_10m',
    ].join(','),
    forecast_hours: '12',
    temperature_unit: 'fahrenheit',
    wind_speed_unit: 'mph',
    precipitation_unit: 'inch',
    timezone: 'auto',
  });

  const [weatherResponse, marineForecast] = await Promise.all([
    fetch(`${OPEN_METEO_URL}?${params}`).then(async (response) => {
      if (!response.ok) {
        throw new Error(`Open-Meteo ${response.status}`);
      }
      return response.json() as Promise<OpenMeteoForecastResponse>;
    }),
    getMarineForecast(lat, lon).catch(() => null),
  ]);

  const hourly = weatherResponse.hourly;
  const times = hourly?.time ?? [];
  if (times.length === 0) {
    throw new Error('No hourly weather forecast returned');
  }

  const marineByTime = new Map(
    (marineForecast?.hourly ?? []).map((entry) => [entry.time, entry] as const),
  );

  const hours: MapWeatherHour[] = times.slice(0, 12).map((time, index) => {
    const marineHour = marineByTime.get(time);
    return {
      time,
      label: formatHourLabel(time, index === 0),
      tempF: Math.round(hourly?.temperature_2m?.[index] ?? 0),
      windMph: Math.round(hourly?.wind_speed_10m?.[index] ?? 0),
      gustMph: hourly?.wind_gusts_10m?.[index] != null
        ? Math.round(hourly.wind_gusts_10m[index] ?? 0)
        : null,
      windDirectionDeg: Math.round(hourly?.wind_direction_10m?.[index] ?? 0),
      precipitationProbability: Math.round(hourly?.precipitation_probability?.[index] ?? 0),
      weatherCode: hourly?.weather_code?.[index] ?? null,
      waveHeightFt: marineHour ? Number(marineHour.waveHeightFt.toFixed(1)) : null,
      wavePeriodSec: marineHour ? Math.round(marineHour.wavePeriodSec) : null,
    };
  });

  const first = hours[0];
  const condition = wmoToCondition(first.weatherCode);
  const forecast: MapWeatherForecast = {
    lat,
    lon,
    updatedAt: first.time,
    current: {
      tempF: first.tempF,
      windMph: first.windMph,
      gustMph: first.gustMph,
      windDirectionDeg: first.windDirectionDeg,
      precipitationProbability: first.precipitationProbability,
      weatherCode: first.weatherCode,
      label: condition.label,
      icon: condition.icon,
      waveHeightFt:
        first.waveHeightFt != null && first.waveHeightFt > 0.05 ? first.waveHeightFt : null,
      wavePeriodSec: first.wavePeriodSec,
      waterTempF:
        marineForecast?.seaSurfaceTempF && marineForecast.seaSurfaceTempF > 0
          ? Math.round(marineForecast.seaSurfaceTempF)
          : null,
      currentSpeedKnots:
        marineForecast?.currentSpeedKnots != null
          ? Number(marineForecast.currentSpeedKnots.toFixed(1))
          : null,
      currentDirectionDeg:
        marineForecast?.currentDirectionDeg != null
          ? Math.round(marineForecast.currentDirectionDeg)
          : null,
      visibilityNm:
        marineForecast?.visibilityNm != null
          ? Number(marineForecast.visibilityNm.toFixed(1))
          : null,
    },
    hourly: hours,
  };

  cache.set(key, { forecast, timestamp: Date.now() });
  return forecast;
}
