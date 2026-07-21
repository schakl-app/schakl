<script lang="ts">
  /**
   * Country-aware phone field (issue #256): a dial-code picker + number input that stores
   * E.164 while showing the national format — the `DateInput` pattern, so the posted value
   * travels through a hidden input and the visible field is free to speak the user's format.
   *
   * The picker is the house `Combobox` (docs/UX.md: type-ahead, never a long native select):
   * its face reads "NL +31" and typing a country name, ISO code or dial code filters the
   * list — names come from `Intl.DisplayNames` in the hint, never a 240-entry catalog. Its
   * `_phone_country` field is UI plumbing the form actions ignore, like `_contact_pick`.
   *
   * A value that isn't E.164 (a contact's pre-#256 freeform phone) displays as stored and is
   * posted back byte-identical until the user actually edits the field — only an edit is a
   * human confirming the picked country, so only an edit may reinterpret the number.
   */
  import { type CountryCode, parsePhoneNumberFromString } from "libphonenumber-js/min";

  import { t } from "$lib/core/i18n";
  import { defaultPhoneCountry, phoneCountries } from "$lib/core/phone";
  import Combobox from "$lib/core/ui/Combobox.svelte";

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

  // Compact face ("NL +31"), full localized name searchable in the hint.
  const countryItems = phoneCountries().map((c) => ({
    value: c.code,
    label: `${c.code} +${c.dial}`,
    hint: c.name,
  }));

  // Seeded once, like ContactDraftField: every surface holding this field remounts per open.
  const initial = value ?? "";
  const parsedInitial = initial.startsWith("+") ? parsePhoneNumberFromString(initial) : undefined;

  // `defaultPhoneCountry` reads the browser locale, so on the server it answers from the UI
  // locale; hydration re-runs this init client-side and lands on the visitor's own region.
  let country = $state<string>(parsedInitial?.country ?? defaultPhoneCountry());
  let text = $state(parsedInitial ? parsedInitial.formatNational() : initial);
  let touched = $state(false);
  let blurred = $state(false);

  const parsed = $derived(parsePhoneNumberFromString(text.trim(), country as CountryCode));

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
  <div class="flex gap-2">
    <div class="w-28 shrink-0">
      <Combobox
        items={countryItems}
        name="_phone_country"
        bind:value={country}
        id="{id}-country"
        ariaLabel={t("phone.country")}
        placeholder={t("phone.country")}
        allowEmpty={false}
        listClass="w-64"
        onselect={() => (touched = true)}
      />
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
      class="min-w-0 flex-1 rounded-lg border border-border px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand"
    />
  </div>
  {#if name}
    <input type="hidden" {name} value={outward} form={formId} />
  {/if}
  {#if invalid && blurred}
    <p class="mt-1 text-xs text-red-600 dark:text-red-400">{t("errors.invalid_phone")}</p>
  {/if}
</div>
