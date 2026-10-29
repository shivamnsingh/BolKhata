// Silence-based utterance segmenter. Pure state machine driven only by audio samples, so timing
// does not depend on requestAnimationFrame / timers (both are throttled in background tabs).

export const DEFAULT_CONFIG = Object.freeze({
  sampleRate: 16000,
  frameMs: 20,
  minThreshold: 0.015, // RMS below this is never "speech"
  noiseMultiplier: 3, // speech must also exceed noiseFloor * multiplier
  silenceMs: 900, // trailing silence that ends an utterance
  minSpeechMs: 250, // shorter voiced audio is treated as a click/noise and discarded
  prerollMs: 300, // audio kept from before speech onset
  tailMs: 300, // trailing silence kept in the emitted segment
  maxSegmentMs: 25000 // hard cap, safely below the documented 30 s API limit
});

function rms(frame) {
  let sum = 0;
  for (let index = 0; index < frame.length; index += 1) sum += frame[index] * frame[index];
  return Math.sqrt(sum / frame.length);
}

function concat(frames) {
  let total = 0;
  frames.forEach((frame) => { total += frame.length; });
  const out = new Float32Array(total);
  let offset = 0;
  frames.forEach((frame) => { out.set(frame, offset); offset += frame.length; });
  return out;
}

export class Segmenter {
  /**
   * @param {object} config overrides for DEFAULT_CONFIG
   * @param {{onSegment: Function, onSpeechStart?: Function, onDiscard?: Function}} handlers
   */
  constructor(config = {}, handlers = {}) {
    this.config = { ...DEFAULT_CONFIG, ...config };
    this.handlers = handlers;
    this.frameSize = Math.round((this.config.sampleRate * this.config.frameMs) / 1000);
    this.prerollFrames = Math.ceil(this.config.prerollMs / this.config.frameMs);
    this.tailFrames = Math.ceil(this.config.tailMs / this.config.frameMs);
    this.reset();
  }

  reset() {
    this.mode = "idle";
    this.carry = new Float32Array(0);
    this.preroll = [];
    this.frames = [];
    this.voicedFrames = 0;
    this.silentRun = 0;
    this.noiseFloor = 0;
  }

  get speaking() {
    return this.mode === "speaking";
  }

  push(samples) {
    let data = samples;
    if (this.carry.length) {
      data = new Float32Array(this.carry.length + samples.length);
      data.set(this.carry, 0);
      data.set(samples, this.carry.length);
    }
    let offset = 0;
    while (data.length - offset >= this.frameSize) {
      this.#frame(data.slice(offset, offset + this.frameSize));
      offset += this.frameSize;
    }
    this.carry = data.slice(offset);
  }

  /** Emit whatever speech is in progress (used when listening is stopped cleanly). */
  flush() {
    if (this.mode === "speaking" && this.frames.length) this.#finish("flush");
    this.mode = "idle";
  }

  #frame(frame) {
    const level = rms(frame);
    const threshold = Math.max(this.config.minThreshold, this.noiseFloor * this.config.noiseMultiplier);
    const voiced = level > threshold;
    if (this.mode === "idle") {
      if (!voiced) {
        this.noiseFloor = this.noiseFloor === 0 ? level : this.noiseFloor * 0.95 + level * 0.05;
        this.preroll.push(frame);
        if (this.preroll.length > this.prerollFrames) this.preroll.shift();
        return;
      }
      this.mode = "speaking";
      this.frames = [...this.preroll, frame];
      this.preroll = [];
      this.voicedFrames = 1;
      this.silentRun = 0;
      if (this.handlers.onSpeechStart) this.handlers.onSpeechStart();
      return;
    }
    this.frames.push(frame);
    if (voiced) {
      this.voicedFrames += 1;
      this.silentRun = 0;
    } else {
      this.silentRun += 1;
      this.noiseFloor = this.noiseFloor === 0 ? level : this.noiseFloor * 0.98 + level * 0.02;
    }
    if (this.silentRun * this.config.frameMs >= this.config.silenceMs) this.#finish("silence");
    else if (this.frames.length * this.config.frameMs >= this.config.maxSegmentMs) this.#finish("max-duration");
  }

  #finish(reason) {
    let frames = this.frames;
    const voiced = this.voicedFrames;
    if (this.silentRun > this.tailFrames) frames = frames.slice(0, frames.length - (this.silentRun - this.tailFrames));
    this.frames = [];
    this.voicedFrames = 0;
    this.silentRun = 0;
    // After a forced split the speaker is still talking, so stay in the speaking state.
    this.mode = reason === "max-duration" ? "speaking" : "idle";
    const speechMs = voiced * this.config.frameMs;
    if (speechMs < this.config.minSpeechMs) {
      if (this.handlers.onDiscard) this.handlers.onDiscard({ reason, speechMs });
      return;
    }
    const samples = concat(frames);
    this.handlers.onSegment({
      samples,
      durationMs: Math.round((samples.length * 1000) / this.config.sampleRate),
      speechMs,
      reason
    });
  }
}
