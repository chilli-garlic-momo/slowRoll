const FLAG = process.env.FLAG_NAME || 'new_checkout';
const CHAOS = process.env.CHAOS_FLAG || 'inject_errors';
const CACHE_MS = 10000;
const cache = new Map();

async function flagsFor(uid) {
  const hit = cache.get(uid);
  if (hit && Date.now() - hit.at < CACHE_MS) return hit.flags;
  const res = await fetch(`${process.env.FLAGSMITH_API_URL}/api/v1/identities/`, {
    method: 'POST',
    headers: {
      'X-Environment-Key': process.env.FLAGSMITH_ENVIRONMENT_KEY,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ identifier: uid, traits: [] }),
    signal: AbortSignal.timeout(5000),
  });
  if (!res.ok) throw new Error(`flagsmith ${res.status}`);
  const data = await res.json();
  const flags = Object.fromEntries(data.flags.map((f) => [f.feature.name, f.enabled]));
  if (cache.size > 2000) cache.clear();
  cache.set(uid, { at: Date.now(), flags });
  return flags;
}

async function count(variant, failed) {
  const bucket = Math.floor(Date.now() / 10000);
  const kinds = failed ? ['req', 'err'] : ['req'];
  const commands = kinds.flatMap((kind) => {
    const key = `sr:${variant}:${kind}:${bucket}`;
    return [['INCR', key], ['EXPIRE', key, 180]];
  });
  await fetch(`${process.env.UPSTASH_REDIS_REST_URL}/pipeline`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${process.env.UPSTASH_REDIS_REST_TOKEN}` },
    body: JSON.stringify(commands),
    signal: AbortSignal.timeout(5000),
  });
}

module.exports = async (req, res) => {
  const uid = String(req.query.uid || 'anonymous').slice(0, 64);
  let flags;
  try {
    flags = await flagsFor(uid);
  } catch (e) {
    return res.status(200).json({ uid, variant: 'control', ok: true, degraded: true });
  }
  const variant = flags[FLAG] ? 'treatment' : 'control';
  const failed = variant === 'treatment' && flags[CHAOS] === true;
  await count(variant, failed).catch(() => {});
  res.status(failed ? 500 : 200).json({ uid, variant, ok: !failed });
};
