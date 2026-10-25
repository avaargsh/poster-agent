# Third-party source notices

The project's own source remains licensed under Apache License 2.0. The
following adapted portions retain their upstream notice.

## EcomGen: compatible image response parsing

- Upstream: https://github.com/linbei0/EcomGen
- Source: `packages/providers/src/openai-compatible.ts`
- Reviewed revision: `966a7c0ec57dad766ecc3be9edaa07033ee78b12`
- Local adaptation: `app/image_provider.py`, notably its PNG/JPEG/WEBP
  byte-signature detection and support for inline Base64 image responses.
- Changes: translated from TypeScript into Python; strict size, decoded pixel,
  allowed-host and no-retry rules preserved or added for Poster Agent.
- Copyright: Copyright (c) 2026 linbei0
- License: MIT

MIT License

Copyright (c) 2026 linbei0

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
