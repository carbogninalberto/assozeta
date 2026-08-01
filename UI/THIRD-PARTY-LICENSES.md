# Third-Party Notices

This software includes third-party open-source components. Those components are the property of their respective copyright holders and are provided under their respective license terms.

This notice applies only to third-party components included with or used by this software. The application itself is licensed separately under the GNU Affero General Public License v3.0. See `LICENSE` for the application license.

No third-party license listed below grants rights to this application's own source code, trademarks, branding, or service names.

## Third-Party Components

### Node.js / npm Packages

| Component | License |
| --- | --- |
| @capacitor/camera | MIT |
| @capacitor/core | MIT |
| @ernane/svelte-star-rating | MIT |
| @event-calendar/build | MIT |
| @sveltejs/vite-plugin-svelte | MIT |
| @tiptap/core | MIT |
| @tiptap/extension-bubble-menu | MIT |
| @tiptap/extension-dropcursor | MIT |
| @tiptap/extension-history | MIT |
| @tiptap/extension-image | MIT |
| @tiptap/extension-link | MIT |
| @tiptap/extension-mention | MIT |
| @tiptap/extension-paragraph | MIT |
| @tiptap/extension-placeholder | MIT |
| @tiptap/extension-table | MIT |
| @tiptap/extension-table-cell | MIT |
| @tiptap/extension-table-header | MIT |
| @tiptap/extension-table-row | MIT |
| @tiptap/extension-task-item | MIT |
| @tiptap/extension-task-list | MIT |
| @tiptap/extension-text-align | MIT |
| @tiptap/extension-underline | MIT |
| @tiptap/pm | MIT |
| @tiptap/starter-kit | MIT |
| @tiptap/suggestion | MIT |
| axios | MIT |
| canvas-confetti | ISC |
| chalk | MIT |
| chart.js | MIT |
| cross-env | MIT |
| each-async | MIT |
| echarts | Apache-2.0 |
| fetch-intercept | MIT |
| filepond | MIT |
| filepond-plugin-file-encode | MIT |
| filepond-plugin-image-exif-orientation | MIT |
| filepond-plugin-image-preview | MIT |
| html-to-text | MIT |
| html2canvas | MIT |
| html5-qrcode | Apache-2.0 |
| indent-string | MIT |
| jose | MIT |
| lint-staged | MIT |
| lodash | MIT |
| lucide | ISC |
| lucide-svelte | ISC |
| moment | MIT |
| svelte-content-loader | MIT |
| svelte-filepond | MIT |
| svelte-i18n | MIT |
| svelte-markdown | MIT |
| svelte-portal | MIT |
| svelte-qrcode | MIT |
| svelte-select | ISC |
| svelte-sonner | MIT |
| svelte-sortable-items | ISC |
| svelte-spa-router | MIT |
| svelte-stripe | MIT |
| svelte-tiny-virtual-list | MIT |
| svelvet | MIT |
| sweetalert2 | MIT |
| tippy.js | MIT |
| tiptap-extension-resize-image | MIT |
| tiptap-imagresize | MIT |
| uuid | MIT |
| vite | MIT |
| whatwg-fetch | MIT |
| xhook | MIT |
| xml2js | MIT |
| zod | MIT |

### Bundled JavaScript

| Component | License | Shipped Path | Upstream Source |
| --- | --- | --- | --- |
| Waypoint email-builder-js | MIT | `src/components/inputs/email-builder/emailbuilder.js` | https://github.com/usewaypoint/email-builder-js |

### Shipped Static CSS / JS Libraries

These libraries are included as pre-built static asset files (`public/static/`) and are not resolved through npm/node_modules at runtime. Their upstream license terms govern use of these asset files.

| Library | Version | License | Shipped Path |
| --- | --- | --- | --- |
| Bootstrap CSS | 4.x | MIT | `public/static/css/bootstrap.min.css` |
| normalize.css | 3.0.3 | MIT | Bundled subset within `public/static/css/app-bundle.css` |
| Dropzone CSS | n/a (subset) | MIT | Bundled subset within `public/static/css/app-bundle.css` |
| SweetAlert2 CSS | n/a (subset) | MIT | Bundled subset within `public/static/css/app-bundle.css` |
| @event-calendar/build | n/a | MIT | `public/static/assets/plugins/event-calendar/event-calendar.min.css` and `.min.js` |

### Bundled CSS File Attribution

The file `public/static/css/app-bundle.css` is an application-maintained CSS bundle. It includes CSS subsets from the following third-party projects:

- **normalize.css** v3.0.3 - MIT License - Copyright Nicolas Gallagher and Jonathan Neal - https://github.com/necolas/normalize.css
- **Dropzone** - MIT License - Copyright Matias Meno - https://www.dropzone.dev
- **SweetAlert2** - MIT License - Copyright Limon Monte and contributors - https://sweetalert2.github.io

These subsets are embedded within a single file for application bundling purposes. Each subset remains under its original license and copyright.

## Included MIT License Notice

The following copyright notices apply to their corresponding components:

- Copyright (c) 2024 Waypoint (Metaccountant, Inc.)
- Copyright (c) 2021 vkurko
- Copyright (c) 2011-2022 Twitter, Inc. and The Bootstrap Authors
- Copyright Nicolas Gallagher and Jonathan Neal
- Copyright (c) 2021 Matias Meno
- Copyright (c) 2014 Tristan Edwards and Limon Monte
- Copyright (c) Facebook, Inc. and its affiliates, for the React code embedded in the Waypoint bundle

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## Distributed Assets

This distribution may include static style, script, image, icon, and media assets from third parties. Those assets remain subject to the license terms and attribution requirements of their respective owners. They are not relicensed under the application license except where expressly stated by their upstream license.

## Static Media Assets - Asset Provenance

### Application-Owned Branding

The following assets are application-owned branding/logos. They are **not** third-party open source and are **not** offered under any open-source license. Do not reuse, redistribute, or modify these assets under any third-party license terms.

| Asset | Path |
| --- | --- |
| Application favicon | `public/favicon.svg` |
| Assozeta brand logos | `public/oem/assozeta/brand/logo.svg`, `public/oem/assozeta/brand/logo_dark_mode.svg` |

### OEM / Client-Specific Branding

The following OEM branding assets are owned by the respective client organizations. They are **not** part of this application's open-source offering and are **not** offered under any open-source license.

| OEM | Path |
| --- | --- |
| None currently bundled | N/A |

### Release-Review Notes - Assets Requiring Provenance Confirmation

The following assets appear in the distribution but their exact provenance and licensing have not been independently confirmed at time of release. They require review before redistribution.

| Asset | Path(s) | Note |
| --- | --- | --- |
| Illustration SVGs | `public/static/assets/media/svg/illustrations/` (all files) | May originate from a commercial or free illustration kit. Provenance TBD - review required. |
| User placeholder image | `public/static/assets/media/users/blank.png` | Generic placeholder; likely app-created or from a UI kit. |
| Testimonial images | `public/static/images/testimonials/*.jpeg` | Client-supplied photos. Verify usage rights with each client. |
| Facebook logo | `public/static/f_logo_RGB-Blue_72.png` | Facebook brand asset. Use must comply with Facebook brand guidelines. |
| Stripe watermark | `public/static/stripe_watermark.png` | Stripe brand asset. Use must comply with Stripe brand guidelines. |
| Background / decorative images | `public/static/images/bg-testimonials.png`, `public/static/banner_testimonial.png` | May be stock photography. Verify license terms. |

## License Text References

The most common third-party licenses represented in this notice are:

- MIT License
- ISC License
- Apache License 2.0
- BSD 2-Clause License

Copies of these license texts are available from their respective upstream projects and from the SPDX license list at https://spdx.org/licenses/.
