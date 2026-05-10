import { render, screen } from "@testing-library/react";
import { SourceLink } from "@/components/SourceLink";

test("resolves airflow URI to readable label", () => {
  render(
    <SourceLink uri="airflow:dag=orders_pipeline/task=extract_orders/run=run_001/try=1/ts=2026-04-22T02:14:00Z" />
  );
  expect(screen.getByText(/orders_pipeline/)).toBeInTheDocument();
  expect(screen.getByText(/extract_orders/)).toBeInTheDocument();
});

test("resolves dbcheck URI to readable label", () => {
  render(
    <SourceLink uri="dbcheck:table=orders/metric=composite/ts=2026-04-22T01:00:00Z" />
  );
  expect(screen.getByText(/orders/)).toBeInTheDocument();
  expect(screen.getByText(/composite/)).toBeInTheDocument();
});

test("renders unknown URI scheme as plain text", () => {
  render(<SourceLink uri="unknown:some/path" />);
  expect(screen.getByText("unknown:some/path")).toBeInTheDocument();
});
