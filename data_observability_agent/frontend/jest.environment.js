const { default: JsDomEnvironment } = require("jest-environment-jsdom");

/**
 * Custom jest environment that extends jsdom and injects the Node.js 18+
 * native Fetch API globals (Response, Request, Headers, fetch, ReadableStream)
 * that jsdom otherwise hides from tests.
 */
class FetchAwareJsDomEnvironment extends JsDomEnvironment {
  async setup() {
    await super.setup();

    const { ReadableStream } = require("stream/web");
    this.global.ReadableStream = ReadableStream;

    // These are available in the Node.js setup() context (Node 18+)
    /* global Response, Request, Headers, fetch */
    if (typeof Response !== "undefined") this.global.Response = Response;
    if (typeof Request !== "undefined") this.global.Request = Request;
    if (typeof Headers !== "undefined") this.global.Headers = Headers;
    if (typeof fetch !== "undefined") this.global.fetch = fetch;
  }
}

module.exports = FetchAwareJsDomEnvironment;
