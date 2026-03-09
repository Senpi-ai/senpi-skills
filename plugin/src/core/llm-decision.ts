/**
 * LLM Decision Layer — One-shot LLM calls with context injection.
 *
 * New component (replaces agent reading mandate + reasoning).
 * Focused one-shot LLM call interface that can later integrate
 * with OpenClaw's llm-task API when it exists.
 */

import { LlmDecisionRequest, LlmDecisionResponse } from './types.js';
import { logger } from './logger.js';

export interface ContextData {
  [key: string]: unknown;
}

/**
 * Build context for an LLM decision based on requested context types.
 * Each context type maps to specific data that gets injected into the prompt.
 */
export class ContextBuilder {
  private providers = new Map<string, () => unknown>();

  /** Register a context provider */
  registerProvider(name: string, provider: () => unknown): void {
    this.providers.set(name, provider);
  }

  /** Build context data from a list of context type names */
  build(contextTypes: string[]): ContextData {
    const data: ContextData = {};
    for (const type of contextTypes) {
      const provider = this.providers.get(type);
      if (provider) {
        try {
          data[type] = provider();
        } catch (err) {
          logger.warn(`Context provider ${type} failed`, { error: String(err) });
          data[type] = null;
        }
      } else {
        logger.debug(`No context provider for: ${type}`);
      }
    }
    return data;
  }
}

/**
 * Make a one-shot LLM decision call.
 *
 * Currently a placeholder that will be connected to an actual LLM client
 * (e.g. @anthropic-ai/sdk or OpenClaw's llm-task API) in Phase 4.
 */
export class LlmDecision {
  private apiKey?: string;

  constructor(apiKey?: string) {
    this.apiKey = apiKey;
  }

  /**
   * Make a structured LLM decision call.
   * Expects the LLM to return valid JSON matching the expected type.
   */
  async decide<T>(request: LlmDecisionRequest): Promise<LlmDecisionResponse<T>> {
    const start = Date.now();

    // Build the full prompt with context
    const contextStr = Object.entries(request.context)
      .map(([key, value]) => `## ${key}\n${JSON.stringify(value, null, 2)}`)
      .join('\n\n');

    const fullPrompt = `${request.prompt}\n\n--- CONTEXT ---\n${contextStr}\n\n--- END CONTEXT ---\n\nRespond with valid JSON only.`;

    logger.debug('LLM decision request', {
      model: request.model ?? 'sonnet',
      promptLength: fullPrompt.length,
    });

    // TODO: Connect to actual LLM API
    // For now, this is a stub that will be implemented when the LLM client SDK is added
    throw new Error(
      'LLM decision layer not yet connected. Add @anthropic-ai/sdk dependency and implement the API call.',
    );

    // When implemented, the flow will be:
    // 1. Call LLM API with fullPrompt
    // 2. Parse JSON response
    // 3. Validate with zod schema
    // 4. Return typed result
  }

  /**
   * Make a decision with retry on parse failure.
   */
  async decideWithRetry<T>(
    request: LlmDecisionRequest,
    maxRetries = 2,
  ): Promise<LlmDecisionResponse<T>> {
    let lastError: unknown;

    for (let attempt = 0; attempt < maxRetries; attempt++) {
      try {
        return await this.decide<T>(request);
      } catch (err) {
        lastError = err;
        logger.warn(`LLM decision attempt ${attempt + 1} failed`, {
          error: String(err),
        });
      }
    }

    throw new Error(`LLM decision failed after ${maxRetries} attempts: ${String(lastError)}`);
  }
}
