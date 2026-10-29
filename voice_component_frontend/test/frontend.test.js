import assert from "node:assert/strict";
import test from "node:test";

import { Downsampler, bytesToBase64, encodeWav } from "../src/audio.js";
import { SegmentQueue } from "../src/queue.js";
import { Segmenter } from "../src/segmenter.js";

const RATE = 16000;
const tone = (ms, amp = 0.3) => Float32Array.from({ length: (RATE * ms) / 1000 }, (_, i) => amp * Math.sin((2 * Math.PI * 200 * i) / RATE));
const silence = (ms) => new Float32Array((RATE * ms) / 1000);
const cat = (...parts) => {
  const out = new Float32Array(parts.reduce((n, p) => n + p.length, 0));
  let o = 0;
  parts.forEach((p) => { out.set(p, o); o += p.length; });
  return out;
};

function run(audio, config = {}, block = 777) {
  const segments = [];
  const discards = [];
  const seg = new Segmenter({ sampleRate: RATE, ...config }, { onSegment: (s) => segments.push(s), onDiscard: (d) => discards.push(d) });
  for (let i = 0; i < audio.length; i += block) seg.push(audio.slice(i, i + block)); // odd block size on purpose
  return { segments, discards, seg };
}

// ---- segmenter -------------------------------------------------------------------------

test("pure silence never produces a segment (regression: 916 ms silent segments)", () => {
  const { segments } = run(silence(30000));
  assert.equal(segments.length, 0);
});

test("one utterance followed by silence yields exactly one segment containing the speech", () => {
  const { segments } = run(cat(silence(1000), tone(2000), silence(3000)));
  assert.equal(segments.length, 1);
  assert.ok(segments[0].durationMs >= 2000 && segments[0].durationMs < 3200, `duration ${segments[0].durationMs}`);
  assert.equal(segments[0].reason, "silence");
});

test("consecutive utterances produce independent segments", () => {
  const { segments } = run(cat(tone(1500), silence(1500), tone(1800), silence(1500), tone(1200), silence(1500)));
  assert.equal(segments.length, 3);
});

test("a short click is discarded, not sent", () => {
  const { segments, discards } = run(cat(silence(500), tone(60), silence(2000)));
  assert.equal(segments.length, 0);
  assert.equal(discards.length, 1);
});

test("continuous speech is force-split below the 30 s API limit (regression: 30670 ms segment)", () => {
  const { segments } = run(cat(tone(70000)));
  assert.ok(segments.length >= 2);
  segments.forEach((s) => assert.ok(s.durationMs <= 25000, `segment ${s.durationMs} ms`));
  assert.equal(segments[0].reason, "max-duration");
});

test("segmentation is driven by samples, not wall-clock time", () => {
  const started = Date.now();
  const { segments } = run(cat(tone(1000), silence(1500), tone(1000), silence(1500)));
  assert.equal(segments.length, 2);
  assert.ok(Date.now() - started < 2000);
});

test("reset() discards an unfinished utterance", () => {
  const { seg, segments } = run(tone(1000));
  seg.reset();
  seg.push(silence(2000));
  assert.equal(segments.length, 0);
  assert.equal(seg.speaking, false);
});

// ---- audio helpers ---------------------------------------------------------------------

test("encodeWav writes a valid 16 kHz mono PCM header and correct length", () => {
  const wav = encodeWav(tone(500));
  const view = new DataView(wav.buffer);
  assert.equal(String.fromCharCode(...wav.slice(0, 4)), "RIFF");
  assert.equal(String.fromCharCode(...wav.slice(8, 12)), "WAVE");
  assert.equal(view.getUint16(20, true), 1);
  assert.equal(view.getUint16(22, true), 1);
  assert.equal(view.getUint32(24, true), 16000);
  assert.equal(view.getUint32(40, true), 8000 * 2);
  assert.equal(wav.length, 44 + 8000 * 2);
  assert.throws(() => encodeWav(new Float32Array(0)));
});

test("Downsampler converts 48 kHz and 44.1 kHz to ~16 kHz", () => {
  assert.equal(new Downsampler(48000).push(new Float32Array(4800)).length, 1600);
  const n = new Downsampler(44100).push(new Float32Array(44100)).length;
  assert.ok(Math.abs(n - 16000) <= 1, `got ${n}`);
});

test("bytesToBase64 round-trips large buffers", () => {
  const bytes = Uint8Array.from({ length: 100000 }, (_, i) => i % 251);
  assert.deepEqual(new Uint8Array(Buffer.from(bytesToBase64(bytes), "base64")), bytes);
});

// ---- queue -----------------------------------------------------------------------------

function makeQueue(extra = {}) {
  const sent = [];
  let n = 0;
  const queue = new SegmentQueue({ send: (s) => sent.push(s), makeId: () => `e${++n}`, ...extra });
  return { queue, sent };
}

test("queue sends one segment at a time and progresses on each ack", () => {
  const { queue, sent } = makeQueue();
  queue.enqueue({ tag: "a" });
  queue.enqueue({ tag: "b" });
  queue.enqueue({ tag: "c" });
  assert.deepEqual(sent.map((s) => s.tag), ["a"]);
  assert.equal(queue.length, 2);
  assert.equal(queue.ack("e1"), true);
  assert.deepEqual(sent.map((s) => s.tag), ["a", "b"]);
  assert.equal(queue.ack("e2"), true);
  assert.equal(queue.ack("e3"), true);
  assert.deepEqual(sent.map((s) => s.tag), ["a", "b", "c"]);
  assert.equal(queue.inFlight, null);
});

test("duplicate, stale and unknown acks are ignored", () => {
  const { queue, sent } = makeQueue();
  queue.enqueue({ tag: "a" });
  queue.enqueue({ tag: "b" });
  assert.equal(queue.ack("nope"), false);
  assert.equal(queue.ack(null), false);
  assert.equal(queue.ack("e1"), true);
  assert.equal(queue.ack("e1"), false); // duplicate render event for the same ack
  assert.equal(sent.length, 2);
  assert.equal(queue.inFlight, "e2");
});

test("queue is bounded and reports a dropped segment", () => {
  const { queue } = makeQueue({ maxQueued: 2 });
  assert.equal(queue.enqueue({}), true); // goes in flight immediately
  assert.equal(queue.enqueue({}), true);
  assert.equal(queue.enqueue({}), true);
  assert.equal(queue.enqueue({}), false);
});

test("a failing send never wedges the queue", () => {
  const errors = [];
  let fail = true;
  let n = 0;
  const sent = [];
  const queue = new SegmentQueue({
    makeId: () => `e${++n}`,
    send: (s) => { if (fail) throw new Error("boom"); sent.push(s); },
    onSendError: (e) => errors.push(e.message)
  });
  queue.enqueue({ tag: "a" });
  assert.equal(queue.inFlight, null);
  assert.deepEqual(errors, ["boom"]);
  fail = false;
  queue.enqueue({ tag: "b" });
  assert.deepEqual(sent.map((s) => s.tag), ["b"]);
});

test("stop/restart: clear() drops in-flight and queued work, then delivery works again", () => {
  const { queue, sent } = makeQueue();
  queue.enqueue({ tag: "a" });
  queue.enqueue({ tag: "b" });
  queue.clear();
  assert.equal(queue.inFlight, null);
  assert.equal(queue.length, 0);
  assert.equal(queue.ack("e1"), false); // late ack from the old session is ignored
  queue.enqueue({ tag: "c" });
  assert.deepEqual(sent.map((s) => s.tag), ["a", "c"]);
  assert.equal(queue.inFlight, "e2");
});
