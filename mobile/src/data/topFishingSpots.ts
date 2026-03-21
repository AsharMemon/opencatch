/**
 * Top 500 fishing destinations in the US and Canada.
 *
 * These are pre-bundled so the map shows pins instantly on startup
 * before the dynamic Overpass discovery kicks in. Each entry is a
 * minimal FishingLocation with heuristic scores — real scores are
 * back-filled once the backend responds.
 */

import type { FishingLocation } from '../types/models';

export interface StaticSpot {
  id: string;
  name: string;
  lat: number;
  lon: number;
  type: 'lake' | 'river' | 'reservoir' | 'pond';
  state: string;  // province/state abbreviation
  country: 'US' | 'CA';
}

const RAW_SPOTS: StaticSpot[] = [
  // ── Great Lakes ──
  { id: 'top-1', name: 'Lake Erie', lat: 42.2, lon: -81.2, type: 'lake', state: 'OH', country: 'US' },
  { id: 'top-2', name: 'Lake Michigan', lat: 43.5, lon: -87.0, type: 'lake', state: 'WI', country: 'US' },
  { id: 'top-3', name: 'Lake Superior', lat: 47.5, lon: -87.5, type: 'lake', state: 'MI', country: 'US' },
  { id: 'top-4', name: 'Lake Huron', lat: 44.8, lon: -82.4, type: 'lake', state: 'MI', country: 'US' },
  { id: 'top-5', name: 'Lake Ontario', lat: 43.7, lon: -77.8, type: 'lake', state: 'NY', country: 'US' },

  // ── Major US Lakes ──
  { id: 'top-6', name: 'Lake Okeechobee', lat: 26.95, lon: -80.8, type: 'lake', state: 'FL', country: 'US' },
  { id: 'top-7', name: 'Lake of the Ozarks', lat: 38.15, lon: -92.65, type: 'reservoir', state: 'MO', country: 'US' },
  { id: 'top-8', name: 'Lake Mead', lat: 36.15, lon: -114.4, type: 'reservoir', state: 'NV', country: 'US' },
  { id: 'top-9', name: 'Lake Powell', lat: 37.05, lon: -111.5, type: 'reservoir', state: 'UT', country: 'US' },
  { id: 'top-10', name: 'Lake Champlain', lat: 44.53, lon: -73.33, type: 'lake', state: 'VT', country: 'US' },
  { id: 'top-11', name: 'Rainy Lake', lat: 48.6, lon: -93.2, type: 'lake', state: 'MN', country: 'US' },
  { id: 'top-12', name: 'Mille Lacs Lake', lat: 46.2, lon: -93.6, type: 'lake', state: 'MN', country: 'US' },
  { id: 'top-13', name: 'Leech Lake', lat: 47.15, lon: -94.35, type: 'lake', state: 'MN', country: 'US' },
  { id: 'top-14', name: 'Lake Winnebago', lat: 44.0, lon: -88.4, type: 'lake', state: 'WI', country: 'US' },
  { id: 'top-15', name: 'Lake of the Woods', lat: 49.0, lon: -94.8, type: 'lake', state: 'MN', country: 'US' },
  { id: 'top-16', name: 'Lake Sakakawea', lat: 47.7, lon: -102.3, type: 'reservoir', state: 'ND', country: 'US' },
  { id: 'top-17', name: 'Fort Peck Lake', lat: 47.6, lon: -106.8, type: 'reservoir', state: 'MT', country: 'US' },
  { id: 'top-18', name: 'Flathead Lake', lat: 47.9, lon: -114.15, type: 'lake', state: 'MT', country: 'US' },
  { id: 'top-19', name: 'Lake Tahoe', lat: 39.1, lon: -120.04, type: 'lake', state: 'CA', country: 'US' },
  { id: 'top-20', name: 'Clear Lake', lat: 39.05, lon: -122.8, type: 'lake', state: 'CA', country: 'US' },

  // ── Southeast / Gulf ──
  { id: 'top-21', name: 'Toledo Bend Reservoir', lat: 31.2, lon: -93.55, type: 'reservoir', state: 'TX', country: 'US' },
  { id: 'top-22', name: 'Sam Rayburn Reservoir', lat: 31.1, lon: -94.1, type: 'reservoir', state: 'TX', country: 'US' },
  { id: 'top-23', name: 'Lake Fork', lat: 32.85, lon: -95.7, type: 'reservoir', state: 'TX', country: 'US' },
  { id: 'top-24', name: 'Lake Guntersville', lat: 34.4, lon: -86.25, type: 'reservoir', state: 'AL', country: 'US' },
  { id: 'top-25', name: 'Pickwick Lake', lat: 34.85, lon: -87.9, type: 'reservoir', state: 'AL', country: 'US' },
  { id: 'top-26', name: 'Wheeler Lake', lat: 34.6, lon: -87.0, type: 'reservoir', state: 'AL', country: 'US' },
  { id: 'top-27', name: 'Lake Seminole', lat: 30.75, lon: -84.85, type: 'reservoir', state: 'GA', country: 'US' },
  { id: 'top-28', name: 'Lake Lanier', lat: 34.25, lon: -83.95, type: 'reservoir', state: 'GA', country: 'US' },
  { id: 'top-29', name: 'Clarks Hill Lake', lat: 33.65, lon: -82.15, type: 'reservoir', state: 'GA', country: 'US' },
  { id: 'top-30', name: 'Lake Hartwell', lat: 34.45, lon: -82.85, type: 'reservoir', state: 'SC', country: 'US' },
  { id: 'top-31', name: 'Santee Cooper Lakes', lat: 33.55, lon: -80.15, type: 'reservoir', state: 'SC', country: 'US' },
  { id: 'top-32', name: 'Kentucky Lake', lat: 36.6, lon: -88.05, type: 'reservoir', state: 'KY', country: 'US' },
  { id: 'top-33', name: 'Lake Barkley', lat: 36.8, lon: -87.85, type: 'reservoir', state: 'KY', country: 'US' },
  { id: 'top-34', name: 'Dale Hollow Lake', lat: 36.6, lon: -85.45, type: 'reservoir', state: 'TN', country: 'US' },
  { id: 'top-35', name: 'Norris Lake', lat: 36.25, lon: -84.1, type: 'reservoir', state: 'TN', country: 'US' },
  { id: 'top-36', name: 'Chickamauga Lake', lat: 35.1, lon: -85.1, type: 'reservoir', state: 'TN', country: 'US' },
  { id: 'top-37', name: 'Lake Toho', lat: 28.2, lon: -81.4, type: 'lake', state: 'FL', country: 'US' },
  { id: 'top-38', name: 'Lake Istokpoga', lat: 27.4, lon: -81.3, type: 'lake', state: 'FL', country: 'US' },
  { id: 'top-39', name: 'Rodman Reservoir', lat: 29.5, lon: -81.85, type: 'reservoir', state: 'FL', country: 'US' },
  { id: 'top-40', name: 'Lake Kissimmee', lat: 27.85, lon: -81.3, type: 'lake', state: 'FL', country: 'US' },

  // ── Rivers (US) ──
  { id: 'top-41', name: 'Mississippi River — La Crosse', lat: 43.8, lon: -91.25, type: 'river', state: 'WI', country: 'US' },
  { id: 'top-42', name: 'Columbia River — Portland', lat: 45.6, lon: -122.7, type: 'river', state: 'OR', country: 'US' },
  { id: 'top-43', name: 'St. Johns River', lat: 29.9, lon: -81.6, type: 'river', state: 'FL', country: 'US' },
  { id: 'top-44', name: 'Sacramento River', lat: 40.4, lon: -122.4, type: 'river', state: 'CA', country: 'US' },
  { id: 'top-45', name: 'Snake River', lat: 43.6, lon: -116.2, type: 'river', state: 'ID', country: 'US' },
  { id: 'top-46', name: 'Delaware River', lat: 41.0, lon: -74.9, type: 'river', state: 'NY', country: 'US' },
  { id: 'top-47', name: 'Susquehanna River', lat: 41.2, lon: -76.0, type: 'river', state: 'PA', country: 'US' },
  { id: 'top-48', name: 'James River', lat: 37.5, lon: -79.4, type: 'river', state: 'VA', country: 'US' },
  { id: 'top-49', name: 'Allegheny River', lat: 41.8, lon: -79.1, type: 'river', state: 'PA', country: 'US' },
  { id: 'top-50', name: 'White River', lat: 36.4, lon: -92.6, type: 'river', state: 'AR', country: 'US' },

  // ── More major US reservoirs ──
  { id: 'top-51', name: 'Table Rock Lake', lat: 36.6, lon: -93.3, type: 'reservoir', state: 'MO', country: 'US' },
  { id: 'top-52', name: 'Bull Shoals Lake', lat: 36.4, lon: -92.6, type: 'reservoir', state: 'AR', country: 'US' },
  { id: 'top-53', name: 'Beaver Lake', lat: 36.35, lon: -93.85, type: 'reservoir', state: 'AR', country: 'US' },
  { id: 'top-54', name: 'Greers Ferry Lake', lat: 35.5, lon: -92.15, type: 'reservoir', state: 'AR', country: 'US' },
  { id: 'top-55', name: 'Grand Lake o the Cherokees', lat: 36.55, lon: -94.85, type: 'reservoir', state: 'OK', country: 'US' },
  { id: 'top-56', name: 'Lake Texoma', lat: 33.8, lon: -96.55, type: 'reservoir', state: 'TX', country: 'US' },
  { id: 'top-57', name: 'Broken Bow Lake', lat: 34.15, lon: -94.65, type: 'reservoir', state: 'OK', country: 'US' },
  { id: 'top-58', name: 'Lake Murray', lat: 34.05, lon: -81.2, type: 'reservoir', state: 'SC', country: 'US' },
  { id: 'top-59', name: 'Lake Norman', lat: 35.5, lon: -80.95, type: 'reservoir', state: 'NC', country: 'US' },
  { id: 'top-60', name: 'Jordan Lake', lat: 35.7, lon: -79.0, type: 'reservoir', state: 'NC', country: 'US' },
  { id: 'top-61', name: 'Smith Mountain Lake', lat: 37.05, lon: -79.55, type: 'reservoir', state: 'VA', country: 'US' },
  { id: 'top-62', name: 'Lake Anna', lat: 38.05, lon: -77.8, type: 'reservoir', state: 'VA', country: 'US' },
  { id: 'top-63', name: 'Kerr Lake', lat: 36.6, lon: -78.35, type: 'reservoir', state: 'VA', country: 'US' },
  { id: 'top-64', name: 'Lake Cumberland', lat: 36.9, lon: -84.95, type: 'reservoir', state: 'KY', country: 'US' },
  { id: 'top-65', name: 'Rough River Lake', lat: 37.6, lon: -86.5, type: 'reservoir', state: 'KY', country: 'US' },
  { id: 'top-66', name: 'Lake Shelbyville', lat: 39.4, lon: -88.8, type: 'reservoir', state: 'IL', country: 'US' },
  { id: 'top-67', name: 'Rend Lake', lat: 38.05, lon: -88.95, type: 'reservoir', state: 'IL', country: 'US' },
  { id: 'top-68', name: 'Stockton Lake', lat: 37.65, lon: -93.75, type: 'reservoir', state: 'MO', country: 'US' },
  { id: 'top-69', name: 'Truman Lake', lat: 38.3, lon: -93.4, type: 'reservoir', state: 'MO', country: 'US' },
  { id: 'top-70', name: 'Mark Twain Lake', lat: 39.5, lon: -91.75, type: 'reservoir', state: 'MO', country: 'US' },

  // ── Northeast / Mid-Atlantic ──
  { id: 'top-71', name: 'Lake George', lat: 43.45, lon: -73.7, type: 'lake', state: 'NY', country: 'US' },
  { id: 'top-72', name: 'Oneida Lake', lat: 43.2, lon: -75.9, type: 'lake', state: 'NY', country: 'US' },
  { id: 'top-73', name: 'Cayuga Lake', lat: 42.7, lon: -76.7, type: 'lake', state: 'NY', country: 'US' },
  { id: 'top-74', name: 'Seneca Lake', lat: 42.65, lon: -76.9, type: 'lake', state: 'NY', country: 'US' },
  { id: 'top-75', name: 'Lake Winnipesaukee', lat: 43.6, lon: -71.3, type: 'lake', state: 'NH', country: 'US' },
  { id: 'top-76', name: 'Quabbin Reservoir', lat: 42.4, lon: -72.3, type: 'reservoir', state: 'MA', country: 'US' },
  { id: 'top-77', name: 'Candlewood Lake', lat: 41.45, lon: -73.55, type: 'lake', state: 'CT', country: 'US' },
  { id: 'top-78', name: 'Lake Wallenpaupack', lat: 41.4, lon: -75.2, type: 'reservoir', state: 'PA', country: 'US' },
  { id: 'top-79', name: 'Pymatuning Reservoir', lat: 41.55, lon: -80.5, type: 'reservoir', state: 'PA', country: 'US' },
  { id: 'top-80', name: 'Deep Creek Lake', lat: 39.5, lon: -79.35, type: 'reservoir', state: 'MD', country: 'US' },

  // ── Western US ──
  { id: 'top-81', name: 'Lake Havasu', lat: 34.5, lon: -114.35, type: 'reservoir', state: 'AZ', country: 'US' },
  { id: 'top-82', name: 'Roosevelt Lake', lat: 33.7, lon: -111.15, type: 'reservoir', state: 'AZ', country: 'US' },
  { id: 'top-83', name: 'Lake Pleasant', lat: 33.9, lon: -112.3, type: 'reservoir', state: 'AZ', country: 'US' },
  { id: 'top-84', name: 'Elephant Butte Lake', lat: 33.15, lon: -107.2, type: 'reservoir', state: 'NM', country: 'US' },
  { id: 'top-85', name: 'Flaming Gorge Reservoir', lat: 41.0, lon: -109.5, type: 'reservoir', state: 'WY', country: 'US' },
  { id: 'top-86', name: 'Yellowstone Lake', lat: 44.45, lon: -110.35, type: 'lake', state: 'WY', country: 'US' },
  { id: 'top-87', name: 'Boysen Reservoir', lat: 43.4, lon: -108.2, type: 'reservoir', state: 'WY', country: 'US' },
  { id: 'top-88', name: 'Blue Mesa Reservoir', lat: 38.45, lon: -107.3, type: 'reservoir', state: 'CO', country: 'US' },
  { id: 'top-89', name: 'Horsetooth Reservoir', lat: 40.55, lon: -105.17, type: 'reservoir', state: 'CO', country: 'US' },
  { id: 'top-90', name: 'Navajo Lake', lat: 36.8, lon: -107.6, type: 'reservoir', state: 'NM', country: 'US' },

  // ── Pacific Northwest / California ──
  { id: 'top-91', name: 'Lake Shasta', lat: 40.75, lon: -122.35, type: 'reservoir', state: 'CA', country: 'US' },
  { id: 'top-92', name: 'Lake Oroville', lat: 39.55, lon: -121.45, type: 'reservoir', state: 'CA', country: 'US' },
  { id: 'top-93', name: 'Don Pedro Reservoir', lat: 37.7, lon: -120.3, type: 'reservoir', state: 'CA', country: 'US' },
  { id: 'top-94', name: 'Lake Berryessa', lat: 38.6, lon: -122.25, type: 'reservoir', state: 'CA', country: 'US' },
  { id: 'top-95', name: 'Lake Casitas', lat: 34.4, lon: -119.35, type: 'reservoir', state: 'CA', country: 'US' },
  { id: 'top-96', name: 'Lake Chelan', lat: 47.9, lon: -120.3, type: 'lake', state: 'WA', country: 'US' },
  { id: 'top-97', name: 'Moses Lake', lat: 47.1, lon: -119.3, type: 'lake', state: 'WA', country: 'US' },
  { id: 'top-98', name: 'Banks Lake', lat: 47.7, lon: -119.1, type: 'reservoir', state: 'WA', country: 'US' },
  { id: 'top-99', name: 'Lake Roosevelt', lat: 47.95, lon: -118.5, type: 'reservoir', state: 'WA', country: 'US' },
  { id: 'top-100', name: 'Potholes Reservoir', lat: 46.95, lon: -119.35, type: 'reservoir', state: 'WA', country: 'US' },
  { id: 'top-101', name: 'Haystack Reservoir', lat: 44.5, lon: -121.15, type: 'reservoir', state: 'OR', country: 'US' },
  { id: 'top-102', name: 'Wickiup Reservoir', lat: 43.7, lon: -121.7, type: 'reservoir', state: 'OR', country: 'US' },
  { id: 'top-103', name: 'Detroit Lake', lat: 44.7, lon: -122.15, type: 'reservoir', state: 'OR', country: 'US' },

  // ── Northern US (upper Midwest) ──
  { id: 'top-104', name: 'Lake Oahe', lat: 44.4, lon: -100.4, type: 'reservoir', state: 'SD', country: 'US' },
  { id: 'top-105', name: 'Lake Sharpe', lat: 44.1, lon: -99.5, type: 'reservoir', state: 'SD', country: 'US' },
  { id: 'top-106', name: 'Lake Francis Case', lat: 43.6, lon: -99.2, type: 'reservoir', state: 'SD', country: 'US' },
  { id: 'top-107', name: 'Devils Lake', lat: 48.1, lon: -99.0, type: 'lake', state: 'ND', country: 'US' },
  { id: 'top-108', name: 'Lake Vermilion', lat: 47.85, lon: -92.3, type: 'lake', state: 'MN', country: 'US' },
  { id: 'top-109', name: 'Gull Lake', lat: 46.4, lon: -94.35, type: 'lake', state: 'MN', country: 'US' },
  { id: 'top-110', name: 'Winnibigoshish Lake', lat: 47.4, lon: -94.05, type: 'lake', state: 'MN', country: 'US' },
  { id: 'top-111', name: 'Lake Pepin', lat: 44.45, lon: -92.2, type: 'lake', state: 'MN', country: 'US' },
  { id: 'top-112', name: 'Sturgeon Bay', lat: 44.85, lon: -87.35, type: 'lake', state: 'WI', country: 'US' },
  { id: 'top-113', name: 'Lake Mendota', lat: 43.1, lon: -89.4, type: 'lake', state: 'WI', country: 'US' },
  { id: 'top-114', name: 'Castle Rock Lake', lat: 43.85, lon: -89.95, type: 'reservoir', state: 'WI', country: 'US' },
  { id: 'top-115', name: 'Lake Gogebic', lat: 46.5, lon: -89.6, type: 'lake', state: 'MI', country: 'US' },
  { id: 'top-116', name: 'Houghton Lake', lat: 44.3, lon: -84.75, type: 'lake', state: 'MI', country: 'US' },
  { id: 'top-117', name: 'Lake St. Clair', lat: 42.45, lon: -82.7, type: 'lake', state: 'MI', country: 'US' },
  { id: 'top-118', name: 'Burt Lake', lat: 45.45, lon: -84.7, type: 'lake', state: 'MI', country: 'US' },
  { id: 'top-119', name: 'Grand Traverse Bay', lat: 44.8, lon: -85.5, type: 'lake', state: 'MI', country: 'US' },
  { id: 'top-120', name: 'Saginaw Bay', lat: 43.8, lon: -83.8, type: 'lake', state: 'MI', country: 'US' },

  // ── Midwest & Plains ──
  { id: 'top-121', name: 'Lake McConaughy', lat: 41.2, lon: -101.95, type: 'reservoir', state: 'NE', country: 'US' },
  { id: 'top-122', name: 'Milford Lake', lat: 39.15, lon: -96.9, type: 'reservoir', state: 'KS', country: 'US' },
  { id: 'top-123', name: 'Tuttle Creek Lake', lat: 39.3, lon: -96.6, type: 'reservoir', state: 'KS', country: 'US' },
  { id: 'top-124', name: 'Lake Red Rock', lat: 41.35, lon: -93.0, type: 'reservoir', state: 'IA', country: 'US' },
  { id: 'top-125', name: 'Spirit Lake', lat: 43.45, lon: -95.1, type: 'lake', state: 'IA', country: 'US' },
  { id: 'top-126', name: 'West Okoboji Lake', lat: 43.35, lon: -95.15, type: 'lake', state: 'IA', country: 'US' },

  // ── Alaska ──
  { id: 'top-127', name: 'Kenai River', lat: 60.5, lon: -150.8, type: 'river', state: 'AK', country: 'US' },
  { id: 'top-128', name: 'Kasilof River', lat: 60.35, lon: -151.3, type: 'river', state: 'AK', country: 'US' },
  { id: 'top-129', name: 'Lake Clark', lat: 60.2, lon: -154.3, type: 'lake', state: 'AK', country: 'US' },
  { id: 'top-130', name: 'Naknek River', lat: 58.7, lon: -157.0, type: 'river', state: 'AK', country: 'US' },

  // ── Hawaii ──
  { id: 'top-131', name: 'Wahiawa Reservoir', lat: 21.5, lon: -158.05, type: 'reservoir', state: 'HI', country: 'US' },

  // ── More Southeast ──
  { id: 'top-132', name: 'Lake Eufaula', lat: 35.3, lon: -95.35, type: 'reservoir', state: 'OK', country: 'US' },
  { id: 'top-133', name: 'Tenkiller Lake', lat: 35.7, lon: -94.95, type: 'reservoir', state: 'OK', country: 'US' },
  { id: 'top-134', name: 'Sardis Lake', lat: 34.6, lon: -95.35, type: 'reservoir', state: 'OK', country: 'US' },
  { id: 'top-135', name: 'Lake Conroe', lat: 30.4, lon: -95.55, type: 'reservoir', state: 'TX', country: 'US' },
  { id: 'top-136', name: 'Lake Travis', lat: 30.4, lon: -97.9, type: 'reservoir', state: 'TX', country: 'US' },
  { id: 'top-137', name: 'Lake Amistad', lat: 29.45, lon: -101.05, type: 'reservoir', state: 'TX', country: 'US' },
  { id: 'top-138', name: 'Falcon Lake', lat: 26.85, lon: -99.2, type: 'reservoir', state: 'TX', country: 'US' },
  { id: 'top-139', name: 'Lake Livingston', lat: 30.85, lon: -95.05, type: 'reservoir', state: 'TX', country: 'US' },
  { id: 'top-140', name: 'Choke Canyon Reservoir', lat: 28.5, lon: -98.35, type: 'reservoir', state: 'TX', country: 'US' },
  { id: 'top-141', name: 'Richland Chambers Reservoir', lat: 31.95, lon: -96.1, type: 'reservoir', state: 'TX', country: 'US' },
  { id: 'top-142', name: 'Cedar Creek Lake', lat: 32.35, lon: -96.05, type: 'reservoir', state: 'TX', country: 'US' },
  { id: 'top-143', name: 'Lake Bob Sandlin', lat: 33.05, lon: -95.0, type: 'reservoir', state: 'TX', country: 'US' },
  { id: 'top-144', name: "Caddo Lake", lat: 32.7, lon: -94.1, type: 'lake', state: 'TX', country: 'US' },
  { id: 'top-145', name: 'Ross Barnett Reservoir', lat: 32.4, lon: -90.0, type: 'reservoir', state: 'MS', country: 'US' },
  { id: 'top-146', name: 'Grenada Lake', lat: 33.8, lon: -89.75, type: 'reservoir', state: 'MS', country: 'US' },
  { id: 'top-147', name: 'Lake Martin', lat: 32.65, lon: -85.9, type: 'reservoir', state: 'AL', country: 'US' },
  { id: 'top-148', name: 'Lewis Smith Lake', lat: 34.1, lon: -87.1, type: 'reservoir', state: 'AL', country: 'US' },
  { id: 'top-149', name: 'Weiss Lake', lat: 34.15, lon: -85.8, type: 'reservoir', state: 'AL', country: 'US' },
  { id: 'top-150', name: 'West Point Lake', lat: 32.9, lon: -85.15, type: 'reservoir', state: 'GA', country: 'US' },

  // ── Canada — Ontario ──
  { id: 'top-151', name: 'Lake Simcoe', lat: 44.4, lon: -79.35, type: 'lake', state: 'ON', country: 'CA' },
  { id: 'top-152', name: 'Lake Nipissing', lat: 46.3, lon: -79.5, type: 'lake', state: 'ON', country: 'CA' },
  { id: 'top-153', name: 'Georgian Bay', lat: 45.0, lon: -80.5, type: 'lake', state: 'ON', country: 'CA' },
  { id: 'top-154', name: 'Lake of Bays', lat: 45.2, lon: -79.1, type: 'lake', state: 'ON', country: 'CA' },
  { id: 'top-155', name: 'Rice Lake', lat: 44.2, lon: -78.2, type: 'lake', state: 'ON', country: 'CA' },
  { id: 'top-156', name: 'Quinte Bay', lat: 44.15, lon: -77.1, type: 'lake', state: 'ON', country: 'CA' },
  { id: 'top-157', name: 'Lake Temagami', lat: 47.05, lon: -80.05, type: 'lake', state: 'ON', country: 'CA' },
  { id: 'top-158', name: 'Lake Couchiching', lat: 44.6, lon: -79.4, type: 'lake', state: 'ON', country: 'CA' },
  { id: 'top-159', name: 'Kawartha Lakes', lat: 44.4, lon: -78.75, type: 'lake', state: 'ON', country: 'CA' },
  { id: 'top-160', name: 'Lake Scugog', lat: 44.2, lon: -78.95, type: 'lake', state: 'ON', country: 'CA' },

  // ── Canada — Quebec ──
  { id: 'top-161', name: 'Lac Saint-Pierre', lat: 46.2, lon: -72.8, type: 'lake', state: 'QC', country: 'CA' },
  { id: 'top-162', name: 'Lac Memphremagog', lat: 45.1, lon: -72.25, type: 'lake', state: 'QC', country: 'CA' },
  { id: 'top-163', name: 'Lac Saint-Jean', lat: 48.6, lon: -72.1, type: 'lake', state: 'QC', country: 'CA' },
  { id: 'top-164', name: 'Gouin Reservoir', lat: 48.35, lon: -74.6, type: 'reservoir', state: 'QC', country: 'CA' },
  { id: 'top-165', name: 'Lac Champlain (QC)', lat: 45.0, lon: -73.3, type: 'lake', state: 'QC', country: 'CA' },

  // ── Canada — Manitoba ──
  { id: 'top-166', name: 'Lake Winnipeg', lat: 52.1, lon: -97.25, type: 'lake', state: 'MB', country: 'CA' },
  { id: 'top-167', name: 'Lake Manitoba', lat: 50.5, lon: -98.7, type: 'lake', state: 'MB', country: 'CA' },
  { id: 'top-168', name: 'Lake Winnipegosis', lat: 51.9, lon: -100.0, type: 'lake', state: 'MB', country: 'CA' },

  // ── Canada — Saskatchewan ──
  { id: 'top-169', name: 'Lac La Ronge', lat: 55.1, lon: -105.3, type: 'lake', state: 'SK', country: 'CA' },
  { id: 'top-170', name: 'Tobin Lake', lat: 53.6, lon: -103.5, type: 'reservoir', state: 'SK', country: 'CA' },
  { id: 'top-171', name: 'Lake Diefenbaker', lat: 51.0, lon: -107.0, type: 'reservoir', state: 'SK', country: 'CA' },
  { id: 'top-172', name: 'Reindeer Lake', lat: 57.3, lon: -102.3, type: 'lake', state: 'SK', country: 'CA' },

  // ── Canada — Alberta ──
  { id: 'top-173', name: 'Bow River', lat: 51.05, lon: -114.1, type: 'river', state: 'AB', country: 'CA' },
  { id: 'top-174', name: 'North Saskatchewan River', lat: 53.5, lon: -113.5, type: 'river', state: 'AB', country: 'CA' },
  { id: 'top-175', name: 'Pigeon Lake', lat: 52.95, lon: -114.0, type: 'lake', state: 'AB', country: 'CA' },
  { id: 'top-176', name: 'Lac La Biche', lat: 54.8, lon: -112.0, type: 'lake', state: 'AB', country: 'CA' },
  { id: 'top-177', name: 'Sylvan Lake', lat: 52.3, lon: -114.1, type: 'lake', state: 'AB', country: 'CA' },
  { id: 'top-178', name: 'Gull Lake', lat: 52.55, lon: -113.5, type: 'lake', state: 'AB', country: 'CA' },
  { id: 'top-251', name: 'Ghost Lake', lat: 51.18, lon: -114.88, type: 'reservoir', state: 'AB', country: 'CA' },
  { id: 'top-252', name: 'Arbour Lake', lat: 51.10, lon: -114.20, type: 'lake', state: 'AB', country: 'CA' },
  { id: 'top-253', name: 'Spray Lakes Reservoir', lat: 50.85, lon: -115.35, type: 'reservoir', state: 'AB', country: 'CA' },
  { id: 'top-254', name: 'Glenmore Reservoir', lat: 50.98, lon: -114.10, type: 'reservoir', state: 'AB', country: 'CA' },
  { id: 'top-255', name: 'Pine Lake', lat: 52.12, lon: -113.45, type: 'lake', state: 'AB', country: 'CA' },
  { id: 'top-256', name: 'Wabamun Lake', lat: 53.55, lon: -114.45, type: 'lake', state: 'AB', country: 'CA' },
  { id: 'top-257', name: 'Chain Lakes Reservoir', lat: 50.25, lon: -114.15, type: 'reservoir', state: 'AB', country: 'CA' },
  { id: 'top-258', name: 'Bearspaw Reservoir', lat: 51.10, lon: -114.30, type: 'reservoir', state: 'AB', country: 'CA' },

  // ── Canada — British Columbia ──
  { id: 'top-179', name: 'Shuswap Lake', lat: 50.9, lon: -119.25, type: 'lake', state: 'BC', country: 'CA' },
  { id: 'top-180', name: 'Okanagan Lake', lat: 49.85, lon: -119.5, type: 'lake', state: 'BC', country: 'CA' },
  { id: 'top-181', name: 'Kootenay Lake', lat: 49.6, lon: -116.8, type: 'lake', state: 'BC', country: 'CA' },
  { id: 'top-182', name: 'Fraser River', lat: 49.2, lon: -121.8, type: 'river', state: 'BC', country: 'CA' },
  { id: 'top-183', name: 'Kamloops Lake', lat: 50.8, lon: -120.5, type: 'lake', state: 'BC', country: 'CA' },
  { id: 'top-184', name: 'Adams Lake', lat: 51.1, lon: -119.7, type: 'lake', state: 'BC', country: 'CA' },
  { id: 'top-185', name: 'Quesnel Lake', lat: 52.5, lon: -121.0, type: 'lake', state: 'BC', country: 'CA' },
  { id: 'top-186', name: 'Stuart Lake', lat: 54.4, lon: -124.3, type: 'lake', state: 'BC', country: 'CA' },
  { id: 'top-187', name: 'Babine Lake', lat: 55.2, lon: -126.1, type: 'lake', state: 'BC', country: 'CA' },

  // ── Canada — Atlantic ──
  { id: 'top-188', name: 'Miramichi River', lat: 46.9, lon: -65.9, type: 'river', state: 'NB', country: 'CA' },
  { id: 'top-189', name: 'Grand Lake (NB)', lat: 46.2, lon: -66.1, type: 'lake', state: 'NB', country: 'CA' },
  { id: 'top-190', name: 'Bras d\'Or Lake', lat: 45.95, lon: -60.8, type: 'lake', state: 'NS', country: 'CA' },
  { id: 'top-191', name: 'Margaree River', lat: 46.4, lon: -61.1, type: 'river', state: 'NS', country: 'CA' },

  // ── More US lakes to fill out major regions ──
  { id: 'top-192', name: 'Lake Winnisquam', lat: 43.5, lon: -71.55, type: 'lake', state: 'NH', country: 'US' },
  { id: 'top-193', name: 'Moosehead Lake', lat: 45.6, lon: -69.65, type: 'lake', state: 'ME', country: 'US' },
  { id: 'top-194', name: 'Sebago Lake', lat: 43.85, lon: -70.55, type: 'lake', state: 'ME', country: 'US' },
  { id: 'top-195', name: 'Rangeley Lake', lat: 44.95, lon: -70.65, type: 'lake', state: 'ME', country: 'US' },
  { id: 'top-196', name: 'Lake Bomoseen', lat: 43.65, lon: -73.2, type: 'lake', state: 'VT', country: 'US' },
  { id: 'top-197', name: 'Chautauqua Lake', lat: 42.15, lon: -79.4, type: 'lake', state: 'NY', country: 'US' },
  { id: 'top-198', name: 'Conesus Lake', lat: 42.75, lon: -77.7, type: 'lake', state: 'NY', country: 'US' },
  { id: 'top-199', name: 'Lake Gaston', lat: 36.5, lon: -77.9, type: 'reservoir', state: 'VA', country: 'US' },
  { id: 'top-200', name: 'Lake Wateree', lat: 34.35, lon: -80.7, type: 'reservoir', state: 'SC', country: 'US' },

  // ── Fill out to ~300 with important fishing destinations ──
  { id: 'top-201', name: 'Lake Ouachita', lat: 34.6, lon: -93.2, type: 'reservoir', state: 'AR', country: 'US' },
  { id: 'top-202', name: 'DeGray Lake', lat: 34.2, lon: -93.1, type: 'reservoir', state: 'AR', country: 'US' },
  { id: 'top-203', name: 'Norfork Lake', lat: 36.45, lon: -92.25, type: 'reservoir', state: 'AR', country: 'US' },
  { id: 'top-204', name: 'Lake Dardanelle', lat: 35.3, lon: -93.15, type: 'reservoir', state: 'AR', country: 'US' },
  { id: 'top-205', name: 'Lake Hamilton', lat: 34.5, lon: -93.1, type: 'reservoir', state: 'AR', country: 'US' },
  { id: 'top-206', name: 'Watts Bar Lake', lat: 35.6, lon: -84.8, type: 'reservoir', state: 'TN', country: 'US' },
  { id: 'top-207', name: 'Cherokee Lake', lat: 36.15, lon: -83.5, type: 'reservoir', state: 'TN', country: 'US' },
  { id: 'top-208', name: 'Douglas Lake', lat: 36.0, lon: -83.3, type: 'reservoir', state: 'TN', country: 'US' },
  { id: 'top-209', name: 'Old Hickory Lake', lat: 36.3, lon: -86.4, type: 'reservoir', state: 'TN', country: 'US' },
  { id: 'top-210', name: 'Percy Priest Lake', lat: 36.1, lon: -86.6, type: 'reservoir', state: 'TN', country: 'US' },
  { id: 'top-211', name: 'Center Hill Lake', lat: 36.1, lon: -85.8, type: 'reservoir', state: 'TN', country: 'US' },
  { id: 'top-212', name: 'Lake Texoma (OK)', lat: 33.85, lon: -96.8, type: 'reservoir', state: 'OK', country: 'US' },
  { id: 'top-213', name: 'Skiatook Lake', lat: 36.4, lon: -96.05, type: 'reservoir', state: 'OK', country: 'US' },
  { id: 'top-214', name: 'Lake O\' the Pines', lat: 32.75, lon: -94.5, type: 'reservoir', state: 'TX', country: 'US' },
  { id: 'top-215', name: 'Chautauqua Lake (TX)', lat: 32.35, lon: -95.05, type: 'reservoir', state: 'TX', country: 'US' },
  { id: 'top-216', name: 'Lake Palestine', lat: 32.1, lon: -95.55, type: 'reservoir', state: 'TX', country: 'US' },
  { id: 'top-217', name: 'Lake Tawakoni', lat: 32.85, lon: -96.0, type: 'reservoir', state: 'TX', country: 'US' },
  { id: 'top-218', name: 'Possum Kingdom Lake', lat: 32.85, lon: -98.5, type: 'reservoir', state: 'TX', country: 'US' },
  { id: 'top-219', name: 'Lake Belton', lat: 31.1, lon: -97.5, type: 'reservoir', state: 'TX', country: 'US' },
  { id: 'top-220', name: 'Lake LBJ', lat: 30.55, lon: -98.35, type: 'reservoir', state: 'TX', country: 'US' },

  // ── More Western ──
  { id: 'top-221', name: 'Lake Pend Oreille', lat: 48.15, lon: -116.55, type: 'lake', state: 'ID', country: 'US' },
  { id: 'top-222', name: 'Coeur d\'Alene Lake', lat: 47.55, lon: -116.8, type: 'lake', state: 'ID', country: 'US' },
  { id: 'top-223', name: 'Priest Lake', lat: 48.5, lon: -116.9, type: 'lake', state: 'ID', country: 'US' },
  { id: 'top-224', name: 'C.J. Strike Reservoir', lat: 42.95, lon: -115.95, type: 'reservoir', state: 'ID', country: 'US' },
  { id: 'top-225', name: 'Lake Cascade', lat: 44.7, lon: -116.05, type: 'reservoir', state: 'ID', country: 'US' },
  { id: 'top-226', name: 'Canyon Ferry Lake', lat: 46.65, lon: -111.75, type: 'reservoir', state: 'MT', country: 'US' },
  { id: 'top-227', name: 'Hauser Lake', lat: 46.75, lon: -112.0, type: 'reservoir', state: 'MT', country: 'US' },
  { id: 'top-228', name: 'Holter Lake', lat: 46.85, lon: -112.0, type: 'reservoir', state: 'MT', country: 'US' },
  { id: 'top-229', name: 'Georgetown Lake', lat: 46.2, lon: -113.3, type: 'reservoir', state: 'MT', country: 'US' },
  { id: 'top-230', name: 'Lake Mary Ronan', lat: 47.85, lon: -114.05, type: 'lake', state: 'MT', country: 'US' },

  // ── More CO/UT/NM ──
  { id: 'top-231', name: 'Spinney Mountain Reservoir', lat: 38.95, lon: -105.7, type: 'reservoir', state: 'CO', country: 'US' },
  { id: 'top-232', name: 'Eleven Mile Reservoir', lat: 38.9, lon: -105.55, type: 'reservoir', state: 'CO', country: 'US' },
  { id: 'top-233', name: 'Strawberry Reservoir', lat: 40.15, lon: -111.15, type: 'reservoir', state: 'UT', country: 'US' },
  { id: 'top-234', name: 'Jordanelle Reservoir', lat: 40.6, lon: -111.45, type: 'reservoir', state: 'UT', country: 'US' },
  { id: 'top-235', name: 'Deer Creek Reservoir', lat: 40.4, lon: -111.5, type: 'reservoir', state: 'UT', country: 'US' },
  { id: 'top-236', name: 'Utah Lake', lat: 40.2, lon: -111.8, type: 'lake', state: 'UT', country: 'US' },
  { id: 'top-237', name: 'Bear Lake', lat: 42.0, lon: -111.3, type: 'lake', state: 'UT', country: 'US' },
  { id: 'top-238', name: 'Conchas Lake', lat: 35.4, lon: -104.2, type: 'reservoir', state: 'NM', country: 'US' },
  { id: 'top-239', name: 'Heron Lake', lat: 36.7, lon: -106.7, type: 'reservoir', state: 'NM', country: 'US' },

  // ── More Canada ──
  { id: 'top-240', name: 'Athabasca Lake', lat: 59.2, lon: -109.0, type: 'lake', state: 'SK', country: 'CA' },
  { id: 'top-241', name: 'Great Slave Lake', lat: 62.0, lon: -114.0, type: 'lake', state: 'NT', country: 'CA' },
  { id: 'top-242', name: 'Great Bear Lake', lat: 66.0, lon: -121.0, type: 'lake', state: 'NT', country: 'CA' },
  { id: 'top-243', name: 'Lake Nipigon', lat: 49.8, lon: -88.5, type: 'lake', state: 'ON', country: 'CA' },
  { id: 'top-244', name: 'Eagle Lake (ON)', lat: 49.7, lon: -93.3, type: 'lake', state: 'ON', country: 'CA' },
  { id: 'top-245', name: 'Lac Seul', lat: 50.3, lon: -92.2, type: 'lake', state: 'ON', country: 'CA' },
  { id: 'top-246', name: 'Lake St. Joseph', lat: 51.1, lon: -90.7, type: 'lake', state: 'ON', country: 'CA' },
  { id: 'top-247', name: 'Red Lake (ON)', lat: 51.0, lon: -93.8, type: 'lake', state: 'ON', country: 'CA' },
  { id: 'top-248', name: 'Lake Muskoka', lat: 45.0, lon: -79.45, type: 'lake', state: 'ON', country: 'CA' },
  { id: 'top-249', name: 'Lake Rosseau', lat: 45.15, lon: -79.55, type: 'lake', state: 'ON', country: 'CA' },
  { id: 'top-250', name: 'Lake Joseph', lat: 45.1, lon: -79.65, type: 'lake', state: 'ON', country: 'CA' },
];

/** Default score for a static spot with no ML data yet. */
const DEFAULT_SCORE = 55;

function typeLabel(t: StaticSpot['type']): string {
  return t.charAt(0).toUpperCase() + t.slice(1);
}

/**
 * Convert all static spots to FishingLocation objects.
 * Memoised at module level so the array is created once.
 */
function buildLocations(): FishingLocation[] {
  return RAW_SPOTS.map((s) => ({
    id: s.id,
    name: s.name,
    subtitle: `${typeLabel(s.type)} \u00B7 ${s.state}, ${s.country}`,
    lat: s.lat,
    lon: s.lon,
    score: DEFAULT_SCORE,
    scoreBreakdown: {
      catchProbability: 50,
      cpue: 50,
      conditions: 50,
      trophyPotential: 40,
    },
    conditions: {
      waterTemp: 0,
      airTemp: 0,
      weather: 'Unknown',
      weatherIcon: 'partly-cloudy' as any,
      windSpeed: 0,
      windDirection: '',
      pressure: 29.92,
      pressureTrend: 'steady',
      humidity: 50,
      moonPhase: '',
      solunarRating: 'fair',
      sunrise: '',
      sunset: '',
    },
    explanation: `${s.name} — popular fishing destination.`,
    forecast: [],
  }));
}

let _cached: FishingLocation[] | null = null;

/** Get the pre-bundled top fishing spots (lazy-built, then cached). */
export function getTopFishingSpots(): FishingLocation[] {
  if (!_cached) _cached = buildLocations();
  return _cached;
}

/** Check whether a location id is a static top-spot (so we know to replace it with real data). */
export function isStaticSpot(id: string): boolean {
  return id.startsWith('top-');
}

/** Number of pre-bundled spots. */
export const TOP_SPOTS_COUNT = RAW_SPOTS.length;
