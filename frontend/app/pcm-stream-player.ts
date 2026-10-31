export type PcmStreamCallbacks = {
  onPlaybackStart: (estimatedPerformanceTime: number) => void;
  onPlaybackComplete: (performanceTime: number) => void;
};

/** Schedules ordered mono s16le PCM chunks on a Web Audio clock. */
export class PcmStreamPlayer {
  private generation = 0;
  private active = false;
  private finished = false;
  private expectedSequence = 0;
  private nextStartAt = 0;
  private playbackStarted = false;
  private chain: Promise<void> = Promise.resolve();
  private readonly sources = new Set<AudioBufferSourceNode>();

  constructor(
    private readonly context: AudioContext,
    private readonly callbacks: PcmStreamCallbacks,
    private readonly sampleRate = 24_000,
  ) {}

  append(sequence: number, bytes: Blob): Promise<void> {
    if (!this.active || this.finished) return Promise.reject(new Error("PCM stream is not accepting chunks"));
    const generation = this.generation;
    this.chain = this.chain.then(async () => {
      if (!this.active || generation !== this.generation) return;
      if (sequence !== this.expectedSequence) {
        throw new Error(`PCM chunk sequence mismatch: expected ${this.expectedSequence}, received ${sequence}`);
      }
      const data = await bytes.arrayBuffer();
      if (!this.active || generation !== this.generation) return;
      if (data.byteLength === 0 || data.byteLength % 2 !== 0) {
        throw new Error("Gemini PCM chunk must contain complete non-empty s16le samples");
      }
      this.expectedSequence += 1;
      const frames = data.byteLength / 2;
      const buffer = this.context.createBuffer(1, frames, this.sampleRate);
      const channel = buffer.getChannelData(0);
      const view = new DataView(data);
      for (let frame = 0; frame < frames; frame += 1) {
        channel[frame] = view.getInt16(frame * 2, true) / 0x8000;
      }

      const source = this.context.createBufferSource();
      source.buffer = buffer;
      source.connect(this.context.destination);
      const now = this.context.currentTime;
      // Reuse the app's existing 30 ms Web Audio scheduling cushion, then
      // schedule chunks against the previous chunk's end to absorb jitter.
      const startAt = this.nextStartAt === 0
        ? now + 0.03
        : Math.max(this.nextStartAt, now + 0.005);
      this.nextStartAt = startAt + buffer.duration;
      this.sources.add(source);
      source.onended = () => {
        this.sources.delete(source);
        source.disconnect();
        this.completeIfDrained();
      };
      if (!this.playbackStarted) {
        this.playbackStarted = true;
        const output = this.context.getOutputTimestamp?.();
        const startPerformanceTime = output && typeof output.performanceTime === "number" && typeof output.contextTime === "number"
          ? output.performanceTime + (startAt - output.contextTime) * 1000
          : performance.now() + (startAt - now) * 1000;
        this.callbacks.onPlaybackStart(startPerformanceTime);
      }
      source.start(startAt);
    });
    return this.chain;
  }

  start(): void {
    this.generation += 1;
    this.active = true;
    this.finished = false;
    this.expectedSequence = 0;
    this.nextStartAt = 0;
    this.playbackStarted = false;
    this.chain = Promise.resolve();
  }

  finish(): void {
    this.finished = true;
    void this.chain.then(() => this.completeIfDrained(), () => undefined);
  }

  stop(): void {
    this.generation += 1;
    this.active = false;
    this.finished = true;
    for (const source of this.sources) {
      source.onended = null;
      try {
        source.stop();
      } catch {
        // A source can finish between the active check and stop().
      }
      source.disconnect();
    }
    this.sources.clear();
  }

  private completeIfDrained(): void {
    if (!this.active || !this.finished || this.sources.size !== 0) return;
    this.active = false;
    this.callbacks.onPlaybackComplete(performance.now());
  }
}
