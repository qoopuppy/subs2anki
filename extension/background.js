/* Subs2Anki service worker
 * 页面探测（MAIN world）+ 跨域抓字幕（复用浏览器登录态）+ 与本地服务通信。
 * 刻意让所有网络请求走这里：B 站字幕需要登录 cookie，扩展 origin 带 host_permissions 才拿得到。
 */

const BASE = 'http://127.0.0.1:8766';

// 这个函数会被序列化后注入页面 MAIN world，不能引用外部变量，必须自包含。
function probePage() {
  const out = {
    url: location.href,
    host: location.hostname,
    title: document.title,
    platform: 'other',
    captionTracks: [], transcriptCues: [],
    videoId: '', bvid: '', aid: 0, cid: 0, page: 1,
    duration: 0, currentTime: 0
  };
  const video = document.querySelector('video');
  if (video) {
    out.duration = Number(video.duration) || 0;
    out.currentTime = Number(video.currentTime) || 0;
  }

  if (/youtube|youtu\.be/.test(out.host)) {
    out.platform = 'youtube';
    const urlVid = new URLSearchParams(location.search).get('v') ||
      (location.pathname.match(/\/embed\/([\w-]{11})/) || [])[1] || '';
    let pr = window.ytInitialPlayerResponse || null;
    // SPA 切换视频后 ytInitialPlayerResponse 可能是旧的，播放器对象上的才是当前视频
    try {
      const player = document.getElementById('movie_player');
      if (player && typeof player.getPlayerResponse === 'function') {
        const fresh = player.getPlayerResponse();
        if (fresh && fresh.videoDetails) pr = fresh;
      }
    } catch (e) { /* 忽略 */ }
    const rawDetails = pr && pr.videoDetails;
    out.videoId = urlVid || (rawDetails && rawDetails.videoId) || '';
    // 关键：播放器响应可能属于 SPA 切换前的上一个视频，此时它的字幕轨必须丢弃，
    // 否则会拿到别的视频的字幕（刷新也不一定有用）。
    const stale = !!(rawDetails && rawDetails.videoId && urlVid &&
      rawDetails.videoId !== urlVid);
    const usable = stale ? null : pr;
    const details = usable && usable.videoDetails;
    out.title = (details && details.title) ||
      (document.querySelector('h1 yt-formatted-string') || {}).textContent ||
      document.title.replace(/\s*-\s*YouTube\s*$/, '');
    out.pageStale = !!stale;
    const tracks = (usable && usable.captions &&
      usable.captions.playerCaptionsTracklistRenderer &&
      usable.captions.playerCaptionsTracklistRenderer.captionTracks) || [];
    out.captionTracks = tracks.map(function (t) {
      return {
        lang: t.languageCode || '',
        name: (t.name && (t.name.simpleText ||
          (t.name.runs || []).map(function (r) { return r.text; }).join(''))) || t.languageCode || '字幕',
        kind: t.kind || '',
        isTranslatable: !!t.isTranslatable,
        baseUrl: t.baseUrl || ''
      };
    });
    // 兜底：字幕曾被加载过的话，从资源请求记录里捞带签名的 timedtext URL
    if (!out.captionTracks.length) {
      try {
        const hit = performance.getEntriesByType('resource')
          .map(function (e) { return e.name; })
          .filter(function (u) { return /\/api\/timedtext/.test(u); })[0];
        if (hit) {
          out.captionTracks = [{
            lang: new URL(hit).searchParams.get('lang') || 'auto',
            name: '自动字幕（资源记录）', kind: 'asr',
            isTranslatable: true, baseUrl: hit
          }];
        }
      } catch (e) { /* 忽略 */ }
    }
    // 终极兜底：如果用户已展开「显示文字记录」，直接从 DOM 里读段落。
    // 这条路径完全不碰 timedtext API，不受签名 / PoToken / 429 影响。
    try {
      const segs = document.querySelectorAll('ytd-transcript-segment-renderer');
      if (segs && segs.length) {
        out.transcriptCues = Array.prototype.map.call(segs, function (el) {
          const stamp = el.querySelector('.segment-timestamp');
          const body = el.querySelector('.segment-text') || el.querySelector('yt-formatted-string');
          const parts = (stamp ? stamp.textContent.trim() : '0:00').split(':').map(Number);
          const start = parts.length === 2 ? parts[0] * 60 + parts[1]
            : parts.length === 3 ? parts[0] * 3600 + parts[1] * 60 + parts[2] : 0;
          return { start: start, end: start + 2, text: (body && body.textContent || '').trim() };
        }).filter(function (c) { return c.text; });
      }
    } catch (e) { /* 忽略 */ }
    return out;
  }

  if (/bilibili/.test(out.host)) {
    out.platform = 'bilibili';
    const st = window.__INITIAL_STATE__ || {};
    const matched = location.href.match(/(BV[0-9A-Za-z]{10})/);
    out.bvid = (matched && matched[1]) || st.bvid || (st.videoData && st.videoData.bvid) || '';
    out.aid = st.aid || (st.videoData && st.videoData.aid) || 0;
    out.cid = st.cid || (st.videoData && st.videoData.cid) ||
      (st.epInfo && st.epInfo.cid) || 0;
    out.page = Number(new URLSearchParams(location.search).get('p')) || 1;
    out.title = (st.videoData && st.videoData.title) || st.title ||
      document.title.replace(/[_-]哔哩哔哩.*$/, '').trim();
    // 刻意不读 __INITIAL_STATE__ 里的字幕轨：从推荐流 SPA 点进来时它可能
    // 属于上一个视频，会拉到完全无关的字幕。字幕一律由 popup 按 bvid+cid
    // 重新请求 x/player/v2 获得；页面 cid 也只作为 API 失败时的兜底。
    out.pageCid = out.cid;
    return out;
  }
  return out;
}

async function fetchText(url, withCredentials) {
  try {
    const resp = await fetch(url, {
      credentials: withCredentials === false ? 'omit' : 'include',
      headers: { 'Accept': '*/*' }
    });
    const text = await resp.text();
    return { ok: resp.ok, status: resp.status, text: text };
  } catch (e) {
    return { ok: false, status: 0, error: String(e) };
  }
}

async function local(path, options) {
  const controller = new AbortController();
  const timer = setTimeout(function () { controller.abort(); }, (options && options.timeout) || 4000);
  try {
    const resp = await fetch(BASE + path, Object.assign({
      signal: controller.signal
    }, options || {}));
    return await resp.json();
  } catch (e) {
    return { ok: false, error: String(e), offline: true };
  } finally {
    clearTimeout(timer);
  }
}

chrome.runtime.onMessage.addListener(function (msg, sender, sendResponse) {
  (async function () {
    switch (msg.type) {
      case 'probe': {
        const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
        const tab = tabs[0];
        if (!tab) return sendResponse({ ok: false, error: '没有找到当前标签页' });
        const results = await chrome.scripting.executeScript({
          target: { tabId: tab.id }, world: 'MAIN', func: probePage
        });
        return sendResponse({ ok: true, data: results && results[0] && results[0].result, tabId: tab.id });
      }
      case 'fetchText':
        return sendResponse(await fetchText(msg.url, msg.withCredentials));
      case 'fetchInPage': {
        // 在页面 MAIN world 里做同源请求：带完整 cookie / Referer / Origin，
        // YouTube timedtext 对扩展后台的跨域请求会返回 200 但 0 字节。
        const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
        if (!tabs[0]) return sendResponse({ ok: false, status: 0, text: '', error: '没有活动标签页' });
        const results = await chrome.scripting.executeScript({
          target: { tabId: tabs[0].id }, world: 'MAIN',
          func: async (u) => {
            try {
              const r = await fetch(u, { credentials: 'include' });
              const t = await r.text();
              return { ok: r.ok, status: r.status, text: t, error: '' };
            } catch (e) {
              return { ok: false, status: 0, text: '', error: String(e) };
            }
          },
          args: [msg.url]
        });
        return sendResponse((results && results[0] && results[0].result) ||
          { ok: false, status: 0, text: '', error: '页面内请求无返回' });
      }
      case 'ping':
        return sendResponse(await local('/api/ping', { timeout: 1800 }));
      case 'config':
        return sendResponse(await local('/api/config'));
      case 'make':
        return sendResponse(await local('/api/make', {
          method: 'POST', timeout: 15000,
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(msg.payload)
        }));
      case 'wbiSign':
        return sendResponse(await local('/api/wbi-sign?bvid=' + encodeURIComponent(msg.bvid)
          + '&cid=' + encodeURIComponent(msg.cid), { timeout: 20000 }));
      case 'status':
        return sendResponse(await local('/api/status?id=' + encodeURIComponent(msg.id)));
      case 'subtitles':
        return sendResponse(await local('/api/subtitles', {
          method: 'POST', timeout: 300000,
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(msg.payload)
        }));
      case 'openDir':
        return sendResponse(await local('/api/open', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: msg.path })
        }));
      case 'download': {
        const dataUrl = 'data:text/plain;charset=utf-8,' + encodeURIComponent(msg.content);
        const id = await chrome.downloads.download({
          url: dataUrl, filename: msg.filename, saveAs: false, conflictAction: 'uniquify'
        });
        return sendResponse({ ok: true, id: id });
      }
      default:
        return sendResponse({ ok: false, error: '未知消息类型' });
    }
  })().catch(function (e) { sendResponse({ ok: false, error: String(e) }); });
  return true; // 保持异步消息通道
});
