import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');

function load(fakeWindow) {
  const source = readFileSync(new URL('../../web/frontend/src/lib/speechInput.ts', import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const exports = {};
  const context = { exports };
  if (fakeWindow) context.window = fakeWindow;
  runInNewContext(outputText, context);
  return exports;
}

const speech = load();
const result = (transcript, isFinal) => ({ isFinal, length: 1, 0: { transcript } });
const inserted = (value, start, end, text) => speech.insertTranscript(value, start, end, text);

test('transcript inserts at the caret and returns the next caret', () => {
  let next = inserted('你好世界', 2, 2, '，');
  assert.equal(next.value, '你好，世界');
  assert.equal(next.caret, 3);
  next = inserted('世界', 0, 0, '你好');
  assert.equal(next.value, '你好世界');
  assert.equal(next.caret, 2);
  next = inserted('你好', 2, 2, '世界');
  assert.equal(next.value, '你好世界');
  assert.equal(next.caret, 4);
});

test('transcript replaces the selected text and clamps stale offsets', () => {
  let next = inserted('12345', 1, 4, 'abc');
  assert.equal(next.value, '1abc5');
  assert.equal(next.caret, 4);
  next = inserted('abc', 99, 120, '中文');
  assert.equal(next.value, 'abc中文');
  assert.equal(next.caret, 5);
});

test('blank transcript leaves the draft untouched', () => {
  const next = inserted('原文', 1, 1, '   ');
  assert.equal(next.value, '原文');
  assert.equal(next.caret, 1);
});

test('final results are committed once while interim text remains a preview', () => {
  const seen = new Set();
  const values = [result('第一句。', true), result('第二句', false)];
  const first = speech.collectSpeechResults(values, 0, seen);
  assert.equal(first.finalText, '第一句。');
  assert.equal(first.interimText, '第二句');
  assert.deepEqual([...first.finalIndexes], [0]);
  first.finalIndexes.forEach((index) => seen.add(index));

  values[1] = result('第二句。', true);
  const second = speech.collectSpeechResults(values, 0, seen);
  assert.equal(second.finalText, '第二句。');
  assert.equal(second.interimText, '');
  assert.deepEqual([...second.finalIndexes], [1]);

  const replay = speech.collectSpeechResults(values, 0, new Set([0, 1]));
  assert.equal(replay.finalText, '');
  assert.equal(replay.interimText, '');
});

test('result list honors resultIndex and ignores empty alternatives', () => {
  const values = [result('旧结果', true), result('', true), result('新结果', false)];
  const chunks = speech.collectSpeechResults(values, 2, new Set());
  assert.equal(chunks.finalText, '');
  assert.equal(chunks.interimText, '新结果');
  assert.equal(chunks.finalIndexes.length, 0);
});

test('speech errors become actionable Chinese messages', () => {
  assert.match(speech.speechErrorMessage('not-allowed'), /麦克风权限/);
  assert.match(speech.speechErrorMessage('service-not-allowed'), /麦克风权限/);
  assert.match(speech.speechErrorMessage('audio-capture'), /麦克风/);
  assert.match(speech.speechErrorMessage('no-speech'), /没听到/);
  assert.match(speech.speechErrorMessage('network'), /网络/);
  assert.match(speech.speechErrorMessage('start-failed', 'InvalidStateError'), /InvalidStateError/);
  assert.match(speech.speechErrorMessage('unexpected'), /重试/);
});

test('recognizer detection supports prefixed, unprefixed and unsupported environments', () => {
  function Recognition() {}
  assert.equal(load({ SpeechRecognition: Recognition }).getSpeechRecognitionCtor(), Recognition);
  assert.equal(load({ webkitSpeechRecognition: Recognition }).getSpeechRecognitionCtor(), Recognition);
  assert.equal(load().getSpeechRecognitionCtor(), null);
});
