<script lang="ts">
  /**
   * A record's vital signs (#364) — the strip under a detail page's header.
   *
   * The client page had no foreground: fourteen equally-weighted cards, and not one of the five
   * numbers that make up a relationship's health (openstaand bedrag, uren deze maand, open taken
   * waarvan n over tijd, laatste contactmoment, eerstvolgende verlenging) on screen. All five
   * were derivable from panels the reader had to scroll to and add up by eye.
   *
   * Three rules it keeps.
   *
   * **Every number opens** (docs/UX.md principle 7): the tile *is* the link to the thing it
   * counted, so a striking figure is one click from its rows rather than a prompt to go and find
   * them. A tile with no `href` is drawn as a plain figure, never as a control that goes nowhere.
   *
   * **A tone is a tone, not a colour.** The API says neutral/good/warn/bad; the palette is
   * decided here, and it is carried by weight and a coloured *figure* rather than a coloured
   * card — a wash of amber cards is the "aandachtspunten" mistake in miniature.
   *
   * **Nothing is a number.** A module returns no tile rather than a zero: a strip that always
   * says "€ 0,00 openstaand" over a client who has never been invoiced is the chrome the whole
   * redesign exists to remove.
   */
  import { fmtMoney, fmtNumber, fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";

  export interface SummaryTile {
    key: string;
    label_key: string;
    value: string;
    format?: string;
    currency?: string | null;
    tone?: string;
    hint_key?: string | null;
    hint_params?: Record<string, unknown>;
    href?: string | null;
  }

  let { tiles }: { tiles: SummaryTile[] } = $props();

  const TONE: Record<string, string> = {
    neutral: "text-text",
    good: "text-green-700 dark:text-green-400",
    warn: "text-amber-700 dark:text-amber-400",
    bad: "text-red-700 dark:text-red-400",
  };

  /** The reader's locale formats every figure (§8) — the API sends the raw value and its units. */
  function display(tile: SummaryTile): string {
    const raw = tile.value;
    switch (tile.format) {
      case "money": {
        const amount = Number(raw);
        if (Number.isNaN(amount)) return raw;
        // The tenant's own currency unless the module named a different one — a client invoiced
        // in USD must not read as euros just because the org's default is.
        return tile.currency
          ? new Intl.NumberFormat(undefined, {
              style: "currency",
              currency: tile.currency,
              trailingZeroDisplay: "stripIfInteger",
            }).format(amount)
          : fmtMoney(amount);
      }
      case "hours": {
        const hours = Number(raw);
        return Number.isNaN(hours) ? raw : `${fmtNumber(hours, 1)} u`;
      }
      case "date":
        return /^\d{4}-\d{2}-\d{2}$/.test(raw) ? fmtNumericDate(raw) : raw;
      case "number": {
        const n = Number(raw);
        return Number.isNaN(n) ? raw : fmtNumber(n, 0);
      }
      default:
        return raw;
    }
  }

  /**
   * The tile's one-line hint, singular-aware.
   *
   * Paraglide as configured here does not parse ICU `{n, plural, …}` — it compiles it to
   * garbage — so the catalogs carry a `<key>_one` beside `<key>` and the *reader's* side picks.
   * The API cannot: plural rules belong to the locale, and the API does not choose one for
   * somebody else (§8). `t()` falls back to the key itself when a message is absent, which is
   * how a hint with no singular form quietly keeps using its plural.
   */
  function hintOf(tile: SummaryTile): string | null {
    if (!tile.hint_key) return null;
    const params = tile.hint_params ?? {};
    if (params.count === 1) {
      const singular = t(`${tile.hint_key}_one`, params);
      if (singular !== `${tile.hint_key}_one`) return singular;
    }
    return t(tile.hint_key, params);
  }
</script>

{#if tiles.length > 0}
  <!-- **The strip ends where the page ends, whatever it is counting.** A fixed five-column grid
       fits five tiles and nothing else: a client with no invoices contributes four, and the row
       stopped 232 px short of the right edge with the empty slot reading as a tile that had
       failed to load. "Nothing is a number" (above) is exactly what makes the count variable —
       one to six today, more as modules contribute — so the tiles *share* the row rather than
       being dealt into slots sized for a count nobody promised.

       It is one row on a desktop and wraps below it, and the two halves need different rules.
       `nowrap` + `basis-0` is what makes a strip a strip: every tile the same width, however many
       there are, which is what the five-column grid did correctly for the one count it knew.
       Wrapping wants a real minimum instead (`basis-36`), or a phone deals two tiles a row and
       leaves the last one stretched across the screen on its own. -->
  <ul class="mb-6 flex flex-wrap gap-2 lg:flex-nowrap">
    {#each tiles as tile (tile.key)}
      {@const body = display(tile)}
      {@const hint = hintOf(tile)}
      <li class="min-w-0 flex-1 basis-36 lg:basis-0">
        <svelte:element
          this={tile.href ? "a" : "div"}
          href={tile.href ?? undefined}
          class="block h-full rounded-xl border border-border bg-surface-raised px-3 py-2.5 {tile.href
            ? 'transition-colors hover:border-brand'
            : ''}"
        >
          <span class="block truncate text-xs font-medium uppercase tracking-wide text-text-muted">
            {t(tile.label_key)}
          </span>
          <span
            class="mt-0.5 block truncate text-lg font-semibold tabular-nums {TONE[
              tile.tone ?? 'neutral'
            ] ?? TONE.neutral}"
          >
            {body}
          </span>
          {#if hint}
            <span class="block truncate text-xs text-text-muted">{hint}</span>
          {/if}
        </svelte:element>
      </li>
    {/each}
  </ul>
{/if}
