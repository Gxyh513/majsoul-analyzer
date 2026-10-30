export function createSseByteMeter() {
  let frame = null;
  let lineHasContent = false;

  function kind(prefix) {
    const complete = /"type"\s*:\s*"complete"/.test(prefix) || /(?:^|[\r\n])event:\s*complete\s*[\r\n]/.test(prefix);
    if (complete) return "complete";
    const progress = /"type"\s*:\s*"progress"/.test(prefix) || /(?:^|[\r\n])event:\s*progress\s*[\r\n]/.test(prefix);
    return progress && /"percent"\s*:\s*100\s*[,}]/.test(prefix) ? "100%" : null;
  }

  function add(chunk, chunkIndex, receivedAt) {
    const started = [];
    const finished = [];
    let offset = 0;
    while (offset < chunk.length) {
      const newline = chunk.indexOf(10, offset);
      const end = newline < 0 ? chunk.length : newline + 1;
      if (!frame) frame = { bytes: 0, prefix: "", firstChunk: chunkIndex, firstAt: receivedAt };
      frame.bytes += end - offset;
      frame.lastChunk = chunkIndex;
      frame.lastAt = receivedAt;
      if (!frame.kind) {
        const prefixEnd = Math.min(end, offset + Math.max(0, 256 - frame.prefix.length));
        for (let i = offset; i < prefixEnd; i++) frame.prefix += String.fromCharCode(chunk[i]);
      }
      if (!frame.kind) {
        frame.kind = kind(frame.prefix);
        if (frame.kind) {
          frame.prefix = "";
          started.push(frame);
        }
      }
      const contentEnd = newline < 0 ? end : newline;
      if (contentEnd - offset > 1 || (contentEnd - offset === 1 && chunk[offset] !== 13)) lineHasContent = true;
      if (newline >= 0) {
        if (!lineHasContent) {
          if (frame.kind) finished.push(frame);
          frame = null;
        }
        lineHasContent = false;
      }
      offset = end;
    }
    return { started, finished };
  }

  function forwarded(chunkIndex, at, finished) {
    for (const item of finished) {
      if (item.firstChunk === chunkIndex) item.firstForwardAt = at;
      item.lastForwardAt = at;
    }
    if (frame && frame.firstChunk === chunkIndex) frame.firstForwardAt = at;
  }

  return { add, forwarded };
}
