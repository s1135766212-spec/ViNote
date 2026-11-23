import assert from 'node:assert/strict';
import test from 'node:test';
import { fitMarkmapWhenSized } from '../src/components/markmapViewport.js';

function element({ width, height, isConnected = true }) {
  return {
    isConnected,
    getBoundingClientRect: () => ({ width, height }),
  };
}

test('does not fit a Markmap while its container has no measurable size', () => {
  let fits = 0;
  assert.equal(fitMarkmapWhenSized(element({ width: 0, height: 0 }), () => fits++), false);
  assert.equal(fits, 0);
});

test('fits a Markmap once its connected container has a measurable size', () => {
  let fits = 0;
  assert.equal(fitMarkmapWhenSized(element({ width: 900, height: 640 }), () => fits++), true);
  assert.equal(fits, 1);
});

test('does not fit a Markmap whose container has been detached', () => {
  let fits = 0;
  assert.equal(fitMarkmapWhenSized(element({ width: 900, height: 640, isConnected: false }), () => fits++), false);
  assert.equal(fits, 0);
});
