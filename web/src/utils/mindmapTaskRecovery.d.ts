export type MindMapInputMode = 'text' | 'video';
export type MindMapTaskState = 'queued' | 'processing' | 'completed' | 'error' | 'cancelled';

export type MindMapSubmission =
  | { kind: 'text'; requestId: string; content: string; language: string }
  | { kind: 'video'; requestId: string; url: string; language: string };

export interface MindMapSession {
  mode: MindMapInputMode;
  textContent: string;
  url: string;
  autoSourceTaskId: string | null;
  autoSourceRequestId: string | null;
  taskId: string | null;
  pendingSubmission: MindMapSubmission | null;
  mindmapContent: string;
  status: MindMapTaskState | null;
  progress: number;
  statusMessage: string;
  error: string;
  cancelRequested: boolean;
}

type StorageLike = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;

export const MINDMAP_SESSION_KEY: string;
export function emptyMindMapSession(): MindMapSession;
export function createMindMapSubmission(
  mode: MindMapInputMode,
  source: string,
  createId?: () => string,
): MindMapSubmission;
export function readMindMapSession(storage?: StorageLike | null): MindMapSession | null;
export function writeMindMapSession(session: MindMapSession, storage?: StorageLike | null): void;
export function clearMindMapSession(storage?: StorageLike | null): void;
