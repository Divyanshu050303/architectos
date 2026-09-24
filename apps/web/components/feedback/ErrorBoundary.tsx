"use client";

import { Component, type ErrorInfo, type ReactNode } from "react";

import { ErrorState } from "./ErrorState";

export interface ErrorBoundaryProps {
  /** Name of the region, e.g. "Simulation", used in the fallback title. */
  label: string;
  children: ReactNode;
}

interface ErrorBoundaryState {
  error: Error | null;
}

/**
 * Feature-level boundary: a failure in one region (e.g. simulation) must not crash
 * the whole workspace (spec §82).
 */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  override state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: unknown): ErrorBoundaryState {
    return { error: error instanceof Error ? error : new Error(String(error)) };
  }

  override componentDidCatch(error: Error, info: ErrorInfo) {
    if (process.env.NODE_ENV !== "production") {
      console.error(`[ErrorBoundary:${this.props.label}]`, error, info.componentStack);
    }
  }

  private readonly reset = () => this.setState({ error: null });

  override render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <ErrorState
        title={`${this.props.label} could not be displayed.`}
        message="An unexpected error occurred in this section. The rest of the workspace still works."
        noChangesApplied
        onRetry={this.reset}
        details={error.message}
      />
    );
  }
}
