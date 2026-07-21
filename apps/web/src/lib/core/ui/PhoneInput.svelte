<script lang="ts">
  /**
   * Country-aware phone field (issue #256): a dial-code picker + number input that stores
   * E.164 while showing the national format — the `DateInput` pattern, so the posted value
   * travels through a hidden input and the visible field is free to speak the user's format.
   *
   * The picker is a real (transparent) `<select>` over a compact "NL +31" face: country is a
   * closed vocabulary with no create path, so the entity-picker combobox rule doesn't apply,
   * and the native control is what a phone keyboard handles best. Names are localized by
   * `Intl.DisplayNames`, never a 240-entry message catalog.
   *
   * A value that isn't E.164 (a contact's pre-#256 freeform phone) displays as stored and is
   * posted back byte-identical until the user actually edits the field — only an edit is a
   * human confirming the picked country, so only an edit may reinterpret the number.
   */
  import { ChevronDown } from "@lucide/svelte";
  import { type CountryCode, parsePhoneNumberFromString } from "libphonenumber-js/min";

  import { t } from "$lib/core/i18n";
  import { defaultPhoneCountry, phoneCountries } from "$lib/core/phone";

  let {
    name = "phone" as string | null,
    value = $bindable(""),
    id = "phone",
    formId,
  }: {
    /** The posted field; `null` renders no hidden input (draft collectors that bind instead). */
    name?: string | null;
    /** E.164, a legacy freeform string, or empty. Two-way for draft use (`bind:value`). */
    value?: string;
    id?: string;
    /** Associate the posted value with an external <form id=…> (single-save layouts). */
    formId?: string;
  } = $props();

  const countries = phoneCountries();

  // Seeded once, like ContactDraftField: every surface holding this field remounts per open.
  const initial = value ?? "";
  const parsedInitial = initial.startsWith("+") ? parsePhoneNumberFromString(initial) : undefined;

  // `defaultPhoneCountry` reads the browser locale, so on the server it answers from the UI
  // locale; hydration re-runs this init client-side and lands on the visitor's own region.
  let country = $state<CountryCode>(parsedInitial?.country ?? defaultPhoneCountry());
  let text = $state(parsedInitial ? parsedInitial.formatNational() : initial);
  let touched = $state(false);
  let blurred = $state(false);

  const dial = $derived(countries.find((c) => c.code === country)?.dial ?? "");
  const parsed = $derived(parsePhoneNumberFromString(text.trim(), country));

  // What the form actually submits: the untouched original (legacy round-trip guarantee),
  // E.164 once the input parses, or the raw text for the server to refuse.
  const outward = $derived(
    !touched ? initial : text.trim() === "" ? "" : parsed?.isValid() ? parsed.number : text.trim(),
  );
  $effect(() => {
    value = outward;
  });

  const invalid = $derived(touched && text.trim() !== "" && !parsed?.isValid());

  function onBlur() {
    blurred = true;
    if (!touched || !parsed?.isValid()) return;
    // Canonicalize on commit, DateInput-style: "+49 30 901820" filed under DE, shown national.
    if (parsed.country && parsed.country !== country) country = parsed.country;
    text = parsed.formatNational();
  }
</script>

<div>
  <div
    class="flex rounded-lg border border-border focus-within:border-brand focus-within:ring-1 focus-within:ring-brand"
  >
    <div
      class="relative flex shrink-0 items-center gap-1 rounded-l-lg border-r border-border bg-surface px-2.5 text-sm text-text"
    >
      <span aria-hidden="true">{country} +{dial}</span>
      <ChevronDown size={12} aria-hidden="true" class="text-text-muted" />
      <select
        bind:value={country}
        onchange={() => (touched = true)}
        aria-label={t("phone.country")}
        class="absolute inset-0 h-full w-full cursor-pointer opacity-0"
      >
        {#each countries as c (c.code)}
          <option value={c.code}>{c.name} (+{c.dial})</option>
        {/each}
      </select>
    </div>
    <input
      {id}
      type="tel"
      autocomplete="tel"
      bind:value={text}
      oninput={() => {
        touched = true;
        blurred = false;
      }}
      onblur={onBlur}
      class="w-full min-w-0 rounded-r-lg bg-transparent px-3 py-2 text-sm text-text outline-none"
    />
  </div>
  {#if name}
    <input type="hidden" {name} value={outward} form={formId} />
  {/if}
  {#if invalid && blurred}
    <p class="mt-1 text-xs text-red-600 dark:text-red-400">{t("errors.invalid_phone")}</p>
  {/if}
</div>
