/**
 * recorder.worklet.js
 *
 * AudioWorklet that accumulates incoming float32 audio frames into a
 * fixed-size buffer (16384 samples ≈ 1.024 s at 16 kHz) and posts each
 * full buffer back to the main thread. The main thread converts to
 * int16 PCM and frames it for the Casual-SST WebSocket.
 *
 * Port-compatible with Skynet's demo so the same wire format is exercised.
 */
class RecorderProcessor extends AudioWorkletProcessor {
  /** Buffer size in samples. At 16 kHz this is ≈ 1.024 s of audio. */
  static BUFFER_SIZE = 16384;

  constructor() {
    super();
    this._buffer = new Float32Array(RecorderProcessor.BUFFER_SIZE);
    this._written = 0;
  }

  /**
   * Called by the Web Audio engine for each render quantum (128 samples
   * by default). We pull the first channel of the first input and stream
   * its samples into our buffer; whenever the buffer fills, we flush.
   */
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) return true; // input not ready yet — keep processor alive
    this._append(channel);
    return true;
  }

  _append(channelData) {
    for (let i = 0; i < channelData.length; i++) {
      if (this._written === RecorderProcessor.BUFFER_SIZE) this._flush();
      this._buffer[this._written++] = channelData[i];
    }
  }

  _flush() {
    // Post a snapshot — copy first so the main thread can hold onto it
    // even after the worklet reuses the underlying memory.
    this.port.postMessage(
      this._written === RecorderProcessor.BUFFER_SIZE
        ? this._buffer.slice(0)
        : this._buffer.slice(0, this._written)
    );
    this._written = 0;
  }
}

registerProcessor('recorder.worklet', RecorderProcessor);
