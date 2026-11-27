const WINDOW = Number(process.env.WINDOW_SECONDS || 60);
const MIN_SAMPLES = Number(process.env.MIN_SAMPLES || 10);
const MAX_ERR_DELTA = Number(process.env.MAX_ERR_DELTA || 0.05);

module.exports = async (req, res) => {
  res.setHeader('Cache-Control', 'no-store');
  const buckets = Math.max(1, Math.round(WINDOW / 10));
  const now = Math.floor(Date.now() / 10000);
  const names = [];
  for (const variant of ['control', 'treatment']) {
    for (const kind of ['req', 'err']) {
      for (let i = 0; i < buckets; i++) names.push(`sr:${variant}:${kind}:${now - i}`);
    }
  }
  let values;
  try {
    const r = await fetch(`${process.env.UPSTASH_REDIS_REST_URL}/pipeline`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${process.env.UPSTASH_REDIS_REST_TOKEN}` },
      body: JSON.stringify([['MGET', ...names]]),
      signal: AbortSignal.timeout(5000),
    });
    if (!r.ok) throw new Error(`upstash ${r.status}`);
    values = (await r.json())[0].result;
  } catch (e) {
    return res.status(200).json({ healthy: null, detail: 'counter store unreachable' });
  }
  const sum = (offset) => values.slice(offset, offset + buckets).reduce((a, v) => a + Number(v || 0), 0);
  const counts = {
    control: { req: sum(0), err: sum(buckets) },
    treatment: { req: sum(buckets * 2), err: sum(buckets * 3) },
  };
  const rate = (c) => (c.req ? c.err / c.req : 0);
  const pct = (c) => `${c.err}/${c.req} (${(rate(c) * 100).toFixed(1)}%)`;
  let healthy = null;
  let detail = `treatment has ${counts.treatment.req} requests in the last ${WINDOW}s, fewer than ${MIN_SAMPLES}`;
  if (counts.treatment.req >= MIN_SAMPLES) {
    healthy = !(rate(counts.treatment) - rate(counts.control) > MAX_ERR_DELTA);
    detail = `treatment errors ${pct(counts.treatment)} vs control ${pct(counts.control)} over ${WINDOW}s`;
  }
  res.status(200).json({ healthy, detail, counts, window_seconds: WINDOW });
};
