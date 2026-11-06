"""Synthetic signal timing through speech processing, WebRTC and media playback.

Requires Playwright + Chromium. No real microphones, accounts, or audio files.
Reports software loopback timing only; physical capture, speaker and room delay
are excluded. It supplements (and must not be confused with) packet statistics.
"""
import asyncio
import json
import os

import classroom_microphone_acoustics as harness
from playwright.async_api import async_playwright


PROBE = r'''async () => {
  const {SpeechChain, RelayPlayback, generationBytes} = await import('/lecture/microphone-audio.js');
  const sleep = ms => new Promise(r => setTimeout(r,ms));
  const context = new AudioContext({sampleRate:24000,latencyHint:'interactive'}); await context.resume();
  await SpeechChain.load(context);
  const buffer = context.createBuffer(1, context.sampleRate * 12, context.sampleRate);
  const samples = buffer.getChannelData(0);
  for(let i=0;i<samples.length;i++) {
    const t=i/context.sampleRate, p=t%0.8;
    if(p<0.12) samples[i]=0.3*Math.sin(2*Math.PI*(700*p+1000*p*p));
  }
  const source=context.createBufferSource(); source.buffer=buffer;
  const chain=new SpeechChain(context,source);
  const stream=context.createMediaStreamDestination(); chain.worklet.connect(stream);
  const silent=context.createGain(); silent.gain.value=0; chain.worklet.connect(silent).connect(context.destination);
  const token = generationBytes('12345678-1234-1234-1234-123456789012');
  let sequence=0, channel;
  const sent=[],received=[]; let prev= -Infinity;
  chain.onFrame=frame=>{
    if(channel?.readyState === 'open') {
      const packet=new ArrayBuffer(32+frame.pcm.byteLength), view=new DataView(packet);
      new Uint8Array(packet,0,16).set(token); view.setUint32(16,sequence++);
      view.setFloat64(20,Date.now()-(context.currentTime-frame.time)*1000); view.setUint32(28,context.sampleRate);
      new Uint8Array(packet,32).set(new Uint8Array(frame.pcm)); channel.send(packet);
    }
    const pcm=new Int16Array(frame.pcm);
    for(let i=0;i<pcm.length;i++) if(Math.abs(pcm[i])>650) {
      const t=frame.time+i/context.sampleRate;
      if(t-prev>0.3) sent.push(performance.now()-(context.currentTime-t)*1000);
      prev=t;
    }
  };
  const a=new RTCPeerConnection({iceServers:[]}), b=new RTCPeerConnection({iceServers:[]});
  const ca=[],cb=[];
  a.onicecandidate=e=>{if(e.candidate){ if(b.remoteDescription)b.addIceCandidate(e.candidate); else cb.push(e.candidate); }};
  b.onicecandidate=e=>{if(e.candidate){ if(a.remoteDescription)a.addIceCandidate(e.candidate); else ca.push(e.candidate); }};
  const out=new AudioContext({latencyHint:'interactive'}); await out.resume();
  await out.audioWorklet.addModule(URL.createObjectURL(new Blob([`class D extends AudioWorkletProcessor {
    constructor(){super();this.previous=-Infinity;}
    process(inputs,outputs){const a=inputs[0]?.[0]; if(a)for(let i=0;i<a.length;i++)if(Math.abs(a[i])>0.02){const t=currentTime+i/sampleRate;if(t-this.previous>0.3)this.port.postMessage(t);this.previous=t;}return true;}
  } registerProcessor('detector',D);`],{type:'application/javascript'})));
  const detector=new AudioWorkletNode(out,'detector'); detector.connect(out.destination);
  detector.port.onmessage=({data:t})=>received.push(performance.now()-(out.currentTime-t)*1000);
  const playback = new RelayPlayback(out,detector); await playback.ready;
  let linked=false;
  b.ondatachannel=e=>{e.channel.binaryType='arraybuffer';e.channel.onmessage=({data})=>playback.play(data,token,Date.now()); e.channel.onopen=()=>{linked=true;};};
  channel=a.createDataChannel('agora-pcm-v1',{ordered:false,maxRetransmits:0});
  await a.setLocalDescription(await a.createOffer()); await b.setRemoteDescription(a.localDescription);
  for(const c of cb)await b.addIceCandidate(c);
  await b.setLocalDescription(await b.createAnswer());await a.setRemoteDescription(b.localDescription);
  for(const c of ca)await a.addIceCandidate(c);
  for(let i=0;i<100&&!linked;i++)await sleep(50);
  const start = context.currentTime + 0.1;
  const injected = performance.now() + (start - context.currentTime) * 1000;
  source.start(start); await sleep(11000);

  const result={sent:sent.length, received:received.length, delays:received.map((t,i)=>Math.round(t-(injected+i*800))), captureRate:context.sampleRate, captureBaseLatency:context.baseLatency, outputLatency:out.outputLatency, outputBaseLatency:out.baseLatency, playbackBufferMs:playback.stats.buffered};
  a.close();b.close();await context.close();await out.close();return result;
}'''
async def main(port):
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),
            args=['--autoplay-policy=no-user-gesture-required'],
        )
        page = await browser.new_page()
        await page.goto(f'http://127.0.0.1:{port}/lecture/microphone.css')
        result = await page.evaluate(PROBE)
        print(json.dumps(result), flush=True)
        assert result['sent'] == result['received'] and result['received'] >= 12, result
        assert all(0 < delay < 150 for delay in result['delays']), result
        print('PASS: synthetic software loopback stays below 150 ms; this excludes physical devices and the room', flush=True)
        await browser.close()


if __name__ == '__main__':
    server = harness.serve()
    try:
        asyncio.run(main(server.server_address[1]))
    finally:
        server.shutdown()
