-- The broker's Zoom Phone line — the number and extension that belong to the
-- zoom_user_id already on this table (0062).
--
-- WRITTEN FROM ZOOM'S OWN ANSWER, NEVER TYPED. Onboarding takes one field: the
-- Zoom user id, pasted from the Zoom admin console. The backend fetches that
-- user from Zoom, and the columns below are filled from what comes back. An
-- admin who also typed the number by hand would introduce the one error this
-- design exists to remove — and a typo there would make us reject a perfectly
-- good id.
--
-- Verification is mandatory: an id Zoom does not recognise cannot be saved.
-- Without that, a mistyped id fails SILENTLY — the broker looks onboarded and
-- their calls simply stop filing against leads, with nothing on screen to say
-- why.
--
-- Name and email are deliberately NOT stored, though the verify step shows
-- them. broker.name and broker.email already hold those, and a second copy
-- only raises the question of which one is right when the two disagree. Their
-- job is to let the admin recognise the person before saving, not to be kept.

-- Full E.164 line, e.g. "+12264448810". What a customer sees on their phone
-- when this broker rings them.
alter table broker add column zoom_phone_number text;

-- The internal extension, e.g. "800". Kept alongside the full number because
-- it is what a broker knows their own line as, and what internal transfers
-- use. Short, and not derivable from the number.
alter table broker add column zoom_extension text;

-- No unique index on either, unlike zoom_user_id (0062).
--
-- The user id is the identity, and one Zoom user belonging to two brokers
-- would make every call from them ambiguous — so that one is enforced. These
-- two are attributes OF that identity: they arrive from Zoom already tied to a
-- verified id, so a duplicate here can only mean Zoom itself reported the same
-- number twice, which its own admin prevents. A constraint would add nothing
-- and would fail onboarding for a number legitimately re-issued after a broker
-- left.
