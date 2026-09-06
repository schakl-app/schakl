/**
 * One mime → icon/kind mapping for every OneDrive surface: the linked-file lists and the
 * browser draw from the same table, so a Word document looks like a Word document everywhere.
 * Kind labels are i18n keys (`microsoft.onedrive.kind.*`), en+nl like everything.
 *
 * Graph reports a file's mime in `file.mimeType` and marks a folder with the `folder` facet
 * rather than a mime, so a folder is decided by the facet the caller already read, never by
 * the type string.
 */
import {
  File,
  FileArchive,
  FileAudio,
  FileImage,
  FileSpreadsheet,
  FileText,
  FileVideo,
  Folder,
  NotebookPen,
  Presentation,
} from "@lucide/svelte";
import type { Component } from "svelte";

interface OneDriveKind {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  icon: Component<any>;
  labelKey: string;
}

const KINDS: Record<string, OneDriveKind> = {
  folder: { icon: Folder, labelKey: "microsoft.onedrive.kind.folder" },
  doc: { icon: FileText, labelKey: "microsoft.onedrive.kind.doc" },
  sheet: { icon: FileSpreadsheet, labelKey: "microsoft.onedrive.kind.sheet" },
  slides: { icon: Presentation, labelKey: "microsoft.onedrive.kind.slides" },
  note: { icon: NotebookPen, labelKey: "microsoft.onedrive.kind.note" },
  pdf: { icon: FileText, labelKey: "microsoft.onedrive.kind.pdf" },
  image: { icon: FileImage, labelKey: "microsoft.onedrive.kind.image" },
  video: { icon: FileVideo, labelKey: "microsoft.onedrive.kind.video" },
  audio: { icon: FileAudio, labelKey: "microsoft.onedrive.kind.audio" },
  archive: { icon: FileArchive, labelKey: "microsoft.onedrive.kind.archive" },
  file: { icon: File, labelKey: "microsoft.onedrive.kind.file" },
};

const OFFICE: Record<string, string> = {
  "application/pdf": "pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "doc",
  "application/msword": "doc",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "sheet",
  "application/vnd.ms-excel": "sheet",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation": "slides",
  "application/vnd.ms-powerpoint": "slides",
  "application/msonenote": "note",
  "application/onenote": "note",
  "application/zip": "archive",
  "application/x-7z-compressed": "archive",
  "application/gzip": "archive",
};

export function oneDriveKind(mimeType: string | null | undefined, isFolder: boolean): OneDriveKind {
  if (isFolder) return KINDS.folder;
  const mime = mimeType ?? "";
  const key =
    OFFICE[mime] ??
    (mime.startsWith("image/")
      ? "image"
      : mime.startsWith("video/")
        ? "video"
        : mime.startsWith("audio/")
          ? "audio"
          : "file");
  return KINDS[key] ?? KINDS.file;
}
