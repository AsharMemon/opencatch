import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { palette } from '../theme/palette';

type ContourQuality = 'survey' | 'high' | 'moderate' | 'coarse' | 'estimate';

interface DataSourceBadgeProps {
  source: string;
  rmseM: number;
  confidence: number;
  contourQuality: ContourQuality;
  attribution?: string;
  compact?: boolean;
}

const QUALITY_CONFIG: Record<
  ContourQuality,
  { icon: string; label: string; color: string; description: string }
> = {
  survey: {
    icon: 'volume-high',
    label: 'Sonar Survey',
    color: '#4CAF50',
    description: 'Sub-meter accuracy',
  },
  high: {
    icon: 'satellite',
    label: 'Satellite Model',
    color: '#8BC34A',
    description: 'Good accuracy',
  },
  moderate: {
    icon: 'analytics',
    label: 'Satellite Estimate',
    color: '#FFC107',
    description: 'Moderate accuracy',
  },
  coarse: {
    icon: 'shapes',
    label: 'Basin Estimate',
    color: '#FF9800',
    description: 'Approximate depth',
  },
  estimate: {
    icon: 'help-circle',
    label: 'Estimated',
    color: '#F44336',
    description: 'Low confidence',
  },
};

const SOURCE_LABELS: Record<string, string> = {
  mn_dnr_survey: 'MN DNR Survey',
  wi_dnr_survey: 'WI DNR Survey',
  mi_dnr_survey: 'MI DNR Survey',
  cudem: 'NOAA Chart',
  gebco: 'Global Ocean Chart',
  emodnet: 'European Chart',
  ml_tier1: 'Satellite Model',
  ml_tier2: 'Satellite Estimate',
  ml_tier3: 'Basin Estimate',
  nhdplus: 'River Estimate',
  morphometric: 'Shape Estimate',
  '3d_lakes': 'Global Lake Model',
};

function formatAccuracy(rmseM: number): string {
  if (rmseM < 1) return `\u00b1${(rmseM * 3.281).toFixed(1)}ft`;
  return `\u00b1${rmseM.toFixed(1)}m`;
}

export function DataSourceBadge({
  source,
  rmseM,
  confidence,
  contourQuality,
  attribution,
  compact = false,
}: DataSourceBadgeProps) {
  const config = QUALITY_CONFIG[contourQuality] || QUALITY_CONFIG.estimate;
  const sourceLabel = SOURCE_LABELS[source] || source;

  if (compact) {
    return (
      <View style={[styles.compactBadge, { backgroundColor: config.color + '20' }]}>
        <Ionicons name={config.icon as any} size={12} color={config.color} />
        <Text style={[styles.compactText, { color: config.color }]}>
          {formatAccuracy(rmseM)}
        </Text>
      </View>
    );
  }

  return (
    <View style={[styles.badge, { borderColor: config.color + '40' }]}>
      <View style={styles.header}>
        <Ionicons name={config.icon as any} size={18} color={config.color} />
        <Text style={styles.title}>{sourceLabel}</Text>
      </View>
      <Text style={styles.accuracy}>
        {config.description} ({formatAccuracy(rmseM)})
      </Text>
      <View style={styles.confidenceBar}>
        <View
          style={[
            styles.confidenceFill,
            {
              width: `${Math.round(confidence * 100)}%`,
              backgroundColor: config.color,
            },
          ]}
        />
      </View>
      {attribution && (
        <Text style={styles.attribution}>{attribution}</Text>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  badge: {
    backgroundColor: '#FFFFFF',
    borderRadius: 12,
    borderWidth: 1,
    padding: 12,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.1,
    shadowRadius: 4,
    elevation: 3,
    maxWidth: 240,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginBottom: 4,
  },
  title: {
    fontSize: 14,
    fontWeight: '600',
    color: '#1A1A1A',
  },
  accuracy: {
    fontSize: 12,
    color: '#666',
    marginBottom: 6,
  },
  confidenceBar: {
    height: 4,
    backgroundColor: '#E0E0E0',
    borderRadius: 2,
    overflow: 'hidden',
    marginBottom: 4,
  },
  confidenceFill: {
    height: '100%',
    borderRadius: 2,
  },
  attribution: {
    fontSize: 10,
    color: '#999',
    fontStyle: 'italic',
    marginTop: 2,
  },
  compactBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 12,
  },
  compactText: {
    fontSize: 11,
    fontWeight: '600',
  },
});

export default DataSourceBadge;
