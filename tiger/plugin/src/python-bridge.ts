import { spawn } from 'node:child_process';
import type { TigerPluginConfig, TigerPaths, ScriptResult, ScriptRunOptions } from './types.js';

export class PythonBridge {
  constructor(
    private readonly paths: TigerPaths,
    private readonly config: TigerPluginConfig,
  ) {}

  async run<T>(
    scriptName: string,
    options?: ScriptRunOptions,
  ): Promise<ScriptResult<T>> {
    const scriptPath = `${this.paths.scriptsDir}/${scriptName}`;
    const timeout = options?.timeout ?? this.config.scriptTimeout;

    const env: Record<string, string> = {
      ...process.env as Record<string, string>,
      TIGER_WORKSPACE: this.paths.workspace,
      ...options?.env,
    };

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);

    try {
      return await new Promise<ScriptResult<T>>((resolve) => {
        const child = spawn(this.config.pythonPath, [scriptPath], {
          env,
          signal: controller.signal,
          stdio: ['ignore', 'pipe', 'pipe'],
        });

        let stdout = '';
        let stderr = '';

        child.stdout.on('data', (chunk: Buffer) => {
          stdout += chunk.toString();
        });
        child.stderr.on('data', (chunk: Buffer) => {
          stderr += chunk.toString();
        });

        child.on('error', (err: Error) => {
          if (err.name === 'AbortError') {
            resolve({
              success: false,
              error: `Script timed out after ${timeout}ms: ${scriptName}`,
              stderr,
              exitCode: -1,
            });
          } else {
            resolve({
              success: false,
              error: `Failed to spawn script: ${err.message}`,
              stderr,
              exitCode: -1,
            });
          }
        });

        child.on('close', (code: number | null) => {
          const exitCode = code ?? 1;

          // Always attempt JSON parse — DSL exits 1 on error but still produces valid JSON
          try {
            const data = JSON.parse(stdout) as T;
            resolve({
              success: exitCode === 0,
              data,
              stderr: stderr || undefined,
              exitCode,
            });
          } catch {
            resolve({
              success: false,
              error: stdout
                ? `Failed to parse script output: ${stdout.slice(0, 200)}`
                : `Script produced no output (exit code ${exitCode})`,
              stderr: stderr || undefined,
              exitCode,
            });
          }
        });
      });
    } finally {
      clearTimeout(timer);
    }
  }
}
