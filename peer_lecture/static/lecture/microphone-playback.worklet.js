// Latency-first PCM playback for both direct data channels and the relay.
// Never grow a jitter cushion: brief gaps are preferable to delayed speech.
const SIZE = 1 << 16, MASK = SIZE - 1;
const MOST = 0.06, PRIME = 0.02;
class ClassroomPlayback extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ring = new Float32Array(SIZE);
    this.deadlines = new Float64Array(SIZE);
    this.epoch = 0;
    this.reset();
    this.port.onmessage = ({data}) => data.samples ? this.push(data) : (this.epoch = data.epoch, this.reset());
  }
  reset() {
    this.write = this.read = this.rate = 0;
    this.playing = false; this.fade = 0; this.last = 0;
    this.underruns = this.dropped = this.expired = this.maxBuffered = 0;
    this.sinceReport = 0;
  }
  queued() { return this.rate ? Math.max(0, this.write - this.read) / this.rate : 0; }
  push({samples, rate, deadline, epoch}) {
    if (epoch !== this.epoch) return;
    if (!Number.isFinite(deadline) || deadline <= currentTime) { this.expired++; return; }
    if (this.rate && rate !== this.rate) this.reset();
    this.rate = rate;
    for (let i = 0; i < samples.length; i++) {
      const index = this.write++ & MASK;
      this.ring[index] = samples[i]; this.deadlines[index] = deadline;
    }
    this.maxBuffered = Math.max(this.maxBuffered, this.queued() * 1000);
    if (this.queued() > MOST) {
      this.read = this.write - Math.round(PRIME * rate);
      this.fade = 0; this.dropped++;
    }
  }
  process(inputs, outputs) {
    const output = outputs[0]?.[0];
    if (!output) return true;
    for (let i = 0; i < output.length; i++) {
      const now = currentTime + i / sampleRate;
      let expired = false;
      while (this.read < this.write && this.deadlines[Math.floor(this.read) & MASK] <= now) {
        this.read = Math.floor(this.read) + 1; expired = true;
      }
      if (expired) { this.expired++; this.fade = 0; }
      if (!this.playing && this.rate && this.queued() >= PRIME - 0.00001) {
        this.playing = true; this.fade = 0;
      }
      const index = Math.floor(this.read);
      if (this.playing && this.write - index < 2) {
        this.playing = false; this.underruns++;
      }
      if (!this.playing) { this.last *= 0.95; output[i] = this.last; continue; }
      const a = this.ring[index & MASK], b = this.ring[(index + 1) & MASK];
      this.fade = Math.min(1, this.fade + 1 / (sampleRate * 0.004));
      output[i] = this.last = (a + (b - a) * (this.read - index)) * this.fade;
      this.read += this.rate / sampleRate;
    }
    if ((this.sinceReport += output.length / sampleRate) >= 0.1) {
      this.sinceReport = 0;
      this.port.postMessage({epoch: this.epoch, buffered: this.queued() * 1000, cushion: 0,
        maxBuffered: this.maxBuffered, underruns: this.underruns, dropped: this.dropped,
        expired: this.expired, playing: this.playing});
    }
    return true;
  }
}
registerProcessor('classroom-playback', ClassroomPlayback);
