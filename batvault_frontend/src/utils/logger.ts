export function log(event: string, payload?: any) {
  // eslint-disable-next-line no-console
  if (process.env.NODE_ENV !== "production") {
    console.log(`[${event}]`, payload ?? "");
  }
}