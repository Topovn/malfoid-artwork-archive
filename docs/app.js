(() => {
  "use strict";

  const CONFIG = Object.assign(
    { useReleases: true, preferLocalMedia: true, mediaBase: "media/", lostFolder: "_LostTweets/" },
    window.MALFOID_CONFIG || {}
  );
  const CELL_RATIO = 1.25; // height / width of a grid cell
  const OVERSCAN = 2; // rows rendered above and below the viewport
  const FAME_SIZE = 50; // Hall of Fame: 50 works
  // Like-range shelves, one work per artist: at least the first number of likes and fewer than the second.
  const SHELF_LIKE_RANGES = {
    famous: [20000, 50000],
    popular: [10000, 20000],
    big: [5000, 10000],
    gems: [2000, 5000],
    hidden: [200, 2000],
  };
  const BURIED_MAX = 200; // Buried Malfoid: every work under this many likes
  const SMALL_FOLLOWERS = 2000; // Small Artists: fewer followers than this (and not in Buried Malfoid)
  // Overlooked Malfoid: every work whose likes fall under this share of the artist's followers,
  // from artists with at least OVERLOOKED_MIN_FOLLOWERS (below that the ratio is just noise).
  const OVERLOOKED_RATIO = 0.01;
  const OVERLOOKED_MIN_FOLLOWERS = 1500;
  // Accounts that dub or translate Japanese comics: their followers come from the dubbing, so their reach isn't overlooked.
  const COMIC_DUBBERS = new Set(["player1_please", "raymnd_x"]);
  // A small link under the handle on every card by these artists (lowercase handle).
  const ARTIST_LINKS = {
    yunayuispink: { text: "Thank You 🩷", label: "Thank you, @yunayuispink's livestream on YouTube", url: "https://www.youtube.com/watch?v=uXFL084iKvk" },
  };
  const SHELF_SIZE = 30; // works shown at once in the shuffled shelves
  const RANDOM_SHELF_SIZE = 20; // works shown at once in the like-range shelves and Buried Malfoid
  const LATEST_SHELF_SIZE = 20; // works shown in Recently Added Works and Newest Posted Works
  const RANDOM_SIZE = 50; // works shown in the Random tab, one per artist
  const LONG_VIDEO = 30; // videos this many seconds or longer count as Videos (see isVideo)
  const VIDEO_ARTISTS = new Set(["alessandro49472"]); // every work by these artists counts as Videos
  const ANIMATION_ARTISTS = new Set(["asuzureincarnat"]); // every video by these artists counts as Animations, however long
  const VIDEO_WORKS = new Set([ // short works that still count as Videos
    "tenmeida-2108357386880606323-01.mp4",
    "Genshin_T1-2107730480061911364-01.mp4",
    "ThonasGareth-2106629595285831850-01.mp4",
    "skoomaaddict08-2115148786314195381-01.mp4",
    "yunayuispink-2113764723632332947-01.mp4",
  ]);
  const ANIMATION_WORKS = new Set([ // long works that still count as Animations
    "felutiahime-2117929539955560644-01.mp4",
    "NakanouKouheiii-2117761397958275391-01.mp4",
    "Coolboy1234510-2114767762493235555-01.mp4",
    "Allamas_Llamas-2109004627169644734-01.mp4",
    "_Togl_-2108905395859357979-01.mp4",
    "atomika39-2117064065730437601-01.mp4",
  ]);
  const TIMEZONE = "Australia/Sydney"; // days on the timeline, as in the original dashboard
  const TIMELINE_START = "2026-09-01";
  const TIMELINE_VIEW_END = "2026-10-01"; // the Timeline tab covers the early days, before the October wave
  const TIMELINE_GROUPED_YEARS = new Set(["2022", "2023"]); // years shown as one group on their year marker, without days
  const VIDEO_EXTS = new Set(["mp4", "mov", "webm"]);

  const $ = (sel) => document.querySelector(sel);
  const el = {
    heroMeta: $("#hero-meta"),
    lostNote: $("#lost-note"),
    q: $("#q"),
    likes: $("#likes"),
    followers: $("#followers"),
    kind: $("#kind"),
    sort: $("#sort"),
    perPage: $("#per-page"),
    randomGrid: $("#random-grid"),
    pager: $("#pager"),
    count: $("#count"),
    activeArtist: $("#active-artist"),
    grid: $("#grid"),
    empty: $("#empty"),
    artistQ: $("#artist-q"),
    artistSort: $("#artist-sort"),
    artistFollowers: $("#artist-followers"),
    artistCount: $("#artist-count"),
    artists: $("#artists"),
    viewer: $("#viewer"),
    viewerMedia: $("#viewer-media"),
    viewerPos: $("#viewer-pos"),
    viewerArtist: $("#viewer-artist"),
    viewerLink: $("#viewer-link"),
    viewerTags: $("#viewer-tags"),
    viewerLost: $("#viewer-lost"),
    viewerPost: $("#viewer-post"),
    viewerText: $("#viewer-text"),
    viewerTextMore: $("#viewer-text-more"),
    viewerMeta: $("#viewer-meta"),
    viewerTweet: $("#viewer-tweet"),
    viewerDanbooru: $("#viewer-danbooru"),
    viewerExtra: $("#viewer-extra"),
    viewerMore: $("#viewer-more"),
    viewerNote: $("#viewer-note"),
    prev: $("#prev"),
    next: $("#next"),
  };

  // The second site has only the Gallery and Artists tabs, opens on the Gallery,
  // and has no My Personal Favorite or Lost tweets filter.
  const ADULT_SITE = document.documentElement.dataset.site === "h";
  const VIEWS = ADULT_SITE ? ["gallery", "artists"] : ["home", "random", "gallery", "timeline", "artists"];
  const DEFAULT_VIEW = VIEWS[0]; // shown at the bare address; the second site then moves it to #gallery
  const MAIN_ONLY_KINDS = new Set(["pick"]);
  const state = { q: "", artist: "", likes: "", followers: "", kind: "", sort: "shuffle", seed: 0, page: 1, view: DEFAULT_VIEW };
  const PER_PAGE_OPTIONS = [50, 100, 150, 200, 250, 300];
  const DEFAULT_PER_PAGE = 100;
  // Gallery "Likes" filter: at least the first number and fewer than the second. Works without a count match none.
  const LIKE_RANGES = {
    "50k": [50000, Infinity],
    "20k": [20000, 50000],
    "10k": [10000, 20000],
    "5k": [5000, 10000],
    "2k": [2000, 5000],
    "200": [200, 2000],
    "0": [0, 200],
  };
  // "Followers" filter in Artists and Gallery, same rules: artists without a count match none.
  const FOLLOWER_RANGES = {
    "0": [0, 2000],
    "2k": [2000, 5000],
    "5k": [5000, 10000],
    "10k": [10000, 20000],
    "20k": [20000, 50000],
    "50k": [50000, 100000],
    "100k": [100000, Infinity],
  };
  let items = [];
  // The archive's totals. The published manifest leaves out hidden artists' works but carries the totals with them
  // ("totals"), so the site's numbers stay the whole archive's; without it they are counted from the works here.
  let manifestTotals = null;

  // ---------- Admin toggle ----------
  // tools/serve.py, the owner's local server, serves the full archive with the hidden artists' works and marks it with
  // an X-Malfoid-Admin header; that shows a toggle next to the icon. Off, the page asks for the published copy
  // (?view=visitor) and looks exactly as visitors see it. The choice is remembered for both sites.
  const VISITOR_VIEW_KEY = "malfoid-visitor-view";
  let visitorView = false;
  try {
    visitorView = localStorage.getItem(VISITOR_VIEW_KEY) === "1";
  } catch {
    // Storage blocked: always the admin view.
  }
  const dataUrl = (path) => (visitorView ? path + "?view=visitor" : path);

  function setupAdminToggle(res) {
    const btn = document.getElementById("admin-toggle");
    if (!btn || res.headers.get("X-Malfoid-Admin") !== "1") return;
    btn.setAttribute("aria-pressed", String(!visitorView));
    btn.setAttribute("aria-label", "Admin view");
    btn.title = visitorView ? "Visitor view" : "Admin view";
    btn.hidden = false;
    btn.addEventListener("click", () => {
      try {
        if (visitorView) localStorage.removeItem(VISITOR_VIEW_KEY);
        else localStorage.setItem(VISITOR_VIEW_KEY, "1");
      } catch {
        return;
      }
      location.reload();
    });
  }
  const totalWorks = () => manifestTotals?.works ?? items.length;
  const totalArtists = () => manifestTotals?.artists ?? artistStats.size;
  const itemByFile = new Map();
  let artistStats = new Map(); // lowercased handle -> {key, handle, count, latest, cover}
  const formerHandles = new Map(); // lowercased former handle -> lowercased current handle
  const resolveHandle = (h) => formerHandles.get(h) || h;
  let filtered = [];
  let randomList = [];
  let pageStart = 0; // index in `filtered` of the first work on the current gallery page
  let lastFilterKey = null;
  let perPage = readPerPage();
  let viewerList = [];
  let viewerIndex = -1;
  let viewerReturnFocus = null;
  let viewerPushed = false; // the open viewer owns a history entry that closing should leave
  let restoring = false; // history restoration must not write more history entries

  const numberFmt = new Intl.NumberFormat("en-US", { useGrouping: false }); // plain digits: 6165
  const compactFmt = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1, roundingMode: "trunc" }); // 1950 reads 1.9K, never "2K" for an under-2000 work
  // Artist like totals: three significant digits, so 1.06M and 1.03M don't both read "1M".
  const totalFmt = new Intl.NumberFormat("en-US", { notation: "compact", maximumSignificantDigits: 3, roundingMode: "trunc" });
  const dateFmt = new Intl.DateTimeFormat("en-US", { year: "numeric", month: "short", day: "numeric" });
  const fmtDate = (ms) => (ms ? dateFmt.format(new Date(ms)) : "Unknown");
  const fmtLikes = (n) => compactFmt.format(n);
  // When a work was posted, in ms. "y" is a posting year for works with no exact date (no tweet id); it counts as Jan 1.
  const postedMs = (it) => it.p || (it.y ? Date.UTC(it.y, 0, 1) : 0);
  // The gallery's Recently added and Newest posted orders, shared with their home shelves.
  const byFile = (a, b) => (a.f < b.f ? -1 : a.f > b.f ? 1 : 0);
  const byAdded = (a, b) => b.d - a.d || byFile(a, b);
  const byNewest = (a, b) => postedMs(b) - postedMs(a) || byFile(a, b);
  // Likes for sorting and filtering: the real count, else a lost tweet's hidden estimate (the artist's average).
  const rankLikes = (it) => it.v ?? it.e;
  const fmtPosted = (it) => (it.p ? fmtDate(it.p) : it.y ? String(it.y) : "Unknown");
  // File-name order as macOS Finder shows it: case-insensitive, numbers by value, "_" before digits and letters.
  const nameCollator = new Intl.Collator("en", { numeric: true, sensitivity: "base" });
  const plural = (n, one, many) => `${numberFmt.format(n)} ${n === 1 ? one : many}`;
  // Long videos, everything by VIDEO_ARTISTS (under a current or former handle) and the works listed in
  // VIDEO_WORKS form the Videos filter, except the works listed in ANIMATION_WORKS and those by ANIMATION_ARTISTS.
  const isVideo = (it) => VIDEO_WORKS.has(it.f) || (!ANIMATION_WORKS.has(it.f) &&
    !ANIMATION_ARTISTS.has(it._al) && !ANIMATION_ARTISTS.has(it._ao) &&
    (it.s >= LONG_VIDEO || VIDEO_ARTISTS.has(it._al) || VIDEO_ARTISTS.has(it._ao)));
  const kindLabel = (it) => (isVideo(it) ? "Video" : it.k === "anim" ? "Animation" : "Artwork");

  const thumbUrl = (it) => "thumbs/" + encodeURIComponent(it.f) + ".webp";
  const onLocalhost = /^(localhost|127\.0\.0\.1|\[::1\])$/.test(location.hostname);
  let releaseBase = "";
  const mediaUrl = (it) => {
    const local = CONFIG.preferLocalMedia && onLocalhost;
    if (CONFIG.useReleases && releaseBase && it.u && !local) {
      return releaseBase + it.u.split("/").map(encodeURIComponent).join("/");
    }
    return CONFIG.mediaBase + (it.l && !ADULT_SITE ? CONFIG.lostFolder : "") + encodeURIComponent(it.f); // the second site's lost tweets stay at the top level
  };
  const tweetUrl = (it) => (it.t ? `https://x.com/${encodeURIComponent(it.a)}/status/${it.t}` : "");
  const profileUrl = (it) => `https://x.com/${encodeURIComponent(it.a)}`;
  const isSafeUrl = (u) => /^https?:\/\//i.test(u);

  // ---------- Paging and seeded randomness ----------

  // Per page is a per-viewer preference; storage can be missing or blocked, so it falls back to the default.
  function readPerPage() {
    try {
      const n = Number(localStorage.getItem("perPage"));
      return PER_PAGE_OPTIONS.includes(n) ? n : DEFAULT_PER_PAGE;
    } catch {
      return DEFAULT_PER_PAGE;
    }
  }

  function savePerPage(n) {
    try {
      localStorage.setItem("perPage", String(n));
    } catch {}
  }

  const newSeed = () => 1 + Math.floor(Math.random() * (2 ** 32 - 1));
  // Random orders: fresh on each load and each time Random is picked, steady while paging or filtering.
  let gallerySeed = newSeed();
  let artistSeed = newSeed();

  // Stable pseudo-random ranks: one set survives reloads, Shuffle picks a new seed.
  function stringHash(s) {
    let h = 0x811c9dc5; // FNV-1a
    for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 0x01000193);
    return h >>> 0;
  }
  function seededRank(hash, seed) {
    let h = (hash ^ seed) >>> 0; // murmur3 finalizer
    h = Math.imul(h ^ (h >>> 16), 0x85ebca6b);
    h = Math.imul(h ^ (h >>> 13), 0xc2b2ae35);
    return (h ^ (h >>> 16)) >>> 0;
  }
  function fileHash(it) {
    if (it._h === undefined) it._h = stringHash(it.f);
    return it._h;
  }
  const randomRank = (it, seed) => seededRank(fileHash(it), seed);
  const artistRank = (key, seed) => seededRank(stringHash(key), seed);

  // ---------- URL state ----------

  let lastUrl = location.pathname + location.search + location.hash;

  function readUrl() {
    const p = new URLSearchParams(location.search);
    state.q = p.get("q") || "";
    state.artist = p.get("artist") || "";
    state.likes = p.get("likes") || "";
    state.followers = p.get("followers") || "";
    state.kind = p.get("type") || "";
    if (ADULT_SITE && MAIN_ONLY_KINDS.has(state.kind)) state.kind = "";
    const legacyRandom = !ADULT_SITE && p.get("sort") === "random";
    state.sort = p.get("sort") || "shuffle";
    // Old links may name an order or range that no longer exists.
    if (![...el.sort.options].some((o) => o.value === state.sort)) state.sort = "shuffle";
    if (state.likes === "30k") state.likes = "20k"; // 30K-50K folded into 20K-50K
    if (!Object.hasOwn(LIKE_RANGES, state.likes)) state.likes = "";
    if (!Object.hasOwn(FOLLOWER_RANGES, state.followers)) state.followers = "";
    const seed = Number(p.get("seed"));
    state.seed = Number.isInteger(seed) && seed > 0 && seed < 2 ** 32 ? seed : 0;
    const page = Number(p.get("page"));
    state.page = Number.isInteger(page) && page >= 1 ? page : 1; // clamped to the last page once results are known
    const hash = location.hash.slice(1);
    const hasFilters = ["q", "artist", "likes", "followers", "type", "sort", "page"].some((k) => p.has(k));
    state.view = legacyRandom ? "random" : VIEWS.includes(hash) ? hash : hasFilters ? "gallery" : DEFAULT_VIEW;
    if (state.view === "random" && !state.seed) state.seed = newSeed();
    return p.get("work");
  }

  // `push` adds a history entry for a user action; the default replaces the current one.
  function writeUrl(work, push = false) {
    const p = new URLSearchParams();
    if (state.view === "gallery") {
      if (state.q) p.set("q", state.q);
      if (state.artist) p.set("artist", state.artist);
      if (state.likes) p.set("likes", state.likes);
      if (state.followers) p.set("followers", state.followers);
      if (state.kind) p.set("type", state.kind);
      if (state.sort !== "shuffle") p.set("sort", state.sort);
      if (state.page > 1) p.set("page", state.page);
    }
    if (state.view === "random") p.set("seed", state.seed);
    if (work) p.set("work", work);
    const qs = p.toString();
    const hash = state.view === "home" ? "" : "#" + state.view;
    const url = location.pathname + (qs ? "?" + qs : "") + hash;
    const current = location.pathname + location.search + location.hash;
    if (url === current) {
      lastUrl = current;
      return;
    }
    lastUrl = url;
    if (push) history.pushState(null, "", url);
    else history.replaceState(null, "", url);
  }

  function syncControls() {
    el.q.value = state.q;
    el.likes.value = state.likes;
    el.followers.value = state.followers;
    el.sort.value = state.sort;
    el.perPage.value = String(perPage);
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
    const exactHandle = exact && (artistStats.has(q) || formerHandles.has(q));
    // Old links may name an artist by a handle they have since changed.
    const artist = resolveHandle(state.artist.toLowerCase());
    const [minLikes, maxLikes] = LIKE_RANGES[state.likes] || [];
    const [minFollowers, maxFollowers] = FOLLOWER_RANGES[state.followers] || [];

    const out = items.filter((it) => {
      if (artist && it._al !== artist) return false;
      const likes = rankLikes(it);
      if (state.likes && (likes == null || likes < minLikes || likes >= maxLikes)) return false;
      if (state.followers) {
        const f = artistStats.get(it._al)?.followers;
        if (f == null || f < minFollowers || f >= maxFollowers) return false;
      }
      const kindOk =
        state.kind === "lost" ? !!it.l
        : state.kind === "pick" ? pickSet.has(it.f)
        : state.kind === "comic" ? !!it.c
        : state.kind === "video" ? isVideo(it)
        : state.kind === "anim" ? it.k === "anim" && !isVideo(it)
        : !state.kind || it.k === state.kind;
      if (!kindOk) return false;
      if (q) {
        // An exact former handle finds the whole artist, including works saved under the new handle.
        if (exact) return exactHandle ? it._al === resolveHandle(q) : it._al.startsWith(q) || it._ao.startsWith(q);
        return (
          it._al.includes(q) ||
          it._ao.includes(q) ||
          (qLoose && (it._an.includes(qLoose) || it._aon.includes(qLoose))) ||
          (it.t && it.t.includes(q))
        );
      }
      return true;
    });

    const sorters = {
      added: byAdded,
      newest: byNewest,
      // Images from one tweet share its count, so they stay together.
      liked: (a, b) => (rankLikes(b) ?? -1) - (rankLikes(a) ?? -1) || (a.t < b.t ? -1 : a.t > b.t ? 1 : 0) || a.i - b.i || byFile(a, b),
      oldest: (a, b) => (postedMs(a) || Infinity) - (postedMs(b) || Infinity) || byFile(a, b),
      artist: (a, b) => (a._al < b._al ? -1 : a._al > b._al ? 1 : postedMs(b) - postedMs(a) || byFile(a, b)),
      name: (a, b) => nameCollator.compare(a._fn, b._fn) || byFile(a, b),
      shuffle: (a, b) => randomRank(a, gallerySeed) - randomRank(b, gallerySeed) || byFile(a, b),
    };
    out.sort(sorters[state.sort] || sorters.added);
    filtered = out;

    // Any change to what is shown, or its order, starts again from page 1.
    const key = [state.q, state.artist, state.likes, state.followers, state.kind, state.sort, state.sort === "shuffle" ? gallerySeed : 0].join("\u0000");
    if (lastFilterKey !== null && key !== lastFilterKey) state.page = 1;
    lastFilterKey = key;

    const total = numberFmt.format(totalWorks());
    const artistsShown = new Set(out.map((it) => it._al)).size;
    el.count.textContent =
      out.length === items.length
        ? `${total} works by ${numberFmt.format(totalArtists())} artists`
        : state.artist
        ? `${numberFmt.format(out.length)} of ${total} works` // the artist chip already names the one artist
        : `${numberFmt.format(out.length)} of ${total} works · ${plural(artistsShown, "artist", "artists")}`;
    el.empty.hidden = out.length > 0;

    renderChips();
    renderPage();
  }

  // Shows the current page of `filtered` in the grid, after clamping the page number to what exists.
  function renderPage() {
    const pages = Math.max(1, Math.ceil(filtered.length / perPage));
    state.page = Math.min(Math.max(1, state.page), pages);
    pageStart = (state.page - 1) * perPage;
    grid.setData(filtered.slice(pageStart, pageStart + perPage));
    renderPager(pages);
  }

  // Page numbers to show: the first, the last, and a window around the current one, with gaps as null.
  function pageNumbers(current, pages) {
    const keep = new Set([1, pages, current - 1, current, current + 1]);
    if (current <= 3) [2, 3, 4].forEach((n) => keep.add(n));
    if (current >= pages - 2) [pages - 1, pages - 2, pages - 3].forEach((n) => keep.add(n));
    const nums = [...keep].filter((n) => n >= 1 && n <= pages).sort((a, b) => a - b);
    const out = [];
    nums.forEach((n, i) => {
      if (i && n - nums[i - 1] > 1) out.push(n - nums[i - 1] === 2 ? n - 1 : null);
      out.push(n);
    });
    return out;
  }

  function renderPager(pages) {
    const hadFocus = el.pager.contains(document.activeElement);
    el.pager.replaceChildren();
    el.pager.hidden = pages <= 1;
    if (pages <= 1) return;
    const button = (label, page, aria) => {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = label;
      b.dataset.page = page;
      if (aria) b.setAttribute("aria-label", aria);
      return b;
    };
    const prev = button("‹ Previous", state.page - 1, "Previous page");
    prev.className = "pager-step";
    prev.disabled = state.page === 1;
    const list = document.createElement("span");
    list.className = "pager-pages";
    for (const n of pageNumbers(state.page, pages)) {
      if (n === null) {
        const gap = document.createElement("span");
        gap.className = "pager-gap";
        gap.textContent = "…";
        list.append(gap);
        continue;
      }
      const b = button(String(n), n, `Page ${n}`);
      if (n === state.page) b.setAttribute("aria-current", "page");
      list.append(b);
    }
    const next = button("Next ›", state.page + 1, "Next page");
    next.className = "pager-step";
    next.disabled = state.page === pages;
    el.pager.append(prev, list, next);
    if (hadFocus) el.pager.querySelector('[aria-current="page"]').focus({ preventScroll: true });
  }

  function goToPage(page) {
    state.page = page;
    renderPage();
    writeUrl(undefined, true);
    // Back to the top of the results, just below the sticky top bar.
    const bar = document.querySelector(".topbar").offsetHeight;
    window.scrollTo({ top: el.count.getBoundingClientRect().top + window.scrollY - bar - 16 });
  }

  // The follower range shows as a plain label (changed from the Followers select); the artist chip can be cleared.
  function renderChips() {
    el.activeArtist.replaceChildren();
    if (state.followers) {
      const chip = document.createElement("span");
      chip.className = "chip chip-label";
      chip.textContent = `${el.followers.selectedOptions[0].textContent} followers`;
      el.activeArtist.append(chip);
    }
    if (state.artist) {
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
    }
    el.activeArtist.hidden = !el.activeArtist.children.length;
  }

  function setArtist(handle) {
    state.artist = handle;
    if (handle) state.q = "";
    syncControls();
    applyFilters();
    writeUrl(undefined, true);
  }

  function showGallery(changes) {
    Object.assign(state, changes);
    state.view = "gallery";
    syncControls();
    showView();
    applyFilters();
    writeUrl(undefined, true);
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

  // Puts the artist's link (ARTIST_LINKS) after the card's open button, or takes it off a recycled card.
  function setArtistLink(card, it) {
    const link = ARTIST_LINKS[it._al];
    let a = card.querySelector(".card-link");
    card.classList.toggle("has-link", !!link);
    if (!link) return a?.remove();
    if (!a) {
      a = document.createElement("a");
      a.className = "card-link";
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      card.querySelector(".card-hit").after(a);
    }
    a.href = link.url;
    a.textContent = link.text;
    a.setAttribute("aria-label", link.label);
  }

  function cardLabel(it) {
    return (
      `Work by @${it.a}` +
      (it.v != null ? `, about ${fmtLikes(it.v)} likes` : "") +
      (it.k === "anim" ? ", " + kindLabel(it).toLowerCase() : "") +
      (it.l ? ", lost tweet" : "") +
      (pickSet.has(it.f) ? ", in My Personal Favorite" : "")
    );
  }

  // ---------- My Personal Favorite ----------
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
      pickEl.setAttribute("aria-label", on ? "Remove from My Personal Favorite" : "Add to My Personal Favorite");
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
        console.error("Couldn't save My Personal Favorite:", err);
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

  // ---------- Comic tag ----------
  // The build marks comics ("c") from tools/detect_comics.py. On the owner's local server (tools/serve.py)
  // the viewer's Comic tag is a toggle; corrections go to data/comics.json through PUT /api/comics and
  // reach the published manifests at the next build. Everywhere else the tag is read-only.

  const COMICS_API = ADULT_SITE ? "../api/comics" : "api/comics"; // the server's root, also from the second site's subfolder
  const comicFix = { comic: new Set(), art: new Set() };
  let comicsEditable = false;
  let comicSave = Promise.resolve();

  async function loadComicFixes() {
    if (!["127.0.0.1", "localhost"].includes(location.hostname)) return; // the API only exists locally
    try {
      const res = await fetch(COMICS_API, { cache: "no-store" });
      if (!res.ok || res.headers.get("X-Malfoid-Edit") !== "1") return;
      setComicFixes(await res.json());
      comicsEditable = true;
    } catch {
      // A plain static server: no corrections to apply.
    }
  }

  function setComicFixes(data) {
    for (const k of ["comic", "art"]) comicFix[k] = new Set(Array.isArray(data?.[k]) ? data[k].filter((f) => typeof f === "string") : []);
  }

  // Corrections not yet built into the manifest win over it.
  function applyComicFixes() {
    for (const it of items) {
      if (comicFix.comic.has(it.f)) it.c = 1;
      else if (comicFix.art.has(it.f)) delete it.c;
    }
  }

  function comicToggle(it) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "badge badge-toggle";
    b.textContent = "Comic";
    b.setAttribute("aria-pressed", String(!!it.c));
    b.addEventListener("click", () => toggleComic(it));
    return b;
  }

  function refreshComic(it) {
    if (state.kind === "comic") applyFilters();
    if (el.viewer.open && viewerList[viewerIndex] === it) {
      const b = el.viewerTags.querySelector(".badge-toggle");
      if (b) b.setAttribute("aria-pressed", String(!!it.c));
    }
  }

  function toggleComic(it) {
    const before = { comic: new Set(comicFix.comic), art: new Set(comicFix.art), c: it.c };
    const on = !it.c;
    if (on) it.c = 1;
    else delete it.c;
    comicFix[on ? "comic" : "art"].add(it.f);
    comicFix[on ? "art" : "comic"].delete(it.f);
    refreshComic(it);
    const body = JSON.stringify({ comic: [...comicFix.comic], art: [...comicFix.art] });
    comicSave = comicSave
      .then(async () => {
        const res = await fetch(COMICS_API, { method: "PUT", headers: { "Content-Type": "application/json" }, body });
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      })
      .catch((err) => {
        console.error("Couldn't save the Comic tag:", err);
        comicFix.comic = before.comic;
        comicFix.art = before.art;
        if (before.c) it.c = before.c;
        else delete it.c;
        refreshComic(it);
      });
  }

  // ---------- Virtualized grid ----------
  // Renders only the cards near the viewport, so thousands of works stay fast.

  function makeVirtualGrid({ container, isActive, onOpen }) {
    const cards = new Map(); // item index -> element
    const cardPool = [];
    let list = [];
    let rows = []; // [{y, h, start, end}]
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
      for (let i = 0; i < list.length; i += cols) {
        rows.push({ y, h: cellH, start: i, end: Math.min(list.length, i + cols) });
        y += cellH + gap;
      }
      container.style.height = Math.max(0, y - gap) + "px";
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
      setArtistLink(node, it);
      node.querySelector(".card-handle").textContent = "@" + it.a;
      node.querySelector(".card-likes").textContent = it.v != null ? "♥ " + fmtLikes(it.v) : "";
      const badges = node.querySelector(".badges");
      badges.replaceChildren();
      if (it.k === "anim") badges.append(badge(it.x === "gif" ? "GIF" : "Video", true));
      if (it.l) badges.append(badge("Lost tweet", false, "badge-lost"));
      if (it.z) badges.append(badge("Hidden", false, "badge-hidden")); // owner's local view only (tools/serve.py)
      node.style.width = cellW + "px";
      node.style.height = cellH + "px";
      node.style.transform = `translate(${x}px, ${y}px)`;
    }

    function recycleAll() {
      for (const [k, n] of cards) (n.remove(), cardPool.push(n), cards.delete(k));
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
      for (let r = firstRowAt(from); r < rows.length && rows[r].y <= to; r++) {
        const row = rows[r];
        for (let i = row.start; i < row.end; i++) wantCards.set(i, [(i - row.start) * (cellW + gap), row.y]);
      }
      for (const [k, n] of cards) if (!wantCards.has(k)) (n.remove(), cardPool.push(n), cards.delete(k));
      const frag = document.createDocumentFragment();
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

    function setData(newList) {
      list = newList;
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
      const row = rows.find((r) => index >= r.start && index < r.end);
      return row ? row.y : 0;
    }

    function scrollToIndex(index) {
      const y = top + rowYOfIndex(index);
      if (y < window.scrollY + 70 || y + cellH > window.scrollY + window.innerHeight) window.scrollTo({ top: y - 90 });
    }

    function focusIndex(index) {
      const n = cards.get(index);
      if (n) n.firstChild.focus({ preventScroll: true });
    }

    window.addEventListener("scroll", schedule, { passive: true });
    new ResizeObserver(relayout).observe(container);
    return { setData, schedule, relayout, scrollToIndex, focusIndex };
  }

  // The gallery grid holds one page; the viewer steps through every filtered work, across pages.
  const grid = makeVirtualGrid({
    container: el.grid,
    isActive: () => state.view === "gallery",
    onOpen: (i, list, btn) => openViewer(pageStart + i, filtered, btn),
  });
  const randomGrid = makeVirtualGrid({
    container: el.randomGrid,
    isActive: () => state.view === "random",
    onOpen: (i, list, btn) => openViewer(i, list, btn),
  });

  // Pick artists first so every work in the set has a different creator.
  function renderRandom() {
    const seed = state.seed;
    const byArtist = new Map();
    for (const it of items) {
      let group = byArtist.get(it._al);
      if (!group) byArtist.set(it._al, (group = []));
      group.push(it);
    }
    randomList = [...byArtist.entries()]
      .map(([key, group]) => ({ key, rank: artistRank(key, seed), group }))
      .sort((a, b) => a.rank - b.rank || (a.key < b.key ? -1 : a.key > b.key ? 1 : 0))
      .slice(0, RANDOM_SIZE)
      .map(({ group }) => group.reduce((best, it) => (!best || randomRank(it, seed) < randomRank(best, seed) ? it : best), null))
      .sort((a, b) => randomRank(a, seed) - randomRank(b, seed) || (a.f < b.f ? -1 : 1));
    randomGrid.setData(randomList);
  }

  function shuffleRandom() {
    state.seed = newSeed();
    renderRandom();
    writeUrl(undefined, true);
  }

  // ---------- Post text ----------
  // tools/fetch_texts.py caches each live tweet's text and the build writes data/texts.json, keyed by tweet ID.
  // It loads after the page is up so the archive index stays small; an open viewer fills in once it arrives.

  let postTexts = {};

  async function loadTexts() {
    try {
      const res = await fetch(dataUrl("data/texts.json"));
      if (!res.ok) return;
      postTexts = await res.json();
    } catch {
      return;
    }
    if (el.viewer.open && viewerList[viewerIndex]) renderPostText(viewerList[viewerIndex]);
  }

  // Links, @mentions and #hashtags, linked the way the tweet links them. A hashtag needs a letter, so "#1" stays text.
  const POST_TOKEN_RE = /(https?:\/\/\S+)|(?<![\w@])@(\w{1,15})|(?<![\w&#])#(?=[\p{N}_]*\p{L})([\p{L}\p{M}\p{N}_]+)/gu;

  function linkedText(text) {
    const frag = document.createDocumentFragment();
    let last = 0;
    for (const m of text.matchAll(POST_TOKEN_RE)) {
      let label = m[0];
      let href;
      if (m[1]) href = label = label.replace(/[.,;:!?'")\]}>]+$/, ""); // punctuation after a link is prose
      else if (m[2]) href = `https://x.com/${m[2]}`;
      else href = `https://x.com/hashtag/${encodeURIComponent(m[3])}`;
      frag.append(text.slice(last, m.index));
      const a = document.createElement("a");
      a.href = href;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      a.textContent = label;
      frag.append(a);
      last = m.index + label.length;
    }
    frag.append(text.slice(last));
    return frag;
  }

  // Long posts start clamped to a few lines; "Show more" appears only when the text overflows.
  function renderPostText(it) {
    const text = !it._early && !it.l && it.t ? postTexts[it.t] : "";
    el.viewerPost.hidden = !text;
    el.viewerText.replaceChildren(text ? linkedText(text) : "");
    setPostExpanded(false);
  }

  function setPostExpanded(open) {
    el.viewerText.classList.toggle("clamped", !open);
    el.viewerTextMore.setAttribute("aria-expanded", String(open));
    el.viewerTextMore.textContent = open ? "Show less" : "Show more";
    fitPostText();
  }

  function fitPostText() {
    if (el.viewerPost.hidden || el.viewerTextMore.getAttribute("aria-expanded") === "true") return;
    el.viewerTextMore.hidden = el.viewerText.scrollHeight <= el.viewerText.clientHeight + 1;
  }

  // Also measures once the dialog opens, since a closed dialog has no layout.
  new ResizeObserver(fitPostText).observe(el.viewerText);
  el.viewerTextMore.addEventListener("click", () => setPostExpanded(el.viewerTextMore.getAttribute("aria-expanded") !== "true"));

  // ---------- Viewer ----------

  function openViewer(index, list = viewerList, from = null, push = true) {
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
    if (push) viewerPushed = true;
    writeUrl(it.f, push);
    preloadNeighbours(index);
  }

  function closeViewer(suppressHistory = false) {
    if (!el.viewer.open) return;
    if (suppressHistory) restoring = true;
    el.viewer.close();
    if (suppressHistory) restoring = false;
  }

  el.viewer.addEventListener("close", () => {
    el.viewerMedia.replaceChildren(); // stops any playing video
    document.documentElement.style.overflow = "";
    const pushClose = viewerPushed && !restoring;
    viewerPushed = false;
    if (state.view === "timeline" && viewerList === tlView.list) {
      if (!restoring) writeUrl(undefined, pushClose);
      requestAnimationFrame(() => focusTimelineCard(viewerIndex));
      viewerReturnFocus = null;
      return;
    }
    const g =
      state.view === "gallery" && viewerList === filtered ? grid
      : state.view === "random" && viewerList === randomList ? randomGrid
      : null;
    // The viewer may have stepped onto another page; show that page so the work is visible.
    if (g === grid && viewerIndex >= 0) {
      const page = Math.floor(viewerIndex / perPage) + 1;
      if (page !== state.page) {
        state.page = page;
        renderPage();
      }
    }
    if (!restoring) writeUrl(undefined, pushClose);
    if (g) {
      const local = g === grid ? viewerIndex - pageStart : viewerIndex;
      g.scrollToIndex(local);
      g.schedule();
      requestAnimationFrame(() => g.focusIndex(local));
    } else if (viewerReturnFocus && viewerReturnFocus.isConnected) {
      viewerReturnFocus.focus({ preventScroll: true });
    }
    viewerReturnFocus = null;
  });

  // Names the site a source link opens, from its host.
  const SOURCE_SITES = [
    [/(^|\.)pixiv\.net$/, "Pixiv"],
    [/(^|\.)tumblr\.com$/, "Tumblr"],
    [/(^|\.)novelpia\.com$/, "Novelpia"],
    [/(^|\.)(x|twitter)\.com$/, "Twitter"],
  ];
  function sourceLabel(url) {
    let host = "";
    try {
      host = new URL(url).hostname.toLowerCase();
    } catch {}
    const site = SOURCE_SITES.find(([re]) => re.test(host));
    return site ? `View on ${site[1]} ↗` : "View source ↗";
  }

  // Early art carries no tweet or file details: only the image, its year or posting date, and any credit or source.
  function renderEarlyViewer(it) {
    el.viewerNote.hidden = true;
    const media = document.createElement("img");
    media.alt = it.credit ? `Work by ${it.credit.name}, ${it.label}` : `Early Malfoid art, ${it.label}`;
    media.src = it.e.thumb;
    const full = new Image();
    full.decoding = "async";
    full.onload = () => {
      if (viewerList[viewerIndex] === it) media.src = full.src;
    };
    full.src = it.e.full;
    el.viewerMedia.replaceChildren(media);
    el.viewerPos.textContent = `${numberFmt.format(viewerIndex + 1)} of ${numberFmt.format(viewerList.length)}`;
    // The heading names the artist, or else the work's title (linked to its source).
    const heading = it.credit ? { text: it.credit.name, url: it.credit.url } : it.title && it.source ? { text: it.title, url: it.source } : null;
    el.viewerArtist.hidden = !heading;
    if (heading) {
      el.viewerArtist.textContent = heading.text;
      el.viewerArtist.href = heading.url;
    }
    el.viewerLink.hidden = true;
    el.viewerTags.replaceChildren(badge("Early art", false));
    el.viewerLost.hidden = true;
    renderPostText(it);
    el.viewerMeta.replaceChildren();
    const dt = document.createElement("dt");
    dt.textContent = it.p ? "Posted" : "Year";
    const dd = document.createElement("dd");
    dd.textContent = it.p ? fmtDate(it.p) : it.label;
    el.viewerMeta.append(dt, dd);
    el.viewerTweet.hidden = !it.source;
    if (it.source) {
      el.viewerTweet.href = it.source;
      el.viewerTweet.textContent = sourceLabel(it.source);
    }
    el.viewerDanbooru.hidden = true;
    const extra = it.links[0];
    el.viewerExtra.hidden = !extra;
    if (extra) {
      el.viewerExtra.href = extra.url;
      el.viewerExtra.textContent = extra.label + " ↗";
    }
    el.viewerMore.hidden = true;
    el.prev.disabled = viewerIndex <= 0;
    el.next.disabled = viewerIndex >= viewerList.length - 1;
    renderViewerPick(null);
  }

  function renderViewer(it) {
    if (it._early) return renderEarlyViewer(it);
    el.viewerArtist.hidden = false;
    el.viewerExtra.hidden = true;
    const isClip = VIDEO_EXTS.has(it.x);
    el.viewerNote.hidden = true;
    const current = () => viewerList[viewerIndex] === it;
    const showMissing = () => {
      el.viewerNote.textContent = "The full-size file isn't uploaded yet, so this is a preview.";
      el.viewerNote.hidden = false;
    };

    let media;
    if (isClip) {
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
    const link = ARTIST_LINKS[it._al];
    el.viewerLink.hidden = !link;
    if (link) {
      el.viewerLink.href = link.url;
      el.viewerLink.textContent = link.text;
      el.viewerLink.setAttribute("aria-label", link.label);
    }

    el.viewerTags.replaceChildren();
    el.viewerTags.append(badge(kindLabel(it), false));
    if (comicsEditable && it.k === "art") el.viewerTags.append(comicToggle(it));
    else if (it.c) el.viewerTags.append(badge("Comic", false));
    el.viewerTags.append(badge(it.x.toUpperCase(), false));
    if (it.l) el.viewerTags.append(badge("Lost tweet", false, "badge-lost"));
    if (it.z) el.viewerTags.append(badge("Hidden", false, "badge-hidden"));

    el.viewerLost.hidden = !it.l;
    if (it.l) el.viewerLost.textContent = it.l;
    renderPostText(it);

    el.viewerMeta.replaceChildren();
    const followers = artistStats.get(it._al)?.followers;
    const rows = [
      ["Likes", it.v != null ? `About ${fmtLikes(it.v)}` : "Unknown"],
      ["Artist followers", followers != null ? `About ${fmtLikes(followers)}` : "Unknown"],
      ["Posted", fmtPosted(it)],
      ["Added", fmtDate(it.d)],
      ["Tweet ID", it.t || "Unknown"],
      ["Image", it.t ? `#${it.i} in tweet` : "Single file"],
      ["File", it._fn],
    ];
    for (const [k, v] of rows) {
      const dt = document.createElement("dt");
      dt.textContent = k;
      const dd = document.createElement("dd");
      dd.textContent = v;
      el.viewerMeta.append(dt, dd);
    }

    // A lost tweet whose artist still has an account (a follower count was found) links to that profile instead.
    const tUrl = tweetUrl(it);
    const artistAlive = artistStats.get(it._al)?.followers != null;
    el.viewerTweet.hidden = !tUrl;
    if (tUrl) {
      el.viewerTweet.href = it.l && artistAlive ? profileUrl(it) : tUrl;
      el.viewerTweet.textContent =
        !it.l ? "View tweet on Twitter ↗"
        : artistAlive ? "View Artist Profile on Twitter ↗"
        : "Try the original tweet ↗";
    }
    el.viewerDanbooru.hidden = !(it.b && isSafeUrl(it.b));
    if (!el.viewerDanbooru.hidden) el.viewerDanbooru.href = it.b;

    const stats = artistStats.get(it._al);
    const n = stats ? stats.count : 1;
    // Inside this artist's own view the shortcut would lead nowhere new, so it hides.
    el.viewerMore.hidden = state.view === "gallery" && state.artist.toLowerCase() === it._al;
    el.viewerMore.textContent = `All ${plural(n, "work", "works")} by @${it.a}`;

    el.prev.disabled = viewerIndex <= 0;
    el.next.disabled = viewerIndex >= viewerList.length - 1;
    renderViewerPick(it);
  }

  // The first idea's artwork opens in the viewer like any card; that pair hides if the work leaves the archive.
  function setupMyIdeas() {
    const btn = $("#ideas-art-open");
    const it = itemByFile.get(btn.dataset.file);
    $("#idea-request-pair").hidden = !it;
    if (it) btn.addEventListener("click", () => openViewer(0, [it], btn));
  }

  function renderViewerPick(it) {
    const b = $("#viewer-pick");
    b.hidden = !picksEditable || !it;
    if (b.hidden) return;
    const on = pickSet.has(it.f);
    b.setAttribute("aria-pressed", String(on));
    b.querySelector("span").textContent = on ? "Remove from My Personal Favorite" : "Add to My Personal Favorite";
  }

  function preloadNeighbours(index) {
    for (const j of [index + 1, index - 1]) {
      const it = viewerList[j];
      if (it && it._early) new Image().src = it.e.thumb;
      else if (it && it.k !== "anim") new Image().src = thumbUrl(it);
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
    viewerReturnFocus = null;
    if (state.view === "gallery") {
      setArtist(it.a);
    } else {
      showGallery({ artist: it.a, q: "", likes: "", followers: "", kind: "" });
    }
    // The close handler scrolls back to this work in the new list.
    viewerList = filtered;
    viewerIndex = filtered.indexOf(it);
    closeViewer(true); // setArtist/showGallery already recorded the new view
  });
  el.viewer.addEventListener("keydown", (e) => {
    if (e.target instanceof HTMLVideoElement && (e.key === "ArrowLeft" || e.key === "ArrowRight")) return;
    if (e.key === "ArrowLeft") openViewer(viewerIndex - 1);
    else if (e.key === "ArrowRight") openViewer(viewerIndex + 1);
  });
  // Whether a point falls on the picture itself. Images fill the stage with object-fit: contain,
  // so the bars beside a tall image (or above a wide one) belong to the <img> without being picture.
  function onPicture(img, x, y) {
    const nw = img.naturalWidth;
    const nh = img.naturalHeight;
    if (!nw || !nh) return true;
    const r = img.getBoundingClientRect();
    const scale = Math.min(r.width / nw, r.height / nh);
    const w = nw * scale;
    const h = nh * scale;
    const left = r.left + (r.width - w) / 2;
    const top = r.top + (r.height - h) / 2;
    return x >= left && x <= left + w && y >= top && y <= top + h;
  }
  // Clicking the dark area around the media closes the viewer.
  el.viewerMedia.addEventListener("click", (e) => {
    const t = e.target;
    if (t === el.viewerMedia || (t instanceof HTMLImageElement && !onPicture(t, e.clientX, e.clientY))) closeViewer();
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

  const shelves = { pick: [], fame: [], famous: [], popular: [], big: [], gems: [], hidden: [], small: [], buried: [], added: [], newest: [] };
  const shelfPools = { famous: [], popular: [], big: [], gems: [], hidden: [], small: [], buried: [] };
  // Shelves that draw a fresh set on every load (Shuffle picks another), one artist at a time, and how many they show.
  const RANDOM_SHELVES = Object.fromEntries(
    [...Object.keys(SHELF_LIKE_RANGES), "buried"].map((k) => [k, RANDOM_SHELF_SIZE])
  );
  const shelfSeeds = Object.fromEntries(Object.keys(RANDOM_SHELVES).map((k) => [k, newSeed()]));
  // My Personal Favorite shows all its works in a fresh order on every load, kept for the visit
  // so the pick shelf doesn't jump while picks are edited. (Overlooked did too; it is switched off for now.)
  // const overlookedSeed = newSeed();
  const pickSeed = newSeed();

  // A small seeded shuffle for Small Artists, which draws a fresh set on every load.
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
      if (it.v == null || !it.t || isVideo(it)) continue; // Videos stay off the home shelves (My Personal Favorite excepted)
      const cur = byTweet.get(it.t);
      if (!cur || it.i < cur.i) byTweet.set(it.t, it);
    }
    const tweets = [...byTweet.values()];

    shelves.fame = tweets
      .filter((it) => it.v != null)
      .sort((a, b) => b.v - a.v)
      .slice(0, FAME_SIZE);

    const bestByArtist = new Map(); // artist -> most-liked tweet
    for (const it of tweets) {
      const best = bestByArtist.get(it._al);
      if (!best || it.v > best.v) bestByArtist.set(it._al, it);
    }
    // Small Artists shows an artist's best work, so those artists stay off the like-range shelves.
    // The like-range shelves and Buried Malfoid draw from every work in their like range, one artist at a time.
    const bests = [...bestByArtist.values()];
    // Overlooked Malfoid artworks is switched off for now, with its section in index.html.
    // It showed every qualifying work, shuffled, and kept those works off Buried Malfoid.
    // shelves.overlooked = tweets
    //   .filter((it) => {
    //     const fo = artistStats.get(it._al).followers;
    //     return fo >= OVERLOOKED_MIN_FOLLOWERS && !COMIC_DUBBERS.has(it._al) && it.v < fo * OVERLOOKED_RATIO;
    //   })
    //   .sort((a, b) => randomRank(a, overlookedSeed) - randomRank(b, overlookedSeed) || (a.f < b.f ? -1 : 1));
    // const overlooked = new Set(shelves.overlooked);
    shelfPools.buried = tweets.filter((it) => it.v < BURIED_MAX);
    shelfPools.small = bests.filter(
      (it) => it.v >= BURIED_MAX && (artistStats.get(it._al).followers ?? Infinity) < SMALL_FOLLOWERS
    );
    const small = new Set(shelfPools.small.map((it) => it._al));
    for (const [k, [min, max]] of Object.entries(SHELF_LIKE_RANGES)) {
      shelfPools[k] = tweets.filter((it) => it.v >= min && it.v < max && !small.has(it._al));
    }

    for (const name of Object.keys(RANDOM_SHELVES)) shuffleShelf(name, shelfSeeds[name]);
    shuffleShelf("small");
    // The gallery's first works when ordered by Recently added and by Newest posted, videos included.
    shelves.added = items.slice().sort(byAdded).slice(0, LATEST_SHELF_SIZE);
    shelves.newest = items.slice().sort(byNewest).slice(0, LATEST_SHELF_SIZE);

    renderShelf("fame");
    // renderShelf("overlooked");
    renderShelf("added");
    renderShelf("newest");
  }

  // One artist max: pick artists with the seed, then one of that artist's works in the pool, in shuffled order.
  function shuffleOnePerArtist(name, seed) {
    shelfSeeds[name] = seed;
    const byArtist = new Map();
    for (const it of shelfPools[name]) {
      let group = byArtist.get(it._al);
      if (!group) byArtist.set(it._al, (group = []));
      group.push(it);
    }
    shelves[name] = [...byArtist.entries()]
      .map(([key, group]) => ({ key, rank: artistRank(key, seed), group }))
      .sort((a, b) => a.rank - b.rank || (a.key < b.key ? -1 : a.key > b.key ? 1 : 0))
      .slice(0, RANDOM_SHELVES[name])
      .map(({ group }) => group.reduce((best, it) => (!best || randomRank(it, seed) < randomRank(best, seed) ? it : best), null))
      .sort((a, b) => randomRank(a, seed) - randomRank(b, seed) || (a.f < b.f ? -1 : 1));
    renderShelf(name);
  }

  function shuffleShelf(name, seed) {
    if (Object.hasOwn(RANDOM_SHELVES, name)) {
      shuffleOnePerArtist(name, seed ?? newSeed());
      return;
    }
    shelves[name] = seededShuffle(shelfPools[name], seed ?? newSeed()).slice(0, SHELF_SIZE);
    renderShelf(name);
  }

  function renderPickShelf(keepScroll) {
    shelves.pick = picks
      .map((f) => itemByFile.get(f))
      .filter(Boolean)
      .sort((a, b) => randomRank(a, pickSeed) - randomRank(b, pickSeed) || (a.f < b.f ? -1 : 1));
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
      setArtistLink(card, it);
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
    showGallery({ sort: "liked", q: "", artist: "", likes: "", followers: "", kind: "" });
  });
  for (const [id, sort] of [
    ["#added-link", "added"],
    ["#newest-link", "newest"],
  ]) {
    $(id).addEventListener("click", (e) => {
      e.preventDefault();
      showGallery({ sort, q: "", artist: "", likes: "", followers: "", kind: "", page: 1 });
    });
  }
  $("#pick-link").addEventListener("click", (e) => {
    e.preventDefault();
    showGallery({ kind: "pick", q: "", artist: "", likes: "", followers: "" });
  });
  for (const [id, likes] of [
    ["#famous-link", "20k"],
    ["#popular-link", "10k"],
    ["#big-link", "5k"],
    ["#gems-link", "2k"],
    ["#hidden-link", "200"],
    ["#buried-link", "0"],
  ]) {
    $(id).addEventListener("click", (e) => {
      e.preventDefault();
      gallerySeed = newSeed();
      showGallery({ likes, q: "", artist: "", followers: "", kind: "", sort: "shuffle", page: 1 });
    });
  }
  // Every work by the Small Artists shelf's artists (under 2K followers), strongest first.
  $("#small-link").addEventListener("click", (e) => {
    e.preventDefault();
    showGallery({ followers: "0", likes: "", sort: "liked", q: "", artist: "", kind: "", page: 1 });
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

  // ---------- Home: Archive Activity ----------

  const timeline = { daily: [], weekly: [], mode: "posted", summary: null };
  const dayKeyFmt = new Intl.DateTimeFormat("en-CA", { timeZone: TIMEZONE, year: "numeric", month: "2-digit", day: "2-digit" });
  const dayKey = (ms) => dayKeyFmt.format(new Date(ms));
  const DAY = 864e5;
  const keyMs = (k) => Date.parse(k + "T00:00:00Z");
  const keyIso = (ms) => new Date(ms).toISOString().slice(0, 10);
  const shortDay = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
  const longDay = new Intl.DateTimeFormat("en-US", { weekday: "short", month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
  const dateDay = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
  const fmtKey = (k) => shortDay.format(new Date(keyMs(k)));
  const fmtKeyLong = (k) => longDay.format(new Date(keyMs(k)));
  const round1 = (n) => Math.round(n * 10) / 10;

  function buildTimeline() {
    // Every work counts, videos included, so the totals match the site header.
    const works = items.map((it) => ({ artist: it._al, post: it.t || it.f, posted: dayKey(postedMs(it) || it.d), saved: dayKey(it.d) }));

    const firstSeen = new Map();
    for (const work of works) {
      const f = firstSeen.get(work.artist);
      if (!f || work.posted < f) firstSeen.set(work.artist, work.posted);
    }

    const days = new Map();
    const day = (k) => {
      if (!days.has(k)) days.set(k, { date: k, posted: 0, saved: 0, posts: new Set(), artists: new Set() });
      return days.get(k);
    };
    for (const work of works) {
      const d = day(work.posted);
      d.posted++;
      d.posts.add(work.post);
      d.artists.add(work.artist);
      day(work.saved).saved++;
    }
    const keys = [...days.keys()].sort();
    const last = keys[keys.length - 1];
    // The chart starts at the first day with art on or after the start date.
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
    for (const work of works) {
      if (work.posted >= TIMELINE_START) {
        const w = week(monday(work.posted));
        w.posted++;
        w.posts.add(work.post);
        w.artists.add(work.artist);
        if (firstSeen.get(work.artist) === work.posted) w.fresh.add(work.artist);
      }
      if (work.saved >= TIMELINE_START) week(monday(work.saved)).saved++;
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
    const lastPosted = [...timeline.daily].reverse().find((d) => d.posted > 0) || timeline.daily[timeline.daily.length - 1];
    const bestWeek = Math.max(...timeline.daily.map((d) => d.posted_avg7));
    const before = works.filter((work) => work.posted < TIMELINE_START).length;
    const weekFrom = keyIso(keyMs(lastPosted.date) - 6 * DAY);
    const newLast7 = timeline.daily.filter((d) => d.date >= weekFrom && d.date <= lastPosted.date).reduce((n, d) => n + d.new_artists, 0);
    timeline.summary = {
      // The archive's totals, the same as the site header's.
      works: totalWorks(),
      posts: manifestTotals?.posts ?? new Set(works.map((work) => work.post)).size,
      artists: totalArtists(),
      newLast7: manifestTotals?.newArtists7 ?? newLast7,
      lastDate: manifestTotals?.weekEnding ?? lastPosted.date,
      last7: manifestTotals?.perDay7 ?? lastPosted.posted_avg7,
      change: bestWeek ? lastPosted.posted_avg7 / bestWeek - 1 : 0,
      before,
    };
    renderTimelineText();
    renderWeekly();
    drawChart();
  }

  function renderTimelineText() {
    const s = timeline.summary;

    $("#t-works").textContent = numberFmt.format(s.works);
    $("#t-posts").textContent = numberFmt.format(s.posts);
    $("#t-artists").textContent = numberFmt.format(s.artists);
    $("#t-new").textContent = numberFmt.format(s.newLast7);
    $("#t-last7").textContent = String(s.last7);
    // A rolling window ending on the latest day with a posted work, so it can end on any weekday.
    $("#t-last7-range").textContent = `${shortDay.format(new Date(keyMs(s.lastDate) - 6 * DAY))} – ${dateDay.format(new Date(keyMs(s.lastDate)))}`;
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
      row("sw-bar", mode === "posted" ? "Works posted" : "Works saved", numberFmt.format(d[mode]));
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
        timeline.mode === "posted" ? "Works per day, by date posted" : "Works per day, by date saved to the folder";
      drawChart();
    });
  }
  new ResizeObserver(() => state.view === "home" && drawChart()).observe($("#chart"));

  // ---------- Timeline view: early art by year, then every work by date posted, oldest first ----------
  // One list down a rail; a red line follows the scroll and each date's dot turns red once the line reaches it.

  const tlView = { list: [], entries: [] };
  let earlyItems = []; // from data/early.json: anonymized, Timeline only, never in the gallery or shelves
  const tlDayFmt = new Intl.DateTimeFormat("en-US", { month: "long", day: "numeric", timeZone: "UTC" });

  async function loadEarly() {
    try {
      const res = await fetch("data/early.json");
      if (!res.ok) return;
      const data = await res.json();
      earlyItems = (Array.isArray(data.early) ? data.early : [])
        .filter((e) => e && typeof e.thumb === "string" && typeof e.full === "string")
        .map((e) => {
          // Credited works (e.g. from Pixiv) name the artist; the anonymous ones carry no credit or source.
          const c = e.credit;
          const credit = c && typeof c.name === "string" && c.name && isSafeUrl(String(c.url)) ? { name: c.name, url: c.url } : null;
          const source = typeof e.source === "string" && isSafeUrl(e.source) ? e.source : "";
          const title = typeof e.title === "string" ? e.title : "";
          const links = (Array.isArray(e.links) ? e.links : []).filter(
            (l) => l && typeof l.label === "string" && l.label && typeof l.url === "string" && isSafeUrl(l.url)
          );
          // "p" (posted, ms) puts the work on its day; without it the work sits under its year label.
          const p = Number.isFinite(e.p) && e.p > 0 ? e.p : 0;
          return { _early: true, e, p, label: String(e.label || e.year), credit, source, title, links };
        });
    } catch {
      // No early art: the Timeline simply starts in 2026.
    }
  }

  function tlCard(it, index) {
    const card = document.createElement("div");
    card.className = "card tl-card";
    card.dataset.index = index;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "card-hit";
    const img = document.createElement("img");
    img.alt = "";
    img.loading = "lazy";
    img.decoding = "async";
    img.addEventListener("load", () => img.classList.add("ready"));
    btn.append(img);
    btn.addEventListener("click", () => openViewer(index, tlView.list, btn));
    card.append(btn);
    if (it._early) {
      // Early screenshots keep their own shape, uncropped.
      card.classList.add("tl-card-early");
      if (it.e.w > 0 && it.e.h > 0) card.style.aspectRatio = `${it.e.w} / ${it.e.h}`;
      img.src = it.e.thumb;
      btn.setAttribute(
        "aria-label",
        it.credit ? `Work by ${it.credit.name}, ${it.label}` : it.title ? `${it.title}, ${it.label}` : `Early Malfoid art, ${it.label}`
      );
      const caption = it.credit?.name || it.title;
      if (caption) {
        const meta = document.createElement("span");
        meta.className = "card-meta";
        const name = document.createElement("span");
        name.className = "card-handle";
        name.textContent = caption;
        meta.append(name);
        btn.append(meta);
      }
      return card;
    }
    img.src = thumbUrl(it);
    btn._item = it;
    btn.setAttribute("aria-label", cardLabel(it));
    const badges = document.createElement("span");
    badges.className = "badges";
    if (it.k === "anim") badges.append(badge(it.x === "gif" ? "GIF" : "Video", true));
    if (it.l) badges.append(badge("Lost tweet", false, "badge-lost"));
    const meta = document.createElement("span");
    meta.className = "card-meta";
    const handle = document.createElement("span");
    handle.className = "card-handle";
    handle.textContent = "@" + it.a;
    const likes = document.createElement("span");
    likes.className = "card-likes";
    likes.textContent = it.v != null ? "♥ " + fmtLikes(it.v) : "";
    meta.append(handle, likes);
    btn.append(badges, meta);
    const pick = makePick();
    card.append(pick);
    setPick(pick, it);
    setArtistLink(card, it);
    return card;
  }

  function buildTimelineView() {
    const when = (it) => postedMs(it) || it.d;
    const name = (it) => (it._early ? it.e.id : it.f);
    // Early works with an exact date join the dated works on their day.
    const recent = [...items, ...earlyItems.filter((it) => it.p)]
      .filter((it) => dayKey(when(it)) < TIMELINE_VIEW_END)
      .sort((a, b) => when(a) - when(b) || (name(a) < name(b) ? -1 : 1));
    tlView.list = [...earlyItems.filter((it) => !it.p), ...recent];

    // Entries: one per early year label, a "2026"-style year marker, then one per day.
    // Works known only by year sit on that year's marker; there is no day to show.
    const entries = [];
    const marker = (year) => {
      let m = entries.find((e) => e.year === year);
      if (!m) entries.push((m = { key: "year:" + year, year, label: year, marker: true, works: [] }));
      return m;
    };
    tlView.list.forEach((it, i) => {
      if (!it._early && !it.p && it.y) {
        marker(String(it.y)).works.push(i);
        return;
      }
      const undated = it._early && !it.p;
      if (!undated && TIMELINE_GROUPED_YEARS.has(dayKey(when(it)).slice(0, 4))) {
        marker(dayKey(when(it)).slice(0, 4)).works.push(i);
        return;
      }
      const key = undated ? "early:" + it.label : dayKey(when(it));
      let cur = entries[entries.length - 1];
      if (!cur || cur.key !== key) {
        if (undated) {
          cur = { key, year: String(it.e.year), label: it.label, early: true, works: [] };
        } else {
          marker(key.slice(0, 4));
          cur = { key, label: tlDayFmt.format(new Date(keyMs(key))), works: [] };
        }
        entries.push(cur);
      }
      cur.works.push(i);
    });
    tlView.entries = entries;

    const list = $("#tl-list");
    list.replaceChildren();
    for (const entry of entries) {
      const li = document.createElement("li");
      li.className = "tl-entry" + (entry.marker ? " tl-marker" : "") + (entry.early ? " tl-year" : "");
      const dot = document.createElement("span");
      dot.className = "tl-dot";
      dot.setAttribute("aria-hidden", "true");
      const h = document.createElement("h3");
      h.className = "tl-date";
      h.textContent = entry.label;
      li.append(dot, h);
      if (entry.works.length) {
        const works = document.createElement("div");
        works.className = "tl-works";
        for (const i of entry.works) works.append(tlCard(tlView.list[i], i));
        li.append(works);
      }
      entry.el = li;
      list.append(li);
    }
    updateRail();
  }

  // The red line ends where the middle of the screen crosses the rail.
  function updateRail() {
    if (state.view !== "timeline" || !tlView.entries.length) return;
    const rail = $("#tl").getBoundingClientRect();
    const reach = Math.min(Math.max(window.innerHeight * 0.5 - rail.top, 0), rail.height);
    $("#tl-progress").style.height = reach + "px";
    for (const entry of tlView.entries) {
      const dot = entry.el.firstChild.getBoundingClientRect();
      entry.el.classList.toggle("reached", dot.top + dot.height / 2 - rail.top <= reach);
    }
  }
  let railFrame = 0;
  const scheduleRail = () => {
    if (!railFrame) railFrame = requestAnimationFrame(() => ((railFrame = 0), updateRail()));
  };
  window.addEventListener("scroll", scheduleRail, { passive: true });
  window.addEventListener("resize", scheduleRail);

  // After the viewer closes on a Timeline work, bring that card into view and focus it.
  function focusTimelineCard(index) {
    const card = $(`#tl-list .tl-card[data-index="${index}"]`);
    if (!card) return;
    const r = card.getBoundingClientRect();
    if (r.top < 70 || r.bottom > window.innerHeight) card.scrollIntoView({ block: "center" });
    card.firstChild.focus({ preventScroll: true });
  }

  // ---------- Artists view ----------

  let artistFrame = 0;
  function renderArtists() {
    const q = el.artistQ.value.trim().toLowerCase().replace(/^@/, "");
    let list = [...artistStats.values()];
    if (q) list = list.filter((a) => a.key.includes(q) || a.formers.some((old) => old.includes(q)));
    const range = FOLLOWER_RANGES[el.artistFollowers.value];
    if (range) list = list.filter((a) => a.followers != null && a.followers >= range[0] && a.followers < range[1]);
    const by = el.artistSort.value;
    list.sort((a, b) =>
      by === "name"
        ? a.key < b.key ? -1 : a.key > b.key ? 1 : 0
        : by === "recent"
        ? b.latest - a.latest
        : by === "followers"
        ? (b.followers ?? -1) - (a.followers ?? -1) || (a.key < b.key ? -1 : 1)
        : by === "likes"
        ? b.likes - a.likes || (a.key < b.key ? -1 : 1)
        : by === "random"
        ? artistRank(a.key, artistSeed) - artistRank(b.key, artistSeed) || (a.key < b.key ? -1 : 1)
        : b.count - a.count || (a.key < b.key ? -1 : 1)
    );
    el.artistCount.textContent = plural(list.length === artistStats.size ? totalArtists() : list.length, "artist", "artists");

    // Render in chunks so 2700+ rows never block the main thread.
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
    const stats = [];
    if (a.followers != null) stats.push(`${fmtLikes(a.followers)} ${a.followers === 1 ? "follower" : "followers"}`);
    if (a.likes > 0) stats.push(`♥ ${totalFmt.format(a.likes)}`);
    if (stats.length) {
      const fo = document.createElement("span");
      fo.className = "artist-followers";
      fo.textContent = stats.join(" · ");
      text.append(fo);
    }
    const count = document.createElement("span");
    count.className = "artist-count";
    count.textContent = plural(a.count, "work", "works");
    btn.append(img, text, count);
    // Keeps the Artists view's follower range, so its chip carries over; the likes range is cleared.
    btn.addEventListener("click", () =>
      showGallery({ artist: a.handle, q: "", likes: "", followers: el.artistFollowers.value })
    );
    li.append(btn);
    return li;
  }

  // ---------- Views ----------

  // On narrow screens the tabs scroll sideways; fade whichever edge has more tabs past it.
  function markNavEdges() {
    const nav = $(".nav");
    nav.classList.toggle("more-left", nav.scrollLeft > 1);
    nav.classList.toggle("more-right", nav.scrollLeft + nav.clientWidth < nav.scrollWidth - 1);
  }

  function showView() {
    for (const v of VIEWS) $(`#view-${v}`).hidden = state.view !== v;
    // The Timeline gathers early works from every platform, so it has its own coverage note.
    $("#tl-coverage").hidden = state.view !== "timeline";
    $("#x-coverage").hidden = state.view === "timeline";
    for (const a of document.querySelectorAll(".nav a[data-view]")) {
      if (a.dataset.view === state.view) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    }
    // On narrow screens the tabs scroll sideways: keep the current one fully in view.
    const nav = $(".nav");
    const cur = nav.querySelector('[aria-current="page"]');
    if (cur) {
      const n = nav.getBoundingClientRect();
      const c = cur.getBoundingClientRect();
      if (c.right > n.right) nav.scrollLeft += c.right - n.right;
      else if (c.left < n.left) nav.scrollLeft -= n.left - c.left;
    }
    markNavEdges();
    if (state.view === "gallery") grid.relayout();
    else grid.schedule(); // drops grid nodes while hidden
    if (state.view === "random") randomGrid.relayout();
    else randomGrid.schedule();
    if (state.view === "timeline") requestAnimationFrame(updateRail);
    if (state.view === "artists" && !el.artists.childElementCount && items.length) renderArtists();
    if (state.view === "home") requestAnimationFrame(drawChart);
  }

  // Entering Random, or Gallery and Artists while ordered randomly, deals a fresh shuffle.
  function enterView(view) {
    if (view === "random") state.seed = newSeed();
    const shuffleGallery = view === "gallery" && state.sort === "shuffle";
    const shuffleArtists = view === "artists" && el.artistSort.value === "random";
    const artistsShown = el.artists.childElementCount > 0; // otherwise showView renders them
    if (shuffleGallery) gallerySeed = newSeed();
    if (shuffleArtists) artistSeed = newSeed();
    state.view = view;
    showView(); // the grid must be visible before it measures itself
    if (view === "random") renderRandom();
    if (shuffleGallery) applyFilters();
    if (shuffleArtists && artistsShown) renderArtists();
  }

  // Back/forward and hand-edited links rebuild the whole state from the address bar.
  function restoreFromUrl() {
    const url = location.pathname + location.search + location.hash;
    if (url === lastUrl) return; // popstate and hashchange can both fire for one traversal
    lastUrl = url;

    const work = readUrl();
    const page = state.page;
    syncControls();
    applyFilters(); // may reset the page when the filter key changed
    state.page = page; // the URL's page wins on history navigation
    renderPage();
    if (state.view === "random") renderRandom();
    showView();

    const list =
      state.view === "random" ? randomList
      : state.view === "timeline" ? tlView.list
      : state.view === "gallery" ? filtered
      : null;
    const idx = work && list ? list.findIndex((it) => it.f === work) : -1;
    viewerPushed = false;
    restoring = true; // opening or closing the viewer here must not write history
    if (idx >= 0) {
      if (state.view === "gallery") {
        state.page = Math.floor(idx / perPage) + 1;
        renderPage();
        grid.scrollToIndex(idx - pageStart);
      } else if (state.view === "random") {
        randomGrid.scrollToIndex(idx);
      } else if (state.view === "timeline") {
        $(`#tl-list .tl-card[data-index="${idx}"]`)?.scrollIntoView({ block: "center" });
      }
      openViewer(idx, list, null, false);
    } else if (el.viewer.open) {
      el.viewer.close();
    }
    restoring = false;

    // A Random link without a seed gets one, recorded without adding another entry.
    if (state.view === "random" && !new URLSearchParams(location.search).has("seed")) writeUrl();
  }

  window.addEventListener("popstate", restoreFromUrl);
  window.addEventListener("hashchange", restoreFromUrl);

  // The second site greets each arrival with its speech bubble, as if the icon were hovered:
  // it grows out, holds, then lingers and fades on the bubble's own transition.
  if (ADULT_SITE) {
    const tip = $(".brand-tip");
    let greetTimer = 0;
    const greet = () => {
      clearTimeout(greetTimer);
      tip.classList.remove("is-greeting");
      // Two frames, so the hidden state is painted first and the bubble animates in.
      requestAnimationFrame(() => requestAnimationFrame(() => {
        tip.classList.add("is-greeting");
        greetTimer = setTimeout(() => tip.classList.remove("is-greeting"), 1500);
      }));
    };
    greet();
    // Coming back with the Back button restores the page from cache without rerunning this script.
    window.addEventListener("pageshow", (e) => e.persisted && greet());
  }

  for (const a of document.querySelectorAll(".nav a[data-view], .brand")) {
    a.addEventListener("click", (e) => {
      e.preventDefault();
      enterView(a.dataset.view || DEFAULT_VIEW);
      writeUrl(undefined, true);
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
    if (state.artist) {
      state.artist = ""; // typing a search replaces the artist filter, chip included
      applyFilters();
      writeUrl(undefined, true); // same history step as dismissing the chip with ✕
    }
    searchTimer = setTimeout(() => {
      state.q = el.q.value;
      applyFilters();
      writeUrl();
    }, 120);
  });
  el.likes.addEventListener("change", () => {
    state.likes = el.likes.value;
    applyFilters();
    writeUrl(undefined, true);
  });
  el.followers.addEventListener("change", () => {
    state.followers = el.followers.value;
    applyFilters();
    writeUrl(undefined, true);
  });
  el.sort.addEventListener("change", () => {
    state.sort = el.sort.value;
    if (state.sort === "shuffle") gallerySeed = newSeed();
    syncControls();
    applyFilters();
    writeUrl(undefined, true);
  });
  $("#random-shuffle").addEventListener("click", shuffleRandom);
  el.perPage.addEventListener("change", () => {
    // Keep the first work of the current page in view under the new page size.
    const first = pageStart;
    perPage = Number(el.perPage.value);
    savePerPage(perPage);
    state.page = Math.floor(first / perPage) + 1;
    renderPage();
    writeUrl(undefined, true);
  });
  el.pager.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-page]");
    if (b && !b.disabled) goToPage(Number(b.dataset.page));
  });
  // The search form never submits anywhere; Enter just keeps the live filter.
  $("#toolbar").addEventListener("submit", (e) => e.preventDefault());
  $(".nav").addEventListener("scroll", markNavEdges, { passive: true });
  window.addEventListener("resize", markNavEdges);
  $("#info-toggle").addEventListener("click", (e) => {
    const open = e.currentTarget.getAttribute("aria-expanded") !== "true";
    e.currentTarget.setAttribute("aria-expanded", String(open));
    $("#hero-notes").classList.toggle("open", open);
  });
  el.kind.addEventListener("click", (e) => {
    const b = e.target.closest("button[role=radio]");
    if (!b) return;
    state.kind = b.dataset.kind;
    syncControls();
    applyFilters();
    writeUrl(undefined, true);
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
  el.artistSort.addEventListener("change", () => {
    if (el.artistSort.value === "random") artistSeed = newSeed();
    renderArtists();
  });
  el.artistFollowers.addEventListener("change", renderArtists);

  window.addEventListener("keydown", (e) => {
    if (e.key === "/" && !el.viewer.open && document.activeElement.tagName !== "INPUT" && (state.view === "gallery" || state.view === "artists")) {
      e.preventDefault();
      (state.view === "artists" ? el.artistQ : el.q).focus();
    }
  });

  // ---------- Boot ----------

  function renderHero(data) {
    const lostItems = items.filter((it) => it.l);
    // The info button rides along with the date, so it never wraps onto a line by itself.
    const date = document.createElement("span");
    date.className = "nowrap";
    date.append(fmtDate(data.generated), $("#info-toggle"));
    el.heroMeta.replaceChildren(
      `${numberFmt.format(totalWorks())} works · ${plural(totalArtists(), "artist", "artists")} · Updated `,
      date,
    );

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
        ` are works whose original post on Twitter is gone: ${list}. ` +
          "The art was saved before it disappeared, so it stays here with an orange tag, but its link to Twitter no longer opens"
      );
    }
  }

  async function boot() {
    const workParam = readUrl();
    syncControls();
    showView();

    let data;
    try {
      const [res] = await Promise.all([fetch(dataUrl("data/manifest.json")), loadPicks(), loadEarly(), loadComicFixes()]);
      setupAdminToggle(res);
      if (!res.ok) throw new Error(res.status + " " + res.statusText);
      data = await res.json();
    } catch (err) {
      el.heroMeta.textContent = "Couldn't load the archive index. Try reloading the page.";
      console.error(err);
      return;
    }

    items = data.items;
    const t = data.totals;
    if (t && Number.isInteger(t.works) && Number.isInteger(t.artists)) manifestTotals = t;
    for (const it of items) itemByFile.set(it.f, it);
    applyComicFixes();
    if (data.releaseBase && isSafeUrl(data.releaseBase)) releaseBase = data.releaseBase;
    const followers = data.followers || {};
    for (const it of items) {
      it._al = it.a.toLowerCase();
      it._an = it._al.replace(/[_.]/g, ""); // lets "kuyou" find "__k_u_y_o_u__"
      it._ao = it.o ? it.o.toLowerCase() : ""; // the handle the file was saved under, if the artist renamed
      it._aon = it._ao.replace(/[_.]/g, "");
      // The file keeps the handle it was saved under; the site names it after the artist's current handle.
      it._fn = it.o && it.f.startsWith(it.o + "-") ? it.a + it.f.slice(it.o.length) : it.f;
      if (it._ao) formerHandles.set(it._ao, it._al);
      const s = artistStats.get(it._al);
      if (!s) {
        const fo = followers[it._al] ?? null;
        artistStats.set(it._al, { key: it._al, handle: it.a, count: 1, latest: it.d, cover: it, followers: fo, formers: [], likes: 0, likedTweets: new Set() });
      }
      else {
        s.count++;
        if (it.d > s.latest) s.latest = it.d;
        if (it.k === "art" && s.cover.k !== "art") s.cover = it;
      }
      const stats = artistStats.get(it._al);
      if (it._ao && !stats.formers.includes(it._ao)) stats.formers.push(it._ao);
      // Total likes: images from one tweet share its count, so each tweet is counted once.
      if (it.v != null && it.t && !stats.likedTweets.has(it.t)) {
        stats.likedTweets.add(it.t);
        stats.likes += it.v;
      }
    }

    renderHero(data);
    loadTexts();


    applyFilters();
    renderRandom();
    renderPickShelf();
    setupMyIdeas();
    buildShelves();
    buildTimeline();
    buildTimelineView();
    showView();
    // Rewrite the address with the settled state: a clamped page, a generated Random seed, dropped junk params.
    writeUrl();

    if (workParam) {
      const list = state.view === "random" ? randomList : state.view === "timeline" ? tlView.list : filtered;
      const idx = list.findIndex((it) => it.f === workParam);
      if (idx >= 0) {
        if (state.view === "gallery") {
          // Open on the page that holds the work.
          state.page = Math.floor(idx / perPage) + 1;
          renderPage();
          grid.scrollToIndex(idx - pageStart);
        }
        if (state.view === "random") randomGrid.scrollToIndex(idx);
        if (state.view === "timeline") $(`#tl-list .tl-card[data-index="${idx}"]`)?.scrollIntoView({ block: "center" });
        openViewer(idx, list, null, false); // the work URL is already the current entry
      }
    }
  }

  boot();
})();
