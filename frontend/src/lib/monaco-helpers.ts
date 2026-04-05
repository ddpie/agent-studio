/** Monaco editor language detection from file extension */
export function getMonacoLanguage(filename: string): string {
  if (filename.endsWith(".py")) return "python";
  if (filename.endsWith(".md")) return "markdown";
  if (filename.endsWith(".json")) return "json";
  if (filename.endsWith(".js")) return "javascript";
  if (filename.endsWith(".ts")) return "typescript";
  if (filename.endsWith(".yaml") || filename.endsWith(".yml")) return "yaml";
  if (filename.endsWith(".sh") || filename.endsWith(".bash")) return "shell";
  if (filename.endsWith(".html")) return "html";
  if (filename.endsWith(".css")) return "css";
  return "plaintext";
}
