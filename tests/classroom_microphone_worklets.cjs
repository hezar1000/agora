// Five minutes of simulated stalled/bursty delivery, using the production worklet.
// No network, real microphone, or classroom is involved.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const file = path.join(__dirname, '../peer_lecture/static/lecture/microphone-playback.worklet.js');
let Processor;
const sandbox = {sampleRate: 24000, currentTime: 0,
  AudioWorkletProcessor: class { constructor() {this.port = {postMessage() {}};} },
  registerProcessor: (_, cls) => {Processor = cls;}};
vm.createContext(sandbox); vm.runInContext(fs.readFileSync(file, 'utf8'), sandbox);
const player = new Processor(), block = new Float32Array(240);
let held = [], maxQueued = 0;
const push = (born, now) => player.push({samples: new Float32Array(480).fill(.2), rate: 24000, deadline: born + .12, epoch: 0});
for (let tick = 0; tick < 30000; tick++) {
  const now = sandbox.currentTime = tick / 100;
  if (tick % 2 === 0) {
    // A 400 ms outage every two seconds; otherwise normal 20 ms frames.
    if (tick % 200 < 40) held.push(now); else push(now, now);
  }
  if (tick % 200 === 40) { for (const born of held) push(born, now); held = []; }
  player.process([], [[block]]);
  maxQueued = Math.max(maxQueued, player.queued());
  assert(player.queued() <= .06001, 'backlog must stay capped across repeated outages');
  for (const value of block) assert(Number.isFinite(value) && Math.abs(value) <= .21);
}
assert(player.expired > 100 && player.dropped > 100 && player.underruns > 100);
player.port.onmessage({data: {clear: true, epoch: 1}});
push(301,301); // A stale generation cannot repopulate the cleared player.
player.process([], [[block]]);
assert(block.every(value => value === 0), 'mute must clear residual audio');
assert.equal(player.queued(), 0);
console.log('PASS: five simulated minutes of repeated 400 ms stalls; queue <=', Math.round(maxQueued*1000), 'ms; stale audio skipped; mute stays silent');
