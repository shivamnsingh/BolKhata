// Pure audio helpers: no DOM, no Web Audio objects, so they can be unit-tested in Node.

export const TARGET_RATE = 16000;

/** Streaming box-filter downsampler (handles fractional ratios such as 44.1 kHz -> 16 kHz). */
export class Downsampler {
  constructor(inRate, outRate = TARGET_RATE) {
    if (!(inRate > 0) || !(outRate > 0)) throw new Error("Invalid sample rate");
    this.ratio = inRate / outRate;
    this.position = 0;
    this.sum = 0;
    this.count = 0;
  }

  push(input) {
    const out = [];
    for (let index = 0; index < input.length; index += 1) {
      this.sum += input[index];
      this.count += 1;
      this.position += 1;
      if (this.position >= this.ratio) {
        out.push(this.sum / this.count);
        this.sum = 0;
        this.count = 0;
        this.position -= this.ratio;
      }
    }
    return Float32Array.from(out);
  }
}

function writeAscii(view, offset, text) {
  for (let index = 0; index < text.length; index += 1) view.setUint8(offset + index, text.charCodeAt(index));
}

/** Encode mono Float32 samples as a real 16-bit PCM WAV file. Returns a Uint8Array. */
export function encodeWav(samples, sampleRate = TARGET_RATE) {
  if (!samples || !samples.length) throw new Error("Cannot encode an empty audio segment");
  const dataBytes = samples.length * 2;
  const buffer = new ArrayBuffer(44 + dataBytes);
  const view = new DataView(buffer);
  writeAscii(view, 0, "RIFF");
  view.setUint32(4, 36 + dataBytes, true);
  writeAscii(view, 8, "WAVE");
  writeAscii(view, 12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeAscii(view, 36, "data");
  view.setUint32(40, dataBytes, true);
  for (let index = 0; index < samples.length; index += 1) {
    const clipped = Math.max(-1, Math.min(1, samples[index]));
    view.setInt16(44 + index * 2, clipped < 0 ? clipped * 0x8000 : clipped * 0x7fff, true);
  }
  return new Uint8Array(buffer);
}

/** Base64 without O(n^2) string concatenation. Uses btoa when available (browser, Node 16+). */
export function bytesToBase64(bytes) {
  let binary = "";
  const chunk = 0x8000;
  for (let offset = 0; offset < bytes.length; offset += chunk) {
    binary += String.fromCharCode.apply(null, bytes.subarray(offset, offset + chunk));
  }
  return btoa(binary);
}
