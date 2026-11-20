(() => {
  "use strict";

  const CONFIG = Object.assign(
    { useReleases: true, preferLocalMedia: true, mediaBase: "media/", lostFolder: "_LostTweets/", dashboardUrl: "" },
    window.MALFOID_CONFIG || {}
  );
  const CELL_RATIO = 1.25; // height / width of a grid cell
  const OVERSCAN = 2; // rows rendered above and below the viewport
  const FAME_MIN = 50000; // Hall of Fame: more likes than this
  // Gem shelves, one work per artist: at least the first number of likes and fewer than the second.
  const GEM_RANGES = { big: [5000, 10000], gems: [2000, 5000], hidden: [200, 2000] };
  const NEW_MAX = 200; // New Malfoid: every work under this many likes
  const SMALL_FOLLOWERS = 2000; // Small Artists: fewer followers than this (and not in New Malfoid)
  const SHELF_SIZE = 30; // works shown at once in the shuffled shelves
  const LONG_VIDEO = 30; // videos this many seconds or longer stay off the home shelves (My Personal Pick excepted)
  const TIMEZONE = "Australia/Sydney"; // days on the timeline, as in the original dashboard
  const TIMELINE_START = "2026-09-01";
  const TIMELINE_VIEW_END = "2026-10-01"; // the Timeline tab covers the early days, before the October wave
  const VIDEO_EXTS = new Set(["mp4", "mov", "webm"]);

  const $ = (sel) => document.querySelector(sel);
  const el = {
    heroMeta: $("#hero-meta"),
    lostNote: $("#lost-note"),
    q: $("#q"),
    likes: $("#likes"),
    kind: $("#kind"),
    sort: $("#sort"),
    count: $("#count"),
    activeArtist: $("#active-artist"),
    artistList: $("#artist-list"),
    grid: $("#grid"),
    empty: $("#empty"),
    artistQ: $("#artist-q"),
    artistSort: $("#artist-sort"),
    artistCount: $("#artist-count"),
    artists: $("#artists"),
    viewer: $("#viewer"),
    viewerMedia: $("#viewer-media"),
    viewerPos: $("#viewer-pos"),
    viewerArtist: $("#viewer-artist"),
    viewerTags: $("#viewer-tags"),
    viewerLost: $("#viewer-lost"),
    viewerMeta: $("#viewer-meta"),
    viewerTweet: $("#viewer-tweet"),
    viewerDanbooru: $("#viewer-danbooru"),
    viewerMore: $("#viewer-more"),
    viewerNote: $("#viewer-note"),
    prev: $("#prev"),
    next: $("#next"),
  };

  const VIEWS = ["home", "gallery", "timeline", "artists"];
  const state = { q: "", artist: "", likes: "", kind: "", sort: "added", view: "home" };
  // Gallery "Likes" filter: at least the first number and fewer than the second. Works without a count match none.
  const LIKE_RANGES = {
    "50k": [50000, Infinity],
    "30k": [30000, 50000],
    "20k": [20000, 30000],
    "10k": [10000, 20000],
    "5k": [5000, 10000],
    "2k": [2000, 5000],
    "200": [200, 2000],
    "0": [0, 200],
  };
  let items = [];
  const itemByFile = new Map();
  let artistStats = new Map(); // lowercased handle -> {key, handle, count, latest, cover}
  let filtered = [];
  let viewerList = [];
  let viewerIndex = -1;
  let viewerReturnFocus = null;

  const numberFmt = new Intl.NumberFormat("en-US");
  const compactFmt = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1, roundingMode: "trunc" }); // 1,950 reads 1.9K, never "2K" for an under-2,000 work
  const dateFmt = new Intl.DateTimeFormat("en-US", { year: "numeric", month: "short", day: "numeric" });
  const fmtDate = (ms) => (ms ? dateFmt.format(new Date(ms)) : "Unknown");
  const fmtLikes = (n) => compactFmt.format(n);
  // File-name order as macOS Finder shows it: case-insensitive, numbers by value, "_" before digits and letters.
  const nameCollator = new Intl.Collator("en", { numeric: true, sensitivity: "base" });
  const plural = (n, one, many) => `${numberFmt.format(n)} ${n === 1 ? one : many}`;

  const thumbUrl = (it) => "thumbs/" + encodeURIComponent(it.f) + ".webp";
  const onLocalhost = /^(localhost|127\.0\.0\.1|\[::1\])$/.test(location.hostname);
  let releaseBase = "";
  const mediaUrl = (it) => {
    const local = CONFIG.preferLocalMedia && onLocalhost;
    if (CONFIG.useReleases && releaseBase && it.u && !local) {
      return releaseBase + it.u.split("/").map(encodeURIComponent).join("/");
    }
    return CONFIG.mediaBase + (it.l ? CONFIG.lostFolder : "") + encodeURIComponent(it.f);
  };
  const tweetUrl = (it) => (it.t ? `https://x.com/${encodeURIComponent(it.a)}/status/${it.t}` : "");
  const profileUrl = (it) => `https://x.com/${encodeURIComponent(it.a)}`;
  const isSafeUrl = (u) => /^https?:\/\//i.test(u);

  // ---------- URL state ----------

  function readUrl() {
    const p = new URLSearchParams(location.search);
    state.q = p.get("q") || "";
    state.artist = p.get("artist") || "";
    state.likes = p.get("likes") || "";
    state.kind = p.get("type") || "";
    state.sort = p.get("sort") || "added";
    // Old links may name an order or range that no longer exists.
    if (![...el.sort.options].some((o) => o.value === state.sort)) state.sort = "added";
    if (!Object.hasOwn(LIKE_RANGES, state.likes)) state.likes = "";
    const hash = location.hash.slice(1);
    const hasFilters = ["q", "artist", "likes", "type", "sort"].some((k) => p.has(k));
    state.view = VIEWS.includes(hash) ? hash : hasFilters ? "gallery" : "home";
    return p.get("work");
  }

  function writeUrl(work) {
    const p = new URLSearchParams();
    if (state.view === "gallery") {
      if (state.q) p.set("q", state.q);
      if (state.artist) p.set("artist", state.artist);
      if (state.likes) p.set("likes", state.likes);
      if (state.kind) p.set("type", state.kind);
      if (state.sort !== "added") p.set("sort", state.sort);
    }
    if (work) p.set("work", work);
    const qs = p.toString();
    const hash = state.view === "home" ? "" : "#" + state.view;
    history.replaceState(null, "", location.pathname + (qs ? "?" + qs : "") + hash);
  }

  function syncControls() {
    el.q.value = state.q;
    el.likes.value = state.likes;
    el.sort.value = state.sort;
    for (const b of el.kind.querySelectorAll("button[role=radio]")) {
      b.setAttribute("aria-checked", String(b.dataset.kind === state.kind));
    }
  }

  // ---------- Filtering ----------

  function applyFilters() {
    const raw = state.q.trim().toLowerCase();
    const exact = raw.startsWith("@");
    const q = raw.replace(/^@/, "");
    const qLoose = q.replace(/[_.\s]/g, "");
    // "@handle" matches that artist exactly when it exists, otherwise any handle starting with it.
    const exactHandle = exact && artistStats.has(q);
    const artist = state.artist.toLowerCase();
    const [minLikes, maxLikes] = LIKE_RANGES[state.likes] || [];

    const out = items.filter((it) => {
      if (artist && it._al !== artist) return false;
      if (state.likes && (it.v == null || it.v < minLikes || it.v >= maxLikes)) return false;
      if (state.kind === "lost" ? !it.l : state.kind === "pick" ? !pickSet.has(it.f) : state.kind && it.k !== state.kind) return false;
      if (q) {
        if (exact) return exactHandle ? it._al === q : it._al.startsWith(q);
        return it._al.includes(q) || (qLoose && it._an.includes(qLoose)) || (it.t && it.t.includes(q));
      }
      return true;
    });

    const byFile = (a, b) => (a.f < b.f ? -1 : a.f > b.f ? 1 : 0);
    const sorters = {
      added: (a, b) => b.d - a.d || byFile(a, b),
      newest: (a, b) => b.p - a.p || byFile(a, b),
      // Images from one tweet share its count, so they stay together.
      liked: (a, b) => (b.v ?? -1) - (a.v ?? -1) || (a.t < b.t ? -1 : a.t > b.t ? 1 : 0) || a.i - b.i || byFile(a, b),
      oldest: (a, b) => (a.p || Infinity) - (b.p || Infinity) || byFile(a, b),
      artist: (a, b) => (a._al < b._al ? -1 : a._al > b._al ? 1 : b.p - a.p || byFile(a, b)),
      name: (a, b) => nameCollator.compare(a.f, b.f) || byFile(a, b),
    };
    out.sort(sorters[state.sort] || sorters.added);
    filtered = out;

    const total = numberFmt.format(items.length);
    const artistsShown = new Set(out.map((it) => it._al)).size;
    el.count.textContent =
      out.length === items.length
        ? `${total} works by ${numberFmt.format(artistStats.size)} artists`
        : `${numberFmt.format(out.length)} of ${total} works · ${plural(artistsShown, "artist", "artists")}`;
    el.empty.hidden = out.length > 0;

    renderArtistChip();
    grid.setData(filtered);
  }

  function renderArtistChip() {
    el.activeArtist.replaceChildren();
    if (!state.artist) {
      el.activeArtist.hidden = true;
      return;
    }
    const stats = artistStats.get(state.artist.toLowerCase());
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = "@" + (stats ? stats.handle : state.artist);
    const x = document.createElement("button");
    x.type = "button";
    x.setAttribute("aria-label", "Show all artists");
    x.textContent = "✕";
    x.addEventListener("click", () => setArtist(""));
    chip.append(x);
    el.activeArtist.append(chip);
    el.activeArtist.hidden = false;
  }

  function setArtist(handle) {
    state.artist = handle;
    if (handle) state.q = "";
    syncControls();
    applyFilters();
    writeUrl();
  }

  function showGallery(changes) {
    Object.assign(state, changes);
    state.view = "gallery";
    syncControls();
    showView();
    applyFilters();
    writeUrl();
    window.scrollTo({ top: 0 });
  }

  // ---------- Shared card pieces ----------

  function badge(text, play, extra) {
    const b = document.createElement("span");
    b.className = "badge" + (extra ? " " + extra : "");
    if (play) {
      b.insertAdjacentHTML("afterbegin", '<svg viewBox="0 0 10 10" aria-hidden="true"><path d="M2 1l7 4-7 4z"/></svg>');
    }
    b.append(text);
    return b;
  }

  function cardLabel(it) {
    return (
      `Work by @${it.a}` +
      (it.v != null ? `, about ${fmtLikes(it.v)} likes` : "") +
      (it.k === "anim" ? ", animation" : "") +
      (it.l ? ", lost tweet" : "") +
      (pickSet.has(it.f) ? ", in My Personal Pick" : "")
    );
  }

  // ---------- My Personal Pick ----------
  // The list lives in data/picks.json. It can only be changed through tools/serve.py, the owner's
  // local server, which marks data/picks.json with an X-Malfoid-Edit header and accepts PUT /api/picks.
  // Everywhere else (GitHub Pages, a plain static server) picks are read-only.

  const HEART_SVG =
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20.5s-7.6-4.6-9.5-9.3C1.1 7.8 3.2 4 6.9 4c2.1 0 3.5 1.1 5.1 3.1C13.6 5.1 15 4 17.1 4c3.7 0 5.8 3.8 4.4 7.2-1.9 4.7-9.5 9.3-9.5 9.3z"/></svg>';
  let picks = []; // filenames, newest pick first
  let pickSet = new Set();
  let picksEditable = false;
  let pickSave = Promise.resolve();

  async function loadPicks() {
    try {
      const res = await fetch("data/picks.json", { cache: "no-store" });
      if (!res.ok) return;
      picksEditable = res.headers.get("X-Malfoid-Edit") === "1";
      const data = await res.json();
      setPicks(data.picks);
    } catch (err) {
      // No picks file: the list is simply empty.
    }
  }

  function setPicks(list) {
    picks = Array.isArray(list) ? list.filter((f) => typeof f === "string") : [];
    pickSet = new Set(picks);
  }

  // A heart button for the owner, or a small read-only heart on picked works for everyone else.
  function makePick() {
    if (picksEditable) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "pick-btn";
      b.innerHTML = HEART_SVG;
      b.addEventListener("click", (e) => {
        e.stopPropagation();
        if (b._item) togglePick(b._item);
      });
      return b;
    }
    const s = document.createElement("span");
    s.className = "pick-badge";
    s.innerHTML = HEART_SVG;
    return s;
  }

  function setPick(pickEl, it) {
    pickEl._item = it;
    const on = pickSet.has(it.f);
    if (pickEl.tagName === "BUTTON") {
      pickEl.setAttribute("aria-pressed", String(on));
      pickEl.setAttribute("aria-label", on ? "Remove from My Personal Pick" : "Add to My Personal Pick");
    } else {
      pickEl.hidden = !on;
    }
    pickEl.parentElement?.classList.toggle("has-pick", picksEditable || on);
  }

  function refreshPicks() {
    for (const pickEl of document.querySelectorAll(".pick-btn, .pick-badge")) if (pickEl._item) setPick(pickEl, pickEl._item);
    for (const hit of document.querySelectorAll(".card-hit")) if (hit._item) hit.setAttribute("aria-label", cardLabel(hit._item));
    renderPickShelf(true);
    if (state.kind === "pick") applyFilters();
    if (el.viewer.open) renderViewerPick(viewerList[viewerIndex]);
  }

  function togglePick(it) {
    const before = picks.slice();
    setPicks(pickSet.has(it.f) ? picks.filter((f) => f !== it.f) : [it.f, ...picks]);
    refreshPicks();
    const body = JSON.stringify({ picks });
    pickSave = pickSave
      .then(async () => {
        const res = await fetch("api/picks", { method: "PUT", headers: { "Content-Type": "application/json" }, body });
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      })
      .catch(async (err) => {
        console.error("Couldn't save My Personal Pick:", err);
        // Fall back to whatever the server holds, or to the list before this change.
        try {
          const res = await fetch("api/picks", { cache: "no-store" });
          setPicks(res.ok ? (await res.json()).picks : before);
        } catch {
          setPicks(before);
        }
        refreshPicks();
      });
  }

  // ---------- Virtualized grid ----------
  // Renders only the cards near the viewport, so thousands of works stay fast.
  // Optional groups add a heading row before each group (used by the timeline).

  function makeVirtualGrid({ container, isActive, onOpen, headerHeight = 64 }) {
    const cards = new Map(); // item index -> element
    const heads = new Map(); // group index -> element
    const cardPool = [];
    const headPool = [];
    let list = [];
    let groups = null; // [{label, sub, start, end}]
    let rows = []; // [{y, h, head: groupIndex} | {y, h, start, end}]
    let cols = 1;
    let cellW = 0;
    let cellH = 0;
    let gap = 12;
    let top = 0;
    let frame = 0;

    function measure() {
      const width = container.clientWidth;
      gap = width < 640 ? 8 : 12;
      const minCell = width < 640 ? 150 : 210;
      cols = Math.max(2, Math.floor((width + gap) / (minCell + gap)));
      cellW = (width - gap * (cols - 1)) / cols;
      cellH = Math.round(cellW * CELL_RATIO);
      rows = [];
      let y = 0;
      const addCards = (from, to) => {
        for (let i = from; i < to; i += cols) {
          rows.push({ y, h: cellH, start: i, end: Math.min(to, i + cols) });
          y += cellH + gap;
        }
      };
      if (groups) {
        groups.forEach((g, gi) => {
          rows.push({ y, h: headerHeight, head: gi });
          y += headerHeight;
          addCards(g.start, g.end);
          y += gi < groups.length - 1 ? 16 : 0;
        });
      } else {
        addCards(0, list.length);
      }
      container.style.height = Math.max(0, y - (groups ? 0 : gap)) + "px";
      top = container.getBoundingClientRect().top + window.scrollY;
    }

    function makeCard() {
      const node = document.createElement("div");
      node.className = "card";
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "card-hit";
      const img = document.createElement("img");
      img.alt = "";
      img.decoding = "async";
      img.addEventListener("load", () => img.classList.add("ready"));
      const badges = document.createElement("span");
      badges.className = "badges";
      const meta = document.createElement("span");
      meta.className = "card-meta";
      const handle = document.createElement("span");
      handle.className = "card-handle";
      const likes = document.createElement("span");
      likes.className = "card-likes";
      meta.append(handle, likes);
      btn.append(img, badges, meta);
      btn.addEventListener("click", () => onOpen(Number(node.dataset.index), list, btn));
      node.append(btn, makePick());
      return node;
    }

    function fillCard(node, index, x, y) {
      const it = list[index];
      node.dataset.index = index;
      const hit = node.firstChild;
      const img = hit.firstChild;
      const src = thumbUrl(it);
      if (img.getAttribute("src") !== src) {
        img.classList.remove("ready");
        img.src = src;
      }
      hit._item = it;
      hit.setAttribute("aria-label", cardLabel(it));
      setPick(node.lastChild, it);
      node.querySelector(".card-handle").textContent = "@" + it.a;
      node.querySelector(".card-likes").textContent = it.v != null ? "♥ " + fmtLikes(it.v) : "";
      const badges = node.querySelector(".badges");
      badges.replaceChildren();
      if (it.k === "anim") badges.append(badge(it.x === "gif" ? "GIF" : "Video", true));
      if (it.l) badges.append(badge("Lost tweet", false, "badge-lost"));
      node.style.width = cellW + "px";
      node.style.height = cellH + "px";
      node.style.transform = `translate(${x}px, ${y}px)`;
    }

    function makeHead() {
      const div = document.createElement("div");
      div.className = "group-head";
      const h = document.createElement("h3");
      const sub = document.createElement("span");
      div.append(h, sub);
      return div;
    }

    function fillHead(node, gi, y) {
      const g = groups[gi];
      node.firstChild.textContent = g.label;
      node.lastChild.textContent = g.sub;
      node.classList.toggle("first", gi === 0);
      node.style.height = headerHeight + "px";
      node.style.transform = `translateY(${y}px)`;
    }

    function recycleAll() {
      for (const [k, n] of cards) (n.remove(), cardPool.push(n), cards.delete(k));
      for (const [k, n] of heads) (n.remove(), headPool.push(n), heads.delete(k));
    }

    function firstRowAt(y) {
      let lo = 0;
      let hi = rows.length - 1;
      while (lo < hi) {
        const mid = (lo + hi) >> 1;
        if (rows[mid].y + rows[mid].h < y) lo = mid + 1;
        else hi = mid;
      }
      return lo;
    }

    function render() {
      frame = 0;
      if (!isActive() || !rows.length) {
        recycleAll();
        return;
      }
      const pad = (cellH + gap) * OVERSCAN;
      const from = window.scrollY - top - pad;
      const to = window.scrollY - top + window.innerHeight + pad;
      const wantCards = new Map();
      const wantHeads = new Map();
      for (let r = firstRowAt(from); r < rows.length && rows[r].y <= to; r++) {
        const row = rows[r];
        if (row.head !== undefined) wantHeads.set(row.head, row.y);
        else for (let i = row.start; i < row.end; i++) wantCards.set(i, [(i - row.start) * (cellW + gap), row.y]);
      }
      for (const [k, n] of cards) if (!wantCards.has(k)) (n.remove(), cardPool.push(n), cards.delete(k));
      for (const [k, n] of heads) if (!wantHeads.has(k)) (n.remove(), headPool.push(n), heads.delete(k));
      const frag = document.createDocumentFragment();
      for (const [gi, y] of wantHeads) {
        let n = heads.get(gi);
        if (!n) {
          n = headPool.pop() || makeHead();
          heads.set(gi, n);
          frag.append(n);
        }
        fillHead(n, gi, y);
      }
      for (const [i, [x, y]] of wantCards) {
        let n = cards.get(i);
        if (!n) {
          n = cardPool.pop() || makeCard();
          cards.set(i, n);
          frag.append(n);
        }
        fillCard(n, i, x, y);
      }
      container.append(frag);
    }

    function schedule() {
      if (!frame) frame = requestAnimationFrame(render);
    }

    function setData(newList, newGroups = null) {
      list = newList;
      groups = newGroups;
      measure();
      recycleAll();
      render();
    }

    function relayout() {
      if (!isActive()) return;
      measure();
      recycleAll();
      render();
    }

    function rowYOfIndex(index) {
      const row = rows.find((r) => r.head === undefined && index >= r.start && index < r.end);
      return row ? row.y : 0;
    }

    function scrollToIndex(index) {
      const y = top + rowYOfIndex(index);
      if (y < window.scrollY + 70 || y + cellH > window.scrollY + window.innerHeight) window.scrollTo({ top: y - 90 });
    }

    function scrollToGroup(gi, offset) {
      const row = rows.find((r) => r.head === gi);
      if (row) window.scrollTo({ top: top + row.y - offset });
    }

    // The group whose heading was last scrolled past.
    function groupAt(offset) {
      if (!groups || !rows.length) return -1;
      const y = window.scrollY - top + offset + 1;
      let gi = 0;
      for (const r of rows) {
        if (r.y > y) break;
        if (r.head !== undefined) gi = r.head;
      }
      return gi;
    }

    function focusIndex(index) {
      const n = cards.get(index);
      if (n) n.firstChild.focus({ preventScroll: true });
    }

    window.addEventListener("scroll", schedule, { passive: true });
    new ResizeObserver(relayout).observe(container);
    return { setData, schedule, relayout, scrollToIndex, scrollToGroup, groupAt, focusIndex };
  }

  const grid = makeVirtualGrid({
    container: el.grid,
    isActive: () => state.view === "gallery",
    onOpen: (i, list, btn) => openViewer(i, list, btn),
  });

  const tlGrid = makeVirtualGrid({
    container: $("#tl-grid"),
    isActive: () => state.view === "timeline",
    onOpen: (i, list, btn) => openViewer(i, list, btn),
  });

  // ---------- Viewer ----------

  function openViewer(index, list = viewerList, from = null) {
    if (index < 0 || index >= list.length) return;
    viewerList = list;
    viewerIndex = index;
    if (from) viewerReturnFocus = from;
    const it = list[index];
    renderViewer(it);
    if (!el.viewer.open) {
      el.viewer.showModal();
      document.documentElement.style.overflow = "hidden";
    }
    writeUrl(it.f);
    preloadNeighbours(index);
  }

  function closeViewer() {
    if (el.viewer.open) el.viewer.close();
  }

  el.viewer.addEventListener("close", () => {
    el.viewerMedia.replaceChildren(); // stops any playing video
    document.documentElement.style.overflow = "";
    writeUrl();
    const g = state.view === "gallery" && viewerList === filtered ? grid : state.view === "timeline" && viewerList === tlView.list ? tlGrid : null;
    if (g) {
      g.scrollToIndex(viewerIndex);
      g.schedule();
      requestAnimationFrame(() => g.focusIndex(viewerIndex));
    } else if (viewerReturnFocus && viewerReturnFocus.isConnected) {
      viewerReturnFocus.focus({ preventScroll: true });
    }
    viewerReturnFocus = null;
  });

  function renderViewer(it) {
    const isVideo = VIDEO_EXTS.has(it.x);
    el.viewerNote.hidden = true;
    const current = () => viewerList[viewerIndex] === it;
    const showMissing = () => {
      el.viewerNote.textContent = "The full-size file isn't uploaded yet, so this is a preview.";
      el.viewerNote.hidden = false;
    };

    let media;
    if (isVideo) {
      media = document.createElement("video");
      media.poster = thumbUrl(it);
      media.controls = true;
      media.autoplay = true;
      media.loop = true;
      media.muted = true;
      media.playsInline = true;
      media.preload = "auto";
      media.addEventListener("error", showMissing, { once: true });
      media.src = mediaUrl(it);
    } else {
      // Show the thumbnail at once, then swap in the full file once it has loaded.
      media = document.createElement("img");
      media.alt = `Work by @${it.a}`;
      media.src = thumbUrl(it);
      const full = new Image();
      full.decoding = "async";
      full.onload = () => {
        if (current()) media.src = full.src;
      };
      full.onerror = () => {
        if (current()) showMissing();
      };
      full.src = mediaUrl(it);
    }
    el.viewerMedia.replaceChildren(media);

    el.viewerPos.textContent = `${numberFmt.format(viewerIndex + 1)} of ${numberFmt.format(viewerList.length)}`;
    el.viewerArtist.textContent = "@" + it.a;
    el.viewerArtist.href = profileUrl(it);

    el.viewerTags.replaceChildren();
    el.viewerTags.append(badge(it.k === "anim" ? "Animation" : "Static art", false));
    el.viewerTags.append(badge(it.x.toUpperCase(), false));
    if (it.l) el.viewerTags.append(badge("Lost tweet", false, "badge-lost"));

    el.viewerLost.hidden = !it.l;
    if (it.l) el.viewerLost.textContent = it.l;

    el.viewerMeta.replaceChildren();
    const followers = artistStats.get(it._al)?.followers;
    const rows = [
      ["Likes", it.v != null ? `About ${fmtLikes(it.v)}` : "Unknown"],
      ["Artist followers", followers != null ? `About ${fmtLikes(followers)}` : "Unknown"],
      ["Posted", fmtDate(it.p)],
      ["Added", fmtDate(it.d)],
      ["Tweet ID", it.t || "Unknown"],
      ["Image", it.t ? `#${it.i} in tweet` : "Single file"],
      ["File", it.f],
    ];
    for (const [k, v] of rows) {
      const dt = document.createElement("dt");
      dt.textContent = k;
      const dd = document.createElement("dd");
      dd.textContent = v;
      el.viewerMeta.append(dt, dd);
    }

    const tUrl = tweetUrl(it);
    el.viewerTweet.hidden = !tUrl;
    if (tUrl) {
      el.viewerTweet.href = tUrl;
      el.viewerTweet.textContent = it.l ? "Try the original tweet ↗" : "View tweet on X ↗";
    }
    el.viewerDanbooru.hidden = !(it.b && isSafeUrl(it.b));
    if (!el.viewerDanbooru.hidden) el.viewerDanbooru.href = it.b;

    const stats = artistStats.get(it._al);
    const n = stats ? stats.count : 1;
    const filteringThisArtist = state.view === "gallery" && state.artist.toLowerCase() === it._al;
    el.viewerMore.textContent = filteringThisArtist ? "Show all artists" : `All ${plural(n, "work", "works")} by @${it.a}`;

    el.prev.disabled = viewerIndex <= 0;
    el.next.disabled = viewerIndex >= viewerList.length - 1;
    renderViewerPick(it);
  }

  function renderViewerPick(it) {
    const b = $("#viewer-pick");
    b.hidden = !picksEditable || !it;
    if (b.hidden) return;
    const on = pickSet.has(it.f);
    b.setAttribute("aria-pressed", String(on));
    b.querySelector("span").textContent = on ? "Remove from My Personal Pick" : "Add to My Personal Pick";
  }

  function preloadNeighbours(index) {
    for (const j of [index + 1, index - 1]) {
      const it = viewerList[j];
      if (it && it.k !== "anim") new Image().src = thumbUrl(it);
    }
  }

  $("#viewer-pick").addEventListener("click", () => {
    const it = viewerList[viewerIndex];
    if (it) togglePick(it);
  });
  el.prev.addEventListener("click", () => openViewer(viewerIndex - 1));
  el.next.addEventListener("click", () => openViewer(viewerIndex + 1));
  $("#close").addEventListener("click", closeViewer);
  el.viewerMore.addEventListener("click", () => {
    const it = viewerList[viewerIndex];
    const showAll = state.view === "gallery" && state.artist.toLowerCase() === it._al;
    viewerReturnFocus = null;
    if (state.view === "gallery") {
      setArtist(showAll ? "" : it.a);
    } else {
      showGallery({ artist: it.a, q: "", likes: "", kind: "" });
    }
    // The close handler scrolls back to this work in the new list.
    viewerList = filtered;
    viewerIndex = filtered.indexOf(it);
    closeViewer();
  });
  el.viewer.addEventListener("keydown", (e) => {
    if (e.target instanceof HTMLVideoElement && (e.key === "ArrowLeft" || e.key === "ArrowRight")) return;
    if (e.key === "ArrowLeft") openViewer(viewerIndex - 1);
    else if (e.key === "ArrowRight") openViewer(viewerIndex + 1);
  });
  // Clicking the dark area around the media closes the viewer.
  el.viewerMedia.addEventListener("click", (e) => {
    if (e.target === el.viewerMedia) closeViewer();
  });

  // Swipe left and right on touch screens.
  let touchX = null;
  el.viewerMedia.addEventListener("touchstart", (e) => (touchX = e.touches[0].clientX), { passive: true });
  el.viewerMedia.addEventListener("touchend", (e) => {
    if (touchX === null) return;
    const dx = e.changedTouches[0].clientX - touchX;
    touchX = null;
    if (Math.abs(dx) > 50) openViewer(viewerIndex + (dx < 0 ? 1 : -1));
  });

  // ---------- Home: shelves ----------

  const shelves = { pick: [], fame: [], big: [], gems: [], hidden: [], small: [], new: [] };
  const shelfPools = { big: [], gems: [], hidden: [], small: [], new: [] };

  // A small seeded shuffle, so the shelves change once a day and stay put on reload.
  function seededShuffle(list, seed) {
    const out = list.slice();
    let s = seed >>> 0 || 1;
    const rand = () => ((s = (s * 1664525 + 1013904223) >>> 0) / 2 ** 32);
    for (let i = out.length - 1; i > 0; i--) {
      const j = Math.floor(rand() * (i + 1));
      [out[i], out[j]] = [out[j], out[i]];
    }
    return out;
  }

  function buildShelves() {
    // One entry per tweet: its first image, carrying the tweet's like count.
    const byTweet = new Map();
    for (const it of items) {
      if (it.v == null || !it.t || it.s >= LONG_VIDEO) continue;
      const cur = byTweet.get(it.t);
      if (!cur || it.i < cur.i) byTweet.set(it.t, it);
    }
    const tweets = [...byTweet.values()];

    shelves.fame = tweets.filter((it) => it.v > FAME_MIN).sort((a, b) => b.v - a.v);

    const bestByArtist = new Map(); // artist -> most-liked tweet
    const bestGem = Object.fromEntries(Object.keys(GEM_RANGES).map((k) => [k, new Map()])); // shelf -> artist -> most-liked tweet in range
    for (const it of tweets) {
      const best = bestByArtist.get(it._al);
      if (!best || it.v > best.v) bestByArtist.set(it._al, it);
      for (const [k, [min, max]] of Object.entries(GEM_RANGES)) {
        if (it.v < min || it.v >= max) continue;
        const gem = bestGem[k].get(it._al);
        if (!gem || it.v > gem.v) bestGem[k].set(it._al, it);
      }
    }
    // New Malfoid and Small Artists each show an artist's best work, so those artists stay off the gem shelves.
    const bests = [...bestByArtist.values()];
    shelfPools.new = bests.filter((it) => it.v < NEW_MAX);
    shelfPools.small = bests.filter(
      (it) => it.v >= NEW_MAX && (artistStats.get(it._al).followers ?? Infinity) < SMALL_FOLLOWERS
    );
    const small = new Set(shelfPools.small.map((it) => it._al));
    for (const k of Object.keys(GEM_RANGES)) shelfPools[k] = [...bestGem[k].values()].filter((it) => !small.has(it._al));

    const day = Math.floor(Date.now() / 864e5);
    shuffleShelf("big", day * 17 + 7);
    shuffleShelf("gems", day * 11 + 1);
    shuffleShelf("hidden", day);
    shuffleShelf("small", day * 7 + 3);
    shuffleShelf("new", day * 13 + 5);
    renderShelf("fame");

  }

  function shuffleShelf(name, seed) {
    shelves[name] = seededShuffle(shelfPools[name], seed ?? Math.random() * 2 ** 32)
      .slice(0, SHELF_SIZE)
      .sort((a, b) => b.v - a.v);
    renderShelf(name);
  }

  function renderPickShelf(keepScroll) {
    shelves.pick = picks.map((f) => itemByFile.get(f)).filter(Boolean);
    $("#my-pick").hidden = !shelves.pick.length && !picksEditable;
    renderShelf("pick", keepScroll);
  }

  function renderShelf(name, keepScroll) {
    const track = $(`#${name}-track`);
    const list = shelves[name];
    const scroll = track.scrollLeft;
    track.replaceChildren();
    track.scrollLeft = 0;
    if (!list.length) {
      const p = document.createElement("p");
      p.className = "shelf-empty";
      p.textContent =
        name === "pick"
          ? "Tap ♥ on any work to pick it"
          : name === "small"
          ? "Follower counts haven't been fetched yet. Run tools/fetch_followers.py and rebuild."
          : "Like counts haven't been fetched yet. Run tools/fetch_likes.py and rebuild.";
      track.append(p);
    }
    list.forEach((it, i) => {
      const card = document.createElement("div");
      card.className = "shelf-card";
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "card-hit";
      btn._item = it;
      btn.setAttribute("aria-label", cardLabel(it));
      const img = document.createElement("img");
      img.alt = "";
      img.loading = "lazy";
      img.decoding = "async";
      img.src = thumbUrl(it);
      btn.append(img);
      if (name === "fame") {
        const rank = document.createElement("span");
        rank.className = "rank";
        rank.textContent = "#" + (i + 1);
        btn.append(rank);
      } else if (it.k === "anim") {
        const badges = document.createElement("span");
        badges.className = "badges";
        badges.append(badge(it.x === "gif" ? "GIF" : "Video", true));
        btn.append(badges);
      }
      const meta = document.createElement("span");
      meta.className = "card-meta";
      const handle = document.createElement("span");
      handle.className = "card-handle";
      handle.textContent = "@" + it.a;
      const likes = document.createElement("span");
      likes.className = "card-likes";
      likes.textContent = it.v != null ? "♥ " + fmtLikes(it.v) : "";
      meta.append(handle, likes);
      btn.append(meta);
      btn.addEventListener("click", () => openViewer(i, list, btn));
      const pickEl = makePick();
      card.append(btn, pickEl);
      setPick(pickEl, it);
      track.append(card);
    });
    if (keepScroll) track.scrollLeft = scroll;
    updateShelfButtons(track);
  }

  function updateShelfButtons(track) {
    const shelf = track.closest(".shelf");
    const [prev, next] = shelf.querySelectorAll(".shelf-btn");
    prev.disabled = track.scrollLeft <= 2;
    next.disabled = track.scrollLeft + track.clientWidth >= track.scrollWidth - 2;
  }

  $("#fame-link").addEventListener("click", (e) => {
    e.preventDefault();
    showGallery({ sort: "liked", q: "", artist: "", likes: "", kind: "" });
  });
  $("#pick-link").addEventListener("click", (e) => {
    e.preventDefault();
    showGallery({ kind: "pick", q: "", artist: "", likes: "" });
  });

  for (const shelf of document.querySelectorAll(".shelf")) {
    const track = shelf.querySelector(".shelf-track");
    let frame = 0;
    track.addEventListener(
      "scroll",
      () => {
        if (!frame) frame = requestAnimationFrame(() => ((frame = 0), updateShelfButtons(track)));
      },
      { passive: true }
    );
    for (const btn of shelf.querySelectorAll(".shelf-btn")) {
      btn.addEventListener("click", () => track.scrollBy({ left: Number(btn.dataset.scroll) * track.clientWidth * 0.85 }));
    }
    const shuffle = shelf.querySelector(".shelf-shuffle");
    if (shuffle) shuffle.addEventListener("click", () => shuffleShelf(shuffle.dataset.shuffle));
    new ResizeObserver(() => updateShelfButtons(track)).observe(track);
  }

  // ---------- Home: timeline (rebuilt from the "Malfoid Art Timeline" Claude dashboard) ----------

  const timeline = { daily: [], weekly: [], mode: "posted", summary: null };
  const dayKeyFmt = new Intl.DateTimeFormat("en-CA", { timeZone: TIMEZONE, year: "numeric", month: "2-digit", day: "2-digit" });
  const dayKey = (ms) => dayKeyFmt.format(new Date(ms));
  const DAY = 864e5;
  const keyMs = (k) => Date.parse(k + "T00:00:00Z");
  const keyIso = (ms) => new Date(ms).toISOString().slice(0, 10);
  const shortDay = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
  const longDay = new Intl.DateTimeFormat("en-US", { weekday: "short", month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
  const fmtKey = (k) => shortDay.format(new Date(keyMs(k)));
  const fmtKeyLong = (k) => longDay.format(new Date(keyMs(k)));
  const round1 = (n) => Math.round(n * 10) / 10;

  function buildTimeline() {
    const images = items
      .filter((it) => !VIDEO_EXTS.has(it.x))
      .map((it) => ({ artist: it._al, post: it.t || it.f, posted: dayKey(it.p || it.d), saved: dayKey(it.d) }));

    const firstSeen = new Map();
    for (const im of images) {
      const f = firstSeen.get(im.artist);
      if (!f || im.posted < f) firstSeen.set(im.artist, im.posted);
    }

    const days = new Map();
    const day = (k) => {
      if (!days.has(k)) days.set(k, { date: k, posted: 0, saved: 0, posts: new Set(), artists: new Set() });
      return days.get(k);
    };
    for (const im of images) {
      const d = day(im.posted);
      d.posted++;
      d.posts.add(im.post);
      d.artists.add(im.artist);
      day(im.saved).saved++;
    }
    const keys = [...days.keys()].sort();
    const last = keys[keys.length - 1];
    // The chart starts at the first day with art on or after the start date, as in the original dashboard.
    const from = keys.find((k) => k >= TIMELINE_START) || TIMELINE_START;
    for (let t = keyMs(from); t <= keyMs(last); t += DAY) day(keyIso(t));
    const count = (k, f) => (days.has(k) ? days.get(k)[f] : 0);
    const avg7 = (k, f) => {
      let sum = 0;
      for (let i = 0; i < 7; i++) sum += count(keyIso(keyMs(k) - i * DAY), f);
      return round1(sum / 7);
    };
    timeline.daily = [...days.values()]
      .filter((d) => d.date >= from)
      .sort((a, b) => (a.date < b.date ? -1 : 1))
      .map((d) => ({
        date: d.date,
        posted: d.posted,
        saved: d.saved,
        posted_avg7: avg7(d.date, "posted"),
        saved_avg7: avg7(d.date, "saved"),
        posts: d.posts.size,
        artists: d.artists.size,
        new_artists: [...d.artists].filter((a) => firstSeen.get(a) === d.date).length,
      }));

    // Monday-to-Sunday weeks, newest first.
    const monday = (k) => keyIso(keyMs(k) - ((new Date(keyMs(k)).getUTCDay() + 6) % 7) * DAY);
    const weeks = new Map();
    const week = (k) => {
      if (!weeks.has(k)) weeks.set(k, { start: k, posted: 0, saved: 0, posts: new Set(), artists: new Set(), fresh: new Set() });
      return weeks.get(k);
    };
    for (const im of images) {
      if (im.posted >= TIMELINE_START) {
        const w = week(monday(im.posted));
        w.posted++;
        w.posts.add(im.post);
        w.artists.add(im.artist);
        if (firstSeen.get(im.artist) === im.posted) w.fresh.add(im.artist);
      }
      if (im.saved >= TIMELINE_START) week(monday(im.saved)).saved++;
    }
    timeline.weekly = [...weeks.values()]
      .sort((a, b) => (a.start < b.start ? 1 : -1))
      .map((w) => {
        const span = Math.min(7, Math.round((keyMs(last) - keyMs(w.start)) / DAY) + 1);
        return {
          start: w.start,
          end: keyIso(keyMs(w.start) + 6 * DAY),
          posted: w.posted,
          perDay: round1(w.posted / span),
          posts: w.posts.size,
          artists: w.artists.size,
          fresh: w.fresh.size,
          saved: w.saved,
        };
      });

    // Headline numbers, by date posted.
    const peak = timeline.daily.reduce((a, b) => (b.posted > a.posted ? b : a), timeline.daily[0]);
    const lastPosted = [...timeline.daily].reverse().find((d) => d.posted > 0) || timeline.daily[timeline.daily.length - 1];
    const bestWeek = Math.max(...timeline.daily.map((d) => d.posted_avg7));
    const before = images.filter((im) => im.posted < TIMELINE_START).length;
    timeline.summary = {
      images: images.length,
      posts: new Set(images.map((im) => im.post)).size,
      artists: new Set(images.map((im) => im.artist)).size,
      peakDate: peak.date,
      peakImages: peak.posted,
      lastDate: lastPosted.date,
      last7: lastPosted.posted_avg7,
      change: bestWeek ? lastPosted.posted_avg7 / bestWeek - 1 : 0,
      before,
    };
    renderTimelineText();
    renderWeekly();
    drawChart();
  }

  function renderTimelineText() {
    const s = timeline.summary;

    $("#t-images").textContent = numberFmt.format(s.images);
    $("#t-artists").textContent = numberFmt.format(s.artists);
    $("#t-peak").textContent = numberFmt.format(s.peakImages);
    $("#t-peak-date").textContent = fmtKey(s.peakDate);
    $("#t-last7").textContent = String(s.last7);
  }

  function renderWeekly() {
    const tbody = $("#weekly tbody");
    tbody.replaceChildren();
    const sameYear = (a, b) => a.slice(0, 4) === b.slice(0, 4);
    for (const w of timeline.weekly) {
      const tr = document.createElement("tr");
      const cells = [
        `${fmtKey(w.start)} – ${sameYear(w.start, w.end) && w.start.slice(5, 7) === w.end.slice(5, 7) ? w.end.slice(8).replace(/^0/, "") : fmtKey(w.end)}`,
        numberFmt.format(w.posted),
        String(w.perDay),
        numberFmt.format(w.posts),
        numberFmt.format(w.artists),
        numberFmt.format(w.fresh),
        numberFmt.format(w.saved),
      ];
      for (const c of cells) {
        const td = document.createElement("td");
        td.textContent = c;
        tr.append(td);
      }
      tbody.append(tr);
    }
  }

  const SVG_NS = "http://www.w3.org/2000/svg";
  const svgEl = (name, attrs = {}) => {
    const n = document.createElementNS(SVG_NS, name);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    return n;
  };

  // A round tick step (1, 2 or 5 times a power of ten) giving about four gridlines.
  function niceScale(v) {
    const raw = Math.max(v, 1) / 4;
    const mag = 10 ** Math.floor(Math.log10(raw));
    const step = [1, 2, 5, 10].map((m) => m * mag).find((s) => s >= raw);
    return { step, max: Math.ceil(Math.max(v, 1) / step) * step };
  }

  function drawChart() {
    const box = $("#chart");
    const data = timeline.daily;
    if (!data.length || !box.clientWidth) return;
    const mode = timeline.mode;
    const W = box.clientWidth;
    const H = box.clientHeight;
    const m = { top: 28, right: 8, bottom: 26, left: 36 };
    const iw = W - m.left - m.right;
    const ih = H - m.top - m.bottom;
    const { step: tickStep, max } = niceScale(Math.max(...data.map((d) => Math.max(d[mode], d[mode + "_avg7"]))));
    const step = iw / data.length;
    const barW = Math.max(1, Math.min(18, step - 2)); // 2px surface gap between bars
    const x = (i) => m.left + i * step + (step - barW) / 2;
    const cx = (i) => m.left + i * step + step / 2;
    const y = (v) => m.top + ih - (v / max) * ih;

    const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": $("#chart-title").textContent });
    const gridG = svgEl("g", { class: "grid" });
    const axis = svgEl("g", { class: "axis" });
    for (let v = 0; v <= max; v += tickStep) {
      gridG.append(svgEl("line", { x1: m.left, x2: W - m.right, y1: y(v), y2: y(v) }));
      const label = svgEl("text", { x: m.left - 8, y: y(v) + 4, "text-anchor": "end" });
      label.textContent = numberFmt.format(Math.round(v));
      axis.append(label);
    }
    // Label Mondays, thinned so labels never collide.
    const every = Math.max(1, Math.ceil(64 / (step * 7)));
    let mondays = 0;
    data.forEach((d, i) => {
      if (new Date(keyMs(d.date)).getUTCDay() !== 1) return;
      if (mondays++ % every) return;
      const label = svgEl("text", { x: cx(i), y: H - 6, "text-anchor": "middle" });
      label.textContent = fmtKey(d.date);
      axis.append(label);
    });
    svg.append(gridG, axis);

    // Bars with 4px rounded tops anchored to the baseline.
    const bars = svgEl("g");
    const r = Math.min(4, barW / 2);
    data.forEach((d, i) => {
      const v = d[mode];
      if (!v) return;
      const top = y(v);
      const h = m.top + ih - top;
      const rr = Math.min(r, h);
      const x0 = x(i);
      const path = `M${x0},${m.top + ih}V${top + rr}Q${x0},${top} ${x0 + rr},${top}H${x0 + barW - rr}Q${x0 + barW},${top} ${x0 + barW},${top + rr}V${m.top + ih}Z`;
      bars.append(svgEl("path", { d: path, class: "bar", "data-i": i }));
    });
    svg.append(bars);

    // Label the busiest day, the one direct label on the chart.
    const peakI = data.reduce((best, d, i) => (d[mode] > data[best][mode] ? i : best), 0);
    if (data[peakI][mode]) {
      const label = svgEl("text", { class: "peak-label", y: y(data[peakI][mode]) - 8, "text-anchor": "middle" });
      label.textContent = `${fmtKey(data[peakI].date)} · ${numberFmt.format(data[peakI][mode])}`;
      const half = 44;
      label.setAttribute("x", Math.min(W - m.right - half, Math.max(m.left + half, cx(peakI))));
      svg.append(label);
    }

    const line = data.map((d, i) => `${i ? "L" : "M"}${cx(i).toFixed(1)},${y(d[mode + "_avg7"]).toFixed(1)}`).join("");
    svg.append(svgEl("path", { d: line, class: "avg" }));

    const cross = svgEl("line", { class: "cross", y1: m.top, y2: m.top + ih, visibility: "hidden" });
    const dot = svgEl("circle", { class: "dot", r: 4, visibility: "hidden" });
    svg.append(cross, dot);

    const tip = document.createElement("div");
    tip.className = "tooltip";
    tip.hidden = true;
    box.replaceChildren(svg, tip);

    const hide = () => {
      tip.hidden = true;
      cross.setAttribute("visibility", "hidden");
      dot.setAttribute("visibility", "hidden");
      for (const b of bars.children) b.classList.remove("dim");
    };
    const show = (clientX) => {
      const rect = svg.getBoundingClientRect();
      const i = Math.max(0, Math.min(data.length - 1, Math.floor((clientX - rect.left - m.left) / step)));
      const d = data[i];
      cross.setAttribute("x1", cx(i));
      cross.setAttribute("x2", cx(i));
      cross.setAttribute("visibility", "visible");
      dot.setAttribute("cx", cx(i));
      dot.setAttribute("cy", y(d[mode + "_avg7"]));
      dot.setAttribute("visibility", "visible");
      for (const b of bars.children) b.classList.toggle("dim", Number(b.dataset.i) !== i);

      tip.replaceChildren();
      const title = document.createElement("strong");
      title.textContent = fmtKeyLong(d.date);
      tip.append(title);
      const row = (swatch, label, value) => {
        const div = document.createElement("div");
        const l = document.createElement("span");
        if (swatch) {
          const sw = document.createElement("i");
          sw.className = swatch;
          l.append(sw);
        }
        l.append(label);
        const v = document.createElement("span");
        v.textContent = value;
        div.append(l, v);
        tip.append(div);
      };
      row("sw-bar", mode === "posted" ? "Images posted" : "Images saved", numberFmt.format(d[mode]));
      row("sw-line", "7-day average", String(d[mode + "_avg7"]));
      if (mode === "posted") {
        row("", "Posts", numberFmt.format(d.posts));
        row("", "Artists", numberFmt.format(d.artists));
        row("", "New artists", numberFmt.format(d.new_artists));
      }
      tip.hidden = false;
      const tw = tip.offsetWidth;
      const left = cx(i) + 12 + tw > W ? cx(i) - 12 - tw : cx(i) + 12;
      tip.style.left = Math.max(0, left) + "px";
      tip.style.top = m.top + "px";
    };
    svg.addEventListener("pointermove", (e) => show(e.clientX));
    svg.addEventListener("pointerdown", (e) => show(e.clientX));
    svg.addEventListener("pointerleave", hide);
  }

  for (const b of document.querySelectorAll(".seg-small button")) {
    b.addEventListener("click", () => {
      timeline.mode = b.dataset.mode;
      for (const o of document.querySelectorAll(".seg-small button")) o.setAttribute("aria-pressed", String(o === b));
      $("#chart-title").textContent =
        timeline.mode === "posted" ? "Images per day, by date posted" : "Images per day, by date saved to the folder";
      drawChart();
    });
  }
  new ResizeObserver(() => state.view === "home" && drawChart()).observe($("#chart"));

  // ---------- Timeline view: every work by date posted, oldest first ----------

  const tlView = { list: [], groups: [], months: [], activeMonth: "" };
  const JUMP_OFFSET = () => (window.innerWidth <= 640 ? 56 : 60) + $("#tl-jump").offsetHeight;
  const monthFmt = new Intl.DateTimeFormat("en-US", { month: "short", year: "numeric", timeZone: "UTC" });
  const fullDayFmt = new Intl.DateTimeFormat("en-US", { month: "long", day: "numeric", year: "numeric", timeZone: "UTC" });

  function buildTimelineView() {
    const when = (it) => it.p || it.d;
    tlView.list = items
      .filter((it) => dayKey(when(it)) < TIMELINE_VIEW_END)
      .sort((a, b) => when(a) - when(b) || (a.f < b.f ? -1 : 1));
    tlView.groups = [];
    tlView.months = [];
    let cur = null;
    tlView.list.forEach((it, i) => {
      const key = dayKey(when(it));
      if (!cur || cur.key !== key) {
        cur = { key, start: i, end: i };
        tlView.groups.push(cur);
      }
      cur.end = i + 1;
    });
    tlView.groups.forEach((g, gi) => {
      const n = g.end - g.start;
      g.label = fullDayFmt.format(new Date(keyMs(g.key)));
      g.sub = "";
      const month = g.key.slice(0, 7);
      const last = tlView.months[tlView.months.length - 1];
      if (!last || last.key !== month) tlView.months.push({ key: month, group: gi, count: n });
      else last.count += n;
    });


    const jump = $("#tl-jump");
    jump.replaceChildren();
    for (const m of tlView.months) {
      const b = document.createElement("button");
      b.type = "button";
      b.dataset.month = m.key;
      b.append(monthFmt.format(new Date(keyMs(m.key + "-01"))));
      b.addEventListener("click", () => tlGrid.scrollToGroup(m.group, JUMP_OFFSET() - 1));
      jump.append(b);
    }
    tlGrid.setData(tlView.list, tlView.groups);
    updateActiveMonth();
  }

  function updateActiveMonth() {
    if (state.view !== "timeline" || !tlView.groups.length) return;
    const gi = Math.max(0, tlGrid.groupAt(JUMP_OFFSET()));
    const month = tlView.groups[gi].key.slice(0, 7);
    if (month === tlView.activeMonth) return;
    tlView.activeMonth = month;
    for (const b of $("#tl-jump").children) {
      const on = b.dataset.month === month;
      b.setAttribute("aria-current", String(on));
      if (on) b.scrollIntoView({ block: "nearest", inline: "nearest" });
    }
  }
  let monthFrame = 0;
  window.addEventListener(
    "scroll",
    () => {
      if (!monthFrame) monthFrame = requestAnimationFrame(() => ((monthFrame = 0), updateActiveMonth()));
    },
    { passive: true }
  );

  // ---------- Artists view ----------

  let artistFrame = 0;
  function renderArtists() {
    const q = el.artistQ.value.trim().toLowerCase().replace(/^@/, "");
    let list = [...artistStats.values()];
    if (q) list = list.filter((a) => a.key.includes(q));
    const by = el.artistSort.value;
    list.sort((a, b) =>
      by === "name"
        ? a.key < b.key ? -1 : a.key > b.key ? 1 : 0
        : by === "recent"
        ? b.latest - a.latest
        : by === "followers"
        ? (b.followers ?? -1) - (a.followers ?? -1) || (a.key < b.key ? -1 : 1)
        : b.count - a.count || (a.key < b.key ? -1 : 1)
    );
    el.artistCount.textContent = plural(list.length, "artist", "artists");

    // Render in chunks so 2,700+ rows never block the main thread.
    cancelAnimationFrame(artistFrame);
    el.artists.replaceChildren();
    let i = 0;
    const step = () => {
      const frag = document.createDocumentFragment();
      for (const end = Math.min(list.length, i + 300); i < end; i++) frag.append(artistRow(list[i]));
      el.artists.append(frag);
      if (i < list.length) artistFrame = requestAnimationFrame(step);
    };
    step();
  }

  function artistRow(a) {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "artist";
    const img = document.createElement("img");
    img.alt = "";
    img.loading = "lazy";
    img.decoding = "async";
    img.src = thumbUrl(a.cover);
    const text = document.createElement("span");
    text.className = "artist-text";
    const name = document.createElement("span");
    name.className = "artist-name";
    name.textContent = "@" + a.handle;
    text.append(name);
    if (a.followers != null) {
      const fo = document.createElement("span");
      fo.className = "artist-followers";
      fo.textContent = `${fmtLikes(a.followers)} ${a.followers === 1 ? "follower" : "followers"}`;
      text.append(fo);
    }
    const count = document.createElement("span");
    count.className = "artist-count";
    count.textContent = numberFmt.format(a.count);
    btn.append(img, text, count);
    btn.addEventListener("click", () => showGallery({ artist: a.handle, q: "" }));
    li.append(btn);
    return li;
  }

  // ---------- Views ----------

  function showView() {
    for (const v of VIEWS) $(`#view-${v}`).hidden = state.view !== v;
    for (const a of document.querySelectorAll(".nav a[data-view]")) {
      if (a.dataset.view === state.view) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    }
    if (state.view === "gallery") grid.relayout();
    else grid.schedule(); // drops grid nodes while hidden
    if (state.view === "timeline") {
      tlGrid.relayout();
      requestAnimationFrame(updateActiveMonth);
    } else tlGrid.schedule();
    if (state.view === "artists" && !el.artists.childElementCount && items.length) renderArtists();
    if (state.view === "home") requestAnimationFrame(drawChart);
  }

  // Back/forward and hand-edited links that only change the #view.
  window.addEventListener("hashchange", () => {
    const hash = location.hash.slice(1);
    const view = VIEWS.includes(hash) ? hash : "home";
    if (view === state.view) return;
    state.view = view;
    showView();
    window.scrollTo({ top: 0 });
  });

  for (const a of document.querySelectorAll(".nav a[data-view], .brand")) {
    a.addEventListener("click", (e) => {
      e.preventDefault();
      state.view = a.dataset.view || "home";
      showView();
      writeUrl();
      window.scrollTo({ top: 0 });
    });
  }

  // ---------- Theme ----------

  const themeBtn = $("#theme-toggle");
  const systemDark = window.matchMedia("(prefers-color-scheme: dark)");
  const currentTheme = () => document.documentElement.dataset.theme || (systemDark.matches ? "dark" : "light");
  const labelTheme = () => {
    const next = currentTheme() === "dark" ? "light" : "dark";
    themeBtn.setAttribute("aria-label", `Switch to ${next} theme`);
    themeBtn.title = `Switch to ${next} theme`;
  };
  themeBtn.addEventListener("click", () => {
    const next = currentTheme() === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem("theme", next);
    } catch (e) {
      // Private mode or blocked storage: the choice just won't persist.
    }
    labelTheme();
  });
  systemDark.addEventListener("change", labelTheme);
  labelTheme();

  // ---------- Controls ----------

  let searchTimer = 0;
  el.q.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.q = el.q.value;
      applyFilters();
      writeUrl();
    }, 120);
  });
  el.likes.addEventListener("change", () => {
    state.likes = el.likes.value;
    applyFilters();
    writeUrl();
  });
  el.sort.addEventListener("change", () => {
    state.sort = el.sort.value;
    applyFilters();
    writeUrl();
  });
  el.kind.addEventListener("click", (e) => {
    const b = e.target.closest("button[role=radio]");
    if (!b) return;
    state.kind = b.dataset.kind;
    syncControls();
    applyFilters();
    writeUrl();
  });
  el.kind.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    const btns = [...el.kind.querySelectorAll("button[role=radio]")];
    const i = btns.indexOf(document.activeElement);
    if (i < 0) return;
    const next = btns[(i + (e.key === "ArrowRight" ? 1 : btns.length - 1)) % btns.length];
    next.focus();
    next.click();
    e.preventDefault();
  });
  el.artistQ.addEventListener("input", renderArtists);
  el.artistSort.addEventListener("change", renderArtists);

  window.addEventListener("keydown", (e) => {
    if (e.key === "/" && !el.viewer.open && document.activeElement.tagName !== "INPUT" && (state.view === "gallery" || state.view === "artists")) {
      e.preventDefault();
      (state.view === "artists" ? el.artistQ : el.q).focus();
    }
  });

  // ---------- Boot ----------

  function renderHero(data) {
    const lostItems = items.filter((it) => it.l);
    el.heroMeta.textContent =
      `${numberFmt.format(items.length)} works · ${plural(artistStats.size, "artist", "artists")} · Updated ${fmtDate(data.generated)}`;

    // Explain lost tweets, with the reasons as recorded when each one was found missing.
    const reasons = new Map();
    for (const it of lostItems) {
      const r = /suspended/i.test(it.l)
        ? "from a suspended account"
        : /no longer exists/i.test(it.l)
        ? "from an account that no longer exists"
        : /deleted/i.test(it.l)
        ? "deleted by the artist"
        : "unavailable for another reason";
      reasons.set(r, (reasons.get(r) || 0) + 1);
    }
    el.lostNote.replaceChildren();
    if (lostItems.length) {
      const strong = document.createElement("strong");
      strong.textContent = "Lost tweets";
      const parts = [...reasons].sort((a, b) => b[1] - a[1]).map(([r, n]) => `${numberFmt.format(n)} ${r}`);
      const list = parts.length > 1 ? parts.slice(0, -1).join(", ") + " and " + parts[parts.length - 1] : parts[0];
      el.lostNote.append(
        strong,
        ` are works whose original post on X is gone: ${list}. ` +
          "The art was saved before it disappeared, so it stays here with an orange tag, but its link to X no longer opens."
      );
    }
  }

  async function boot() {
    const dash = $("#dashboard-link");
    if (CONFIG.dashboardUrl && isSafeUrl(CONFIG.dashboardUrl)) dash.href = CONFIG.dashboardUrl;
    else dash.hidden = true;

    const workParam = readUrl();
    syncControls();
    showView();

    let data;
    try {
      const [res] = await Promise.all([fetch("data/manifest.json"), loadPicks()]);
      if (!res.ok) throw new Error(res.status + " " + res.statusText);
      data = await res.json();
    } catch (err) {
      el.heroMeta.textContent = "Couldn't load the archive index. Try reloading the page.";
      console.error(err);
      return;
    }

    items = data.items;
    for (const it of items) itemByFile.set(it.f, it);
    if (data.releaseBase && isSafeUrl(data.releaseBase)) releaseBase = data.releaseBase;
    const followers = data.followers || {};
    for (const it of items) {
      it._al = it.a.toLowerCase();
      it._an = it._al.replace(/[_.]/g, ""); // lets "kuyou" find "__k_u_y_o_u__"
      const s = artistStats.get(it._al);
      if (!s) {
        const fo = followers[it._al] ?? null;
        artistStats.set(it._al, { key: it._al, handle: it.a, count: 1, latest: it.d, cover: it, followers: fo });
      }
      else {
        s.count++;
        if (it.d > s.latest) s.latest = it.d;
        if (it.k === "art" && s.cover.k !== "art") s.cover = it;
      }
    }

    renderHero(data);

    const frag = document.createDocumentFragment();
    for (const s of [...artistStats.values()].sort((a, b) => b.count - a.count)) {
      const o = document.createElement("option");
      o.value = "@" + s.handle;
      o.label = plural(s.count, "work", "works");
      frag.append(o);
    }
    el.artistList.append(frag);

    applyFilters();
    renderPickShelf();
    buildShelves();
    buildTimeline();
    buildTimelineView();
    showView();

    if (workParam) {
      const list = state.view === "timeline" ? tlView.list : filtered;
      const idx = list.findIndex((it) => it.f === workParam);
      if (idx >= 0) {
        if (state.view === "gallery") grid.scrollToIndex(idx);
        if (state.view === "timeline") tlGrid.scrollToIndex(idx);
        openViewer(idx, list);
      }
    }
  }

  boot();
})();
