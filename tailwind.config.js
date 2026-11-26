/** @type {import('tailwindcss').Config} */
module.exports = {
  // static/js is included too: several pages build markup client-side
  // (e.g. builders.js's live build-log view, images-status.js's status
  // poller) using Tailwind classes that appear nowhere in a .html template,
  // so without this they'd silently be missing from the compiled output.
  content: ["./app/templates/**/*.html", "./app/static/js/**/*.js"],
  // Flash messages build their class as alert-{{ category }}, which the
  // scanner can't see, so without this success/info flashes render grey.
  safelist: ["alert-success", "alert-error", "alert-info", "alert-warning"],
  theme: {
    extend: {},
  },
  plugins: [require("daisyui")],
};
