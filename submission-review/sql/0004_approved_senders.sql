-- Who may send files for review. Set on MyDay's Admin page, Review senders.
--
-- Every existing row starts off. Those brokers were added automatically by the
-- worker, and from here on nobody is reviewed until an admin saves their address.
-- Turning someone off keeps their row, because their files point at it.

alter table review.broker
  add column approved boolean not null default false,
  add constraint broker_approved_is_fairstar
    check (not approved or email like '%@fairstarinsurance.ca');

notify pgrst, 'reload schema';
