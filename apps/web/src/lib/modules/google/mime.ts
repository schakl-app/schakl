/**
 * One mime → icon/kind mapping for every Drive surface (#150): the linked-file lists and the
 * browser draw from the same table, so a Google Doc looks like a Google Doc everywhere.
 * Kind labels are i18n keys (`google.drive.kind.*`), en+nl like everything.
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
  ListChecks,
  Presentation,
} from "@lucide/svelte";
import type { Component } from "svelte";

interface DriveKind {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  icon: Component<any>;
  labelKey: string;
}

const KINDS: Record<string, DriveKind> = {
  folder: { icon: Folder, labelKey: "google.drive.kind.folder" },
  doc: { icon: FileText, labelKey: "google.drive.kind.doc" },
  sheet: { icon: FileSpreadsheet, labelKey: "google.drive.kind.sheet" },
  slides: { icon: Presentation, labelKey: "google.drive.kind.slides" },
  form: { icon: ListChecks, labelKey: "google.drive.kind.form" },
  pdf: { icon: FileText, labelKey: "google.drive.kind.pdf" },
  image: { icon: FileImage, labelKey: "google.drive.kind.image" },
  video: { icon: FileVideo, labelKey: "google.drive.kind.video" },
  audio: { icon: FileAudio, labelKey: "google.drive.kind.audio" },
  archive: { icon: FileArchive, labelKey: "google.drive.kind.archive" },
  file: { icon: File, labelKey: "google.drive.kind.file" },
};

const GOOGLE_APPS: Record<string, string> = {
  "application/vnd.google-apps.folder": "folder",
  "application/vnd.google-apps.document": "doc",
  "application/vnd.google-apps.spreadsheet": "sheet",
  "application/vnd.google-apps.presentation": "slides",
  "application/vnd.google-apps.form": "form",
};

const OFFICE: Record<string, string> = {
  "application/pdf": "pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "doc",
  "application/msword": "doc",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "sheet",
  "application/vnd.ms-excel": "sheet",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation": "slides",
  "application/vnd.ms-powerpoint": "slides",
  "application/zip": "archive",
  "application/x-7z-compressed": "archive",
  "application/gzip": "archive",
};

export function driveKind(mimeType: string | null | undefined, isFolder: boolean): DriveKind {
  if (isFolder) return KINDS.folder;
  const mime = mimeType ?? "";
  const key =
    GOOGLE_APPS[mime] ??
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
