/* fx.js — the polish layer.
 *
 * Purely additive over app.js: it drives the ambient aurora (anime.js), springs
 * freshly-rendered tiles into place, and adds pointer-follow glow, 3D tilt, and
 * magnetic buttons (Motion). It owns no state and no data path. If anime.js or
 * Motion fail to load, or the user asked for reduced motion, every effect no-ops
 * and the page is simply the restyled static version — nothing breaks.
 *
 * It never touches app.js's logic. New content is discovered with a MutationObserver
 * on .wrap, so there are zero hooks to add on the app side.
 */
(function () {
  "use strict";

  var anime = window.anime;
  var M = window.Motion;
  var reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Card-like tiles that earn an entrance and a glow. Rows/prose are left to CSS.
  var REVEAL = ".card, .kpi, .banner, .sector-card, .ratio, .read, .brief-col, .brief-market";

  /* --- 1. Aurora drift (anime.js) ------------------------------------------
   * Three colour fields easing back and forth forever. Slow and out of phase so
   * the light never looks like it's on a timer. Held still under reduced-motion. */
  function aurora() {
    if (!anime || reduce) return;
    var defs = [
      [".aurora .b1", 46, -30, 1.16, 15000],
      [".aurora .b2", -42, 28, 1.12, 18500],
      [".aurora .b3", 28, 40, 1.22, 21000]
    ];
    defs.forEach(function (d) {
      if (!document.querySelector(d[0])) return;
      anime.animate(d[0], {
        translateX: [0, d[1]],
        translateY: [0, d[2]],
        scale: [1, d[3]],
        duration: d[4],
        ease: "inOutQuad",
        loop: true,
        alternate: true
      });
    });
  }

  /* --- 2. Spring-in for freshly rendered tiles (Motion) --------------------
   * Content built by app.js (KPIs, insights, sector cards, ratios…) is injected
   * after render; each new tile fades + lifts into place, gently staggered. Inline
   * styles are cleared on settle so CSS owns the resting state (hover/tilt intact). */
  function springIn(nodes) {
    nodes.forEach(function (n, i) {
      wire(n);
      if (n.__fxshown) return;
      n.__fxshown = true;
      if (reduce || !M) return;
      var controls = M.animate(
        n,
        { opacity: [0, 1], y: [12, 0], scale: [0.985, 1] },
        { duration: 0.5, delay: Math.min(i, 12) * 0.045, ease: [0.22, 1, 0.36, 1] }
      );
      if (controls && controls.finished) {
        controls.finished
          .then(function () { n.style.opacity = ""; n.style.transform = ""; })
          .catch(function () {});
      }
    });
  }

  /* --- 3. Pointer-follow glow + 3D tilt -----------------------------------
   * Every tile tracks the cursor with a soft accent glow (via --mx/--my, styled in
   * CSS). The small showpiece tiles (KPIs, sector cards) also tilt toward it. Wired
   * once per element. */
  var TILT = 5; // degrees
  function wire(card) {
    if (card.__fxwired) return;
    card.__fxwired = true;
    var tilt = !reduce && (card.classList.contains("hero-stat") || card.classList.contains("sector-card"));
    card.addEventListener("pointermove", function (e) {
      var r = card.getBoundingClientRect();
      var xr = (e.clientX - r.left) / r.width;
      var yr = (e.clientY - r.top) / r.height;
      card.style.setProperty("--mx", (xr * 100).toFixed(1) + "%");
      card.style.setProperty("--my", (yr * 100).toFixed(1) + "%");
      if (tilt) {
        card.style.transform =
          "perspective(760px) rotateX(" + ((0.5 - yr) * TILT).toFixed(2) +
          "deg) rotateY(" + ((xr - 0.5) * TILT).toFixed(2) + "deg) translateY(-3px)";
      }
    });
    if (tilt) {
      card.addEventListener("pointerleave", function () { card.style.transform = ""; });
    }
  }

  /* --- 4. Magnetic primary buttons (Motion spring) ------------------------ */
  function magnetise(el) {
    if (el.__fxmag) return;
    el.__fxmag = true;
    if (reduce || !M) return;
    el.addEventListener("pointermove", function (e) {
      var r = el.getBoundingClientRect();
      M.animate(
        el,
        { x: (e.clientX - r.left - r.width / 2) * 0.25, y: (e.clientY - r.top - r.height / 2) * 0.35 },
        { type: "spring", stiffness: 280, damping: 18 }
      );
    });
    el.addEventListener("pointerleave", function () {
      M.animate(el, { x: 0, y: 0 }, { type: "spring", stiffness: 200, damping: 16 });
    });
  }

  /* --- 5. Discover new content and enhance it ----------------------------- */
  function scan(root) {
    root = root || document;
    var fresh = [];
    root.querySelectorAll(REVEAL).forEach(function (n) { if (!n.__fxshown) fresh.push(n); });
    if (fresh.length) springIn(fresh);
    root.querySelectorAll("button.action.primary").forEach(magnetise);
  }

  function observe() {
    var target = document.querySelector(".wrap") || document.body;
    var pending = false;
    new MutationObserver(function () {
      if (pending) return;
      pending = true;
      requestAnimationFrame(function () { pending = false; scan(document); });
    }).observe(target, { childList: true, subtree: true });
  }

  /* --- 6. Aurora parallax on scroll --------------------------------------
   * The whole field drifts a little slower than the page, so depth reads as you
   * move. Set on the container, independent of the per-blob anime drift. */
  function parallax() {
    if (reduce) return;
    var au = document.querySelector(".aurora");
    if (!au) return;
    var ticking = false;
    addEventListener("scroll", function () {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(function () {
        ticking = false;
        au.style.transform = "translate3d(0," + (scrollY * 0.08).toFixed(1) + "px,0)";
      });
    }, { passive: true });
  }

  function init() {
    aurora();
    parallax();
    scan(document);
    observe();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
