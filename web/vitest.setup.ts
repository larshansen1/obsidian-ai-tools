import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(cleanup);

// jsdom has no ResizeObserver; assistant-ui's viewport needs one.
class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
globalThis.ResizeObserver = ResizeObserverStub;

// jsdom has no element scrollTo; the chat viewport calls it when a message is added.
if (!Element.prototype.scrollTo) {
  Element.prototype.scrollTo = () => undefined;
}
