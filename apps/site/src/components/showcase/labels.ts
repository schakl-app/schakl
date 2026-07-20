// In-UI labels for the animated product demos. These are NOT free marketing copy: they
// reproduce the real schakl app interface, so every string is the verbatim value from the
// app's own message catalog (messages/nl.json + messages/en.json). Keeping them here — one
// flat table per locale — is the site equivalent of that catalog: nl stays complete, and a
// new locale is one more object. The tour's *narration* (overlines, headings) lives in the
// CMS-managed landing.json; only the faithful chrome lives in code.

export type Locale = 'nl' | 'en';

// Locale-correct number/currency formatting, matching the app's core/format.ts (nl-NL / en-GB):
// nl uses "," decimals and "." thousands (12,5 · € 3.500); en uses "." and "," (12.5 · € 3,500).
const intlLocale = (l: Locale) => (l === 'nl' ? 'nl-NL' : 'en-GB');
export const num = (l: Locale, n: number, frac = 0): string =>
  new Intl.NumberFormat(intlLocale(l), { minimumFractionDigits: frac, maximumFractionDigits: frac }).format(n);
export const eur = (l: Locale, n: number, frac = 0): string => `€ ${num(l, n, frac)}`;

export interface DemoLabels {
  // App shell / nav
  navDashboard: string;
  navCompanies: string;
  navContacts: string;
  navProjects: string;
  navTasks: string;
  navTime: string;
  navLeave: string;
  navSubscriptions: string;
  navCalendar: string;
  navSettings: string;
  search: string;
  // Common
  save: string;
  cancel: string;
  add: string;
  today: string;
  // Companies
  clients: string;
  newClient: string;
  fName: string;
  fWebsite: string;
  fInvoiceEmail: string;
  fStatus: string;
  fAssignees: string;
  fNotes: string;
  stActive: string;
  stLead: string;
  stOnboarding: string;
  colContacts: string;
  colProjects: string;
  colResponsible: string;
  // Time
  addTime: string;
  timesheet: string;
  timerHeading: string;
  timerStart: string;
  timerStop: string;
  timerRunning: string;
  fStart: string;
  fEnd: string;
  fBreak: string;
  fDuration: string;
  durationHint: string;
  billable: string;
  notBillable: string;
  fDate: string;
  fClient: string;
  fProject: string;
  fTask: string;
  fDescription: string;
  worked: (d: string) => string;
  budgetSpent: (spent: string, budget: string) => string;
  timesheetRow: string;
  total: string;
  // Projects
  budget: string;
  logged: string;
  cost: string;
  billableValue: string;
  hoursUnit: string; // "u" / "h"
  projActive: string;
  budgetPeriodTotal: string;
  // Tasks
  tasks: string;
  stOpen: string;
  stInProgress: string;
  stDone: string;
  prHigh: string;
  prNormal: string;
  todosProgress: (done: number, total: number) => string;
  // Leave
  requestLeave: string;
  lType: string;
  lFirstDay: string;
  lLastDay: string;
  lHours: string;
  lDaysEquiv: (days: string) => string;
  lRemaining: (hours: string) => string;
  lSubmit: string;
  lPending: string;
  lApproved: string;
  leaveVacation: string;
  leaveHolidayNote: string; // "Feestdag" tag on a day that costs no hours
  // White-label
  brandLabel: string;
}

export const labels: Record<Locale, DemoLabels> = {
  nl: {
    navDashboard: 'Dashboard',
    navCompanies: 'Klanten',
    navContacts: 'Contactpersonen',
    navProjects: 'Projecten',
    navTasks: 'Taken',
    navTime: 'Uren',
    navLeave: 'Verlof',
    navSubscriptions: 'Abonnementen',
    navCalendar: 'Agenda',
    navSettings: 'Instellingen',
    search: 'Zoeken',
    save: 'Opslaan',
    cancel: 'Annuleren',
    add: 'Toevoegen',
    today: 'Vandaag',
    clients: 'Klanten',
    newClient: 'Nieuwe klant',
    fName: 'Naam',
    fWebsite: 'Website',
    fInvoiceEmail: 'Factuur-emailadres',
    fStatus: 'Status',
    fAssignees: 'Toegewezen medewerkers',
    fNotes: 'Notities',
    stActive: 'Actief',
    stLead: 'Lead',
    stOnboarding: 'Onboarding',
    colContacts: 'Contactpersonen',
    colProjects: 'Projecten',
    colResponsible: 'Verantwoordelijke',
    addTime: 'Uren toevoegen',
    timesheet: 'Weekstaat',
    timerHeading: 'Timer',
    timerStart: 'Starten',
    timerStop: 'Stoppen',
    timerRunning: 'Timer actief',
    fStart: 'Van',
    fEnd: 'Tot',
    fBreak: 'Pauze',
    fDuration: 'Duur',
    durationHint: 'bijv. 1:30 of 90m',
    billable: 'Facturabel',
    notBillable: 'Niet facturabel',
    fDate: 'Datum',
    fClient: 'Klant',
    fProject: 'Project',
    fTask: 'Taak',
    fDescription: 'Omschrijving',
    worked: (d) => `${d} gewerkt`,
    budgetSpent: (spent, budget) => `${spent} / ${budget} u deze periode`,
    timesheetRow: 'Klant / project / taak',
    total: 'Totaal',
    budget: 'Budget',
    logged: 'Geboekt',
    cost: 'Kosten (medewerkerstarieven)',
    billableValue: 'Facturabele waarde',
    hoursUnit: 'u',
    projActive: 'Actief',
    budgetPeriodTotal: 'Hele project',
    tasks: 'Taken',
    stOpen: 'Open',
    stInProgress: 'In behandeling',
    stDone: 'Gereed',
    prHigh: 'Hoog',
    prNormal: 'Normaal',
    todosProgress: (done, total) => `${done}/${total} klaar`,
    requestLeave: 'Verlof aanvragen',
    lType: 'Type',
    lFirstDay: 'Eerste dag',
    lLastDay: 'Laatste dag',
    lHours: 'Uren',
    lDaysEquiv: (days) => `≈ ${days} werkdagen`,
    lRemaining: (hours) => `${hours} u resterend`,
    lSubmit: 'Aanvraag indienen',
    lPending: 'In afwachting',
    lApproved: 'Goedgekeurd',
    leaveVacation: 'Vakantie',
    leaveHolidayNote: 'Feestdag',
    brandLabel: 'Jouw merk',
  },
  en: {
    navDashboard: 'Dashboard',
    navCompanies: 'Clients',
    navContacts: 'Contacts',
    navProjects: 'Projects',
    navTasks: 'Tasks',
    navTime: 'Time',
    navLeave: 'Leave',
    navSubscriptions: 'Subscriptions',
    navCalendar: 'Calendar',
    navSettings: 'Settings',
    search: 'Search',
    save: 'Save',
    cancel: 'Cancel',
    add: 'Add',
    today: 'Today',
    clients: 'Clients',
    newClient: 'New client',
    fName: 'Name',
    fWebsite: 'Website',
    fInvoiceEmail: 'Invoice email address',
    fStatus: 'Status',
    fAssignees: 'Assigned employees',
    fNotes: 'Notes',
    stActive: 'Active',
    stLead: 'Lead',
    stOnboarding: 'Onboarding',
    colContacts: 'Contacts',
    colProjects: 'Projects',
    colResponsible: 'Responsible',
    addTime: 'Add time',
    timesheet: 'Weekly timesheet',
    timerHeading: 'Timer',
    timerStart: 'Start',
    timerStop: 'Stop',
    timerRunning: 'Timer running',
    fStart: 'From',
    fEnd: 'To',
    fBreak: 'Break',
    fDuration: 'Duration',
    durationHint: 'e.g. 1:30 or 90m',
    billable: 'Billable',
    notBillable: 'Non-billable',
    fDate: 'Date',
    fClient: 'Client',
    fProject: 'Project',
    fTask: 'Task',
    fDescription: 'Description',
    worked: (d) => `${d} worked`,
    budgetSpent: (spent, budget) => `${spent} / ${budget} h this period`,
    timesheetRow: 'Client / project / task',
    total: 'Total',
    budget: 'Budget',
    logged: 'Logged',
    cost: 'Cost (employee rates)',
    billableValue: 'Billable value',
    hoursUnit: 'h',
    projActive: 'Active',
    budgetPeriodTotal: 'Whole project',
    tasks: 'Tasks',
    stOpen: 'Open',
    stInProgress: 'In progress',
    stDone: 'Done',
    prHigh: 'High',
    prNormal: 'Normal',
    todosProgress: (done, total) => `${done}/${total} done`,
    requestLeave: 'Request leave',
    lType: 'Type',
    lFirstDay: 'First day',
    lLastDay: 'Last day',
    lHours: 'Hours',
    lDaysEquiv: (days) => `≈ ${days} working days`,
    lRemaining: (hours) => `${hours} h remaining`,
    lSubmit: 'Submit request',
    lPending: 'Pending',
    lApproved: 'Approved',
    leaveVacation: 'Vacation',
    leaveHolidayNote: 'Holiday',
    brandLabel: 'Your brand',
  },
};

// Sample data shown inside the demos. Locale-neutral where a real value would be (names,
// emails), localized where the app would localize.
export const sample = {
  clientName: 'Bakkerij De Vries',
  clientWebsite: 'devries.nl',
  clientEmail: 'facturen@devries.nl',
  people: ['SV', 'TM', 'AJ'],
  projectName: { nl: 'Website herontwerp', en: 'Website redesign' } as Record<Locale, string>,
  taskName: { nl: 'Homepage teksten', en: 'Homepage copy' } as Record<Locale, string>,
  existingClients: [
    { name: 'Studio Noord', initials: 'SN' },
    { name: 'Groen & Co', initials: 'GC' },
    { name: 'Meijer Media', initials: 'MM' },
  ],
};
