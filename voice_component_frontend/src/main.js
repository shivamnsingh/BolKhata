import { Streamlit } from "streamlit-component-lib";

const root = document.getElementById("root");
const MIN_SEGMENT_MS = 700;
const MAX_SEGMENT_MS = 10000;
const SILENCE_MS = 900;
const THRESHOLD = 0.035;

let stream = null;
let recorder = null;
let analyser = null;
let audioContext = null;
let chunks = [];
let listening = false;
let segmentStartedAt = 0;
let silenceStartedAt = 0;
let eventSequence = 0;
let lastAcknowledged = null;
let inFlightEvent = null;
const queuedBlobs = [];
let recorderMimeType = "";
let componentStatus = "Ready";

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
  updateStatus(listening ? "Listening" : componentStatus);
  Streamlit.setFrameHeight(105);
}

function updateStatus(status) {
  componentStatus = status;
  const element = root.querySelector("#status");
  if (element) element.textContent = status;
}

async function emitBlob(blob, durationMs) {
  if (!listening || inFlightEvent || !blob || !blob.size) return;
  try {
    const buffer = await blob.arrayBuffer();
    const bytes = new Uint8Array(buffer);
    let binary = "";
    bytes.forEach((byte) => { binary += String.fromCharCode(byte); });
    const eventId = `${Date.now()}-${eventSequence++}`;
    inFlightEvent = eventId;
    Streamlit.setComponentValue({
      event_id: eventId,
      audio_base64: btoa(binary),
      mime_type: blob.type || "audio/webm",
      duration_ms: durationMs
    });
    updateStatus("Processing");
  } catch (error) {
    updateStatus("Error: audio segment unavailable");
  }
}

async function startListening() {
  if (listening) return;
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    updateStatus("Unsupported browser: use Chrome or Edge");
    return;
  }
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    audioContext = new AudioContext();
    const source = audioContext.createMediaStreamSource(stream);
    analyser = audioContext.createAnalyser();
    analyser.fftSize = 1024;
    source.connect(analyser);
    listening = true;
    startSegment();
  } catch (error) {
    updateStatus(error?.name === "NotAllowedError" ? "Microphone permission denied" : "Microphone unavailable");
  }
}

function startSegment() {
  if (!listening || !stream) return;
  chunks = [];
  segmentStartedAt = performance.now();
  silenceStartedAt = 0;
  const supportedMime = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus"]
    .find((mime) => MediaRecorder.isTypeSupported(mime));
  try {
    recorder = supportedMime ? new MediaRecorder(stream, { mimeType: supportedMime }) : new MediaRecorder(stream);
    recorderMimeType = recorder.mimeType || supportedMime || "audio/webm";
  } catch (error) {
    updateStatus("This browser cannot encode microphone audio");
    listening = false;
    return;
  }
  recorder.ondataavailable = (event) => {
    if (event.data.size) chunks.push(event.data);
  };
  recorder.onstop = async () => {
    const durationMs = Math.round(performance.now() - segmentStartedAt);
    if (durationMs >= MIN_SEGMENT_MS && chunks.length) {
      const blob = new Blob(chunks, { type: recorderMimeType });
      if (inFlightEvent) {
        if (queuedBlobs.length < 3) queuedBlobs.push({ blob, durationMs });
        else updateStatus("Busy — speak after confirmation");
      } else {
        emitBlob(blob, durationMs);
      }
    }
    if (listening) startSegment();
  };
  recorder.start();
  updateStatus("Listening");
  detectSpeechBoundary();
}

function detectSpeechBoundary() {
  if (!listening || !recorder || recorder.state !== "recording") return;
  const values = new Uint8Array(analyser.fftSize);
  analyser.getByteTimeDomainData(values);
  let sum = 0;
  values.forEach((value) => { const normalized = (value - 128) / 128; sum += normalized * normalized; });
  const rms = Math.sqrt(sum / values.length);
  const elapsed = performance.now() - segmentStartedAt;
  if (rms < THRESHOLD) {
    if (!silenceStartedAt) silenceStartedAt = performance.now();
    if (elapsed >= MIN_SEGMENT_MS && performance.now() - silenceStartedAt >= SILENCE_MS) {
      recorder.stop();
      return;
    }
  } else {
    silenceStartedAt = 0;
  }
  if (elapsed >= MAX_SEGMENT_MS) recorder.stop();
  else requestAnimationFrame(detectSpeechBoundary);
}

function stopListening() {
  listening = false;
  queuedBlobs.length = 0;
  inFlightEvent = null;
  if (recorder && recorder.state === "recording") recorder.stop();
  if (stream) stream.getTracks().forEach((track) => track.stop());
  if (audioContext) audioContext.close();
  stream = null;
  recorder = null;
  analyser = null;
  audioContext = null;
  updateStatus("Stopped");
}

Streamlit.events.addEventListener(Streamlit.RENDER_EVENT, (event) => {
  const data = event.detail;
  const ack = data?.args?.ack_event_id;
  if (ack && ack !== lastAcknowledged) {
    lastAcknowledged = ack;
    if (ack === inFlightEvent) {
      inFlightEvent = null;
      const next = queuedBlobs.shift();
      if (next) emitBlob(next.blob, next.durationMs);
    }
  }
  render();
});
Streamlit.setComponentReady();
Streamlit.setFrameHeight(105);
