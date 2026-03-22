# OpenCatch Design Guidelines

> Derived from *Designing Map Interfaces* (Michael Gaigg, Esri Press, 2023) and
> "An Approach to Decompose and Evaluate a Complex GIS-Application Design to a
> Simple, Lightweight, User-Centered App-Based Design" (Kammerhofer & Scholz, MDPI 2020).

---

## 1. Core Design Principles

### 1.1 Avoid the Kitchen Sink
The book's "Kitchen Sink" anti-pattern describes apps that cram every conceivable
function on one page. Each element added to the UI increases decision time
logarithmically (Hick's Law). OpenCatch currently shows 8+ floating buttons on
the map screen — this must be reduced to 3-4 essential controls.

**Rule:** If a feature is used by <20% of users on any given session, it must be
hidden behind a single tap (slide-out panel, bottom sheet, or layer picker).

### 1.2 Task-Oriented Workflows
Users come to OpenCatch with specific goals: "Where should I fish today?",
"What are conditions at my lake?", "Log my catch." The UI must funnel users
into clear task workflows rather than scattering tools across the screen.

**Rule:** Every screen should have ONE primary task. Secondary tasks should be
accessible but not competing for visual attention.

### 1.3 Progressive Disclosure
Show only what is needed at each step. Complexity is unlocked as users dig deeper.
This aligns with the MDPI paper's "micro-application" concept — each task should
feel like its own focused mini-app rather than a feature in a monolithic GIS.

**Levels:**
- L0 (Glance): Map + current location + nearby spots with scores
- L1 (Tap): Spot card with score, species, key conditions
- L2 (Expand): Full location detail with all data
- L3 (Tools): Specialized tools accessed from Tools tab or slide-out

### 1.4 Contextual Surfacing
Only show features relevant to the user's current context:
- **Coastal features** (tides, buoys, nautical charts, artificial reefs) only when
  user is near a coast
- **Ice fishing** only during winter months in northern latitudes
- **Boating tools** only when user has boating profile enabled or is near a marina
- **Recording tools** promoted when user is at a fishing spot

### 1.5 Data-Ink Ratio
Maximize the ratio of meaningful information to visual elements. The map should
be the star — minimize chrome, headers, and decorative elements that consume
pixels without communicating useful data.

**Rule:** Every pixel on the map screen must either show map data or provide
a control that >30% of users will use in that context.

---

## 2. Application to OpenCatch

### 2.1 Map Screen — The Heart of the App (Full Map Layout)
The map screen uses a "full map" layout pattern, meaning the map IS the app.
All other UI elements float on top and must be minimal.

**Current problems:**
- 8 floating buttons on the right edge (layers, ruler, wind, annotate, contour,
  radar, marina, access) — this is the Kitchen Sink
- All overlays always visible regardless of context
- Bottom sheet shows everything at once

**Fixes applied:**
1. **Reduce visible buttons to 3:** Layers, Locate Me, and the QuickActionFAB
2. **Move 5 secondary tools into a slide-out "Map Tools" drawer** that opens
   from the layers button or a dedicated tools button
3. **Contextual overlay toggles** appear only when relevant
4. **Compass** appears only when map is rotated (standard behavior)

### 2.2 Bottom Sheet — Info Pop-up / Info Panel Pattern
The book distinguishes between lightweight pop-ups and rich panels. On mobile,
the pop-up should be docked to the bottom with minimal content.

**Applied pattern:**
- PEEK state (100px): Shows "Top Picks" horizontal scroll — one glance
- COLLAPSED state (260px): Shows spot count + first few cards
- EXPANDED state (75%): Full list with area insights
- The "Top Picks" row IS the progressive disclosure hook — it shows the
  answer before the user even asks

### 2.3 Tools Screen — Micro-Application Decomposition
The MDPI paper recommends decomposing complex GIS into single-task focused
"micro-applications." Each tool in OpenCatch should feel like its own mini-app.

**Applied pattern:**
- Show "Favorites" / "Recently Used" section at top (max 4 items)
- Show "Recommended For You" based on context (season, location, time)
- Collapse remaining categories — expand on tap
- Each category shows 3-item preview with "See All" to expand

### 2.4 Overlays — Layer List + Theme Toggle Patterns
The book recommends layer lists for managing multiple data layers, with
scale-dependent visibility to prevent overload.

**Applied rules:**
- **Coastal overlays** (nautical, nav aids, no-wake, artificial reefs, buoys):
  Only appear in layer picker when user is within 50km of coast
- **Ice fishing**: Only in layer picker Nov-Mar for latitudes > 40N
- **Depth contours**: Only shown at zoom > 10
- **Scale dependency**: Hide overlays that aren't meaningful at current zoom

### 2.5 Onboarding — Landing Page + Empty State Patterns
The book emphasizes landing pages that collect location first, then funnel
into relevant content. The empty state pattern reminds us to never show a
meaningless default view.

**Applied pattern:**
- Onboarding collects location permission (already done)
- After onboarding, the map zooms to user's location (focal point pattern)
- First-time hints using coach marks for: search bar, quick action FAB,
  and bottom sheet
- Progressive feature unlock: show "New" badges on tools as user explores

---

## 3. Feature Organization Strategy

### 3.1 Visibility Tiers

| Tier | Visibility | Features |
|------|-----------|----------|
| Always | On map screen | Search, Locate Me, Layer toggle, QuickActionFAB |
| On Demand | One tap away | Map tools drawer (measure, annotate, contour, compass mode) |
| Contextual | Auto-shown when relevant | Wind (>15mph), Radar (precipitation nearby), Marina (near water) |
| In Tab | Tools screen | All 24 tools organized by task |
| Deep | Behind navigation | Settings, Profile, Track History |

### 3.2 Tools Screen Organization

```
[Recently Used]     (auto-tracked, max 4 quick-access tiles)
[Recommended]       (contextual: season + location + time of day)
─────────────────
Conditions          (Forecasts, Water Data, Best Times, ...)
Planning            (Regulations, Species Guide, Lake Finder, ...)
Recording           (Track Trip, Log Catch, My Stats, ...)
Boating             (Fuel Calc, Maintenance)  ← hide if no boat profile
Map Tools           (Offline Maps, Buoys, Species Map)
Seasonal            (Ice Fishing)  ← hide outside season
```

### 3.3 Map Button Hierarchy

```
RIGHT EDGE (top to bottom):
  [Layers]          ← Always visible, opens layer picker + map tools drawer
  [Locate Me]       ← Always visible

BOTTOM RIGHT:
  [QuickActionFAB]  ← Always visible, expands to: Log Catch, Record Trip,
                      Mark Spot, Check Conditions

LEFT EDGE:
  (nothing — keep clean for map interaction)

TOP:
  [Search Bar]      ← Always visible
  [Segment Toggle]  ← Spots / My Spots
```

---

## 4. Map Interface Patterns Reference

### From the Book — Patterns We Follow:

| Pattern | Chapter | How We Apply It |
|---------|---------|----------------|
| Landing Page | Ch.1 | Onboarding + first-launch zoom to location |
| Task Oriented | Ch.1 | Each tool is a focused workflow |
| Empty State | Ch.1 | Helpful messages when no spots found, no catches logged |
| Focal Point | Ch.1 | Blue dot + pulsating selected spot |
| Search | Ch.1 | Federated search across spots, species, locations |
| Location Finder | Ch.1 | Locate Me button + search bar |
| Full Map | Ch.2 | Map screen uses full-map layout |
| Marker | Ch.3 | Color-coded pins by forecast score |
| Rich Marker | Ch.3 | Score shown in pin |
| Info Pop-up | Ch.3 | Bottom-docked card on spot tap |
| Info Panel | Ch.3 | Full location detail screen |
| Attribute Filter | Ch.4 | Species, water type, score filters |
| Spatial Filter | Ch.4 | "Search this area" after pan |
| Timeline Slider | Ch.4 | Forecast timeline in location detail |
| Cluster Marker | Ch.4 | Cluster at low zoom levels |
| Locate Me | Ch.5 | Bottom-right button |
| Blue Dot | Ch.5 | User location indicator |
| Search This Area | Ch.5 | Re-fetch spots after significant pan |
| Offline Maps | Ch.5 | Download regions for offline use |
| Location List | Ch.6 | Bottom sheet spot list |
| Kitchen Sink | Ch.7 | **AVOID** — our previous 8-button layout |
| Desert Fog | Ch.7 | **AVOID** — always show location context |
| Data-Ink Ratio | Ch.7 | Minimal basemap, meaningful overlays only |

### From the Paper — Decomposition Principles:

| Principle | Application |
|-----------|------------|
| Single-task micro-apps | Each tool screen is self-contained |
| User stories drive features | "As an angler, I want to check conditions" |
| Reduced UI for focus | Hide tools not relevant to current task |
| App-based > monolithic | Tools tab acts as app launcher |

---

## 5. What to Show vs. Hide by Default

### Always Visible (Map Screen)
- Map with basemap
- Search bar
- Spots/My Spots segment toggle
- Layer toggle button (1 button, not 8)
- Locate Me button
- QuickAction FAB (collapsed)
- Bottom sheet (peek state with top picks)
- User location blue dot

### Hidden by Default (One Tap Away)
- Map tools drawer (measure, annotate, contour settings)
- All overlay toggles (accessed via layer picker)
- Wind/radar/marina/access toggles (moved into layer picker)
- Compass (appears on map rotation only)
- Speed/Course HUD (appears when moving at boating speed)

### Contextually Shown
- Fishing Time banner (when conditions are prime)
- Alert badges (when weather warnings exist)
- Track recording overlay (when actively recording)
- Anchor watch circle (when anchor alarm is set)
- "Search This Area" button (after significant pan)

### Never on Map Screen
- Settings, profile, stats navigation
- Full tool listings
- Long text content
- Non-map data tables

---

## 6. Visual Design Tokens

Following the established OpenCatch style:
- **Headers:** Playfair Display (serif) for screen titles
- **Body:** System sans-serif for all other text
- **Background:** Soft off-white (`palette.background`)
- **Surface:** White cards with subtle shadow
- **Accent:** `palette.accent` for primary actions
- **Touch targets:** Minimum 44x44px per Apple HIG
- **Map controls:** 40x40px circular buttons with surface background
- **Border radius:** 12-14px for cards, 20px for circular buttons
- **Shadows:** Subtle — opacity 0.06-0.12, blur 6-12px

---

*Last updated: 2026-03-21*
