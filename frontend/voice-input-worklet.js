class QimingPcmCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.chunk = new Float32Array(4096);
    this.offset = 0;
    this.port.onmessage = event => {
      if (event.data?.type !== 'flush') return;
      this.flush();
      this.port.postMessage({type: 'flushed'});
    };
  }

  flush() {
    if (!this.offset) return;
    const samples = this.chunk.slice(0, this.offset);
    this.offset = 0;
    this.port.postMessage({type: 'audio', samples}, [samples.buffer]);
  }

  process(inputs, outputs) {
    const input = inputs[0]?.[0];
    if (input) {
      let sourceOffset = 0;
      while (sourceOffset < input.length) {
        const count = Math.min(this.chunk.length - this.offset, input.length - sourceOffset);
        this.chunk.set(input.subarray(sourceOffset, sourceOffset + count), this.offset);
        this.offset += count;
        sourceOffset += count;
        if (this.offset === this.chunk.length) this.flush();
      }
    }
    for (const channel of outputs[0] || []) channel.fill(0);
    return true;
  }
}

registerProcessor('qiming-pcm-capture', QimingPcmCapture);
