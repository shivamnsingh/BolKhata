import { Streamlit } from "streamlit-component-lib";
import { Downsampler, TARGET_RATE, bytesToBase64, encodeWav } from "./audio.js";
import { SegmentQueue } from "./queue.js";
import { Segmenter } from "./segmenter.js";

const root = document.getElementById("root");
const LOG = "[BolKhata voice]";
const WORKLET_SOURCE = `
class BolKhataCapture extends AudioWorkletProcessor {
  constructor() { super(); this.buffer = new Float32Array(2048); this.length = 0; }
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) return true;
    for (let index = 0; index < channel.length; index += 1) {
      this.buffer[this.length++] = channel[index];
      if (this.length === this.buffer.length) { this.port.postMessage(this.buffer.slice(0)); this.length = 0; }
    }
    return true;
  }
}
registerProcessor("bolkhata-capture", BolKhataCapture);
`;

// One audio session at a time. `generation` invalidates every async continuation from an older session.
let generation = 0;
let session = null; // { stream, context, node, sink, downsampler, segmenter }
let starting = false;
let status = "Ready";

const queue = new SegmentQueue({
  makeId: () => (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`),
  maxQueued: 3,
  send: (segment) => {
    console.info(LOG, "state", { state: "PROCESSING", eventId: segment.eventId, queued: queue.length, durationMs: segment.durationMs });
    setStatus("Processing — confirm or reject the transaction to continue");
    Streamlit.setComponentValue({
      event_id: segment.eventId,
      audio_base64: bytesToBase64(segment.wav),
      mime_type: "audio/wav",
      duration_ms: segment.durationMs
    });
  },
  onSendError: (error) => {
    console.error(LOG, "send failed", error);
    setStatus("Error: could not send audio; still listening");
  }
});

function render() {
  if (!root.querySelector("#start")) {
    root.innerHTML = `
      <div style="font-family: sans-serif; color: #173d36">
        <button id="start" style="background:#123b35;color:white;border:0;border-radius:8px;padding:12px 18px;font-weight:600">Start Listening</button>
        <button id="stop" style="margin-left:8px;background:#fff;color:#123b35;border:1px solid #123b35;border-radius:8px;padding:12px 18px">Stop Listening</button>
        <span id="status" style="margin-left:12px">Ready</span>
        <div style="font-size:12px;margin-top:8px;color:#68756f">The browser keeps listening between utterances. Silence ends a segment; no audio is sent until an utterance is complete.</div>
      </div>`;
    root.querySelector("#start").onclick = startListening;
    root.querySelector("#stop").onclick = stopListening;
  }
  setStatus(status);
  Streamlit.setFrameHeight(105);
}

function setStatus(text) {
  status = text;
  const element = root.querySelector("#status");
  if (element && element.textContent !== text) element.textContent = text;
}

function onSegment(segment) {
  let wav;
  try {
    wav = encodeWav(segment.samples, TARGET_RATE);
  } catch (error) {
    console.error(LOG, "WAV encoding failed", error);
    setStatus("Error: could not encode audio; still listening");
    return;
  }
  const accepted = queue.enqueue({ wav, durationMs: segment.durationMs });
  console.info(LOG, "segment captured", {
    durationMs: segment.durationMs,
    speechMs: segment.speechMs,
    reason: segment.reason,
    sizeBytes: wav.length,
    accepted,
    queued: queue.length,
    inFlightEvent: queue.inFlight
  });
  if (!accepted) setStatus("Busy — speak after confirmation");
  else if (!queue.inFlight && session) setStatus("Listening");
}

async function startListening() {
  if (session || starting) return;
  if (!navigator.mediaDevices?.getUserMedia || !window.AudioContext || !window.AudioWorkletNode) {
    setStatus("Unsupported browser: use Chrome or Edge");
    return;
  }
  starting = true;
  const myGeneration = ++generation;
  let stream = null;
  let context = null;
  try {
    context = new AudioContext(); // created inside the click handler so it is allowed to run
    stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1 } });
    if (myGeneration !== generation) throw new Error("cancelled");
    await context.resume();
    const moduleUrl = URL.createObjectURL(new Blob([WORKLET_SOURCE], { type: "application/javascript" }));
    try {
      await context.audioWorklet.addModule(moduleUrl);
    } finally {
      URL.revokeObjectURL(moduleUrl);
    }
    if (myGeneration !== generation) throw new Error("cancelled");
    const source = context.createMediaStreamSource(stream);
    const node = new AudioWorkletNode(context, "bolkhata-capture", { numberOfInputs: 1, numberOfOutputs: 1, channelCount: 1 });
    const sink = context.createGain();
    sink.gain.value = 0; // keeps the graph pulled without playing the microphone back
    const downsampler = new Downsampler(context.sampleRate, TARGET_RATE);
    const segmenter = new Segmenter({ sampleRate: TARGET_RATE }, {
      onSpeechStart: () => { if (!queue.inFlight) setStatus("Hearing you…"); },
      onDiscard: (info) => console.info(LOG, "noise discarded", info),
      onSegment
    });
    node.port.onmessage = (event) => {
      if (myGeneration !== generation) return;
      segmenter.push(downsampler.push(event.data));
    };
    source.connect(node);
    node.connect(sink);
    sink.connect(context.destination);
    session = { stream, context, node, sink, source, segmenter };
    console.info(LOG, "audio session started", { contextRate: context.sampleRate, targetRate: TARGET_RATE });
    setStatus(queue.inFlight ? status : "Listening");
  } catch (error) {
    releaseAudio({ stream, context });
    if (error?.message !== "cancelled") {
      console.error(LOG, "start failed", error);
      setStatus(error?.name === "NotAllowedError" ? "Microphone permission denied" : "Microphone unavailable");
    }
  } finally {
    starting = false;
  }
}

function releaseAudio(resources) {
  const { stream, context, node, sink, source } = resources;
  try { source?.disconnect(); } catch (error) { /* already disconnected */ }
  try { node?.disconnect(); } catch (error) { /* already disconnected */ }
  try { sink?.disconnect(); } catch (error) { /* already disconnected */ }
  if (node) node.port.onmessage = null;
  if (stream) stream.getTracks().forEach((track) => track.stop());
  if (context && context.state !== "closed") context.close().catch(() => {});
}

function stopListening() {
  generation += 1; // invalidates in-flight async work from the stopped session
  const current = session;
  session = null;
  starting = false;
  if (current) {
    current.segmenter.reset(); // an unfinished utterance is discarded, never sent after Stop
    releaseAudio(current);
  }
  queue.clear();
  setStatus("Stopped");
  console.info(LOG, "stopped; microphone released");
}

Streamlit.events.addEventListener(Streamlit.RENDER_EVENT, (event) => {
  const ack = event.detail?.args?.ack_event_id;
  if (queue.ack(ack)) {
    console.info(LOG, "state", { state: "ACKNOWLEDGED", eventId: ack, queued: queue.length, inFlightEvent: queue.inFlight });
    if (!queue.inFlight) setStatus(session ? "Listening" : "Stopped");
  }
  render();
});
window.addEventListener("pagehide", () => { if (session) stopListening(); });
Streamlit.setComponentReady();
Streamlit.setFrameHeight(105);
