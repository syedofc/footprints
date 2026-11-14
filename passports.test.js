const assert = require('node:assert/strict');
const test = require('node:test');
const { passportBadges, summarizePassports, needsAttention, hasPublisherSignature } = require('./passports.js');

const agent = (setup, record = 'intact') => ({ kind: 'repo-agent', version: '1.0.0', record, setup });
const tool = (install, publisher = 'verified', extra = {}) =>
  ({ kind: 'device-tool', version: '2.1.287', record: 'intact', install, publisher, ...extra });

test('shows version, record check and setup check for repository passports', () => {
  assert.deepEqual(passportBadges(agent('unchanged')).map(badge => [badge.text, badge.tone]), [
    ['v1.0.0', 'info'], ['✓ Record ID matches', 'ok'], ['✓ Setup unchanged', 'ok'],
  ]);
  assert.deepEqual(passportBadges(agent('changed', 'id_mismatch')).map(badge => badge.text),
    ['v1.0.0', '⚠ Record ID check failed', '⚠ Setup changed']);
  assert.deepEqual(passportBadges({ kind: 'provider-model', version: '1.0', record: 'intact' }).map(badge => badge.text),
    ['v1.0', '✓ Record ID matches', 'Provider model']);
});

test('shows install and publisher checks for tool passports', () => {
  assert.deepEqual(passportBadges(tool('unchanged')).map(badge => badge.text),
    ['v2.1.287', '✓ Record ID matches', '✓ Installation fingerprint matches', '✓ Signing team matches']);
  assert.deepEqual(passportBadges(tool('updated', 'verified', { current_version: '2.1.300' })).map(badge => badge.text),
    ['v2.1.287', '✓ Record ID matches', 'New version installed: v2.1.300', '✓ Signing team matches']);
  assert.deepEqual(passportBadges(tool('changed', 'changed')).map(badge => badge.text),
    ['v2.1.287', '✓ Record ID matches', '⚠ Installation fingerprint differs', '⚠ Signing identity differs']);
  assert.deepEqual(passportBadges(tool('missing', 'user')).map(badge => badge.text),
    ['v2.1.287', '✓ Record ID matches', 'Not installed', 'Vouched for by a person']);
});

test('an update is normal, a changed program or publisher needs attention', () => {
  assert.equal(needsAttention(tool('updated')), false);
  assert.equal(needsAttention(tool('missing', 'user')), false);
  assert.equal(needsAttention(tool('changed')), true);
  assert.equal(needsAttention(tool('unchanged', 'changed')), true);
  assert.equal(needsAttention(agent('files-removed')), true);
  assert.equal(needsAttention(agent('unchanged', 'id_mismatch')), true);
});

test('summarizes passports for the tiles', () => {
  assert.deepEqual(summarizePassports([
    tool('unchanged'), tool('changed'), agent('unchanged'), agent('changed'), agent('repo-missing', 'id_mismatch'),
    { kind: 'model', record: 'intact' }, { kind: 'provider-model', record: 'intact' },
  ]), { tools: 2, agents: 3, models: 1, attention: 3 });
});

test('only installations with their own valid signature take the publisher from it', () => {
  assert.equal(hasPublisherSignature('Q6L2SF6YDW'), true);
  for (const signer of [undefined, 'unsigned', 'invalid', 'script']) assert.equal(hasPublisherSignature(signer), false, signer);
});
