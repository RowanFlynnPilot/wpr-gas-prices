// docs/annotations.json is edited by people (the newsroom), not the scraper, so
// it is checked here and NOT in tests/data-contract.test.mjs: the update workflow
// runs the contract before every publish, and one typo in a note would otherwise
// block every price update until someone fixed it. This runs in the Tests
// workflow on the commit that changed the file, which names the bad note.
//
// Run: npm run test:widget
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const sandbox = { self: {} };
vm.runInNewContext(readFileSync(new URL('../docs/widget-logic.js', import.meta.url), 'utf8'), sandbox);
const L = sandbox.self.WprGasLogic;
const load = name => JSON.parse(readFileSync(new URL('../docs/' + name, import.meta.url), 'utf8'));

test('annotations.json: hand-edited newsroom notes are all well-formed', () => {
  // A typo here would silently drop a note from the chart; fail loudly instead.
  const notes = load('annotations.json');
  assert.ok(Array.isArray(notes), 'annotations.json is a JSON array');
  notes.forEach((n, i) => assert.ok(L.validNote(n),
    `note #${i + 1} needs "date": "YYYY-MM-DD", "text" of 1–${L.NOTE_MAX} characters, optional "fuels": ${JSON.stringify(n)}`));
});
