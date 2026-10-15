// The home page's tour autoplays, muted and looping. Someone who has asked for reduced motion gets
// the poster and the play button instead. document$ re-runs this after every instant navigation,
// since a page swapped in that way never fires a fresh load.
document$.subscribe(() => {
  const video = document.querySelector(".ax-video video");
  if (video && window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    video.removeAttribute("autoplay");
    video.pause();
  }
});
