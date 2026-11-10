# Source modes

Two design studies generated with the built-in image generation tool before implementation:

- `website.png`: URL mode, retaining scan settings.
- `files.png`: file mode, with a file picker and selected-file list.

Implementation keeps the existing app typography, navigation and 4:5 / 9:16 outputs. The right-hand showcase follows the dark panel in `files.png`, with responsive HTML captions, a CSS document cover and a shared scenic image. Generated mockup copy and incidental format labels are illustrative; the implemented previews use the actual portrait output ratios.

## Showcase image asset

Both source modes share a decorative contact-sheet frame via the accessibility-hidden `film_edge` macro.

Website mode illustrates colour, typography and imagery extraction; Files mode shows images and PDFs becoming video. `sources.js` hides the inactive figure, including from the accessibility tree. The NORTH website is fictitious.

`app/static/source-landscape.webp` was generated with the built-in image generation tool, then encoded as WebP for local serving. No external image requests are needed. The HTML/CSS panel reuses the image in both example output formats; these are labelled illustrations, not playable videos.

Generation prompt:

Use case: photorealistic-natural. Asset type: shared scenic photograph for the Kiehnema website and file-to-video demo cards. Create a realistic editorial photograph of a northern German North Sea beach: pale sand dunes with windblown marram grass in the foreground, a broad empty sandy beach, gentle blue-grey sea and a flat distant horizon. Quiet coastal atmosphere, soft late-afternoon light, muted natural sand, straw, sage and sea-blue colours. Wide 3:2 composition. Keep the sea and beach clearly recognizable in centered portrait crops of 4:5 and 9:16, as well as a shallow wide website hero crop. Natural fine photographic texture. No people, buildings, mountains, tropical vegetation, text, logos, watermarks, frames, collage or UI. Typography will be added in HTML.

## Generation prompts

### 1

Use case: ui-mockup. Create a polished desktop UI design mockup for existing Kiehnema video creation app. Warm paper #f4f0e7, ivory cards #fffdf8, charcoal #292b28, muted ochre #d5ad68, editorial serif italics, clean sans labels, monospace small metadata. Header Kiehnema. Main heading "Your brand. A little more motion." Two column layout: left New video brief, right dark cinematic contact sheet displaying two portrait video formats. At the very top of brief a segmented source mode selection "Website URL" and "Files", with Website URL selected. Below a URL input, collapsed Website scan settings, duration, Creative direction textarea, Create preview charcoal button. Bottom note "You’ll review everything before the final render." Elegant quiet established product, fine uniform borders only, no colored side stripes, no generic gradient UI. Design concept 01, landscape screen.

### 2

Use case: ui-mockup. Create a polished desktop UI design mockup for existing Kiehnema video creation app, files source mode. Warm paper #f4f0e7, ivory cards #fffdf8, charcoal #292b28, muted ochre #d5ad68, editorial serif italics, clean sans labels, monospace small metadata. Header Kiehnema. Main heading "Your brand. A little more motion." Two column layout: left New video brief, right dark cinematic contact sheet showing image and PDF source material becoming two video formats. At top of brief segmented source mode "Website URL" and "Files" with Files selected. A spacious upload area with simple outlined image and document motifs and "Choose images or PDFs", format help "PNG, JPG, WebP · PDF", then two selected file rows "Brand guide.pdf" and "Campaign.jpg" with small thumbnails and file sizes. Below duration and Creative direction field, charcoal "Create preview" button. Fine uniform borders only, no colored side stripes, accessible clean controls, professional editorial layout. Design concept 02, landscape screen.

