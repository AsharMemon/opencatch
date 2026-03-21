/**
 * OpenCatch — Knot & Rig Guide Screen
 *
 * Reference guide for essential fishing knots and popular rigs.
 * - 15 knots with use cases, strength ratings, difficulty levels, step-by-step instructions
 * - 8 rigs with descriptions, best species, seasonal usage
 * - Searchable and filterable by category/difficulty
 */

import React, { useCallback, useMemo, useState } from 'react';
import {
  FlatList,
  LayoutAnimation,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  UIManager,
  View,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { palette } from '../theme/palette';
import { type as typeStyles, fonts } from '../theme/typography';
import { hapticLight } from '../utils/haptics';

// Enable LayoutAnimation on Android
if (Platform.OS === 'android' && UIManager.setLayoutAnimationEnabledExperimental) {
  UIManager.setLayoutAnimationEnabledExperimental(true);
}

// ── Types ────────────────────────────────────────────────────────────────────

type KnotDifficulty = 'Easy' | 'Medium' | 'Hard';
type KnotCategory = 'terminal' | 'line-to-line' | 'loop' | 'snell';

interface KnotEntry {
  id: string;
  name: string;
  category: KnotCategory;
  useCase: string;
  strengthPercent: number;
  difficulty: KnotDifficulty;
  steps: string[];
  tip?: string;
}

interface RigEntry {
  id: string;
  name: string;
  description: string;
  bestSpecies: string[];
  seasonalUsage: string;
  components: string[];
  technique: string;
}

type TabKey = 'knots' | 'rigs';

// ── Knot Data ────────────────────────────────────────────────────────────────

const KNOTS: KnotEntry[] = [
  {
    id: 'palomar',
    name: 'Palomar Knot',
    category: 'terminal',
    useCase: 'Hook or swivel attachment. Works with braided and mono line.',
    strengthPercent: 95,
    difficulty: 'Easy',
    steps: [
      'Double about 6 inches of line and pass the loop through the hook eye.',
      'Tie a simple overhand knot with the doubled line, letting the hook hang loose.',
      'Pass the loop over the hook entirely.',
      'Pull both the standing line and tag end to tighten. Trim the tag end.',
    ],
    tip: 'Keep the loop large enough to pass over the hook easily.',
  },
  {
    id: 'improved-clinch',
    name: 'Improved Clinch Knot',
    category: 'terminal',
    useCase: 'Universal hook/lure connection. Best with mono under 20lb test.',
    strengthPercent: 89,
    difficulty: 'Easy',
    steps: [
      'Thread the line through the hook eye, leaving 6 inches of tag end.',
      'Wrap the tag end around the standing line 5-7 times.',
      'Thread the tag end through the small loop near the eye.',
      'Then pass the tag end through the large loop you just created.',
      'Moisten the knot and pull the tag end and standing line to tighten. Trim.',
    ],
  },
  {
    id: 'uni-knot',
    name: 'Uni Knot',
    category: 'terminal',
    useCase: 'Versatile terminal knot. Works for hooks, swivels, and can join two lines (double uni).',
    strengthPercent: 90,
    difficulty: 'Easy',
    steps: [
      'Pass the line through the hook eye and fold it back parallel to the standing line.',
      'Form a loop by laying the tag end over the doubled line.',
      'Make 5-6 wraps through the loop and around the doubled line.',
      'Moisten and pull the tag end to tighten the wraps.',
      'Slide the knot down to the eye by pulling the standing line. Trim.',
    ],
  },
  {
    id: 'blood-knot',
    name: 'Blood Knot',
    category: 'line-to-line',
    useCase: 'Joining two similar-diameter lines. Popular for fly fishing leaders.',
    strengthPercent: 83,
    difficulty: 'Medium',
    steps: [
      'Overlap the ends of the two lines by about 6 inches.',
      'Wrap one tag end around the other line 5 times, then bring it back through the center gap.',
      'Repeat with the other tag end, wrapping in the opposite direction.',
      'Pass the second tag end through the center gap in the opposite direction of the first.',
      'Moisten, pull both standing lines slowly to tighten. Trim both tag ends.',
    ],
    tip: 'Both lines should be similar in diameter for best results.',
  },
  {
    id: 'double-uni',
    name: 'Double Uni Knot',
    category: 'line-to-line',
    useCase: 'Joining braid to fluorocarbon/mono leader. Strong and reliable.',
    strengthPercent: 90,
    difficulty: 'Medium',
    steps: [
      'Overlap the two lines by about 8 inches.',
      'With the first line, form a loop and wrap the tag end through and around both lines 4-6 times.',
      'Pull to tighten the first uni knot.',
      'Repeat with the second line, wrapping in the opposite direction.',
      'Moisten both knots and pull the standing lines to slide the knots together. Trim.',
    ],
  },
  {
    id: 'fg-knot',
    name: 'FG Knot',
    category: 'line-to-line',
    useCase: 'Slimmest braid-to-leader connection. Passes through guides easily.',
    strengthPercent: 98,
    difficulty: 'Hard',
    steps: [
      'Tension the braid between your hands or use a tool to keep it taut.',
      'Lay the leader across the braid at a right angle.',
      'Alternately wrap the braid over and under the leader, creating a woven pattern. Do 15-20 wraps.',
      'Pinch the weave tightly and make 2-3 half hitches with the braid tag end around the leader.',
      'Continue with 3-4 more half hitches, alternating sides, moving away from the weave.',
      'Trim the leader tag flush. Trim the braid tag end.',
    ],
    tip: 'Practice with thick line first. Maintaining tension is the key to a clean FG knot.',
  },
  {
    id: 'surgeons-knot',
    name: "Surgeon's Knot",
    category: 'line-to-line',
    useCase: 'Quick line-to-line join. Easier than blood knot, works with different diameters.',
    strengthPercent: 85,
    difficulty: 'Easy',
    steps: [
      'Overlap the two lines by 6-8 inches.',
      'Treat both lines as one and form a simple overhand knot.',
      'Pass the lines through the loop a second time (double overhand).',
      'Moisten and pull all four ends to tighten evenly. Trim.',
    ],
  },
  {
    id: 'loop-knot',
    name: 'Non-Slip Loop Knot',
    category: 'loop',
    useCase: 'Creates a fixed loop for free lure movement. Great for jigs and swimbaits.',
    strengthPercent: 85,
    difficulty: 'Medium',
    steps: [
      'Tie a loose overhand knot in the line about 6 inches from the end.',
      'Pass the tag end through the hook eye.',
      'Thread the tag end back through the overhand knot, entering from the same side it exited.',
      'Wrap the tag end around the standing line 3-5 times.',
      'Pass the tag end back through the overhand knot from the same direction.',
      'Moisten and pull the tag end to seat the wraps, then pull the standing line to tighten the loop. Trim.',
    ],
    tip: 'Fewer wraps for heavier line. 5 wraps for 8lb, 3 wraps for 40lb+.',
  },
  {
    id: 'perfection-loop',
    name: 'Perfection Loop',
    category: 'loop',
    useCase: 'Creates a perfectly aligned loop at the end of a leader. Essential for fly fishing.',
    strengthPercent: 82,
    difficulty: 'Medium',
    steps: [
      'Form a loop behind the standing line (loop A).',
      'Make a second, smaller loop in front of loop A (loop B).',
      'Pass the tag end between loops A and B.',
      'Pull loop B through loop A.',
      'Moisten and tighten by pulling the loop and standing line in opposite directions. Trim.',
    ],
  },
  {
    id: 'dropper-loop',
    name: 'Dropper Loop',
    category: 'loop',
    useCase: 'Creates a loop mid-line for attaching a dropper fly or hook.',
    strengthPercent: 78,
    difficulty: 'Medium',
    steps: [
      'Form a loop in the line where you want the dropper.',
      'Wrap one side of the loop around the other side 5-6 times.',
      'Find the center of the wraps and push the original loop through.',
      'Hold the pushed-through loop and pull both ends of the standing line to tighten. Adjust loop size.',
    ],
  },
  {
    id: 'snell-knot',
    name: 'Snell Knot',
    category: 'snell',
    useCase: 'Attaches line to a hook shank for inline pull. Maximizes hook-set power.',
    strengthPercent: 96,
    difficulty: 'Medium',
    steps: [
      'Thread the line through the hook eye and run about 8 inches along the shank.',
      'Form a loop below the hook shank.',
      'Wrap the tag end tightly around the shank and the standing line 6-8 times, working toward the eye.',
      'Hold the wraps and pull the standing line to tighten. The wraps should be snug and neat.',
      'Trim the tag end.',
    ],
    tip: 'The snell knot gives a more direct pull for better hooksets with circle hooks.',
  },
  {
    id: 'trilene-knot',
    name: 'Trilene Knot',
    category: 'terminal',
    useCase: 'Double-pass version of the clinch knot. Extra strong for slippery lines.',
    strengthPercent: 92,
    difficulty: 'Easy',
    steps: [
      'Thread the line through the hook eye twice, creating a small double loop at the eye.',
      'Wrap the tag end around the standing line 5 times.',
      'Thread the tag end through the double loop at the eye.',
      'Moisten and pull to tighten. Trim.',
    ],
  },
  {
    id: 'alberto-knot',
    name: 'Alberto Knot',
    category: 'line-to-line',
    useCase: 'Compact braid-to-leader knot. Good alternative to FG knot.',
    strengthPercent: 92,
    difficulty: 'Medium',
    steps: [
      'Double the leader line to form a loop about 4 inches long.',
      'Pass the braid through the leader loop.',
      'Wrap the braid around the doubled leader 7 times going away from the loop end.',
      'Then wrap back 7 times over the original wraps toward the loop.',
      'Pass the braid tag through the leader loop from the same side it entered.',
      'Moisten and slowly pull the braid standing line and leader to tighten. Trim both tags.',
    ],
  },
  {
    id: 'san-diego-jam',
    name: 'San Diego Jam Knot',
    category: 'terminal',
    useCase: 'Strong terminal knot for fluorocarbon. Popular with West Coast anglers.',
    strengthPercent: 94,
    difficulty: 'Medium',
    steps: [
      'Thread the line through the hook eye, leaving about 10 inches of tag end.',
      'Bring the tag end back and lay it along the standing line.',
      'Wrap the tag end around both the standing line and the tag section 7 times, working away from the hook.',
      'Pass the tag end through the strand closest to the eye.',
      'Then pass the tag end through the loop formed between the wraps and the eye.',
      'Moisten and pull the standing line to tighten. Trim.',
    ],
  },
  {
    id: 'arbor-knot',
    name: 'Arbor Knot',
    category: 'terminal',
    useCase: 'Attaching line to a reel spool. Essential when spooling new line.',
    strengthPercent: 60,
    difficulty: 'Easy',
    steps: [
      'Wrap the line around the reel arbor (spool).',
      'Tie an overhand knot around the standing line with the tag end.',
      'Tie a second overhand knot in the tag end alone (acts as a stopper).',
      'Pull the standing line to slide the first knot down to the arbor. The stopper knot prevents slipping.',
      'Trim the tag end close to the stopper knot.',
    ],
  },
];

// ── Rig Data ─────────────────────────────────────────────────────────────────

const RIGS: RigEntry[] = [
  {
    id: 'texas-rig',
    name: 'Texas Rig',
    description: 'Weedless soft plastic presentation. The most versatile bass rig for heavy cover. The bullet weight slides on the line ahead of an offset worm hook buried in the soft plastic.',
    bestSpecies: ['Largemouth Bass', 'Smallmouth Bass'],
    seasonalUsage: 'Year-round, especially effective spring through fall',
    components: ['Bullet weight (1/8 - 1/2 oz)', 'Offset worm hook (3/0 - 5/0)', 'Soft plastic bait', 'Optional: bobber stop for pegged weight'],
    technique: 'Cast into cover, let the bait sink, then hop or drag along the bottom. The weedless design lets you fish heavy vegetation, brush piles, and docks without snagging.',
  },
  {
    id: 'carolina-rig',
    name: 'Carolina Rig',
    description: 'Bottom-contact rig with a free-floating bait trailing behind a weight. Covers water quickly and keeps the bait hovering just off bottom.',
    bestSpecies: ['Largemouth Bass', 'Smallmouth Bass', 'Walleye'],
    seasonalUsage: 'Best in summer and fall when fish are deep or scattered',
    components: ['Egg or bullet weight (1/2 - 1 oz)', 'Bead', 'Barrel swivel', '18-36 inch fluorocarbon leader', 'Offset hook', 'Soft plastic'],
    technique: 'Drag slowly along the bottom. The weight kicks up sediment and makes noise against the bead, attracting fish. The bait floats naturally behind on the leader.',
  },
  {
    id: 'drop-shot',
    name: 'Drop Shot Rig',
    description: 'Finesse vertical presentation with the bait suspended above the bottom. Extremely effective for pressured fish in clear water.',
    bestSpecies: ['Smallmouth Bass', 'Largemouth Bass', 'Perch', 'Walleye'],
    seasonalUsage: 'Year-round, especially effective in cold water or clear conditions',
    components: ['Drop shot weight (1/8 - 3/8 oz)', 'Small finesse hook (#1 - 1/0)', 'Small soft plastic (3-4 inch)', 'Light fluorocarbon line (6-8 lb)'],
    technique: 'Lower to the bottom, then shake the rod tip gently to impart subtle action to the bait while keeping it in the strike zone. Extremely effective when fish are inactive.',
  },
  {
    id: 'ned-rig',
    name: 'Ned Rig',
    description: 'Simple, ultra-finesse presentation that catches everything that swims. A small mushroom head jig paired with a trimmed soft plastic.',
    bestSpecies: ['Smallmouth Bass', 'Largemouth Bass', 'Crappie', 'Panfish'],
    seasonalUsage: 'Year-round, exceptional in cold water and tough conditions',
    components: ['Mushroom head jig (1/16 - 1/4 oz)', 'Small soft plastic stick bait (2.5-3 inch, cut down)', 'Light spinning tackle'],
    technique: 'Cast and let it sink to the bottom. Slowly drag or hop it. The buoyant plastic makes the bait stand up on bottom, presenting an easy target. Dead-sticking works too.',
  },
  {
    id: 'wacky-rig',
    name: 'Wacky Rig',
    description: 'A straight worm hooked through the middle for a natural fluttering fall. One of the best ways to catch bass in shallow water.',
    bestSpecies: ['Largemouth Bass', 'Smallmouth Bass'],
    seasonalUsage: 'Spring and summer, especially around spawning beds',
    components: ['Wide-gap or wacky-specific hook (#1 - 1/0)', 'Senko-style stick worm (5 inch)', 'Optional: O-ring to save baits', 'Optional: small nail weight for faster sink'],
    technique: 'Cast near cover and let it sink on a slack line. The worm undulates naturally as it falls. Most strikes come on the fall. Twitch occasionally and let it sink again.',
  },
  {
    id: 'neko-rig',
    name: 'Neko Rig',
    description: 'A weighted wacky rig that sinks headfirst and stands up on bottom. Combines the action of wacky and drop shot presentations.',
    bestSpecies: ['Largemouth Bass', 'Smallmouth Bass'],
    seasonalUsage: 'Spring through fall, great for spotted bass on deep structure',
    components: ['Wacky hook or Neko hook with weedguard', 'Stick worm (5-6 inch)', 'Nail weight (1/32 - 1/8 oz, inserted in worm nose)', 'O-ring (optional)'],
    technique: 'Insert a nail weight in one end of a stick worm. Hook wacky-style through the middle. The weighted end sinks first and the bait stands up off bottom, wobbling enticingly.',
  },
  {
    id: 'slip-bobber',
    name: 'Slip Bobber Rig',
    description: 'Adjustable-depth float rig for precise depth control. Excellent for suspended fish or presenting live bait at exact depths.',
    bestSpecies: ['Walleye', 'Crappie', 'Panfish', 'Trout', 'Catfish'],
    seasonalUsage: 'Year-round, especially for ice-out and summer suspended fish',
    components: ['Slip bobber', 'Bobber stop and bead', 'Split shot or small jig', 'Small hook (#4 - #1)', 'Live bait (minnow, leech, or worm)'],
    technique: 'Set the bobber stop at your desired depth. The bobber slides freely during casting for long distances, then seats at the stop depth. Watch for the bobber to go under or tip sideways.',
  },
  {
    id: 'bottom-rig',
    name: 'Bottom / Catfish Rig',
    description: 'Simple bottom fishing rig with a sinker holding the bait on or near the bottom. The go-to setup for catfish and many saltwater species.',
    bestSpecies: ['Catfish', 'Carp', 'Drum'],
    seasonalUsage: 'Spring through fall, best during warm water periods',
    components: ['Egg sinker (1/2 - 2 oz)', 'Bead', 'Barrel swivel', '12-24 inch leader', 'Circle hook (2/0 - 6/0)', 'Cut bait, chicken liver, or prepared bait'],
    technique: 'Cast to a likely area (channel edges, deep holes, current breaks). Let the rig rest on the bottom. The sinker holds position while the bait scent disperses. Use a circle hook for self-setting catches.',
  },
];

// ── Category helpers ─────────────────────────────────────────────────────────

const CATEGORY_LABELS: Record<KnotCategory, string> = {
  terminal: 'Terminal Tackle',
  'line-to-line': 'Line-to-Line',
  loop: 'Loop Knots',
  snell: 'Snell',
};

const DIFFICULTY_COLORS: Record<KnotDifficulty, string> = {
  Easy: palette.success,
  Medium: palette.warning,
  Hard: palette.error,
};

// ── Expandable Knot Card ─────────────────────────────────────────────────────

function KnotCard({ knot }: { knot: KnotEntry }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <Pressable
      style={knotStyles.card}
      onPress={() => {
        LayoutAnimation.configureNext(LayoutAnimation.Presets.easeInEaseOut);
        hapticLight();
        setExpanded(!expanded);
      }}
    >
      <View style={knotStyles.header}>
        <View style={{ flex: 1 }}>
          <Text style={knotStyles.name}>{knot.name}</Text>
          <Text style={knotStyles.useCase} numberOfLines={expanded ? undefined : 1}>{knot.useCase}</Text>
        </View>
        <View style={knotStyles.badges}>
          <View style={[knotStyles.diffBadge, { backgroundColor: DIFFICULTY_COLORS[knot.difficulty] + '18' }]}>
            <Text style={[knotStyles.diffText, { color: DIFFICULTY_COLORS[knot.difficulty] }]}>{knot.difficulty}</Text>
          </View>
          <Ionicons name={expanded ? 'chevron-up' : 'chevron-down'} size={16} color={palette.textMuted} />
        </View>
      </View>

      {/* Strength bar */}
      <View style={knotStyles.strengthRow}>
        <Text style={knotStyles.strengthLabel}>Strength</Text>
        <View style={knotStyles.strengthBar}>
          <View style={[knotStyles.strengthFill, { width: `${knot.strengthPercent}%` }]} />
        </View>
        <Text style={knotStyles.strengthValue}>{knot.strengthPercent}%</Text>
      </View>

      {/* Category */}
      <View style={knotStyles.catRow}>
        <View style={knotStyles.catBadge}>
          <Text style={knotStyles.catText}>{CATEGORY_LABELS[knot.category]}</Text>
        </View>
      </View>

      {/* Expanded: step-by-step */}
      {expanded && (
        <View style={knotStyles.stepsContainer}>
          <Text style={knotStyles.stepsTitle}>Step-by-Step</Text>
          {knot.steps.map((step, idx) => (
            <View key={idx} style={knotStyles.stepRow}>
              <View style={knotStyles.stepNum}>
                <Text style={knotStyles.stepNumText}>{idx + 1}</Text>
              </View>
              <Text style={knotStyles.stepText}>{step}</Text>
            </View>
          ))}
          {knot.tip && (
            <View style={knotStyles.tipBox}>
              <Ionicons name="bulb" size={14} color={palette.warning} />
              <Text style={knotStyles.tipText}>{knot.tip}</Text>
            </View>
          )}
        </View>
      )}
    </Pressable>
  );
}

const knotStyles = StyleSheet.create({
  card: {
    backgroundColor: palette.surface,
    borderRadius: 14,
    padding: 14,
    marginBottom: 10,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.border,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 8,
  },
  name: {
    fontSize: 16,
    fontWeight: '700',
    color: palette.text,
  },
  useCase: {
    fontSize: 13,
    color: palette.textMuted,
    marginTop: 2,
  },
  badges: {
    alignItems: 'flex-end',
    gap: 6,
  },
  diffBadge: {
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 6,
  },
  diffText: {
    fontSize: 11,
    fontWeight: '700',
  },
  strengthRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginTop: 10,
  },
  strengthLabel: {
    fontSize: 11,
    color: palette.textMuted,
    width: 52,
  },
  strengthBar: {
    flex: 1,
    height: 6,
    borderRadius: 3,
    backgroundColor: palette.surfaceRaised,
    overflow: 'hidden',
  },
  strengthFill: {
    height: '100%',
    borderRadius: 3,
    backgroundColor: palette.accent,
  },
  strengthValue: {
    fontSize: 12,
    fontWeight: '700',
    color: palette.accent,
    width: 36,
    textAlign: 'right',
  },
  catRow: {
    flexDirection: 'row',
    marginTop: 8,
  },
  catBadge: {
    backgroundColor: palette.surfaceRaised,
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 6,
  },
  catText: {
    fontSize: 11,
    fontWeight: '500',
    color: palette.textSecondary,
  },
  stepsContainer: {
    marginTop: 14,
    paddingTop: 12,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: palette.border,
    gap: 10,
  },
  stepsTitle: {
    fontSize: 14,
    fontWeight: '700',
    color: palette.text,
    marginBottom: 2,
  },
  stepRow: {
    flexDirection: 'row',
    gap: 10,
  },
  stepNum: {
    width: 22,
    height: 22,
    borderRadius: 11,
    backgroundColor: palette.accentLight,
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: 1,
  },
  stepNumText: {
    fontSize: 11,
    fontWeight: '700',
    color: palette.accent,
  },
  stepText: {
    flex: 1,
    fontSize: 14,
    lineHeight: 20,
    color: palette.textSecondary,
  },
  tipBox: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 8,
    backgroundColor: palette.warning + '10',
    padding: 10,
    borderRadius: 10,
    marginTop: 4,
  },
  tipText: {
    flex: 1,
    fontSize: 13,
    color: palette.textSecondary,
    lineHeight: 18,
  },
});

// ── Rig Card ─────────────────────────────────────────────────────────────────

function RigCard({ rig }: { rig: RigEntry }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <Pressable
      style={rigStyles.card}
      onPress={() => {
        LayoutAnimation.configureNext(LayoutAnimation.Presets.easeInEaseOut);
        hapticLight();
        setExpanded(!expanded);
      }}
    >
      <View style={rigStyles.header}>
        <View style={{ flex: 1 }}>
          <Text style={rigStyles.name}>{rig.name}</Text>
          <Text style={rigStyles.desc} numberOfLines={expanded ? undefined : 2}>{rig.description}</Text>
        </View>
        <Ionicons name={expanded ? 'chevron-up' : 'chevron-down'} size={16} color={palette.textMuted} />
      </View>

      {/* Species badges */}
      <View style={rigStyles.speciesRow}>
        {rig.bestSpecies.map((sp) => (
          <View key={sp} style={rigStyles.speciesBadge}>
            <Ionicons name="fish" size={11} color={palette.accent} />
            <Text style={rigStyles.speciesText}>{sp}</Text>
          </View>
        ))}
      </View>

      <Text style={rigStyles.season}>
        <Ionicons name="calendar-outline" size={12} color={palette.textMuted} /> {rig.seasonalUsage}
      </Text>

      {/* Expanded details */}
      {expanded && (
        <View style={rigStyles.details}>
          <Text style={rigStyles.detailTitle}>Components</Text>
          {rig.components.map((c, i) => (
            <View key={i} style={rigStyles.componentRow}>
              <View style={rigStyles.bullet} />
              <Text style={rigStyles.componentText}>{c}</Text>
            </View>
          ))}

          <Text style={[rigStyles.detailTitle, { marginTop: 12 }]}>How to Fish It</Text>
          <Text style={rigStyles.techniqueText}>{rig.technique}</Text>
        </View>
      )}
    </Pressable>
  );
}

const rigStyles = StyleSheet.create({
  card: {
    backgroundColor: palette.surface,
    borderRadius: 14,
    padding: 14,
    marginBottom: 10,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.border,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 8,
  },
  name: {
    fontSize: 16,
    fontWeight: '700',
    color: palette.text,
  },
  desc: {
    fontSize: 13,
    color: palette.textMuted,
    marginTop: 2,
    lineHeight: 18,
  },
  speciesRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 6,
    marginTop: 10,
  },
  speciesBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    backgroundColor: palette.accentDim,
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 6,
  },
  speciesText: {
    fontSize: 11,
    fontWeight: '600',
    color: palette.accent,
  },
  season: {
    fontSize: 12,
    color: palette.textMuted,
    marginTop: 8,
  },
  details: {
    marginTop: 14,
    paddingTop: 12,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: palette.border,
  },
  detailTitle: {
    fontSize: 14,
    fontWeight: '700',
    color: palette.text,
    marginBottom: 6,
  },
  componentRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 8,
    marginBottom: 4,
  },
  bullet: {
    width: 5,
    height: 5,
    borderRadius: 2.5,
    backgroundColor: palette.accent,
    marginTop: 7,
  },
  componentText: {
    flex: 1,
    fontSize: 13,
    color: palette.textSecondary,
    lineHeight: 18,
  },
  techniqueText: {
    fontSize: 14,
    lineHeight: 20,
    color: palette.textSecondary,
  },
});

// ── Main Screen ──────────────────────────────────────────────────────────────

export function KnotGuideScreen() {
  const [activeTab, setActiveTab] = useState<TabKey>('knots');
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedDifficulty, setSelectedDifficulty] = useState<KnotDifficulty | null>(null);
  const [selectedCategory, setSelectedCategory] = useState<KnotCategory | null>(null);

  const filteredKnots = useMemo(() => {
    let results = KNOTS;
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      results = results.filter((k) =>
        k.name.toLowerCase().includes(q) ||
        k.useCase.toLowerCase().includes(q) ||
        CATEGORY_LABELS[k.category].toLowerCase().includes(q),
      );
    }
    if (selectedDifficulty) {
      results = results.filter((k) => k.difficulty === selectedDifficulty);
    }
    if (selectedCategory) {
      results = results.filter((k) => k.category === selectedCategory);
    }
    return results;
  }, [searchQuery, selectedDifficulty, selectedCategory]);

  const filteredRigs = useMemo(() => {
    if (!searchQuery.trim()) return RIGS;
    const q = searchQuery.toLowerCase();
    return RIGS.filter((r) =>
      r.name.toLowerCase().includes(q) ||
      r.description.toLowerCase().includes(q) ||
      r.bestSpecies.some((sp) => sp.toLowerCase().includes(q)),
    );
  }, [searchQuery]);

  return (
    <View style={styles.container}>
      {/* Search */}
      <View style={styles.searchBar}>
        <Ionicons name="search" size={18} color={palette.textMuted} />
        <TextInput
          style={styles.searchInput}
          placeholder={activeTab === 'knots' ? 'Search knots...' : 'Search rigs...'}
          placeholderTextColor={palette.textDim}
          value={searchQuery}
          onChangeText={setSearchQuery}
        />
        {searchQuery.length > 0 && (
          <Pressable onPress={() => setSearchQuery('')}>
            <Ionicons name="close-circle" size={18} color={palette.textMuted} />
          </Pressable>
        )}
      </View>

      {/* Tabs */}
      <View style={styles.tabRow}>
        <Pressable
          style={[styles.tab, activeTab === 'knots' && styles.tabActive]}
          onPress={() => { hapticLight(); setActiveTab('knots'); }}
        >
          <Ionicons name="link" size={16} color={activeTab === 'knots' ? palette.accent : palette.textMuted} />
          <Text style={[styles.tabText, activeTab === 'knots' && styles.tabTextActive]}>
            Knots ({filteredKnots.length})
          </Text>
        </Pressable>
        <Pressable
          style={[styles.tab, activeTab === 'rigs' && styles.tabActive]}
          onPress={() => { hapticLight(); setActiveTab('rigs'); }}
        >
          <Ionicons name="git-branch" size={16} color={activeTab === 'rigs' ? palette.accent : palette.textMuted} />
          <Text style={[styles.tabText, activeTab === 'rigs' && styles.tabTextActive]}>
            Rigs ({filteredRigs.length})
          </Text>
        </Pressable>
      </View>

      {/* Knot filters */}
      {activeTab === 'knots' && (
        <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.filterScroll} contentContainerStyle={styles.filterScrollContent}>
          {/* Difficulty */}
          {(['Easy', 'Medium', 'Hard'] as KnotDifficulty[]).map((d) => (
            <Pressable
              key={d}
              style={[styles.filterChip, selectedDifficulty === d && { backgroundColor: DIFFICULTY_COLORS[d] + '18', borderColor: DIFFICULTY_COLORS[d] }]}
              onPress={() => { hapticLight(); setSelectedDifficulty(selectedDifficulty === d ? null : d); }}
            >
              <Text style={[styles.filterChipText, selectedDifficulty === d && { color: DIFFICULTY_COLORS[d] }]}>{d}</Text>
            </Pressable>
          ))}
          <View style={styles.filterDivider} />
          {/* Category */}
          {(Object.entries(CATEGORY_LABELS) as [KnotCategory, string][]).map(([cat, label]) => (
            <Pressable
              key={cat}
              style={[styles.filterChip, selectedCategory === cat && styles.filterChipActive]}
              onPress={() => { hapticLight(); setSelectedCategory(selectedCategory === cat ? null : cat); }}
            >
              <Text style={[styles.filterChipText, selectedCategory === cat && styles.filterChipTextActive]}>{label}</Text>
            </Pressable>
          ))}
        </ScrollView>
      )}

      {/* Content */}
      <ScrollView
        style={styles.scrollArea}
        contentContainerStyle={styles.scrollContent}
        showsVerticalScrollIndicator={false}
      >
        {activeTab === 'knots' ? (
          filteredKnots.length === 0 ? (
            <View style={styles.emptyState}>
              <Ionicons name="link-outline" size={40} color={palette.textDim} />
              <Text style={styles.emptyText}>No knots match your search</Text>
            </View>
          ) : (
            filteredKnots.map((knot) => <KnotCard key={knot.id} knot={knot} />)
          )
        ) : (
          filteredRigs.length === 0 ? (
            <View style={styles.emptyState}>
              <Ionicons name="git-branch-outline" size={40} color={palette.textDim} />
              <Text style={styles.emptyText}>No rigs match your search</Text>
            </View>
          ) : (
            filteredRigs.map((rig) => <RigCard key={rig.id} rig={rig} />)
          )
        )}
        <View style={{ height: 40 }} />
      </ScrollView>
    </View>
  );
}

// ── Styles ───────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: palette.background,
  },

  // Search
  searchBar: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: palette.surface,
    borderRadius: 12,
    paddingHorizontal: 12,
    height: 42,
    marginHorizontal: 16,
    marginTop: 8,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.border,
    gap: 8,
  },
  searchInput: {
    flex: 1,
    fontSize: 15,
    color: palette.text,
    paddingVertical: 0,
  },

  // Tabs
  tabRow: {
    flexDirection: 'row',
    marginHorizontal: 16,
    marginTop: 12,
    gap: 8,
  },
  tab: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    paddingVertical: 10,
    borderRadius: 10,
    backgroundColor: palette.surfaceRaised,
  },
  tabActive: {
    backgroundColor: palette.accentLight,
  },
  tabText: {
    fontSize: 14,
    fontWeight: '600',
    color: palette.textMuted,
  },
  tabTextActive: {
    color: palette.accent,
  },

  // Knot filters
  filterScroll: {
    maxHeight: 44,
    marginTop: 10,
  },
  filterScrollContent: {
    paddingHorizontal: 16,
    gap: 8,
    alignItems: 'center',
  },
  filterChip: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 8,
    backgroundColor: palette.surfaceRaised,
    borderWidth: 1,
    borderColor: 'transparent',
  },
  filterChipActive: {
    backgroundColor: palette.accentLight,
    borderColor: palette.accent,
  },
  filterChipText: {
    fontSize: 12,
    fontWeight: '600',
    color: palette.textMuted,
  },
  filterChipTextActive: {
    color: palette.accent,
  },
  filterDivider: {
    width: 1,
    height: 20,
    backgroundColor: palette.border,
  },

  // Content
  scrollArea: {
    flex: 1,
  },
  scrollContent: {
    padding: 16,
    paddingTop: 10,
  },

  // Empty
  emptyState: {
    alignItems: 'center',
    paddingTop: 60,
    gap: 8,
  },
  emptyText: {
    fontSize: 14,
    color: palette.textMuted,
  },
});
