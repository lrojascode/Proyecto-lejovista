export function safeRedirect(url: string) {
  if (typeof window !== "undefined") {
    window.location.replace(url);
  }
}
