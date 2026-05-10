import "@testing-library/jest-dom";
import { TextEncoder, TextDecoder } from "util";

// jsdom hides TextEncoder/TextDecoder — restore from Node util
Object.assign(global, { TextEncoder, TextDecoder });
