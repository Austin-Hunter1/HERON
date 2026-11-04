/* s02.js - Part 02: needs of a GNSS.
 * The cards are <details> elements, so the presenter can open one need at
 * a time while the team talks about it. These two buttons open or close
 * all of them at once, for review after the talk. */
G.section("s02", (root) => {
  const set = (open) => root.querySelectorAll("details.need").forEach((d) => (d.open = open));
  root.querySelector("#s02-all").addEventListener("click", () => set(true));
  root.querySelector("#s02-none").addEventListener("click", () => set(false));
});
