'use strict';

function normalizeVersion(value) {
  const version = String(value || '').trim();
  const normalized = version.startsWith('v') ? version : `v${version}`;
  if (!/^v\d+\.\d+\.\d+$/.test(normalized)) throw new Error(`invalid release version: ${value}`);
  return normalized;
}

function nextPatch(tags) {
  const versions = tags
    .map((tag) => String(tag).match(/^v(\d+)\.(\d+)\.(\d+)$/))
    .filter(Boolean)
    .map((match) => match.slice(1).map(Number));
  if (!versions.length) return 'v0.1.0';
  versions.sort((a, b) => (a[0] - b[0]) || (a[1] - b[1]) || (a[2] - b[2]));
  const [major, minor, patch] = versions[versions.length - 1];
  return `v${major}.${minor}.${patch + 1}`;
}

function checkoutSha({ eventName, githubSha, workflowRun }) {
  if (eventName === 'workflow_run') return workflowRun?.head_sha || '';
  return githubSha || '';
}

function autoDecision({
  event,
  conclusion,
  headBranch,
  headRepository,
  repository,
  expectedRepository,
  targetSha,
  mainSha,
  releaseShas,
}) {
  if (event !== 'push') return { publish: false, reason: `workflow event ${event || 'unknown'}` };
  if (conclusion !== 'success') return { publish: false, reason: `CI conclusion ${conclusion || 'unknown'}` };
  if (headBranch !== 'main') return { publish: false, reason: `branch ${headBranch || 'unknown'}` };
  if (!headRepository || headRepository !== expectedRepository || repository !== expectedRepository) {
    return { publish: false, reason: 'foreign repository or fork' };
  }
  if (!targetSha || targetSha !== mainSha) return { publish: false, reason: 'successful CI is not current main HEAD' };
  if (releaseShas.includes(targetSha)) return { publish: false, reason: 'SHA already has a release' };
  return { publish: true, reason: 'successful main push CI' };
}

module.exports = { normalizeVersion, nextPatch, checkoutSha, autoDecision };
