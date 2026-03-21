// Type declarations for optional dependencies that may not be installed.
// These modules are loaded via dynamic import with .catch(() => null) guards.

declare module 'expo-print' {
  export function printToFileAsync(options: {
    html: string;
    width?: number;
    height?: number;
  }): Promise<{ uri: string }>;
}

declare module 'expo-notifications' {
  export interface NotificationContent {
    title: string;
    body: string;
    data?: Record<string, unknown>;
    sound?: boolean | string;
  }

  export interface Notification {
    identifier: string;
    content: NotificationContent;
    trigger: unknown;
  }

  export enum SchedulableTriggerInputTypes {
    DATE = 'date',
    DAILY = 'daily',
    WEEKLY = 'weekly',
    CALENDAR = 'calendar',
    TIME_INTERVAL = 'timeInterval',
  }

  export function requestPermissionsAsync(): Promise<{ status: string }>;
  export function getPermissionsAsync(): Promise<{ status: string }>;
  export function getAllScheduledNotificationsAsync(): Promise<Notification[]>;
  export function cancelScheduledNotificationAsync(identifier: string): Promise<void>;
  export function scheduleNotificationAsync(options: {
    content: NotificationContent;
    trigger: { type?: SchedulableTriggerInputTypes; date?: Date; seconds?: number } | null;
  }): Promise<string>;
  export function cancelAllScheduledNotificationsAsync(): Promise<void>;
  export function setNotificationHandler(handler: {
    handleNotification: () => Promise<{
      shouldShowAlert: boolean;
      shouldPlaySound: boolean;
      shouldSetBadge: boolean;
    }>;
  }): void;
}
