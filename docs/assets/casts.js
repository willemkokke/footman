/* Playable casts.
 *
 * The recordings are self-contained animated SVGs: every frame of the
 * session stacked in one file, cycled by CSS keyframes that share a single
 * duration. That plays fine on its own — but an <img> is a sealed document,
 * so nothing outside it can pause or seek, and a viewer who blinks has to
 * reload the page to see the moment again.
 *
 * So each cast is fetched and inlined, which puts its animations in this
 * document's timeline, where the Web Animations API can drive them:
 * pause(), play(), and a settable currentTime that is all a scrubber needs.
 * No player library, and the SVG on disk is unchanged — it still animates
 * by itself anywhere else it is opened.
 *
 * Progressive enhancement throughout: if the fetch fails, if the browser
 * has no getAnimations(), or if anything below throws, the original <img>
 * is left exactly where it was and still plays.
 */

function onEachPage(fn) {
  const run = () => {
    try {
      fn();
    } catch (err) {
      console.warn("footman casts:", err);
    }
  };
  if (window.document$ && typeof window.document$.subscribe === "function") {
    window.document$.subscribe(run); // instant navigation: fires per page
  } else if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", run);
  } else {
    run();
  }
}

const PLAY = "M8 5v14l11-7z";
const PAUSE = "M6 5h4v14H6zm8 0h4v14h-4z";
const REPLAY =
  "M12 5V1L7 6l5 5V7a6 6 0 1 1-6 6H4a8 8 0 1 0 8-8z";

function icon(path) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
  p.setAttribute("d", path);
  svg.appendChild(p);
  return svg;
}

function clock(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  return `0:${String(s).padStart(2, "0")}`;
}

/* Turn one <img src="…-cast.svg"> into a player. */
async function enhance(img) {
  const source = img.getAttribute("src");
  if (!source) return;

  const response = await fetch(source);
  if (!response.ok) return;
  const markup = await response.text();

  const parsed = new DOMParser().parseFromString(markup, "image/svg+xml");
  const svg = parsed.documentElement;
  if (!svg || svg.nodeName.toLowerCase() !== "svg") return;

  const figure = document.createElement("figure");
  figure.className = "cast";
  // The alt text described the session for anyone who could not see it, and
  // inlining must not lose that: it becomes the figure's accessible name.
  const description = img.getAttribute("alt") || "Terminal recording";
  figure.setAttribute("role", "group");
  figure.setAttribute("aria-label", description);

  const stage = document.createElement("div");
  stage.className = "cast-stage";
  stage.appendChild(document.importNode(svg, true));
  figure.appendChild(stage);


  img.replaceWith(figure);

  // A recording inside a closed tab is `display: none`, so its CSS
  // animations do not exist yet and getAnimations() finds nothing to drive.
  // Wiring the controls has to wait until the tab is actually opened —
  // otherwise only the tab that happens to be open on page load gets them.
  if (!wire(figure)) {
    const seen = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting) && wire(figure)) {
        seen.disconnect();
      }
    });
    seen.observe(figure);
  }
}

/* Hang the controls off an inlined recording. Returns false when the
 * animations are not live yet (a hidden tab), so the caller can retry. */
function wire(figure) {
  if (figure.dataset.wired) return true;

  const animations = figure.getAnimations
    ? figure.getAnimations({ subtree: true })
    : [];
  if (!animations.length) return false; // hidden, or animating on its own

  const timing = animations[0].effect.getComputedTiming();
  const cycle = Number(timing.duration) || 0;
  if (!cycle) return false;
  figure.dataset.wired = "1";

  /* ---- controls ---- */

  const controls = document.createElement("div");
  controls.className = "cast-controls";

  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "cast-toggle";
  controls.appendChild(toggle);

  const scrub = document.createElement("input");
  scrub.type = "range";
  scrub.className = "cast-scrub";
  scrub.min = "0";
  scrub.max = String(Math.round(cycle));
  scrub.step = "1";
  scrub.value = "0";
  scrub.setAttribute("aria-label", "Seek within the recording");
  controls.appendChild(scrub);

  const time = document.createElement("span");
  time.className = "cast-time";
  controls.appendChild(time);

  const replay = document.createElement("button");
  replay.type = "button";
  replay.className = "cast-replay";
  replay.title = "Play from the start";
  replay.setAttribute("aria-label", "Play from the start");
  replay.appendChild(icon(REPLAY));
  controls.appendChild(replay);

  figure.appendChild(controls);

  /* ---- driving them ---- */

  const at = () => Number(animations[0].currentTime) || 0;
  const seek = (ms) => {
    for (const a of animations) a.currentTime = ms;
  };
  const playing = () => animations[0].playState === "running";

  let raf = 0;
  let dragging = false;
  let shown = null; // which icon the button is currently showing

  const label = (t) =>
    (time.textContent = `${clock(t / 1000)} / ${clock(cycle / 1000)}`);

  // Only touched when the state actually flips. Repainting the button every
  // animation frame replaced the <svg> under the pointer between mousedown
  // and mouseup, so the click never completed and Pause did nothing.
  const paintButton = () => {
    const now = playing();
    if (now === shown) return;
    shown = now;
    toggle.title = now ? "Pause" : "Play";
    toggle.setAttribute("aria-label", now ? "Pause" : "Play");
    toggle.replaceChildren(icon(now ? PAUSE : PLAY));
  };

  const tick = () => {
    if (!dragging) {
      const t = at() % cycle;
      scrub.value = String(Math.round(t));
      label(t);
    }
    paintButton();
    raf = playing() ? requestAnimationFrame(tick) : 0;
  };

  const pause = () => {
    for (const a of animations) a.pause();
    if (raf) cancelAnimationFrame(raf);
    raf = 0;
    paintButton();
  };
  const play = () => {
    for (const a of animations) a.play();
    paintButton();
    if (!raf) raf = requestAnimationFrame(tick);
  };

  toggle.addEventListener("click", () => (playing() ? pause() : play()));
  replay.addEventListener("click", () => {
    seek(0);
    label(0);
    scrub.value = "0";
    play();
  });

  // Order matters: read the slider, *then* pause. Pausing used to repaint
  // the slider from the animation clock first, overwriting the value being
  // dragged, so every drag seeked back to where it started.
  const scrubTo = () => {
    const t = Number(scrub.value);
    if (playing()) pause();
    seek(t);
    label(t);
  };
  scrub.addEventListener("pointerdown", () => {
    dragging = true;
  });
  scrub.addEventListener("input", scrubTo);
  const endDrag = () => {
    dragging = false;
  };
  scrub.addEventListener("pointerup", endDrag);
  scrub.addEventListener("pointercancel", endDrag);
  scrub.addEventListener("blur", endDrag);

  // A recording that starts moving is the point of the front page — but a
  // reader who has asked the OS for less motion gets a still first frame
  // and a play button, not a surprise.
  const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (still) {
    seek(0);
    scrub.value = "0";
    label(0);
    pause();
  } else {
    play();
  }
  return true;
}

onEachPage(() => {
  for (const img of document.querySelectorAll('img[src$="cast.svg"]')) {
    enhance(img).catch((err) => console.warn("footman casts:", err));
  }
});
