// Runs on the student's phone. The same processed signal feeds WebRTC and the
// fallback relay: a speech gate, a capped level control, and a peak limiter.
//
// Browser automatic gain control is off because it raises the gain whenever
// the student pauses, which turns up the room noise and the loudspeakers'
// own sound -- the loop that howls. Here the gain only moves while the
// student is speaking, is capped, and the feedback guard can pull it down.
const GATE_FLOOR = 10 ** (-24 / 20);  // Room sound between phrases, 24 dB down.
const GATE_HOLD = 0.3;                // Seconds kept open after speech, for word endings.
const SPEECH_TARGET = -26;            // dBFS block level the level control aims for.
const MAX_BOOST = 0;                  // dB; never automatically amplify the room or speaker return.
const MAX_CUT = -12;

const decibels = power => 10 * Math.log10(power + 1e-12);
const coefficient = seconds => 1 - Math.exp(-1 / (sampleRate * seconds));

class ClassroomCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.frame = new Int16Array(sampleRate / 50);
    this.offset = 0;
    this.frameTime = 0;
    this.limit = 1;
    this.limitRelease = coefficient(0.06);
    this.floor = -50;        // Noise floor estimate, dBFS.
    this.open = false;
    this.hold = 0;
    this.gate = GATE_FLOOR;
    this.gateAttack = coefficient(0.002);
    this.gateRelease = coefficient(0.15);
    this.speech = null;      // Speaking level estimate, dBFS.
    this.level = 0;          // Level-control gain, dB.
    this.backoff = 0;        // dB taken off by the feedback guard.
    this.steady = 0;         // dB taken off for a sound too steady to be speech.
    this.span = [Infinity, -Infinity, 0];
    this.gain = 1;
    this.gainSmoothing = coefficient(0.03);
    this.fadeIn = 0;
    this.fadeStep = 1 / (sampleRate * 0.15);
    this.port.onmessage = ({ data }) => {
      if (typeof data?.backoff === 'number') this.backoff = Math.max(-12, Math.min(0, data.backoff));
      if (data?.reset) { this.backoff = this.steady = 0; this.span = [Infinity, -Infinity, 0]; }
    };
  }

  measure(input) {
    let power = 0;
    for (let i = 0; i < input.length; i++) power += input[i] * input[i];
    const seconds = input.length / sampleRate;
    // A 20 ms envelope: single blocks of room noise jump around by 10 dB.
    this.power = this.power === undefined ? power / input.length
      : this.power + (power / input.length - this.power) * (1 - Math.exp(-seconds / 0.02));
    const level = decibels(this.power);
    // The floor falls quickly to quieter moments and creeps up slowly, more
    // slowly still while someone talks, so speech is not mistaken for noise.
    if (level < this.floor) this.floor += (level - this.floor) * (1 - Math.exp(-seconds / 0.1));
    else this.floor = Math.min(level, this.floor + (this.open ? 0.5 : 3) * seconds);
    this.floor = Math.max(-80, Math.min(-35, this.floor));
    const opensAt = Math.max(-60, Math.min(-30, this.floor + 10));
    if (level > (this.open ? opensAt - 4 : opensAt)) {
      this.open = true;
      this.hold = GATE_HOLD;
    } else if ((this.hold -= seconds) <= 0) {
      this.open = false;
    }
    // Speech swings by well over 8 dB between syllables within any second
    // and a half. A gate held open by a level that does not is a howl spread
    // over many frequencies (the phone hears the loudspeakers about as loudly
    // as the student), or a machine; neither is cured by a notch.
    if (this.open) {
      this.span = [Math.min(this.span[0], level), Math.max(this.span[1], level), this.span[2] + seconds];
      if (this.span[2] >= 1.5) {
        if (this.span[1] - this.span[0] < 8 && this.steady > -12) {
          this.steady -= 3;
          this.port.postMessage({ steady: this.steady });
        }
        this.span = [Infinity, -Infinity, 0];
      }
    } else {
      this.span = [Infinity, -Infinity, 0];
    }
    // Level only to what is clearly a voice, never to noise that slipped past the gate.
    if (level > opensAt + 6) {
      this.speech = this.speech === null ? level : this.speech + (level - this.speech) * (1 - Math.exp(-seconds / 0.4));
      const wanted = Math.max(MAX_CUT, Math.min(MAX_BOOST, SPEECH_TARGET - this.speech));
      const step = (wanted > this.level ? 1 : 12) * seconds;
      this.level += Math.max(-step, Math.min(step, wanted - this.level));
    }
    return 10 ** ((this.level + Math.max(-12, this.backoff + this.steady)) / 20);
  }

  process(inputs, outputs) {
    const input = inputs[0]?.[0];
    const output = outputs[0]?.[0];
    if (!input || !output) return true;
    const makeup = this.measure(input);
    const gateTarget = this.open ? 1 : GATE_FLOOR;
    const gateRate = this.open ? this.gateAttack : this.gateRelease;
    for (let i = 0; i < input.length; i++) {
      this.gate += (gateTarget - this.gate) * gateRate;
      this.gain += (makeup - this.gain) * this.gainSmoothing;
      this.fadeIn = Math.min(1, this.fadeIn + this.fadeStep);
      const sample = input[i] * this.gate * this.gain * this.fadeIn;
      const target = Math.min(1, 0.85 / Math.max(0.85, Math.abs(sample)));
      this.limit = target < this.limit ? target : this.limit + (target - this.limit) * this.limitRelease;
      const value = sample * this.limit;
      output[i] = value;
      if (!this.offset) this.frameTime = currentTime + i / sampleRate;
      this.frame[this.offset++] = Math.round(value * 32767);
      if (this.offset === this.frame.length) {
        this.port.postMessage({ pcm: this.frame.buffer, time: this.frameTime, levelDb: this.level }, [this.frame.buffer]);
        this.frame = new Int16Array(sampleRate / 50);
        this.offset = 0;
      }
    }
    return true;
  }
}
registerProcessor('classroom-capture', ClassroomCapture);
