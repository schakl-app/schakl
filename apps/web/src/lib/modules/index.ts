/**
 * Loads every web module so it self-registers into the registry (CLAUDE.md §6).
 * Imported from both server and client hooks. Add a module here as it's built.
 */
import "$lib/core/activity"; // core capability: the activity panel on every auditable entity (#67)
import "./companies";
import "./contacts";
import "./tasks";
import "./projects";
import "./subscriptions";
import "./time";
import "./leave";
import "./notifications";
import "./domains";
import "./hosting";
import "./interactions";
import "./google";
