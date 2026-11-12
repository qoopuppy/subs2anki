/* Subs2Anki popup：探测页面 -> 抓字幕 -> 预览 -> 一键交给本地服务制卡。
 * 本地服务没开时自动降级成「纯文字 + 时间戳跳转链接」的 TSV，插件单独也能用。
 */
const $ = (id) => document.getElementById(id);

const state = {
  probe: null, tracks: [], cues: [], transCues: [], sentences: [],
  serverOnline: false, busy: false, hints: { server: '', track: '' },
  serverCheck: Promise.resolve(),
  biliMeta: null, page: 1, diagLines: []
};

function send(msg) {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage(msg, (res) => resolve(res || { ok: false, error: '后台无响应' }));
  });
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const esc = (s) => String(s || '').replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const clock = (s) => `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(Math.floor(s % 60)).padStart(2, '0')}`;

function setHint(key, text) {
  state.hints[key] = text || '';
  const parts = [state.hints.server, state.hints.track].filter(Boolean);
  const el = $('setupHint');
  el.innerHTML = parts.map((p) => esc(p)).join('<br>');
  el.classList.toggle('hidden', !parts.length);
}
// 字幕轨相关提示走 track 通道，避免与服务状态提示互相覆盖
const hint = (text) => setHint('track', text);
function setStatus(cls, text) {
  const el = $('serverStatus');
  el.className = 'status ' + (cls || '');
  el.querySelector('span').textContent = text;
}
function setParam(u, key, value) {
  try {
    const url = new URL(u, location.href);
    url.searchParams.set(key, value);
    return url.toString();
  } catch (e) {
    return u + (u.includes('?') ? '&' : '?') + key + '=' + encodeURIComponent(value);
  }
}
function tsUrl(url, sec) {
  try {
    const u = new URL(url);
    u.searchParams.set('t', /youtube|youtu\.be/.test(u.hostname)
      ? Math.floor(sec) + 's' : String(Math.floor(sec)));
    return u.toString();
  } catch (e) { return url; }
}
function diag(line) {
  state.diagLines.push(String(line));
  const el = $('diagText');
  if (el) el.textContent = state.diagLines.join('\n');
}
// 去掉 trackid / spm_id_from / vd_source 等推荐流参数，并明确指定分 P，
// 否则 yt-dlp 会把整个分 P 当合集处理，音频与字幕可能对不上。
function videoUrl(raw, platform, page) {
  try {
    const u = new URL(raw);
    const v = u.searchParams.get('v');
    const p = u.searchParams.get('p');
    u.search = '';
    if (platform === 'youtube' && v) u.searchParams.set('v', v);
    if (platform === 'bilibili') u.searchParams.set('p', String(page || 1));
    else if (p) u.searchParams.set('p', p);
    return u.toString();
  } catch (e) { return raw; }
}

/* ---------- 字幕解析（归一化成 {start,end,text}） ---------- */
function clean(s) {
  return String(s || '').replace(/<[^>]+>/g, '').replace(/\[[^\]]{1,20}\]/g, '')
    .replace(/\s+/g, ' ').replace(/^[ ♪♫\s]+|[ ♪♫\s]+$/g, '').trim();
}
function timecode(v) {
  const p = v.replace(',', '.').split(':').map(Number);
  return p.length === 3 ? p[0] * 3600 + p[1] * 60 + p[2]
    : p.length === 2 ? p[0] * 60 + p[1] : p[0];
}
function parseYoutubeJson3(text) {
  const cues = [];
  let data;
  try { data = JSON.parse(text); } catch (e) { return cues; }
  for (const ev of data.events || []) {
    const body = clean((ev.segs || []).map((s) => s.utf8 || '').join(''));
    if (!body) continue;
    const start = (ev.tStartMs || 0) / 1000;
    const end = start + (ev.dDurationMs || 0) / 1000;
    const prev = cues[cues.length - 1];
    if (ev.aAppend && prev) {
      prev.text = (prev.text + body).trim(); prev.end = Math.max(prev.end, end); continue;
    }
    if (prev && body.length > 3 && (prev.text.includes(body) || body.includes(prev.text))) {
      prev.text = body; prev.end = Math.max(prev.end, end); continue;
    }
    cues.push({ start: start, end: end, text: body });
  }
  return cues;
}
// timedtext 未指定 fmt 时返回 XML，做一层兜底
function parseTimedtextXml(text) {
  const cues = [];
  const re = /<text\s+([^>]*)>([\s\S]*?)<\/text>/g;
  let m;
  while ((m = re.exec(text)) !== null) {
    const start = Number((m[1].match(/start="([\d.]+)"/) || [])[1] || 0);
    const dur = Number((m[1].match(/dur="([\d.]+)"/) || [])[1] || 1);
    const body = clean(m[2].replace(/&amp;/g, '&').replace(/&lt;/g, '<')
      .replace(/&gt;/g, '>').replace(/&#39;/g, "'").replace(/&quot;/g, '"'));
    if (body) cues.push({ start: start, end: start + (dur || 1), text: body });
  }
  return cues;
}
function parseBiliJson(text) {
  let data;
  try { data = JSON.parse(text); } catch (e) { return []; }
  return (data.body || []).map((b) => ({
    start: Number(b.from) || 0, end: Number(b.to) || 0, text: clean(b.content)
  })).filter((c) => c.text);
}
function parseSrtLike(text) {
  const cues = [];
  for (const block of text.trim().split(/\n\s*\n/)) {
    const lines = block.split('\n').filter((l) => l.trim());
    const i = lines.findIndex((l) => l.includes('-->'));
    if (i < 0 || /^(WEBVTT|NOTE|STYLE)/.test(lines[0])) continue;
    const m = lines[i].match(/([\d:.,]+)\s*-->\s*([\d:.,]+)/);
    if (!m) continue;
    const body = clean(lines.slice(i + 1).join(' '));
    if (body) cues.push({ start: timecode(m[1]), end: timecode(m[2]), text: body });
  }
  return cues;
}
function parseCues(text, format) {
  let cues;
  if (format === 'youtube_json3') {
    cues = parseYoutubeJson3(text);
    if (!cues.length) cues = parseTimedtextXml(text);
  } else if (format === 'bilibili_json') cues = parseBiliJson(text);
  else {
    const head = text.trim().slice(0, 120);
    if (head.startsWith('<')) cues = parseTimedtextXml(text);
    else if (head.startsWith('{') && head.includes('"events"')) cues = parseYoutubeJson3(text);
    else if (head.startsWith('{') && head.includes('"body"')) cues = parseBiliJson(text);
    else cues = parseSrtLike(text);
  }
  return cues.sort((a, b) => a.start - b.start).filter((c) => c.text);
}

/* ---------- 前端断句（预览 + 纯 TSV 降级用，规则与服务端一致） ---------- */
const isCJK = (ch) => ch && /[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af\uff00-\uffef]/.test(ch);
function joinParts(parts) {
  let out = '';
  for (const raw of parts) {
    const p = String(raw || '').trim();
    if (!p) continue;
    if (!out) out = p;
    else out += (isCJK(out.slice(-1)) || isCJK(p[0])) ? p : ' ' + p;
  }
  return out.replace(/\s+([,.!?;:])/g, '$1').trim();
}
function endsSentence(text, lang) {
  const t = text.trim();
  if (!t) return false;
  if ('。！？!?…；;'.includes(t.slice(-1))) return true;
  const core = t.replace(/["'」』）)\]〉》]+$/, '');
  if (core && '。！？!?…；;'.includes(core.slice(-1))) return true;
  if (t.endsWith('.') && !/(?:^|\s)[A-Za-z]\.$|\d\.\d?$/.test(t)) return true;
  if ((lang === 'ja' || lang === 'auto') && t.length >= 8 &&
      /(?:です|ます|ました|ません|でした|ください|ましょう|だった|だろう|ですね|ますね)$/.test(t)) return true;
  return false;
}
function segmentCues(cues) {
  const lang = $('lang').value;
  const minD = Number($('minDur').value) || 1;
  const maxD = Number($('maxDur').value) || 12;
  const gapT = Number($('gap').value) || 0.65;
  const maxChars = 90;
  const out = [];
  let parts = [], bucket = [];
  cues.forEach((c, i) => {
    parts.push(c.text); bucket.push(c);
    const span = bucket[bucket.length - 1].end - bucket[0].start;
    const text = joinParts(parts);
    const gap = i + 1 < cues.length ? cues[i + 1].start - c.end : 999;
    const boundary = endsSentence(text, lang) && span >= minD;
    const tooLong = span >= maxD || text.length >= maxChars;
    const pause = gap >= gapT && span > minD * 1.5 && !endsSentence(text, lang);
    if (i === cues.length - 1 || boundary || tooLong || pause) {
      const body = joinParts(parts).replace(/^[\s、，,。．.]+/, '');
      if (body && bucket.length) out.push({
        index: out.length + 1, start: bucket[0].start, end: bucket[bucket.length - 1].end,
        text: body, translation: ''
      });
      parts = []; bucket = [];
    }
  });
  state.sentences = out;
  for (const s of out) {
    const picked = state.transCues
      .filter((c) => Math.min(s.end, c.end) - Math.max(s.start, c.start) > 0.08)
      .map((c) => c.text);
    s.translation = joinParts(picked);
  }
  return out;
}

/* ---------- 页面探测与字幕轨 ---------- */
function fillVideoCard() {
  const d = state.probe;
  const badge = $('platformBadge');
  const label = { youtube: 'YouTube', bilibili: 'B 站', other: '其它页面' }[d.platform];
  badge.textContent = label;
  badge.className = 'badge ' + (d.platform === 'bilibili' ? 'bili' : d.platform === 'other' ? 'other' : '');
  $('videoTitle').textContent = d.title || '未识别到标题';
  let sub;
  if (d.platform === 'other') sub = '可以手动导入本地 srt/vtt 字幕文件';
  else if (d.platform === 'bilibili') {
    const n = state.biliMeta && state.biliMeta.pages ? state.biliMeta.pages.length : 0;
    sub = `${d.bvid || ''}${n > 1 ? ' · ' + n + ' 个分 P' : ''} · cid ${d.cid || '解析中'} · ${clock(d.currentTime || 0)}`;
  } else {
    sub = `${d.videoId || ''}${d.pageStale ? ' · 页面状态过期' : ''} · ${clock(d.currentTime || 0)}`;
  }
  $('videoSub').textContent = sub;
}

// 唯一真相源：按 URL 里的 bvid 重新解析 cid 与分 P 列表，绝不信任页面初始状态。
async function fetchBiliMeta(bvid) {
  const api = `https://api.bilibili.com/x/web-interface/view?bvid=${encodeURIComponent(bvid)}`;
  const r = await send({ type: 'fetchText', url: api, withCredentials: true });
  diag(`view API: HTTP ${r.status || 'ERR'} ${r.error || ''}`);
  if (!r.ok || !r.text) return null;
  try {
    const j = JSON.parse(r.text);
    if (j.code !== 0 || !j.data) { diag(`view API code=${j.code} ${j.message || ''}`); return null; }
    const pages = (j.data.pages || []).map((p, i) => ({
      page: Number(p.page) || i + 1, cid: p.cid, part: p.part || `P${i + 1}`
    }));
    diag(`view API: ${j.data.title} / aid=${j.data.aid} / ${pages.length} 个分 P`);
    pages.forEach((p) => diag(`  P${p.page} ${p.part} -> cid ${p.cid}`));
    return { aid: j.data.aid, cid: j.data.cid, title: j.data.title || '', pages: pages };
  } catch (e) { diag('view API JSON 解析失败: ' + e); return null; }
}

async function fetchBiliSubs(bvid, cid) {
  // 必须走带 WBI 签名的 /x/player/wbi/v2：无签名的 /x/player/v2 被风控降级时
  // 会返回别的视频的 AI 字幕（HTTP 200 假成功、每次还不一样）。
  // 签名由本地服务用 hashlib 算，请求在 B 站页面内发起以带上登录 Cookie。
  const signed = await send({ type: 'wbiSign', bvid: bvid, cid: cid });
  diag(`WBI 签名: ${signed.ok ? 'OK' : '失败 ' + (signed.error || '')}`);
  if (!signed.ok) return [];
  const r = await send({ type: 'fetchInPage', url: signed.url });
  diag(`player/wbi/v2 (页面内, cid=${cid}): HTTP ${r.status || 'ERR'} ${(r.text || '').length}B ${r.error || ''}`);
  if (!r.ok || !r.text) return [];
  try {
    const j = JSON.parse(r.text);
    const sub = ((j.data || {}).subtitle) || {};
    const subs = sub.subtitles || [];
    diag(`  code=${j.code}, 字幕轨 ${subs.length} 条, need_login=${!!sub.need_login_subtitle}`);
    if (!subs.length && sub.need_login_subtitle) {
      hint('B 站提示需要登录才能查看 AI 字幕：请先在 bilibili.com 登录，再重新打开插件。');
    }
    return subs;
  } catch (e) {
    diag('  wbi/v2 JSON 解析失败');
    return [];
  }
}

// 同一 cid 每次返回不同内容 => 数据源不稳定，拒绝使用
function simpleHash(s) {
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = ((h * 33) ^ s.charCodeAt(i)) >>> 0;
  return h.toString(16);
}
async function subtitleStable(cid, cues) {
  if (!cid || !cues.length) return true;
  const key = 's2a_subhash_' + cid;
  const h = simpleHash(cues.slice(0, 12).map((c) => c.text).join('|'));
  const data = await chrome.storage.local.get([key]);
  const prev = data[key];
  await chrome.storage.local.set({ [key]: h });
  diag(`  稳定性校验 cid=${cid}: ${prev ? prev + ' -> ' + h : '首次记录 ' + h}`);
  return !prev || prev === h;
}

function renderPageSelect(meta) {
  const row = $('pageRow');
  const sel = $('page');
  if (!row || !sel) return;
  if (!meta || !meta.pages || meta.pages.length <= 1) { row.style.display = 'none'; return; }
  row.style.display = '';
  sel.innerHTML = '';
  meta.pages.forEach((p) => {
    const o = document.createElement('option');
    o.value = String(p.page);
    o.textContent = `P${p.page} ${p.part}`;
    sel.appendChild(o);
  });
  if (meta.pages.some((p) => p.page === state.page)) sel.value = String(state.page);
  else { state.page = meta.pages[0].page; sel.value = String(state.page); }
  sel.onchange = async () => {
    state.page = Number(sel.value) || 1;
    state.probe.url = videoUrl(state.probe.url, 'bilibili', state.page);
    diag(`切换分 P -> P${state.page} URL=${state.probe.url}`);
    await loadTracks();
    await loadSelectedTrack();
  };
}

async function loadTracks() {
  const d = state.probe;
  let tracks = [];
  diag(`平台=${d.platform} URL=${d.url}`);
  if (d.platform === 'youtube') {
    diag(`videoId=${d.videoId} 页面字幕轨=${d.captionTracks.length} pageStale=${!!d.pageStale}`);
    for (const t of d.captionTracks) {
      tracks.push({
        label: `${t.name} (${t.lang})${t.kind === 'asr' ? ' · 自动' : ''}`,
        lang: t.lang || '', baseUrl: t.baseUrl, url: setParam(t.baseUrl, 'fmt', 'json3'),
        format: 'youtube_json3', translatable: t.isTranslatable || t.kind === 'asr'
      });
    }
    // 页面已展开「显示文字记录」时直接读 DOM 段落，完全绕开 timedtext 签名
    if (d.transcriptCues && d.transcriptCues.length) {
      diag(`页面文字记录: ${d.transcriptCues.length} 段`);
      tracks.push({
        label: `页面文字记录 (${d.transcriptCues.length} 段)`,
        lang: d.captionTracks[0] ? d.captionTracks[0].lang : '',
        format: 'transcript', preloaded: d.transcriptCues, verified: true
      });
    }
    // 裸 timedtext 请求现在返回空（需要带签名的 baseUrl），不在这里浪费请求。
    // 页面轨拿不到时交给本地服务用 yt-dlp 兜底，见 serverFetchSubtitles()。
    if (!tracks.length) hint('页面没给出字幕轨：正在让本地服务用 yt-dlp 兜底抓取；也可点下方按钮重试，或导入本地 srt/vtt。');
  } else if (d.platform === 'bilibili') {
    // 唯一真相源：按 URL 的 bvid 重新解析 cid；页面 __INITIAL_STATE__ 的 cid 一律覆盖
    const meta = state.biliMeta || await fetchBiliMeta(d.bvid);
    state.biliMeta = meta;
    renderPageSelect(meta);
    const page = state.page || d.page || 1;
    const chosen = meta && meta.pages.find((p) => p.page === page);
    const cid = (chosen && chosen.cid) || (meta && meta.cid) || d.pageCid || 0;
    d.cid = cid;
    diag(`选定 P${page} -> cid=${cid} (页面 cid=${d.pageCid || 0})`);
    if (!cid) {
      hint('没能解析出 cid：检查网络 / 是否登录 B 站，或手动导入本地字幕文件。');
    } else {
      // 自动字幕抓取经多轮适配（cid 校正、WBI 签名、页面登录态、内容校验）仍未稳定，
      // 因此默认关闭。音频切分 + 手动导入 srt/vtt 才是 B 站的可靠路径。
      const experimental = $('allowAI') && $('allowAI').checked;
      const subs = experimental ? await fetchBiliSubs(d.bvid, cid) : [];
      if (!experimental) diag('B 站自动字幕默认关闭（实验性，适配未成功）；音频切分不受影响');
      tracks = (subs || []).map((s) => {
        const raw = s.subtitle_url || '';
        return {
          label: `${s.lan_doc || s.lan}${s.ai_status ? ' · AI(需核对)' : ''} (P${page})`,
          lang: s.lan || '', baseUrl: raw,
          url: raw.startsWith('//') ? 'https:' + raw : raw,
          format: 'bilibili_json', translatable: false, verified: true,
          isAI: !!s.ai_status
        };
      });
      if (!tracks.length) {
        const why = !experimental
          ? '自动字幕抓取已默认关闭：多轮适配后仍不稳定，不建议依赖'
          : '该分 P 没有可用的字幕轨（字幕常压进画面，或文稿放在简介/置顶评论）';
        hint(`P${page} ${why}。B 站音频切割正常可用：把自己的 srt/vtt 拖到下方「手动导入本地字幕」即可制卡；也可在高级设置勾选实验开关再试一次。`);
      }
    }
  } else {
    hint('当前不是 YouTube / B 站页面。可以拖入本地字幕文件，或用命令行 python -m server --subtitle xx.srt 制卡。');
  }
  state.tracks = tracks;
  fillVideoCard();
  fillTrackSelects();
}

async function serverFetchSubtitles(manual) {
  if (state.probe.platform === 'bilibili') {
    diag('B 站跳过 yt-dlp 兜底：字幕改走带 WBI 签名的 wbi/v2 + 页面登录态');
    if (manual) hint('B 站字幕不靠 yt-dlp：请重新打开插件让它走带签名的接口；若下拉仍为空，说明该分 P 没有字幕轨（多为压在画面里的硬字幕），请把文稿存成 srt/vtt 手动导入。');
    return false;
  }
  await state.serverCheck;
  if (!state.serverOnline) {
    diag('服务端抓字幕跳过：本地服务未启动');
    if (manual) hint('本地服务没开：双击 start.bat 后再点这个按钮。');
    return false;
  }
  const url = videoUrl(state.probe.url, state.probe.platform, state.page);
  const lang = $('lang').value;
  $('trackTip').textContent = '服务端抓取中…';
  diag(`请求服务端抓字幕: ${url} lang=${lang}`);
  const r = await send({ type: 'subtitles', payload: {
    url: url, lang: lang,
    options: { proxy: $('proxy').value.trim(), cookies_from_browser: $('cookies').value }
  } });
  diag(`服务端抓字幕: ${r.ok ? r.count + ' 条碎片' : '失败 ' + (r.error || '未知')}`);
  if (!r.ok || !r.cues || !r.cues.length) {
    $('trackTip').textContent = '无字幕';
    const tip = state.probe.platform === 'youtube'
      ? 'YouTube 常见于 429 限流：请在高级设置勾选读取浏览器 Cookie 后重试，或在视频下方点「显示文字记录」再打开插件。'
      : 'B 站字幕轨需要登录；若日语是内嵌硬字幕则无法提取，请把文稿存成 srt/vtt 手动导入。';
    hint(`本地服务也没抓到字幕：${String(r.error || '').slice(0, 160)}。${tip}`);
    return false;
  }
  const idx = state.tracks.length;
  state.tracks.push({
    label: `服务端抓取 (${r.cues.length} 条)`, lang: lang, format: 'server',
    preloaded: r.cues, verified: true
  });
  fillTrackSelects();
  $('track').value = String(idx);
  hint('');
  await loadSelectedTrack();
  return true;
}

function fillTrackSelects() {
  const sel = $('track');
  sel.innerHTML = '';
  if (!state.tracks.length) {
    sel.innerHTML = '<option value="">（没有可用字幕轨，可手动导入文件）</option>';
  } else {
    state.tracks.forEach((t, i) => {
      const o = document.createElement('option');
      o.value = String(i); o.textContent = t.label; sel.appendChild(o);
    });
    // 优先选「已验证」的轨（按当前 videoId / cid 取的），避免 SPA 残留的页面轨
    const verified = state.tracks.findIndex((t) => t.verified);
    const prefer = state.tracks.findIndex((t) => t.verified && /^(ja|ko|en)/.test(t.lang || ''));
    sel.selectedIndex = prefer >= 0 ? prefer : (verified >= 0 ? verified : 0);
  }
  const trans = $('transTrack');
  trans.innerHTML = '<option value="">不要译文</option>';
  state.tracks.forEach((t, i) => {
    const o = document.createElement('option');
    o.value = 't:' + i; o.textContent = '用字幕轨：' + t.label; trans.appendChild(o);
  });
  if (state.tracks.some((t) => t.translatable)) {
    [['zh-Hans', '自动翻译成中文'], ['en', '自动翻译成英文'], ['zh-Hant', '自动翻译成繁中']]
      .forEach(([code, name]) => {
        const o = document.createElement('option');
        o.value = 'a:' + code; o.textContent = name + '（YouTube）'; trans.appendChild(o);
      });
  }
}

// YouTube 字幕必须在页面内同源请求；扩展后台跨域 fetch 会拿到 200/0 字节。
// 依次试原始 baseUrl 与多种 fmt，任一解析出内容即可。
async function fetchYoutubeTrack(baseUrl) {
  const variants = [
    { label: '原始 baseUrl', url: baseUrl },
    { label: 'fmt=json3', url: setParam(baseUrl, 'fmt', 'json3') },
    { label: 'fmt=vtt', url: setParam(baseUrl, 'fmt', 'vtt') },
    { label: 'fmt=srv3', url: setParam(baseUrl, 'fmt', 'srv3') }
  ];
  for (const v of variants) {
    const r = await send({ type: 'fetchInPage', url: v.url });
    diag(`  页面内 ${v.label}: HTTP ${r.status || 'ERR'} ${(r.text || '').length}B ${r.error || ''}`);
    if (!r.ok || !r.text) continue;
    let cues = [];
    try { cues = parseCues(r.text, 'auto'); } catch (e) { diag('  解析失败: ' + e); }
    diag(`  -> ${cues.length} cues`);
    if (cues.length) return cues;
  }
  for (const v of variants.slice(0, 2)) {
    const r = await send({ type: 'fetchText', url: v.url, withCredentials: true });
    diag(`  后台 ${v.label}: HTTP ${r.status || 'ERR'} ${(r.text || '').length}B`);
    if (r.ok && r.text) {
      const cues = parseCues(r.text, 'auto');
      if (cues.length) return cues;
    }
  }
  return [];
}

async function loadSelectedTrack() {
  const idx = $('track').value;
  $('trackTip').textContent = '';
  if (idx === '' || !state.tracks[Number(idx)]) {
    state.cues = []; renderPreview(); return;
  }
  const t = state.tracks[Number(idx)];
  $('trackTip').textContent = '读取中…';
  if (t.localCues) {
    state.cues = t.localCues;
  } else if (t.preloaded) {
    state.cues = t.preloaded;
    diag(`使用已探测的字幕轨「${t.label}」 ${state.cues.length} 条`);
  } else if (state.probe.platform === 'youtube' && t.baseUrl) {
    const cues = await fetchYoutubeTrack(t.baseUrl);
    if (!cues.length) {
      $('trackTip').textContent = '字幕为空';
      hint('YouTube 页面字幕轨返回空：正在让本地服务用 yt-dlp 兜底。也可在视频下方点「显示文字记录」后再打开插件（会直接读页面段落）。');
      await serverFetchSubtitles(false);
      return;
    }
    state.cues = cues;
    diag(`  共 ${state.cues.length} 条碎片`);
  } else {
    // B 站字幕是 CDN 静态文件，带 cookie 反而可能拿到随机缓存内容
    const r = await send({ type: 'fetchText', url: t.url,
      withCredentials: state.probe.platform !== 'bilibili' });
    diag(`字幕轨「${t.label}」: HTTP ${r.status || 'ERR'} ${(r.text || '').length}B ${r.error || ''}`);
    if (!r.ok || !r.text) {
      $('trackTip').textContent = '读取失败';
      hint(`字幕拉取失败（HTTP ${r.status || 'ERR'} ${r.error || ''}）。B 站字幕常需登录；也可拖入本地 srt/vtt。点开下方「诊断信息」可看完整过程。`);
      state.cues = [];
      renderPreview(); return;
    }
    state.cues = parseCues(r.text, t.format);
    diag(`  解析出 ${state.cues.length} 条碎片`);
  }
  // 三重防串轨：语言一致性 + 同一 cid 内容稳定性（只校验 B 站远程字幕）
  if (state.cues.length && state.probe.platform === 'bilibili' && !t.localCues && !t.preloaded) {
    const lang = $('lang').value;
    const sample = state.cues.slice(0, 20).map((c) => c.text).join('');
    const ratio = (sample.match(/[\u4e00-\u9fff]/g) || []).length / Math.max(1, sample.length);
    diag(`  语言校验: 中文占比 ${(ratio * 100).toFixed(0)}%, 设置=${lang}`);
    if (['en', 'ja', 'ko'].includes(lang) && ratio > 0.35) {
      diag('  判定为中文 AI 字幕错轨，丢弃');
      state.cues = [];
      $('trackTip').textContent = '语言不符';
      hint(`抓到的是中文字幕，但你选的是 ${lang}：疑似 B 站 AI 字幕错轨，已丢弃。请核对字幕轨或手动导入 srt/vtt。`);
    } else if (!(await subtitleStable(state.probe.cid, state.cues))) {
      diag('  判定为不稳定数据源，丢弃');
      state.cues = [];
      $('trackTip').textContent = '字幕源不稳定';
      hint('同一分 P 两次抓到的字幕内容不一样：B 站返回了不稳定字幕源，已丢弃以免混入别的视频。请手动导入 srt/vtt 或稍后重试。');
    }
  }
  if (!state.cues.length) {
    $('trackTip').textContent = '字幕为空';
    hint('这条字幕轨是空的，换一条试试。');
  } else {
    $('trackTip').textContent = `${state.cues.length} 条碎片`;
  }
  await loadTranslation();
  renderPreview();
}

async function loadTranslation() {
  const v = $('transTrack').value;
  state.transCues = [];
  if (!v) return;
  let url = '', format = 'youtube_json3';
  if (v.startsWith('t:')) {
    const t = state.tracks[Number(v.slice(2))];
    if (!t) return;
    if (t.localCues) { state.transCues = t.localCues; return; }
    url = t.url; format = t.format;
  } else if (v.startsWith('a:')) {
    const t = state.tracks[Number($('track').value)];
    if (!t || !t.baseUrl) return;
    url = setParam(setParam(t.baseUrl, 'tlang', v.slice(2)), 'fmt', 'json3');
  }
  if (!url) return;
  const r = await send({ type: 'fetchText', url: url, withCredentials: true });
  if (r.ok && r.text) state.transCues = parseCues(r.text, format);
}

function renderPreview() {
  const sents = segmentCues(state.cues);
  const box = $('preview');
  if (!sents.length) {
    box.innerHTML = '<div class="empty">选好字幕轨后，这里会显示前几句预览</div>';
    return;
  }
  box.innerHTML = `<div class="empty" style="text-align:left;padding:0 0 6px">共 ${sents.length} 句 · 预览前 3 句</div>` +
    sents.slice(0, 3).map((s) =>
      `<div class="row"><span class="ts">${clock(s.start)}</span>` +
      `<span class="t">${esc(s.text)}</span>` +
      (s.translation ? `<div class="tr">${esc(s.translation)}</div>` : '') + '</div>').join('');
}

/* ---------- 制卡 ---------- */
function currentOptions() {
  return {
    audio: $('wantAudio').checked,
    apkg: true,
    ankiconnect: $('ankiConnect').checked,
    audio_source: $('audioSource').value.trim(),
    cookies_from_browser: $('cookies').value,
    proxy: $('proxy').value.trim(),
    min_duration: Number($('minDur').value) || 1,
    max_duration: Number($('maxDur').value) || 12,
    gap_threshold: Number($('gap').value) || 0.65,
    max_chars: 90
  };
}
function deckPrefix() {
  const v = $('deck').value.trim();
  // 兼容旧版保存过的偏好值
  return (!v || v === 'Subs2Anki::句子卡') ? 'Subs2Anki' : v.replace(/:+$/, '');
}
function deckName() {
  const slug = (state.probe.title || 'video')
    .replace(/[^\w\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af-]+/g, '_')
    .replace(/_+/g, '_').replace(/^_+|_+$/g, '').slice(0, 40);
  return deckPrefix() + '::' + (slug || 'video');
}
function buildPayload() {
  const url = videoUrl(state.probe.url, state.probe.platform, state.page);
  let title = state.probe.title;
  const meta = state.biliMeta;
  if (state.probe.platform === 'bilibili' && meta && meta.pages && meta.pages.length > 1) {
    const p = meta.pages.find((x) => x.page === state.page);
    if (p) title += ` · P${p.page} ${p.part}`;
  }
  diag(`制卡 URL=${url}`);
  return {
    url: url, title: title,
    platform: state.probe.platform, lang: $('lang').value,
    deck: deckPrefix(),
    card_mode: $('cardMode').value,
    cues: state.cues, translation_cues: state.transCues,
    options: currentOptions()
  };
}
function buildTsv() {
  const d = state.probe;
  const sents = segmentCues(state.cues);
  const lines = ['#separator:tab', '#html:true', '#deck:' + deckName(),
    '#notetype:Basic', '#tags column:3'];
  for (const s of sents) {
    const link = `<a href="${esc(tsUrl(videoUrl(d.url, d.platform, state.page), s.start))}">▶ ${esc(d.title)} · ${clock(s.start)}</a>`;
    const front = `<div class="sa-src">${link}</div>`;
    const back = `<div>${esc(s.text)}</div>` +
      (s.translation ? `<div style="color:#3d6a94">${esc(s.translation)}</div>` : '') +
      `<div style="font-size:12px;color:#999">${clock(s.start)}–${clock(s.end)} · #${s.index}</div>`;
    lines.push([front, back, 'subs2anki'].join('\t').replace(/\n/g, ' '));
  }
  return lines.join('\n') + '\n';
}
function setProgress(p, msg) {
  $('progressWrap').classList.remove('hidden');
  $('pbarFill').style.width = Math.max(0, Math.min(100, p)) + '%';
  $('pmsg').textContent = msg || '';
}
function showResult(r) {
  let html = `<div class="result-card"><b>✅ 做好啦：${r.count} 张句子卡</b>` +
    (r.audio_count ? `，其中 ${r.audio_count} 张带原声片段` : '') + '<br>' +
    `<span class="path">${esc(r.out_dir)}</span><br>` +
    (r.deck ? `导入到牌组：<b>${esc(r.deck)}</b><br>` : '') +
    (r.apkg ? '📦 双击 <b>.apkg</b> 即可导入 Anki（音频已打包）<br>' : '') +
    '📄 也可以用「导入文本文件」选 <b>anki_import.tsv</b><br>';
  for (const w of r.warnings || []) html += `<span class="warn">⚠️ ${esc(w)}</span>`;
  html += `<button id="openDir">打开输出目录</button><button id="copyPath">复制路径</button></div>`;
  $('result').innerHTML = html;
  $('openDir').onclick = async () => {
    const res = await send({ type: 'openDir', path: r.out_dir });
    if (!res.ok) alert('打开失败：' + (res.error || '本地服务不可用'));
  };
  $('copyPath').onclick = () => navigator.clipboard.writeText(r.out_dir);
}
function showError(msg) {
  $('result').innerHTML = `<div class="result-card error"><b>制卡失败</b><br>${esc(msg)}</div>`;
}
async function tsvOnly(reason) {
  setProgress(100, '导出 TSV…');
  const name = 'subs2anki_' + (state.probe.title || 'cards').replace(/[^\w\u4e00-\u9fff]+/g, '_').slice(0, 30) + '.tsv';
  const res = await send({ type: 'download', content: buildTsv(), filename: name });
  $('result').innerHTML = `<div class="result-card"><b>📄 已导出纯文字 TSV</b><br>` +
    `共 ${state.sentences.length} 句，卡片背面有可点击的原视频时间戳链接。` +
    `<span class="warn">⚠️ ${esc(reason || '本地服务未启动，所以没有音频片段')}。想听原声请运行项目里的 <b>start.bat</b> 后重新一键制卡。</span>` +
    `导入：Anki → 导入文本文件，选刚下载的 .tsv，勾选「允许 HTML」。</div>`;
  if (!res.ok) showError('下载失败：' + res.error);
}
async function poll(jobId) {
  for (let i = 0; i < 360; i++) {
    const s = await send({ type: 'status', id: jobId });
    if (s.state === 'done') { setProgress(100, '完成'); showResult(s.result); return; }
    if (s.state === 'error') { showError(s.error || s.message); return; }
    setProgress(s.progress || 0, s.message || '处理中…');
    await sleep(600);
  }
  showError('等待超时，但服务可能仍在后台处理，请到 out 目录查看。');
}
async function make() {
  if (state.busy) return;
  if (!state.cues.length) { hint('请先选一条字幕轨，或拖入本地字幕文件。'); return; }
  state.busy = true;
  $('makeBtn').disabled = true;
  $('makeBtn').textContent = '制卡中…';
  $('result').innerHTML = '';
  savePrefs();
  try {
    await state.serverCheck;   // 等 ping 结果落地，避免误判成「服务没开」
    if (!state.serverOnline) { await tsvOnly(); return; }
    setProgress(2, '提交任务…');
    const r = await send({ type: 'make', payload: buildPayload() });
    if (!r.ok || r.error || !r.job_id) {
      await tsvOnly('本地服务没响应（' + (r.error || '未知') + '）');
      return;
    }
    await poll(r.job_id);
  } finally {
    state.busy = false;
    $('makeBtn').disabled = false;
    $('makeBtn').textContent = '✨ 一键制卡';
  }
}

/* ---------- 偏好 / 事件 ---------- */
const PREF_IDS = ['lang', 'cardMode', 'deck', 'wantAudio', 'ankiConnect',
  'minDur', 'maxDur', 'gap', 'audioSource', 'cookies', 'proxy', 'allowAI'];
function savePrefs() {
  const data = {};
  for (const id of PREF_IDS) {
    const el = $(id);
    data[id] = el.type === 'checkbox' ? el.checked : el.value;
  }
  chrome.storage.local.set(data);
}
async function loadPrefs() {
  const data = await chrome.storage.local.get(PREF_IDS);
  for (const id of PREF_IDS) {
    if (data[id] === undefined) continue;
    const el = $(id);
    if (el.type === 'checkbox') el.checked = !!data[id]; else el.value = data[id];
  }
}
async function checkServer() {
  const r = await send({ type: 'ping' });
  state.serverOnline = !!(r.ok && !r.offline);
  diag(`本地服务: ${state.serverOnline ? '在线' : '未启动'} ${JSON.stringify(r.deps || r.error || {})}`);
  if (state.serverOnline) {
    const d = r.deps || {};
    setStatus('', '服务在线');
    const missing = [];
    if (!d.yt_dlp) missing.push('yt-dlp');
    if (!d.ffmpeg) missing.push('ffmpeg');
    setHint('server', missing.length
      ? `本地服务在线，但缺 ${missing.join(' / ')}：仍能出卡片，音频会降级成时间戳链接。装好依赖见 README。`
      : '');
  } else {
    setStatus('off', '服务未启动');
    setHint('server', '本地服务没开：现在只能导出纯文字 TSV。运行 start.bat 后，再点一次插件图标，就能切出带原声的 .apkg。');
  }
}
function showHelp() {
  const el = $('help');
  el.classList.toggle('hidden');
  if (!el.classList.contains('hidden')) {
    el.innerHTML = `<b>三步用起来</b><br>
      1. 双击 <code>setup.bat</code> 装依赖（只需一次）<br>
      2. 双击 <code>start.bat</code>，那个黑窗口保持开着<br>
      3. 在视频页面点插件 → 选字幕轨 → <b>一键制卡</b><br>
      产物在 <code>out/</code> 里，双击 <code>.apkg</code> 导入 Anki。<br>
      卡片正面自带 <b>0.5× / 0.75× / 1× / 1.5× / 2×</b> 倍速重播，选过的速度会记住。<br>
      B 站字幕按 bvid + 分 P 的 cid 重新取，多 P 视频请先选分 P。<br>
      B 站字幕要登录；YouTube 会自动直连探测字幕轨。<br>
      拉不到字幕或音频时，点开底部「诊断信息」把内容复制给我。<br>
      不开服务也能用：点「只导出 TSV」，卡片里是时间戳链接。<br>
      Anki 里听音频：.apkg 已内置声音；TSV 模式要把音频文件放进 Anki 的 collection.media。`;
  }
}

document.addEventListener('DOMContentLoaded', async () => {
  $('makeBtn').onclick = make;
  $('tsvBtn').onclick = () => tsvOnly('手动选择的纯文字模式');
  $('helpBtn').onclick = showHelp;
  $('track').onchange = loadSelectedTrack;
  $('serverSubsBtn').onclick = () => serverFetchSubtitles(true);
  $('transTrack').onchange = async () => { await loadTranslation(); renderPreview(); };
  $('lang').onchange = renderPreview;
  ['minDur', 'maxDur', 'gap'].forEach((id) => { $(id).onchange = renderPreview; });
  $('localSub').onchange = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const cues = parseCues(await file.text(), 'auto');
    if (!cues.length) { hint('这个文件里没解析出字幕，支持 srt / vtt / json3 / B站 json。'); return; }
    const idx = state.tracks.length;
    state.tracks.push({ label: '本地文件：' + file.name, format: 'local', localCues: cues });
    fillTrackSelects();
    $('track').value = String(idx);
    hint('');
    await loadSelectedTrack();
  };

  await loadPrefs();
  state.serverCheck = checkServer();
  const p = await send({ type: 'probe' });
  if (!p.ok) {
    $('videoTitle').textContent = '读取页面失败';
    hint(p.error || '请确认在 YouTube / B 站页面打开了插件。');
    return;
  }
  state.probe = p.data;
  state.page = p.data.page || 1;
  diag(`页面标题: ${p.data.title}`);
  fillVideoCard();
  await loadTracks();
  await loadSelectedTrack();
  // 只有 YouTube 自动用 yt-dlp 兜底；B 站该路径无效，避免反复出现误导性失败提示
  if (!state.tracks.length && state.probe.platform === 'youtube') {
    serverFetchSubtitles(false);
  }
});
