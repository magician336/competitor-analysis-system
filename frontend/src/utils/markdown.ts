import { marked } from "marked";
import DOMPurify from "dompurify";

export function renderMarkdown(markdown: string): string {
  return DOMPurify.sanitize(marked.parse(markdown) as string);
}
