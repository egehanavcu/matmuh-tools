import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const SITE = "https://mtm.yildiz.edu.tr";
const OUT_DIR = "sources";
const WAYBACK_DIR = join(OUT_DIR, "wayback");
const OUT = join(OUT_DIR, "announcement-dates.json");
const SKIP_WAYBACK = process.argv.includes("--no-wayback");
const TODAY = new Date().toISOString().slice(0, 10);

const MONTHS = {
  OCA: 1, ŞUB: 2, MAR: 3, NİS: 4, MAY: 5, HAZ: 6, TEM: 7, AĞU: 8, EYL: 9, EKİ: 10, KAS: 11, ARA: 12,
  JAN: 1, FEB: 2, APR: 4, JUN: 6, JUL: 7, AUG: 8, SEP: 9, OCT: 10, NOV: 11, DEC: 12,
};
const KINDS = { announcements: "duyurular", news: "haberler" };

mkdirSync(WAYBACK_DIR, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const pad = (n) => String(n).padStart(2, "0");

async function get(url, { tries = 3, delay = 600, timeout = 60000 } = {}) {
  for (let attempt = 1; ; attempt += 1) {
    await sleep(delay);
    try {
      const res = await fetch(url, { signal: AbortSignal.timeout(timeout), headers: { "User-Agent": "matmuh-dates/1.0" } });
      const text = await res.text();
      if (res.ok) return text;
      if (attempt >= tries || res.status < 500) return null;
    } catch (error) {
      if (attempt >= tries) return null;
    }
    await sleep(2000 * attempt);
  }
}

const decode = (s) =>
  s.replace(/&nbsp;/g, " ").replace(/&amp;/g, "&").replace(/&quot;/g, '"').replace(/&#39;/g, "'")
    .replace(/&ouml;/g, "ö").replace(/&Ouml;/g, "Ö").replace(/&uuml;/g, "ü").replace(/&Uuml;/g, "Ü")
    .replace(/&ccedil;/g, "ç").replace(/&Ccedil;/g, "Ç").replace(/<[^>]+>/g, "").replace(/\s+/g, " ").trim();

const normTitle = (s) => s.toLocaleLowerCase("tr").replace(/[^\p{L}\p{N}]+/gu, " ").trim();

function parseDate(text) {
  let m = /(\d{1,2})\s+([A-ZÇĞİÖŞÜ]{3})\s+(\d{4})/u.exec(text);
  if (m && MONTHS[m[2]]) return `${m[3]}-${pad(MONTHS[m[2]])}-${pad(m[1])}`;
  m = /(\d{1,2})[./](\d{1,2})[./](\d{4})/.exec(text);
  if (m && +m[2] >= 1 && +m[2] <= 12) return `${m[3]}-${pad(m[2])}-${pad(m[1])}`;
  return null;
}

function datedLinks(html) {
  const out = [];
  const re = /href="[^"]*?\/(duyurular|haberler)\/(\d+)\/[^"]*"[\s\S]{0,700}?<small>([^<]*)<\/small>/g;
  for (const m of html.matchAll(re)) {
    const inner = m[0].slice(m[0].indexOf(">"));
    if (/href="[^"]*\/(duyurular|haberler)\/\d+\//.test(inner)) continue;
    const date = parseDate(m[3]);
    const title = decode(/<h5[^>]*>([\s\S]*?)<\/h5>/.exec(m[0])?.[1] ?? "");
    if (date) out.push({ kind: m[1] === "duyurular" ? "announcements" : "news", id: +m[2], date, title });
  }
  return out;
}

async function crawlList(kind) {
  const path = KINDS[kind];
  const items = new Map();
  for (let page = 1; page < 200; page += 1) {
    const html = await get(`${SITE}/${path}/${page}`);
    if (!html) throw new Error(`${path}/${page} alınamadı`);
    const before = items.size;
    for (const m of html.matchAll(/<span class="news-item">\s*<a href="([^"]+)">[\s\S]*?<span class="news-title">([\s\S]*?)<\/span>/g)) {
      const id = +(/\/(\d+)\/[^/]*$/.exec(m[1])?.[1] ?? NaN);
      if (Number.isFinite(id) && !items.has(id)) items.set(id, { id, title: decode(m[2]), url: m[1] });
    }
    if (items.size === before) break;
  }
  return [...items.values()].sort((a, b) => b.id - a.id);
}

async function waybackAnchors() {
  if (SKIP_WAYBACK) return { status: "atlandı", snapshots: 0, anchors: [] };
  const anchors = [];
  const timestamps = new Set();
  for (const host of ["mtm.yildiz.edu.tr", "www.mtm.yildiz.edu.tr"]) {
    const cdx = await get(`https://web.archive.org/cdx/search/cdx?url=${host}/&fl=timestamp,original&filter=statuscode:200&collapse=digest&from=2018&output=json`, { tries: 4, delay: 1500, timeout: 120000 });
    if (!cdx || !cdx.trim().startsWith("[")) return { status: "arşive ulaşılamadı", snapshots: 0, anchors };
    for (const [ts, original] of JSON.parse(cdx).slice(1)) timestamps.add(`${ts} ${original}`);
  }
  let fetched = 0;
  for (const entry of [...timestamps].sort()) {
    const [ts, original] = entry.split(" ");
    const cache = join(WAYBACK_DIR, `${ts}.html`);
    let html = existsSync(cache) ? readFileSync(cache, "utf8") : null;
    if (!html) {
      html = await get(`https://web.archive.org/web/${ts}id_/${original}`, { tries: 3, delay: 1500, timeout: 90000 });
      if (!html) continue;
      writeFileSync(cache, html);
    }
    fetched += 1;
    for (const link of datedLinks(html)) anchors.push({ ...link, snapshot: ts });
  }
  return { status: "tamam", snapshots: fetched, listed: timestamps.size, anchors };
}

const toDays = (d) => Date.parse(`${d}T00:00:00Z`) / 86400000;
const fromDays = (n) => new Date(Math.round(n) * 86400000).toISOString().slice(0, 10);

function termWindow(title) {
  const t = title.toLocaleLowerCase("tr");
  const m = /(20\d{2})\s*-\s*(20\d{2})/.exec(t);
  if (!m || +m[2] !== +m[1] + 1) return null;
  const [y1, y2] = [m[1], m[2]];
  if (/güz/.test(t)) return [`${y1}-07-01`, `${y2}-02-28`];
  if (/bahar/.test(t)) return [`${y2}-01-01`, `${y2}-07-31`];
  if (/yaz\b|yaz okulu/.test(t)) return [`${y2}-05-01`, `${y2}-09-30`];
  return [`${y1}-06-01`, `${y2}-09-30`];
}

const clampDate = (d, [lo, hi]) => (d < lo ? lo : d > hi ? hi : d);

function monotonic(rows) {
  const tails = [];
  const prev = new Array(rows.length).fill(-1);
  for (let i = 0; i < rows.length; i += 1) {
    let lo = 0;
    let hi = tails.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (rows[tails[mid]].publishedAt <= rows[i].publishedAt) lo = mid + 1;
      else hi = mid;
    }
    if (lo > 0) prev[i] = tails[lo - 1];
    tails[lo] = i;
  }
  const out = [];
  for (let i = tails.at(-1) ?? -1; i >= 0; i = prev[i]) out.unshift(rows[i]);
  return out;
}

function resolve(kind, items, homepage, wayback) {
  const byId = new Map(items.map((i) => [i.id, { ...i, candidates: [] }]));
  for (const a of homepage) if (a.kind === kind && byId.has(a.id)) byId.get(a.id).candidates.push({ source: "homepage", date: a.date });
  for (const a of wayback) {
    const row = a.kind === kind ? byId.get(a.id) : null;
    if (!row) continue;
    if (a.title && normTitle(a.title) !== normTitle(row.title)) {
      row.reusedFrom ??= [];
      if (!row.reusedFrom.some((r) => r.title === a.title)) row.reusedFrom.push({ title: a.title, date: a.date, snapshot: a.snapshot });
      continue;
    }
    row.candidates.push({ source: "wayback", date: a.date, snapshot: a.snapshot });
  }

  const rows = [...byId.values()].sort((a, b) => a.id - b.id);
  for (const row of rows) {
    const dates = [...new Set(row.candidates.map((c) => c.date))];
    const best = [...row.candidates].sort((a, b) => a.date.localeCompare(b.date))[0];
    row.publishedAt = best?.date ?? null;
    row.source = best?.source ?? null;
    row.conflict = dates.length > 1 ? dates : undefined;
  }

  const anchors = monotonic(rows.filter((r) => r.publishedAt));
  const anchorIds = new Set(anchors.map((a) => a.id));
  for (const row of rows) if (row.publishedAt && !anchorIds.has(row.id)) row.notAnchor = true;
  for (const row of rows) {
    if (row.publishedAt) continue;
    const prev = anchors.filter((a) => a.id < row.id).at(-1);
    const next = anchors.find((a) => a.id > row.id);
    if (prev && next) {
      const t = (row.id - prev.id) / (next.id - prev.id);
      row.estimate = fromDays(toDays(prev.publishedAt) + t * (toDays(next.publishedAt) - toDays(prev.publishedAt)));
      row.range = [prev.publishedAt, next.publishedAt];
    } else if (next) {
      row.range = [null, next.publishedAt];
    } else if (prev) {
      row.range = [prev.publishedAt, TODAY];
    }
    const term = termWindow(row.title);
    if (term) {
      row.term = term;
      const lo = row.range?.[0] && row.range[0] > term[0] ? row.range[0] : term[0];
      const hi = row.range?.[1] && row.range[1] < term[1] ? row.range[1] : term[1];
      if (lo <= hi) {
        row.estimate = row.estimate ? clampDate(row.estimate, [lo, hi]) : fromDays((toDays(lo) + toDays(hi)) / 2);
        row.range = [lo, hi];
      } else {
        row.termMismatch = true;
        row.estimate = row.estimate ? clampDate(row.estimate, term) : fromDays((toDays(term[0]) + toDays(term[1])) / 2);
        row.range = term;
      }
    }
    row.publishedAt = row.estimate ?? null;
    row.source = row.estimate ? (row.termMismatch ? "term" : term ? "interpolated+term" : "interpolated") : null;
  }
  for (const row of rows) {
    const term = termWindow(row.title);
    if (!term || !row.publishedAt || row.source?.startsWith("interpolated") || row.source === "term") continue;
    const slack = 90;
    if (toDays(row.publishedAt) < toDays(term[0]) - slack || toDays(row.publishedAt) > toDays(term[1]) + slack) row.termMismatch = true;
  }

  const out = rows.sort((a, b) => b.id - a.id).map(({ candidates, ...r }) => ({
    ...r,
    exact: r.source === "homepage" || r.source === "wayback",
    candidates,
  }));
  return out;
}

console.log("listeler çekiliyor…");
const lists = {};
for (const kind of Object.keys(KINDS)) {
  lists[kind] = await crawlList(kind);
  console.log(`  ${kind.padEnd(14)} ${lists[kind].length} kayıt (numara ${lists[kind].at(-1)?.id}–${lists[kind][0]?.id})`);
}

const home = await get(`${SITE}/`);
const homepage = home ? datedLinks(home) : [];
console.log(`ana sayfa: ${homepage.length} tarihli bağlantı`);

console.log("wayback…");
const wb = await waybackAnchors();
console.log(`  ${wb.status}; ${wb.snapshots} kopya, ${wb.anchors.length} tarihli bağlantı`);

const result = { generatedAt: new Date().toISOString(), wayback: { status: wb.status, snapshots: wb.snapshots }, collections: {} };
for (const kind of Object.keys(KINDS)) {
  const rows = resolve(kind, lists[kind], homepage, wb.anchors);
  const count = (fn) => rows.filter(fn).length;
  const summary = {
    total: rows.length,
    homepage: count((r) => r.source === "homepage"),
    wayback: count((r) => r.source === "wayback"),
    interpolated: count((r) => r.source === "interpolated"),
    interpolatedWithTerm: count((r) => r.source === "interpolated+term"),
    termOnly: count((r) => r.source === "term"),
    exactNotAnchor: count((r) => r.notAnchor),
    termMismatch: count((r) => r.termMismatch),
    unknown: count((r) => !r.publishedAt),
    conflicts: count((r) => r.conflict),
    reusedId: count((r) => r.reusedFrom),
  };
  result.collections[kind] = { summary, items: rows };
  console.log(`\n${kind}`);
  for (const [k, v] of Object.entries(summary)) console.log(`  ${k.padEnd(16)} ${v}`);
}

writeFileSync(OUT, JSON.stringify(result, null, 1));
console.log(`\nyazıldı: ${OUT}`);
