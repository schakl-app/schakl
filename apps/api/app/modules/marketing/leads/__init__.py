"""The leads dashboard — a client's form, call and e-mail conversions, and their ad spend, read
live from GA4 and Google Ads through a per-client **measurement profile** (docs/MARKETING.md).

The Looker Studio reports an agency used to build by hand, one per client, were five pages of
the same fourteen questions asked in that client's own event names. What differed per client
was never the dashboard; it was the *vocabulary*: which event is a request, which parameter
carries the service, what ``internationaal-transport`` is called on screen. So the vocabulary is
data (``profile.py``, stored on ``marketing_company_settings.lead_profile``) and the questions
are code (``widgets.py``, a fixed catalog), which is the whole design — a report that can be
built for a second client by typing, not by programming, and deliberately not a canvas.

Three rules hold it up:

- **The profile names roles, the dashboard names nothing.** No event name, dimension value or
  conversion action is spelled anywhere in this package; a client without a profile has no
  leads dashboard rather than one that guesses.
- **A widget is shown when its data can exist, and withheld when it cannot** — a client whose
  profile has no form-start role gets no funnel, and nothing draws a zero in its place.
- **Every number is traceable to one request** (``ga4.py``, ``ads.py``): the payload carries
  which report answered which widget, and GA4 events and Ads costs are never joined on a date.
"""
