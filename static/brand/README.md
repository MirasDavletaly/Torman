# Torman brand assets

The mark is a geometric T formed by a table header and a column. The lime cell
represents a selected catalogue item. The letter's diagonal foot distinguishes
the silhouette at small sizes.

- `logo-light.svg`: complete logo for the application's dark navigation.
- `logo-dark.svg`: complete logo for light backgrounds.
- `mark.svg`: compact symbol for small brand placements.
- `logo-dark.png`: transparent, outlined wordmark for the native launcher.
- `torman.ico`: Windows executable, window and taskbar icon, with 16, 24, 32,
  48, 64, 128 and 256 px frames.
- `icon-*.png`: native window icon sizes.
- `apple-touch-icon.png`: browser home-screen shortcut.
- `preview.png`: dark/light and small-size visual reference.

Palette: forest `#163D31`, ivory `#F4F6E9`, lime `#C7EB91`.
Use the full logo in navigation and the launcher. Use only the symbol for app
icons, favicons and the footer. Preserve the proportions and leave clear space
around the mark. Do not repeat it inside product cards or replace action icons.

SVG lettering is stored as paths, so it does not depend on installed fonts.
To regenerate all files on Windows, run `scripts/build-brand.ps1` from the
project root. Generation uses built-in System.Drawing and Segoe UI; the app
itself needs neither a new dependency nor font installation.
