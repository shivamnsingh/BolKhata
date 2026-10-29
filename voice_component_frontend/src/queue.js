// Ordered, bounded, single-in-flight delivery queue with explicit acknowledgement.
// Pure logic (no DOM/Streamlit), so every transition can be unit-tested.

export class SegmentQueue {
  /**
   * @param {{send: Function, makeId: Function, maxQueued?: number, onSendError?: Function}} options
   */
  constructor({ send, makeId, maxQueued = 3, onSendError = () => {} }) {
    this.send = send;
    this.makeId = makeId;
    this.maxQueued = maxQueued;
    this.onSendError = onSendError;
    this.items = [];
    this.inFlight = null;
  }

  get length() {
    return this.items.length;
  }

  /** @returns {boolean} false when the queue is full and the segment was dropped */
  enqueue(segment) {
    if (this.items.length >= this.maxQueued) return false;
    this.items.push(segment);
    this.pump();
    return true;
  }

  /** Send the next segment if nothing is currently awaiting acknowledgement. */
  pump() {
    if (this.inFlight || !this.items.length) return null;
    const segment = this.items.shift();
    const eventId = this.makeId();
    this.inFlight = eventId;
    try {
      this.send({ ...segment, eventId });
    } catch (error) {
      this.inFlight = null; // never leave the queue wedged by a failed send
      this.onSendError(error);
      return null;
    }
    return eventId;
  }

  /** Acknowledge the in-flight event; stale/duplicate/unknown acks are ignored. */
  ack(eventId) {
    if (!eventId || eventId !== this.inFlight) return false;
    this.inFlight = null;
    this.pump();
    return true;
  }

  clear() {
    this.items = [];
    this.inFlight = null;
  }
}
