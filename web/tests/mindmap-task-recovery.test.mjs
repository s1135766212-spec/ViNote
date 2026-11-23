import assert from 'node:assert/strict';
import test from 'node:test';
import {
  createMindMapSubmission,
  readMindMapSession,
  writeMindMapSession,
} from '../src/utils/mindmapTaskRecovery.js';

function memoryStorage() {
  const values = new Map();
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  };
}

test('session restores the draft, existing task pointer and current result', () => {
  const storage = memoryStorage();
  const session = {
    mode: 'video', textContent: 'draft', url: 'https://example.test/video', autoSourceTaskId: null, autoSourceRequestId: null,
    taskId: 'task-123', pendingSubmission: null, mindmapContent: '# restored', status: 'completed',
    progress: 100, statusMessage: '完成', error: '', cancelRequested: false,
  };

  writeMindMapSession(session, storage);
  assert.deepEqual(readMindMapSession(storage), session);
});

test('lost submit response can replay a saved request ID, while explicit regeneration gets a fresh one', () => {
  const ids = ['first-request', 'second-request'];
  const createId = () => ids.shift();
  const pending = createMindMapSubmission('text', 'SQL joins', createId);
  const replay = { ...pending };
  const regenerated = createMindMapSubmission('text', 'SQL joins', createId);

  assert.equal(replay.requestId, 'first-request');
  assert.equal(regenerated.requestId, 'second-request');
  assert.notEqual(regenerated.requestId, replay.requestId);
});

test('invalid session payloads and unavailable storage are ignored safely', () => {
  const storage = {
    getItem: () => '{broken', setItem() { throw new Error('blocked'); }, removeItem() { throw new Error('blocked'); },
  };
  assert.equal(readMindMapSession(storage), null);
  assert.doesNotThrow(() => writeMindMapSession({ mode: 'text' }, storage));
});
