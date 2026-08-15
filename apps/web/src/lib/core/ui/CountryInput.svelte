<script lang="ts">
  /**
   * The address country, chosen from a searchable list (#349).
   *
   * It was a two-character free-text box labelled "Land (2 letters)" — which asks a human to know
   * ISO 3166 (Germany is `DE` not `DU`, Austria `AT` not `OE`), was blank even though the tenant
   * has a `default_country`, and sat three fields below a working country picker in the same
   * `<form>`: `PhoneInput`, whose face reads "NL +31" and which filters on name, ISO code or dial
   * code. A wrong two letters is a *silently valid* country, which is the worst kind.
   *
   * So this is that list, extracted: the house `Combobox` over `phoneCountries()` — country names
   * from `Intl.DisplayNames`, so ~240 names never enter the message catalogs (§8) — defaulting to
   * the org's own `default_country`. It stores and posts the ISO code exactly as before, so this
   * is a UI change with no schema or API impact.
   */
  import { page } from "$app/state";

  import { t } from "$lib/core/i18n";
  import { defaultPhoneCountry, phoneCountries } from "$lib/core/phone";
  import Combobox from "$lib/core/ui/Combobox.svelte";

  let {
    name = "country",
    value = $bindable(""),
    id = name,
    formId,
    /**
     * Fall back to the org's country when the record has none. On a **create** form that is the
     * helpful default the issue asks for; on an **edit** form of a record that genuinely stores
     * no country it would invent one, so the caller decides.
     */
    fallbackToOrg = true,
  }: {
    name?: string;
    value?: string;
    id?: string;
    formId?: string;
    fallbackToOrg?: boolean;
  } = $props();

  const items = $derived(
    phoneCountries().map((c) => ({ value: c.code, label: c.name, hint: c.code })),
  );

  // Seeded once — the surfaces holding this field remount per open (the `PhoneInput` rule).
  // `NL` and `nl` are the same country, so the stored value is normalised before the picker
  // ever sees it; otherwise the face reads blank over a record that has a perfectly good one.
  const stored = value?.trim().toUpperCase() ?? "";
  value =
    stored || (fallbackToOrg ? (page.data.theme?.defaultCountry ?? defaultPhoneCountry()) : "");
</script>

<Combobox
  {items}
  {name}
  {id}
  {formId}
  bind:value
  placeholder={t("companies.country_placeholder")}
/>
