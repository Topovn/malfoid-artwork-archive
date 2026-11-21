// Site settings for the Malfoid Artwork Archive.
// Full-size files come from the GitHub Releases recorded in the manifest (see tools/upload_originals.py).
// mediaBase is the fallback for works not uploaded yet, and is always used on localhost when preferLocalMedia is on,
// where ./media links to the originals on disk. To move to another host later, set mediaBase and turn off useReleases.
// Thumbnails are always served from ./thumbs/, so the grid works even before full-size files are uploaded.
window.MALFOID_CONFIG = {
  useReleases: true,
  preferLocalMedia: true,
  mediaBase: "media/",
  lostFolder: "_LostTweets/",
};
