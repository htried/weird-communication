# Project website

A static, single-page site: `index.html`, `assets/` (CSS and JS), `data/site.json`, `img/`, and
`videos/` (captioned room clips with poster frames). Charts use Observable Plot and d3 from
jsDelivr (pinned versions with integrity hashes); everything else is local.

## Edit and preview

```bash
python3 scripts/build_site_data.py              # rebuild data/site.json after changing inputs
python3 -m http.server 8090 --directory site    # then open http://localhost:8090
```

`data/site.json` is built from `analysis/figures/solved_by_rung.csv`,
`harness/config/ladder.json`, the caption timelines in `site/edl/`, and the headline counts at
the top of `scripts/build_site_data.py`. Open the page through a web server, not `file://`.

The introduction is a placeholder (`#intro` in `index.html`), as is the env3 photo
(`img/env3-photo.jpg`).

## Videos

Clips are cut from the OBS room recording and captioned from the agents' own logs:

1. `scripts/auto_edl.py artifacts/trials/<trial>` drafts a caption timeline (EDL) from both
   agents' logs; review and trim it by hand.
2. On the machine with the recordings and ffmpeg (lab1): `scripts/cut_clips.py` cuts the raw
   clip, and `scripts/make_reel.py` renders the captioned clip from the EDL.
3. Copy the clip to `site/videos/`, the EDL to `site/edl/`, add a poster frame
   (`<trial>.jpg`), and list it in `VIDEOS` in `scripts/build_site_data.py`.

Clips from `e3p*` trials are from the pilot runs, before the sound-server fix; the page labels
them "pilot".

## Deploy

Any static host works (GitHub Pages, Netlify, Cloudflare Pages). The site is about 30 MB, mostly
video. GitHub Pages serves from a public repository, or from a private one on a paid plan.
