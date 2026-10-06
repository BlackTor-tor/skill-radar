import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const plan = require('../.github/scripts/release-plan.cjs');

test('increments patch from semantic version tags', () => {
  assert.equal(plan.nextPatch(['v2.2.0', 'v1.9.9', 'garbage']), 'v2.2.1');
  assert.equal(plan.nextPatch([]), 'v0.1.0');
});

test('normalizes and validates explicit versions', () => {
  assert.equal(plan.normalizeVersion('2.3.0'), 'v2.3.0');
  assert.throws(() => plan.normalizeVersion('v2.3'), /invalid release version/);
});

test('workflow run builds from immutable head sha', () => {
  assert.equal(plan.checkoutSha({ eventName: 'workflow_run', githubSha: 'moving', workflowRun: { head_sha: 'fixed' } }), 'fixed');
  assert.equal(plan.checkoutSha({ eventName: 'push', githubSha: 'fixed' }), 'fixed');
});

test('failed CI, stale CI, duplicate SHA and fork are skipped', () => {
  const base = {
    event: 'push', conclusion: 'success', headBranch: 'main', headRepository: 'owner/repo', repository: 'owner/repo', expectedRepository: 'owner/repo', targetSha: 'abc', mainSha: 'abc', releaseShas: [],
  };
  assert.equal(plan.autoDecision({ ...base, conclusion: 'failure' }).publish, false);
  assert.equal(plan.autoDecision({ ...base, mainSha: 'newer' }).publish, false);
  assert.equal(plan.autoDecision({ ...base, releaseShas: ['abc'] }).publish, false);
  assert.equal(plan.autoDecision({ ...base, headRepository: 'fork/repo' }).publish, false);
  assert.equal(plan.autoDecision(base).publish, true);
});
