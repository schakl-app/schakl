/**
 * Recorded audio → base64, for the JSON hop to the API (#246).
 *
 * The web app reaches the API through one same-origin proxy that forwards JSON
 * (`routes/(app)/ai/[...path]/+server.ts`). Adding a multipart path for a single endpoint
 * would cost more than the 33% base64 adds to a clip measured in tens of kilobytes.
 *
 * Pure and dependency-free so it can be unit-tested without a browser.
 */

/** Strip the `data:...;base64,` prefix a FileReader data URL carries. */
export function stripDataUrl(dataUrl: string): string {
  const comma = dataUrl.indexOf(",");
  return comma === -1 ? dataUrl : dataUrl.slice(comma + 1);
}

export async function blobToBase64(blob: Blob): Promise<string> {
  const dataUrl = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error ?? new Error("read failed"));
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.readAsDataURL(blob);
  });
  return stripDataUrl(dataUrl);
}
