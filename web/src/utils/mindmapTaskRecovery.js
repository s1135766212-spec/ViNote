export const MINDMAP_SESSION_KEY = 'vinote.mindmap.session.v1';

export function emptyMindMapSession() {
  return {
    mode: 'text', textContent: '', url: '', autoSourceTaskId: null, autoSourceRequestId: null, taskId: null,
    pendingSubmission: null, mindmapContent: '', status: null, progress: 0,
    statusMessage: '', error: '', cancelRequested: false,
  };
}

function browserSessionStorage() {
  try {
    return typeof window === 'undefined' ? null : window.sessionStorage;
  } catch {
    return null;
  }
}

export function createMindMapSubmission(mode, source, createId = () => crypto.randomUUID()) {
  const requestId = createId();
  return mode === 'text'
    ? { kind: 'text', requestId, content: source, language: 'zh' }
    : { kind: 'video', requestId, url: source, language: 'zh' };
}

function isSubmission(value) {
  if (!value || typeof value !== 'object') return false;
  return typeof value.requestId === 'string'
    && typeof value.language === 'string'
    && ((value.kind === 'text' && typeof value.content === 'string')
      || (value.kind === 'video' && typeof value.url === 'string'));
}

export function readMindMapSession(storage = browserSessionStorage()) {
  if (!storage) return null;
  try {
    const raw = storage.getItem(MINDMAP_SESSION_KEY);
    if (!raw) return null;
    const value = JSON.parse(raw);
    if (value.mode !== 'text' && value.mode !== 'video') return null;
    const states = ['queued', 'processing', 'completed', 'error', 'cancelled'];
    return {
      ...emptyMindMapSession(),
      mode: value.mode,
      textContent: typeof value.textContent === 'string' ? value.textContent : '',
      url: typeof value.url === 'string' ? value.url : '',
      autoSourceTaskId: typeof value.autoSourceTaskId === 'string' ? value.autoSourceTaskId : null,
      autoSourceRequestId: typeof value.autoSourceRequestId === 'string' ? value.autoSourceRequestId : null,
      taskId: typeof value.taskId === 'string' ? value.taskId : null,
      pendingSubmission: isSubmission(value.pendingSubmission) ? value.pendingSubmission : null,
      mindmapContent: typeof value.mindmapContent === 'string' ? value.mindmapContent : '',
      status: states.includes(value.status) ? value.status : null,
      progress: typeof value.progress === 'number' ? Math.max(0, Math.min(100, value.progress)) : 0,
      statusMessage: typeof value.statusMessage === 'string' ? value.statusMessage : '',
      error: typeof value.error === 'string' ? value.error : '',
      cancelRequested: value.cancelRequested === true,
    };
  } catch {
    return null;
  }
}

export function writeMindMapSession(session, storage = browserSessionStorage()) {
  if (!storage) return;
  try {
    storage.setItem(MINDMAP_SESSION_KEY, JSON.stringify(session));
  } catch {
    // Recovery is best-effort when storage is blocked or full.
  }
}

export function clearMindMapSession(storage = browserSessionStorage()) {
  try {
    storage?.removeItem(MINDMAP_SESSION_KEY);
  } catch {
    // Ignore browser storage restrictions.
  }
}
