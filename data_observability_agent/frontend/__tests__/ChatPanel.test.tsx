import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ChatPanel } from "@/components/ChatPanel";

// Mock global fetch to return an SSE stream
function mockSseResponse(chunks: string[]) {
  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(`data: ${chunk}\n\n`));
      }
      controller.enqueue(encoder.encode("data: [DONE]\n\n"));
      controller.close();
    },
  });
  return new Response(stream, {
    headers: { "Content-Type": "text/event-stream" },
  });
}

beforeEach(() => {
  process.env.NEXT_PUBLIC_API_URL = "http://localhost:8000";
});

test("renders chat input and submit button", () => {
  render(<ChatPanel apiKey="test-key" />);
  expect(screen.getByPlaceholderText(/ask about/i)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /send/i })).toBeInTheDocument();
});

test("streams response and displays it", async () => {
  global.fetch = jest.fn().mockResolvedValueOnce(
    mockSseResponse(["orders_pipeline failed at extract_orders."])
  );
  render(<ChatPanel apiKey="test-key" />);
  await userEvent.type(screen.getByPlaceholderText(/ask about/i), "pipeline status?");
  await userEvent.click(screen.getByRole("button", { name: /send/i }));
  await waitFor(() => {
    expect(screen.getByText(/orders_pipeline failed/)).toBeInTheDocument();
  });
});

test("shows degraded reranking banner when stream contains flag", async () => {
  global.fetch = jest.fn().mockResolvedValueOnce(
    mockSseResponse(["[DEGRADED_RERANKING]", "orders ok."])
  );
  render(<ChatPanel apiKey="test-key" />);
  await userEvent.type(screen.getByPlaceholderText(/ask about/i), "pipeline status?");
  await userEvent.click(screen.getByRole("button", { name: /send/i }));
  await waitFor(() => {
    expect(screen.getByText(/reranking/i)).toBeInTheDocument();
  });
});

test("sends X-API-Key header", async () => {
  global.fetch = jest.fn().mockResolvedValueOnce(mockSseResponse(["ok."]));
  render(<ChatPanel apiKey="my-api-key" />);
  await userEvent.type(screen.getByPlaceholderText(/ask about/i), "q");
  await userEvent.click(screen.getByRole("button", { name: /send/i }));
  await waitFor(() => expect(global.fetch).toHaveBeenCalled());
  const [, init] = (global.fetch as jest.Mock).mock.calls[0];
  expect((init as RequestInit).headers?.["X-API-Key"]).toBe("my-api-key");
});
